"""CLI ネイティブセッションストアのリーダ — format ごとに 1 実装(仕様書 §3・C7)。

どこに・どの形式であるかは `agents/<name>.json` の `session_log` ブロックが宣言する
(契約: schemas/agent-cli.schema.json)。ここは format の閉じた enum だけを実装し、
新 CLI が既存 format なら JSON 追記だけで収集できる。本文のノイズ除去(clean)も同じ
`session_log` に additive に載る宣言で、実装は `cleaning.py` に 1 つ(仕様書 §3)。

セッションの正規形(このモジュールの戻り値):
  {"native_id", "store", "cwd", "created_at", "updated_at",  # epoch 秒
   "model", "log_version", "turns", "tokens_in", "tokens_out", "usage_measured",
   "usage_breakdown": {...},  # optional; unavailable components are null
   "messages": [(role, text), ...]}   # want_messages=True のときだけ
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import cleaning
from .util import elog

FORMATS = ("jsonl-dir", "kiro-sqlite", "vscode-chat")


def expand_paths(paths, extra_homes=()) -> "list[str]":
    """宣言された `paths` を、ホームと `extra_homes` の両方へ展開する。

    `~/` で始まる宣言だけを追加のホームへ載せ替える。絶対パスの宣言はそのホスト固有の
    場所を指しているので載せ替えない。WSL から `/mnt/c/Users/<me>` を渡すと、Windows 側の
    CLI ログを同じ定義のまま読める。存在しない展開先は呼び先の glob が空を返すだけで、
    黙って飛ばす（使っていないホストを「未収集」と騒がない）。
    """
    out: "list[str]" = []
    seen: "set[str]" = set()
    for raw in paths or []:
        cands = [os.path.expanduser(str(raw))]
        if str(raw).startswith("~/"):
            for home in extra_homes or ():
                cands.append(os.path.join(os.path.expanduser(str(home)), str(raw)[2:]))
        for cand in cands:
            key = os.path.normpath(cand)
            if key not in seen:
                seen.add(key)
                out.append(cand)
    return out


def read_sessions(session_log: dict, *, want_messages: bool = False,
                   limit: "int | None" = None, extra_homes=()) -> "list[dict]":
    """limit を指定すると、更新が新しい順に最大 limit セッションだけ読む
    （doctor の棚卸しなど、全件走査が高くつく場面向けの間引き。通常の collect は
    limit なし = 全件・冪等）。"""
    session_log = session_log or {}
    fmt = session_log.get("format")
    paths = expand_paths(session_log.get("paths"), extra_homes)
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
    if fmt == "vscode-chat":
        out = []
        for p in paths:
            out.extend(_read_vscode_dir(p, want_messages=want_messages, limit=limit))
        return _cap(out, limit)
    return []          # 未知 format は「未収集」— 呼び出し側（doctor / collect）が明示する


def session_identities(session_log: dict, *, since: float = 0.0, extra_homes=()) -> "list[dict]":
    """Enumerate source identities through the same parsers used by collect.

    Keeping this deliberately thin prevents reconcile from acquiring a second implementation
    of CLI formats or timestamp semantics.
    """
    return [{"native_id": session["native_id"], "store": session["store"],
             "updated_at": session.get("updated_at") or 0.0}
            for session in read_sessions(session_log, want_messages=False,
                                         extra_homes=extra_homes)
            if not since or (session.get("updated_at") or 0.0) >= since]


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
    components = []
    components_by_message = {}
    total_components = None
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
        if usage is None:
            flat = _flat_usage(obj)
            if flat is not None:
                components.append(_usage_components(obj, semantics="flat-total"))
                sum_in += flat[0]
                sum_out += flat[1]
            elif obj.get("kind") == "llm_end":
                components.append(_usage_components(obj, semantics="flat-total"))
        if usage is not None:
            i, o = _usage_of(usage)
            breakdown = _usage_components(usage)
            # Claude は 1 API 応答の thinking / text を別行にし、同じ message.id と
            # usage を各行へ再掲する。API 呼び出しを表す id がある行は最後の 1 件だけ数える。
            usage_id = None
            for container in (msg, payload, obj):
                if container and isinstance(container.get("id"), str):
                    usage_id = container["id"]
                    break
            if usage_id:
                usage_by_message[usage_id] = (i, o)
                components_by_message[usage_id] = breakdown
            else:
                components.append(breakdown)
                sum_in += i
                sum_out += o
        total = _find_total_usage(obj)
        if total is not None:
            last_total = total
            total_components = _usage_components(_find_total_usage_data(obj),
                                                 semantics="openai-total-with-cached-subset")
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
        **({"usage_breakdown": total_components or _sum_components(
            components + list(components_by_message.values()))}
           if total_components is not None or components or components_by_message else {}),
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


def _flat_usage(obj: dict) -> "tuple[int, int] | None":
    """平らな `tokens_in` / `tokens_out` を載せるログ形（agentcore が自分で書く形）。

    `agent-ollama` は 1 ラウンドの実測を `llm_end` の**トップレベル**へ書く
    （`ollama_loop.py` の `emit("llm_end", ..., tokens_in=, tokens_out=)`）。ここが
    入れ子の `usage` しか見ていなかったので、**書いている側と読んでいる側が食い違って
    いた**——`session_log.usage` を true にしても 0 トークンで「実測済み」と記帳され、
    秒からの推定より悪くなる（設計 2026-08-27 §7.3 C / 実装計画 段 8）。

    見るのは `llm_end` だけである。`llm_progress` は同じ綴りで**途中経過**の
    `tokens_out` を載せるので、行を選ばずに足すと 1 ラウンドを何度も数える。
    """
    if obj.get("kind") != "llm_end":
        return None
    tin, tout = obj.get("tokens_in"), obj.get("tokens_out")
    if not isinstance(tin, (int, float)) and not isinstance(tout, (int, float)):
        return None
    return (int(tin) if isinstance(tin, (int, float)) else 0,
            int(tout) if isinstance(tout, (int, float)) else 0)


def _find_total_usage(obj: dict, depth: int = 0) -> "tuple[int, int] | None":
    u = _find_total_usage_data(obj, depth)
    return _usage_of(u) if u is not None else None


def _find_total_usage_data(obj: dict, depth: int = 0) -> "dict | None":
    """Return the same last cumulative source for legacy totals and components."""
    if depth > 2 or not isinstance(obj, dict):
        return None
    u = obj.get("total_token_usage")
    if isinstance(u, dict):
        return u
    for v in obj.values():
        if isinstance(v, dict):
            got = _find_total_usage_data(v, depth + 1)
            if got is not None:
                return got
    return None


USAGE_FIELDS = ("input_total", "input_uncached", "cache_read", "cache_write", "output")


def _token_count(value):
    # Missing, invalid, negative and boolean values are not measured zero.
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage_components(u: dict, *, semantics: str = "unknown") -> dict:
    """Decode field families, never indiscriminately add cached subsets to input."""
    result = dict.fromkeys(USAGE_FIELDS)
    result["output"] = _token_count(u.get("output_tokens"))
    anthropic = any(k in u for k in ("cache_creation_input_tokens", "cache_read_input_tokens"))
    details = u.get("input_tokens_details")
    details = details if isinstance(details, dict) else {}
    openai = "cached_input_tokens" in u or "cached_tokens" in details
    if anthropic and openai:
        semantics = "unknown"  # Conflicting families: no safe attribution.
    elif anthropic:
        semantics = "anthropic-separate-input-components"
        result["input_uncached"] = _token_count(u.get("input_tokens"))
        result["cache_read"] = _token_count(u.get("cache_read_input_tokens"))
        result["cache_write"] = _token_count(u.get("cache_creation_input_tokens"))
        parts = [result[k] for k in ("input_uncached", "cache_read", "cache_write")]
        if all(v is not None for v in parts):
            result["input_total"] = sum(parts)
    elif openai or semantics == "openai-total-with-cached-subset":
        semantics = "openai-total-with-cached-subset"
        result["input_total"] = _token_count(u.get("input_tokens"))
        result["cache_read"] = _token_count(u.get("cached_input_tokens", details.get("cached_tokens")))
        total, cached = result["input_total"], result["cache_read"]
        if total is not None and cached is not None and cached <= total:
            result["input_uncached"] = total - cached
        # Cache creation is not inferred from absence of a write field.
    elif semantics == "flat-total":
        result["input_total"] = _token_count(u.get("tokens_in"))
        result["output"] = _token_count(u.get("tokens_out"))
    result["semantics"] = semantics
    result["completeness"] = "complete" if all(result[k] is not None for k in USAGE_FIELDS) else "partial"
    return result


def _sum_components(parts: list[dict]) -> dict:
    # A partial call must not make the whole session look fully measured.
    result = {k: sum(p[k] for p in parts) if all(p[k] is not None for p in parts) else None
              for k in USAGE_FIELDS}
    semantics = {p["semantics"] for p in parts}
    result["semantics"] = next(iter(semantics)) if len(semantics) == 1 else "mixed"
    result["completeness"] = "complete" if all(result[k] is not None for k in USAGE_FIELDS) else "partial"
    return result


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


# -- kiro-sqlite (legacy store.db and current kiro-cli/data.sqlite3) ------

def _read_kiro_sqlite(db_path: str, *, want_messages: bool,
                       clean: "dict | None" = None, native_id: "str | None" = None) -> "list[dict]":
    if not os.path.exists(db_path):
        return []
    sessions = []
    try:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0].lower() for r in cur.fetchall()}
            seen = set()
            for table in ("conversations_v2", "sessions", "chat_sessions", "conversations"):
                if table not in tables:
                    continue
                columns = {r[1].lower() for r in cur.execute(f"PRAGMA table_info([{table}])")}
                id_column = "conversation_id" if "conversation_id" in columns else "id" if "id" in columns else None
                query = f"SELECT * FROM [{table}]"  # Closed table/column allowlists above.
                params = ()
                if native_id is not None and id_column:
                    query += f" WHERE [{id_column}] = ?"
                    params = (native_id,)
                cur.execute(query, params)
                cols = [d[0].lower() for d in cur.description]
                for row in cur:
                    data = dict(zip(cols, row))
                    s = _parse_kiro_row(data, db_path, want_messages=want_messages, clean=clean)
                    if s and s["native_id"] not in seen and (native_id is None or s["native_id"] == native_id):
                        seen.add(s["native_id"])
                        sessions.append(s)
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return []
    return sessions


def _parse_kiro_row(data: dict, db_path: str, *, want_messages: bool,
                     clean: "dict | None" = None) -> "dict | None":
    # Current CLI databases store the conversation as JSON in a key/value row.
    payload = data.get("value")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    current = isinstance(payload, dict) and isinstance(payload.get("history"), list)
    if current:
        history = payload["history"]
        converted = []
        completion = []
        cwd = data.get("key", "")
        for turn in history:
            if not isinstance(turn, dict):
                continue
            user = turn.get("user") or {}
            content = user.get("content") or {}
            env = (user.get("env_context") or {}).get("env_state") or {}
            cwd = env.get("current_working_directory") or cwd
            if isinstance(content, dict):
                prompt = content.get("Prompt") or content.get("CancelledToolUses") or {}
                if isinstance(prompt, dict) and isinstance(prompt.get("prompt"), str):
                    converted.append({"role": "user", "content": prompt["prompt"]})
                    completion.append(True)
            assistant = turn.get("assistant") or {}
            response = assistant.get("Response") or assistant.get("ToolUse") or {}
            if isinstance(response, dict) and isinstance(response.get("content"), str) and response["content"]:
                converted.append({"role": "assistant", "content": response["content"]})
                completion.append("Response" in assistant)
        data = {**data, "id": data.get("conversation_id") or payload.get("conversation_id"),
                "messages": converted, "directory": cwd}
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
    message_completion = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
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
                    message_completion.append(completion[index] if current else True)

    def _sec(v):
        return _epoch_sec(v)

    return {
        "native_id": sid,
        "store": db_path,
        "cwd": str(data.get("directory") or data.get("project_path")
                   or data.get("workspace") or ""),
        "created_at": _sec(data.get("created_at", 0)),
        "updated_at": _sec(data.get("updated_at") or data.get("created_at", 0)),
        "model": str((payload.get("model_info") or {}).get("model_id") or "") if current else "",
        "message_completion": message_completion,
        "log_version": version,
        "turns": turns,
        "tokens_in": None,
        "tokens_out": None,
        "usage_measured": False,
        "messages": messages,
        "_clean_warnings": warnings,
    }


# -- vscode-chat（VS Code の Copilot チャット: 1 会話 = 1 *.json） --------------
#
# VS Code は会話を「初版 + 追記される差分」として書く。読解はここだけに置き、
# デスクトップ側の閲覧口（session_browser）もこの関数を呼ぶ——同じ形式の
# パーサを 2 つ持つと、VS Code が書き方を変えたときに片方だけ直る。

def visible_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (visible_text(v) for v in value)))
    if isinstance(value, dict):
        # Only visible text/markdown, never arbitrary tool payloads.
        if value.get("kind") not in (None, "markdownContent", "markdownVuln"):
            return ""
        return visible_text(value.get("text", value.get("value", value.get("content", ""))))
    return ""


def vscode_objects(file, status):
    """Replay append-only logs without retaining superseded versions in memory."""
    with file.open(encoding="utf-8-sig") as stream:
        first = next((line for line in stream if line.strip()), "")
        try:
            obj = json.loads(first)
        except ValueError:
            # Pretty-printed JSON exports are a single document.
            stream.seek(0)
            try:
                yield json.load(stream)
            except ValueError as exc:
                raise ValueError("会話の保存形式を読み取れません") from exc
            return
        yield obj
        for line in stream:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                status["partial"] = True


def restore_vscode(objects):
    state = None
    for entry in objects:
        if not isinstance(entry, dict):
            raise ValueError("未対応の会話更新形式です")
        if state is None and "requests" in entry:
            state = entry
            continue
        kind = entry.get("kind")
        if kind == 0 and state is None:
            state = entry["v"]
            continue
        keys = entry.get("k")
        if state is None or kind not in (1, 2, 3) or not isinstance(keys, list):
            raise ValueError("未対応の会話更新形式です")
        if not keys:
            if kind == 1:
                state = entry["v"]
                continue
            target = state
        else:
            target = state
            for key in keys[:-1]:
                target = target[key]
            key = keys[-1]
            if kind == 1:
                target[key] = entry["v"]
                continue
            if kind == 3:
                if isinstance(target, dict):
                    target.pop(key, None)
                else:
                    raise ValueError("未対応の会話更新形式です")
                continue
            target = target[key]
        if kind == 2 and isinstance(target, list):
            if "i" in entry:
                i = entry["i"]
                if not isinstance(i, int) or i < 0 or i > len(target):
                    raise ValueError("不正な会話更新位置です")
                del target[i:]
            target.extend(entry.get("v", []))
        else:
            raise ValueError("未対応の会話更新形式です")
    if not isinstance(state, dict) or not isinstance(state.get("requests"), list):
        raise ValueError("会話の本文がありません")
    return state


def local_path(value):
    if isinstance(value, dict):
        value = value.get("path", "") if value.get("scheme") == "file" else ""
    if not isinstance(value, str):
        return ""
    if value.startswith("file://"):
        parsed = urlparse(value)
        path = unquote(parsed.path)
        if len(path) > 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return ("//" + parsed.netloc if parsed.netloc else "") + path
    return value


def vscode_session(file, objects):
    obj = restore_vscode(objects)
    messages = []
    dates = []
    model = ""
    for i, req in enumerate(obj["requests"]):
        if not isinstance(req, dict) or req.get("hiddenFromTranscript") or req.get("isHidden"):
            continue
        stamp = _epoch_sec(req.get("timestamp"))
        if stamp:
            dates.append(stamp)
        rid = str(req.get("requestId") or i)
        if not req.get("requestHiddenFromTranscript"):
            body = visible_text(req.get("message"))
            if body:
                messages.append({"id": rid + ":user", "role": "user", "text": body})
        body = visible_text(req.get("response"))
        if body:
            status = req.get("modelState", {})
            status = status.get("value") if isinstance(status, dict) else status
            messages.append({"id": rid + ":assistant", "role": "assistant", "text": body,
                             "complete": not req.get("isCanceled") and status in (None, 1)})
        model = req.get("modelId") or model
    model = model or (obj.get("inputState", {}).get("selectedModel") or {}).get("identifier", "")
    cwd = local_path(obj.get("workingDirectory", ""))
    workspace = file.parent.parent / "workspace.json"
    if not cwd and workspace.is_file():
        info = json.loads(workspace.read_text(encoding="utf-8"))
        cwd = local_path(info.get("folder", info.get("workspace", "")))
    return {"nativeId": str(obj.get("sessionId") or file.stem), "title": obj.get("customTitle", ""),
            "repo": cwd, "model": model, "messages": messages,
            "createdAt": _epoch_sec(obj.get("creationDate")), "updatedAt": max(dates or [0])}


def _read_vscode_dir(root: str, *, want_messages: bool,
                      limit: "int | None" = None) -> "list[dict]":
    """`root` はグロブでもよい（`.../workspaceStorage/*/chatSessions`）。"""
    files: "list[Path]" = []
    for base in _dbs(root):
        p = Path(base)
        if p.is_dir():
            files.extend(sorted(q for q in p.glob("*.json") if q.is_file()))
        elif p.is_file() and p.suffix == ".json":
            files.append(p)
    if limit is not None and len(files) > limit:
        files = sorted(files, key=lambda q: _safe_mtime(str(q)), reverse=True)[:limit]
    out = []
    for file in files:
        got = _parse_vscode_session(file, want_messages=want_messages)
        if got:
            out.append(got)
    return out


def _parse_vscode_session(file, *, want_messages: bool) -> "dict | None":
    file = Path(file)
    try:
        obj = vscode_session(file, vscode_objects(file, {"partial": False}))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    if not obj["messages"]:
        return None         # 開いただけで何も訊いていないチャット。測るものが無い
    mtime = _safe_mtime(str(file))
    messages = [(m["role"], m["text"]) for m in obj["messages"]] if want_messages else []
    return {
        "native_id": obj["nativeId"],
        "store": str(file),
        "cwd": obj.get("repo") or "",
        "created_at": obj.get("createdAt") or mtime,
        "updated_at": obj.get("updatedAt") or mtime,
        "model": obj.get("model") or "",
        "log_version": "",
        "turns": len(obj["messages"]),
        # VS Code は使用量を保存しない。推定へ回す（measured にはしない）。
        "tokens_in": None,
        "tokens_out": None,
        "usage_measured": False,
        "messages": messages,
        "message_completion": [bool(m.get("complete", True)) for m in obj["messages"]],
        "_clean_warnings": [],
    }
