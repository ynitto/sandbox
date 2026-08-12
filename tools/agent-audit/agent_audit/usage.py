"""usage / calibrate — トークン・コスト集計と rates 較正（決定的。設計 §5.1・§5.3）。

measured（実測: セッションログ由来 or 台帳の実測行）と estimated（rates 推定）を
**別々の列で数える**——混ぜた単一の数字は出さない（不変条件 3）。
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import statistics

from .collect import correlate, correlation_candidates
from .configfile import resolve_audit_dir, resolve_budget_dir
from .scrub import scrub_obj
from .store import Store, record_id
from .util import iter_jsonl, log, now_iso, parse_iso, read_json, write_json_atomic

GROUP_KEYS = ("workload", "tool", "agent_cli", "model", "purpose", "ref", "node")
QUOTA_RATE_LIMIT_TTL_SEC = 3600


def _period_floor(period: str, now: "_dt.datetime | None" = None) -> float:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return 0.0


def _next_period_reset(period: str, now: _dt.datetime) -> str:
    """node-budget の UTC 期間が次に切り替わる時刻。total は期限なし。"""
    if period == "day":
        reset = now.replace(hour=0, minute=0, second=0, microsecond=0) \
            + _dt.timedelta(days=1)
    elif period == "month":
        reset = (now.replace(day=28, hour=0, minute=0, second=0, microsecond=0)
                 + _dt.timedelta(days=4)).replace(day=1)
    else:
        return ""
    return reset.isoformat().replace("+00:00", "Z")


def aggregate_agent_limits(args, store: Store, rows: "list[dict]", *, rows_period: str = "",
                           now: "_dt.datetime | None" = None) -> "list[dict]":
    """CLI 別の宣言上限と、最新の quota 観測・復帰予定を同じ行へ畳む。"""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    now_epoch = now.timestamp()
    cfg = read_json(os.path.join(resolve_budget_dir(args), "config.json")) or {}
    period = cfg.get("period") if cfg.get("period") in ("day", "month", "total") else "day"
    agents = ((cfg.get("allocation") or {}).get("agents") or {})
    agents = agents if isinstance(agents, dict) else {}
    latest: "dict[str, tuple[float, dict]]" = {}
    floor = _period_floor(period, now)
    records = list(store.iter_records(since_epoch=floor))
    ledger_dir = os.path.join(resolve_budget_dir(args), "ledger")
    for path in sorted(glob.glob(os.path.join(glob.escape(ledger_dir), "*.jsonl"))):
        records.extend(iter_jsonl(path))
    for rec in records:
        if rec.get("event") not in ("quota", "quota_snapshot"):
            continue
        cli = str(rec.get("agent_cli") or "")
        ts = parse_iso(rec.get("ts"))
        if not cli or ts is None or (floor and ts < floor):
            continue
        if cli not in latest or ts >= latest[cli][0]:
            latest[cli] = (ts, rec)

    if rows_period and rows_period != period:
        rows = aggregate_usage(args, store, period, "agent_cli")
    used = {str(row.get("group") or ""): int(
        (row.get("measured_in") or 0) + (row.get("measured_out") or 0)
        + (row.get("estimated_tokens") or 0)) for row in rows}
    out = []
    for cli in sorted(set(agents) | set(latest) | set(used)):
        spec = agents.get(cli) if isinstance(agents.get(cli), dict) else {}
        try:
            max_tokens = max(0, int(spec.get("max_tokens") or 0))
        except (TypeError, ValueError):
            max_tokens = 0
        event = latest.get(cli, (None, {}))[1]
        kind = str(event.get("quota_kind") or "")
        try:
            quota_used_percent = max(0, min(100, int(event.get("quota_used_percent")))) \
                if event.get("quota_used_percent") is not None else None
        except (TypeError, ValueError):
            quota_used_percent = None
        quota_source = str(event.get("quota_source") or "")
        reset_at = str(event.get("reset_at") or "")
        reset_estimated = False
        reset_source = "observed" if reset_at else ""
        reset_epoch = parse_iso(reset_at)
        if kind == "rate_limit" and reset_epoch is None:
            observed = parse_iso(event.get("ts"))
            if observed is not None:
                reset_epoch = observed + QUOTA_RATE_LIMIT_TTL_SEC
                reset_at = _dt.datetime.fromtimestamp(
                    reset_epoch, _dt.timezone.utc).isoformat().replace("+00:00", "Z")
                reset_estimated = True
                reset_source = "estimated"
        if reset_epoch is not None and reset_epoch <= now_epoch:
            kind = ""
            reset_at = ""
            reset_source = ""
        if not reset_at and (max_tokens > 0 or kind == "exhausted"):
            reset_at = _next_period_reset(period, now)
            reset_source = "period" if reset_at else ""
        used_tokens = used.get(cli, 0)
        blocked = bool(kind) or (max_tokens > 0 and used_tokens >= max_tokens)
        out.append({
            "agent_cli": cli, "period": period, "max_tokens": max_tokens,
            "used_tokens": used_tokens,
            "remaining_tokens": max(0, max_tokens - used_tokens) if max_tokens else None,
            "quota_kind": kind or None, "observed_at": event.get("ts") or None,
            "quota_used_percent": quota_used_percent,
            "quota_source": quota_source or None,
            "quota_supported": cli in ("claude", "codex", "copilot", "kiro") or bool(quota_source),
            "reset_at": reset_at or None, "reset_estimated": reset_estimated,
            "reset_source": reset_source or None,
            "blocked": blocked,
        })
    return out


def load_period_records(store: Store, period: str) -> "tuple[list[dict], list[dict], list[dict]]":
    """(ledger, session, run) レコードを期間で絞って読む。"""
    floor = _period_floor(period)
    ledger, run = [], []
    sessions: "dict[tuple[str, str], tuple[tuple[int, float, int], dict]]" = {}
    for rec in store.iter_records(since_epoch=floor):
        ts = parse_iso(rec.get("ts"))
        if floor and (ts is None or ts < floor):
            continue
        kind = rec.get("kind")
        if kind == "ledger":
            ledger.append(rec)
        elif kind in ("session", "session-usage"):
            key = (str(rec.get("agent_cli") or ""), str(rec.get("session_id") or rec.get("id") or ""))
            try:
                revision = int(rec.get("parser_revision") or 0)
            except (TypeError, ValueError):
                revision = 0
            rank = (revision, parse_iso(rec.get("ts")) or 0.0,
                    1 if kind == "session-usage" else 0)
            if key not in sessions or rank > sessions[key][0]:
                normalized = dict(rec)
                normalized["kind"] = "session"
                sessions[key] = (rank, normalized)
        elif kind == "run":
            run.append(rec)
    return ledger, [item[1] for item in sessions.values()], run


def _is_llm_ledger(led: dict) -> bool:
    kind = led.get("usage_kind")
    if kind:
        return kind == "llm"
    if led.get("tool") == "agent-audit":
        return (led.get("purpose") or led.get("ref")) in ("extract", "distill", "review")
    return True


def _operation_key(led: dict) -> tuple[str, str, str, str]:
    return (str(led.get("tool") or ""), str(led.get("purpose") or led.get("ref") or ""),
            str(led.get("agent_cli") or ""), str(led.get("model") or ""))


def _rates(args) -> "tuple[float, dict]":
    cfg = read_json(os.path.join(resolve_budget_dir(args), "config.json")) or {}
    rates = cfg.get("rates") or {}
    default = float(rates.get("default_tokens_per_second") or 0.0)
    per_cli = rates.get("per_cli") or {}
    return default, per_cli if isinstance(per_cli, dict) else {}


def _rate_for(cli: str, model: str, default: float, per_cli: dict) -> float:
    for key in (f"{cli}:{model}" if model else "", cli):
        if key and isinstance(per_cli.get(key), (int, float)):
            return float(per_cli[key])
    return default


def aggregate_usage(args, store: Store, period: str, by: str) -> "list[dict]":
    """行の構成: 台帳行（linked セッションで実測を裏取り）+ 未結合セッション行。
    linked セッションは台帳行の実測へ吸収し二重計上しない。"""
    ledger, session, _run = load_period_records(store, period)
    links = correlate(ledger, session, slack_sec=float(getattr(args, "join_slack_sec", 120.0)))
    sess_by_id = {s["id"]: s for s in session}
    default_rate, per_cli = _rates(args)

    operation_samples: "dict[tuple[str, str, str, str], list[int]]" = {}
    for led in ledger:
        if led.get("tool") != "agent-audit":
            continue
        sess = sess_by_id.get(links.get(led["id"], ""))
        if not sess or not sess.get("measured"):
            continue
        total = int(sess.get("tokens_in") or 0) + int(sess.get("tokens_out") or 0)
        if total > 0:
            operation_samples.setdefault(_operation_key(led), []).append(total)

    groups: "dict[str, dict]" = {}

    def bucket(key: str) -> dict:
        return groups.setdefault(key or "(なし)", {
            "group": key or "(なし)", "runs": 0, "seconds": 0.0,
            "measured_in": 0, "measured_out": 0, "estimated_tokens": 0,
            "unmeasured_runs": 0, "usd": 0.0})

    linked_sessions = set(links.values())
    for led in ledger:
        sess = sess_by_id.get(links.get(led["id"], ""))
        if by == "purpose":
            group = led.get("purpose") or led.get("ref")
        elif by in ("agent_cli", "model"):
            group = led.get(by) or (sess or {}).get(by)
        else:
            group = led.get(by)
        b = bucket(str(group or ""))
        b["runs"] += 1
        try:
            b["seconds"] += float(led.get("seconds") or 0.0)
        except (TypeError, ValueError):
            pass
        try:
            b["usd"] += float(led.get("usd") or 0.0)
        except (TypeError, ValueError):
            pass
        tin, tout = led.get("tokens_in"), led.get("tokens_out")
        if sess and sess.get("measured"):
            tin = sess.get("tokens_in") if sess.get("tokens_in") is not None else tin
            tout = sess.get("tokens_out") if sess.get("tokens_out") is not None else tout
        if tin is not None or tout is not None:
            b["measured_in"] += int(tin or 0)
            b["measured_out"] += int(tout or 0)
        else:
            b["unmeasured_runs"] += 1
            if not _is_llm_ledger(led):
                continue
            # 相関が曖昧でも近傍に実測セッションがあるなら、未帰属の実測行として後段で
            # 数える。ここでも秒レートを足すと同じ呼び出しを実測＋推定で二重計上する。
            if any(s.get("measured") for s in correlation_candidates(
                    led, session, float(getattr(args, "join_slack_sec", 120.0)))):
                continue
            if led.get("tool") == "agent-audit":
                samples = operation_samples.get(_operation_key(led), [])
                if len(samples) >= 3:
                    b["estimated_tokens"] += int(statistics.median(samples))
                continue
            rate = _rate_for(led.get("agent_cli") or "", led.get("model") or "",
                             default_rate, per_cli)
            try:
                sec = float(led.get("seconds") or 0.0)
            except (TypeError, ValueError):
                sec = 0.0
            if rate > 0 and sec > 0:
                b["estimated_tokens"] += int(sec * rate)
    for sess in session:
        if sess["id"] in linked_sessions:
            continue
        if not sess.get("measured"):
            continue
        key = str(sess.get(by) or "") if by != "workload" else "(session)"
        b = bucket(key)
        b["runs"] += 1
        b["measured_in"] += int(sess.get("tokens_in") or 0)
        b["measured_out"] += int(sess.get("tokens_out") or 0)
    return sorted(groups.values(), key=lambda g: g["group"])


def cmd_usage(args) -> int:
    store = Store(resolve_audit_dir(args))
    period = getattr(args, "period", None) or "month"
    by = getattr(args, "by", None) or "workload"
    if by not in GROUP_KEYS:
        print(f"[agent-audit] usage: --by は {', '.join(GROUP_KEYS)} から選んでください")
        return 2
    rows = aggregate_usage(args, store, period, by)
    limits = aggregate_agent_limits(
        args, store, rows, rows_period=period) if by == "agent_cli" else []
    if getattr(args, "json", False):
        payload = {"period": period, "by": by, "rows": rows}
        if by == "agent_cli":
            payload["agent_limits"] = limits
        print(json.dumps(scrub_obj(payload),
                         ensure_ascii=False, indent=1))
        return 0
    print(f"トークン・コスト集計（period={period} / by={by}）")
    header = f"{'group':<24} {'runs':>6} {'seconds':>10} {'実測in':>10} {'実測out':>9} " \
             f"{'推定tokens':>10} {'未計測':>6} {'usd':>8}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['group']:<24} {r['runs']:>6} {r['seconds']:>10.1f} {r['measured_in']:>10} "
              f"{r['measured_out']:>9} {r['estimated_tokens']:>10} {r['unmeasured_runs']:>6} "
              f"{r['usd']:>8.2f}")
    if not rows:
        print("（レコードがありません。まず agent-audit collect を実行してください）")
    if limits:
        print("\nCLI 利用枠（node-budget 宣言 + quota 観測）")
        for item in limits:
            cap = str(item["max_tokens"]) if item["max_tokens"] else "未設定"
            reset = item["reset_at"] or "期限なし"
            state = item["quota_kind"] or ("上限到達" if item["blocked"] else "利用可")
            print(f"  {item['agent_cli']}: 上限 {cap} / {state} / 更新・復帰 {reset}")
    return 0


# -- calibrate（rates 較正の管理面実装。設計 §5.3） ---------------------------

def calibration_rates(args, store: Store) -> "dict[str, float]":
    """実測（tokens と seconds が揃った）行から tokens/秒 の中央値を <cli>:<model> と
    <cli> の両粒度で導く。"""
    ledger, session, _run = load_period_records(store, "total")
    links = correlate(ledger, session, slack_sec=float(getattr(args, "join_slack_sec", 120.0)))
    sess_by_id = {s["id"]: s for s in session}
    samples: "dict[str, list[float]]" = {}
    for led in ledger:
        if led.get("tool") == "agent-audit":
            continue      # 短い段別呼び出しは aggregate_usage の operation 中央値で扱う
        try:
            sec = float(led.get("seconds") or 0.0)
        except (TypeError, ValueError):
            sec = 0.0
        tin, tout = led.get("tokens_in"), led.get("tokens_out")
        sess = sess_by_id.get(links.get(led["id"], ""))
        if sess and sess.get("measured"):
            tin = sess.get("tokens_in") if sess.get("tokens_in") is not None else tin
            tout = sess.get("tokens_out") if sess.get("tokens_out") is not None else tout
            started, ended = parse_iso(sess.get("started_at")), parse_iso(sess.get("ts"))
            if started is not None and ended is not None and ended > started:
                sec = ended - started
        if sec <= 0:
            continue
        total = int(tin or 0) + int(tout or 0)
        if total <= 0:
            continue
        cli = led.get("agent_cli") or ""
        model = led.get("model") or ""
        if not cli:
            continue
        rate = total / sec
        if model:
            samples.setdefault(f"{cli}:{model}", []).append(rate)
        samples.setdefault(cli, []).append(rate)
    return {key: round(statistics.median(vals), 1) for key, vals in sorted(samples.items())}


def cmd_calibrate(args) -> int:
    store = Store(resolve_audit_dir(args))
    rates = calibration_rates(args, store)
    if not rates:
        print("[agent-audit] calibrate: 実測（tokens + seconds）の揃った行がまだありません。")
        return 0
    print("rates 提案（tokens/秒 の中央値・実測行より）:")
    for key, val in rates.items():
        print(f"  {key}: {val}")
    if not getattr(args, "write", False):
        print("反映するには --write を付けてください"
              "（budget config.json の rates.per_cli を更新します）。")
        return 0
    default_rate, prior_rates = _rates(args)
    ts = now_iso()
    for key, measured_rate in rates.items():
        cli, _, model = key.partition(":")
        prior_rate = _rate_for(cli, model, default_rate, prior_rates)
        store.append_record({
            "id": record_id("calibration", "rates", f"{ts}:{key}"),
            "ts": ts,
            "kind": "calibration",
            "agent_cli": cli,
            "model": model,
            "estimated_tokens_per_second": prior_rate if prior_rate > 0 else None,
            "measured_tokens_per_second": measured_rate,
            "delta_ratio": (round((measured_rate - prior_rate) / prior_rate, 4)
                            if prior_rate > 0 else None),
        })
    store.save_state()
    cfg_path = os.path.join(resolve_budget_dir(args), "config.json")
    cfg = read_json(cfg_path) or {}
    r = cfg.setdefault("rates", {})
    per = r.setdefault("per_cli", {})
    per.update(rates)
    cfg["updated_at"] = now_iso()
    cfg["updated_by"] = "agent-audit"
    write_json_atomic(cfg_path, cfg)
    log("calibrate", f"rates.per_cli を {len(rates)} 件更新しました: {cfg_path}")
    return 0
