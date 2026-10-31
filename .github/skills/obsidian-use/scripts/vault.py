#!/usr/bin/env python3
"""Obsidian ボルトを知識 wiki として使うためのコマンド（obsidian-use）。

設計: docs/designs/obsidian-wiki-design.md

  python vault.py config                         設定（ボルトの場所）を表示する
  python vault.py init --vault PATH [--wiki-dir llm-wiki] [--vault-name NAME]
                                                 wiki の骨組みを作り、設定を書く
  python vault.py search "語" [--scope knowledge|project|env] [--limit 10] [--json]
                                                 Obsidian の検索で探す（Obsidian が動いていなければ素の文字列一致）
  python vault.py note --scope knowledge --title "題" --body-file F [--type concept] [--aliases a,b] ...
                                                 ノートを作る・追記する（ingest / save）
  python vault.py memo --scope project|env|knowledge --kind bug|tip|fact|decision --name slug \
                       --title "題" --detail "1 行" [--reason "1 行"] [--keywords a,b] [--session SID]
                                                 フックの指示（OBSIDIAN NOTE）で 1 件のメモを残す。同じ名前なら「起きた回」を足す
  python vault.py notes --scope project|env [--limit 5]
                                                 このプロジェクト・この環境のメモの一覧（新しい順）
  python vault.py log "やったこと"               操作ログ（meta/log.md）に 1 行足す
  python vault.py lint                           プロパティの欠けと、Obsidian が数えたリンク切れ・孤立ノートを出す

検索・バックリンク・タグ・未解決リンクの数え上げは Obsidian（公式 CLI）に任せ、このスクリプトは
索引もスコアも持たない。書き込みはファイルに直接行う（Obsidian が閉じていても書ける）。
標準ライブラリだけで動く。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

SKILL_NAME = "obsidian-use"
REGISTRY = "skill-registry.json"
AGENT_DIRS = (".claude", ".copilot", ".kiro", ".codex")
DEFAULT_WIKI_DIR = "llm-wiki"
SCOPES = {"knowledge": "knowledge", "project": "projects", "env": "envs"}
KNOWLEDGE_TYPES = ("concept", "term", "person", "organization", "product", "topic")
MEMO_KINDS = {  # 種別 → (reason の見出し, detail の見出し)
    "bug": ("原因", "回避"),
    "tip": ("理由", "やること"),
    "fact": ("根拠", "内容"),
    "decision": ("理由", "決めたこと"),
}
TITLE_MAX, LINE_MAX = 60, 200
CLI_TIMEOUT = 8

# 書いてはいけないもの（見つけたら書かない）。プロジェクト名・パス・ホスト名は書いてよい
SECRET_PATTERNS = [
    re.compile(r"glpat-[0-9A-Za-z_\-]{20,}"),
    re.compile(r"gh[pousr]_[0-9A-Za-z]{30,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{30,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S+"),
]


# --- 設定 ----------------------------------------------------------------------

def agent_home() -> Path:
    """scripts/ → obsidian-use/ → skills/ → エージェントの置き場所。無ければ標準の場所を探す。"""
    env = os.environ.get("OBSIDIAN_USE_AGENT_HOME")
    if env:
        return Path(os.path.expanduser(env))
    candidate = Path(__file__).resolve().parent.parent.parent.parent
    if candidate.name in AGENT_DIRS:
        return candidate
    home = Path.home()
    for d in AGENT_DIRS:
        if (home / d / REGISTRY).is_file():
            return home / d
    return home / ".claude"


def state_home() -> Path:
    """フックの控えを置く場所。"""
    env = os.environ.get("OBSIDIAN_USE_HOME")
    return Path(os.path.expanduser(env)) if env else agent_home() / ".obsidian-use"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_config() -> dict:
    """{"vault", "vault_name", "wiki_dir"}。未設定なら {}。"""
    cfg = _read_json(agent_home() / REGISTRY, {}).get("skill_configs", {}).get(SKILL_NAME) or {}
    if not cfg.get("vault"):
        return {}
    vault = Path(os.path.expanduser(cfg["vault"]))
    return {"vault": vault, "vault_name": cfg.get("vault_name") or vault.name,
            "wiki_dir": cfg.get("wiki_dir") or DEFAULT_WIKI_DIR}


def save_config(vault: str, vault_name: str, wiki_dir: str) -> Path:
    path = agent_home() / REGISTRY
    reg = _read_json(path, {})
    reg.setdefault("skill_configs", {})[SKILL_NAME] = {"vault": vault, "vault_name": vault_name, "wiki_dir": wiki_dir}
    _write_json(path, reg)
    return path


def config_or_exit() -> dict:
    cfg = load_config()
    if not cfg:
        print("[ERROR] ボルトが未設定です。python vault.py init --vault <ボルトのフォルダ> を実行してください",
              file=sys.stderr)
        sys.exit(1)
    return cfg


def wiki_root(cfg: dict) -> Path:
    return cfg["vault"] / cfg["wiki_dir"]


# --- このプロジェクト・この環境 --------------------------------------------------------

def _safe(name: str, limit: int = 60) -> str:
    name = re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]', "-", str(name)).strip(" .-")
    return re.sub(r"-{2,}", "-", name)[:limit] or "unknown"


def project_name(cwd: str = "") -> str:
    """git の origin のリポジトリ名。無ければ作業フォルダの名前。"""
    cwd = cwd or os.getcwd()
    p = Path(cwd)
    for d in (p, *p.parents):
        cfg = d / ".git" / "config"
        if cfg.is_file():
            try:
                text = cfg.read_text(encoding="utf-8", errors="replace")
            except OSError:
                break
            m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*\S*?([^/:\s]+?)(?:\.git)?\s*$', text, re.M | re.S)
            return _safe(m.group(1) if m else d.name)
        if (d / ".git").is_file():   # worktree・サブモジュール
            return _safe(d.name)
    return _safe(p.name)


def env_name() -> str:
    """ホスト名と OS（WSL は WSL と書く）。"""
    os_name = platform.system() or "unknown"
    if os_name == "Linux" and "microsoft" in platform.release().lower():
        os_name = "WSL"
    elif os_name == "Darwin":
        os_name = "macOS"
    host = socket.gethostname().split(".")[0] or "host"
    return _safe(f"{host}-{os_name}".lower())


def scope_dir(cfg: dict, scope: str, cwd: str = "") -> Path:
    base = wiki_root(cfg) / SCOPES[scope]
    if scope == "project":
        return base / project_name(cwd)
    if scope == "env":
        return base / env_name()
    return base


def rel(cfg: dict, path: Path) -> str:
    try:
        return path.relative_to(cfg["vault"]).as_posix()
    except ValueError:
        return path.as_posix()


# --- Obsidian CLI -------------------------------------------------------------

def _cli_env() -> dict:
    return {**os.environ, "NO_COLOR": "1"}


def obsidian(cfg: dict, *args: str, timeout: int = CLI_TIMEOUT) -> tuple[int, str]:
    """公式の obsidian CLI を、設定のボルトに向けて呼ぶ。使えなければ (127, 理由)。"""
    exe = os.environ.get("OBSIDIAN_CLI") or shutil.which("obsidian")
    if not exe:
        return 127, "obsidian コマンドが見つかりません"
    cmd = [exe, f"vault={cfg['vault_name']}", *args]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=str(cfg["vault"]), env=_cli_env())
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)
    out = p.stdout.decode("utf-8", "replace").strip()
    err = p.stderr.decode("utf-8", "replace").strip()
    # CLI は Obsidian が閉じていても 0 で終わり、本文にエラーを書くことがある
    if p.returncode != 0 or re.match(r"(?i)^(error|unable to connect|obsidian is not running)", out):
        return p.returncode or 1, (err or out)[:300]
    return 0, out


def _paths_from_cli(out: str) -> list[str]:
    """search の出力（JSON か 1 行 1 パス）からパスを取り出す。"""
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    paths: list[str] = []
    if isinstance(data, list):
        for it in data:
            p = it if isinstance(it, str) else (it.get("path") or it.get("file")) if isinstance(it, dict) else None
            if p:
                paths.append(str(p))
    else:
        for line in out.splitlines():
            line = line.strip().lstrip("-* ").strip()
            m = re.match(r"(.+?\.md)\b", line)
            if m:
                paths.append(m.group(1))
    seen, uniq = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _fallback_search(cfg: dict, root: Path, query: str, limit: int) -> list[str]:
    """Obsidian が使えないときだけの、素の文字列一致（語がすべて含まれるノート。順位は付けない）。"""
    words = [w.lower() for w in query.split() if w]
    hits = []
    for p in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in p.relative_to(cfg["vault"]).parts):
            continue
        try:
            text = (p.stem + "\n" + p.read_text(encoding="utf-8", errors="replace")).lower()
        except OSError:
            continue
        if all(w in text for w in words):
            hits.append(rel(cfg, p))
        if len(hits) >= limit:
            break
    return hits


def search(cfg: dict, query: str, scope: str = "", limit: int = 10) -> dict:
    root = scope_dir(cfg, scope) if scope else wiki_root(cfg)
    folder = rel(cfg, root)
    code, out = obsidian(cfg, "search", f"query={query}", f"path={folder}", f"limit={limit}", "format=json")
    if code != 0 and not out.startswith(("obsidian コマンドが見つかりません", "[Errno")):
        # 古い CLI は path= / format= を受けないことがある。素の search にして、フォルダで絞る
        code, out = obsidian(cfg, "search", f"query={query}", f"limit={max(limit * 5, 50)}")
    if code == 0:
        paths = [p for p in _paths_from_cli(out) if p == folder or p.startswith(folder + "/")]
        return {"via": "obsidian", "query": query, "folder": folder, "paths": paths[:limit]}
    return {"via": "fallback", "reason": out, "query": query, "folder": folder,
            "paths": _fallback_search(cfg, root, query, limit) if root.is_dir() else []}


# --- ノートの読み書き ------------------------------------------------------------

def today() -> str:
    return time.strftime("%Y-%m-%d")


def _yaml_scalar(v) -> str:
    s = str(v)
    if s == "" or re.search(r'[:#\[\]{},&*!|>\'"%@`]|^\s|\s$|^[-?]', s):
        return json.dumps(s, ensure_ascii=False)
    return s


def _yaml_list(items) -> str:
    return "[" + ", ".join(_yaml_scalar(i) for i in items) + "]"


def frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if v is None or v == "" or v == []:
            continue
        lines.append(f"{k}: {_yaml_list(v) if isinstance(v, (list, tuple)) else _yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """このスクリプトが書く平らな YAML だけを読む（入れ子は扱わない）。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta: dict = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key] = [_unquote(x) for x in _split_list(val[1:-1])]
        else:
            meta[key] = _unquote(val)
    return meta, text[end + 4:].lstrip("\n")


def _split_list(s: str) -> list[str]:
    items, buf, quote = [], "", ""
    for ch in s:
        if quote:
            buf += ch
            if ch == quote and not buf.endswith("\\" + quote):
                quote = ""
        elif ch in "\"'":
            quote, buf = ch, buf + ch
        elif ch == ",":
            items.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(buf.strip())
    return items


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        try:
            return json.loads(s)
        except ValueError:
            return s[1:-1]
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1]
    return s


def _csv(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _tags(s: str | None) -> list[str]:
    """Obsidian のタグに使えない文字（空白・記号）をハイフンにする。"""
    out = []
    for t in _csv(s):
        t = re.sub(r"[^\w/\-]+", "-", t.lstrip("#")).strip("-")
        if t and not t.isdigit() and t not in out:
            out.append(t)
    return out


def secret_in(text: str) -> bool:
    return any(p.search(text) for p in SECRET_PATTERNS)


def append_log(cfg: dict, what: str) -> None:
    path = wiki_root(cfg) / "meta" / "log.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# 操作ログ\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} {' '.join(what.split())}\n")


def write_note(cfg: dict, scope: str, title: str, body: str, *, note_type: str = "", aliases=(), tags=(),
               sources=(), summary: str = "", cwd: str = "") -> tuple[Path, bool]:
    """ノートを作る。同じ題のノートがあれば、本文を日付つきの節として末尾に足す。(パス, 新規か)"""
    folder = scope_dir(cfg, scope, cwd)
    path = folder / f"{_safe(title, 100)}.md"
    if path.exists():
        meta, old = parse_frontmatter(path.read_text(encoding="utf-8"))
        meta["updated"] = today()
        for key, add in (("aliases", aliases), ("tags", tags), ("sources", sources)):
            merged = list(meta.get(key) or []) if isinstance(meta.get(key), list) else _csv(meta.get(key))
            merged += [a for a in add if a not in merged]
            meta[key] = merged
        if summary:
            meta["summary"] = summary
        path.write_text(frontmatter(meta) + old.rstrip() + f"\n\n## 追記 {today()}\n\n{body.strip()}\n",
                        encoding="utf-8")
        return path, False
    meta = {"title": title, "type": note_type or ("topic" if scope == "knowledge" else "memo"), "scope": scope}
    if scope == "project":
        meta["project"] = project_name(cwd)
    elif scope == "env":
        meta["env"] = env_name()
    meta.update({"aliases": list(aliases), "tags": list(tags), "sources": list(sources),
                 "created": today(), "updated": today(), "summary": summary})
    folder.mkdir(parents=True, exist_ok=True)
    text = body.strip()
    if not text.startswith("# "):
        text = f"# {title}\n\n{text}"
    path.write_text(frontmatter(meta) + "\n" + text + "\n", encoding="utf-8")
    return path, True


def find_memo(folder: Path, name: str) -> Path | None:
    """同じ名前のメモ（プロパティ memo が name）を探す。重複の判定だけに使う。"""
    if not folder.is_dir():
        return None
    for p in folder.glob("*.md"):
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            continue
        if re.search(rf"^memo:\s*\"?{re.escape(name)}\"?\s*$", head, re.M):
            return p
    return None


def list_notes(folder: Path, limit: int = 5) -> list[dict]:
    """フォルダのノートを新しい順に（題と要約・回避の 1 行）。"""
    if not folder.is_dir():
        return []
    items = []
    for p in folder.glob("*.md"):
        try:
            meta, body = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            items.append((p.stat().st_mtime, p, meta, body))
        except OSError:
            continue
    items.sort(key=lambda x: -x[0])
    out = []
    for _, p, meta, body in items[:limit]:
        line = meta.get("summary") or ""
        if not line:
            m = re.search(r"^- (?:回避|やること|内容|決めたこと): (.+)$", body, re.M)
            line = m.group(1) if m else ""
        out.append({"title": meta.get("title") or p.stem, "path": p, "kind": meta.get("kind", ""),
                    "name": meta.get("memo", ""), "line": line})
    return out


def session_file(session: str) -> Path:
    return state_home() / "sessions" / f"{re.sub(r'[^0-9A-Za-z_.-]', '_', session)[:80]}.json"


def _session_note(session: str, wrote: str) -> None:
    """フックの控えに、このセッションで書いた件数を数える（上限の判定に使う）。"""
    if not session:
        return
    path = session_file(session)
    sess = _read_json(path, None)
    if isinstance(sess, dict):
        sess["notes"] = sess.get("notes", 0) + 1
        sess.setdefault("written", []).append(wrote)
        _write_json(path, sess)


def memo(cfg: dict, args) -> tuple[int, str]:
    kind = args.kind
    errs = []
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,59}", args.name or ""):
        errs.append("--name は英小文字・数字・ハイフンで 60 字以内")
    if not args.title or len(args.title) > TITLE_MAX or "\n" in args.title:
        errs.append(f"--title は 1 行 {TITLE_MAX} 字以内")
    for flag, val, need in (("--detail", args.detail, True), ("--reason", args.reason, False)):
        if need and not val:
            errs.append(f"{flag} は必須")
        if val and (len(val) > LINE_MAX or "\n" in val):
            errs.append(f"{flag} は 1 行 {LINE_MAX} 字以内")
    if errs:
        return 1, "NG " + " / ".join(errs)
    keywords = _tags(args.keywords)
    if secret_in(" ".join([args.title, args.detail, args.reason or "", args.keywords or ""])):
        return 2, "NG 秘密情報（トークン・鍵・パスワード）を含むので書きません。伏せてから書き直してください"
    session = getattr(args, "session", "") or ""
    sess = _read_json(session_file(session), {}) if session else {}
    when = today() + (f"（{sess['env']}）" if sess.get("env") else "")
    folder = scope_dir(cfg, args.scope, getattr(args, "cwd", "") or sess.get("cwd", ""))
    reason_label, detail_label = MEMO_KINDS[kind]
    existing = find_memo(folder, args.name)
    if existing:
        text = existing.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        meta["updated"] = today()
        old_kw = meta.get("tags") if isinstance(meta.get("tags"), list) else []
        meta["tags"] = old_kw + [k for k in keywords if k not in old_kw]
        add = []
        if args.detail not in body:
            add.append(f"- {detail_label}（{today()}）: {args.detail}")
        add.append(f"- 起きた回: {when}")
        existing.write_text(frontmatter(meta) + "\n" + body.rstrip() + "\n" + "\n".join(add) + "\n", encoding="utf-8")
        append_log(cfg, f"memo {rel(cfg, existing)}（追記）")
        _session_note(session, rel(cfg, existing))
        return 0, f"OK {rel(cfg, existing)} に追記しました"
    lines = [f"# {args.title}", ""]
    if args.reason:
        lines.append(f"- {reason_label}: {args.reason}")
    lines.append(f"- {detail_label}: {args.detail}")
    if args.tools:
        lines.append(f"- 関連ツール: {', '.join(_csv(args.tools))}")
    lines.append(f"- 起きた回: {when}")
    meta = {"title": args.title, "type": "memo", "kind": kind, "scope": args.scope, "memo": args.name}
    if args.scope == "project":
        meta["project"] = folder.name
    elif args.scope == "env":
        meta["env"] = folder.name
    meta.update({"tags": keywords, "created": today(), "updated": today()})
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_safe(args.title, 100)}.md"
    if path.exists():
        path = folder / f"{_safe(args.title, 90)}-{args.name}.md"
    path.write_text(frontmatter(meta) + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    append_log(cfg, f"memo {rel(cfg, path)}（新規）")
    _session_note(session, rel(cfg, path))
    return 0, f"OK {rel(cfg, path)} に書きました"


# --- 骨組み ----------------------------------------------------------------------

def index_base(wiki_dir: str) -> str:
    """Bases の一覧。並べ替え・絞り込みは Obsidian がする（index.md を手で更新しない）。"""
    def view(name: str, folder: str, group: str, order: list[str], limit: int = 0) -> str:
        lines = [f"  - type: table", f"    name: {name}"]
        if folder:
            lines += ["    filters:", "      and:", f"        - 'file.inFolder(\"{wiki_dir}/{folder}\")'"]
        if group:
            lines += ["    groupBy:", f"      property: {group}", "      direction: ASC"]
        lines += ["    order:"] + [f"      - {o}" for o in order]
        lines += ["    sort:", "      - property: file.mtime", "        direction: DESC"]
        if limit:
            lines.append(f"    limit: {limit}")
        return "\n".join(lines)

    return "\n".join([
        "filters:", "  and:", f"    - 'file.inFolder(\"{wiki_dir}\")'", "    - 'file.ext == \"md\"'",
        "    - 'file.folder != \"" + wiki_dir + "/meta\"'",
        "views:",
        view("最近", "", "", ["file.name", "scope", "type", "updated"], 30),
        view("知識", "knowledge", "type", ["file.name", "summary", "updated"]),
        view("プロジェクト", "projects", "project", ["file.name", "kind", "updated"]),
        view("環境", "envs", "env", ["file.name", "kind", "updated"]),
    ]) + "\n"


SCHEMA = """# このボルトの wiki の決まり

- `knowledge/`: 世界・ドメインの知識（概念・用語・人物・組織・製品と、それらを横断するまとめ）。`type` で分ける
- `projects/<プロジェクト>/`: そのプロジェクトだけで通じるメモ（ビルドの手順、置き場所、決めたこと、詰まりと回避）
- `envs/<ホスト-OS>/`: その PC・OS・ツールの版だけで起きること
- `meta/log.md`: 操作ログ（追記だけ）
- `index.base`: 一覧（Obsidian の Bases。並べ替え・絞り込みは Obsidian がする）

ノートの題はファイル名と同じにし、関係するノートは `[[題]]` でつなぐ。別名・訳語・略称は `aliases` に入れる
（Obsidian の検索とリンクの補完がそれで当たる）。検索の索引は持たない。探すときは Obsidian の検索を使う。
"""


def init(vault: str, wiki_dir: str, vault_name: str) -> tuple[Path, Path]:
    vpath = Path(os.path.expanduser(vault)).resolve()
    root = vpath / wiki_dir
    for d in ("knowledge", "projects", "envs", "meta"):
        (root / d).mkdir(parents=True, exist_ok=True)
    for name, text in (("SCHEMA.md", SCHEMA), ("index.base", index_base(wiki_dir)),
                       ("meta/log.md", "# 操作ログ\n\n")):
        p = root / name
        if not p.exists():
            p.write_text(text, encoding="utf-8")
    reg = save_config(str(vpath), vault_name or vpath.name, wiki_dir)
    return root, reg


# --- lint --------------------------------------------------------------------

def lint(cfg: dict) -> list[str]:
    root = wiki_root(cfg)
    problems = []
    for p in sorted(root.rglob("*.md")):
        relp = p.relative_to(root)
        if relp.parts[0] == "meta" or p.name == "SCHEMA.md":
            continue
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        missing = [k for k in ("title", "type", "updated") if not meta.get(k)]
        if missing:
            problems.append(f"プロパティが欠けている: {rel(cfg, p)}（{', '.join(missing)}）")
        if relp.parts[0] == "knowledge" and meta.get("type") not in (*KNOWLEDGE_TYPES, "memo", None, ""):
            problems.append(f"type が決まりにない: {rel(cfg, p)}（{meta.get('type')}）")
        if len(body.strip()) < 20:
            problems.append(f"中身がほとんど無い: {rel(cfg, p)}")
    folder = rel(cfg, root)
    for cmd, label in (("unresolved", "リンク切れ"), ("orphans", "孤立ノート")):
        code, out = obsidian(cfg, cmd)
        if code != 0:
            problems.append(f"（リンク切れ・孤立ノートは Obsidian が使えないので数えていません: {out[:80]}）")
            break
        for line in (ln.strip() for ln in out.splitlines()):
            # 孤立ノートは wiki の中だけ。リンク切れはリンク先の名前で出るのでそのまま出す
            if line and (cmd == "unresolved" or folder in line):
                problems.append(f"{label}: {line}")
    return problems


# --- main --------------------------------------------------------------------

def _read_body(args) -> str:
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text(encoding="utf-8")
    if getattr(args, "body", None):
        return args.body
    return sys.stdin.read() if not sys.stdin.isatty() else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Obsidian ボルトを知識 wiki として使う")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config", help="設定を表示する")
    p = sub.add_parser("init", help="wiki の骨組みを作り、設定を書く")
    p.add_argument("--vault", required=True)
    p.add_argument("--wiki-dir", default=DEFAULT_WIKI_DIR)
    p.add_argument("--vault-name", default="")
    p = sub.add_parser("search", help="Obsidian の検索で探す")
    p.add_argument("query")
    p.add_argument("--scope", choices=sorted(SCOPES), default="")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("note", help="ノートを作る・追記する")
    p.add_argument("--scope", choices=sorted(SCOPES), default="knowledge")
    p.add_argument("--title", required=True)
    p.add_argument("--type", default="", choices=["", *KNOWLEDGE_TYPES, "memo"])
    p.add_argument("--body", default="")
    p.add_argument("--body-file", default="")
    p.add_argument("--aliases", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--sources", default="")
    p.add_argument("--summary", default="")
    p.add_argument("--cwd", default="")
    p = sub.add_parser("memo", help="フックの指示で 1 件のメモを残す")
    p.add_argument("--scope", choices=sorted(SCOPES), required=True)
    p.add_argument("--kind", choices=sorted(MEMO_KINDS), required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--detail", required=True)
    p.add_argument("--reason", default="")
    p.add_argument("--keywords", default="")
    p.add_argument("--tools", default="")
    p.add_argument("--session", default="")
    p.add_argument("--cwd", default="")
    p = sub.add_parser("notes", help="このプロジェクト・この環境のメモの一覧")
    p.add_argument("--scope", choices=sorted(SCOPES), default="project")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--cwd", default="")
    p = sub.add_parser("log", help="操作ログに 1 行足す")
    p.add_argument("text")
    sub.add_parser("lint", help="wiki を点検する")
    a = ap.parse_args(argv)

    if a.cmd == "init":
        root, reg = init(a.vault, a.wiki_dir, a.vault_name)
        print(f"OK wiki: {root}\n   設定: {reg}")
        return 0
    if a.cmd == "config":
        cfg = config_or_exit()
        code, out = obsidian(cfg, "version", timeout=5)
        print(f"vault      : {cfg['vault']}\nvault_name : {cfg['vault_name']}\nwiki       : {wiki_root(cfg)}"
              f"\nproject    : {project_name()}\nenv        : {env_name()}"
              f"\nobsidian   : {out.splitlines()[0] if code == 0 and out else '使えない（' + out[:80] + '）'}")
        return 0
    cfg = config_or_exit()
    if a.cmd == "search":
        res = search(cfg, a.query, a.scope, a.limit)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            if res["via"] == "fallback":
                print(f"（Obsidian が使えないので文字列一致で探しました: {res['reason'][:80]}）")
            print("\n".join(f"- {p}" for p in res["paths"]) or "- 見つかりませんでした")
        return 0
    if a.cmd == "note":
        body = _read_body(a)
        if not body.strip():
            print("NG 本文が空です（--body か --body-file か標準入力）", file=sys.stderr)
            return 1
        if secret_in(body):
            print("NG 秘密情報（トークン・鍵・パスワード）を含むので書きません", file=sys.stderr)
            return 2
        path, new = write_note(cfg, a.scope, a.title, body, note_type=a.type, aliases=_csv(a.aliases),
                               tags=_tags(a.tags), sources=_csv(a.sources), summary=a.summary, cwd=a.cwd)
        append_log(cfg, f"note {rel(cfg, path)}（{'新規' if new else '追記'}）")
        print(f"OK {rel(cfg, path)} を{'作りました' if new else '追記しました'}")
        return 0
    if a.cmd == "memo":
        code, msg = memo(cfg, a)
        print(msg)
        return code
    if a.cmd == "notes":
        items = list_notes(scope_dir(cfg, a.scope, a.cwd), a.limit)
        print("\n".join(f"- [[{it['title']}]]" + (f" — {it['line']}" if it["line"] else "") for it in items)
              or "- まだありません")
        return 0
    if a.cmd == "log":
        append_log(cfg, a.text)
        print("OK")
        return 0
    if a.cmd == "lint":
        problems = lint(cfg)
        append_log(cfg, f"lint {len(problems)} 件")
        print("\n".join(f"- {x}" for x in problems) or "- 問題はありません")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
