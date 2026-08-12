"""collect — 源泉の増分収集と正規化（LLM 不使用。設計 §4）。

源泉は読み取り専用・増分・冪等。CLI quota snapshot だけは候補切替へ共有するため
node-budget の追記専用台帳へ観測行として写す。カーソルと収集済み管理は store.state.json。
源泉の場所は 引数 / 設定 > 契約上の既定パス で解決し、環境変数は見ない。
明示設定された源泉が読めないときは fail-close（exit 2）——黙って部分集計を
全体と偽らない（不変条件 3）。
"""
from __future__ import annotations

import glob
import json
import os
import queue
import re
import shutil
import subprocess
import threading

from .configfile import resolve_budget_dir
from .store import Store, home_relative, record_id
from .util import append_jsonl, elog, epoch_to_iso, iter_jsonl, log, now_iso, parse_iso, read_json, utc_day

ERROR_TAG_RE = re.compile(r"\[agent-error:([a-z]+)\]")

KNOWN_SOURCES = ("budget-ledger", "cli-native", "cli-quota", "flow-bus", "project-root",
                 "amigos-bus", "loop-log")
SESSION_PARSER_REVISION = 2


class SourceError(RuntimeError):
    """明示設定された源泉が読めない（exit 2 に対応）。"""


def cmd_collect(args) -> int:
    from .configfile import resolve_audit_dir
    store = Store(resolve_audit_dir(args))
    wanted = list(getattr(args, "source", None) or getattr(args, "sources", None) or [])
    for s in wanted:
        if s not in KNOWN_SOURCES:
            elog(f"collect: 不明な source です: {s}（指定できるのは {', '.join(KNOWN_SOURCES)}）")
            return 2
    since = 0.0
    if getattr(args, "since", None):
        since = parse_iso(args.since) or 0.0

    def on(name: str) -> bool:
        return not wanted or name in wanted

    added = 0
    try:
        if on("budget-ledger"):
            added += collect_budget_ledger(args, store)
        if on("cli-native"):
            added += collect_cli_native(args, store,
                                        with_transcripts=bool(getattr(args, "with_transcripts", False)),
                                        since=since)
        if on("cli-quota"):
            added += collect_cli_quota(args, store)
        if on("flow-bus"):
            added += collect_flow_buses(args, store)
        if on("project-root"):
            added += collect_project_roots(args, store)
        if on("amigos-bus"):
            added += collect_amigos_buses(args, store)
        if on("loop-log"):
            added += collect_loop_logs(args, store)
    except SourceError as e:
        store.save_state()
        elog(f"collect: {e}")
        return 2
    store.save_state()
    log("collect", f"新規レコード {added} 件（store: {home_relative(store.root)}）")

    # 定期クリーンアップの相乗り（設計 §3.3）。定期に走る唯一のコマンドが collect なので、
    # ここに乗せる——新しい常駐や書き手を増やさない（C7）。
    if getattr(args, "gc_auto", True):
        from .gccmd import auto_gc
        auto_gc(args, store)
    return 0


def _dirs_of(args, key: str) -> "list[str]":
    vals = getattr(args, key, None) or []
    if isinstance(vals, str):
        vals = [vals]
    out = []
    for v in vals:
        p = os.path.abspath(os.path.expanduser(str(v)))
        if not os.path.exists(p):
            raise SourceError(f"{key} に設定されたパスが存在しません: {v}")
        out.append(p)
    return out


# -- budget-ledger -----------------------------------------------------------

def collect_budget_ledger(args, store: Store) -> int:
    """ledger/<YYYYMMDD>.jsonl の新規行を kind:ledger レコードへ。
    カーソルはファイルごとのバイトオフセット（追記専用なので後退しない）。"""
    ledger_dir = os.path.join(resolve_budget_dir(args), "ledger")
    if not os.path.isdir(ledger_dir):
        return 0
    added = 0
    for path in sorted(glob.glob(os.path.join(glob.escape(ledger_dir), "*.jsonl"))):
        key = f"budget-ledger::{path}"
        offset = int(store.cursor(key) or 0)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size <= offset:
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            lineno = 0
            for line in f:
                lineno += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or "ts" not in row:
                    continue
                if row.get("quota_kind") and not row.get("event"):
                    continue    # 旧い quota 観測行（event を持たない世代）。集計に混ぜない
                ts = parse_iso(row.get("ts")) or 0.0
                # 観測行（`event` 付き）は消費ではないので `kind: event` で入れる。
                # `kind: ledger` に混ぜると、消費 0 の行が実行回数を水増しして平均消費を
                # 下げる——**コストを測るために書いた行がコストの数字を動かす**。
                # 落とさず別種別にするのは、昇格が何回起きたかを後から数えたいため。
                rec = {
                    "id": record_id("budget-ledger", path, f"{offset}+{lineno}"),
                    "_epoch": ts,
                    "ts": row.get("ts"),
                    "kind": "event" if row.get("event") else "ledger",
                    "source": "budget-ledger",
                    "node": row.get("node") or "",
                    "tool": row.get("tool") or "",
                    "workload": row.get("workload") or "",
                    "ref": row.get("ref") or "",
                    "purpose": row.get("purpose") or row.get("ref") or "",
                    "agent_cli": row.get("agent_cli") or "",
                    "model": row.get("model") or "",
                    "seconds": row.get("seconds"),
                    "tokens_in": row.get("tokens_in"),
                    "tokens_out": row.get("tokens_out"),
                    "usd": row.get("usd"),
                    "measured": row.get("tokens_in") is not None or row.get("tokens_out") is not None,
                }
                if row.get("usage_kind"):
                    rec["usage_kind"] = row["usage_kind"]
                if row.get("event"):
                    rec["event"] = row["event"]
                if row.get("quota_kind"):
                    rec["quota_kind"] = row["quota_kind"]
                if row.get("reset_at"):
                    rec["reset_at"] = row["reset_at"]
                for key in ("quota_used_percent", "quota_source", "quota_window_minutes"):
                    if row.get(key) is not None:
                        rec[key] = row[key]
                if isinstance(row.get("escalation"), dict):
                    rec["escalation"] = row["escalation"]
                for key in ("run_id", "flow_node"):
                    if row.get(key):
                        rec[key] = row[key]
                if isinstance(row.get("methods"), list):
                    rec["methods"] = [str(v) for v in row["methods"] if str(v)]
                if isinstance(row.get("trial"), dict):
                    rec["trial"] = {"id": str(row["trial"].get("id") or ""),
                                    "variant": str(row["trial"].get("variant") or "")}
                if store.append_record(rec):
                    added += 1
            store.set_cursor(key, f.tell())
    return added


# -- cli-native --------------------------------------------------------------

def agent_defs_with_session_log(project_dir: "str | None" = None) -> "list[tuple[str, dict]]":
    """agents/<name>.json のうち session_log を宣言する定義を先勝ちで列挙する。
    探索順はプラグイン契約の 1 実装（agentcore.agentcli.plugin_dirs）に委ねる。"""
    from agentcore import agentcli as _agentcli
    seen: "dict[str, dict]" = {}
    for d in _agentcli.plugin_dirs(project_dir):
        if not os.path.isdir(d):
            continue
        for path in sorted(glob.glob(os.path.join(glob.escape(d), "*.json"))):
            name = os.path.splitext(os.path.basename(path))[0]
            if name in seen:
                continue
            data = read_json(path)
            if isinstance(data, dict):
                seen[name] = data
    return [(name, spec) for name, spec in sorted(seen.items())
            if isinstance(spec.get("session_log"), dict)]


def collect_cli_native(args, store: Store, *, with_transcripts: bool, since: float = 0.0) -> int:
    from . import readers
    added = 0
    for name, spec in agent_defs_with_session_log():
        slog = spec["session_log"]
        if slog.get("format") not in readers.FORMATS:
            log("collect", f"{name}: session_log.format={slog.get('format')!r} は未対応のため"
                           "未収集です（対応 format: " + ", ".join(readers.FORMATS) + "）")
            continue
        for sess in readers.read_sessions(slog, want_messages=with_transcripts):
            if since and (sess["updated_at"] or 0) < since:
                continue
            # parser revision をカーソルへ含め、usage の読み方を直したときは既存セッションも
            # 1 度だけ読み直す。records は追記専用なので、既存 id には補正行を重ねる。
            key = f"cli-native-v{SESSION_PARSER_REVISION}::{name}::{sess['store']}::{sess['native_id']}"
            prev = float(store.cursor(key) or 0.0)
            if sess["updated_at"] and sess["updated_at"] <= prev:
                continue
            store.set_cursor(key, sess["updated_at"] or 0.0)
            rec = {
                "id": record_id(f"cli-native:{name}", sess["store"], sess["native_id"]),
                "_epoch": sess["updated_at"] or 0.0,
                "ts": _iso(sess["updated_at"]),
                "kind": "session",
                "source": f"{name}-native",
                "cwd": home_relative(sess["cwd"]),
                "agent_cli": name,
                "model": sess["model"],
                "log_version": sess.get("log_version") or "",
                "session_id": sess["native_id"],
                "started_at": _iso(sess["created_at"]),
                "turns": sess["turns"],
                "tokens_in": sess["tokens_in"],
                "tokens_out": sess["tokens_out"],
                "measured": bool(sess["usage_measured"]),
                "parser_revision": SESSION_PARSER_REVISION,
            }
            if with_transcripts and sess["messages"]:
                rec["excerpt_ref"] = _write_transcript(store, name, sess)
            if store.append_record(rec):
                added += 1
            else:
                correction = dict(rec)
                correction["id"] = record_id(
                    f"cli-native-usage-v{SESSION_PARSER_REVISION}:{name}", sess["store"],
                    f"{sess['native_id']}:{sess['updated_at']}")
                correction["kind"] = "session-usage"
                if store.append_record(correction):
                    added += 1
    return added


def _write_transcript(store: Store, cli_name: str, sess: dict) -> str:
    safe_sid = re.sub(r"[^A-Za-z0-9._-]", "_", sess["native_id"])[:64]
    rel = os.path.join("transcripts", cli_name, f"{safe_sid}.log")
    path = os.path.join(store.root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Session: {sess['native_id']}\nSource: {cli_name}\n"
                f"Path: {home_relative(sess['cwd'])}\n\n")
        for role, text in sess["messages"]:
            f.write(f"[{role}]\n{text}\n\n")
    return rel


def _iso(sec) -> str:
    from .util import epoch_to_iso
    return epoch_to_iso(float(sec)) if sec else ""


# -- CLI quota ---------------------------------------------------------------

def collect_cli_quota(args, store: Store) -> int:
    """各CLIが自分で表示する契約枠を、モデル実行なしで収集する。"""
    added = 0
    for executable, collector in (("claude", collect_claude_quota),
                                  ("codex", collect_codex_quota),
                                  ("copilot", collect_copilot_quota),
                                  ("kiro-cli", collect_kiro_quota)):
        if shutil.which(executable):
            added += collector(args, store)
    return added


def _append_quota_snapshot(args, store: Store, *, agent_cli: str, source: str,
                           used_percent, reset_at: str = "", window_minutes=None,
                           reached: bool = False) -> int:
    try:
        used = max(0.0, min(100.0, float(used_percent)))
    except (TypeError, ValueError):
        return 0
    used = int(used) if used.is_integer() else round(used, 1)
    snapshot = {
        "agent_cli": agent_cli,
        "quota_used_percent": used,
        "quota_source": source,
        "reset_at": reset_at,
        **({"quota_window_minutes": window_minutes} if window_minutes is not None else {}),
        **({"quota_kind": "rate_limit"} if reached or used >= 100 else {}),
    }
    signature = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    cursor = f"cli-quota::{agent_cli}"
    ts = now_iso()
    duplicate = store.cursor(cursor) == signature
    added = 0
    if not duplicate:
        event = {"ts": ts, "kind": "event", "source": source,
                 "event": "quota_snapshot", **snapshot}
        event["id"] = record_id("cli-quota", agent_cli, signature)
        added = int(store.append_record({"_epoch": parse_iso(ts) or 0.0, **event}))
    append_jsonl(os.path.join(resolve_budget_dir(args), "ledger",
                              f"{utc_day(parse_iso(ts) or 0.0)}.jsonl"),
                 {"ts": ts, "workload": "audit", "tool": "agent-audit", "seconds": 0,
                  "ref": "quota", "purpose": "quota", "event": "quota_snapshot", **snapshot})
    if not duplicate:
        store.set_cursor(cursor, signature)
    return added


_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _claude_reset_epoch(value: str, now=None) -> "int | None":
    import datetime as dt
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    zone_match = re.search(r"\(([^)]+)\)\s*$", value)
    try:
        zone = ZoneInfo(zone_match.group(1)) if zone_match else dt.timezone.utc
    except ZoneInfoNotFoundError:
        zone = dt.timezone.utc
    current = now.astimezone(zone) if now else dt.datetime.now(zone)
    value = re.sub(r"\s*\([^)]+\)\s*$", "", value).strip()
    dated = re.fullmatch(r"([A-Z][a-z]{2})\s+(\d{1,2})\s+at\s+(\d{1,2})(?::(\d{2}))?(am|pm)", value)
    timed = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)", value)
    try:
        if dated:
            month = dt.datetime.strptime(dated.group(1), "%b").month
            hour = int(dated.group(3)) % 12 + (12 if dated.group(5) == "pm" else 0)
            reset = dt.datetime(current.year, month, int(dated.group(2)), hour,
                                int(dated.group(4) or 0), tzinfo=zone)
            if reset <= current:
                reset = reset.replace(year=current.year + 1)
        elif timed:
            hour = int(timed.group(1)) % 12 + (12 if timed.group(3) == "pm" else 0)
            reset = current.replace(hour=hour, minute=int(timed.group(2) or 0),
                                    second=0, microsecond=0)
            if reset <= current:
                reset += dt.timedelta(days=1)
        else:
            return None
    except ValueError:
        return None
    return int(reset.timestamp())


def _parse_claude_usage(output: str, now=None) -> dict:
    lines = [line.strip() for line in _ANSI_RE.sub("", output).replace("\r", "\n").splitlines()
             if line.strip()]
    windows = {}
    for index, label in enumerate(lines):
        if label != "Current session" and not label.startswith("Current week"):
            continue
        following = lines[index + 1:index + 7]
        percent = next((re.search(r"(\d+(?:\.\d+)?)%\s+used", line)
                        for line in following if "%" in line and "used" in line), None)
        reset = next((line.partition("Resets ")[2] for line in following
                      if line.startswith("Resets ")), "")
        if percent:
            windows[label] = {"used_percentage": float(percent.group(1)),
                              "resets_at": _claude_reset_epoch(reset, now) if reset else None}
    return windows


def _claude_rate_limits() -> "dict | None":
    """Claude の組み込み /usage を画面読取モードで読む。"""
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 35, 120, 0, 0))
    env = dict(os.environ, TERM="xterm-256color")
    proc = subprocess.Popen(
        ["claude", "--setting-sources", "", "--ax-screen-reader", "--no-chrome",
         "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
        stdin=slave, stdout=slave, stderr=slave, cwd=os.getcwd(), env=env, close_fds=True)
    os.close(slave)
    output = bytearray()
    ready_at = sent_at = None
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.25)
            if ready:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
            decoded = output.decode("utf-8", errors="replace")
            if ready_at is None and "manual mode on" in decoded:
                ready_at = time.monotonic() + 0.5
            if ready_at and sent_at is None and time.monotonic() >= ready_at:
                os.write(master, b"/usage\r")
                sent_at = time.monotonic()
            if sent_at and time.monotonic() >= sent_at + 2:
                limits = _parse_claude_usage(decoded)
                if limits:
                    return limits
        return None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.close(master)


def collect_claude_quota(args, store: Store, *, query=None) -> int:
    if query is None:
        query = _claude_rate_limits
    try:
        limits = query()
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("collect", f"claude quota: 取得できませんでした（{exc}）")
        return 0
    windows = [(name, value) for name, value in (limits or {}).items()
               if isinstance(value, dict) and value.get("used_percentage") is not None]
    shared = [(name, value) for name, value in windows if name in
              ("five_hour", "seven_day", "Current session", "Current week (all models)")]
    windows = shared or windows
    if not windows:
        log("collect", "claude quota: /usage から枠を取得できませんでした")
        return 0
    name, window = max(windows, key=lambda item: float(item[1]["used_percentage"]))
    try:
        reset_at = epoch_to_iso(float(window["resets_at"])) if window.get("resets_at") else ""
    except (TypeError, ValueError, OverflowError):
        reset_at = ""
    return _append_quota_snapshot(
        args, store, agent_cli="claude", source="claude-usage",
        used_percent=window["used_percentage"], reset_at=reset_at,
        window_minutes=300 if name == "Current session" else 10080)


def _parse_copilot_usage(output: str) -> "dict | None":
    text = _ANSI_RE.sub("", output).replace("\r", "\n")
    match = re.search(r"Plan.*?(\d+(?:\.\d+)?)%\s+used", text, re.DOTALL)
    if not match:
        return None
    reset = re.search(r"(?:Resets?|Renews?)\s+(\d{4}-\d{2}-\d{2}[^\n]*)", text,
                      re.IGNORECASE)
    return {"quota_used_percent": float(match.group(1)),
            "reset_at": reset.group(1).strip() if reset else ""}


def _copilot_usage() -> "dict | None":
    """Copilot の組み込み /usage を画面読取モードで読む。"""
    import fcntl
    import pty
    import select
    import struct
    import tempfile
    import termios
    import time

    with tempfile.TemporaryDirectory(prefix="agent-audit-copilot-") as temp_dir:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
        env = dict(os.environ, TERM="xterm-256color")
        proc = subprocess.Popen(
            ["copilot", "--screen-reader", "--no-color", "--no-custom-instructions",
             "--disable-builtin-mcps", "--no-remote", "--no-auto-update",
             "--log-dir", temp_dir],
            stdin=slave, stdout=slave, stderr=slave, cwd=os.getcwd(), env=env, close_fds=True)
        os.close(slave)
        output = bytearray()
        ready_at = sent_at = None
        try:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.25)
                if ready:
                    try:
                        output.extend(os.read(master, 65536))
                    except OSError:
                        break
                decoded = output.decode("utf-8", errors="replace")
                if ready_at is None and "Session:" in decoded:
                    ready_at = time.monotonic() + 1
                if ready_at and sent_at is None and time.monotonic() >= ready_at:
                    os.write(master, b"/usage\r")
                    sent_at = time.monotonic()
                if sent_at:
                    result = _parse_copilot_usage(decoded)
                    if result:
                        return result
            return None
        finally:
            try:
                os.write(master, b"/exit\r")
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            os.close(master)


def collect_copilot_quota(args, store: Store, *, query=None) -> int:
    if query is None:
        query = _copilot_usage
    try:
        usage = query()
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("collect", f"copilot quota: 取得できませんでした（{exc}）")
        return 0
    if not isinstance(usage, dict) or usage.get("quota_used_percent") is None:
        log("collect", "copilot quota: /usage から枠を取得できませんでした")
        return 0
    return _append_quota_snapshot(
        args, store, agent_cli="copilot", source="copilot-usage",
        used_percent=usage["quota_used_percent"], reset_at=str(usage.get("reset_at") or ""))


def _kiro_usage() -> "dict | None":
    """Kiro ACP の構造化 usage コマンドを実行する。"""
    import tempfile

    proc = subprocess.Popen(
        ["kiro-cli", "acp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=tempfile.gettempdir())
    messages: "queue.Queue[dict]" = queue.Queue()

    def read_messages() -> None:
        for line in proc.stdout or ():
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass

    threading.Thread(target=read_messages, daemon=True).start()

    def request(request_id: int, method: str, params: dict) -> "dict | None":
        if proc.stdin is None:
            return None
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                     "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        while True:
            try:
                reply = messages.get(timeout=20)
            except queue.Empty:
                return None
            if reply.get("id") == request_id:
                return reply.get("result") if isinstance(reply.get("result"), dict) else None

    session_id = ""
    try:
        if request(1, "initialize", {
                "protocolVersion": 1, "clientCapabilities": {},
                "clientInfo": {"name": "agent-audit", "version": "1"}}) is None:
            return None
        session = request(2, "session/new", {"cwd": tempfile.gettempdir(), "mcpServers": []})
        session_id = str((session or {}).get("sessionId") or "")
        if not session_id:
            return None
        result = request(3, "_kiro.dev/commands/execute", {
            "sessionId": session_id, "command": {"command": "usage", "args": {}}})
        data = (result or {}).get("data")
        return data if isinstance(data, dict) else None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        if session_id:
            subprocess.run(["kiro-cli", "chat", "--delete-session", session_id],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                           check=False)


def collect_kiro_quota(args, store: Store, *, query=None) -> int:
    if query is None:
        query = _kiro_usage
    try:
        usage = query()
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("collect", f"kiro quota: 取得できませんでした（{exc}）")
        return 0
    breakdowns = usage.get("usageBreakdowns") if isinstance(usage, dict) else None
    credit = next((row for row in breakdowns or []
                   if isinstance(row, dict) and row.get("resourceType") == "CREDIT"), None)
    if not credit or credit.get("percentage") is None:
        log("collect", "kiro quota: ACP usage から枠を取得できませんでした")
        return 0
    reset_date = str(usage.get("billingCycleReset") or "")
    reset_at = f"{reset_date}T00:00:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", reset_date) else ""
    return _append_quota_snapshot(
        args, store, agent_cli="kiro", source="kiro-acp-usage",
        used_percent=credit["percentage"], reset_at=reset_at)

def _codex_rate_limits() -> "dict | None":
    """app-server は initialize 応答後に次の要求を送る必要があるため逐次RPCする。"""
    proc = subprocess.Popen(
        ["codex", "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1)
    lines: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout or ()],
                     daemon=True).start()

    def request(message: dict) -> "dict | None":
        if proc.stdin is None:
            return None
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        while True:
            try:
                reply = json.loads(lines.get(timeout=15))
            except queue.Empty:
                return None
            except ValueError:
                continue
            if reply.get("id") == message["id"]:
                return reply.get("result") if isinstance(reply.get("result"), dict) else None

    try:
        initialized = request({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "agent-audit", "version": "1"},
            "capabilities": {"experimentalApi": True}}})
        if initialized is None:
            return None
        return request({"id": 2, "method": "account/rateLimits/read", "params": None})
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def collect_codex_quota(args, store: Store, *, query=None) -> int:
    """Codex の公式 app-server API から現在の枠を読む。モデル実行は行わない。"""
    if query is None:
        if shutil.which("codex") is None:
            return 0
        query = _codex_rate_limits
    try:
        payload = query()
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("collect", f"codex quota: 取得できませんでした（{exc}）")
        return 0
    limits = (payload or {}).get("rateLimits")
    primary = limits.get("primary") if isinstance(limits, dict) else None
    if not isinstance(primary, dict):
        log("collect", "codex quota: app-server から枠を取得できませんでした")
        return 0
    try:
        used_percent = max(0, min(100, int(primary.get("usedPercent"))))
    except (TypeError, ValueError):
        return 0
    reset_epoch = primary.get("resetsAt")
    try:
        reset_at = epoch_to_iso(float(reset_epoch)) if reset_epoch is not None else ""
    except (TypeError, ValueError, OverflowError):
        reset_at = ""
    return _append_quota_snapshot(
        args, store, agent_cli="codex", source="codex-app-server",
        used_percent=used_percent, reset_at=reset_at,
        window_minutes=primary.get("windowDurationMins"),
        reached=bool(limits.get("rateLimitReachedType")))


# -- flow-bus ----------------------------------------------------------------

def collect_flow_buses(args, store: Store) -> int:
    added = 0
    buses = _dirs_of(args, "flow_buses")
    for root in _dirs_of(args, "project_roots"):
        bus = os.path.join(root, "bus")
        if os.path.isdir(bus):
            buses.append(bus)
    for bus in buses:
        runs = os.path.join(bus, "runs")
        if not os.path.isdir(runs):
            continue
        for run_dir in sorted(glob.glob(os.path.join(glob.escape(runs), "*"))):
            meta = read_json(os.path.join(run_dir, "meta.json"))
            if not isinstance(meta, dict):
                continue
            status = str(meta.get("status") or "")
            if status not in ("done", "failed", "cancelled", "canceled"):
                continue          # 非終端 run は収集しない（終端後に一度だけ拾う）
            rid = record_id("flow-bus", bus, os.path.basename(run_dir))
            # run レコードは results/ を拾い終えた**後**に書く。この id が「この run は
            # 収集済み」の目印なので、先に書くと結果の取りこぼしが起きうるし、逆にこの
            # skip を外すと終端 run の results/ を毎回 glob して読み直すことになる。
            if store.has_record(rid):
                continue
            failure = str(meta.get("failure_reason") or "")
            m = ERROR_TAG_RE.search(failure)
            retries, verify = _flow_events_summary(run_dir)
            ts = parse_iso(meta.get("updated_at")) or os.path.getmtime(run_dir)
            graph = read_json(os.path.join(run_dir, "graph.json")) or {}
            strategy = graph.get("strategy") or {}
            comparisons = []
            for item in strategy.get("decision_comparisons") or []:
                if (isinstance(item, dict) and item.get("decision")
                        and isinstance(item.get("agree"), bool)):
                    comparisons.append({key: item.get(key) for key in
                                        ("decision", "llm", "rule", "agree")})
            rec = {
                "id": rid, "_epoch": ts, "ts": _iso(ts),
                "kind": "run", "source": "flow-bus",
                "tool": "agent-flow", "workload": "flow",
                "ref": os.path.basename(run_dir),
                "status": "cancelled" if status == "canceled" else status,
                "error_class": m.group(1) if m else ("" if status == "done" else "content"),
                "retries": retries,
                "verify": verify,
            }
            if comparisons:
                rec["decision_comparisons"] = comparisons
            run_methods = {str(v) for v in strategy.get("methods") or [] if str(v)}
            run_trial = strategy.get("trial") if isinstance(strategy.get("trial"), dict) else None
            results_dir = os.path.join(run_dir, "results")
            for result_path in sorted(glob.glob(os.path.join(glob.escape(results_dir), "*.json"))):
                result = read_json(result_path)
                if not isinstance(result, dict):
                    continue
                node_id = str(result.get("id") or os.path.splitext(os.path.basename(result_path))[0])
                result_rec = {
                    "id": record_id("flow-result", bus, f"{os.path.basename(run_dir)}:{node_id}"),
                    "_epoch": ts, "ts": _iso(ts),
                    "kind": "result", "source": "flow-bus",
                    "tool": "agent-flow", "workload": "flow",
                    "ref": f"{os.path.basename(run_dir)}/{node_id}",
                    "purpose": result.get("kind") or "work",
                    "agent_cli": result.get("agent_cli") or "",
                    "model": result.get("model") or "",
                    # 品質はこのノード自身の結末で見る。統一 verify は run に 1 回・node id
                    # 無しで出る run 全体の判定なので、各ノードへ配ると別モデルのノードまで
                    # 同じ pass/fail を貰い、モデル別の PASS 率が測れなくなる。run の判定は
                    # 参考として run スコープであることが分かる名前で持つ。
                    "status": result.get("status") or "",
                    "run_verify": verify,
                }
                allocation = result.get("context_allocation")
                if isinstance(allocation, dict):
                    result_rec["context_allocation"] = allocation
                    if allocation.get("reported"):
                        result_rec["allocation_hit"] = bool(allocation.get("hit"))
                        result_rec["outside_reads"] = int(allocation.get("outside_reads") or 0)
                dependency = result.get("dependency_context")
                if isinstance(dependency, dict):
                    result_rec["dependency_context"] = dependency
                    result_rec["dependency_saved_chars"] = int(dependency.get("saved_chars") or 0)
                if isinstance(result.get("escalation"), dict):
                    result_rec["escalation"] = result["escalation"]
                if isinstance(result.get("methods"), list):
                    result_rec["methods"] = [str(v) for v in result["methods"] if str(v)]
                    run_methods.update(result_rec["methods"])
                if isinstance(result.get("trial"), dict):
                    result_rec["trial"] = {"id": str(result["trial"].get("id") or ""),
                                           "variant": str(result["trial"].get("variant") or "")}
                    run_trial = run_trial or result_rec["trial"]
                if store.append_record(result_rec):
                    added += 1
            if run_methods:
                rec["methods"] = sorted(run_methods)
            if run_trial:
                rec["trial"] = run_trial
            if store.append_record(rec):
                added += 1
    return added


def _flow_events_summary(run_dir: str) -> "tuple[int, str]":
    """events/*.jsonl から失敗結果の数と最後の verify verdict を決定的に拾う。"""
    retries = 0
    verify = ""
    for path in sorted(glob.glob(os.path.join(glob.escape(run_dir), "events", "*.jsonl"))):
        for ev in iter_jsonl(path):
            kind = ev.get("kind")
            if kind == "result" and ev.get("status") == "failed":
                retries += 1
            elif kind == "verify" and isinstance(ev.get("verdict"), str):
                verify = ev["verdict"]
    return retries, verify


# -- project-root ------------------------------------------------------------

def collect_project_roots(args, store: Store) -> int:
    added = 0
    for root in _dirs_of(args, "project_roots"):
        runlog = os.path.join(root, "run-log.jsonl")
        if not os.path.isfile(runlog):
            continue
        key = f"project-runlog::{runlog}"
        offset = int(store.cursor(key) or 0)
        with open(runlog, encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            lineno = 0
            for line in f:
                lineno += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                native = str(row.get("run_id") or f"{offset}+{lineno}")
                ts = parse_iso(row.get("ts")) or 0.0
                rec = {
                    "id": record_id("project-runlog", runlog, native),
                    "_epoch": ts, "ts": row.get("ts"),
                    "kind": "run", "source": "project-runlog",
                    "tool": "agent-project", "workload": "project",
                    "node": row.get("node") or "",
                    "ref": native,
                    "status": str(row.get("reason") or ""),
                    "seconds": row.get("duration_s"),
                    "tokens_in": None,
                    "tokens_out": row.get("tokens"),   # @cost は合計トークンのみ（内訳なし）
                    "usd": row.get("cost"),
                    "measured": bool(row.get("tokens")),
                    "done": row.get("done"),
                    "blocked": row.get("blocked"),
                    "escalations": row.get("escalations"),
                }
                if store.append_record(rec):
                    added += 1
            store.set_cursor(key, f.tell())
    return added


# -- amigos-bus --------------------------------------------------------------

def collect_amigos_buses(args, store: Store) -> int:
    added = 0
    for bus in _dirs_of(args, "amigos_buses"):
        missions = os.path.join(bus, "missions")
        roots = sorted(glob.glob(os.path.join(glob.escape(missions), "*"))) \
            if os.path.isdir(missions) else []
        for mdir in roots:
            mid = os.path.basename(mdir)
            terminal = any(os.path.isfile(os.path.join(mdir, n))
                           for n in ("final.json", "cancelled.json"))
            if not terminal:
                continue
            for epath in sorted(glob.glob(os.path.join(glob.escape(mdir), "events", "*.jsonl"))):
                who = os.path.splitext(os.path.basename(epath))[0]
                rid = record_id("amigos-bus", bus, f"{mid}::{who}")
                if store.has_record(rid):
                    continue
                seconds = 0.0
                turns = 0
                last_ts = 0.0
                for ev in iter_jsonl(epath):
                    turns += 1
                    try:
                        seconds += float(ev.get("cli_seconds") or 0.0)
                    except (TypeError, ValueError):
                        pass
                    last_ts = max(last_ts, parse_iso(ev.get("ts")) or 0.0)
                rec = {
                    "id": rid, "_epoch": last_ts or os.path.getmtime(epath),
                    "ts": _iso(last_ts), "kind": "run", "source": "amigos-bus",
                    "tool": "agent-amigos", "workload": "amigos",
                    "ref": f"{mid}/{who}",
                    "status": "done" if os.path.isfile(os.path.join(mdir, "final.json"))
                              else "cancelled",
                    "seconds": seconds, "turns": turns,
                }
                if store.append_record(rec):
                    added += 1
    return added


# -- loop-log ----------------------------------------------------------------

_LOOP_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (.*)$")


def collect_loop_logs(args, store: Store) -> int:
    """agent-loop 本体ログの ERROR 行だけを粗い run レコードにする。
    loop は計測点が薄い（tmux 打鍵で stdout を見ない）という現実をそのまま記録する。"""
    added = 0
    for path in _dirs_of(args, "loop_logs"):
        if not os.path.isfile(path):
            raise SourceError(f"loop_logs はファイルを指定してください: {path}")
        key = f"loop-log::{path}"
        offset = int(store.cursor(key) or 0)
        try:
            if os.path.getsize(path) < offset:
                offset = 0          # ローテートで縮んだら先頭から
        except OSError:
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            for line in f:
                m = _LOOP_LINE_RE.match(line.strip())
                if not m or m.group(2) not in ("ERROR", "WARNING"):
                    continue
                ts = parse_iso(m.group(1).replace(" ", "T") + "Z") or 0.0
                msg = m.group(3)
                tag = ERROR_TAG_RE.search(msg)
                rec = {
                    "id": record_id("loop-log", path, f"{ts}:{msg[:80]}"),
                    "_epoch": ts, "ts": _iso(ts),
                    "kind": "run", "source": "loop-log",
                    "tool": "agent-loop", "workload": "routine",
                    "status": "failed" if m.group(2) == "ERROR" else "warning",
                    "error_class": tag.group(1) if tag else "",
                    "note": msg[:200],
                }
                if store.append_record(rec):
                    added += 1
            store.set_cursor(key, f.tell())
    return added


# -- 相関（読み出し時・決定的。設計 §4.3） -----------------------------------

def correlation_candidates(led: dict, session_recs: "list[dict]", slack_sec: float = 120.0,
                           used: "set[str] | None" = None) -> "list[dict]":
    ts = parse_iso(led.get("ts"))
    if ts is None:
        return []
    try:
        seconds = float(led.get("seconds") or 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    lo, hi = ts - seconds - slack_sec, ts + slack_sec
    candidates = []
    for sess in session_recs:
        if used is not None and sess["id"] in used:
            continue
        if (led.get("agent_cli") or "") != (sess.get("agent_cli") or ""):
            continue
        lm, sm = led.get("model") or "", sess.get("model") or ""
        if lm and sm and lm not in sm and sm not in lm:
            continue
        s0 = parse_iso(sess.get("started_at")) or parse_iso(sess.get("ts"))
        s1 = parse_iso(sess.get("ts"))
        if s0 is None or s1 is None or s1 < lo or s0 > hi:
            continue
        candidates.append(sess)
    return candidates


def correlate(ledger_recs: "list[dict]", session_recs: "list[dict]",
              slack_sec: float = 120.0) -> "dict[str, str]":
    """ledger レコード id → session レコード id の一意対応を返す。

    条件: agent_cli 一致・（両方にあれば）model 一致・セッションの時間範囲が
    実行区間 [ts - seconds - slack, ts + slack] と重なる。複数候補でも終了時刻と
    実行時間から一意に決まる短時間呼び出しだけ結び、それ以外は結合しない。
    records は追記専用なので相関は書き戻さず、読み出しのたびに同じ結果を導く。"""
    links: "dict[str, str]" = {}
    used: "set[str]" = set()
    sessions = sorted(session_recs, key=lambda r: r.get("id") or "")
    for led in sorted(ledger_recs, key=lambda r: (parse_iso(r.get("ts")) or 0.0,
                                                   r.get("id") or "")):
        ts = parse_iso(led.get("ts"))
        if ts is None:
            continue
        try:
            seconds = float(led.get("seconds") or 0.0)
        except (TypeError, ValueError):
            seconds = 0.0
        candidates = correlation_candidates(led, sessions, slack_sec, used)
        chosen = candidates[0] if len(candidates) == 1 else None
        if len(candidates) > 1:
            scored = []
            for sess in candidates:
                s0 = parse_iso(sess.get("started_at")) or parse_iso(sess.get("ts")) or ts
                s1 = parse_iso(sess.get("ts")) or ts
                scored.append((abs(s1 - ts), abs((s1 - s0) - seconds), sess))
            scored.sort(key=lambda item: (item[0], item[1], item[2]["id"]))
            best = scored[0]
            # 密集した短時間呼び出しは広い overlap 窓では必ず複数候補になる。
            # 終了時刻が 2 秒以内で、次点と時刻・長さの組が異なる場合だけ一意とみなす。
            if best[0] <= 2.0 and (len(scored) == 1 or best[:2] != scored[1][:2]):
                chosen = best[2]
        if chosen is not None:
            links[led["id"]] = chosen["id"]
            used.add(chosen["id"])
    return links
