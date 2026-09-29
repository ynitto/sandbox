#!/usr/bin/env python3
"""pair_align — 実装と設計書の 2 リポジトリを交互に揃えるステートマシンの決定的な下請け。

ステートマシン（同じフォルダの workflow.yaml）のうち、機械で決まる仕事だけをここに置く。
判断（反映が要るか・どこへ反映するか・依頼文をどう書くか）はアクションの側でモデルが行い、
このスクリプトは材料を集めて、書かれた依頼文を検査し、控えを残す。

    collect        受け取った依頼の残りを確かめ、無ければ自分の変更を集める
    locate         相手のリポジトリで関係しそうな箇所を探す（graphify があれば使う）
    verify-prompt  書かれた依頼文が決まった形を満たすかを検査する（check 用）
    record         依頼文を送り箱へ移し、基準点を進める（--skip で依頼なしに進める）
    ack ID         相手からの依頼を「反映不要」として受け取り済みにする
    status         設定・基準点・未処理の依頼を表示する

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

    def require_pair(self) -> None:
        if not self.pair.is_dir():
            raise PairAlignError(
                f"相手のリポジトリが見つかりません: {self.pair}\n"
                f"  {CONFIG_FILE} の pair_path を直してください"
            )
        if not git(["rev-parse", "--show-toplevel"], self.pair, check=False).strip():
            raise PairAlignError(f"相手のフォルダが git リポジトリではありません: {self.pair}")

    def load_state(self) -> dict:
        if not self.state_file.is_file():
            return {"baseline": None, "acked": [], "sent": []}
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state.setdefault("baseline", None)
        state.setdefault("acked", [])
        state.setdefault("sent", [])
        return state

    def save_state(self, state: dict) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def load_current(self) -> dict:
        path = self.work / "current.json"
        if not path.is_file():
            raise PairAlignError("先に collect を実行してください（.pair-align/work/current.json がありません）")
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

def verify_prompt_text(text: str, pid: str) -> list[str]:
    problems = []
    positions = []
    for heading in REQUIRED_HEADINGS:
        m = re.search(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE)
        if not m:
            problems.append(f"見出しがありません: {heading}")
            continue
        positions.append((m.start(), heading, m.end()))
    positions.sort()
    for i, (_, heading, end) in enumerate(positions):
        stop = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        if not re.sub(r"<!--.*?-->", "", text[end:stop], flags=re.DOTALL).strip():
            problems.append(f"見出しの中身が空です: {heading}")
    if [h for _, h, _ in positions] != [h for h in REQUIRED_HEADINGS if h in {p[1] for p in positions}]:
        problems.append("見出しの順番がテンプレートと違います")
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
                          "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
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
    pair_done = acknowledged_ids(ctx.pair, {"acked": []})
    pair_state_file = ctx.pair / DATA_DIRNAME / "state.json"
    if pair_state_file.is_file():
        pair_done.update(json.loads(pair_state_file.read_text(encoding="utf-8")).get("acked", []))
    sent = outbox_prompts(ctx.root)
    waiting = [(pid, p) for pid, p in sent if pid not in pair_done]
    print(f"送って相手が未処理の依頼: {len(waiting)} 件")
    for pid, path in waiting:
        print(f"  {pid}  {path}")
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pair_align.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
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
    sub.add_parser("status", help="設定・基準点・未処理の依頼を表示する")
    return p


COMMANDS = {
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
