#!/usr/bin/env python3
"""concord — 参照先のリポジトリ（実装⇔設計書）を読んで、自分の変更を練るステートマシンの下請け。

ステートマシン（同じフォルダの workflow.yaml）のうち、機械で決まる仕事だけをここに置く。
判断（参照先の前提・制約・その他、ずれ、変更案、影響範囲）はアクションの側でモデルが行う。

    explore --term 語   参照先を探す（graphify のグラフを必要なら作り直してから引く）
    impact  --term 語   自分のリポジトリで影響を受ける箇所を探す（同上）
    verify-plan         計画（.concord/plan.md）が決まった形か、根拠のパスが実在するかを検査する。
                        参照先の変更案があれば、それを自分に適用したときの影響範囲を測り、計画の影響範囲が
                        測ったファイルをすべて挙げているかも検査する
    verify-apply        計画どおりに変えたか（参照先を変えてよいのは変更案があるときだけ）と、検査コマンドを確かめる。
                        参照先を変えたら、実際の変更から影響範囲を測り直し、測ったファイルを直したか
                        「変更不要」としたかを検査する

置き場所は `<リポジトリ>/.statemachine/concord/`。設定は同じフォルダの concord.json、
作業ファイルと graphify のグラフは `<リポジトリ>/.concord/` に置く。依存は python3 と git のみ
（graphify は任意）。

graphify のグラフは、リポジトリの HEAD と作業中の変更から作る「印」を控えておき、
explore / impact のたびに印が変わっていれば `graphify update` で作り直す（自動更新）。
グラフは参照先の中ではなく自分の `.concord/graph/` に書く（探すだけで参照先に何も書かない）。
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
MACHINE_REL = ".statemachine/concord"
CONFIG_NAME = "concord.json"
DATA_DIRNAME = ".concord"
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
NO_CHANGE_MARK = "変更不要"
MAX_MEASURED = 40

# 影響範囲を測る語。計画からは `…` で囲んだ名前、参照先の実際の差分からは定義・見出し・`…` を拾う。
_BACKTICK = re.compile(r"`([^`\n]{2,60})`")
_DIFF_TERMS = (
    re.compile(r"^[+-]\s*(?:async\s+)?def\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^[+-]\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^[+-]\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]{2,})"),
    re.compile(r"^[+-]\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]{2,})\s*="),
    re.compile(r"^[+-]\s*(?:pub\s+)?(?:fn|func|interface|type|struct|enum)\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^[+-]\s*#{1,6}\s+(.{2,60}?)\s*#*\s*$"),
)
_WORDLIKE = re.compile(r"^[A-Za-z0-9_$]+$")


class ConcordError(Exception):
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
        raise ConcordError(f"設定がありません: {path}\n"
                             '  例: {"side": "impl", "ref_path": "../my-design"}')
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("side") not in SIDES:
        raise ConcordError(f"{path} の side は impl か design です（今: {config.get('side')!r}）")
    if not config.get("ref_path"):
        raise ConcordError(f"{path} の ref_path が空です（参照先のリポジトリのパスを書いてください）")
    config.setdefault("graphify", "auto")
    if config["graphify"] not in ("auto", "off"):
        raise ConcordError(f"{path} の graphify は auto か off です（今: {config['graphify']!r}）")
    check = config.get("check")
    if check is not None and not (isinstance(check, list) and check and all(isinstance(a, str) for a in check)):
        raise ConcordError(f'{path} の check はコマンドの配列です（例: ["python3", "-m", "pytest", "-q"]）')
    return config


class Ctx:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = load_config(MACHINE_DIR)
        self.side = self.config["side"]
        self.ref_side = "design" if self.side == "impl" else "impl"
        ref = Path(os.path.expanduser(self.config["ref_path"]))
        self.ref = (ref if ref.is_absolute() else root / ref).resolve()
        self.data = root / DATA_DIRNAME
        self.plan = self.data / "plan.md"
        if not self.ref.is_dir() or run(["git", "rev-parse", "--show-toplevel"], self.ref, GIT_TIMEOUT)[0]:
            raise ConcordError(f"参照先の git リポジトリが見つかりません: {self.ref}\n"
                                 f"  {MACHINE_DIR / CONFIG_NAME} の ref_path を直してください")

    def repo(self, which: str) -> Path:
        return self.ref if which == "ref" else self.root


def repo_root(start: Path) -> Path:
    rc, out = run(["git", "rev-parse", "--show-toplevel"], start, GIT_TIMEOUT)
    if rc != 0:
        raise ConcordError(f"git リポジトリではありません: {start}")
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


def head(repo: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], repo, GIT_TIMEOUT)[1].strip()


def dirty_files(repo: Path) -> dict[str, str]:
    """作業中の変更・未追跡のファイル → 中身のハッシュ（消えていれば "deleted"）。"""
    out = run(["git", "status", "--porcelain", "--untracked-files=all", "--", ".",
               f":(exclude){DATA_DIRNAME}"], repo, GIT_TIMEOUT)[1]
    files = {}
    for line in out.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        try:
            files[path] = hashlib.sha256((repo / path).read_bytes()).hexdigest()
        except OSError:
            files[path] = "deleted"
    return files


def changed_since(repo: Path, before_head: str, before_files: dict[str, str]) -> set[str]:
    """控えたときから中身が変わったファイル。"""
    now = dirty_files(repo)
    changed = {p for p in set(now) | set(before_files) if now.get(p) != before_files.get(p)}
    if before_head and head(repo) != before_head:  # 途中でコミットされても取りこぼさない
        rc, out = run(["git", "diff", "--name-only", before_head, "HEAD"], repo, GIT_TIMEOUT)
        if rc == 0:
            changed |= set(out.splitlines())
    return changed


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
        # 識別子は語単位（-w）で引く。`hello` で `helloWorld` を拾って影響範囲を水増ししない。
        word = ["-w"] if _WORDLIKE.match(term) else []
        rc, out = run(["git", "grep", "--untracked", "-n", "-I", "-i", "-F", *word, "--max-count", "3", "-e", term,
                       "--", ".", f":(exclude){DATA_DIRNAME}", f":(exclude){MACHINE_REL}"], repo, GIT_TIMEOUT)
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
        raise ConcordError("検索語を --term で渡してください")
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
    body, files, note = search(ctx, "ref", terms, "query")
    path = write_report(ctx, "explore.md", f"参照先（{SIDES[ctx.ref_side]}）で関係する箇所",
                        ctx.ref, terms, note, body, files)
    # verify-apply が「どちらを変えたか」を測るための印。計画を練る間は何も変えないので、探すたびに取り直してよい。
    (ctx.data / "before.json").write_text(json.dumps(
        {"own": stamp(ctx.root), "ref": stamp(ctx.ref), "own_head": head(ctx.root),
         "own_files": dirty_files(ctx.root)}, indent=2) + "\n", encoding="utf-8")
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


# ---------------------------------------------------------------- 影響範囲を測る

def unique(terms) -> list[str]:
    out: list[str] = []
    for t in terms:
        t = t.strip()
        if 2 <= len(t) <= 60 and t not in out:
            out.append(t)
    return out


def terms_from_plan(bodies: dict[str, str]) -> list[str]:
    return unique(_BACKTICK.findall(bodies.get("## 参照先の変更案", "") + "\n" + bodies.get("## ずれ", "")))


def terms_from_diff(repo: Path) -> list[str]:
    """参照先の実際の変更（作業中の差分と、新しいファイル）から、変わった名前を拾う。"""
    diff = run(["git", "diff", "HEAD", "--", ".", f":(exclude){DATA_DIRNAME}"], repo, GIT_TIMEOUT)[1]
    terms = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        for pat in _DIFF_TERMS:
            m = pat.match(line)
            if m:
                terms.append(m.group(1))
        terms += _BACKTICK.findall(line)
    for name in run(["git", "ls-files", "--others", "--exclude-standard", "--", ".",
                     f":(exclude){DATA_DIRNAME}"], repo, GIT_TIMEOUT)[1].splitlines():
        try:
            text = (repo / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        terms += [m.group(1) for ln in text.splitlines() for pat in _DIFF_TERMS
                  for m in [pat.match("+" + ln)] if m]
    return unique(terms)


def measure(ctx: Ctx, terms: list[str], name: str, title: str) -> list[str]:
    """参照先の変更で動く名前から、自分のリポジトリで影響を受けるファイルを測る（graphify affected + git grep）。"""
    terms = terms[:MAX_TERMS]
    body, files, note = search(ctx, "own", terms, "affected")
    files = files[:MAX_MEASURED]
    write_report(ctx, name, title, ctx.root, terms, note, body, files)
    return files


def listed_paths(body: str, repo: Path, only_no_change: bool = False) -> set[str]:
    paths: set[str] = set()
    for item in items(body) or [body]:
        if only_no_change and NO_CHANGE_MARK not in item:
            continue
        paths.update(cited(item, repo))
    return paths


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


def verify_plan_text(text: str, root: Path, ref: Path) -> list[str]:
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
            if not cited(item, ref):
                problems.append(f"{heading} の項目に、参照先に実在する根拠のパスがありません: {item[:80]}")
    drift = not is_none(bodies["## ずれ"])
    ref_change = not is_none(bodies["## 参照先の変更案"])
    impact = not is_none(bodies["## 影響範囲"])
    if drift and not ref_change:
        problems.append("ずれがあるのに、参照先の変更案が「なし」です（ずれを残すなら、ずれではなくその他に書く）")
    if not drift and ref_change:
        problems.append("ずれが「なし」なのに、参照先の変更案があります")
    if ref_change and not impact:
        problems.append("参照先の変更案があるのに、影響範囲が「なし」です")
    if impact:
        for item in items(bodies["## 影響範囲"]) or [bodies["## 影響範囲"]]:
            if not cited(item, root):
                problems.append(f"影響範囲の項目に、自分のリポジトリに実在するパスがありません: {item[:80]}")
    if ref_change and not terms_from_plan(bodies):
        problems.append("参照先の変更案で変わる名前（関数・API・用語・見出し）を `…` で囲んでください"
                        "（影響範囲を測る語になります）")
    return problems


def cmd_verify_plan(ctx: Ctx, args: argparse.Namespace) -> int:
    if not ctx.plan.is_file():
        print(f"計画がありません: {DATA_DIRNAME}/plan.md", file=sys.stderr)
        return 1
    text = ctx.plan.read_text(encoding="utf-8")
    problems = verify_plan_text(text, ctx.root, ctx.ref)
    measured: list[str] = []
    if not problems:
        _, bodies = sections(text, PLAN_HEADINGS)
        if not is_none(bodies["## 参照先の変更案"]):
            # 参照先の変更案を自分に適用したときの影響範囲を測り、計画がそれを漏れなく挙げているかを見る。
            measured = measure(ctx, terms_from_plan(bodies), "impact.md",
                               f"参照先の変更案を自分のリポジトリ（{SIDES[ctx.side]}）に適用したときの影響範囲（測定）")
            missing = [p for p in measured if p not in listed_paths(bodies["## 影響範囲"], ctx.root)]
            if missing:
                problems.append(
                    "測った影響範囲のうち、計画の影響範囲に無いファイルがあります（直すなら直し方を、"
                    f"直さなくてよいなら「{NO_CHANGE_MARK}: 理由」を添えて影響範囲に足してください）: "
                    + ", ".join(missing) + f"（詳細: {DATA_DIRNAME}/impact.md）")
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print("OK plan" + (f"（影響範囲を測った: {len(measured)} files、{DATA_DIRNAME}/impact.md）" if measured else ""))
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
    ref_changed = stamp(ctx.ref) != before["ref"]
    want_own = not is_none(bodies.get("## 自分の変更案", "なし"))
    want_ref = not is_none(bodies.get("## 参照先の変更案", "なし"))

    problems = []
    if want_own and not own_changed:
        problems.append("自分の変更案があるのに、自分のリポジトリが変わっていません")
    if want_ref and not ref_changed:
        problems.append("参照先の変更案があるのに、参照先のリポジトリが変わっていません")
    if not want_ref and ref_changed:
        problems.append("参照先の変更案は「なし」なのに、参照先のリポジトリが変わっています（戻してください）")
    measured: list[str] = []
    if ref_changed:
        # 参照先を実際に変えたあとの影響範囲を測り直す。測ったファイルは、直したか「変更不要」と書いたかのどちらか。
        measured = measure(ctx, unique(terms_from_diff(ctx.ref) + terms_from_plan(bodies)), "impact-after.md",
                           f"参照先の変更後に、自分のリポジトリ（{SIDES[ctx.side]}）で影響を受ける範囲（測定）")
        touched = changed_since(ctx.root, before.get("own_head", ""), before.get("own_files", {}))
        waived = listed_paths(bodies.get("## 影響範囲", ""), ctx.root, only_no_change=True)
        untouched = [p for p in measured if p not in touched and p not in waived]
        if untouched:
            problems.append(
                "参照先の変更で影響を受けるのに、直していないファイルがあります（直すか、計画の影響範囲に"
                f"「{NO_CHANGE_MARK}: 理由」を書いてください）: " + ", ".join(untouched)
                + f"（詳細: {DATA_DIRNAME}/impact-after.md）")
    problems += run_check(ctx.root, ctx.config.get("check"), SIDES[ctx.side])
    if ref_changed:
        # 参照先の検査は、参照先に置いた同じマシンの設定（concord.json の check）を使う。
        ref_machine = ctx.ref / MACHINE_REL
        if (ref_machine / CONFIG_NAME).is_file():
            problems += run_check(ctx.ref, load_config(ref_machine).get("check"), SIDES[ctx.ref_side])
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print(f"OK own={'changed' if own_changed else 'same'} ref={'changed' if ref_changed else 'same'}"
          + (f" impact={len(measured)} files（{DATA_DIRNAME}/impact-after.md）" if ref_changed else ""))
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="concord.py", description=__doc__.split("\n")[0])
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
    except ConcordError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
