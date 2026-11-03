#!/usr/bin/env python3
"""この環境に入っているスキルのスクリプトをつないで、Dagu v2 の DAG を組み立てる。LLM は使わない。

各スキルは、自分のスクリプトが何を受け取り何を作るかを `chain.json`（スキル直下）で宣言する。
このスクリプトはインストール先のスキルを走査して宣言を集め、「型」（`openapi` や
`openapi-report` のような名前）を頂点にした有向超グラフの上で、手元の入力から目的の型までの
最小コストの実行チェーンを決定的に探し、Dagu v2 の YAML に書き出す。

サブコマンド:
  scan   見つかったスキルとアクション、作れる型を並べる
  check  chain.json を検査する
  plan   入力の型から目的の型までのチェーンを決めて表示する
  build  plan の結果を Dagu v2 の DAG（YAML）として書き出す

例:
  python3 chain.py scan
  python3 chain.py plan --from openapi=./openapi.yaml --to openapi-report
  python3 chain.py build --from openapi=./openapi.yaml --to openapi-report -o ~/.config/dagu/dags/api-check.yaml

終了コード: 0 = 成功 / 1 = 検査の違反・チェーンが組めない / 2 = 使い方の誤り。
標準ライブラリだけで動く（Python 3.10+）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

CONTRACT_FILE = "chain.json"
CONTRACT_VERSION = 1

# install.py の AGENT_DIRS と同じ並び。前にあるホームのスキルが優先される。
AGENT_HOME_DIRS = (".copilot", ".claude", ".codex", ".kiro", ".agents", os.path.join(".config", "opencode"))
SKILLS_ENV = "DAGU_CHAIN_SKILLS"

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PLACEHOLDER_RE = re.compile(r"\{(in|out|param):([^{}]*)\}|\{skill_dir\}")
INTERPRETERS = {"python", "python3", "node"}


class ChainError(Exception):
    """チェーンが組めない・宣言が壊れている。終了コード 1。"""


# ---------------------------------------------------------------- 宣言の読み込み


@dataclass
class Output:
    name: str
    type: str
    ext: str = ""
    stdout: bool = False


@dataclass
class Param:
    name: str
    default: str | None = None
    description: str = ""


@dataclass
class Action:
    skill: str
    skill_dir: Path
    id: str
    summary: str
    run: list[str]
    inputs: dict[str, str]
    outputs: list[Output]
    params: list[Param] = field(default_factory=list)
    ok_exit: list[int] = field(default_factory=lambda: [0])
    timeout_sec: int | None = None

    @property
    def key(self) -> str:
        return f"{self.skill}/{self.id}"

    @property
    def step_name(self) -> str:
        return f"{self.skill}-{self.id}"


def skill_roots(explicit: list[str] | None = None) -> list[Path]:
    """走査するスキル置き場。--skills-dir > 環境変数 DAGU_CHAIN_SKILLS > 各エージェントのホーム。"""
    if explicit:
        return [Path(p).expanduser() for p in explicit]
    env = os.environ.get(SKILLS_ENV, "")
    if env.strip():
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return [home / d / "skills" for d in AGENT_HOME_DIRS]


def discover(roots: list[Path]) -> tuple[dict[str, Path], list[tuple[str, Path]]]:
    """スキル名 → ディレクトリ。同じ名前は先に見つかった方を使い、後のものは shadowed に返す。"""
    found: dict[str, Path] = {}
    shadowed: list[tuple[str, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not (d / "SKILL.md").is_file() or not (d / CONTRACT_FILE).is_file():
                continue
            if d.name in found:
                shadowed.append((d.name, d))
            else:
                found[d.name] = d
    return found, shadowed


def _str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def load_contract(skill_dir: Path) -> tuple[list[Action], list[str]]:
    """chain.json を読み、(アクション, 違反) を返す。違反のあるアクションは返さない。"""
    path = skill_dir / CONTRACT_FILE
    skill = skill_dir.name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [], [f"{path}: 読めない（{e}）"]
    if not isinstance(data, dict):
        return [], [f"{path}: 最上位はオブジェクトにする"]
    if data.get("chain") != CONTRACT_VERSION:
        return [], [f"{path}: \"chain\": {CONTRACT_VERSION} が要る"]
    raw_actions = data.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        return [], [f"{path}: actions を 1 つ以上書く"]

    actions: list[Action] = []
    problems: list[str] = []
    seen: set[str] = set()
    for i, ra in enumerate(raw_actions):
        where = f"{path}: actions[{i}]"
        errs: list[str] = []
        if not isinstance(ra, dict):
            problems.append(f"{where}: オブジェクトにする")
            continue
        aid = ra.get("id")
        if not isinstance(aid, str) or not ID_RE.match(aid):
            errs.append("id は英小文字・数字・ハイフンにする")
        elif aid in seen:
            errs.append(f"id {aid} が重複している")
        else:
            seen.add(aid)
            where = f"{path}: {aid}"
        summary = ra.get("summary", "")
        if not isinstance(summary, str) or not summary.strip():
            errs.append("summary（何をするかの 1 行）が要る")
        run = ra.get("run")
        if not _str_list(run) or not run:
            errs.append("run は文字列の配列にする")
            run = []

        inputs = ra.get("inputs", {})
        if not isinstance(inputs, dict) or not all(
            isinstance(k, str) and NAME_RE.match(k) and isinstance(v, str) and TYPE_RE.match(v)
            for k, v in inputs.items()
        ):
            errs.append("inputs は {名前: 型} にする（名前は英小文字と _、型は英小文字・数字・. + -）")
            inputs = {}

        outputs: list[Output] = []
        raw_out = ra.get("outputs")
        if not isinstance(raw_out, dict) or not raw_out:
            errs.append("outputs を 1 つ以上書く")
            raw_out = {}
        for oname, ospec in raw_out.items():
            if not NAME_RE.match(oname) or not isinstance(ospec, dict):
                errs.append(f"outputs.{oname}: 名前は英小文字と _、中身は {{\"type\": …}} にする")
                continue
            otype = ospec.get("type")
            ext = ospec.get("ext", "")
            if not isinstance(otype, str) or not TYPE_RE.match(otype):
                errs.append(f"outputs.{oname}: type が要る")
                continue
            if not isinstance(ext, str) or not re.match(r"^[A-Za-z0-9.]*$", ext):
                errs.append(f"outputs.{oname}: ext は英数字にする")
                continue
            outputs.append(Output(oname, otype, ext, bool(ospec.get("stdout", False))))
        if sum(o.stdout for o in outputs) > 1:
            errs.append("stdout に出す出力は 1 つまで")

        params: list[Param] = []
        raw_params = ra.get("params", {})
        if not isinstance(raw_params, dict):
            errs.append("params は {名前: {default, description}} にする")
            raw_params = {}
        for pname, pspec in raw_params.items():
            if not NAME_RE.match(pname) or not isinstance(pspec, dict):
                errs.append(f"params.{pname}: 名前は英小文字と _、中身はオブジェクトにする")
                continue
            default = pspec.get("default")
            if default is not None and not isinstance(default, str):
                errs.append(f"params.{pname}: default は文字列にする")
                continue
            params.append(Param(pname, default, str(pspec.get("description", ""))))

        ok_exit = ra.get("ok_exit", [0])
        if not isinstance(ok_exit, list) or not all(isinstance(x, int) for x in ok_exit) or 0 not in ok_exit:
            errs.append("ok_exit は 0 を含む整数の配列にする")
            ok_exit = [0]
        timeout = ra.get("timeout_sec")
        if timeout is not None and (not isinstance(timeout, int) or timeout <= 0):
            errs.append("timeout_sec は正の整数にする")
            timeout = None

        # run の差し込みと宣言の突き合わせ
        used = {"in": set(), "out": set(), "param": set()}
        for tok in run:
            for m in PLACEHOLDER_RE.finditer(tok):
                if m.group(1):
                    used[m.group(1)].add(m.group(2))
        names = {"in": set(inputs), "out": {o.name for o in outputs}, "param": {p.name for p in params}}
        for kind in ("in", "out", "param"):
            for n in sorted(used[kind] - names[kind]):
                errs.append(f"run の {{{kind}:{n}}} が宣言されていない")
        for n in sorted(names["in"] - used["in"]):
            errs.append(f"入力 {n} が run で使われていない")
        for o in outputs:
            if not o.stdout and o.name not in used["out"]:
                errs.append(f"出力 {o.name} が run に無い（ファイルで出すなら {{out:{o.name}}}、標準出力なら \"stdout\": true）")
            if o.stdout and o.name in used["out"]:
                errs.append(f"出力 {o.name} は stdout と {{out:…}} の両方にしない")
        for p in params:
            if p.name not in used["param"]:
                errs.append(f"パラメータ {p.name} が run で使われていない")
        for tok in run[:2]:
            if tok.startswith(("scripts/", "scripts\\")) and not (skill_dir / tok).is_file():
                errs.append(f"run のスクリプト {tok} が無い")
        if set(inputs.values()) & {o.type for o in outputs}:
            errs.append("同じ型を受け取って同じ型を出すアクションは書けない（チェーンが閉じない）")

        if errs:
            problems.extend(f"{where}: {e}" for e in errs)
            continue
        actions.append(Action(skill, skill_dir, aid, summary.strip(), list(run), dict(inputs),
                              outputs, params, sorted(set(ok_exit)), timeout))
    return actions, problems


def load_all(roots: list[Path]) -> tuple[list[Action], list[str], list[tuple[str, Path]]]:
    skills, shadowed = discover(roots)
    actions: list[Action] = []
    problems: list[str] = []
    for name in sorted(skills):
        a, p = load_contract(skills[name])
        actions.extend(a)
        problems.extend(p)
    actions.sort(key=lambda a: (a.skill, a.id))
    return actions, problems, shadowed


# ---------------------------------------------------------------- 探索


@dataclass
class Plan:
    have: dict[str, str]              # 型 → 入力のパス（空なら実行時に渡す）
    goals: list[str]
    steps: list[Action]               # 実行順（依存の前に依存元）
    producer: dict[str, Action]       # 型 → それを作るステップ
    cost: dict[str, int]


def solve(actions: list[Action], have: dict[str, str], goals: list[str], avoid: list[str]) -> Plan:
    """手元の型から目的の型までのチェーンを決める（決定的）。

    型のコスト = 作るのに要るステップの延べ数（手元の型は 0）。アクションを (スキル, id) の順に
    なめて、全入力のコストが決まっているアクションの出力を「1 + 入力のコストの和」で緩和する。
    同じコストなら先に見た（名前順で前の）アクションが勝つ。
    """
    def avoided(a: Action) -> bool:
        return any(a.key == x or a.skill == x for x in avoid)

    usable = [a for a in actions if not avoided(a)]
    cost: dict[str, int] = {t: 0 for t in have}
    via: dict[str, Action] = {}
    changed = True
    while changed:
        changed = False
        for a in usable:
            in_types = sorted(set(a.inputs.values()))
            if not all(t in cost for t in in_types):
                continue
            c = 1 + sum(cost[t] for t in in_types)
            for o in a.outputs:
                if o.type not in cost or c < cost[o.type]:
                    cost[o.type] = c
                    via[o.type] = a
                    changed = True

    missing = [g for g in goals if g not in cost]
    if missing:
        raise ChainError(_explain_missing(missing, usable, cost))

    order: list[Action] = []
    done: set[str] = set()

    def visit(t: str) -> None:
        a = via.get(t)
        if a is None or a.key in done:
            return
        for it in sorted(set(a.inputs.values())):
            visit(it)
        done.add(a.key)
        order.append(a)

    for g in goals:
        visit(g)
    producer = {o.type: a for a in order for o in a.outputs if via.get(o.type) is a}
    return Plan(dict(have), list(goals), order, producer, cost)


def _explain_missing(missing: list[str], actions: list[Action], cost: dict[str, int]) -> str:
    lines = []
    for g in missing:
        makers = [a for a in actions if any(o.type == g for o in a.outputs)]
        if not makers:
            lines.append(f"{g} を作るアクションが、見つかったスキルに無い")
            continue
        lines.append(f"{g} を作るアクションはあるが、入力が揃わない:")
        for a in makers:
            lack = sorted({t for t in a.inputs.values() if t not in cost})
            lines.append(f"  {a.key}: 足りない型 {', '.join(lack)}（--from で渡すか、作るスキルを入れる）")
    return "\n".join(lines)


# ---------------------------------------------------------------- Dagu v2 への書き出し


def param_name(*parts: str) -> str:
    return "_".join(re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_").upper() for p in parts if p)


def input_param(t: str) -> str:
    return param_name("IN", t)


def output_file(a: Action, o: Output) -> str:
    return f"{a.step_name}.{o.name}" + (f".{o.ext}" if o.ext else "")


def _abs(p: str) -> str:
    return Path(p).expanduser().resolve().as_posix()


def interpreter(token: str) -> str:
    if token in ("python", "python3"):
        return Path(sys.executable).as_posix() if sys.executable else "python3"
    found = shutil.which(token)
    return Path(found).as_posix() if found else token


def render_step(a: Action, plan: Plan, values: dict[str, str]) -> tuple[dict, list[dict]]:
    """1 アクション → Dagu のステップ。(ステップ, このステップが足す params) を返す。"""
    step_params: list[dict] = []
    bindings: dict[tuple[str, str], str] = {}
    for iname, itype in a.inputs.items():
        if itype in plan.have:
            bindings[("in", iname)] = "${params.%s}" % input_param(itype)
        else:
            src = plan.producer[itype]
            o = next(o for o in src.outputs if o.type == itype)
            bindings[("in", iname)] = output_file(src, o)
    for o in a.outputs:
        bindings[("out", o.name)] = output_file(a, o)
    for p in a.params:
        pn = param_name(a.skill, a.id, p.name)
        given = values.get(f"{a.key}.{p.name}", values.get(p.name))
        default = given if given is not None else p.default
        entry: dict = {"name": pn, "description": p.description or f"{a.key} の {p.name}"}
        if default is None:
            entry["required"] = True
        else:
            entry["default"] = default
        step_params.append(entry)
        bindings[("param", p.name)] = "${params.%s}" % pn

    def sub(tok: str) -> str:
        def repl(m: re.Match) -> str:
            if m.group(0) == "{skill_dir}":
                return a.skill_dir.resolve().as_posix()
            return bindings[(m.group(1), m.group(2))]
        return PLACEHOLDER_RE.sub(repl, tok)

    argv = [sub(t) for t in a.run]
    head = a.run[0]
    if head in INTERPRETERS:
        command = interpreter(head)
    elif (a.skill_dir / head).is_file():
        command = (a.skill_dir / head).resolve().as_posix()
    else:
        command = head
    args = []
    for raw, tok in zip(a.run[1:], argv[1:]):
        if not PLACEHOLDER_RE.search(raw) and (a.skill_dir / raw).is_file():
            tok = (a.skill_dir / raw).resolve().as_posix()
        args.append(tok)

    deps = sorted({plan.producer[t].step_name for t in a.inputs.values() if t not in plan.have})
    step: dict = {
        "name": a.step_name,
        "description": a.summary,
        "action": "exec",
        "with": {"command": command, "args": args},
    }
    so = next((o for o in a.outputs if o.stdout), None)
    if so:
        step["stdout"] = output_file(a, so)
    if deps:
        step["depends"] = deps
    extra = [c for c in a.ok_exit if c != 0]
    if extra:
        step["continue_on"] = {"exit_code": a.ok_exit, "mark_success": True}
    if a.timeout_sec:
        step["timeout_sec"] = a.timeout_sec
    return step, step_params


def render_dag(plan: Plan, values: dict[str, str], description: str = "") -> str:
    params: list[dict] = []
    for t in sorted(plan.have):
        if not any(t in a.inputs.values() for a in plan.steps):
            continue
        entry: dict = {"name": input_param(t), "description": f"入力 {t} のパス（絶対パス）"}
        if plan.have[t]:
            entry["default"] = _abs(plan.have[t])
        else:
            entry["required"] = True
        params.append(entry)
    steps = []
    for a in plan.steps:
        s, sp = render_step(a, plan, values)
        steps.append(s)
        params.extend(sp)

    goal_files = []
    for g in plan.goals:
        if g in plan.have:
            continue
        a = plan.producer[g]
        o = next(o for o in a.outputs if o.type == g)
        goal_files.append(f"{g} → {output_file(a, o)}")
    header = [
        "# 生成: dagu-workflow-builder（chain.py build）。手で直さず、作り直す。",
        "# 成果物は実行ごとの作業フォルダ（DAG_RUN_WORK_DIR）にできる:",
        *[f"#   {g}" for g in goal_files],
    ]
    doc: dict = {"description": description or f"{' + '.join(sorted(plan.have)) or '入力なし'} から {' + '.join(plan.goals)} を作る",
                 "type": "graph"}
    if params:
        doc["params"] = params
    doc["steps"] = steps
    return "\n".join(header) + "\n" + to_yaml(doc) + "\n"


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)


def to_yaml(v, indent: int = 0) -> str:
    """dict / list / スカラーだけの小さな YAML 書き出し。文字列は JSON の二重引用で出す（YAML として正しい）。"""
    pad = "  " * indent
    if isinstance(v, dict):
        lines = []
        for k, x in v.items():
            if isinstance(x, (dict, list)) and x:
                if isinstance(x, list) and all(not isinstance(i, (dict, list)) for i in x):
                    lines.append(f"{pad}{k}: [{', '.join(_scalar(i) for i in x)}]")
                else:
                    lines.append(f"{pad}{k}:\n{to_yaml(x, indent + 1)}")
            elif isinstance(x, (dict, list)):
                lines.append(f"{pad}{k}: {'{}' if isinstance(x, dict) else '[]'}")
            else:
                lines.append(f"{pad}{k}: {_scalar(x)}")
        return "\n".join(lines)
    if isinstance(v, list):
        lines = []
        for x in v:
            if isinstance(x, dict) and x:
                body = to_yaml(x, indent + 1).splitlines()
                lines.append(f"{pad}- {body[0].lstrip()}")
                lines.extend(body[1:])
            else:
                lines.append(f"{pad}- {_scalar(x)}")
        return "\n".join(lines)
    return pad + _scalar(v)


# ---------------------------------------------------------------- CLI


def _parse_from(items: list[str]) -> dict[str, str]:
    have: dict[str, str] = {}
    for it in items:
        t, _, p = it.partition("=")
        t = t.strip()
        if not TYPE_RE.match(t):
            raise SystemExit(_usage(f"--from の型 {t!r} が不正（英小文字・数字・. + -）"))
        have[t] = p.strip()
    return have


def _parse_params(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep or not k.strip():
            raise SystemExit(_usage(f"--param は 名前=値 か スキル/アクション.名前=値 で書く: {it!r}"))
        out[k.strip()] = v
    return out


def _usage(msg: str) -> int:
    print(f"エラー: {msg}", file=sys.stderr)
    return 2


def _io(a: Action) -> str:
    ins = ", ".join(sorted(set(a.inputs.values()))) or "（なし）"
    outs = ", ".join(o.type for o in a.outputs)
    return f"{ins} → {outs}"


def cmd_scan(args, roots: list[Path]) -> int:
    actions, problems, shadowed = load_all(roots)
    if args.json:
        json.dump({
            "roots": [r.as_posix() for r in roots],
            "actions": [{"key": a.key, "summary": a.summary, "inputs": sorted(set(a.inputs.values())),
                         "outputs": [o.type for o in a.outputs], "skill_dir": a.skill_dir.as_posix()}
                        for a in actions],
            "types": sorted({o.type for a in actions for o in a.outputs}),
            "problems": problems,
            "shadowed": [{"skill": n, "path": p.as_posix()} for n, p in shadowed],
        }, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    print("見たスキル置き場:")
    for r in roots:
        print(f"  {r.as_posix()}{'' if r.is_dir() else '（無い）'}")
    if not actions:
        print("\nchain.json を持つスキルが見つからない。")
    else:
        width = max(len(a.key) for a in actions)
        print(f"\nアクション（{len(actions)} 件）:")
        for a in actions:
            print(f"  {a.key.ljust(width)}  {_io(a)}  — {a.summary}")
        print("\n作れる型: " + ", ".join(sorted({o.type for a in actions for o in a.outputs})))
    for n, p in shadowed:
        print(f"（{n} は先に見つかった方を使う。無視: {p.as_posix()}）")
    if problems:
        print(f"\n読めなかった宣言（{len(problems)} 件。`check` で詳しく）:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
    return 0


def cmd_check(args, roots: list[Path]) -> int:
    if args.paths:
        dirs = [Path(p).parent if Path(p).name == CONTRACT_FILE else Path(p) for p in args.paths]
        problems: list[str] = []
        count = 0
        for d in dirs:
            if not (d / CONTRACT_FILE).is_file():
                problems.append(f"{d.as_posix()}: {CONTRACT_FILE} が無い")
                continue
            a, p = load_contract(d)
            count += len(a)
            problems.extend(p)
    else:
        actions, problems, _ = load_all(roots)
        count = len(actions)
    for p in problems:
        print(p, file=sys.stderr)
    print(f"アクション {count} 件、違反 {len(problems)} 件")
    return 1 if problems else 0


def _solve_from_args(args, roots: list[Path]) -> Plan:
    actions, problems, _ = load_all(roots)
    for p in problems:
        print(f"（読み飛ばし）{p}", file=sys.stderr)
    have = _parse_from(args.from_)
    return solve(actions, have, list(dict.fromkeys(args.to)), args.avoid)


def cmd_plan(args, roots: list[Path]) -> int:
    plan = _solve_from_args(args, roots)
    if args.json:
        json.dump({
            "inputs": plan.have,
            "goals": plan.goals,
            "steps": [{"step": a.step_name, "action": a.key, "inputs": sorted(set(a.inputs.values())),
                       "outputs": [o.type for o in a.outputs]} for a in plan.steps],
        }, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    for t, p in sorted(plan.have.items()):
        print(f"入力: {t} ← {p or '（実行時に渡す）'}")
    print(f"目的: {', '.join(plan.goals)}")
    if not plan.steps:
        print("\n手元の入力だけで足りている（実行するステップは無い）。")
        return 0
    print()
    for i, a in enumerate(plan.steps, 1):
        print(f"{i}. {a.key}  {_io(a)}  — {a.summary}")
    return 0


def cmd_build(args, roots: list[Path]) -> int:
    plan = _solve_from_args(args, roots)
    if not plan.steps:
        raise ChainError("手元の入力だけで目的の型が揃っている。DAG にするステップが無い。")
    text = render_dag(plan, _parse_params(args.param), args.description)
    if args.output and args.output != "-":
        out = Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"書き出しました: {out.as_posix()}（ステップ {len(plan.steps)} 個）")
    else:
        sys.stdout.write(text)
    for t, p in sorted(plan.have.items()):
        if not p and any(t in a.inputs.values() for a in plan.steps):
            print(f"実行時に渡す: {input_param(t)}=<{t} の絶対パス>", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="スキルのスクリプトをつないで Dagu v2 の DAG を組み立てる（LLM 不要）")
    ap.add_argument("--skills-dir", action="append", metavar="DIR",
                    help=f"走査するスキル置き場（複数可）。省略時は環境変数 {SKILLS_ENV}、それも無ければ各エージェントのホーム")
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="見つかったアクションと作れる型を並べる")
    s.add_argument("--json", action="store_true")

    c = sub.add_parser("check", help="chain.json を検査する")
    c.add_argument("paths", nargs="*", help="スキルのディレクトリか chain.json（省略時は見つかったもの全部）")

    for name, helptext in (("plan", "チェーンを決めて表示する"), ("build", "チェーンを Dagu v2 の DAG に書き出す")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--from", dest="from_", action="append", default=[], metavar="TYPE[=PATH]",
                       help="手元にある入力の型（PATH を省くと実行時に渡す）。複数可")
        p.add_argument("--to", action="append", required=True, metavar="TYPE", help="作りたい型。複数可")
        p.add_argument("--avoid", action="append", default=[], metavar="SKILL[/ACTION]",
                       help="使わないスキル・アクション。複数可")
        if name == "plan":
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("-o", "--output", help="書き出す先（省略か - で標準出力）")
            p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE",
                           help="アクションのパラメータの既定値。スキル/アクション.名前=値 で 1 つに絞れる")
            p.add_argument("--description", default="", help="DAG の説明")

    args = ap.parse_args(argv)
    roots = skill_roots(args.skills_dir)
    try:
        return {"scan": cmd_scan, "check": cmd_check, "plan": cmd_plan, "build": cmd_build}[args.command](args, roots)
    except ChainError as e:
        print(f"チェーンを組めない:\n{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
