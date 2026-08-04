"""report — Markdown レポート（決定的。設計 §7）。export はスクラバを必ず通す。"""
from __future__ import annotations

import datetime as _dt
import os

from .configfile import resolve_audit_dir
from .scrub import scrub_text
from .stats import aggregate_stats
from .store import Store, home_relative
from .usage import aggregate_usage
from .util import log


def build_report(args, store: Store, kind: str) -> str:
    parts = [f"# agent-audit レポート（{kind}）",
             "",
             f"- 生成: {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
             f"- store: {home_relative(store.root)}",
             ""]
    if kind in ("usage", "all"):
        parts.append("## トークン・コスト（当月・workload 別）\n")
        rows = aggregate_usage(args, store, "month", "workload")
        if rows:
            parts.append("| group | runs | seconds | 実測 in | 実測 out | 推定 tokens | 未計測 | usd |")
            parts.append("|---|--:|--:|--:|--:|--:|--:|--:|")
            for r in rows:
                parts.append(f"| {r['group']} | {r['runs']} | {r['seconds']:.0f} | "
                             f"{r['measured_in']} | {r['measured_out']} | "
                             f"{r['estimated_tokens']} | {r['unmeasured_runs']} | "
                             f"{r['usd']:.2f} |")
        else:
            parts.append("（レコードなし）")
        parts.append("")
    if kind in ("quality", "all"):
        parts.append("## 実行品質（当月）\n")
        data = aggregate_stats(store, "month")
        if data["tools"]:
            for b in data["tools"]:
                statuses = ", ".join(f"{k}={v}" for k, v in sorted(b["status"].items()))
                errors = ", ".join(f"[{k}]={v}" for k, v in sorted(b["error_class"].items()))
                parts.append(f"- **{b['tool']}**: {b['runs']} run（{statuses}）")
                if errors:
                    parts.append(f"  - 失敗内訳: {errors}")
                if b["verify_pass"] or b["verify_fail"]:
                    parts.append(f"  - verify: pass={b['verify_pass']} fail={b['verify_fail']}")
        else:
            parts.append("（run レコードなし）")
        parts.append("")
    if kind in ("insights", "all"):
        parts.append("## 洞察\n")
        insights = list(store.iter_insights())
        if insights:
            for ins in sorted(insights, key=lambda i: (-int(i.get("occurrences") or 0),
                                                       i.get("id") or "")):
                review = ins.get("review") or {}
                verdict = f" / review={review.get('verdict')}" if review else ""
                parts.append(f"- **{ins.get('statement', '')}**")
                parts.append(f"  - kind={ins.get('kind')} / 観測 {ins.get('occurrences')} 件 / "
                             f"confidence={ins.get('confidence')}{verdict}")
                if ins.get("suggested_action"):
                    parts.append(f"  - 提案: {ins['suggested_action']}")
        else:
            parts.append("（洞察なし。agent-audit extract → distill を実行してください）")
        parts.append("")
    return scrub_text("\n".join(parts))


def cmd_report(args) -> int:
    store = Store(resolve_audit_dir(args))
    kind = getattr(args, "kind", None) or "all"
    if kind not in ("usage", "quality", "insights", "all"):
        print("[agent-audit] report: --kind は usage / quality / insights / all")
        return 2
    text = build_report(args, store, kind)
    out = getattr(args, "out", None)
    if not out:
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = os.path.join(store.reports_dir, f"{stamp}-{kind}.md")
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    log("report", f"保存しました: {home_relative(out)}")
    return 0
