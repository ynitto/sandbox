#!/usr/bin/env python3
"""メトリクスの可視化レポートを出力する。

使い方:
    python metrics_report.py                                 # 全スキルサマリテーブル
    python metrics_report.py --skill <name> --detail         # 特定スキルの詳細
    python metrics_report.py --co-occurrence                 # 共起ヒートマップ
    python metrics_report.py --output metrics-report.md      # Markdown ファイル出力

JSONL ログ (metrics-log.jsonl) と レジストリ (skill-registry.json) の
両方を参照して可視化する。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

# 同一パッケージから集計ロジックを借用
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
from metrics_collector import (  # noqa: E402
    load_events,
    compute_skill_metrics,
    aggregate_all,
)


# registry.py の __file__ ベースのパス解決を利用（metrics_collector 経由で既に import 済み）
from registry import _agent_home, _registry_path


def _load_registry() -> dict:
    path = _registry_path()
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ───────────────────────────────────────────
# ASCII バーチャート
# ───────────────────────────────────────────

def _bar(value: float, max_val: float = 1.0, width: int = 10) -> str:
    """0〜max_val の値を ASCII バーに変換する。"""
    filled = int(round(value / max_val * width)) if max_val > 0 else 0
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _pct(value: float) -> str:
    return f"{value:.0%}"


# ───────────────────────────────────────────
# サマリテーブル
# ───────────────────────────────────────────

def render_summary_table(aggregated: dict[str, dict]) -> str:
    """全スキルのサマリをテーブル形式で返す。"""
    lines = [
        "📊 スキル実行メトリクス サマリ",
        "━" * 80,
        f"{'スキル名':<30} {'回数':>4}  {'成功率':>6}  {'平均時間':>8}  "
        f"{'P90':>6}  {'SA平均':>6}  {'7d回数':>4}  {'7d率':>5}",
        "─" * 80,
    ]

    for name in sorted(aggregated.keys()):
        m = aggregated[name]
        total = m["total_executions"]
        ok = m["ok_rate"]
        dur = m.get("avg_duration_sec")
        p90 = m.get("p90_duration_sec")
        sub = m.get("avg_subagent_calls")
        t7d = m.get("trend_7d", {})
        t7d_exec = t7d.get("executions", 0)
        t7d_ok = t7d.get("ok_rate", 0.0)

        dur_s = f"{dur:>6.1f}s" if dur is not None else "     -"
        p90_s = f"{p90:>4.1f}s" if p90 is not None else "    -"
        sub_s = f"{sub:>4.1f}" if sub is not None else "   -"

        lines.append(
            f"  {name:<28} {total:>4}  {ok:>5.0%}  {dur_s}  "
            f"{p90_s}  {sub_s}  {t7d_exec:>4}  {_pct(t7d_ok):>5}"
        )

    lines.append("━" * 80)
    lines.append(f"  合計 {sum(m['total_executions'] for m in aggregated.values())} 回実行")
    return "\n".join(lines)


# ───────────────────────────────────────────
# 詳細レポート
# ───────────────────────────────────────────

def render_detail(skill_name: str, events: list[dict], metrics: dict) -> str:
    """特定スキルの詳細レポートを返す。"""
    total = metrics["total_executions"]
    ok_rate = metrics["ok_rate"]
    dur = metrics.get("avg_duration_sec")
    p90 = metrics.get("p90_duration_sec")
    sub = metrics.get("avg_subagent_calls")
    t7d = metrics.get("trend_7d", {})
    top_co = metrics.get("top_co_skills", [])

    # verdict 内訳
    v_ok = sum(1 for e in events if e.get("verdict") == "ok")
    v_ni = sum(1 for e in events if e.get("verdict") == "needs-improvement")
    v_br = sum(1 for e in events if e.get("verdict") == "broken")

    # トレンド矢印
    trend_arrow = ""
    if t7d.get("ok_rate", 0) > ok_rate:
        trend_arrow = " ↑"
    elif t7d.get("ok_rate", 0) < ok_rate:
        trend_arrow = " ↓"

    lines = [
        f"📊 {skill_name}  メトリクスレポート",
        "━" * 50,
        f"  総実行回数:     {total}",
        f"  成功率:         {ok_rate:.1%}  (ok:{v_ok} / NI:{v_ni} / broken:{v_br})",
        f"  平均実行時間:   {dur:.1f}s" if dur is not None else "  平均実行時間:   -",
        f"  P90 実行時間:   {p90:.1f}s" if p90 is not None else "  P90 実行時間:   -",
        f"  平均サブエージェント: {sub:.1f}回" if sub is not None else "  平均サブエージェント: -",
        "",
        f"  直近7日トレンド:",
        f"    実行数: {t7d.get('executions', 0)}     "
        f"成功率: {t7d.get('ok_rate', 0):.1%}{trend_arrow}",
    ]

    # 週次成功率チャート
    weekly = _compute_weekly_ok_rates(events)
    if weekly:
        lines.append("")
        lines.append("  成功率推移（週次）:")
        for label, rate in weekly:
            lines.append(f"    {label} {_bar(rate)} {_pct(rate)}")

    # 共起スキル
    if top_co:
        lines.append("")
        lines.append("  よく一緒に使われるスキル:")
        co_counter: Counter = Counter()
        for e in events:
            for s in e.get("co_executed_skills", []):
                co_counter[s] += 1
        for i, (s, cnt) in enumerate(co_counter.most_common(5), 1):
            pct = cnt / total * 100 if total > 0 else 0
            lines.append(f"    {i}. {s:<30} ({cnt}回 / {pct:.0f}%)")

    return "\n".join(lines)


def _compute_weekly_ok_rates(events: list[dict]) -> list[tuple[str, float]]:
    """直近 4 週間の週次成功率を返す。"""
    now = datetime.now(timezone.utc)
    weeks: list[tuple[str, float]] = []

    for i in range(4, 0, -1):
        start = now - timedelta(weeks=i)
        end = now - timedelta(weeks=i - 1)
        week_events = []
        for e in events:
            try:
                ts = datetime.fromisoformat(e.get("timestamp", ""))
                if start <= ts < end:
                    week_events.append(e)
            except (ValueError, TypeError):
                pass
        if week_events:
            ok = sum(1 for e in week_events if e.get("verdict") == "ok")
            rate = ok / len(week_events)
        else:
            rate = 0.0
        label = f"W{5 - i}"
        weeks.append((label, rate))

    return weeks


# ───────────────────────────────────────────
# 共起分析
# ───────────────────────────────────────────

def render_co_occurrence(events: list[dict]) -> str:
    """スキル共起マトリクスの上位ペアを返す。"""
    pair_counter: Counter = Counter()

    # 各イベントの co_executed_skills をペアとしてカウント
    by_skill: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        name = e.get("skill_name")
        if name:
            by_skill[name].append(e)

    for name, evts in by_skill.items():
        for e in evts:
            for co in e.get("co_executed_skills", []):
                if co != name:
                    # 正規化: アルファベット順のペアにする
                    pair = tuple(sorted([name, co]))
                    pair_counter[pair] += 1

    if not pair_counter:
        return "📊 共起データがありません"

    lines = [
        "📊 スキル共起マトリクス（上位10ペア）",
        "━" * 60,
    ]
    for (a, b), count in pair_counter.most_common(10):
        lines.append(f"  {a:<28} × {b:<28} {count}回")

    return "\n".join(lines)


# ───────────────────────────────────────────
# Markdown 出力
# ───────────────────────────────────────────

def render_summary_markdown(aggregated: dict[str, dict]) -> str:
    """Markdown 形式のサマリテーブルを返す。"""
    lines = [
        "# スキル実行メトリクス レポート",
        "",
        f"生成日時: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "| スキル名 | 回数 | 成功率 | 平均時間 | P90 | SA平均 | 7d回数 | 7d率 |",
        "|----------|------|--------|----------|-----|--------|--------|------|",
    ]

    for name in sorted(aggregated.keys()):
        m = aggregated[name]
        total = m["total_executions"]
        ok = m["ok_rate"]
        dur = m.get("avg_duration_sec")
        p90 = m.get("p90_duration_sec")
        sub = m.get("avg_subagent_calls")
        t7d = m.get("trend_7d", {})

        dur_s = f"{dur:.1f}s" if dur is not None else "-"
        p90_s = f"{p90:.1f}s" if p90 is not None else "-"
        sub_s = f"{sub:.1f}" if sub is not None else "-"
        t7d_exec = t7d.get("executions", 0)
        t7d_ok = t7d.get("ok_rate", 0.0)

        lines.append(
            f"| {name} | {total} | {ok:.0%} | {dur_s} | "
            f"{p90_s} | {sub_s} | {t7d_exec} | {_pct(t7d_ok)} |"
        )

    lines.append("")
    lines.append(
        f"**合計**: {sum(m['total_executions'] for m in aggregated.values())} 回実行"
    )
    return "\n".join(lines)


# ───────────────────────────────────────────
# CLI
# ───────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="メトリクスの可視化レポートを出力する"
    )
    parser.add_argument(
        "--skill", default=None,
        help="特定スキルの詳細を表示",
    )
    parser.add_argument(
        "--detail", action="store_true",
        help="詳細レポート（--skill と併用）",
    )
    parser.add_argument(
        "--co-occurrence", action="store_true",
        help="共起分析を表示",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="直近 N 日のみ対象",
    )
    parser.add_argument(
        "--output", default=None,
        help="Markdown ファイルへ出力",
    )
    args = parser.parse_args()

    events = load_events(days=args.days, skill_name=args.skill if args.detail else None)
    if not events:
        print("📊 メトリクスログにイベントがありません")
        return

    # 共起分析モード
    if args.co_occurrence:
        all_events = load_events(days=args.days)
        print(render_co_occurrence(all_events))
        return

    # 特定スキル詳細モード
    if args.skill and args.detail:
        metrics = compute_skill_metrics(events)
        print(render_detail(args.skill, events, metrics))
        return

    # 全スキルサマリ
    all_events = load_events(days=args.days)
    aggregated = aggregate_all(all_events)

    if args.output:
        md = render_summary_markdown(aggregated)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md + "\n")
        print(f"📊 レポートを {args.output} に出力しました")
    else:
        print(render_summary_table(aggregated))


if __name__ == "__main__":
    main()
