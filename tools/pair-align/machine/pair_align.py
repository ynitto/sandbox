#!/usr/bin/env python3
"""pair_align — 実装と設計書の 2 リポジトリを交互に揃えるステートマシンの決定的な下請け。

ステートマシン（同じフォルダの workflow.yaml）のうち、機械で決まる仕事だけをここに置く。
判断（相手を読んで前提・制約・自由に分ける、意図と合うか、どう直すか、依頼文をどう書くか）は
アクションの側でモデルが行い、このスクリプトは材料を集め、書かれたものを検査し、控えを残す。

    begin          今回の実行が何から始まるかを決める（届いた依頼 / 波及 / 新しい意図 / 確認の答え / 伝えるだけ）
    verify-reading 相手を読んだ結果（前提・制約・自由）が決まった形か、根拠のパスが実在するかを検査する
    verify-question 利用者への確認が決まった形かを検査する
    pause          利用者の答えを待つために、意図と読んだ結果を控えて止まる
    self-check     自分への変更を検査する（pair.json の check を実行する）
    commit         自分への変更をコミットし、意図を片付ける（届いた依頼・波及なら合図の行を付ける）
    collect        受け取った依頼の残りを確かめ、無ければ自分の変更を集める
    locate         相手のリポジトリで関係しそうな箇所を探す（graphify があれば使う）
    verify-prompt  書かれた依頼文が決まった形を満たすかを検査する（check 用）
    record         依頼文を送り箱へ移し、基準点を進める（--skip で依頼なしに進める）
    ack ID         相手からの依頼を「反映不要」として受け取り済みにする
    status         設定・基準点・未処理の依頼・進行中の意図を表示する

意図（session）: 「こうしたい」という 1 件の変更。利用者が入れたもの・相手から届いた依頼・
相手を直してもらったあとの波及、のどれか。進行中の 1 件を state.json の active に、
相手の反映待ちを waiting に持つ。本文と読んだ結果は `.pair-align/session/<ID>/` に置く。

置き場所は `<リポジトリ>/.statemachine/pair_align/`。設定は同じフォルダの pair.json、
作業ファイル・送り箱・状態は `<リポジトリ>/.pair-align/` に置く。依存は python3 と git のみ。

往復の止め方: 相手の依頼を反映したコミットには `Pair-Align: <ID>` の行を付ける。
collect はその行を持つコミットを「自分の変更」から外すので、反映がまた依頼になって戻らない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MACHINE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = MACHINE_DIR / "pair.json"
TEMPLATE_FILE = MACHINE_DIR / "templates" / "prompt.md"

DATA_DIRNAME = ".pair-align"
TRAILER = "Pair-Align"
ID_HEADER = "Pair-Align-Id"

SIDES = {"impl": "実装", "design": "設計書"}

# 依頼文に必ず要る見出し（templates/prompt.md と同じ順）。
REQUIRED_HEADINGS = (
    "## 変更元",
    "## 変更の要約",
    "## 反映してほしいこと",
    "## 対象の候補",
    "## 完了の合図",
)

MAX_DIFF_CHARS = 60000
MAX_TERMS = 12
GREP_LINES_PER_TERM = 20
GIT_TIMEOUT = 60
GRAPHIFY_TIMEOUT = 120
GRAPHIFY_UPDATE_TIMEOUT = 600
GRAPHIFY_BUDGET = 600

_TRAILER_RE = re.compile(rf"^{TRAILER}:\s*(\S+)\s*$", re.MULTILINE)
_ID_RE = re.compile(rf"^{ID_HEADER}:\s*(\S+)\s*$", re.MULTILINE)
# graphify query の出力の `NODE 名前 [src=docs/api.md loc=L3 …]` からファイルを拾う。
_GRAPHIFY_SRC = re.compile(r"\bsrc=([^\s\]]+)")


class PairAlignError(Exception):
    """利用者が直せる設定・状態の誤り（終了コード 2）。"""


# ---------------------------------------------------------------- git

def git(args: list[str], cwd: Path, check: bool = True) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise PairAlignError(f"git {' '.join(args)} が {GIT_TIMEOUT} 秒で終わりませんでした") from exc
    if check and proc.returncode != 0:
        raise PairAlignError(f"git {' '.join(args)} に失敗しました: {proc.stderr.strip()}")
    return proc.stdout


def repo_root(start: Path) -> Path:
    out = git(["rev-parse", "--show-toplevel"], start, check=False).strip()
    if not out:
        raise PairAlignError(f"git リポジトリではありません: {start}")
    return Path(out)


def has_commit(cwd: Path, rev: str) -> bool:
    return bool(git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd, check=False).strip())


def exclude_pathspecs() -> list[str]:
    """自分の変更から外すパス（このマシン自身と作業フォルダ）。"""
    return [".", f":(exclude){DATA_DIRNAME}", ":(exclude).statemachine/pair_align"]


# ---------------------------------------------------------------- 設定と状態

class Ctx:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = load_config()
        self.side = self.config["side"]
        self.pair_side = "design" if self.side == "impl" else "impl"
        pair = Path(os.path.expanduser(self.config["pair_path"]))
        self.pair = (pair if pair.is_absolute() else (root / pair)).resolve()
        self.data = root / DATA_DIRNAME
        self.work = self.data / "work"
        self.outbox = self.data / "outbox"
        self.state_file = self.data / "state.json"
        self.sessions = self.data / "session"

    def require_pair(self) -> None:
        if not self.pair.is_dir():
            raise PairAlignError(
                f"相手のリポジトリが見つかりません: {self.pair}\n"
                f"  {CONFIG_FILE} の pair_path を直してください"
            )
        if not git(["rev-parse", "--show-toplevel"], self.pair, check=False).strip():
            raise PairAlignError(f"相手のフォルダが git リポジトリではありません: {self.pair}")

    def load_state(self) -> dict:
        state = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.is_file() else {}
        state.setdefault("baseline", None)
        state.setdefault("acked", [])
        state.setdefault("sent", [])
        state.setdefault("active", None)
        state.setdefault("waiting", [])
        return state

    def save_state(self, state: dict) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def load_current(self) -> dict:
        path = self.work / "current.json"
        if not path.is_file():
            raise PairAlignError("先に begin か collect を実行してください（.pair-align/work/current.json がありません）")
        return json.loads(path.read_text(encoding="utf-8"))


def load_config() -> dict:
    if not CONFIG_FILE.is_file():
        raise PairAlignError(
            f"設定がありません: {CONFIG_FILE}\n"
            '  例: {"side": "impl", "pair_path": "../my-design"}'
        )
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if config.get("side") not in SIDES:
        raise PairAlignError(f"pair.json の side は impl か design です（今: {config.get('side')!r}）")
    if not config.get("pair_path"):
        raise PairAlignError("pair.json の pair_path が空です（相手のリポジトリのパスを書いてください）")
    config.setdefault("graphify", "auto")
    if config["graphify"] not in ("auto", "off"):
        raise PairAlignError(f"pair.json の graphify は auto か off です（今: {config['graphify']!r}）")
    check = config.get("check")
    if check is not None and not (isinstance(check, list) and check and all(isinstance(a, str) for a in check)):
        raise PairAlignError('pair.json の check はコマンドの配列です（例: ["python3", "-m", "pytest", "-q"]）')
    return config


# ---------------------------------------------------------------- 受け取った依頼

def acknowledged_ids(root: Path, state: dict) -> set[str]:
    """自分の履歴にある `Pair-Align: <ID>` と、ack で受け取り済みにした ID。"""
    ids = set(state.get("acked", []))
    if has_commit(root, "HEAD"):
        log = git(["log", "--all", f"--grep=^{TRAILER}:", "--format=%B%x00"], root, check=False)
        ids.update(_TRAILER_RE.findall(log))
    return ids


def outbox_prompts(repo: Path) -> list[tuple[str, Path]]:
    """送り箱の依頼（ID, パス）を古い順に返す。"""
    box = repo / DATA_DIRNAME / "outbox"
    if not box.is_dir():
        return []
    found = []
    for path in sorted(box.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name)):
        m = _ID_RE.search(path.read_text(encoding="utf-8", errors="replace"))
        if m:
            found.append((m.group(1), path))
    return found


def pending_inbound(ctx: Ctx, state: dict) -> list[tuple[str, Path]]:
    done = acknowledged_ids(ctx.root, state)
    return [(pid, path) for pid, path in outbox_prompts(ctx.pair) if pid not in done]


# ---------------------------------------------------------------- 自分の変更

def own_commits(root: Path, base: str | None) -> tuple[list[dict], list[dict]]:
    """base..HEAD のコミットを（伝えるもの, 反映として外すもの）に分ける。"""
    if not has_commit(root, "HEAD"):
        return [], []
    rng = f"{base}..HEAD" if base else "HEAD"
    raw = git(["log", "--reverse", "--format=%H%x1f%s%x1f%B%x1e", rng], root)
    keep, skipped = [], []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, subject, body = (rec.split("\x1f") + ["", ""])[:3]
        files = git(["show", "--format=", "--name-status", sha, "--", *exclude_pathspecs()], root).strip()
        entry = {"sha": sha, "subject": subject, "files": files.splitlines() if files else []}
        ids = _TRAILER_RE.findall(body)
        if ids:
            entry["pair_ids"] = ids
            skipped.append(entry)
        elif entry["files"]:
            keep.append(entry)
    return keep, skipped


def default_base(root: Path, state: dict, since: str | None) -> str | None:
    if since:
        if not has_commit(root, since):
            raise PairAlignError(f"--since のリビジョンが見つかりません: {since}")
        return since
    if state.get("baseline") and has_commit(root, state["baseline"]):
        return state["baseline"]
    # 初回は直前の 1 コミットだけを見る（履歴全体を相手へ流さない）。
    if has_commit(root, "HEAD~1"):
        return git(["rev-parse", "HEAD~1"], root).strip()
    return None


_TERM_PATTERNS = (
    re.compile(r"^[+-]\s*(?:async\s+)?def\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^[+-]\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^[+-]\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]{2,})"),
    re.compile(r"^[+-]\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]{2,})\s*=\s*(?:async\s*)?\("),
    re.compile(r"^[+-]\s*(?:pub\s+)?(?:fn|func|interface|type|struct|enum)\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^[+-]\s*#{1,6}\s+(.{2,60}?)\s*#*\s*$"),
)
_BACKTICK = re.compile(r"`([^`\s][^`]{1,38}[^`\s])`")


def extract_terms(diff: str, files: list[str]) -> list[str]:
    """差分から相手側の検索語を拾う（関数・クラス名、見出し、`用語`、ファイル名）。"""
    terms: list[str] = []

    def add(term: str) -> None:
        term = term.strip()
        if 3 <= len(term) <= 60 and term not in terms:
            terms.append(term)

    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        for pat in _TERM_PATTERNS:
            m = pat.match(line)
            if m:
                add(m.group(1))
        if line.startswith(("+", "-")):
            for m in _BACKTICK.finditer(line):
                add(m.group(1))
    for path in files:
        stem = Path(path).stem
        if stem.lower() not in ("readme", "index", "__init__", "main", "changelog"):
            add(stem)
    return terms[:MAX_TERMS]


def cmd_collect(ctx: Ctx, args: argparse.Namespace) -> int:
    ctx.require_pair()
    state = ctx.load_state()
    ctx.work.mkdir(parents=True, exist_ok=True)
    for stale in ("changes.md", "current.json", "candidates.md", "prompt.md", "inbound.md"):
        (ctx.work / stale).unlink(missing_ok=True)

    # 1. 相手からの依頼が残っていれば、先にそれを片付ける（交互に進める）。
    pending = pending_inbound(ctx, state)
    if pending and not args.ignore_inbound:
        parts = [
            f"# {SIDES[ctx.pair_side]}からの未処理の依頼（{len(pending)} 件）\n",
            f"相手のリポジトリ: {ctx.pair}\n",
            "反映したらコミットメッセージの末尾に各依頼の `Pair-Align: <ID>` 行を付けてください。",
            "反映が不要なら `python3 .statemachine/pair_align/pair_align.py ack <ID>` で受け取り済みにします。\n",
        ]
        for pid, path in pending:
            parts.append(f"\n---\n\n<!-- {pid}: {path} -->\n")
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        (ctx.work / "inbound.md").write_text("\n".join(parts), encoding="utf-8")
        print(f"INBOUND_PENDING {len(pending)}")
        for pid, path in pending:
            print(f"  {pid}  {path}")
        print("  詳細: .pair-align/work/inbound.md")
        return 0

    # 2. 自分の変更を集める。
    base = default_base(ctx.root, state, args.since)
    keep, skipped = own_commits(ctx.root, base)
    head = git(["rev-parse", "HEAD"], ctx.root).strip() if has_commit(ctx.root, "HEAD") else None
    dirty = git(["status", "--porcelain", "--", *exclude_pathspecs()], ctx.root).strip()

    if not keep:
        if head and not args.since:
            state["baseline"] = head
            ctx.save_state(state)
        print("NO_CHANGES")
        if skipped:
            print(f"  反映として外したコミット: {len(skipped)} 件")
        if dirty:
            print("  コミットしていない変更があります（依頼に含めるにはコミットしてください）")
        return 0

    files: list[str] = []
    for c in keep:
        for line in c["files"]:
            path = line.split("\t")[-1]
            if path not in files:
                files.append(path)

    diff_parts = []
    for c in keep:
        diff_parts.append(git(["show", "--format=commit %H%n%n    %s%n", "--patch", c["sha"], "--",
                               *exclude_pathspecs()], ctx.root))
    diff = "\n".join(diff_parts)
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS] + "\n… (以降は省略。全体は git show で確認してください)\n"

    terms = extract_terms(diff, files)
    pid = f"{ctx.side}-{head[:10]}"
    current = {
        "id": pid, "side": ctx.side, "pair_side": ctx.pair_side, "pair_path": str(ctx.pair),
        "base": base, "head": head, "commits": [c["sha"] for c in keep], "files": files, "terms": terms,
    }
    (ctx.work / "current.json").write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# {SIDES[ctx.side]}側の変更（{SIDES[ctx.pair_side]}へ伝える候補）",
        "",
        f"- 依頼 ID: {pid}",
        f"- 範囲: {base[:10] if base else '(最初のコミット)'}..{head[:10]}",
        f"- 相手のリポジトリ: {ctx.pair}",
        f"- 検索語の候補: {', '.join(terms) if terms else '(なし)'}",
        "",
        "## コミット",
        "",
        *[f"- {c['sha'][:10]} {c['subject']}" for c in keep],
        "",
        "## 変更したファイル",
        "",
        *[f"- {p}" for p in files],
    ]
    if skipped:
        lines += ["", "## 相手の依頼の反映として外したコミット", "",
                  *[f"- {c['sha'][:10]} {c['subject']}（{', '.join(c['pair_ids'])}）" for c in skipped]]
    if dirty:
        lines += ["", "## コミットしていない変更（今回の依頼には含めない）", "", "```", dirty, "```"]
    lines += ["", "## 差分", "", "```diff", diff.rstrip("\n"), "```", ""]
    (ctx.work / "changes.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"CHANGES {len(keep)} commits, {len(files)} files")
    print(f"  依頼 ID: {pid}")
    print("  詳細: .pair-align/work/changes.md")
    if truncated:
        print(f"  差分が長いので先頭 {MAX_DIFF_CHARS} 文字だけを載せました")
    if dirty:
        print("  コミットしていない変更は含めていません")
    return 0


# ---------------------------------------------------------------- 相手側の検索

def graphify_status(ctx: Ctx) -> tuple[str | None, Path]:
    graph = ctx.pair / "graphify-out" / "graph.json"
    if ctx.config["graphify"] == "off":
        return None, graph
    return shutil.which("graphify"), graph


def run_tool(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"({timeout} 秒で終わらなかったので打ち切りました)"
    except OSError as exc:
        return 127, f"(実行できませんでした: {exc})"
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def cmd_locate(ctx: Ctx, args: argparse.Namespace) -> int:
    ctx.require_pair()
    current = ctx.load_current()
    terms: list[str] = []
    for t in [*(args.term or []), *current.get("terms", [])]:
        t = t.strip()
        if t and t not in terms:
            terms.append(t)
    terms = terms[:MAX_TERMS]
    if not terms:
        raise PairAlignError("検索語がありません（--term で渡してください）")

    exe, graph = graphify_status(ctx)
    graph_note = "off"
    if exe:
        if args.refresh:
            rc, out = run_tool([exe, "update", str(ctx.pair)], ctx.pair, GRAPHIFY_UPDATE_TIMEOUT)
            if rc != 0:
                print(f"  graphify update に失敗しました（{rc}）: {out[:200]}", file=sys.stderr)
        graph_note = "used" if graph.is_file() else "no-graph"
    elif ctx.config["graphify"] == "auto":
        graph_note = "not-installed"

    sections = [f"# {SIDES[ctx.pair_side]}側で関係しそうな箇所", "",
                f"- 相手のリポジトリ: {ctx.pair}", f"- 検索語: {', '.join(terms)}",
                f"- graphify: {graph_note}", ""]
    hit_files: list[str] = []

    if graph_note == "used":
        sections += ["## graphify（知識グラフの探索）", ""]
        for term in terms:
            rc, out = run_tool([exe, "query", term, "--graph", str(graph), "--budget", str(GRAPHIFY_BUDGET)],
                               ctx.pair, GRAPHIFY_TIMEOUT)
            sections += [f"### {term}", "", "```", out or "(該当なし)", "```", ""]
            for m in _GRAPHIFY_SRC.finditer(out or ""):
                cand = m.group(1)
                if (ctx.pair / cand).is_file() and cand not in hit_files:
                    hit_files.append(cand)

    sections += ["## 文字列の一致（git grep）", ""]
    for term in terms:
        rc, out = run_tool(["git", "grep", "-n", "-I", "-i", "-F", "--max-count", "3", "-e", term, "--",
                            *exclude_pathspecs(), ":(exclude)graphify-out"],
                           ctx.pair, GIT_TIMEOUT)
        lines = out.splitlines()[:GREP_LINES_PER_TERM] if rc == 0 else []
        sections += [f"### {term}", ""]
        sections += [f"- {ln[:200]}" for ln in lines] or ["- (該当なし)"]
        sections.append("")
        for ln in lines:
            cand = ln.split(":", 1)[0]
            if cand and cand not in hit_files:
                hit_files.append(cand)

    sections += ["## 候補のファイル", "", *([f"- {p}" for p in hit_files] or ["- (見つからない。新しく書く箇所として扱う)"]), ""]
    ctx.work.mkdir(parents=True, exist_ok=True)
    (ctx.work / "candidates.md").write_text("\n".join(sections), encoding="utf-8")
    print(f"CANDIDATES {len(hit_files)} files (graphify: {graph_note})")
    print("  詳細: .pair-align/work/candidates.md")
    if graph_note == "no-graph":
        print(f"  相手側にグラフがありません。{ctx.pair} で graphify を一度実行すると探索が速くなります")
    return 0


# ---------------------------------------------------------------- 依頼文の検査と控え

def sections(text: str, headings: tuple[str, ...]) -> tuple[list[str], dict[str, str]]:
    """決まった見出しが順にそろい、中身（コメントを除く）が空でないかを見る。

    戻り値は（問題の一覧, 見出し → 中身）。中身は次の `## ` 見出しまで。
    """
    problems = []
    positions = []
    for heading in headings:
        m = re.search(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE)
        if not m:
            problems.append(f"見出しがありません: {heading}")
            continue
        positions.append((m.start(), heading, m.end()))
    positions.sort()
    bodies = {}
    for i, (_, heading, end) in enumerate(positions):
        stop = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        nxt = re.search(r"^## ", text[end:stop], re.MULTILINE)
        body = re.sub(r"<!--.*?-->", "", text[end:end + nxt.start()] if nxt else text[end:stop],
                      flags=re.DOTALL).strip()
        bodies[heading] = body
        if not body:
            problems.append(f"見出しの中身が空です: {heading}")
    if [h for _, h, _ in positions] != [h for h in headings if h in bodies]:
        problems.append("見出しの順番がテンプレートと違います")
    return problems, bodies


def verify_prompt_text(text: str, pid: str) -> list[str]:
    problems, _ = sections(text, REQUIRED_HEADINGS)
    ids = _ID_RE.findall(text)
    if ids != [pid]:
        problems.append(f"{ID_HEADER}: {pid} の行がちょうど 1 つ必要です（今: {ids or 'なし'}）")
    if f"{TRAILER}: {pid}" not in text:
        problems.append(f"完了の合図に `{TRAILER}: {pid}` の行がありません")
    if "{{" in text or "}}" in text:
        problems.append("テンプレートの {{…}} が残っています")
    if "<!-- TODO" in text:
        problems.append("テンプレートの TODO が残っています")
    return problems


def cmd_verify_prompt(ctx: Ctx, args: argparse.Namespace) -> int:
    current = ctx.load_current()
    path = ctx.work / "prompt.md"
    if not path.is_file():
        print("依頼文がありません: .pair-align/work/prompt.md", file=sys.stderr)
        return 1
    problems = verify_prompt_text(path.read_text(encoding="utf-8"), current["id"])
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return 1
    print(f"OK {current['id']}")
    return 0


def cmd_record(ctx: Ctx, args: argparse.Namespace) -> int:
    current = ctx.load_current()
    state = ctx.load_state()
    if args.skip:
        state["baseline"] = current["head"]
        ctx.save_state(state)
        print(f"SKIPPED {current['id']}（{SIDES[ctx.pair_side]}への反映は不要。基準点を進めました）")
        return 0
    prompt = ctx.work / "prompt.md"
    if not prompt.is_file():
        raise PairAlignError("依頼文がありません: .pair-align/work/prompt.md")
    problems = verify_prompt_text(prompt.read_text(encoding="utf-8"), current["id"])
    if problems:
        raise PairAlignError("依頼文が決まった形を満たしていません:\n  " + "\n  ".join(problems))
    ctx.outbox.mkdir(parents=True, exist_ok=True)
    dest = ctx.outbox / f"{current['id']}.md"
    shutil.copyfile(prompt, dest)
    state["baseline"] = current["head"]
    state["sent"] = [s for s in state["sent"] if s.get("id") != current["id"]]
    state["sent"].append({"id": current["id"], "base": current["base"], "head": current["head"],
                          "at": now_iso()})
    active = state.get("active")
    if active and active.get("phase") == "decided_pair" and current.get("session") == active["id"]:
        # 相手を直してもらう依頼。反映されたら波及としてこちらを直すので、意図を待ちに回す。
        active.update(phase="waiting", outbound_id=current["id"])
        state["waiting"].append(active)
        state["active"] = None
        ctx.save_state(state)
        print(f"RECORDED_REQUEST {dest.relative_to(ctx.root)}")
        print(f"  {SIDES[ctx.pair_side]}の側で反映されたら、もう一度実行するとこちらへの波及を直します")
        return 0
    ctx.save_state(state)
    print(f"RECORDED {dest.relative_to(ctx.root)}")
    return 0


def cmd_ack(ctx: Ctx, args: argparse.Namespace) -> int:
    ctx.require_pair()
    known = {pid for pid, _ in outbox_prompts(ctx.pair)}
    if args.id not in known and not args.force:
        raise PairAlignError(f"相手の送り箱にこの ID はありません: {args.id}（--force で登録します）")
    state = ctx.load_state()
    if args.id not in state["acked"]:
        state["acked"].append(args.id)
    ctx.save_state(state)
    print(f"ACKED {args.id}")
    return 0


def cmd_status(ctx: Ctx, args: argparse.Namespace) -> int:
    state = ctx.load_state()
    exe, graph = graphify_status(ctx)
    print(f"この側: {SIDES[ctx.side]}（{ctx.side}）  リポジトリ: {ctx.root}")
    print(f"相手:   {SIDES[ctx.pair_side]}（{ctx.pair_side}）  リポジトリ: {ctx.pair}"
          + ("" if ctx.pair.is_dir() else "  ← 見つかりません"))
    print(f"基準点: {state['baseline'] or '(未設定。初回は直前の 1 コミットを見ます)'}")
    print(f"graphify: {'使う' if exe and graph.is_file() else ('グラフ未作成' if exe else '使わない')}")
    if not ctx.pair.is_dir():
        return 0
    pending = pending_inbound(ctx, state)
    print(f"受け取って未処理の依頼: {len(pending)} 件")
    for pid, path in pending:
        print(f"  {pid}  {path}")
    pair_done = pair_acknowledged(ctx)
    sent = outbox_prompts(ctx.root)
    waiting = [(pid, p) for pid, p in sent if pid not in pair_done]
    print(f"送って相手が未処理の依頼: {len(waiting)} 件")
    for pid, path in waiting:
        print(f"  {pid}  {path}")
    active = state.get("active")
    if active:
        print(f"進行中の意図: {active['id']}（{ORIGIN_LABELS[active['origin']]}・{PHASE_LABELS[active['phase']]}）")
    for w in state["waiting"]:
        print(f"相手の反映待ちの意図: {w['id']}（依頼 {w['outbound_id']}）")
    return 0


# ---------------------------------------------------------------- 意図（読む → 分ける → 合うか → 直す）

ORIGIN_LABELS = {"user": "利用者の意図", "inbound": "相手から届いた依頼", "ripple": "相手を直したあとの波及"}
PHASE_LABELS = {"working": "作業中", "confirm": "利用者の答え待ち", "decided_pair": "相手への依頼を作成中",
                "waiting": "相手の反映待ち"}
DECISIONS = {"pair": "pair", "相手を直す": "pair", "revise": "revise", "意図を直す": "revise",
             "abort": "abort", "やめる": "abort"}

READING_HEADINGS = ("## 前提", "## 制約", "## 自由")
QUESTION_HEADINGS = ("## 意図", "## ぶつかっている点", "## 相手を直す場合に頼むこと", "## 波及してこちらで直すこと")
_PATHISH = re.compile(r"[\w@.\-]+(?:/[\w@.\-]+)+|[\w@\-]+\.[A-Za-z0-9]{1,8}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pair_acknowledged(ctx: Ctx) -> set[str]:
    """相手が受け取った（反映をコミットした・ack した）こちらの依頼の ID。"""
    done = acknowledged_ids(ctx.pair, {"acked": []})
    pair_state = ctx.pair / DATA_DIRNAME / "state.json"
    if pair_state.is_file():
        done.update(json.loads(pair_state.read_text(encoding="utf-8")).get("acked", []))
    return done


def session_dir(ctx: Ctx, sid: str) -> Path:
    return ctx.sessions / sid


def new_session(ctx: Ctx, origin: str, intent: str, **extra) -> dict:
    sid = datetime.now().strftime("%Y%m%d%H%M%S")
    while session_dir(ctx, sid).exists():
        sid = str(int(sid) + 1)
    session_dir(ctx, sid).mkdir(parents=True)
    (session_dir(ctx, sid) / "intent.md").write_text(intent, encoding="utf-8")
    return {"id": sid, "origin": origin, "phase": "working", "created": now_iso(), **extra}


def stage_session(ctx: Ctx, session: dict, state: dict, extra_files: tuple[str, ...] = ()) -> None:
    """意図を作業フォルダへ出す（アクションが読む intent.md と、ID を持つ current.json）。"""
    ctx.work.mkdir(parents=True, exist_ok=True)
    for stale in ("changes.md", "current.json", "candidates.md", "prompt.md", "inbound.md",
                  "reading.md", "question.md", "intent.md"):
        (ctx.work / stale).unlink(missing_ok=True)
    src = session_dir(ctx, session["id"])
    shutil.copyfile(src / "intent.md", ctx.work / "intent.md")
    for name in extra_files:
        if (src / name).is_file():
            shutil.copyfile(src / name, ctx.work / name)
    head = git(["rev-parse", "HEAD"], ctx.root).strip() if has_commit(ctx.root, "HEAD") else None
    current = {"id": f"{ctx.side}-{session['id']}", "session": session["id"], "origin": session["origin"],
               "side": ctx.side, "pair_side": ctx.pair_side, "pair_path": str(ctx.pair),
               "base": state.get("baseline"), "head": head, "terms": []}
    (ctx.work / "current.json").write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")


def cmd_begin(ctx: Ctx, args: argparse.Namespace) -> int:
    ctx.require_pair()
    state = ctx.load_state()
    intent = None
    if args.intent_file:
        path = Path(args.intent_file)
        intent = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        intent = intent or None
    decision = None
    if args.decision:
        decision = DECISIONS.get(args.decision.strip())
        if decision is None:
            raise PairAlignError(f"答えは「相手を直す」「意図を直す」「やめる」のどれかです（今: {args.decision!r}）")

    def start(session: dict, word: str, detail: str, extra: tuple[str, ...] = ()) -> int:
        state["active"] = session
        ctx.save_state(state)
        stage_session(ctx, session, state, extra)
        print(f"{word} {session['id']}")
        print(f"  {detail}")
        print("  意図: .pair-align/work/intent.md")
        return 0

    # 1. 利用者の答えを待っている意図があれば、答えで先へ進める。
    active = state.get("active")
    if active and active["phase"] == "confirm":
        if decision is None:
            stage_session(ctx, active, state, ("reading.md", "question.md"))
            print(f"AWAITING_DECISION {active['id']}")
            print("  確認: .pair-align/work/question.md（答えを入れてもう一度実行してください）")
            return 0
        if decision == "abort":
            if active["origin"] == "inbound":
                state["acked"].append(active["inbound_id"])  # 届いた依頼は「反映しない」で閉じる
            state["active"] = None
            ctx.save_state(state)
            print(f"ABORTED {active['id']}")
            return 0
        if decision == "revise":
            if intent is None:
                stage_session(ctx, active, state, ("reading.md", "question.md"))
                print(f"AWAITING_DECISION {active['id']}")
                print("  「意図を直す」には、直した意図も入れてください")
                return 0
            (session_dir(ctx, active["id"]) / "intent.md").write_text(intent, encoding="utf-8")
            active["phase"] = "working"
            return start(active, "REVISED", "直した意図で、相手をもう一度読みます")
        active["phase"] = "decided_pair"
        return start(active, "DECIDED_PAIR", f"{SIDES[ctx.pair_side]}を直す依頼を作ります",
                     ("reading.md", "question.md"))

    # 2. 途中で止まった意図は、その続きから。
    if active:
        if active["phase"] == "decided_pair":
            return start(active, "DECIDED_PAIR", "相手を直す依頼の作成を続けます", ("reading.md", "question.md"))
        return start(active, "RESUME", f"{ORIGIN_LABELS[active['origin']]}の続きを行います")

    # 3. 相手から届いた依頼は、それ自体を意図として扱う（交互に片付ける）。
    pending = pending_inbound(ctx, state)
    if pending:
        pid, path = pending[0]
        session = new_session(ctx, "inbound", path.read_text(encoding="utf-8", errors="replace"), inbound_id=pid)
        note = "（入れた意図は、これを片付けたあとでもう一度実行してください）" if intent else ""
        return start(session, "INBOUND", f"{SIDES[ctx.pair_side]}から届いた依頼 {pid} を反映します{note}")

    # 4. 相手を直してもらった意図は、反映されたら波及としてこちらを直す。
    done = pair_acknowledged(ctx)
    for w in list(state["waiting"]):
        if w["outbound_id"] in done:
            state["waiting"].remove(w)
            w.update(origin="ripple", phase="working")
            return start(w, "RIPPLE", f"{SIDES[ctx.pair_side]}が依頼 {w['outbound_id']} を反映しました。波及を直します")

    # 5. 新しい意図。
    if intent:
        return start(new_session(ctx, "user", intent), "INTENT", f"{SIDES[ctx.pair_side]}を読んでから進めます")

    # 6. 意図が無ければ、コミット済みの変更を相手へ伝えるだけ。
    for stale in ("reading.md", "question.md", "intent.md"):
        (ctx.work / stale).unlink(missing_ok=True)
    if state["waiting"]:
        print("WAITING_PAIR " + " ".join(w["outbound_id"] for w in state["waiting"]))
        print(f"  {SIDES[ctx.pair_side]}の反映を待っています。反映されたらもう一度実行してください")
        return 0
    print("PROPAGATE")
    print("  意図の入力はありません。コミット済みの変更を相手へ伝えるかを見ます")
    return 0


def cited_paths(line: str, repo: Path) -> list[str]:
    found = []
    for m in _PATHISH.finditer(line):
        cand = re.sub(r"(:\d+(-\d+)?|#.*)$", "", m.group(0))
        cand = cand[2:] if cand.startswith("./") else cand
        if cand and (repo / cand).exists():
            found.append(cand)
    return found


def verify_reading_text(text: str, pair: Path) -> list[str]:
    problems, bodies = sections(text, READING_HEADINGS)
    for heading, body in bodies.items():
        items = [ln.strip() for ln in body.splitlines() if ln.strip().startswith(("- ", "* "))]
        if not items:
            problems.append(f"{heading} に箇条書きがありません（無ければ「- なし」）")
        for item in items:
            if item[2:].strip() in ("なし", "無し"):
                continue
            if not cited_paths(item, pair):
                problems.append(f"{heading} の項目に、相手のリポジトリに実在する根拠のパスがありません: {item[:80]}")
    return problems


def cmd_verify_reading(ctx: Ctx, args: argparse.Namespace) -> int:
    path = ctx.work / "reading.md"
    if not path.is_file():
        print("読んだ結果がありません: .pair-align/work/reading.md", file=sys.stderr)
        return 1
    problems = verify_reading_text(path.read_text(encoding="utf-8"), ctx.pair)
    for p in problems:
        print(p, file=sys.stderr)
    if not problems:
        print("OK reading")
    return 1 if problems else 0


def cmd_verify_question(ctx: Ctx, args: argparse.Namespace) -> int:
    path = ctx.work / "question.md"
    if not path.is_file():
        print("確認がありません: .pair-align/work/question.md", file=sys.stderr)
        return 1
    problems, _ = sections(path.read_text(encoding="utf-8"), QUESTION_HEADINGS)
    for p in problems:
        print(p, file=sys.stderr)
    if not problems:
        print("OK question")
    return 1 if problems else 0


def require_active(state: dict) -> dict:
    if not state.get("active"):
        raise PairAlignError("進行中の意図がありません（先に begin を実行してください）")
    return state["active"]


def cmd_pause(ctx: Ctx, args: argparse.Namespace) -> int:
    state = ctx.load_state()
    active = require_active(state)
    for name in ("reading.md", "question.md"):
        if not (ctx.work / name).is_file():
            raise PairAlignError(f".pair-align/work/{name} がありません")
        shutil.copyfile(ctx.work / name, session_dir(ctx, active["id"]) / name)
    active["phase"] = "confirm"
    ctx.save_state(state)
    print(f"PAUSED {active['id']}")
    print("  確認: .pair-align/work/question.md")
    return 0


def own_dirty(ctx: Ctx) -> str:
    return git(["status", "--porcelain", "--", *exclude_pathspecs()], ctx.root).strip()


def cmd_self_check(ctx: Ctx, args: argparse.Namespace) -> int:
    require_active(ctx.load_state())
    check = ctx.config.get("check")
    if not check:
        print("OK（pair.json に check がないので、変更の検査は省きました）")
        return 0
    rc, out = run_tool(check, ctx.root, 900)
    tail = "\n".join(out.splitlines()[-30:])
    if rc != 0:
        print(f"check が失敗しました（{rc}）: {' '.join(check)}\n{tail}", file=sys.stderr)
        return 1
    print(f"OK {' '.join(check)}")
    return 0


def cmd_commit(ctx: Ctx, args: argparse.Namespace) -> int:
    state = ctx.load_state()
    active = require_active(state)
    link = {"inbound": active.get("inbound_id"), "ripple": active.get("outbound_id")}.get(active["origin"])
    kind = "LINKED" if link else "USER"
    if (ctx.work / "reading.md").is_file():
        shutil.copyfile(ctx.work / "reading.md", session_dir(ctx, active["id"]) / "reading.md")
    if not own_dirty(ctx):
        if active["origin"] == "inbound":
            state["acked"].append(link)  # 直すことが無かった依頼も、受け取り済みにする
        state["active"] = None
        ctx.save_state(state)
        print(f"UNCHANGED_{kind} {active['id']}（変更はありませんでした）")
        return 0
    message = args.message.strip() or f"{ORIGIN_LABELS[active['origin']]}を反映"
    if link:
        message += f"\n\n{TRAILER}: {link}"
    # .pair-align/ は .gitignore にあると除外の pathspec にも書けない（git が断る）ので、足してから外す。
    git(["add", "-A", "--", ".", ":(exclude).statemachine/pair_align"], ctx.root)
    if git(["diff", "--cached", "--name-only", "--", DATA_DIRNAME], ctx.root).strip():
        git(["reset", "-q", "--", DATA_DIRNAME], ctx.root)
    git(["commit", "-q", "-m", message], ctx.root)
    sha = git(["rev-parse", "HEAD"], ctx.root).strip()
    state["active"] = None
    ctx.save_state(state)
    print(f"COMMITTED_{kind} {sha[:10]}" + (f"（{TRAILER}: {link}）" if link else ""))
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pair_align.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("begin", help="今回の実行が何から始まるかを決める")
    b.add_argument("--intent-file", help="利用者の意図を書いたファイル")
    b.add_argument("--decision", help="確認への答え（相手を直す / 意図を直す / やめる）")
    sub.add_parser("verify-reading", help="相手を読んだ結果（前提・制約・自由）を検査する")
    sub.add_parser("verify-question", help="利用者への確認を検査する")
    sub.add_parser("pause", help="利用者の答えを待つために止まる")
    sub.add_parser("self-check", help="自分への変更を検査する（pair.json の check）")
    m = sub.add_parser("commit", help="自分への変更をコミットし、意図を片付ける")
    m.add_argument("-m", "--message", default="", help="コミットメッセージ")
    c = sub.add_parser("collect", help="受け取った依頼の残りと自分の変更を集める")
    c.add_argument("--since", help="この版からの変更を見る（既定は前回の基準点）")
    c.add_argument("--ignore-inbound", action="store_true", help="未処理の依頼があっても自分の変更を集める")
    l = sub.add_parser("locate", help="相手のリポジトリで関係しそうな箇所を探す")
    l.add_argument("--term", action="append", help="検索語（繰り返し可。collect が拾った語に足す）")
    l.add_argument("--refresh", action="store_true", help="探す前に graphify update で相手のグラフを更新する")
    sub.add_parser("verify-prompt", help="依頼文が決まった形を満たすか検査する")
    r = sub.add_parser("record", help="依頼文を送り箱へ移し、基準点を進める")
    r.add_argument("--skip", action="store_true", help="依頼を出さずに基準点だけ進める")
    a = sub.add_parser("ack", help="相手からの依頼を反映不要として受け取り済みにする")
    a.add_argument("id")
    a.add_argument("--force", action="store_true")
    sub.add_parser("status", help="設定・基準点・未処理の依頼・進行中の意図を表示する")
    return p


COMMANDS = {
    "begin": cmd_begin, "verify-reading": cmd_verify_reading, "verify-question": cmd_verify_question,
    "pause": cmd_pause, "self-check": cmd_self_check, "commit": cmd_commit,
    "collect": cmd_collect, "locate": cmd_locate, "verify-prompt": cmd_verify_prompt,
    "record": cmd_record, "ack": cmd_ack, "status": cmd_status,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = Ctx(repo_root(Path.cwd()))
        return COMMANDS[args.cmd](ctx, args)
    except PairAlignError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
