"""CLI ネイティブセッションストアのリーダ — format ごとに 1 実装（設計 §4.2・C7）。

どこに・どの形式であるかは `agents/<name>.json` の `session_log` ブロックが宣言する
（契約: schemas/agent-cli.schema.json）。ここは format の閉じた enum だけを実装し、
新 CLI が既存 format なら JSON 追記だけで収集できる。

セッションの正規形（このモジュールの戻り値）:
  {"native_id", "store", "cwd", "created_at", "updated_at",  # epoch 秒
   "model", "turns", "tokens_in", "tokens_out", "usage_measured",
   "messages": [(role, text), ...]}   # want_messages=True のときだけ
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3

FORMATS = ("jsonl-dir", "kiro-sqlite")


def read_sessions(session_log: dict, *, want_messages: bool = False) -> "list[dict]":
    fmt = (session_log or {}).get("format")
    paths = [os.path.expanduser(p) for p in (session_log or {}).get("paths") or []]
    if fmt == "jsonl-dir":
        out = []
        for p in paths:
            out.extend(_read_jsonl_dir(p, want_messages=want_messages))
        return out
    if fmt == "kiro-sqlite":
        out = []
        for p in paths:
            for db in sorted(glob.glob(p)) or ([p] if os.path.exists(p) else []):
                out.extend(_read_kiro_sqlite(db, want_messages=want_messages))
        return out
    return []          # 未知 format は「未収集」— 呼び出し側（doctor / collect）が明示する


# -- jsonl-dir（claude / codex 系: 1 セッション = 1 *.jsonl） -------------------

def _read_jsonl_dir(root: str, *, want_messages: bool) -> "list[dict]":
    sessions = []
    pattern = os.path.join(glob.escape(root), "**", "*.jsonl")
    for path in sorted(glob.glob(pattern, recursive=True)):
        s = _parse_jsonl_session(path, want_messages=want_messages)
        if s:
            sessions.append(s)
    return sessions


def _parse_jsonl_session(path: str, *, want_messages: bool) -> "dict | None":
    first_ts = last_ts = None
    cwd = model = sid = None
    sum_in = sum_out = 0
    last_total = None
    turns = 0
    messages: "list[tuple[str, str]]" = []
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
                if not isinstance(obj, dict):
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
                    sum_in += i
                    sum_out += o
                total = _find_total_usage(obj)
                if total is not None:
                    last_total = total
                role_text = _message_of(obj)
                if role_text:
                    turns += 1
                    if want_messages:
                        messages.append(role_text)
    except OSError:
        return None
    if first_ts is None and turns == 0:
        return None
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
        "turns": turns,
        "tokens_in": tokens_in or None,
        "tokens_out": tokens_out or None,
        "usage_measured": bool(tokens_in or tokens_out),
        "messages": messages,
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


# -- kiro-sqlite（~/.kiro/store.db。tools/kiro-log-exporter の手順を移植） ------

def _read_kiro_sqlite(db_path: str, *, want_messages: bool) -> "list[dict]":
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
                s = _parse_kiro_row(data, db_path, want_messages=want_messages)
                if s:
                    sessions.append(s)
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return []
    return sessions


def _parse_kiro_row(data: dict, db_path: str, *, want_messages: bool) -> "dict | None":
    from .util import parse_iso
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
    messages: "list[tuple[str, str]]" = []
    turns = 0
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            got = _message_of(item)
            if got:
                turns += 1
                if want_messages:
                    messages.append(got)

    def _sec(v):
        if isinstance(v, (int, float)):
            return float(v) / 1000.0 if v > 4102444800 else float(v)   # ms なら秒へ
        return parse_iso(v) or 0.0

    return {
        "native_id": sid,
        "store": db_path,
        "cwd": str(data.get("directory") or data.get("project_path")
                   or data.get("workspace") or ""),
        "created_at": _sec(data.get("created_at", 0)),
        "updated_at": _sec(data.get("updated_at") or data.get("created_at", 0)),
        "model": "",
        "turns": turns,
        "tokens_in": None,
        "tokens_out": None,
        "usage_measured": False,
        "messages": messages,
    }
