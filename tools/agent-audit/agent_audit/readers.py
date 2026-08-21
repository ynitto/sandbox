"""CLI ネイティブセッションストアのリーダ — format ごとに 1 実装(仕様書 §3・C7)。

どこに・どの形式であるかは `agents/<name>.json` の `session_log` ブロックが宣言する
(契約: schemas/agent-cli.schema.json)。ここは format の閉じた enum だけを実装し、
新 CLI が既存 format なら JSON 追記だけで収集できる。本文のノイズ除去(clean)も同じ
`session_log` に additive に載る宣言で、実装は `cleaning.py` に 1 つ(仕様書 §3)。

セッションの正規形(このモジュールの戻り値):
  {"native_id", "store", "cwd", "created_at", "updated_at",  # epoch 秒
   "model", "log_version", "turns", "tokens_in", "tokens_out", "usage_measured",
   "messages": [(role, text), ...]}   # want_messages=True のときだけ
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3

from . import cleaning
from .util import elog

FORMATS = ("jsonl-dir", "kiro-sqlite", "opencode-sqlite")


def read_sessions(session_log: dict, *, want_messages: bool = False,
                   limit: "int | None" = None) -> "list[dict]":
    """limit を指定すると、更新が新しい順に最大 limit セッションだけ読む
    （doctor の棚卸しなど、全件走査が高くつく場面向けの間引き。通常の collect は
    limit なし = 全件・冪等）。"""
    session_log = session_log or {}
    fmt = session_log.get("format")
    paths = [os.path.expanduser(p) for p in session_log.get("paths") or []]
    clean = session_log.get("clean") if isinstance(session_log.get("clean"), dict) else None
    if fmt == "jsonl-dir":
        out = []
        for p in paths:
            out.extend(_read_jsonl_dir(p, want_messages=want_messages, clean=clean, limit=limit))
        return _cap(out, limit)
    if fmt == "kiro-sqlite":
        out = []
        for p in paths:
            for db in _dbs(p):
                out.extend(_read_kiro_sqlite(db, want_messages=want_messages, clean=clean))
        return _cap(out, limit)
    if fmt == "opencode-sqlite":
        out = []
        for p in paths:
            for db in _dbs(p):
                out.extend(_read_opencode_sqlite(db, want_messages=want_messages,
                                                 clean=clean, limit=limit))
        return _cap(out, limit)
    return []          # 未知 format は「未収集」— 呼び出し側（doctor / collect）が明示する


def _dbs(pattern: str) -> "list[str]":
    """パスをグロブとして解く（当たらなければ実在するときだけそのパス自身）。"""
    return sorted(glob.glob(pattern)) or ([pattern] if os.path.exists(pattern) else [])


def _cap(sessions: "list[dict]", limit: "int | None") -> "list[dict]":
    if limit is None or len(sessions) <= limit:
        return sessions
    return sorted(sessions, key=lambda s: s.get("updated_at") or 0.0, reverse=True)[:limit]


def _make_warn(path: str, sink: "list[str] | None" = None):
    """1 セッション内で同じ警告は 1 回だけ出す（同じノイズが大量行に散っていても
    ログを埋めない）。sink を渡すと同じ文言をそこへも集める（doctor の棚卸し用）。"""
    seen: "set[str]" = set()

    def warn(msg: str) -> None:
        if msg not in seen:
            seen.add(msg)
            elog(f"clean: {os.path.basename(path)}: {msg}")
            if sink is not None:
                sink.append(msg)
    return warn


# -- jsonl-dir（claude / codex 系: 1 セッション = 1 *.jsonl） -------------------

def _read_jsonl_dir(root: str, *, want_messages: bool, clean: "dict | None" = None,
                     limit: "int | None" = None) -> "list[dict]":
    sessions = []
    pattern = os.path.join(glob.escape(root), "**", "*.jsonl")
    paths = sorted(glob.glob(pattern, recursive=True))
    if limit is not None and len(paths) > limit:
        # 全件は解析せずに済ませる粗い間引き: mtime が新しい順に limit 件だけ解析する
        # （正確な updated_at はログ内容依存だが、doctor の棚卸しは近似で十分）。
        paths = sorted(paths, key=lambda p: _safe_mtime(p), reverse=True)[:limit]
    for path in paths:
        s = _parse_jsonl_session(path, want_messages=want_messages, clean=clean)
        if s:
            sessions.append(s)
    return sessions


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _parse_jsonl_session(path: str, *, want_messages: bool,
                          clean: "dict | None" = None) -> "dict | None":
    objs: "list[dict]" = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    objs.append(obj)
    except OSError:
        return None

    warnings: "list[str]" = []
    warn = _make_warn(path, sink=warnings)
    version = cleaning.probe_version(objs, (clean or {}).get("version_key")) if clean else ""
    rules, _when = cleaning.select_rules(clean, version) if clean else ([], "")

    first_ts = last_ts = None
    cwd = model = sid = None
    sum_in = sum_out = 0
    usage_by_message: "dict[str, tuple[int, int]]" = {}
    last_total = None
    turns = 0
    messages: "list[tuple[str, str]]" = []
    for obj in objs:
        if rules and cleaning.should_drop_line(obj, rules, warn=warn):
            continue
        ts = _ts_of(obj)
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        cwd = cwd or _str_of(obj, "cwd")
        sid = sid or _str_of(obj, "sessionId") or _str_of(obj, "session_id") \
            or _str_of(obj, "conversation_id")
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else None
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else None
        for m in (msg, payload):
            if m and not model and isinstance(m.get("model"), str):
                model = m["model"]
        if not model and isinstance(obj.get("model"), str):
            model = obj["model"]
        # 使用量: メッセージ単位の usage は加算（API 呼び出しごとに課金される）、
        # 累計形（total_token_usage）は最後の値を採る。両方あれば累計形が勝つ。
        usage = None
        for container in (msg, payload, obj):
            if container and isinstance(container.get("usage"), dict):
                usage = container["usage"]
                break
        if usage is not None:
            i, o = _usage_of(usage)
            # Claude は 1 API 応答の thinking / text を別行にし、同じ message.id と
            # usage を各行へ再掲する。API 呼び出しを表す id がある行は最後の 1 件だけ数える。
            usage_id = None
            for container in (msg, payload, obj):
                if container and isinstance(container.get("id"), str):
                    usage_id = container["id"]
                    break
            if usage_id:
                usage_by_message[usage_id] = (i, o)
            else:
                sum_in += i
                sum_out += o
        total = _find_total_usage(obj)
        if total is not None:
            last_total = total
        role_text = _message_of(obj)
        if role_text:
            role, text = role_text
            if rules:
                text = cleaning.clean_message_text(text, rules, warn=warn)
            if text:
                turns += 1
                if want_messages:
                    messages.append((role, text))
    if first_ts is None and turns == 0:
        return None
    if last_total is None:
        sum_in += sum(i for i, _ in usage_by_message.values())
        sum_out += sum(o for _, o in usage_by_message.values())
    tokens_in, tokens_out = (last_total if last_total is not None else (sum_in, sum_out))
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return {
        "native_id": sid or os.path.splitext(os.path.basename(path))[0],
        "store": path,
        "cwd": cwd or "",
        "created_at": first_ts or mtime,
        "updated_at": last_ts or mtime,
        "model": model or "",
        "log_version": version,
        "turns": turns,
        "tokens_in": tokens_in or None,
        "tokens_out": tokens_out or None,
        "usage_measured": bool(tokens_in or tokens_out),
        "messages": messages,
        "_clean_warnings": warnings,
    }


def _ts_of(obj: dict) -> "float | None":
    from .util import parse_iso
    for key in ("timestamp", "ts", "created_at", "time"):
        v = obj.get(key)
        got = parse_iso(v)
        if got is not None:
            return got
    return None


def _str_of(obj: dict, key: str) -> "str | None":
    v = obj.get(key)
    return v if isinstance(v, str) and v else None


def _usage_of(u: dict) -> "tuple[int, int]":
    def num(*keys):
        total = 0
        for k in keys:
            v = u.get(k)
            if isinstance(v, (int, float)):
                total += int(v)
        return total
    return (num("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                "cached_input_tokens"),
            num("output_tokens"))


def _find_total_usage(obj: dict, depth: int = 0) -> "tuple[int, int] | None":
    """累計形 total_token_usage（codex の token_count イベント等）を浅く探す。"""
    if depth > 2 or not isinstance(obj, dict):
        return None
    u = obj.get("total_token_usage")
    if isinstance(u, dict):
        return _usage_of(u)
    for v in obj.values():
        if isinstance(v, dict):
            got = _find_total_usage(v, depth + 1)
            if got is not None:
                return got
    return None


def _message_of(obj: dict) -> "tuple[str, str] | None":
    """1 行から (role, text) を取り出す。claude 形（message.role/content）と
    codex 形（payload.role/content）の両方を読む。role が user/assistant 以外は無視。"""
    for container in (obj.get("message"), obj.get("payload"), obj):
        if not isinstance(container, dict):
            continue
        role = container.get("role")
        if role not in ("user", "assistant", "human"):
            continue
        content = container.get("content") or container.get("text") or ""
        if isinstance(content, list):
            text = "\n".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and isinstance(b.get("text"), str))
        else:
            text = str(content)
        if not text.strip():
            return None
        return ("User" if role in ("user", "human") else "Assistant", text)
    return None


def _epoch_sec(value) -> float:
    """ストアの時刻を epoch 秒へ。ms（13 桁）でも ISO 文字列でも受ける。"""
    from .util import parse_iso
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 1000.0 if value > 4102444800 else float(value)   # ms なら秒へ
    return parse_iso(value) or 0.0


# -- kiro-sqlite（~/.kiro/store.db。tools/kiro-log-exporter の手順を移植） ------

def _read_kiro_sqlite(db_path: str, *, want_messages: bool,
                       clean: "dict | None" = None) -> "list[dict]":
    if not os.path.exists(db_path):
        return []
    sessions = []
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0].lower() for r in cur.fetchall()}
            table = next((t for t in ("sessions", "chat_sessions", "conversations")
                          if t in tables), None)
            if table is None:
                return []
            cur.execute(f"SELECT * FROM [{table}]")  # noqa: S608 テーブル名は上の閉じた候補のみ
            cols = [d[0].lower() for d in cur.description]
            for row in cur.fetchall():
                data = dict(zip(cols, row))
                s = _parse_kiro_row(data, db_path, want_messages=want_messages, clean=clean)
                if s:
                    sessions.append(s)
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return []
    return sessions


def _parse_kiro_row(data: dict, db_path: str, *, want_messages: bool,
                     clean: "dict | None" = None) -> "dict | None":
    sid = str(data.get("id") or "").strip()
    if not sid:
        return None
    raw = None
    for key in ("messages", "conversation", "content", "history", "data"):
        if data.get(key):
            raw = data[key]
            break
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None

    warnings: "list[str]" = []
    warn = _make_warn(f"{db_path}::{sid}", sink=warnings)
    version = cleaning.probe_version([data], (clean or {}).get("version_key")) if clean else ""
    rules, _when = cleaning.select_rules(clean, version) if clean else ([], "")

    messages: "list[tuple[str, str]]" = []
    turns = 0
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            if rules and cleaning.should_drop_line(item, rules, warn=warn):
                continue
            got = _message_of(item)
            if not got:
                continue
            role, text = got
            if rules:
                text = cleaning.clean_message_text(text, rules, warn=warn)
            if text:
                turns += 1
                if want_messages:
                    messages.append((role, text))

    def _sec(v):
        return _epoch_sec(v)

    return {
        "native_id": sid,
        "store": db_path,
        "cwd": str(data.get("directory") or data.get("project_path")
                   or data.get("workspace") or ""),
        "created_at": _sec(data.get("created_at", 0)),
        "updated_at": _sec(data.get("updated_at") or data.get("created_at", 0)),
        "model": "",
        "log_version": version,
        "turns": turns,
        "tokens_in": None,
        "tokens_out": None,
        "usage_measured": False,
        "messages": messages,
        "_clean_warnings": warnings,
    }


# -- opencode-sqlite（~/.local/share/opencode/opencode.db） ---------------------
#
# kiro-sqlite と別 format にしたのは、行の形が違うから。kiro は 1 行に会話配列が丸ごと
# 入る（列を舐めれば読める）が、opencode は session / message / part の 3 表に正規化されて
# いて、本文は part 行の JSON、役割は親の message 行の JSON にある。同じパーサでは読めない。
#
# 実測トークンは session 行の列（tokens_input / tokens_output / tokens_cache_*）にあり、
# opencode 自身が合算したものなので、こちらで part を足し直さない。

_OPENCODE_TABLES = ("session", "message", "part")


def _connect_ro(db_path: str) -> "sqlite3.Connection | None":
    """読み取り専用で開く（収集が走っている裏で CLI が書いていても壊さない）。

    ro で開けない環境（uri 非対応・WAL の -shm を読めない等）では通常接続へ落とす。
    ここで諦めると「未収集」ではなく「黙って 0 件」になるので、落とす方を選ぶ。
    """
    for opener in (lambda: sqlite3.connect(f"file:{db_path}?mode=ro", uri=True),
                   lambda: sqlite3.connect(db_path)):
        try:
            return opener()
        except (sqlite3.Error, OSError):
            continue
    return None


def _read_opencode_sqlite(db_path: str, *, want_messages: bool, clean: "dict | None" = None,
                           limit: "int | None" = None) -> "list[dict]":
    if not os.path.exists(db_path):
        return []
    conn = _connect_ro(db_path)
    if conn is None:
        return []
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0].lower() for r in cur.fetchall()}
        if not set(_OPENCODE_TABLES) <= tables:
            return []           # 想定と違うストア（版差）は「読めない」＝0 件で返す
        cur.execute("PRAGMA table_info(session)")
        cols = {str(r[1]).lower() for r in cur.fetchall()}
        # 新しい順に間引けるのは doctor の棚卸し用（collect は limit なし＝全件・冪等）。
        order = " ORDER BY time_updated DESC" if "time_updated" in cols else ""
        cap = f" LIMIT {int(limit)}" if limit and limit > 0 else ""
        cur.execute(f"SELECT * FROM session{order}{cap}")  # noqa: S608 定数のみ
        names = [d[0].lower() for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        sessions = []
        for row in rows:
            s = _parse_opencode_session(conn, row, db_path,
                                        want_messages=want_messages, clean=clean)
            if s:
                sessions.append(s)
        return sessions
    except (sqlite3.Error, OSError):
        return []
    finally:
        conn.close()


def _parse_opencode_session(conn, row: dict, db_path: str, *, want_messages: bool,
                             clean: "dict | None" = None) -> "dict | None":
    sid = str(row.get("id") or "").strip()
    if not sid:
        return None

    warnings: "list[str]" = []
    warn = _make_warn(f"{db_path}::{sid}", sink=warnings)
    # バージョンは session 行の列（version="1.18.14"）から採る。jsonl-dir がログ行から
    # 採るのと同じ役割で、clean のルールセット選択に使う。
    version = cleaning.probe_version([row], (clean or {}).get("version_key")) if clean else ""
    rules, _when = cleaning.select_rules(clean, version) if clean else ([], "")

    turns = 0
    messages: "list[tuple[str, str]]" = []
    for role, text in _opencode_messages(conn, sid, rules=rules, warn=warn):
        turns += 1
        if want_messages:
            messages.append((role, text))

    tokens_in, tokens_out = _opencode_tokens(row)
    return {
        "native_id": sid,
        "store": db_path,
        "cwd": str(row.get("directory") or ""),
        "created_at": _epoch_sec(row.get("time_created")),
        "updated_at": _epoch_sec(row.get("time_updated") or row.get("time_created")),
        "model": _opencode_model(row.get("model")),
        "log_version": version,
        "turns": turns,
        "tokens_in": tokens_in or None,
        "tokens_out": tokens_out or None,
        "usage_measured": bool(tokens_in or tokens_out),
        "messages": messages,
        "_clean_warnings": warnings,
    }


def _opencode_messages(conn, session_id: str, *, rules, warn) -> "list[tuple[str, str]]":
    """part 行（本文）を親の message 行（役割）と突き合わせて (role, text) に畳む。

    1 メッセージが複数の text パートに割れることがあるので、message_id 単位で連結する。
    text 以外のパート（step-start / tool / reasoning …）は本文ではないので落とす。
    """
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT p.message_id, p.data, m.data FROM part p "
            "JOIN message m ON m.id = p.message_id "
            "WHERE p.session_id = ? ORDER BY p.time_created, p.id", (session_id,))
        rows = cur.fetchall()
    except sqlite3.Error:
        return []

    ordered: "list[str]" = []
    buckets: "dict[str, list[str]]" = {}
    roles: "dict[str, str]" = {}
    for message_id, part_raw, message_raw in rows:
        part = _json_obj(part_raw)
        message = _json_obj(message_raw)
        if part.get("type") != "text":
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        # drop-line の対象は part 行（親の役割を載せた形）。jsonl-dir の「生ログ 1 行」に
        # あたるのがここ——本文と一緒にメタ（type / role / synthetic 等）が乗っている。
        line = {**message, **part, "role": role}
        if rules and cleaning.should_drop_line(line, rules, warn=warn):
            continue
        text = part.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        key = str(message_id)
        if key not in buckets:
            buckets[key] = []
            ordered.append(key)
            roles[key] = "User" if role == "user" else "Assistant"
        buckets[key].append(text)

    out: "list[tuple[str, str]]" = []
    for key in ordered:
        text = "\n".join(buckets[key])
        if rules:
            text = cleaning.clean_message_text(text, rules, warn=warn)
        if text.strip():
            out.append((roles[key], text))
    return out


def _json_obj(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(obj, dict):
            return obj
    return {}


def _opencode_tokens(row: dict) -> "tuple[int, int]":
    """session 行の実測トークン。キャッシュ分は入力側へ寄せる（jsonl-dir の扱いと同じ）。"""
    def num(*keys):
        total = 0
        for key in keys:
            v = row.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += int(v)
        return total
    return (num("tokens_input", "tokens_cache_read", "tokens_cache_write"),
            num("tokens_output"))


def _opencode_model(raw) -> str:
    """model 列は JSON（`{"id":"qwen3","providerID":"ollama"}`）。opencode の作法に
    合わせて `provider/model` の 1 文字列へ畳む。素の文字列ならそのまま。"""
    obj = _json_obj(raw)
    if not obj:
        return raw.strip() if isinstance(raw, str) else ""
    model = obj.get("id") or obj.get("modelID") or ""
    provider = obj.get("providerID") or obj.get("provider") or ""
    if provider and model:
        return f"{provider}/{model}"
    return str(model or provider or "")
