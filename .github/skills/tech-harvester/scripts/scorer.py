#!/usr/bin/env python3
"""
scorer.py — フィード品質スコアリング

フェッチ結果（articles.json）を元にフィードごとの品質指標を
skill-registry.json の feed_stats セクションに蓄積する。

Usage:
  # フェッチ結果からスコアを更新
  python scorer.py update --articles articles.json
  python scorer.py update --articles articles.json --failed "Feed A,Feed B"

  # 関連性スコアを手動設定（エージェントが記事を評価した後に使う）
  python scorer.py set-relevance "Hacker News" 75.0

  # 現在の feed_stats を表示
  python scorer.py show
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / "skill-registry.json"

_EMPTY_STAT = {
    "fetch_count": 0,
    "article_count": 0,
    "avg_desc_length": 0.0,
    "consecutive_failures": 0,
    "last_fetched": "",
    "relevance_score": 0.0,
}


def load_registry(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _get_stats(registry: dict) -> dict:
    return (
        registry
        .setdefault("skill_configs", {})
        .setdefault("tech-harvester", {})
        .setdefault("feed_stats", {})
    )


def update_stats(registry: dict, articles: list[dict], failed_feeds: list[str]) -> dict:
    stats = _get_stats(registry)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    by_feed: dict[str, list[dict]] = {}
    for a in articles:
        by_feed.setdefault(a["feed"], []).append(a)

    for feed_name, feed_articles in by_feed.items():
        entry = stats.setdefault(feed_name, dict(_EMPTY_STAT))
        desc_lengths = [len(a.get("description", "")) for a in feed_articles]
        avg = sum(desc_lengths) / len(desc_lengths) if desc_lengths else 0.0
        old_count = entry["fetch_count"]
        new_count = old_count + 1
        # Cumulative moving average for description length
        entry["avg_desc_length"] = round(
            (entry["avg_desc_length"] * old_count + avg) / new_count, 1
        )
        entry["fetch_count"] = new_count
        entry["article_count"] = entry.get("article_count", 0) + len(feed_articles)
        entry["consecutive_failures"] = 0
        entry["last_fetched"] = now

    for feed_name in failed_feeds:
        entry = stats.setdefault(feed_name, dict(_EMPTY_STAT))
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        entry["last_fetched"] = now

    return registry


def set_relevance(registry: dict, feed_name: str, score: float) -> dict:
    stats = _get_stats(registry)
    if feed_name not in stats:
        print(f"[scorer] WARN: フィードが見つかりません: {feed_name}", file=sys.stderr)
        return registry
    stats[feed_name]["relevance_score"] = round(max(0.0, min(100.0, score)), 1)
    return registry


def show(registry: dict) -> None:
    config = registry.get("skill_configs", {}).get("tech-harvester", {})
    stats = config.get("feed_stats", {})
    if not stats:
        print("feed_stats はまだありません。'scorer.py update --articles FILE' で更新してください。")
        return

    print(f"{'フィード名':<35} {'取得回数':>6} {'記事数':>6} {'平均説明長':>8} {'連続失敗':>6} {'関連性':>6}")
    print("-" * 80)
    for name, s in sorted(stats.items(), key=lambda x: x[1].get("relevance_score", 0), reverse=True):
        failures = s.get("consecutive_failures", 0)
        flag = " ⚠" if failures >= 3 else ""
        print(
            f"{name:<35} {s.get('fetch_count', 0):>6} {s.get('article_count', 0):>6}"
            f" {s.get('avg_desc_length', 0.0):>8.1f} {failures:>6} {s.get('relevance_score', 0.0):>6.1f}{flag}"
        )


def main():
    parser = argparse.ArgumentParser(description="フィード品質スコアリング")
    parser.add_argument("--registry", default=str(REGISTRY_PATH), metavar="FILE")
    sub = parser.add_subparsers(dest="cmd")

    upd = sub.add_parser("update", help="フェッチ結果からスコアを更新")
    upd.add_argument("--articles", required=True, metavar="FILE")
    upd.add_argument("--failed", default="", help="カンマ区切りの失敗フィード名")

    rel = sub.add_parser("set-relevance", help="関連性スコアを手動設定")
    rel.add_argument("feed_name")
    rel.add_argument("score", type=float)

    sub.add_parser("show", help="現在の feed_stats を表示")

    args = parser.parse_args()
    registry_path = Path(args.registry)
    registry = load_registry(registry_path)

    if args.cmd == "update":
        data = json.loads(Path(args.articles).read_text(encoding="utf-8"))
        articles = data.get("articles", [])
        failed = [f.strip() for f in args.failed.split(",") if f.strip()]
        registry = update_stats(registry, articles, failed)
        save_registry(registry_path, registry)
        stats_count = len(_get_stats(registry))
        print(f"[scorer] {stats_count} フィードのスコアを更新しました。", file=sys.stderr)

    elif args.cmd == "set-relevance":
        registry = set_relevance(registry, args.feed_name, args.score)
        save_registry(registry_path, registry)
        print(f"[scorer] {args.feed_name} の関連性スコアを {args.score} に設定しました。", file=sys.stderr)

    else:
        show(registry)


if __name__ == "__main__":
    main()
