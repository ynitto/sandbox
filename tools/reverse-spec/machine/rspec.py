#!/usr/bin/env python3
"""reverse-spec — 既存のソースコードから、根拠つきの仕様書を起こすステートマシンの駆動スクリプト。

置き先は仕様書リポジトリの `.rspec/rspec.py`。エージェント（Claude Code・Codex・GitHub Copilot・
Kiro・Gemini など、シェルを叩けるものなら何でも）は `step` で 1 段ずつ指示を受け取り、出力を渡す。
検査と遷移はこのスクリプトが行い、モデルの申告は信じない。

    python3 .rspec/rspec.py step              今の段の指示を出す（何度出してもよい）
    python3 .rspec/rspec.py step "OK"         今の段の出力を渡す（検査して次の段へ）
    python3 .rspec/rspec.py status            進み具合
    python3 .rspec/rspec.py run --agent claude  段ごとに新しいエージェントを起こして無人で回す
    python3 .rspec/rspec.py gates             最後の検査だけを動かす
    python3 .rspec/rspec.py drift [--apply]   ソースが変わった単位を挙げる（--apply で作り直しに回す）
    python3 .rspec/rspec.py requeue UNIT...   単位を作り直しに回す（--blocked で止めた単位をすべて）

依存は python3（3.9 以上）と git だけ。設計: docs/designs/reverse-spec-design.md
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("RSPEC_ROOT") or HERE.parent).resolve()
TOOL = ".rspec/rspec.py"

CONFIG_FILE = "rspec.json"
STATE_FILE = ".rspec/state.json"
LOCK_FILE = ".rspec/step.lock"
RUN_LOCK_FILE = ".rspec/run.lock"
TOKEN_FILE = ".rspec/run-token.json"
LOG_DIR = ".rspec/logs"

EXT = "extraction"
INVENTORY = f"{EXT}/inventory.tsv"
INVENTORY_SUMMARY = f"{EXT}/inventory-summary.md"
SCOPE = f"{EXT}/scope.md"
UNITS = f"{EXT}/units.txt"
VERIFY_DIR = f"{EXT}/verify"
WORKLOG = f"{EXT}/worklog.md"
GATE_RESULTS = f"{EXT}/gate-results.md"
REPORT = f"{EXT}/report.md"

SPECS = "specs"
OVERVIEW = f"{SPECS}/overview.md"
CROSS = f"{SPECS}/cross-cutting.md"
INDEX = f"{SPECS}/index.md"
SOURCE_INDEX = f"{SPECS}/source-index.md"

DEFAULT_CONFIG = {
    "source": "",
    "exclude": [".git/**", "node_modules/**", "**/__pycache__/**", "**/*.min.js", "**/*.map"],
    "confirm": "human",        # human: 単位の分け方を人が確かめる / auto: 確かめずに進む
    "on_stuck": "ask",         # ask: 単位で止まったら人に訊く / skip: その単位を止めて次へ（無人の長時間実行向け）
    "max_unit_lines": 2500,    # 1 単位の行数の上限（1 つの会話で読み切れる大きさに保つ）
    "max_unit_files": 30,
    "read_chunk": 600,         # これより長いファイルは範囲を区切って読むよう指示する
    "verify_sample": 8,        # 1 回の検証で確かめる根拠の数
    "max_verify_rounds": 3,
    "retries": 2,              # 検査で落ちたとき、同じ段をやり直す回数（使い切ったら stuck）
    "auto_commit": True,       # 段を進めるたびに仕様書リポジトリへコミットする（途中で落ちても戻れる）
    "agents": {},              # run で使うエージェントのコマンド（既定を上書き・追加する）
}

CATEGORIES = {
    "module": ("modules", ["責務", "機能要件", "処理フロー", "依存", "未確定事項"]),
    "api": ("api", ["概要", "契約", "エラー処理", "例", "未確定事項"]),
    "data": ("data", ["スキーマ", "制約と不変条件", "読み書きする箇所", "未確定事項"]),
    "behavior": ("behavior", ["トリガーと前提", "フローと状態遷移", "副作用と結果", "未確定事項"]),
    "screen": ("screens", ["画面の目的", "表示項目", "操作と遷移", "入力チェック", "未確定事項"]),
    "batch": ("batch", ["起動条件", "入力と出力", "処理手順", "異常時", "未確定事項"]),
}
OUT_OF_SCOPE = "out-of-scope"
# 「なし」と書いてよい節（根拠を求めない）
MAY_BE_EMPTY = {"依存", "未確定事項", "例", "入力チェック", "用語"}
OVERVIEW_SECTIONS = ["目的", "スコープ", "用語", "全体像", "外部とのつながり", "制約と前提", "非機能の特徴", "未確定事項"]
CROSS_SECTIONS = ["認証と認可", "エラー処理", "ログと監視", "設定", "トランザクションと整合性", "未確定事項"]
CROSS_MAY_BE_EMPTY = set(CROSS_SECTIONS)  # 横断の関心事は無いこともある（あれば根拠を書く）

STATUS_WORDS = {"pending": "未着手", "drafted": "検証待ち", "verified": "検証済み", "blocked": "止めた",
                "out-of-scope": "対象外"}
UNKNOWN_KINDS = ("blocking", "deferred", "out-of-scope", "undecidable", "partial")
PLACEHOLDER = "<!-- 書く:"
NONE_WORDS = {"なし", "該当なし", "なし。", "該当なし。", "-", "none", "n/a"}

# 段の定義。expect は出力の第 1 語。human は人の答えを待つ段（run はそこで止まる）。
STATES = {
    "scope": {"expect": ["OK", "FAILED"], "desc": "対象の範囲を確かめる"},
    "units": {"expect": ["OK", "FAILED"], "desc": "ソースを単位に分ける"},
    "confirm": {"expect": ["OK", "NG", "STOP"], "human": True, "desc": "単位の分け方を確かめてもらう"},
    "draft": {"expect": ["OK", "FAILED"], "unit": True, "desc": "単位の仕様書を書く"},
    "verify": {"expect": ["OK", "FAILED"], "unit": True, "desc": "根拠を別の目で確かめる"},
    "fix": {"expect": ["OK", "FAILED"], "unit": True, "desc": "検証で否とされた記述を直す"},
    "synthesize": {"expect": ["OK", "FAILED"], "desc": "全体の仕様書を書く"},
    "audit": {"expect": [], "desc": "最後の検査（自動）"},
    "stuck": {"expect": ["RETRY", "SKIP", "STOP"], "human": True, "desc": "止まった理由を示して次の手を訊く"},
    "done": {"expect": [], "terminal": True, "desc": "終わり"},
    "stopped": {"expect": [], "terminal": True, "desc": "やめた"},
}

CITE_RE = re.compile(r"`([^`\s]+?):(\d+)(?:-(\d+))?`")
SPEC_LINK_RE = re.compile(r"\[\[(SPEC-[A-Za-z0-9_.-]+)\]\]")
UNKNOWN_RE = re.compile(r"UNKNOWN(\[([a-z-]+)\])?")
MD_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
UNIT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# ---------------------------------------------------------------- 小道具

class Fail(Exception):
    """利用者に見せて止める誤り（終了コード 2）。"""


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def p(rel: str) -> Path:
    return ROOT / rel


def read(rel_or_path) -> str:
    path = rel_or_path if isinstance(rel_or_path, Path) else p(rel_or_path)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write(rel_or_path, text: str) -> None:
    path = rel_or_path if isinstance(rel_or_path, Path) else p(rel_or_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    raw = read(CONFIG_FILE)
    if raw:
        try:
            cfg.update(json.loads(raw))
        except json.JSONDecodeError as e:
            raise Fail(f"{CONFIG_FILE} が JSON として読めません: {e}")
    return cfg


def source_root(cfg: dict) -> Path:
    if not cfg.get("source"):
        raise Fail(f"{CONFIG_FILE} の source にソースのフォルダを書いてください")
    src = Path(cfg["source"])
    if not src.is_absolute():
        src = (ROOT / src)
    src = src.resolve()
    if not src.is_dir():
        raise Fail(f"ソースのフォルダがありません: {src}")
    return src


def load_state() -> dict:
    raw = read(STATE_FILE)
    if not raw:
        return {"version": 1, "state": "scope", "seq": 0, "attempts": {}, "units": {}, "order": [],
                "current": "", "answer": "", "stuck": {}, "findings": [], "updated": now()}
    return json.loads(raw)


def save_state(st: dict) -> None:
    st["updated"] = now()
    write(STATE_FILE, json.dumps(st, ensure_ascii=False, indent=2) + "\n")


def worklog(line: str) -> None:
    path = p(WORKLOG)
    if not path.exists():
        write(WORKLOG, "# 作業の記録\n\n段が進むたびに rspec が書き足す。\n\n")
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"- {now()} {line}\n")


def git(*args, cwd: Path = None, check=False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd or ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check)


def commit(message: str) -> None:
    if not load_config().get("auto_commit") or not (ROOT / ".git").exists():
        return
    git("add", "-A")
    if not git("status", "--porcelain").stdout.strip():
        return
    ident = []
    if not git("config", "user.email").stdout.strip():
        ident = ["-c", "user.name=rspec", "-c", "user.email=rspec@localhost"]
    git(*ident, "commit", "-q", "-m", message)


class Lock:
    """同じ仕様書リポジトリで step を重ねて動かさない（長い検査の途中で別のプロセスが状態を書き換えないように）。"""

    def __init__(self, rel: str, stale_sec: int = 7200):
        self.path = p(rel)
        self.stale_sec = stale_sec
        self.held = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pid": os.getpid(), "at": time.time()}).encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                if self._stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise Fail(f"ほかの rspec が動いています（{self.path.relative_to(ROOT)}）。終わるのを待ってください")
        raise Fail("ロックを取れませんでした")

    def _stale(self) -> bool:
        try:
            info = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return True
        if time.time() - info.get("at", 0) > self.stale_sec:
            return True
        pid = info.get("pid")
        if os.name != "nt" and pid:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
        return False

    def __exit__(self, *exc):
        if self.held:
            self.path.unlink(missing_ok=True)


# ---------------------------------------------------------------- パターン

def glob_re(pattern: str) -> re.Pattern:
    pat = pattern.strip()
    if pat.startswith("./"):
        pat = pat[2:]
    if pat.endswith("/"):
        pat += "**"
    if not any(ch in pat for ch in "*?["):
        return re.compile(re.escape(pat) + r"(?:/.*)?$")
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            out += ".*"
            i += 2
        elif pat[i] == "*":
            out += "[^/]*"
            i += 1
        elif pat[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pat[i])
            i += 1
    return re.compile(out + "$")


def matcher(patterns):
    regs = [glob_re(x) for x in patterns if x.strip()]
    return lambda path: any(r.match(path) for r in regs)


# ---------------------------------------------------------------- 取り込み（ingest）

def list_source_files(src: Path) -> list:
    if (src / ".git").exists():
        proc = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=src)
        if proc.returncode == 0:
            return sorted({x for x in proc.stdout.split("\0") if x and (src / x).is_file()})
    files = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            files.append(Path(dirpath, name).relative_to(src).as_posix())
    return sorted(files)


def count_lines(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def pin_source(src: Path, inventory_sha: str) -> str:
    if (src / ".git").exists():
        head = git("rev-parse", "HEAD", cwd=src)
        if head.returncode == 0:
            dirty = git("status", "--porcelain", cwd=src).stdout.strip()
            return head.stdout.strip() + (" (dirty)" if dirty else "")
    return "tree:" + inventory_sha[:16]


def ingest(cfg: dict = None) -> dict:
    """ソースのファイルを数え上げ、分母（inventory.tsv）と要約を書く。モデルには数えさせない。"""
    cfg = cfg or load_config()
    src = source_root(cfg)
    excluded = matcher(cfg.get("exclude", []))
    rows, skipped = [], []
    for rel in list_source_files(src):
        if excluded(rel):
            skipped.append(rel)
            continue
        data = (src / rel).read_bytes()
        kind = "binary" if b"\0" in data[:8192] else "text"
        rows.append((rel, count_lines(data) if kind == "text" else 0, hashlib.sha1(data).hexdigest(), kind))
    body = "".join(f"{r}\t{n}\t{h}\t{k}\n" for r, n, h, k in rows)
    pin = pin_source(src, sha(body.encode()))
    write(INVENTORY, f"# source-commit: {pin}\npath\tlines\tsha1\tkind\n" + body)
    write(INVENTORY_SUMMARY, summarize_inventory(src, pin, rows, skipped, cfg))
    return {"pin": pin, "files": len(rows), "skipped": len(skipped)}


def summarize_inventory(src, pin, rows, skipped, cfg) -> str:
    text = [r for r in rows if r[3] == "text"]
    by_dir, by_ext = {}, {}
    for rel, n, _, _ in text:
        top = rel.split("/", 1)[0] if "/" in rel else "(直下)"
        ext = Path(rel).suffix or "(拡張子なし)"
        for table, key in ((by_dir, top), (by_ext, ext)):
            files, lines = table.get(key, (0, 0))
            table[key] = (files + 1, lines + n)
    out = ["# ソースの数え上げ（rspec が書く。手で直さない）", "",
           f"- ソース: `{src}`", f"- 固定した版: `{pin}`",
           f"- 対象のファイル: {len(text)}（{sum(r[1] for r in text)} 行）",
           f"- バイナリ（分母に入れない）: {len(rows) - len(text)}",
           f"- 除外したファイル: {len(skipped)}（`{CONFIG_FILE}` の exclude: {', '.join(cfg.get('exclude', [])) or 'なし'}）",
           "", "## フォルダごと", "", "| フォルダ | ファイル | 行 |", "|---|---:|---:|"]
    out += [f"| `{k}` | {f} | {n} |" for k, (f, n) in sorted(by_dir.items(), key=lambda x: -x[1][1])]
    out += ["", "## 拡張子ごと", "", "| 拡張子 | ファイル | 行 |", "|---|---:|---:|"]
    out += [f"| `{k}` | {f} | {n} |" for k, (f, n) in sorted(by_ext.items(), key=lambda x: -x[1][1])]
    out += ["", "## 大きいファイル", "", "| ファイル | 行 |", "|---|---:|"]
    out += [f"| `{r}` | {n} |" for r, n, _, _ in sorted(text, key=lambda x: -x[1])[:15]]
    return "\n".join(out) + "\n"


def load_inventory() -> dict:
    """{path: {"lines": n, "sha1": h, "kind": k}} と固定した版。"""
    inv, pin = {}, ""
    for line in read(INVENTORY).splitlines():
        if line.startswith("# source-commit:"):
            pin = line.split(":", 1)[1].strip()
            continue
        if not line or line.startswith("path\t"):
            continue
        rel, n, h, k = line.split("\t")
        inv[rel] = {"lines": int(n), "sha1": h, "kind": k}
    return {"files": inv, "pin": pin}


def text_files(inv: dict) -> dict:
    return {k: v for k, v in inv["files"].items() if v["kind"] == "text"}


# ---------------------------------------------------------------- 単位（units.txt）

def parse_units(text: str) -> tuple:
    """units.txt を読む。`unit <id> <category> <title>` の行のあとに、字下げしたパターンを並べる。"""
    units, errors, cur = [], [], None
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not raw[:1].isspace():
            parts = line.split(None, 3)
            if len(parts) < 3 or parts[0] != "unit":
                errors.append(f"{UNITS}:{no}: 単位の行は `unit <id> <種類> <題名>` と書く: {line}")
                cur = None
                continue
            uid, cat = parts[1], parts[2]
            title = parts[3] if len(parts) > 3 else ""
            if not UNIT_ID_RE.match(uid):
                errors.append(f"{UNITS}:{no}: id は小文字・数字・ハイフン・下線で書く: {uid}")
            if cat not in CATEGORIES and cat != OUT_OF_SCOPE:
                errors.append(f"{UNITS}:{no}: 種類は {', '.join(list(CATEGORIES) + [OUT_OF_SCOPE])} のどれか: {cat}")
            if not title:
                errors.append(f"{UNITS}:{no}: 題名（{OUT_OF_SCOPE} なら対象外にする理由）を書く: {uid}")
            cur = {"id": uid, "category": cat, "title": title, "patterns": [], "line": no}
            units.append(cur)
        else:
            if cur is None:
                errors.append(f"{UNITS}:{no}: パターンの前に unit の行が要る")
                continue
            cur["patterns"].append(line.strip())
    seen = set()
    for u in units:
        if u["id"] in seen:
            errors.append(f"{UNITS}:{u['line']}: id が重なっている: {u['id']}")
        seen.add(u["id"])
        if not u["patterns"]:
            errors.append(f"{UNITS}:{u['line']}: パターンが 1 つも無い: {u['id']}")
    return units, errors


def resolve_units(units: list, inv: dict) -> dict:
    files = sorted(text_files(inv))
    out = {}
    for u in units:
        match = matcher(u["patterns"])
        out[u["id"]] = [f for f in files if match(f)]
    return out


def check_units(cfg: dict) -> tuple:
    """分母（数え上げたテキストのファイル）をすべて、どれかの単位か対象外で受け持っているかを確かめる。"""
    inv = load_inventory()
    if not inv["files"]:
        return False, ["数え上げ（inventory）がありません。`python3 .rspec/rspec.py ingest` を動かしてください"], [], {}
    text = read(UNITS)
    if not text.strip():
        return False, [f"{UNITS} がありません（空です）"], [], {}
    units, errors = parse_units(text)
    resolved = resolve_units(units, inv)
    tf = text_files(inv)
    owners = {}
    for u in units:
        if not resolved[u["id"]]:
            errors.append(f"{u['id']}: パターンに当たるファイルが無い（{', '.join(u['patterns'])}）")
        for f in resolved[u["id"]]:
            owners.setdefault(f, []).append(u)
        if u["category"] == OUT_OF_SCOPE:
            continue
        lines = sum(tf[f]["lines"] for f in resolved[u["id"]])
        n = len(resolved[u["id"]])
        if n > 1 and lines > cfg["max_unit_lines"]:
            errors.append(f"{u['id']}: {lines} 行あり上限 {cfg['max_unit_lines']} を超える。単位を分ける"
                          f"（1 ファイルだけで超えるなら、その 1 ファイルを 1 単位にする）")
        if n > cfg["max_unit_files"]:
            errors.append(f"{u['id']}: {n} ファイルあり上限 {cfg['max_unit_files']} を超える。単位を分ける")
    uncovered = [f for f in tf if f not in owners]
    if uncovered:
        shown = "\n".join(f"    {f}" for f in uncovered[:40])
        more = f"\n    …ほか {len(uncovered) - 40} 件" if len(uncovered) > 40 else ""
        errors.append(f"どの単位にも入っていないファイルが {len(uncovered)} 件（分母を使い切っていない）:\n{shown}{more}")
    for f, us in owners.items():
        cats = {u["category"] for u in us}
        if OUT_OF_SCOPE in cats and len(cats) > 1:
            errors.append(f"{f}: 対象外と、対象の単位（{', '.join(u['id'] for u in us if u['category'] != OUT_OF_SCOPE)}）の両方に入っている")
    return not errors, errors, units, resolved


# ---------------------------------------------------------------- 仕様書の読み書き

def spec_id(unit: dict) -> str:
    return f"SPEC-{unit['category']}-{unit['id']}"


def spec_path(unit: dict) -> str:
    return f"{SPECS}/{CATEGORIES[unit['category']][0]}/{unit['id']}.md"


def parse_front(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def sections(text: str) -> dict:
    """`## 見出し` ごとの本文（### 以下を含む）。HTML コメントは本文に数えない。"""
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur, buf = line[3:].strip(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


def blank_comments(text: str) -> str:
    """HTML コメントを消す（行番号は保つ）。ひな形の説明にある書き方の例を、根拠や UNKNOWN と取り違えないため。"""
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def citations(text: str) -> list:
    """[(行番号, その行, パス, 始め, 終わり)]。根拠はバッククォートで囲んだ `パス:行` か `パス:行-行`。"""
    out = []
    for no, line in enumerate(blank_comments(text).splitlines(), 1):
        for m in CITE_RE.finditer(line):
            path = m.group(1)
            if "://" in path or path.startswith("http"):
                continue
            start = int(m.group(2))
            end = int(m.group(3) or start)
            out.append((no, line, path, start, end))
    return out


def check_citations(text: str, label: str, inv: dict) -> list:
    errors = []
    files = inv["files"]
    for no, _, path, start, end in citations(text):
        where = f"{label}:{no}"
        info = files.get(path)
        if info is None:
            errors.append(f"{where}: 根拠のファイルが数え上げに無い: `{path}`（パスはソースのフォルダからの相対で書く）")
        elif info["kind"] != "text":
            errors.append(f"{where}: バイナリのファイルを行で指している: `{path}`")
        elif start < 1 or end < start or end > info["lines"]:
            errors.append(f"{where}: 行が範囲外: `{path}:{start}-{end}`（{info['lines']} 行まで）")
    return errors


def check_unknowns(text: str, label: str) -> list:
    errors = []
    for no, line in enumerate(blank_comments(text).splitlines(), 1):
        for m in UNKNOWN_RE.finditer(line):
            kind = m.group(2)
            if kind not in UNKNOWN_KINDS:
                errors.append(f"{label}:{no}: UNKNOWN は UNKNOWN[{'|'.join(UNKNOWN_KINDS)}] と書く")
            elif kind == "partial" and ("確認した範囲" not in line or not CITE_RE.search(line)):
                errors.append(f"{label}:{no}: UNKNOWN[partial] には同じ行に「確認した範囲: `パス:行`」を書く")
    return errors


def grounded(body: str) -> bool:
    return bool(CITE_RE.search(body) or SPEC_LINK_RE.search(body) or "UNKNOWN[" in body)


def check_sections(text: str, label: str, required: list, may_be_empty: set) -> list:
    errors = []
    secs = sections(text)
    for name in required:
        if name not in secs:
            errors.append(f"{label}: 見出し `## {name}` が無い（見出しの名前と順番は変えない）")
            continue
        body = strip_comments(secs[name])
        if not body:
            errors.append(f"{label}: `## {name}` が空。書くことが無ければ「なし」と書く" if name in may_be_empty
                          else f"{label}: `## {name}` が空")
            continue
        if body.lower() in NONE_WORDS:
            if name not in may_be_empty:
                errors.append(f"{label}: `## {name}` は「なし」にできない。分からなければ UNKNOWN[...] で書く")
            continue
        if not grounded(body):
            errors.append(f"{label}: `## {name}` に根拠（`パス:行`）も UNKNOWN[...] も無い")
    if PLACEHOLDER in text:
        no = text[:text.index(PLACEHOLDER)].count("\n") + 1
        errors.append(f"{label}:{no}: ひな形の「書く:」のコメントが残っている（本文に置き換える）")
    return errors


def check_unit_spec(unit: dict, files: list, inv: dict) -> list:
    path = spec_path(unit)
    text = read(path)
    if not text:
        return [f"{path} がありません"]
    errors = []
    front = parse_front(text)
    if front.get("spec-id") != spec_id(unit) or front.get("unit") != unit["id"]:
        errors.append(f"{path}: 先頭の spec-id / unit を書き換えない（{spec_id(unit)} / {unit['id']}）")
    errors += check_sections(text, path, CATEGORIES[unit["category"]][1], MAY_BE_EMPTY)
    errors += check_citations(text, path, inv)
    errors += check_unknowns(text, path)
    cited = {c[2] for c in citations(text)}
    missing = [f for f in files if f not in cited]
    if missing:
        errors.append(f"{path}: 単位のファイルのうち、根拠に一度も出てこないもの（読んで根拠に挙げる）:\n"
                      + "\n".join(f"    {f}" for f in missing))
    return errors


def render_template(name: str, values: dict) -> str:
    text = read(HERE / "templates" / name)
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def ensure_unit_spec(unit: dict, files: list, pin: str) -> str:
    path = spec_path(unit)
    if not p(path).exists():
        write(path, render_template(f"{unit['category']}.md", {
            "spec_id": spec_id(unit), "unit": unit["id"], "category": unit["category"], "title": unit["title"],
            "source_commit": pin, "source_paths": ", ".join(unit["patterns"]),
        }))
    return path


# ---------------------------------------------------------------- 検証（verify）

def sheet_path(uid: str, rnd: int) -> str:
    return f"{VERIFY_DIR}/{uid}-{rnd}.md"


def make_sheet(st: dict, unit: dict, cfg: dict, src: Path) -> str:
    """仕様書の根拠から決まった数を選び、検証の用紙を書く。選ぶのはスクリプトで、モデルに選ばせない。"""
    u = st["units"][unit["id"]]
    rnd = u.get("round", 0) + 1
    path = sheet_path(unit["id"], rnd)
    if p(path).exists() and u.get("sheet") == path:
        return path
    text = read(spec_path(unit))
    seen, items = set(), []
    for no, line, ref, start, end in citations(text):
        key = f"{ref}:{start}-{end}"
        if key in seen:
            continue
        seen.add(key)
        items.append({"spec_line": no, "claim": line.strip()[:400], "ref": ref, "start": start, "end": end, "key": key})
    forced = set(u.get("ng_keys", []))
    items.sort(key=lambda it: (it["key"] not in forced, hashlib.sha1(f"{unit['id']}:{rnd}:{it['key']}".encode()).hexdigest()))
    picked = items[:max(1, cfg["verify_sample"])]
    picked.sort(key=lambda it: it["spec_line"])
    out = [f"# 検証: {unit['id']}（{rnd} 回目）", "",
           f"ソースのフォルダ: `{src}`", "",
           "各 `### C番号` について、**根拠の行だけ**をソースで開き、主張がその行から言えるかを判定する。",
           "仕様書（specs/）は開かない。判定は `OK` か `NG`。`NG` なら理由に、ソースでは実際にどうなっているかを書く。",
           "主張の言い過ぎ（根拠の行からは言えないことまで書いている）も `NG`。", ""]
    for i, it in enumerate(picked, 1):
        rng = f"{it['start']}" if it["start"] == it["end"] else f"{it['start']}-{it['end']}"
        out += [f"### C{i}", f"- 主張: {it['claim']}", f"- 根拠: `{it['ref']}:{rng}`",
                "- 判定: ?", "- 理由: ", ""]
    write(path, "\n".join(out))
    u["round"] = rnd
    u["sheet"] = path
    u["claims"] = [{"key": it["key"], "claim": it["claim"]} for it in picked]
    return path


def read_sheet(path: str) -> tuple:
    """[(番号, 判定, 理由)] と誤り。"""
    text = read(path)
    blocks = re.split(r"^### C(\d+)\s*$", text, flags=re.M)
    out, errors = [], []
    for i in range(1, len(blocks), 2):
        n, body = int(blocks[i]), blocks[i + 1]
        m = re.search(r"^- 判定:\s*(\S*)", body, flags=re.M)
        verdict = (m.group(1) if m else "").upper()
        r = re.search(r"^- 理由:[ \t]*(.*)$", body, flags=re.M)
        reason = (r.group(1).strip() if r else "")
        if verdict not in ("OK", "NG"):
            errors.append(f"{path}: C{n} の判定が OK / NG になっていない（{verdict or '空'}）")
        elif verdict == "NG" and not reason:
            errors.append(f"{path}: C{n} は NG なので理由を書く")
        out.append((n, verdict, reason))
    return out, errors


# ---------------------------------------------------------------- 全体の仕様書と索引

def all_units(st: dict, cfg: dict = None) -> list:
    units, _ = parse_units(read(UNITS))
    return [u for u in units if u["id"] in st["units"]]


def in_scope(units: list) -> list:
    return [u for u in units if u["category"] in CATEGORIES and UNIT_ID_RE.fullmatch(u["id"])]


def generate_indexes(st: dict) -> None:
    units = all_units(st)
    inv = load_inventory()
    rows = ["# 仕様書の索引（rspec が書く。手で直さない）", "",
            f"- ソースの版: `{inv['pin']}`", "",
            "| spec-id | 種類 | 題名 | 状態 | ファイル |", "|---|---|---|---|---|"]
    for u in in_scope(units):
        s = st["units"].get(u["id"], {}).get("status", "pending")
        rel = spec_path(u)[len(SPECS) + 1:]
        rows.append(f"| {spec_id(u)} | {u['category']} | {u['title']} | {STATUS_WORDS.get(s, s)} | [{rel}]({rel}) |")
    oos = [u for u in units if u["category"] == OUT_OF_SCOPE]
    if oos:
        rows += ["", "## 対象外にしたもの", "", "| 単位 | 理由 | パターン |", "|---|---|---|"]
        rows += [f"| {u['id']} | {u['title']} | {' '.join(f'`{x}`' for x in u['patterns'])} |" for u in oos]
    write(INDEX, "\n".join(rows) + "\n")
    write(SOURCE_INDEX, source_index_text(units, inv))


def source_index_text(units: list, inv: dict) -> str:
    cited = {}
    for u in in_scope(units):
        for c in citations(read(spec_path(u))):
            cited.setdefault(c[2], set()).add(spec_id(u))
    resolved = resolve_units(units, inv)
    owner = {}
    for u in units:
        for f in resolved.get(u["id"], []):
            owner.setdefault(f, []).append(u["id"])
    rows = ["# ソースから仕様書へ（rspec が書く。手で直さない）", "",
            "| ソースのファイル | 単位 | 根拠に使った仕様書 |", "|---|---|---|"]
    for f in sorted(text_files(inv)):
        rows.append(f"| `{f}` | {', '.join(owner.get(f, [])) or '—'} | {', '.join(sorted(cited.get(f, []))) or '—'} |")
    return "\n".join(rows) + "\n"


def ensure_synth_files(pin: str) -> None:
    for rel, name in ((OVERVIEW, "overview.md"), (CROSS, "cross-cutting.md")):
        if not p(rel).exists():
            write(rel, render_template(name, {"source_commit": pin}))


def check_synth(st: dict) -> list:
    inv = load_inventory()
    ids = {spec_id(u) for u in in_scope(all_units(st))}
    errors = []
    for rel, required, empty_ok in ((OVERVIEW, OVERVIEW_SECTIONS, MAY_BE_EMPTY), (CROSS, CROSS_SECTIONS, CROSS_MAY_BE_EMPTY)):
        text = read(rel)
        if not text:
            errors.append(f"{rel} がありません")
            continue
        errors += check_sections(text, rel, required, empty_ok)
        errors += check_citations(text, rel, inv)
        errors += check_unknowns(text, rel)
        for no, line in enumerate(blank_comments(text).splitlines(), 1):
            for m in SPEC_LINK_RE.finditer(line):
                if m.group(1) not in ids:
                    errors.append(f"{rel}:{no}: 無い仕様書を指している: [[{m.group(1)}]]")
    return errors


# ---------------------------------------------------------------- 最後の検査（gates）

def run_gates(st: dict, cfg: dict) -> list:
    """[(名前, 通ったか, 詳細)]。通らないものが 1 つでもあれば終われない。"""
    results = []
    inv = load_inventory()
    results.append(("G-INVENTORY", bool(inv["files"] and inv["pin"]),
                    f"{len(inv['files'])} ファイル、版 {inv['pin'] or '未固定'}"))
    # ソースが固定した版から変わっていないか
    try:
        src = source_root(cfg)
        changed = drift_files(src, inv)
        results.append(("G-FRESH", not any(changed.values()),
                        "変わっていない" if not any(changed.values()) else
                        "ソースが変わった（`drift` で確かめる）: " + ", ".join(f"{k} {len(v)}" for k, v in changed.items() if v)))
    except Fail as e:
        results.append(("G-FRESH", False, str(e)))
    ok, errors, units, resolved = check_units(cfg)
    results.append(("G-DENOM", ok, "分母をすべて受け持った" if ok else "; ".join(e.splitlines()[0] for e in errors)))
    spec_errors, verify_errors, blocked = [], [], []
    for u in in_scope(units):
        su = st["units"].get(u["id"], {})
        if su.get("status") == "blocked":
            blocked.append(u["id"])
            continue
        spec_errors += check_unit_spec(u, resolved.get(u["id"], []), inv)
        text = read(spec_path(u))
        if su.get("status") != "verified":
            verify_errors.append(f"{u['id']}: 検証していない")
        elif su.get("spec_sha") != sha(text.encode()):
            verify_errors.append(f"{u['id']}: 検証したあとに仕様書が変わった（`requeue {u['id']}` で検証し直す）")
    results.append(("G-SPEC", not spec_errors, f"{len(spec_errors)} 件" + (": " + spec_errors[0].splitlines()[0] if spec_errors else "")))
    results.append(("G-VERIFY", not verify_errors, "; ".join(verify_errors) or "すべて検証済み"))
    results.append(("G-BLOCKED", not blocked, ", ".join(blocked) or "止めた単位は無い"))
    synth = check_synth(st)
    results.append(("G-SYNTH", not synth, f"{len(synth)} 件" + (": " + synth[0] if synth else "")))
    links = check_links()
    results.append(("G-LINK", not links, "; ".join(links[:5]) or "切れたリンクは無い"))
    stale = []
    if read(INDEX) or read(SOURCE_INDEX):
        before = (read(INDEX), read(SOURCE_INDEX))
        generate_indexes(st)
        if before != (read(INDEX), read(SOURCE_INDEX)):
            stale.append("索引を作り直した（手で直していた分は消えた）")
    results.append(("G-INDEX", True, "; ".join(stale) or "索引は最新"))
    return results


def check_links() -> list:
    errors = []
    root = p(SPECS)
    for md in sorted(root.rglob("*.md")):
        for no, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            for m in MD_LINK_RE.finditer(line):
                target = m.group(1).split("#", 1)[0]
                if not target or re.match(r"^[a-z]+:", target):
                    continue
                if not (md.parent / target).exists():
                    errors.append(f"{md.relative_to(ROOT).as_posix()}:{no}: リンク先が無い: {target}")
    return errors


def drift_files(src: Path, inv: dict) -> dict:
    cfg = load_config()
    excluded = matcher(cfg.get("exclude", []))
    now_files = {f for f in list_source_files(src) if not excluded(f)}
    changed, removed = [], []
    for rel, info in inv["files"].items():
        path = src / rel
        if rel not in now_files or not path.is_file():
            removed.append(rel)
        elif hashlib.sha1(path.read_bytes()).hexdigest() != info["sha1"]:
            changed.append(rel)
    added = sorted(now_files - set(inv["files"]))
    return {"changed": sorted(changed), "added": added, "removed": sorted(removed)}


def format_gates(results: list) -> str:
    lines = [f"# 最後の検査の結果（{now()}）", "", "| 検査 | 結果 | 詳細 |", "|---|---|---|"]
    lines += [f"| {n} | {'通過' if ok else '不通過'} | {d.replace('|', '/')} |" for n, ok, d in results]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- ステートマシン

def unit_by_id(uid: str) -> dict:
    for u in parse_units(read(UNITS))[0]:
        if u["id"] == uid:
            return u
    raise Fail(f"単位がありません: {uid}")


def unit_files(uid: str) -> list:
    _, _, units, resolved = check_units(load_config())
    return resolved.get(uid, [])


def goto(st: dict, state: str, note: str = "") -> None:
    prev = st["state"]
    st["state"] = state
    st["attempts"][state] = 0
    worklog(f"{prev} → {state}" + (f" {st.get('current')}" if STATES[state].get("unit") else "") + (f": {note}" if note else ""))
    enter(st)


def enter(st: dict) -> None:
    """段に入ったときにスクリプトがする用意（ひな形を置く・用紙を作る・自動の検査を動かす）。"""
    cfg = load_config()
    state = st["state"]
    if state == "confirm" and cfg.get("confirm") == "auto":
        goto(st, *next_unit(st, "単位の確認は省く設定"))
    elif state == "draft":
        u = unit_by_id(st["current"])
        ensure_unit_spec(u, unit_files(u["id"]), load_inventory()["pin"])
    elif state == "verify":
        make_sheet(st, unit_by_id(st["current"]), cfg, source_root(cfg))
    elif state == "synthesize":
        ensure_synth_files(load_inventory()["pin"])
        generate_indexes(st)
    elif state == "audit":
        generate_indexes(st)
        results = run_gates(st, cfg)
        write(GATE_RESULTS, format_gates(results))
        failed = [f"{n}: {d}" for n, ok, d in results if not ok]
        if failed:
            go_stuck(st, "audit", "最後の検査が通らない:\n" + "\n".join(f"  - {x}" for x in failed))
        else:
            goto(st, "done")
    elif state == "done":
        write(REPORT, report_text(st))


def next_unit(st: dict, note: str = "") -> tuple:
    """次にする段。単位の並び順に、未着手なら書く、検証待ちなら検証する。無ければ全体の仕様書へ。"""
    for uid in st["order"]:
        s = st["units"][uid]["status"]
        if s == "pending":
            st["current"] = uid
            return "draft", note
        if s == "drafted":
            st["current"] = uid
            return "verify", note
    st["current"] = ""
    return "synthesize", note


def go_stuck(st: dict, origin: str, reason: str) -> None:
    cfg = load_config()
    unit = st.get("current", "") if STATES[origin].get("unit") else ""
    if unit and cfg.get("on_stuck") == "skip":
        st["units"][unit]["status"] = "blocked"
        st["units"][unit]["note"] = reason
        worklog(f"{unit} を止めて次へ（on_stuck: skip）: {reason.splitlines()[0]}")
        goto(st, *next_unit(st))
        return
    st["stuck"] = {"from": origin, "unit": unit, "reason": reason}
    goto(st, "stuck", reason.splitlines()[0])


def retry_or_stuck(st: dict, errors: list) -> None:
    cfg = load_config()
    state = st["state"]
    st["attempts"][state] = st["attempts"].get(state, 0) + 1
    st["findings"] = errors
    if st["attempts"][state] > cfg["retries"]:
        go_stuck(st, state, f"検査が {st['attempts'][state]} 回続けて通らない:\n" + "\n".join(errors))


def sync_units(st: dict, units: list, resolved: dict) -> None:
    """units.txt の単位を状態に写す。ファイルの組み合わせが変わった単位は作り直しに回す。"""
    inv = text_files(load_inventory())
    old = st["units"]
    new, order = {}, []
    for u in units:
        if u["category"] == OUT_OF_SCOPE:
            new[u["id"]] = {"status": "out-of-scope"}
            continue
        fp = sha("\n".join(f"{f}:{inv[f]['sha1']}" for f in resolved[u["id"]]).encode() + u["category"].encode())
        prev = old.get(u["id"])
        if prev and prev.get("files_sha") == fp and prev.get("status") != "out-of-scope":
            new[u["id"]] = prev
        else:
            new[u["id"]] = {"status": "pending", "files_sha": fp, "round": 0}
        order.append(u["id"])
    st["units"], st["order"] = new, order


def submit(st: dict, text: str) -> list:
    """段の出力を受け取り、検査して遷移する。利用者に見せる指摘を返す。"""
    state = st["state"]
    spec = STATES[state]
    if spec.get("terminal"):
        raise Fail("もう終わっています（`status` で結果を見る）")
    word, _, rest = text.strip().partition(" ")
    word = word.upper().rstrip(":")
    if word not in spec["expect"]:
        raise Fail(f"この段（{state}）の出力の第 1 語は {' / '.join(spec['expect'])} のどれか（受け取った: {text[:40]!r}）")
    rest = rest.strip()
    st["seq"] += 1
    st["findings"] = []
    cfg = load_config()

    if word == "FAILED":
        go_stuck(st, state, f"エージェントが進められないと答えた: {rest or '（理由なし）'}")
        return st.get("findings", [])

    if state == "scope":
        errors = []
        if not load_inventory()["files"]:
            errors.append("数え上げがありません（`python3 .rspec/rspec.py ingest`）")
        scope = read(SCOPE)
        if not scope:
            errors.append(f"{SCOPE} がありません")
        else:
            errors += check_sections(scope, SCOPE, ["目的", "対象と対象外"], set())
        if errors:
            # 範囲のメモには根拠を求めない（check_sections の根拠の指摘は外す）
            errors = [e for e in errors if "根拠" not in e]
        if errors:
            retry_or_stuck(st, errors)
        else:
            goto(st, "units")
    elif state == "units":
        ok, errors, units, resolved = check_units(cfg)
        if not ok:
            retry_or_stuck(st, errors)
        else:
            sync_units(st, units, resolved)
            goto(st, "confirm", f"{len(st['order'])} 単位")
    elif state == "confirm":
        st["answer"] = rest
        if word == "OK":
            goto(st, *next_unit(st, "利用者が了承"))
        elif word == "NG":
            goto(st, "units", f"利用者の指摘: {rest}")
        else:
            goto(st, "stopped", rest)
    elif state == "draft":
        uid = st["current"]
        errors = check_unit_spec(unit_by_id(uid), unit_files(uid), load_inventory())
        if errors:
            retry_or_stuck(st, errors)
        else:
            st["units"][uid].update(status="drafted", round=0, ng_keys=[], sheet="")
            goto(st, "verify")
    elif state == "verify":
        uid = st["current"]
        su = st["units"][uid]
        verdicts, errors = read_sheet(su["sheet"])
        if len(verdicts) != len(su.get("claims", [])):
            errors.append(f"{su['sheet']}: C 番号の数が変わった（{len(su.get('claims', []))} 件あるはず）。用紙の形を変えない")
        if errors:
            retry_or_stuck(st, errors)
            return st["findings"]
        ng = [(n, r) for n, v, r in verdicts if v == "NG"]
        if not ng:
            su.update(status="verified", spec_sha=sha(read(spec_path(unit_by_id(uid))).encode()), ng=[], ng_keys=[])
            goto(st, *next_unit(st, f"{uid} を検証済みにした"))
        elif su["round"] >= cfg["max_verify_rounds"]:
            go_stuck(st, "verify", f"{su['round']} 回検証しても否とされる記述が残る:\n"
                     + "\n".join(f"  C{n}: {r}" for n, r in ng))
        else:
            su["ng"] = [{"n": n, "reason": r, **su["claims"][n - 1]} for n, r in ng if 0 < n <= len(su["claims"])]
            su["ng_keys"] = [x["key"] for x in su["ng"]]
            goto(st, "fix", f"否 {len(ng)} 件")
    elif state == "fix":
        uid = st["current"]
        su = st["units"][uid]
        text = read(spec_path(unit_by_id(uid)))
        errors = check_unit_spec(unit_by_id(uid), unit_files(uid), load_inventory())
        lines = {ln.strip()[:400] for ln in text.splitlines()}
        for x in su.get("ng", []):
            if x["claim"] in lines:
                errors.append(f"否とされた記述がそのまま残っている（C{x['n']}）: {x['claim'][:120]}")
        if errors:
            retry_or_stuck(st, errors)
        else:
            goto(st, "verify", "直したので検証し直す")
    elif state == "synthesize":
        errors = check_synth(st)
        if errors:
            retry_or_stuck(st, errors)
        else:
            goto(st, "audit")
    elif state == "stuck":
        stuck = st.get("stuck", {})
        origin, uid = stuck.get("from", ""), stuck.get("unit", "")
        if word == "STOP":
            goto(st, "stopped", rest)
        elif word == "SKIP":
            if not uid:
                raise Fail("SKIP は単位の段（draft / verify / fix）で止まったときだけ選べる。RETRY か STOP を選ぶ")
            st["units"][uid].update(status="blocked", note=stuck.get("reason", "") + (f"\n利用者: {rest}" if rest else ""))
            st["stuck"] = {}
            goto(st, *next_unit(st, f"{uid} を止めて次へ"))
        else:  # RETRY
            st["stuck"] = {}
            st["answer"] = rest
            if origin == "audit":
                for su in st["units"].values():
                    if su.get("status") == "blocked":
                        su["status"] = "pending"
                goto(st, *next_unit(st, "止めた単位をやり直す"))
            elif uid and origin == "verify":
                goto(st, "fix" if st["units"][uid].get("ng") else "verify", rest)
            else:
                goto(st, origin or "scope", rest)
    return st.get("findings", [])


# ---------------------------------------------------------------- 指示の組み立て

def render_action(st: dict) -> str:
    state = st["state"]
    cfg = load_config()
    values = {"tool": f"python3 {TOOL}", "answer": st.get("answer") or "（なし）"}
    if state in ("draft", "verify", "fix") and st.get("current"):
        u = unit_by_id(st["current"])
        files = unit_files(u["id"])
        inv = text_files(load_inventory())
        total = sum(inv[f]["lines"] for f in files)
        rows = []
        for f in files:
            n = inv[f]["lines"]
            hint = f"（長いので {cfg['read_chunk']} 行ずつ区切って読む）" if n > cfg["read_chunk"] else ""
            rows.append(f"- `{f}` {n} 行{hint}")
        su = st["units"][u["id"]]
        values.update(unit=u["id"], title=u["title"], category=u["category"], spec_id=spec_id(u),
                      spec=spec_path(u), files="\n".join(rows), total=total, count=len(files),
                      sections=" / ".join(CATEGORIES[u["category"]][1]),
                      source=str(source_root(cfg)), sheet=su.get("sheet", ""),
                      round=su.get("round", 0), claims=len(su.get("claims", [])),
                      ng="\n".join(f"- C{x['n']}: {x['claim'][:200]}\n  - 根拠: `{x['key']}`\n  - 検証の理由: {x['reason']}"
                                   for x in su.get("ng", [])) or "（なし）")
    if state == "confirm":
        values["summary"] = units_summary(st)
    if state == "stuck":
        s = st.get("stuck", {})
        values.update(origin=s.get("from", ""), unit=s.get("unit", "") or "（なし）", reason=s.get("reason", ""),
                      skip="- `SKIP 理由` — この単位を止めて次の単位へ進む（最後の検査で止めた単位として挙がる）\n"
                      if s.get("unit") else "")
    if state in ("scope", "units"):
        values.update(source=str(source_root(cfg)), max_lines=cfg["max_unit_lines"], max_files=cfg["max_unit_files"],
                      categories=", ".join(list(CATEGORIES) + [OUT_OF_SCOPE]))
    if state == "synthesize":
        values["specs"] = "\n".join(f"- [[{spec_id(u)}]] `{spec_path(u)}` {u['title']}" for u in in_scope(all_units(st))
                                    if st["units"][u["id"]]["status"] == "verified")
    if state in ("done", "stopped"):
        return report_text(st)
    text = read(HERE / "actions" / f"{state}.md")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def units_summary(st: dict) -> str:
    units = all_units(st)
    _, _, _, resolved = check_units(load_config())
    inv = text_files(load_inventory())
    rows = ["| 順 | 単位 | 種類 | 題名 | ファイル | 行 |", "|---:|---|---|---|---:|---:|"]
    for i, uid in enumerate(st["order"], 1):
        u = next(x for x in units if x["id"] == uid)
        fs = resolved.get(uid, [])
        rows.append(f"| {i} | {uid} | {u['category']} | {u['title']} | {len(fs)} | {sum(inv[f]['lines'] for f in fs)} |")
    oos = [u for u in units if u["category"] == OUT_OF_SCOPE]
    if oos:
        rows += ["", "対象外: " + "; ".join(f"{u['id']}（{u['title']}、{len(resolved.get(u['id'], []))} ファイル）" for u in oos)]
    rows += ["", f"段の見込み: 単位ごとに書く・検証するの 2 段 × {len(st['order'])} 単位 ＋ 全体 1 段"]
    return "\n".join(rows)


def header(st: dict) -> str:
    counts = progress(st)
    done = counts.get("verified", 0)
    total = sum(v for k, v in counts.items() if k != "out-of-scope")
    unit = f" / 単位 {st['current']}" if STATES[st["state"]].get("unit") and st.get("current") else ""
    return f"== reverse-spec: {st['state']}（{STATES[st['state']]['desc']}）{unit} — 検証済み {done}/{total} =="


def footer(st: dict) -> str:
    spec = STATES[st["state"]]
    if spec.get("terminal"):
        return "END"
    words = " / ".join(spec["expect"])
    wait = "利用者に見せて答えを待ち、答えをそのまま渡す。推測して先へ進まない。\n" if spec.get("human") else ""
    return (f"---\n{wait}終えたら: python3 {TOOL} step \"<出力>\"（第 1 語は {words}。2 語目から理由や指摘を続けてよい）")


def progress(st: dict) -> dict:
    counts = {}
    for su in st["units"].values():
        counts[su.get("status", "pending")] = counts.get(su.get("status", "pending"), 0) + 1
    return counts


def report_text(st: dict) -> str:
    counts = progress(st)
    inv = load_inventory()
    units = all_units(st)
    lines = [f"# reverse-spec の結果（{st['state']}）", "",
             f"- ソースの版: `{inv['pin']}`",
             f"- 単位: 検証済み {counts.get('verified', 0)}、止めた {counts.get('blocked', 0)}、"
             f"未着手 {counts.get('pending', 0)}、検証待ち {counts.get('drafted', 0)}、対象外 {counts.get('out-of-scope', 0)}",
             f"- 索引: `{INDEX}`、ソースからの索引: `{SOURCE_INDEX}`、最後の検査: `{GATE_RESULTS}`"]
    unknown = {k: 0 for k in UNKNOWN_KINDS}
    for u in in_scope(units):
        for m in UNKNOWN_RE.finditer(blank_comments(read(spec_path(u)))):
            if m.group(2) in unknown:
                unknown[m.group(2)] += 1
    lines.append("- 分からなかったこと: " + "、".join(f"{k} {v}" for k, v in unknown.items()))
    blocked = [(uid, su.get("note", "")) for uid, su in st["units"].items() if su.get("status") == "blocked"]
    if blocked:
        lines += ["", "## 止めた単位", ""] + [f"- {uid}: {note.splitlines()[0] if note else ''}" for uid, note in blocked]
    if unknown["blocking"]:
        lines += ["", f"UNKNOWN[blocking] が {unknown['blocking']} 件ある。利用者に訊いて埋める（`grep -rn 'UNKNOWN\\[blocking' specs`）"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- コマンド

def run_token(st_changed: bool = None) -> bool:
    """run が起こしたエージェントは 1 段だけ進める。段が進んだあとに次の出力を渡そうとしたら断る。

    検査で落ちて同じ段に残ったときは使い切らない（同じプロセスが指摘を直して渡し直せる。回数は retries で止まる）。
    st_changed が None なら確かめるだけ、True なら使い切ったと記す。"""
    token = os.environ.get("RSPEC_RUN_TOKEN")
    if not token:
        return True
    try:
        info = json.loads(read(TOKEN_FILE) or "{}")
    except ValueError:
        info = {}
    if info.get("token") != token:
        return True
    if st_changed is None:
        if info.get("used"):
            print("この実行で進める段は終わりました。ほかに何もせず、ここで終了してください。")
            return False
        return True
    if st_changed:
        info["used"] = True
        write(TOKEN_FILE, json.dumps(info))
    return True


def print_instruction(st: dict) -> None:
    print(header(st))
    if st.get("findings") and st["attempts"].get(st["state"], 0):
        print(f"直前の検査の指摘（{st['attempts'][st['state']]} 回目）。指摘されたところだけを直して、もう一度出力を渡す:")
        for f in st["findings"]:
            print("  - " + f.replace("\n", "\n    "))
        print()
    print(render_action(st))
    print(footer(st))


def cmd_step(args) -> int:
    with Lock(LOCK_FILE):
        st = load_state()
        if not run_token():
            return 0
        if args.output is None:
            print_instruction(st)
            return 0
        before = st["state"]
        try:
            submit(st, args.output)
        finally:
            save_state(st)
        stayed = st["state"] == before and st["attempts"].get(before, 0) > 0
        run_token(st_changed=not stayed)
        commit(f"rspec: {before} → {st['state']}" + (f" ({st['current']})" if st.get("current") else ""))
        if os.environ.get("RSPEC_RUN_TOKEN") and not stayed:
            print(f"受け付けました（今の段: {st['state']}）。ここで終了してください。次の段は別の実行で進めます。")
            return 0
        print_instruction(st)
        return 0


def cmd_status(args) -> int:
    st = load_state()
    counts = progress(st)
    print(header(st))
    print("単位: " + "、".join(f"{STATUS_WORDS.get(k, k)} {v}" for k, v in sorted(counts.items())) if counts
          else "単位: まだ分けていない")
    print(f"段を進めた回数: {st['seq']}、最後に進めた時刻: {st['updated']}")
    if st.get("findings"):
        print("直前の検査の指摘:")
        for f in st["findings"][:10]:
            print("  - " + f.splitlines()[0])
    if st["state"] == "stuck":
        print("止まっている理由: " + st["stuck"].get("reason", "").splitlines()[0])
    if args.json:
        print(json.dumps({"state": st["state"], "seq": st["seq"], "current": st.get("current"), "units": counts},
                         ensure_ascii=False))
    return 0


def cmd_ingest(args) -> int:
    info = ingest()
    print(f"数え上げました: {info['files']} ファイル（除外 {info['skipped']}）、版 {info['pin']}")
    print(f"要約: {INVENTORY_SUMMARY}")
    return 0


def cmd_gates(args) -> int:
    st = load_state()
    results = run_gates(st, load_config())
    write(GATE_RESULTS, format_gates(results))
    for n, ok, d in results:
        print(f"{'通過  ' if ok else '不通過'} {n}: {d}")
    return 0 if all(ok for _, ok, _ in results) else 1


def cmd_check(args) -> int:
    """書いている途中の単位を検査だけする（状態は変えない）。"""
    st = load_state()
    uid = args.unit or st.get("current")
    if not uid:
        raise Fail("単位を指定してください")
    errors = check_unit_spec(unit_by_id(uid), unit_files(uid), load_inventory())
    for e in errors:
        print("  - " + e.replace("\n", "\n    "))
    print("OK" if not errors else f"{len(errors)} 件")
    return 0 if not errors else 1


def cmd_drift(args) -> int:
    cfg = load_config()
    inv = load_inventory()
    changed = drift_files(source_root(cfg), inv)
    st = load_state()
    units = all_units(st)
    resolved = resolve_units(units, inv)
    touched = sorted({u["id"] for u in in_scope(units)
                      for f in changed["changed"] + changed["removed"] if f in resolved.get(u["id"], [])})
    for k, label in (("changed", "変わった"), ("added", "増えた"), ("removed", "消えた")):
        print(f"{label}: {len(changed[k])}")
        for f in changed[k][:30]:
            print(f"  {f}")
    print("作り直しが要る単位: " + (", ".join(touched) or "なし"))
    if not args.apply:
        return 0 if not any(changed.values()) else 1
    with Lock(LOCK_FILE):
        ingest(cfg)
        st = load_state()
        st["seq"] += 1
        goto(st, "units", f"ソースが変わった（{', '.join(touched) or '単位の変更なし'}）")
        save_state(st)
        commit("rspec: ソースの変化を取り込んだ")
    print("数え上げ直しました。単位の段から進めます（増えたファイルを単位に入れる。変わった単位は書き直しに回る）")
    return 0


def cmd_requeue(args) -> int:
    with Lock(LOCK_FILE):
        st = load_state()
        targets = [uid for uid, su in st["units"].items() if su.get("status") == "blocked"] if args.blocked else args.units
        for uid in targets:
            if uid not in st["units"] or st["units"][uid].get("status") == "out-of-scope":
                raise Fail(f"作り直せる単位ではありません: {uid}")
            st["units"][uid].update(status="pending", round=0, ng=[], ng_keys=[], sheet="")
        if not targets:
            print("作り直す単位はありません")
            return 0
        st["seq"] += 1
        if st["state"] in ("synthesize", "audit", "stuck", "done") or STATES[st["state"]].get("unit"):
            st["stuck"] = {}
            goto(st, *next_unit(st, f"作り直し: {', '.join(targets)}"))
        save_state(st)
        commit(f"rspec: 作り直しに回した ({', '.join(targets)})")
    print(f"作り直しに回しました: {', '.join(targets)}（今の段: {st['state']}）")
    return 0


# ---------------------------------------------------------------- 無人で回す（run）

RUN_PROMPT = (
    "あなたは reverse-spec（既存のソースコードから根拠つきの仕様書を起こす作業）の 1 段だけを進めます。\n"
    "1. `python3 .rspec/rspec.py step` を実行し、出た指示を読む。\n"
    "2. 指示どおりに作業する（ソースのフォルダは読むだけで、変えない）。\n"
    "3. `python3 .rspec/rspec.py step \"<出力>\"` で出力を渡す（第 1 語は指示の末尾にある語）。\n"
    "4. 「受け付けました」「ここで終了」と出たら、それ以上何もせずに終了する。\n"
    "   検査の指摘が出たら、指摘されたところだけを直して 3 をやり直す。\n"
    "AGENTS.md の決まりに従う。人に質問はしない（人の判断が要る段は、この実行には回ってこない）。"
)

# 既定のエージェント。CLI の引数は版で変わるので、合わないときは rspec.json の agents で上書きする。
# {prompt} は指示の本文、{prompt_file} はそれを書いたファイル、{source} はソースのフォルダに置き換わる。
AGENT_PRESETS = {
    "claude": ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits", "--add-dir", "{source}",
               "--allowedTools", "Bash(python3 .rspec/rspec.py:*)", "Read", "Grep", "Glob", "Edit", "Write"],
    "codex": ["codex", "exec", "--full-auto", "--skip-git-repo-check", "{prompt}"],
    "copilot": ["copilot", "-p", "{prompt}", "--allow-all-tools", "--add-dir", "{source}"],
    "kiro": ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", "{prompt}"],
    "gemini": ["gemini", "--yolo", "--include-directories", "{source}", "-p", "{prompt}"],
}


def agent_argv(name_or_cmd: str, cfg: dict, prompt: str, prompt_file: Path, src: Path) -> list:
    presets = dict(AGENT_PRESETS)
    presets.update(cfg.get("agents") or {})
    if name_or_cmd in presets:
        tmpl = presets[name_or_cmd]
        tmpl = shlex.split(tmpl) if isinstance(tmpl, str) else list(tmpl)
    else:
        tmpl = shlex.split(name_or_cmd)
    subs = {"{prompt}": prompt, "{prompt_file}": str(prompt_file), "{source}": str(src), "{root}": str(ROOT)}
    out = []
    for a in tmpl:
        for k, v in subs.items():
            a = a.replace(k, v)
        out.append(a)
    return out


def needs_human(st: dict, cfg: dict) -> bool:
    return bool(STATES[st["state"]].get("human")) and not (st["state"] == "confirm" and cfg.get("confirm") == "auto")


def run_once(argv: list, log: Path, timeout: int, env: dict) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as f:
        f.write(f"# {now()} {argv[0]}（指示は .rspec/logs/prompt.md）\n")
        f.flush()
        kwargs = {"start_new_session": True} if os.name != "nt" else {}
        try:
            proc = subprocess.Popen(argv, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=env, **kwargs)
        except FileNotFoundError:
            f.write(f"エージェントのコマンドが見つかりません: {argv[0]}\n")
            return 127
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            f.write(f"\n# {timeout} 秒で打ち切った\n")
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            return 124


def cmd_run(args) -> int:
    cfg = load_config()
    src = source_root(cfg)
    with Lock(RUN_LOCK_FILE, stale_sec=10 ** 9):
        stalls = 0
        for i in range(1, args.max_steps + 1):
            st = load_state()
            if STATES[st["state"]].get("terminal"):
                print(report_text(st))
                return 0
            if needs_human(st, cfg):
                print(header(st))
                print(render_action(st))
                print(f"---\n人の判断が要る段で止めました。`python3 {TOOL} step \"<答え>\"` で答えてから、run をもう一度動かしてください。")
                return 3
            token = uuid.uuid4().hex
            write(TOKEN_FILE, json.dumps({"token": token, "used": False}))
            prompt_file = p(LOG_DIR) / "prompt.md"
            write(prompt_file, RUN_PROMPT + "\n")
            argv = agent_argv(args.agent, cfg, RUN_PROMPT, prompt_file, src)
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            log = p(LOG_DIR) / f"{stamp}-{st['seq']:05d}-{st['state']}{('-' + st['current']) if st.get('current') else ''}.log"
            env = dict(os.environ, RSPEC_RUN_TOKEN=token, RSPEC_ROOT=str(ROOT))
            print(f"[{now()}] {i}: {st['state']} {st.get('current', '')} を {argv[0]} に渡します（ログ: {log.relative_to(ROOT)}）",
                  flush=True)
            if args.dry_run:
                print("  " + " ".join(shlex.quote(a) for a in argv))
                return 0
            code = run_once(argv, log, args.timeout, env)
            after = load_state()
            if after["seq"] > st["seq"]:
                stalls = 0
                print(f"  → {after['state']} {after.get('current', '')}（終了コード {code}）", flush=True)
                continue
            stalls += 1
            print(f"  進みませんでした（終了コード {code}、{stalls}/{args.retries} 回目）", flush=True)
            if stalls >= args.retries:
                print(f"同じ段で {stalls} 回続けて進まなかったので止めます。ログを確かめてください: {log.relative_to(ROOT)}")
                return 4
            time.sleep(min(args.backoff * (2 ** (stalls - 1)), 600))
        print(f"上限の {args.max_steps} 段に達したので止めます（`run` をもう一度動かせば続きから進みます）")
        return 5


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rspec.py", description="既存のソースから根拠つきの仕様書を起こすステートマシン")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("step", help="今の段の指示を出す / 出力を渡す")
    s.add_argument("output", nargs="?")
    s.set_defaults(func=cmd_step)
    s = sub.add_parser("status", help="進み具合")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)
    sub.add_parser("ingest", help="ソースを数え上げ直す").set_defaults(func=cmd_ingest)
    sub.add_parser("gates", help="最後の検査だけを動かす").set_defaults(func=cmd_gates)
    s = sub.add_parser("check", help="単位の仕様書を検査だけする")
    s.add_argument("unit", nargs="?")
    s.set_defaults(func=cmd_check)
    s = sub.add_parser("drift", help="ソースの変化を調べる")
    s.add_argument("--apply", action="store_true", help="数え上げ直し、変わった単位を作り直しに回す")
    s.set_defaults(func=cmd_drift)
    s = sub.add_parser("requeue", help="単位を作り直しに回す")
    s.add_argument("units", nargs="*")
    s.add_argument("--blocked", action="store_true")
    s.set_defaults(func=cmd_requeue)
    s = sub.add_parser("run", help="段ごとに新しいエージェントを起こして無人で回す")
    s.add_argument("--agent", default="claude", help=f"{', '.join(AGENT_PRESETS)} か、rspec.json の agents の名前か、コマンド")
    s.add_argument("--max-steps", type=int, default=1000)
    s.add_argument("--timeout", type=int, default=3600, help="1 段の上限（秒）")
    s.add_argument("--retries", type=int, default=3, help="同じ段で続けて進まなかったら止める回数")
    s.add_argument("--backoff", type=int, default=30, help="進まなかったときに待つ秒（回ごとに倍）")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_run)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Fail as e:
        print(f"rspec: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
