#!/usr/bin/env python3
"""codd — 参照先のリポジトリ（実装⇔設計書。いくつでも）を読んで、自分の変更を練るステートマシンの下請け。

ステートマシン（同じフォルダの workflow.yaml）のうち、機械で決まる仕事だけをここに置く。
判断（参照先の前提・制約・その他、ずれ、変更案、影響範囲）はアクションの側でモデルが行う。

    show [--phase P]    この側・参照先の一覧と、使うスキル（計画を練るとき / 変えるとき）を示す
    explore --term 語   参照先を探す（graphify のグラフを必要なら作り直してから引く）。--ref で絞れる
    impact  --term 語   自分のリポジトリで影響を受ける箇所を探す（同上）
    verify-plan         計画（.codd/plan.md）が決まった形か、根拠のパスが実在するかを検査する。
                        参照先の変更案があれば、それを自分に適用したときの影響範囲を測り、計画の影響範囲が
                        測ったファイルをすべて挙げているかも検査する
    verify-apply        計画どおりに変えたか（変えてよい参照先は変更案に挙げたものだけ）と、検査コマンドを確かめる。
                        参照先を変えたら、実際の変更から影響範囲を測り直し、測ったファイルを直したか
                        「変更不要」としたかを検査する

置き場所は `<リポジトリ>/.statemachine/codd/`。設定は同じフォルダの codd.json、
作業ファイルと graphify のグラフは `<リポジトリ>/.codd/` に置く。依存は python3 と git のみ
（graphify は任意）。

参照先が複数あるとき、計画の根拠は `名前:パス`（codd.json の refs の name）で書く。
参照先が 1 つなら、パスだけでよい。

graphify のグラフは、リポジトリの HEAD と作業中の変更から作る「印」を控えておき、
explore / impact のたびに印が変わっていれば `graphify update` で作り直す（自動更新）。
グラフは参照先の中ではなく自分の `.codd/graph/` に書く（探すだけで参照先に何も書かない）。
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
from dataclasses import dataclass, field
from pathlib import Path

MACHINE_DIR = Path(__file__).resolve().parent
MACHINE_REL = ".statemachine/codd"
CONFIG_NAME = "codd.json"
DATA_DIRNAME = ".codd"
SIDES = {"impl": "実装", "design": "設計書"}
PHASES = {"plan": "計画を練るとき", "apply": "変えるとき"}

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
CITED_IN_REFS = ("## 参照先の前提", "## 参照先の制約", "## 参照先のその他", "## ずれ")

MAX_TERMS = 12
GREP_LINES_PER_TERM = 20
GIT_TIMEOUT = 60
GRAPHIFY_TIMEOUT = 120
GRAPHIFY_UPDATE_TIMEOUT = 900
GRAPHIFY_BUDGET = 600
CHECK_TIMEOUT = 900

# graphify の出力からファイルを拾う。query は `[src=docs/api.md loc=L3]`、affected は `src/use.py:L4`。
_GRAPHIFY_SRC = re.compile(r"\bsrc=([^\s\]]+)|(?<![\w=])([^\s\[\]=]+):L\d+")
# 根拠のパス。`名前:パス` の名前は参照先の name（複数あるとき）。末尾の `:行` は落とす。
_CITE = re.compile(r"(?:(?P<ref>[A-Za-z0-9_.-]+):)?"
                   r"(?P<path>[\w@.\-]+(?:/[\w@.\-]+)+|[\w@\-]+\.[A-Za-z0-9]{1,8})(?::\d+(?:-\d+)?)?")
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_SKILL = re.compile(r"^[A-Za-z0-9_.:-]+$")
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


class CoddError(Exception):
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


def skill_list(value, where: str) -> list[str]:
    if value is None:
        return []
    if not (isinstance(value, list) and all(isinstance(s, str) and _SKILL.match(s) for s in value)):
        raise CoddError(f"{where} はスキル名の配列です（例: [\"tdd\", \"doc-writer\"]）")
    return list(value)


def load_config(machine_dir: Path) -> dict:
    path = machine_dir / CONFIG_NAME
    if not path.is_file():
        raise CoddError(f"設定がありません: {path}\n"
                        '  例: {"side": "impl", "refs": [{"name": "docs", "path": "../my-docs"}]}')
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("side") not in SIDES:
        raise CoddError(f"{path} の side は impl か design です（今: {config.get('side')!r}）")
    if "refs" not in config and config.get("ref_path"):  # 参照先が 1 つだった頃の書き方
        config["refs"] = [{"path": config["ref_path"]}]
    refs = config.get("refs")
    if not (isinstance(refs, list) and refs):
        raise CoddError(f"{path} の refs が空です（参照先のリポジトリを 1 つ以上書いてください）")
    names = set()
    for i, ref in enumerate(refs):
        if not (isinstance(ref, dict) and isinstance(ref.get("path"), str) and ref["path"]):
            raise CoddError(f"{path} の refs[{i}] には path が要ります")
        ref.setdefault("name", Path(ref["path"].rstrip("/\\")).name)
        if not _NAME.match(ref["name"]) or ref["name"] in names:
            raise CoddError(f"{path} の refs[{i}] の name が不正か重複しています: {ref['name']!r}"
                            "（英数字と _ . - で、参照先ごとに違う名前）")
        names.add(ref["name"])
        ref["skills"] = skill_list(ref.get("skills"), f"{path} の refs[{i}].skills")
    skills = config.get("skills") or {}
    if not isinstance(skills, dict) or set(skills) - set(PHASES):
        raise CoddError(f"{path} の skills は {{\"plan\": [...], \"apply\": [...]}} の形です")
    config["skills"] = {phase: skill_list(skills.get(phase), f"{path} の skills.{phase}") for phase in PHASES}
    config.setdefault("graphify", "auto")
    if config["graphify"] not in ("auto", "off"):
        raise CoddError(f"{path} の graphify は auto か off です（今: {config['graphify']!r}）")
    check = config.get("check")
    if check is not None and not (isinstance(check, list) and check and all(isinstance(a, str) for a in check)):
        raise CoddError(f'{path} の check はコマンドの配列です（例: ["python3", "-m", "pytest", "-q"]）')
    return config


@dataclass
class Ref:
    name: str
    path: Path
    config: dict | None      # 参照先に置いた同じマシンの設定（あれば）
    entry_skills: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        side = (self.config or {}).get("side")
        return SIDES.get(side, "参照先")

    @property
    def apply_skills(self) -> list[str]:
        """この参照先を変えるときのスキル。codd.json の refs の skills、無ければ参照先自身の skills.apply。"""
        return self.entry_skills or (self.config or {}).get("skills", {}).get("apply", [])

    @property
    def check(self) -> list[str] | None:
        return (self.config or {}).get("check")


class Ctx:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = load_config(MACHINE_DIR)
        self.side = self.config["side"]
        self.data = root / DATA_DIRNAME
        self.plan = self.data / "plan.md"
        self.refs: list[Ref] = []
        for entry in self.config["refs"]:
            path = Path(os.path.expanduser(entry["path"]))
            path = (path if path.is_absolute() else root / path).resolve()
            if not path.is_dir() or run(["git", "rev-parse", "--show-toplevel"], path, GIT_TIMEOUT)[0]:
                raise CoddError(f"参照先 {entry['name']} の git リポジトリが見つかりません: {path}\n"
                                f"  {MACHINE_DIR / CONFIG_NAME} の refs を直してください")
            ref_machine = path / MACHINE_REL
            ref_config = None
            if (ref_machine / CONFIG_NAME).is_file():
                try:
                    ref_config = load_config(ref_machine)
                except CoddError:
                    ref_config = None  # 参照先側の設定の誤りは、参照先で直す。ここでは読むだけ
            self.refs.append(Ref(entry["name"], path, ref_config, entry["skills"]))

    def ref(self, name: str) -> Ref:
        for r in self.refs:
            if r.name == name:
                return r
        raise CoddError(f"参照先 {name!r} はありません（{', '.join(r.name for r in self.refs)}）")

    def graph_key(self, repo: Path) -> str:
        if repo == self.root:
            return "own"
        return "ref-" + next(r.name for r in self.refs if r.path == repo)


def repo_root(start: Path) -> Path:
    rc, out = run(["git", "rev-parse", "--show-toplevel"], start, GIT_TIMEOUT)
    if rc != 0:
        raise CoddError(f"git リポジトリではありません: {start}")
    return Path(out.strip())


def stamp(repo: Path) -> str:
    """リポジトリの今の中身を表す印（HEAD と、作業中の変更・未追跡のファイルの中身）。"""
    h = hashlib.sha256(f"{head(repo)}\n".encode())
    h.update(run(["git", "diff", "HEAD", "--", ".", f":(exclude){DATA_DIRNAME}"], repo, GIT_TIMEOUT)[1].encode())
    untracked = run(["git", "ls-files", "--others", "--exclude-standard", "--", ".",
                     f":(exclude){DATA_DIRNAME}"], repo, GIT_TIMEOUT)[1].splitlines()
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


# ---------------------------------------------------------------- 設定を示す

def skill_words(names: list[str]) -> str:
    # `名前` スキル の形で書く。statemachine-use の実行ハーネスはこの表記でスキルを読み込む。
    return "、".join(f"`{n}` スキル" for n in names) if names else "なし"


def cmd_show(ctx: Ctx, args: argparse.Namespace) -> int:
    print(f"この側: {SIDES[ctx.side]}（{ctx.side}）  {ctx.root}")
    print("参照先:")
    for r in ctx.refs:
        print(f"  - {r.name}: {r.label}  {r.path}")
    phases = [args.phase] if args.phase else list(PHASES)
    for phase in phases:
        print(f"使うスキル（{PHASES[phase]}）:")
        print(f"  - 自分: {skill_words(ctx.config['skills'][phase])}")
        if phase == "apply":
            for r in ctx.refs:
                print(f"  - {r.name} を変えるとき: {skill_words(r.apply_skills)}")
    if len(ctx.refs) > 1:
        print("計画の根拠は `名前:パス` で書く（例: " + f"{ctx.refs[0].name}:docs/api.md）")
    return 0


# ---------------------------------------------------------------- 探す（graphify + git grep）

def ensure_graph(ctx: Ctx, repo: Path) -> tuple[str | None, Path | None, str]:
    """graphify のグラフを用意する。印が変わっていれば作り直す。戻り値は（実行ファイル, グラフ, 状態）。"""
    if ctx.config["graphify"] == "off":
        return None, None, "off"
    exe = shutil.which("graphify")
    if not exe:
        return None, None, "not-installed"
    out_dir = ctx.data / "graph" / ctx.graph_key(repo)
    graph = out_dir / "graph.json"
    stamp_file = out_dir / "stamp"
    now = stamp(repo)
    if graph.is_file() and stamp_file.is_file() and stamp_file.read_text(encoding="utf-8") == now:
        return exe, graph, "fresh"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GRAPHIFY_OUT": str(out_dir)}
    # --force: 削除や改名でノードが減っても作り直した方を採る（古いノードを残さない）。
    rc, out = run([exe, "update", ".", "--force"], repo, GRAPHIFY_UPDATE_TIMEOUT, env)
    if rc != 0 or not graph.is_file():
        print(f"  graphify update に失敗しました（{rc}）: {out.strip()[:200]}", file=sys.stderr)
        return None, None, "update-failed"
    stamp_file.write_text(now, encoding="utf-8")
    return exe, graph, "updated"


def search(ctx: Ctx, repo: Path, terms: list[str], graph_cmd: str) -> tuple[str, list[str], str]:
    """語ごとに graphify と git grep で引き、（本文, 候補のファイル, graphify の状態）を返す。"""
    exe, graph, note = ensure_graph(ctx, repo)
    lines: list[str] = []
    files: list[str] = []

    def add_file(path: str) -> None:
        if path and path not in files and (repo / path).is_file():
            files.append(path)

    if exe and graph:
        lines += [f"### graphify {graph_cmd}", ""]
        for term in terms:
            argv = [exe, graph_cmd, term, "--graph", str(graph)]
            if graph_cmd == "query":
                argv += ["--budget", str(GRAPHIFY_BUDGET)]
            _, out = run(argv, repo, GRAPHIFY_TIMEOUT)
            out = "\n".join(ln for ln in out.splitlines() if not ln.startswith("[graphify] note"))
            lines += [f"#### {term}", "", "```", out.strip() or "(該当なし)", "```", ""]
            for m in _GRAPHIFY_SRC.finditer(out):
                add_file(m.group(1) or m.group(2))

    lines += ["### 文字列の一致（git grep）", ""]
    for term in terms:
        # --untracked: まだコミットしていない新しいファイルも拾う（.gitignore に載っているものは除く）。
        # 識別子は語単位（-w）で引く。`hello` で `helloWorld` を拾って影響範囲を水増ししない。
        word = ["-w"] if _WORDLIKE.match(term) else []
        rc, out = run(["git", "grep", "--untracked", "-n", "-I", "-i", "-F", *word, "--max-count", "3", "-e", term,
                       "--", ".", f":(exclude){DATA_DIRNAME}", f":(exclude){MACHINE_REL}"], repo, GIT_TIMEOUT)
        hits = out.splitlines()[:GREP_LINES_PER_TERM] if rc == 0 else []
        lines += [f"#### {term}", "", *([f"- {h[:200]}" for h in hits] or ["- (該当なし)"]), ""]
        for h in hits:
            add_file(h.split(":", 1)[0])
    return "\n".join(lines), files, note


def terms_of(args: argparse.Namespace) -> list[str]:
    terms = unique(args.term or [])
    if not terms:
        raise CoddError("検索語を --term で渡してください")
    return terms[:MAX_TERMS]


def write_report(ctx: Ctx, name: str, title: str, terms: list[str], parts: list[tuple[str, str, str]],
                 files: list[str]) -> Path:
    """parts は（見出し, graphify の状態, 本文）の列。"""
    ctx.data.mkdir(parents=True, exist_ok=True)
    path = ctx.data / name
    lines = [f"# {title}", "", f"- 検索語: {', '.join(terms)}", ""]
    for heading, note, body in parts:
        lines += [f"## {heading}", "", f"- graphify: {note}", "", body]
    lines += ["## 候補のファイル", "", *([f"- {p}" for p in files] or ["- (見つからない)"]), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def cmd_explore(ctx: Ctx, args: argparse.Namespace) -> int:
    terms = terms_of(args)
    targets = [ctx.ref(n) for n in args.ref] if args.ref else ctx.refs
    many = len(ctx.refs) > 1
    parts, files, notes = [], [], []
    for r in targets:
        body, found, note = search(ctx, r.path, terms, "query")
        parts.append((f"{r.name}（{r.label}）  {r.path}", note, body))
        files += [f"{r.name}:{p}" if many else p for p in found]
        notes.append(f"{r.name}={note}" if many else note)
    path = write_report(ctx, "explore.md", "参照先で関係する箇所", terms, parts, files)
    # verify-apply が「どれを変えたか」を測るための印。計画を練る間は何も変えないので、探すたびに取り直してよい。
    (ctx.data / "before.json").write_text(json.dumps({
        "own": stamp(ctx.root), "refs": {r.name: stamp(r.path) for r in ctx.refs},
        "own_head": head(ctx.root), "own_files": dirty_files(ctx.root),
    }, indent=2) + "\n", encoding="utf-8")
    print(f"FOUND {len(files)} files (graphify: {', '.join(notes)})")
    print(f"  詳細: {path.relative_to(ctx.root)}")
    return 0


def cmd_impact(ctx: Ctx, args: argparse.Namespace) -> int:
    terms = terms_of(args)
    body, files, note = search(ctx, ctx.root, terms, "affected")
    path = write_report(ctx, "impact.md", f"自分のリポジトリ（{SIDES[ctx.side]}）で影響を受ける箇所",
                        terms, [(str(ctx.root), note, body)], files)
    print(f"FOUND {len(files)} files (graphify: {note})")
    print(f"  詳細: {path.relative_to(ctx.root)}")
    return 0


# ---------------------------------------------------------------- 根拠のパス

def cited_refs(ctx: Ctx, line: str, allow_new: bool = False) -> tuple[set[tuple[str, str]], list[str]]:
    """行の中の参照先のパスを（参照先の名前, パス）で返す。2 つ目は曖昧だったパス。

    `名前:パス` はその参照先で、パスだけなら実在する参照先で探す（複数で見つかれば曖昧）。
    allow_new は、まだ無いファイル（親のフォルダはある）も認める（参照先の変更案で新しく書くとき）。
    """
    def exists(repo: Path, rel: str) -> bool:
        target = repo / rel
        return target.exists() or (allow_new and "/" in rel and target.parent.is_dir())

    found: set[tuple[str, str]] = set()
    ambiguous: list[str] = []
    names = {r.name for r in ctx.refs}
    for m in _CITE.finditer(line):
        rel = m.group("path")
        rel = rel[2:] if rel.startswith("./") else rel
        if m.group("ref") in names:
            r = ctx.ref(m.group("ref"))
            if exists(r.path, rel):
                found.add((r.name, rel))
            continue
        hits = [r for r in ctx.refs if exists(r.path, rel)]
        if len(hits) == 1:
            found.add((hits[0].name, rel))
        elif len(hits) > 1:
            ambiguous.append(rel)
    return found, ambiguous


def cited_own(line: str, root: Path) -> list[str]:
    paths = []
    for m in _CITE.finditer(line):
        rel = m.group("path")
        rel = rel[2:] if rel.startswith("./") else rel
        if (root / rel).exists():
            paths.append(rel)
    return paths


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
    body, files, note = search(ctx, ctx.root, terms, "affected")
    files = files[:MAX_MEASURED]
    write_report(ctx, name, title, terms, [(str(ctx.root), note, body)], files)
    return files


def listed_paths(body: str, root: Path, only_no_change: bool = False) -> set[str]:
    paths: set[str] = set()
    for item in items(body) or [body]:
        if only_no_change and NO_CHANGE_MARK not in item:
            continue
        paths.update(cited_own(item, root))
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


def planned_refs(ctx: Ctx, bodies: dict[str, str]) -> tuple[set[str], list[str]]:
    """参照先の変更案が変える参照先の名前と、その検査で見つかった問題。"""
    names: set[str] = set()
    problems: list[str] = []
    if is_none(bodies.get("## 参照先の変更案", "なし")):
        return names, problems
    listed = items(bodies["## 参照先の変更案"])
    if not listed:
        problems.append("## 参照先の変更案 は箇条書きにしてください（無ければ「なし」）")
    for item in listed:
        found, ambiguous = cited_refs(ctx, item, allow_new=True)
        if ambiguous and not found:
            problems.append(f"## 参照先の変更案 のパスがどの参照先か決まりません。`名前:パス` で書いてください: {item[:80]}")
        elif not found:
            problems.append(f"## 参照先の変更案 の項目に、参照先のパスがありません: {item[:80]}")
        names |= {n for n, _ in found}
    return names, problems


def verify_plan_text(ctx: Ctx, text: str) -> list[str]:
    problems, bodies = sections(text, PLAN_HEADINGS)
    if problems:
        return problems
    for heading in CITED_IN_REFS:
        if is_none(bodies[heading]):
            continue
        listed = items(bodies[heading])
        if not listed:
            problems.append(f"{heading} は箇条書きにしてください（無ければ「なし」）")
        for item in listed:
            found, ambiguous = cited_refs(ctx, item)
            if not found:
                hint = "（どの参照先か `名前:パス` で書いてください）" if ambiguous else ""
                problems.append(f"{heading} の項目に、参照先に実在する根拠のパスがありません{hint}: {item[:80]}")
    drift = not is_none(bodies["## ずれ"])
    ref_change = not is_none(bodies["## 参照先の変更案"])
    impact = not is_none(bodies["## 影響範囲"])
    if drift and not ref_change:
        problems.append("ずれがあるのに、参照先の変更案が「なし」です（ずれを残すなら、ずれではなくその他に書く）")
    if not drift and ref_change:
        problems.append("ずれが「なし」なのに、参照先の変更案があります")
    problems += planned_refs(ctx, bodies)[1]
    if ref_change and not impact:
        problems.append("参照先の変更案があるのに、影響範囲が「なし」です")
    if impact:
        for item in items(bodies["## 影響範囲"]) or [bodies["## 影響範囲"]]:
            if not cited_own(item, ctx.root):
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
    problems = verify_plan_text(ctx, text)
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
    before_refs = before.get("refs", {})
    own_changed = stamp(ctx.root) != before["own"]
    changed = [r for r in ctx.refs if stamp(r.path) != before_refs.get(r.name)]
    want_own = not is_none(bodies.get("## 自分の変更案", "なし"))
    want, plan_problems = planned_refs(ctx, bodies)

    problems = list(plan_problems)
    if want_own and not own_changed:
        problems.append("自分の変更案があるのに、自分のリポジトリが変わっていません")
    for r in ctx.refs:
        if r.name in want and r not in changed:
            problems.append(f"参照先の変更案で {r.name} を変えるはずなのに、{r.name} が変わっていません")
        if r.name not in want and r in changed:
            problems.append(f"参照先の変更案に {r.name} は無いのに、{r.name} が変わっています（戻してください）")
    measured: list[str] = []
    if changed:
        # 参照先を実際に変えたあとの影響範囲を測り直す。測ったファイルは、直したか「変更不要」と書いたかのどちらか。
        terms = unique([t for r in changed for t in terms_from_diff(r.path)] + terms_from_plan(bodies))
        measured = measure(ctx, terms, "impact-after.md",
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
    for r in changed:
        # 参照先の検査は、参照先に置いた同じマシンの設定（codd.json の check）を使う。
        problems += run_check(r.path, r.check, f"{r.name}（{r.label}）")
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    refs_note = ",".join(r.name for r in changed) or "none"
    print(f"OK own={'changed' if own_changed else 'same'} refs={refs_note}"
          + (f" impact={len(measured)} files（{DATA_DIRNAME}/impact-after.md）" if changed else ""))
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codd.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="この側・参照先と、使うスキルを示す")
    s.add_argument("--phase", choices=list(PHASES), help="plan（計画を練るとき）か apply（変えるとき）")
    e = sub.add_parser("explore", help="参照先を探す")
    e.add_argument("--term", action="append", help="検索語（繰り返し可）")
    e.add_argument("--ref", action="append", help="探す参照先の名前（繰り返し可。既定はすべて）")
    i = sub.add_parser("impact", help="自分のリポジトリで影響を受ける箇所を探す")
    i.add_argument("--term", action="append", help="検索語（繰り返し可）")
    sub.add_parser("verify-plan", help="計画が決まった形かを検査する")
    sub.add_parser("verify-apply", help="計画どおりに変えたかを検査する")
    return p


COMMANDS = {"show": cmd_show, "explore": cmd_explore, "impact": cmd_impact,
            "verify-plan": cmd_verify_plan, "verify-apply": cmd_verify_apply}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](Ctx(repo_root(Path.cwd())), args)
    except CoddError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
