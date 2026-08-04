"""usage / calibrate — トークン・コスト集計と rates 較正（決定的。設計 §5.1・§5.3）。

measured（実測: セッションログ由来 or 台帳の実測行）と estimated（rates 推定）を
**別々の列で数える**——混ぜた単一の数字は出さない（不変条件 3）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import statistics

from .collect import correlate
from .configfile import resolve_audit_dir, resolve_budget_dir
from .scrub import scrub_obj
from .store import Store
from .util import log, now_iso, parse_iso, read_json, write_json_atomic

GROUP_KEYS = ("workload", "tool", "agent_cli", "model", "ref", "node")


def _period_floor(period: str) -> float:
    now = _dt.datetime.now(_dt.timezone.utc)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return 0.0


def load_period_records(store: Store, period: str) -> "tuple[list[dict], list[dict], list[dict]]":
    """(ledger, session, run) レコードを期間で絞って読む。"""
    floor = _period_floor(period)
    ledger, session, run = [], [], []
    for rec in store.iter_records(since_epoch=floor):
        ts = parse_iso(rec.get("ts"))
        if floor and (ts is None or ts < floor):
            continue
        kind = rec.get("kind")
        if kind == "ledger":
            ledger.append(rec)
        elif kind == "session":
            session.append(rec)
        elif kind == "run":
            run.append(rec)
    return ledger, session, run


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

    groups: "dict[str, dict]" = {}

    def bucket(key: str) -> dict:
        return groups.setdefault(key or "(なし)", {
            "group": key or "(なし)", "runs": 0, "seconds": 0.0,
            "measured_in": 0, "measured_out": 0, "estimated_tokens": 0,
            "unmeasured_runs": 0, "usd": 0.0})

    linked_sessions = set(links.values())
    for led in ledger:
        b = bucket(str(led.get(by) or ""))
        b["runs"] += 1
        try:
            b["seconds"] += float(led.get("seconds") or 0.0)
        except (TypeError, ValueError):
            pass
        try:
            b["usd"] += float(led.get("usd") or 0.0)
        except (TypeError, ValueError):
            pass
        sess = sess_by_id.get(links.get(led["id"], ""))
        tin, tout = led.get("tokens_in"), led.get("tokens_out")
        if sess and sess.get("measured"):
            tin = sess.get("tokens_in") if sess.get("tokens_in") is not None else tin
            tout = sess.get("tokens_out") if sess.get("tokens_out") is not None else tout
        if tin is not None or tout is not None:
            b["measured_in"] += int(tin or 0)
            b["measured_out"] += int(tout or 0)
        else:
            rate = _rate_for(led.get("agent_cli") or "", led.get("model") or "",
                             default_rate, per_cli)
            try:
                sec = float(led.get("seconds") or 0.0)
            except (TypeError, ValueError):
                sec = 0.0
            if rate > 0 and sec > 0:
                b["estimated_tokens"] += int(sec * rate)
            else:
                b["unmeasured_runs"] += 1
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
    if getattr(args, "json", False):
        print(json.dumps(scrub_obj({"period": period, "by": by, "rows": rows}),
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
        try:
            sec = float(led.get("seconds") or 0.0)
        except (TypeError, ValueError):
            continue
        if sec <= 0:
            continue
        tin, tout = led.get("tokens_in"), led.get("tokens_out")
        sess = sess_by_id.get(links.get(led["id"], ""))
        if sess and sess.get("measured"):
            tin = sess.get("tokens_in") if sess.get("tokens_in") is not None else tin
            tout = sess.get("tokens_out") if sess.get("tokens_out") is not None else tout
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
