"""report — Markdown レポート（決定的。仕様書 §5）。export はスクラバを必ず通す。"""
from __future__ import annotations

import datetime as _dt
import os

from .configfile import resolve_audit_dir
from .scrub import scrub_text
from .stats import aggregate_stats
from .store import Store, home_relative
from .usage import aggregate_usage
from .util import elog, log


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
    if kind in ("knowledge", "all"):
        parts.append("## 記憶層の健全性（3 層 + 共有路）\n")
        parts.extend(knowledge_lines(args, store))
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


def knowledge_lines(args, store: Store) -> "list[str]":
    """記憶層の決定的集計を Markdown 行にする（LLM 不使用。計画 §3.1）。"""
    import time as _time

    from .memory import MemoryStoreError, knowledge_summary
    try:
        summary = knowledge_summary(args, now=_time.time(), store=store)
    except MemoryStoreError as e:
        return [f"（{e}）"]

    lines: "list[str]" = []
    layers = summary["layers"]
    if not layers["ltm"]["configured"]:
        lines.append("- **ltm**: 未収集（`memory_stores.ltm_dirs` が未設定）")
    for s in layers["ltm"]["stores"]:
        growth = "" if s.get("growth_7d") is None else f" / 週次 {s['growth_7d']:+d}"
        access_growth = ("" if s.get("access_growth_7d") is None
                         else f" / 想起週次 {s['access_growth_7d']:+d}")
        lines.append(f"- **ltm** `{s['path']}`: {s['total']} 件"
                     f"（active {s['active']} / archived {s['archived']}）{growth}{access_growth}")
        lines.append(f"  - publish 待ち（share_score >= "
                     f"{summary['thresholds']['share_threshold']} かつ未公開）: "
                     f"**{s['publish_waiting']}** 件")
        lines.append(f"  - 忘却リスク（retention < {summary['thresholds']['retention_risk']}）: "
                     f"{s['forgetting_risk']} 件 / 保持率帯 " +
                     " ".join(f"{k}={v}" for k, v in s["retention_bands"].items()))
        lines.append(f"  - 退役候補（未参照のまま {summary['thresholds']['dormant_days']} 日超）: "
                     f"{s['dormant']} 件（最古 {s['dormant_oldest_days']} 日）")
        lines.append(f"  - 類似クラスタ: {s['similar_clusters']} 塊 "
                     f"（{s['similar_clustered']} 件が重複候補）")
        if s["index_drift"] is None:
            lines.append("  - 索引: 未構築（`build_index.py --force`）")
        elif s["index_drift"]:
            lines.append(f"  - 索引との乖離: {s['index_drift']} 件"
                         f"（索引 {s['index_entries']} / 実ファイル {s['total']}）")

    if not layers["wiki"]["configured"]:
        lines.append("- **wiki**: 未収集（`memory_stores.wiki_root` が未設定）")
    for s in layers["wiki"]["stores"]:
        growth = "" if s.get("growth_7d") is None else f" / 週次 {s['growth_7d']:+d}"
        rate = "—" if s["queries_hit_rate"] is None else f"{s['queries_hit_rate'] * 100:.0f}%"
        lines.append(f"- **wiki** `{s['path']}`: atoms {s['atoms']} / topics {s['topics']}"
                     f"（直近 7 日 新規 {s['new_last_7d']}・更新 {s['updated_last_7d']}）{growth}")
        lines.append(f"  - index 乖離: {s['index_drift']} 件"
                     f"（孤立 {s['index_orphans']} / 実体なし {s['index_missing_files']}）")
        lines.append(f"  - lint 違反: {s['lint_violations']} 件"
                     f"（短小 {s['short_pages']} / リンク切れ {s['dangling_links']}）")
        lines.append(f"  - queries ヒット率: {rate}"
                     f"（{s['queries_hit']}/{s['queries_total']}・0 件 {s['queries_miss']}）")

    if not layers["persona"]["configured"]:
        lines.append("- **persona**: 未収集（`memory_stores.persona_home` が未設定）")
    for s in layers["persona"]["stores"]:
        # persona は件数と滞留日数だけ（本文もタイトルも出さない。C1）
        lines.append(f"- **persona** `{s['path']}`: 未反映の観察ログ {s['pending_updates']} 件"
                     f"（最古 {s['pending_oldest_days']} 日 / 管理ファイル "
                     f"{s['managed_files']}/3）")

    if not layers["moltbook"]["configured"]:
        lines.append("- **moltbook**: 未収集（`memory_stores.moltbook_home` が未設定）")
    for s in layers["moltbook"]["stores"]:
        lines.append(f"- **moltbook** `{s['path']}`: outbox 滞留 **{s['outbox_pending']}** 件"
                     f"（最古 {s['outbox_oldest_days']} 日）/ 公開済み {s['published_local']} 件"
                     f" / inbox 未取込 {s['inbox_pending']} 件")
        lines.append(f"  - 未収集（ローカルからは測れない）: "
                     f"{', '.join(s['uncollected'])}")
    return lines


def knowledge_json(args, store: Store) -> dict:
    """dashboard・当番プロンプトが読む JSON（スクラバを通す。仕様書 §5）。"""
    import time as _time

    from .memory import MemoryStoreError, knowledge_summary
    from .scrub import scrub_obj
    try:
        return scrub_obj(knowledge_summary(args, now=_time.time(), store=store))
    except MemoryStoreError as e:
        return {"error": str(e)}


def cmd_report(args) -> int:
    store = Store(resolve_audit_dir(args))
    kind = getattr(args, "kind", None) or "all"
    if kind not in ("usage", "quality", "knowledge", "insights", "all"):
        print("[agent-audit] report: --kind は usage / quality / knowledge / insights / all")
        return 2
    if getattr(args, "json", False) and kind != "knowledge":
        print("[agent-audit] report: --json は --kind knowledge のときだけ使えます")
        return 2
    if kind in ("knowledge", "all"):
        # 設定されたストアが読めなければ fail-close（不変条件 3）——集計を黙って
        # 部分値で出さない。未設定のストアは「未収集」として本文に出る。
        import time as _time

        from .memory import MemoryStoreError, knowledge_summary
        try:
            knowledge_summary(args, now=_time.time())
        except MemoryStoreError as e:
            elog(f"report: {e}")
            return 2
    if getattr(args, "json", False):
        import json as _json
        print(_json.dumps(knowledge_json(args, store), ensure_ascii=False, indent=2))
        return 0
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
