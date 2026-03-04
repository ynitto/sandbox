#!/usr/bin/env python3
"""スキルの実行メトリクスを集計・表示する。

使い方:
    python metrics.py                          # 全スキルのサマリー表示
    python metrics.py <skill-name>             # 指定スキルの詳細表示
    python metrics.py <skill-name> --trend     # 成功率の時系列推移を表示
    python metrics.py --co-occurrence          # スキル共起マトリクスを表示
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone

from registry import load_registry


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _ok_rate_label(rate: float | None) -> str:
    if rate is None:
        return "  n/a "
    pct = int(rate * 100)
    return f"{pct:4d}% "


def _dur_label(avg: float | None) -> str:
    if avg is None:
        return "      n/a"
    return f"{avg:8.1f}s"


def _trend_blocks(rate: float | None) -> str:
    """ok率を 10 段階のブロックグラフに変換する。"""
    if rate is None:
        return "──────────"
    filled = round(rate * 10)
    return "█" * filled + "░" * (10 - filled)


# ---------------------------------------------------------------------------
# サマリー（全スキル一覧）
# ---------------------------------------------------------------------------

def show_summary() -> None:
    reg = load_registry()
    skills = reg.get("installed_skills", [])
    if not skills:
        print("⚠️  インストール済みスキルが見つかりません")
        return

    # フィードバック実績のあるスキルを先頭に
    def sort_key(s: dict) -> tuple:
        m = s.get("metrics") or {}
        return (-((m.get("total_executions") or 0)), s["name"])

    skills = sorted(skills, key=sort_key)

    print("📊 スキル実行メトリクス サマリー")
    print()
    header = f"  {'スキル名':<30}  {'実行回数':>6}  {'ok率':>6}  {'平均実行時間':>10}  {'最終実行':>10}"
    print(header)
    print("  " + "─" * (len(header) - 2))

    for skill in skills:
        name = skill["name"]
        m = skill.get("metrics") or {}
        total = m.get("total_executions") or 0
        ok_rate = m.get("ok_rate")
        avg_dur = m.get("avg_duration_sec")
        last_at = (m.get("last_executed_at") or "")[:10] or "    ─    "
        print(
            f"  {name:<30}  {total:>6}  {_ok_rate_label(ok_rate):>6}"
            f"  {_dur_label(avg_dur):>10}  {last_at:>10}"
        )

    print()
    total_execs = sum((s.get("metrics") or {}).get("total_executions", 0) for s in skills)
    print(f"  合計実行回数: {total_execs}")


# ---------------------------------------------------------------------------
# 詳細表示（特定スキル）
# ---------------------------------------------------------------------------

def show_detail(skill_name: str) -> None:
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' が見つかりません")
        sys.exit(1)

    m = skill.get("metrics") or {}
    history = skill.get("feedback_history") or []

    print(f"📊 {skill_name} — メトリクス詳細")
    print()

    total = m.get("total_executions", 0)
    ok_count = sum(1 for e in history if e.get("verdict") == "ok")
    ni_count = sum(1 for e in history if e.get("verdict") == "needs-improvement")
    br_count = sum(1 for e in history if e.get("verdict") == "broken")

    print(f"  総実行回数    : {total}")
    print(f"  ok            : {ok_count}")
    print(f"  needs-improve : {ni_count}")
    print(f"  broken        : {br_count}")
    print(f"  ok率          : {_ok_rate_label(m.get('ok_rate')).strip()}")
    print(f"  中央 ok率     : {_ok_rate_label(m.get('central_ok_rate')).strip()}")
    print(f"  平均実行時間  : {_dur_label(m.get('avg_duration_sec')).strip()}")
    print(f"  最終実行      : {(m.get('last_executed_at') or '─')[:19]}")
    print()

    co_occ = m.get("co_occurrence") or {}
    if co_occ:
        print("  共起スキル:")
        for s_name, cnt in sorted(co_occ.items(), key=lambda x: -x[1]):
            print(f"    {s_name:<30}  {cnt:>3} 回")
        print()

    if history:
        print("  直近フィードバック（最大 5 件）:")
        for e in history[-5:]:
            ts = e.get("timestamp", "")[:19]
            verdict = e.get("verdict", "")
            note = e.get("note", "")
            dur = e.get("duration_sec")
            dur_label = f"  ({dur:.1f}s)" if dur is not None else ""
            mark = {"ok": "✅", "needs-improvement": "⚠️", "broken": "❌"}.get(verdict, "📝")
            note_label = f"  {note}" if note else ""
            print(f"    {ts}  {mark} {verdict}{dur_label}{note_label}")


# ---------------------------------------------------------------------------
# 時系列トレンド
# ---------------------------------------------------------------------------

def show_trend(skill_name: str) -> None:
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' が見つかりません")
        sys.exit(1)

    history = skill.get("feedback_history") or []
    if not history:
        print(f"ℹ️  '{skill_name}' のフィードバック履歴がありません")
        return

    # 月単位で集計
    monthly: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "total": 0})
    for e in history:
        ts = e.get("timestamp", "")
        if not ts:
            continue
        month = ts[:7]  # "YYYY-MM"
        monthly[month]["total"] += 1
        if e.get("verdict") == "ok":
            monthly[month]["ok"] += 1

    if not monthly:
        print(f"ℹ️  '{skill_name}' のトレンドデータがありません")
        return

    print(f"📈 {skill_name} — 成功率トレンド（月次）")
    print()
    print(f"  {'月':^8}  {'ok率':>5}  グラフ")
    print(f"  {'─'*8}  {'─'*5}  {'─'*12}")

    for month in sorted(monthly.keys()):
        d = monthly[month]
        rate = d["ok"] / d["total"] if d["total"] > 0 else 0.0
        pct = int(rate * 100)
        bar = _trend_blocks(rate)
        print(f"  {month}   {pct:3d}%  {bar}  ({d['ok']}/{d['total']})")

    print()

    # 傾向判定（直近2ヶ月比較）
    months = sorted(monthly.keys())
    if len(months) >= 2:
        prev_m = monthly[months[-2]]
        curr_m = monthly[months[-1]]
        prev_rate = prev_m["ok"] / prev_m["total"] if prev_m["total"] > 0 else 0.0
        curr_rate = curr_m["ok"] / curr_m["total"] if curr_m["total"] > 0 else 0.0
        diff = curr_rate - prev_rate
        if diff > 0.05:
            trend_mark = "📈 改善傾向"
        elif diff < -0.05:
            trend_mark = "📉 悪化傾向"
        else:
            trend_mark = "➡️  安定"
        print(f"  直近トレンド: {trend_mark}  ({int(prev_rate*100)}% → {int(curr_rate*100)}%)")


# ---------------------------------------------------------------------------
# 共起マトリクス（全スキル）
# ---------------------------------------------------------------------------

def show_co_occurrence() -> None:
    reg = load_registry()
    skills = reg.get("installed_skills", [])

    # 全スキルから共起データを収集
    co_matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for skill in skills:
        name = skill["name"]
        history = skill.get("feedback_history") or []
        for e in history:
            for co_skill in e.get("co_skills", []):
                co_matrix[name][co_skill] += 1

    # 実際にデータのあるスキルのみ表示
    active = {s for s, partners in co_matrix.items() if partners}
    if not active:
        print("ℹ️  共起データがまだ記録されていません。")
        print("   フィードバック記録時に --co-skills を指定してください。")
        return

    active_sorted = sorted(active)
    print("🔗 スキル共起マトリクス")
    print()

    for skill_name in active_sorted:
        partners = co_matrix[skill_name]
        if not partners:
            continue
        print(f"  {skill_name}:")
        for partner, cnt in sorted(partners.items(), key=lambda x: -x[1]):
            print(f"    └─ {partner:<30}  {cnt:>3} 回")
        print()


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="スキルの実行メトリクスを集計・表示する"
    )
    parser.add_argument(
        "skill_name",
        nargs="?",
        default=None,
        help="対象スキル名。省略すると全スキルのサマリーを表示する",
    )
    parser.add_argument(
        "--trend",
        action="store_true",
        help="指定スキルの成功率トレンド（時系列）を表示する",
    )
    parser.add_argument(
        "--co-occurrence",
        action="store_true",
        help="全スキルの共起マトリクスを表示する",
    )
    args = parser.parse_args()

    if args.co_occurrence:
        show_co_occurrence()
    elif args.skill_name and args.trend:
        show_trend(args.skill_name)
    elif args.skill_name:
        show_detail(args.skill_name)
    else:
        show_summary()


if __name__ == "__main__":
    main()
