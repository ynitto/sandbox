"""stats — 実行品質の決定的集計（設計 §5.2。LLM 不使用）。"""
from __future__ import annotations

import json

from .configfile import resolve_audit_dir
from .scrub import scrub_obj
from .store import Store
from .collect import correlate
from .usage import _period_floor, _rate_for, _rates, load_period_records
from .util import parse_iso


def aggregate_stats(store: Store, period: str) -> dict:
    _ledger, _session, runs = load_period_records(store, period)
    by_tool: "dict[str, dict]" = {}
    by_decision: "dict[str, dict]" = {}
    for rec in runs:
        tool = rec.get("tool") or "(不明)"
        b = by_tool.setdefault(tool, {
            "tool": tool, "runs": 0, "status": {}, "error_class": {},
            "retries": 0, "verify_pass": 0, "verify_fail": 0, "escalations": 0})
        b["runs"] += 1
        status = str(rec.get("status") or "(なし)")
        b["status"][status] = b["status"].get(status, 0) + 1
        ec = str(rec.get("error_class") or "")
        if ec:
            b["error_class"][ec] = b["error_class"].get(ec, 0) + 1
        try:
            b["retries"] += int(rec.get("retries") or 0)
        except (TypeError, ValueError):
            pass
        verify = str(rec.get("verify") or "")
        if verify == "pass":
            b["verify_pass"] += 1
        elif verify == "fail":
            b["verify_fail"] += 1
        try:
            b["escalations"] += int(rec.get("escalations") or 0)
        except (TypeError, ValueError):
            pass
        for item in rec.get("decision_comparisons") or []:
            if not isinstance(item, dict) or not item.get("decision"):
                continue
            name = str(item["decision"])
            d = by_decision.setdefault(name, {"decision": name, "samples": 0, "matches": 0})
            d["samples"] += 1
            d["matches"] += int(item.get("agree") is True)
    decisions = []
    for d in sorted(by_decision.values(), key=lambda d: d["decision"]):
        decisions.append({**d, "agreement_rate": round(d["matches"] / d["samples"], 4)})
    return {"period": period, "tools": sorted(by_tool.values(), key=lambda b: b["tool"]),
            "decisions": decisions}


def cmd_stats(args) -> int:
    store = Store(resolve_audit_dir(args))
    period = getattr(args, "period", None) or "month"
    data = aggregate_stats(store, period)
    if getattr(args, "json", False):
        print(json.dumps(scrub_obj(data), ensure_ascii=False, indent=1))
        return 0
    print(f"実行品質集計（period={period}）")
    if not data["tools"]:
        print("（run レコードがありません。まず agent-audit collect を実行してください）")
        return 0
    for b in data["tools"]:
        print(f"\n{b['tool']}: {b['runs']} run")
        for status, n in sorted(b["status"].items()):
            print(f"  status {status}: {n}")
        for ec, n in sorted(b["error_class"].items()):
            print(f"  error [{ec}]: {n}")
        if b["retries"]:
            print(f"  リトライ合計: {b['retries']}")
        if b["verify_pass"] or b["verify_fail"]:
            print(f"  verify: pass={b['verify_pass']} fail={b['verify_fail']}")
        if b["escalations"]:
            print(f"  needs エスカレーション: {b['escalations']}")
    if data["decisions"]:
        print("\nLLM判断と決定的ルールの一致率（計測のみ）")
        for d in data["decisions"]:
            print(f"  {d['decision']}: {d['agreement_rate']:.1%} "
                  f"({d['matches']}/{d['samples']})")
    return 0


def aggregate_ratings(args, store: Store, period: str) -> "list[dict]":
    """仕事種別×モデルで、台帳の消費と flow の**ノード単位**の結末を結合する。

    品質の入力は result レコードの `status`（done / failed）で、run 全体の統一 verify
    （`run_verify`）ではない。統一 verify は run に 1 回・node id 無しで出るので、それを
    ノードへ配ると planner とワーカーが別モデルでも同じ判定を貰い、モデル別の PASS 率が
    測れない。ここが測れないと F4 の格付けは印象に戻る。"""
    ledger, sessions, _runs = load_period_records(store, period)
    links = correlate(ledger, sessions, slack_sec=float(getattr(args, "join_slack_sec", 120.0)))
    sess_by_id = {s["id"]: s for s in sessions}
    default_rate, per_cli = _rates(args)
    groups: "dict[tuple[str, str], dict]" = {}

    def bucket(purpose: str, model: str) -> dict:
        key = (purpose or "(なし)", model or "(不明)")
        return groups.setdefault(key, {
            "purpose": key[0], "model": key[1], "usage_runs": 0, "total_tokens": 0.0,
            "outcome_runs": 0, "outcome_ok": 0,
        })

    for led in ledger:
        purpose = str(led.get("purpose") or led.get("ref") or "")
        model = str(led.get("model") or led.get("agent_cli") or "")
        b = bucket(purpose, model)
        b["usage_runs"] += 1
        sess = sess_by_id.get(links.get(led["id"], ""))
        tin, tout = led.get("tokens_in"), led.get("tokens_out")
        if sess and sess.get("measured"):
            tin = sess.get("tokens_in") if sess.get("tokens_in") is not None else tin
            tout = sess.get("tokens_out") if sess.get("tokens_out") is not None else tout
        if tin is not None or tout is not None:
            b["total_tokens"] += max(0, int(tin or 0)) + max(0, int(tout or 0))
        else:
            rate = _rate_for(str(led.get("agent_cli") or ""), str(led.get("model") or ""),
                             default_rate, per_cli)
            try:
                b["total_tokens"] += max(0.0, float(led.get("seconds") or 0.0)) * rate
            except (TypeError, ValueError):
                pass

    floor = _period_floor(period)
    for rec in store.iter_records(since_epoch=floor):
        if rec.get("kind") != "result":
            continue
        ts = parse_iso(rec.get("ts"))
        if floor and (ts is None or ts < floor):
            continue
        outcome = str(rec.get("status") or "")
        if outcome not in ("done", "failed"):
            continue
        b = bucket(str(rec.get("purpose") or ""),
                   str(rec.get("model") or rec.get("agent_cli") or ""))
        b["outcome_runs"] += 1
        b["outcome_ok"] += int(outcome == "done")

    rows = []
    for b in groups.values():
        usage_runs = b["usage_runs"]
        outcome_runs = b["outcome_runs"]
        rows.append({
            **b,
            "average_tokens": round(b["total_tokens"] / usage_runs, 1) if usage_runs else None,
            "pass_rate": round(b["outcome_ok"] / outcome_runs, 4) if outcome_runs else None,
        })
    out = []
    for purpose in sorted({r["purpose"] for r in rows}):
        ranked = sorted(
            (r for r in rows if r["purpose"] == purpose),
            key=lambda r: (-(r["pass_rate"] if r["pass_rate"] is not None else -1),
                           r["average_tokens"] if r["average_tokens"] is not None else float("inf"),
                           r["model"]),
        )
        for rank, row in enumerate(ranked, 1):
            out.append({"rank": rank, **row})
    return out


def cmd_ratings(args) -> int:
    store = Store(resolve_audit_dir(args))
    period = getattr(args, "period", None) or "month"
    rows = aggregate_ratings(args, store, period)
    data = {"period": period, "rows": rows}
    if getattr(args, "json", False):
        print(json.dumps(scrub_obj(data), ensure_ascii=False, indent=1))
        return 0
    print(f"品質×消費格付け（period={period}）")
    print(f"{'#':>2} {'purpose':<16} {'model':<24} {'PASS':>7} {'avg tokens':>12} {'n':>5}")
    for r in rows:
        pass_text = f"{r['pass_rate']:.1%}" if r["pass_rate"] is not None else "-"
        avg_text = f"{r['average_tokens']:.1f}" if r["average_tokens"] is not None else "-"
        print(f"{r['rank']:>2} {r['purpose']:<16} {r['model']:<24} {pass_text:>7} "
              f"{avg_text:>12} {r['outcome_runs']:>5}")
    if not rows:
        print("（消費または結果レコードがありません）")
    return 0
