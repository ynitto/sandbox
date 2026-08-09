"""collect — 源泉の増分収集と正規化（決定的・LLM 不使用。設計 §4）。

すべて読み取り専用・増分・冪等。カーソルと収集済み管理は store.state.json。
源泉の場所は 引数 / 設定 > 契約上の既定パス で解決し、環境変数は見ない。
明示設定された源泉が読めないときは fail-close（exit 2）——黙って部分集計を
全体と偽らない（不変条件 3）。
"""
from __future__ import annotations

import glob
import json
import os
import re

from .configfile import resolve_budget_dir
from .store import Store, home_relative, record_id
from .util import elog, iter_jsonl, log, now_iso, parse_iso, read_json

ERROR_TAG_RE = re.compile(r"\[agent-error:([a-z]+)\]")

KNOWN_SOURCES = ("budget-ledger", "cli-native", "flow-bus", "project-root",
                 "amigos-bus", "loop-log")


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
                if row.get("quota_kind"):
                    continue    # 消費ではなく quota 観測の行（node-budget 契約）。集計に混ぜない
                ts = parse_iso(row.get("ts")) or 0.0
                rec = {
                    "id": record_id("budget-ledger", path, f"{offset}+{lineno}"),
                    "_epoch": ts,
                    "ts": row.get("ts"),
                    "kind": "ledger",
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
            key = f"cli-native::{name}::{sess['store']}::{sess['native_id']}"
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
            }
            if with_transcripts and sess["messages"]:
                rec["excerpt_ref"] = _write_transcript(store, name, sess)
            if store.append_record(rec):
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
                if store.append_record(result_rec):
                    added += 1
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

def correlate(ledger_recs: "list[dict]", session_recs: "list[dict]",
              slack_sec: float = 120.0) -> "dict[str, str]":
    """ledger レコード id → session レコード id の一意対応を返す。

    条件: agent_cli 一致・（両方にあれば）model 一致・セッションの時間範囲が
    実行区間 [ts - seconds - slack, ts + slack] と重なる。候補が複数 / ゼロなら
    結合しない（偽の実測を作らない）。records は追記専用なので相関は書き戻さず、
    読み出しのたびに同じ入力から同じ結果を導く。"""
    links: "dict[str, str]" = {}
    used: "set[str]" = set()
    sessions = sorted(session_recs, key=lambda r: r.get("id") or "")
    for led in sorted(ledger_recs, key=lambda r: r.get("id") or ""):
        ts = parse_iso(led.get("ts"))
        if ts is None:
            continue
        try:
            seconds = float(led.get("seconds") or 0.0)
        except (TypeError, ValueError):
            seconds = 0.0
        lo, hi = ts - seconds - slack_sec, ts + slack_sec
        candidates = []
        for sess in sessions:
            if sess["id"] in used:
                continue
            if (led.get("agent_cli") or "") != (sess.get("agent_cli") or ""):
                continue
            lm, sm = led.get("model") or "", sess.get("model") or ""
            if lm and sm and lm not in sm and sm not in lm:
                continue
            s0 = parse_iso(sess.get("started_at")) or parse_iso(sess.get("ts"))
            s1 = parse_iso(sess.get("ts"))
            if s0 is None or s1 is None:
                continue
            if s1 < lo or s0 > hi:
                continue
            candidates.append(sess)
        if len(candidates) == 1:
            links[led["id"]] = candidates[0]["id"]
            used.add(candidates[0]["id"])
    return links
