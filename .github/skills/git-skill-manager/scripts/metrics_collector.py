#!/usr/bin/env python3
"""メトリクスログ (JSONL) を集計し、レジストリのサマリを一括更新する。

使い方:
    python metrics_collector.py                      # 全スキル集計
    python metrics_collector.py --skill <name>       # 特定スキルのみ
    python metrics_collector.py --days 30            # 直近 30 日のみ対象
    python metrics_collector.py --rotate             # 90 日超のログをアーカイブ

metrics-log.jsonl の各行は以下の形式:
    {
      "event_id": "evt-...",
      "skill_name": "...",
      "timestamp": "ISO8601",
      "duration_sec": float | null,
      "verdict": "ok" | "needs-improvement" | "broken",
      "note": "...",
      "subagent_calls": int | null,
      "co_executed_skills": ["..."],
      "context": {"sprint_id": "..." | null, "node_id": "..." | null}
    }

レジストリの installed_skills[].metrics を更新するフィールド:
    total_executions, ok_rate, last_executed_at,
    avg_duration_sec, p90_duration_sec, avg_subagent_calls,
    trend_7d, top_co_skills
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta


# registry.py の __file__ ベースのパス解決を利用
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from registry import _agent_home, _registry_path


def _metrics_log_path() -> str:
    return os.path.join(_agent_home(), "metrics-log.jsonl")


# ───────────────────────────────────────────
# JSONL 読み込み
# ───────────────────────────────────────────

def load_events(
    days: int | None = None,
    skill_name: str | None = None,
) -> list[dict]:
    """metrics-log.jsonl を読み込み、フィルタ済みのイベントリストを返す。"""
    path = _metrics_log_path()
    if not os.path.isfile(path):
        return []

    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    events: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            if skill_name and ev.get("skill_name") != skill_name:
                continue

            if cutoff:
                ts_str = ev.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue

            events.append(ev)

    return events


# ───────────────────────────────────────────
# 集計ロジック
# ───────────────────────────────────────────

def _percentile(sorted_values: list[float], p: float) -> float:
    """ソート済みリストから p パーセンタイルを線形補間で返す。"""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    k = (n - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_values[-1]
    d = k - f
    return sorted_values[f] + d * (sorted_values[c] - sorted_values[f])


def compute_skill_metrics(events: list[dict]) -> dict:
    """1 スキル分のイベントリストからサマリメトリクスを算出する。

    Returns:
        {
            "total_executions": int,
            "ok_rate": float,
            "last_executed_at": str | None,
            "avg_duration_sec": float | None,
            "p90_duration_sec": float | None,
            "avg_subagent_calls": float | None,
            "trend_7d": {"executions": int, "ok_rate": float},
            "top_co_skills": [str, ...]
        }
    """
    if not events:
        return {
            "total_executions": 0,
            "ok_rate": 0.0,
            "last_executed_at": None,
            "avg_duration_sec": None,
            "p90_duration_sec": None,
            "avg_subagent_calls": None,
            "trend_7d": {"executions": 0, "ok_rate": 0.0},
            "top_co_skills": [],
        }

    total = len(events)
    ok_count = sum(1 for e in events if e.get("verdict") == "ok")
    ok_rate = round(ok_count / total, 3) if total > 0 else 0.0

    # 最終実行時刻
    timestamps = [e.get("timestamp", "") for e in events]
    last_executed_at = max(timestamps) if timestamps else None

    # 実行時間
    durations = sorted(
        e["duration_sec"] for e in events
        if e.get("duration_sec") is not None
    )
    avg_duration = round(sum(durations) / len(durations), 1) if durations else None
    p90_duration = round(_percentile(durations, 90), 1) if durations else None

    # サブエージェント呼び出し
    subagent_vals = [
        e["subagent_calls"] for e in events
        if e.get("subagent_calls") is not None
    ]
    avg_subagent = (
        round(sum(subagent_vals) / len(subagent_vals), 1) if subagent_vals else None
    )

    # 直近 7 日トレンド
    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
    recent = []
    for e in events:
        try:
            ts = datetime.fromisoformat(e.get("timestamp", ""))
            if ts >= cutoff_7d:
                recent.append(e)
        except (ValueError, TypeError):
            pass
    recent_total = len(recent)
    recent_ok = sum(1 for e in recent if e.get("verdict") == "ok")
    trend_7d = {
        "executions": recent_total,
        "ok_rate": round(recent_ok / recent_total, 3) if recent_total > 0 else 0.0,
    }

    # 共起スキル
    co_counter: Counter = Counter()
    for e in events:
        for s in e.get("co_executed_skills", []):
            co_counter[s] += 1
    top_co_skills = [s for s, _ in co_counter.most_common(5)]

    return {
        "total_executions": total,
        "ok_rate": ok_rate,
        "last_executed_at": last_executed_at,
        "avg_duration_sec": avg_duration,
        "p90_duration_sec": p90_duration,
        "avg_subagent_calls": avg_subagent,
        "trend_7d": trend_7d,
        "top_co_skills": top_co_skills,
    }


def aggregate_all(
    events: list[dict],
) -> dict[str, dict]:
    """全イベントをスキルごとにグルーピングして集計する。

    Returns:
        {skill_name: metrics_dict, ...}
    """
    by_skill: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        name = e.get("skill_name")
        if name:
            by_skill[name].append(e)

    return {name: compute_skill_metrics(evts) for name, evts in by_skill.items()}


# ───────────────────────────────────────────
# レジストリ更新
# ───────────────────────────────────────────

def update_registry(aggregated: dict[str, dict]) -> int:
    """レジストリの metrics フィールドを集計結果で更新する。

    Returns:
        更新したスキル数。
    """
    path = _registry_path()
    if not os.path.isfile(path):
        print("⚠️  レジストリが見つかりません")
        return 0

    with open(path, encoding="utf-8") as f:
        reg = json.load(f)

    updated = 0
    for skill in reg.get("installed_skills", []):
        name = skill["name"]
        if name not in aggregated:
            continue
        new_metrics = aggregated[name]
        # central_ok_rate は外部データなので保持
        central_ok_rate = skill.get("metrics", {}).get("central_ok_rate")
        new_metrics["central_ok_rate"] = central_ok_rate
        skill["metrics"] = new_metrics
        updated += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)

    return updated


# ───────────────────────────────────────────
# ログローテーション
# ───────────────────────────────────────────

def rotate_log(max_days: int = 90) -> int:
    """指定日数を超えた古いログを .bak へ移動する。

    Returns:
        アーカイブされた行数。
    """
    path = _metrics_log_path()
    if not os.path.isfile(path):
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    bak_path = path + ".bak"
    keep_lines: list[str] = []
    archived = 0

    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                ev = json.loads(stripped)
                ts = datetime.fromisoformat(ev.get("timestamp", ""))
                if ts < cutoff:
                    with open(bak_path, "a", encoding="utf-8") as bak:
                        bak.write(stripped + "\n")
                    archived += 1
                    continue
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
            keep_lines.append(stripped)

    with open(path, "w", encoding="utf-8") as f:
        for line in keep_lines:
            f.write(line + "\n")

    return archived


# ───────────────────────────────────────────
# CLI
# ───────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="メトリクスログを集計してレジストリを更新する"
    )
    parser.add_argument(
        "--skill", default=None,
        help="特定スキルのみ集計",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="直近 N 日のみ対象（省略時は全期間）",
    )
    parser.add_argument(
        "--rotate", action="store_true",
        help="90 日超の古いログを .bak へアーカイブ",
    )
    args = parser.parse_args()

    if args.rotate:
        count = rotate_log()
        print(f"🗄️  {count} 件のログをアーカイブしました")
        if count == 0:
            return

    events = load_events(days=args.days, skill_name=args.skill)
    if not events:
        print("📊 メトリクスログにイベントがありません")
        return

    if args.skill:
        aggregated = {args.skill: compute_skill_metrics(events)}
    else:
        aggregated = aggregate_all(events)

    updated = update_registry(aggregated)
    print(f"📊 {len(aggregated)} スキルを集計、{updated} スキルのレジストリを更新しました")

    # サマリ表示
    for name, m in sorted(aggregated.items()):
        total = m["total_executions"]
        ok = m["ok_rate"]
        dur = m["avg_duration_sec"]
        dur_str = f"{dur}s" if dur is not None else "-"
        sub = m["avg_subagent_calls"]
        sub_str = f"{sub}" if sub is not None else "-"
        print(f"  {name}: {total}回  ok={ok:.1%}  avg={dur_str}  subagent={sub_str}")


if __name__ == "__main__":
    main()
