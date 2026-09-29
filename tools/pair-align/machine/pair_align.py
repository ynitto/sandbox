#!/usr/bin/env python3
"""pair_align — 参照先のリポジトリ（実装⇔設計書）を読んで、自分の変更を練るステートマシンの下請け。

ステートマシン（同じフォルダの workflow.yaml）のうち、機械で決まる仕事だけをここに置く。
判断（参照先の前提・制約・その他、ずれ、変更案、影響範囲）はアクションの側でモデルが行う。

    explore --term 語   参照先を探す（graphify のグラフを必要なら作り直してから引く）
    impact  --term 語   自分のリポジトリで影響を受ける箇所を探す（同上）
    verify-plan         計画（.pair-align/plan.md）が決まった形か、根拠のパスが実在するかを検査する
    verify-apply        計画どおりに変えたか（参照先を変えてよいのは変更案があるときだけ）と、検査コマンドを確かめる

置き場所は `<リポジトリ>/.statemachine/pair_align/`。設定は同じフォルダの pair.json、
作業ファイルと graphify のグラフは `<リポジトリ>/.pair-align/` に置く。依存は python3 と git のみ
（graphify は任意）。

graphify のグラフは、リポジトリの HEAD と作業中の変更から作る「印」を控えておき、
explore / impact のたびに印が変わっていれば `graphify update` で作り直す（自動更新）。
グラフは参照先の中ではなく自分の `.pair-align/graph/` に書く（探すだけで参照先に何も書かない）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MACHINE_DIR = Path(__file__).resolve().parent
MACHINE_REL = ".statemachine/pair_align"
CONFIG_NAME = "pair.json"
DATA_DIRNAME = ".pair-align"
SIDES = {"impl": "実装", "design": "設計書"}

PLAN_HEADINGS = (
    "## やりたいこと",
    "## 参照先の前提",
    "## 参照先の制約",
    "## 参照先のその他",
    "## ずれ",
    "## 自分の変更案",
    "## 参照先の変更案",
    "## 影響範囲",
)
CITED_IN_PAIR = ("## 参照先の前提", "## 参照先の制約", "## 参照先のその他", "## ずれ")

MAX_TERMS = 12
GREP_LINES_PER_TERM = 20
GIT_TIMEOUT = 60
GRAPHIFY_TIMEOUT = 120
GRAPHIFY_UPDATE_TIMEOUT = 900
GRAPHIFY_BUDGET = 600
CHECK_TIMEOUT = 900

# graphify の出力からファイルを拾う。query は `[src=docs/api.md loc=L3]`、affected は `src/use.py:L4`。
_GRAPHIFY_SRC = re.compile(r"\bsrc=([^\s\]]+)|(?<![\w=])([^\s\[\]=]+):L\d+")
_PATHISH = re.compile(r"[\w@.\-]+(?:/[\w@.\-]+)+|[\w@\-]+\.[A-Za-z0-9]{1,8}")
_NONE_WORDS = ("なし", "無し")


class PairAlignError(Exception):
    """利用者が直せる設定・状態の誤り（終了コード 2）。"""


# ---------------------------------------------------------------- 下回り

def run(argv: list[str], cwd: Path, timeout: int, env: dict | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"({timeout} 秒で終わらなかったので打ち切りました)"
    except OSError as exc:
        return 127, f"(実行できませんでした: {exc})"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def load_config(machine_dir: Path) -> dict:
    path = machine_dir / CONFIG_NAME
    if not path.is_file():
        raise PairAlignError(f"設定がありません: {path}\n"
                             '  例: {"side": "impl", "pair_path": "../my-design"}')
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("side") not in SIDES:
        raise PairAlignError(f"{path} の side は impl か design です（今: {config.get('side')!r}）")
    if not config.get("pair_path"):
        raise PairAlignError(f"{path} の pair_path が空です（参照先のリポジトリのパスを書いてください）")
    config.setdefault("graphify", "auto")
    if config["graphify"] not in ("auto", "off"):
        raise PairAlignError(f"{path} の graphify は auto か off です（今: {config['graphify']!r}）")
    check = config.get("check")
    if check is not None and not (isinstance(check, list) and check and all(isinstance(a, str) for a in check)):
        raise PairAlignError(f'{path} の check はコマンドの配列です（例: ["python3", "-m", "pytest", "-q"]）')
    return config


class Ctx:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = load_config(MACHINE_DIR)
        self.side = self.config["side"]
        self.pair_side = "design" if self.side == "impl" else "impl"
        pair = Path(os.path.expanduser(self.config["pair_path"]))
        self.pair = (pair if pair.is_absolute() else root / pair).resolve()
        self.data = root / DATA_DIRNAME
        self.plan = self.data / "plan.md"
        if not self.pair.is_dir() or run(["git", "rev-parse", "--show-toplevel"], self.pair, GIT_TIMEOUT)[0]:
            raise PairAlignError(f"参照先の git リポジトリが見つかりません: {self.pair}\n"
                                 f"  {MACHINE_DIR / CONFIG_NAME} の pair_path を直してください")

    def repo(self, which: str) -> Path:
        return self.pair if which == "pair" else self.root


def repo_root(start: Path) -> Path:
    rc, out = run(["git", "rev-parse", "--show-toplevel"], start, GIT_TIMEOUT)
    if rc != 0:
        raise PairAlignError(f"git リポジトリではありません: {start}")
    return Path(out.strip())


def stamp(repo: Path) -> str:
    """リポジトリの今の中身を表す印（HEAD と、作業中の変更・未追跡のファイルの中身）。"""
    head = run(["git", "rev-parse", "HEAD"], repo, GIT_TIMEOUT)[1].strip()
    diff = run(["git", "diff", "HEAD", "--", ".", f":(exclude){DATA_DIRNAME}"], repo, GIT_TIMEOUT)[1]
    untracked = run(["git", "ls-files", "--others", "--exclude-standard", "--", ".",
                     f":(exclude){DATA_DIRNAME}"], repo, GIT_TIMEOUT)[1].splitlines()
    h = hashlib.sha256(f"{head}\n{diff}".encode())
    for name in sorted(untracked):
        h.update(name.encode())
        try:
            h.update((repo / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()


# ---------------------------------------------------------------- 探す（graphify + git grep）

def ensure_graph(ctx: Ctx, which: str) -> tuple[str | None, Path | None, str]:
    """graphify のグラフを用意する。印が変わっていれば作り直す。戻り値は（実行ファイル, グラフ, 状態）。"""
    if ctx.config["graphify"] == "off":
        return None, None, "off"
    exe = shutil.which("graphify")
    if not exe:
        return None, None, "not-installed"
    out_dir = ctx.data / "graph" / which
    graph = out_dir / "graph.json"
    stamp_file = out_dir / "stamp"
    now = stamp(ctx.repo(which))
    if graph.is_file() and stamp_file.is_file() and stamp_file.read_text(encoding="utf-8") == now:
        return exe, graph, "fresh"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GRAPHIFY_OUT": str(out_dir)}
    # --force: 削除や改名でノードが減っても作り直した方を採る（古いノードを残さない）。
    rc, out = run([exe, "update", ".", "--force"], ctx.repo(which), GRAPHIFY_UPDATE_TIMEOUT, env)
    if rc != 0 or not graph.is_file():
        print(f"  graphify update に失敗しました（{rc}）: {out.strip()[:200]}", file=sys.stderr)
        return None, None, "update-failed"
    stamp_file.write_text(now, encoding="utf-8")
    return exe, graph, "updated"


def search(ctx: Ctx, which: str, terms: list[str], graph_cmd: str) -> tuple[str, list[str], str]:
    """語ごとに graphify と git grep で引き、（本文, 候補のファイル, graphify の状態）を返す。"""
    repo = ctx.repo(which)
    exe, graph, note = ensure_graph(ctx, which)
    lines: list[str] = []
    files: list[str] = []

    def add_file(path: str) -> None:
        if path and path not in files and (repo / path).is_file():
            files.append(path)

    if exe and graph:
        lines += [f"## graphify {graph_cmd}", ""]
        for term in terms:
            argv = [exe, graph_cmd, term, "--graph", str(graph)]
            if graph_cmd == "query":
                argv += ["--budget", str(GRAPHIFY_BUDGET)]
            _, out = run(argv, repo, GRAPHIFY_TIMEOUT)
            out = "\n".join(ln for ln in out.splitlines() if not ln.startswith("[graphify] note"))
            lines += [f"### {term}", "", "```", out.strip() or "(該当なし)", "```", ""]
            for m in _GRAPHIFY_SRC.finditer(out):
                add_file(m.group(1) or m.group(2))

    lines += ["## 文字列の一致（git grep）", ""]
    for term in terms:
        # --untracked: まだコミットしていない新しいファイルも拾う（.gitignore に載っているものは除く）。
        rc, out = run(["git", "grep", "--untracked", "-n", "-I", "-i", "-F", "--max-count", "3", "-e", term, "--", ".",
                       f":(exclude){DATA_DIRNAME}", f":(exclude){MACHINE_REL}"], repo, GIT_TIMEOUT)
        hits = out.splitlines()[:GREP_LINES_PER_TERM] if rc == 0 else []
        lines += [f"### {term}", "", *([f"- {h[:200]}" for h in hits] or ["- (該当なし)"]), ""]
        for h in hits:
            add_file(h.split(":", 1)[0])
    return "\n".join(lines), files, note


def terms_of(args: argparse.Namespace) -> list[str]:
    terms: list[str] = []
    for t in args.term or []:
        t = t.strip()
        if t and t not in terms:
            terms.append(t)
    if not terms:
        raise PairAlignError("検索語を --term で渡してください")
    return terms[:MAX_TERMS]


def write_report(ctx: Ctx, name: str, title: str, repo: Path, terms: list[str], note: str,
                 body: str, files: list[str]) -> Path:
    ctx.data.mkdir(parents=True, exist_ok=True)
    path = ctx.data / name
    path.write_text("\n".join([
        f"# {title}", "", f"- リポジトリ: {repo}", f"- 検索語: {', '.join(terms)}", f"- graphify: {note}", "",
        body, "## 候補のファイル", "",
        *([f"- {p}" for p in files] or ["- (見つからない)"]), "",
    ]), encoding="utf-8")
    return path


def cmd_explore(ctx: Ctx, args: argparse.Namespace) -> int:
    terms = terms_of(args)
    body, files, note = search(ctx, "pair", terms, "query")
    path = write_report(ctx, "explore.md", f"参照先（{SIDES[ctx.pair_side]}）で関係する箇所",
                        ctx.pair, terms, note, body, files)
    # verify-apply が「どちらを変えたか」を測るための印。計画を練る間は何も変えないので、探すたびに取り直してよい。
    (ctx.data / "before.json").write_text(json.dumps(
        {"own": stamp(ctx.root), "pair": stamp(ctx.pair)}, indent=2) + "\n", encoding="utf-8")
    print(f"FOUND {len(files)} files (graphify: {note})")
    print(f"  詳細: {path.relative_to(ctx.root)}")
    return 0


def cmd_impact(ctx: Ctx, args: argparse.Namespace) -> int:
    terms = terms_of(args)
    body, files, note = search(ctx, "own", terms, "affected")
    path = write_report(ctx, "impact.md", f"自分のリポジトリ（{SIDES[ctx.side]}）で影響を受ける箇所",
                        ctx.root, terms, note, body, files)
    print(f"FOUND {len(files)} files (graphify: {note})")
    print(f"  詳細: {path.relative_to(ctx.root)}")
    return 0


# ---------------------------------------------------------------- 計画の検査

def sections(text: str, headings: tuple[str, ...]) -> tuple[list[str], dict[str, str]]:
    """決まった見出しが順にそろい、中身（コメントを除く）が空でないかを見る。"""
    problems: list[str] = []
    found = []
    for heading in headings:
        m = re.search(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE)
        if m:
            found.append((m.start(), heading, m.end()))
        else:
            problems.append(f"見出しがありません: {heading}")
    found.sort()
    if [h for _, h, _ in found] != [h for h in headings if h in {f[1] for f in found}]:
        problems.append("見出しの順番がテンプレートと違います")
    bodies = {}
    for _, heading, end in found:
        nxt = re.search(r"^## ", text[end:], re.MULTILINE)
        body = re.sub(r"<!--.*?-->", "", text[end:end + nxt.start()] if nxt else text[end:], flags=re.DOTALL)
        bodies[heading] = body.strip()
        if not bodies[heading]:
            problems.append(f"見出しの中身が空です: {heading}")
    return problems, bodies


def is_none(body: str) -> bool:
    return re.sub(r"^[-*]\s*", "", body.strip()).strip("。． ") in _NONE_WORDS


def items(body: str) -> list[str]:
    return [ln.strip()[2:].strip() for ln in body.splitlines() if ln.strip().startswith(("- ", "* "))]


def cited(line: str, repo: Path) -> list[str]:
    paths = []
    for m in _PATHISH.finditer(line):
        cand = re.sub(r":\d+(-\d+)?$", "", m.group(0))
        cand = cand[2:] if cand.startswith("./") else cand
        if cand and (repo / cand).exists():
            paths.append(cand)
    return paths


def verify_plan_text(text: str, root: Path, pair: Path) -> list[str]:
    problems, bodies = sections(text, PLAN_HEADINGS)
    if problems:
        return problems
    for heading in CITED_IN_PAIR:
        if is_none(bodies[heading]):
            continue
        listed = items(bodies[heading])
        if not listed:
            problems.append(f"{heading} は箇条書きにしてください（無ければ「なし」）")
        for item in listed:
            if not cited(item, pair):
                problems.append(f"{heading} の項目に、参照先に実在する根拠のパスがありません: {item[:80]}")
    drift = not is_none(bodies["## ずれ"])
    pair_change = not is_none(bodies["## 参照先の変更案"])
    impact = not is_none(bodies["## 影響範囲"])
    if drift and not pair_change:
        problems.append("ずれがあるのに、参照先の変更案が「なし」です（ずれを残すなら、ずれではなくその他に書く）")
    if not drift and pair_change:
        problems.append("ずれが「なし」なのに、参照先の変更案があります")
    if pair_change and not impact:
        problems.append("参照先の変更案があるのに、影響範囲が「なし」です")
    if impact:
        for item in items(bodies["## 影響範囲"]) or [bodies["## 影響範囲"]]:
            if not cited(item, root):
                problems.append(f"影響範囲の項目に、自分のリポジトリに実在するパスがありません: {item[:80]}")
    return problems


def cmd_verify_plan(ctx: Ctx, args: argparse.Namespace) -> int:
    if not ctx.plan.is_file():
        print(f"計画がありません: {DATA_DIRNAME}/plan.md", file=sys.stderr)
        return 1
    problems = verify_plan_text(ctx.plan.read_text(encoding="utf-8"), ctx.root, ctx.pair)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print("OK plan")
    return 0


# ---------------------------------------------------------------- 変えたあとの検査

def run_check(repo: Path, command: list[str] | None, label: str) -> list[str]:
    if not command:
        return []
    rc, out = run(command, repo, CHECK_TIMEOUT)
    if rc == 0:
        return []
    tail = "\n".join(out.strip().splitlines()[-20:])
    return [f"{label}の検査が失敗しました（{rc}）: {' '.join(command)}\n{tail}"]


def cmd_verify_apply(ctx: Ctx, args: argparse.Namespace) -> int:
    before_file = ctx.data / "before.json"
    if not ctx.plan.is_file() or not before_file.is_file():
        print("計画か、探したときの印がありません（計画からやり直してください）", file=sys.stderr)
        return 1
    _, bodies = sections(ctx.plan.read_text(encoding="utf-8"), PLAN_HEADINGS)
    before = json.loads(before_file.read_text(encoding="utf-8"))
    own_changed = stamp(ctx.root) != before["own"]
    pair_changed = stamp(ctx.pair) != before["pair"]
    want_own = not is_none(bodies.get("## 自分の変更案", "なし"))
    want_pair = not is_none(bodies.get("## 参照先の変更案", "なし"))

    problems = []
    if want_own and not own_changed:
        problems.append("自分の変更案があるのに、自分のリポジトリが変わっていません")
    if want_pair and not pair_changed:
        problems.append("参照先の変更案があるのに、参照先のリポジトリが変わっていません")
    if not want_pair and pair_changed:
        problems.append("参照先の変更案は「なし」なのに、参照先のリポジトリが変わっています（戻してください）")
    problems += run_check(ctx.root, ctx.config.get("check"), SIDES[ctx.side])
    if pair_changed:
        # 参照先の検査は、参照先に置いた同じマシンの設定（pair.json の check）を使う。
        pair_machine = ctx.pair / MACHINE_REL
        if (pair_machine / CONFIG_NAME).is_file():
            problems += run_check(ctx.pair, load_config(pair_machine).get("check"), SIDES[ctx.pair_side])
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print(f"OK own={'changed' if own_changed else 'same'} pair={'changed' if pair_changed else 'same'}")
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pair_align.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("explore", "参照先を探す"), ("impact", "自分のリポジトリで影響を受ける箇所を探す")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--term", action="append", help="検索語（繰り返し可）")
    sub.add_parser("verify-plan", help="計画が決まった形かを検査する")
    sub.add_parser("verify-apply", help="計画どおりに変えたかを検査する")
    return p


COMMANDS = {"explore": cmd_explore, "impact": cmd_impact,
            "verify-plan": cmd_verify_plan, "verify-apply": cmd_verify_apply}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](Ctx(repo_root(Path.cwd())), args)
    except PairAlignError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
