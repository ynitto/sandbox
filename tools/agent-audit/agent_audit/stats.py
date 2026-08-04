"""stats — 実行品質の決定的集計（設計 §5.2。LLM 不使用）。"""
from __future__ import annotations

import json

from .configfile import resolve_audit_dir
from .scrub import scrub_obj
from .store import Store
from .usage import load_period_records


def aggregate_stats(store: Store, period: str) -> dict:
    _ledger, _session, runs = load_period_records(store, period)
    by_tool: "dict[str, dict]" = {}
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
    return {"period": period, "tools": sorted(by_tool.values(), key=lambda b: b["tool"])}


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
    return 0
