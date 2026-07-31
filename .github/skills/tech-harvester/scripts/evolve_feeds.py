#!/usr/bin/env python3
"""
evolve_feeds.py — フィード自律進化（統合スクリプト）

feed_stats と candidate_feeds を元に skill-registry.json の feeds 配列を
ローカルで直接更新する。リポジトリへのプッシュは行わない。
ltm-use が利用可能であれば、進化ログを記憶として保存する。

Usage:
  # 進化を実行（デフォルト: 削除・追加の両方を適用）
  python evolve_feeds.py

  # 実行前に変更内容をプレビューするだけ（適用しない）
  python evolve_feeds.py --dry-run

  # 削除のみ / 追加のみ
  python evolve_feeds.py --only-remove
  python evolve_feeds.py --only-add

  # 昇格閾値を変更
  python evolve_feeds.py --min-discovery 3 --min-relevance 70

削除基準:
  - consecutive_failures >= 3
  - relevance_score < 30 かつ fetch_count >= 10

追加基準（candidate_feeds からの昇格）:
  - discovery_count >= 2 かつ relevance_score >= 60
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / "skill-registry.json"
LTM_SAVE = Path(__file__).parent.parent.parent / "ltm-use" / "scripts" / "save_memory.py"

_REMOVE_FAILURE_THRESHOLD = 3
_REMOVE_RELEVANCE_MAX = 30.0
_REMOVE_FETCH_MIN = 10


def load_registry(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _get_config(registry: dict) -> dict:
    return registry.setdefault("skill_configs", {}).setdefault("tech-harvester", {})


def find_removals(config: dict) -> list[dict]:
    feeds = config.get("feeds", [])
    stats = config.get("feed_stats", {})
    removals = []
    for feed in feeds:
        name = feed["name"]
        s = stats.get(name, {})
        failures = s.get("consecutive_failures", 0)
        relevance = s.get("relevance_score", 0.0)
        fetch_count = s.get("fetch_count", 0)

        if failures >= _REMOVE_FAILURE_THRESHOLD:
            removals.append({**feed, "_reason": f"{failures} 回連続フェッチ失敗"})
        elif relevance > 0.0 and relevance < _REMOVE_RELEVANCE_MAX and fetch_count >= _REMOVE_FETCH_MIN:
            # relevance_score == 0.0 は「未評価」を意味するため削除対象外
            removals.append({**feed, "_reason": f"関連性スコア {relevance:.0f}/100 (閾値: {_REMOVE_RELEVANCE_MAX:.0f})"})
    return removals


def find_promotions(config: dict, min_discovery: int, min_relevance: float) -> list[dict]:
    candidates = config.get("candidate_feeds", [])
    return [
        c for c in candidates
        if c.get("discovery_count", 0) >= min_discovery
        and c.get("relevance_score", 0.0) >= min_relevance
        and c.get("status", "pending") == "pending"
    ]


def apply_removals(config: dict, removals: list[dict]) -> None:
    remove_names = {r["name"] for r in removals}
    config["feeds"] = [f for f in config.get("feeds", []) if f["name"] not in remove_names]
    # Also clean up feed_stats for removed feeds
    stats = config.get("feed_stats", {})
    for name in remove_names:
        stats.pop(name, None)


def apply_promotions(config: dict, promotions: list[dict]) -> None:
    existing_urls = {f["url"] for f in config.get("feeds", [])}
    promoted_names = set()
    for candidate in promotions:
        if candidate["url"] in existing_urls:
            continue
        new_feed = {
            "name": candidate["name"],
            "url": candidate["url"],
            "lang": candidate.get("lang", ""),
            "tags": candidate.get("suggested_tags", []),
        }
        config.setdefault("feeds", []).append(new_feed)
        promoted_names.add(candidate["name"])

    # Mark promoted candidates as "promoted"
    for c in config.get("candidate_feeds", []):
        if c["name"] in promoted_names:
            c["status"] = "promoted"


def _save_ltm_log(removals: list[dict], promotions: list[dict]) -> None:
    if not LTM_SAVE.exists():
        print("[evolve_feeds] ltm-use が見つかりません。進化ログの保存をスキップします。", file=sys.stderr)
        return
    if not removals and not promotions:
        return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## tech-harvester フィード進化ログ: {now_str}\n"]

    if promotions:
        lines.append("### 追加")
        for p in promotions:
            reason = "; ".join(p.get("discovered_from", [])[:2])
            lines.append(f"- **{p['name']}** ({p['url']})")
            lines.append(f"  - 発見根拠: {reason or '不明'}")
            lines.append(f"  - 関連性スコア: {p.get('relevance_score', 0):.0f}/100")
            lines.append(f"  - 付与タグ: {', '.join(p.get('suggested_tags', []))}")

    if removals:
        lines.append("### 削除")
        for r in removals:
            lines.append(f"- **{r['name']}** ({r['url']})")
            lines.append(f"  - 理由: {r.get('_reason', '不明')}")

    content = "\n".join(lines)
    summary = (
        f"tech-harvester フィード進化 {now_str}: "
        f"追加 {len(promotions)} 件, 削除 {len(removals)} 件"
    )

    try:
        result = subprocess.run(
            [
                sys.executable, str(LTM_SAVE),
                "--non-interactive", "--no-dedup", "--no-auto-tags",
                "--scope", "home",
                "--memory-type", "episodic",
                "--importance", "normal",
                "--category", "tech-trends",
                "--title", f"tech-harvester フィード進化 {now_str}",
                "--summary", summary,
                "--content", content,
                "--tags", "tech-harvester,feed-evolution,episodic",
                "--context", f"自動記録: evolve_feeds.py by tech-harvester skill ({now_str})",
            ],
            capture_output=True,
            text=True,
            cwd=str(LTM_SAVE.parent),
            timeout=30,
        )
        if result.returncode == 0:
            print("[evolve_feeds] ltm-use に進化ログを保存しました。", file=sys.stderr)
        else:
            print(f"[evolve_feeds] ltm-use 保存に失敗: {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[evolve_feeds] ltm-use 保存をスキップ: {e}", file=sys.stderr)


def _print_report(removals: list[dict], promotions: list[dict], dry_run: bool) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n## 🌱 {prefix}tech-harvester フィード進化レポート")

    if promotions:
        print("\n### 追加")
        for p in promotions:
            sources = "; ".join(p.get("discovered_from", [])[:2])
            print(f"- **{p['name']}** ({p['url']})")
            print(f"  - 発見根拠: {sources or '不明'}")
            print(f"  - 関連性スコア: {p.get('relevance_score', 0):.0f}/100")
            print(f"  - 付与タグ: {', '.join(p.get('suggested_tags', []))}")
    else:
        print("\n### 追加: なし")

    if removals:
        print("\n### 削除")
        for r in removals:
            print(f"- **{r['name']}** ({r['url']})")
            print(f"  - 理由: {r.get('_reason', '不明')}")
    else:
        print("\n### 削除: なし")

    if not dry_run:
        if removals or promotions:
            print("\nskill-registry.json をローカルで更新しました。")
        else:
            print("\n変更はありませんでした。")


def main():
    parser = argparse.ArgumentParser(description="フィード自律進化")
    parser.add_argument("--registry", default=str(REGISTRY_PATH), metavar="FILE")
    parser.add_argument("--dry-run", action="store_true", help="変更をプレビューのみ（適用しない）")
    parser.add_argument("--only-remove", action="store_true", help="削除のみ実行")
    parser.add_argument("--only-add", action="store_true", help="追加のみ実行")
    parser.add_argument("--min-discovery", type=int, default=2, metavar="N", help="昇格に必要な発見回数 (デフォルト: 2)")
    parser.add_argument("--min-relevance", type=float, default=60.0, metavar="SCORE", help="昇格に必要な関連性スコア (デフォルト: 60.0)")
    parser.add_argument("--no-ltm", action="store_true", help="ltm-use へのログ保存をスキップ")
    args = parser.parse_args()

    registry_path = Path(args.registry)
    registry = load_registry(registry_path)
    config = _get_config(registry)

    removals = [] if args.only_add else find_removals(config)
    promotions = [] if args.only_remove else find_promotions(config, args.min_discovery, args.min_relevance)

    _print_report(removals, promotions, args.dry_run)

    if args.dry_run:
        return

    if removals:
        apply_removals(config, removals)
    if promotions:
        apply_promotions(config, promotions)

    if removals or promotions:
        save_registry(registry_path, registry)
        if not args.no_ltm:
            _save_ltm_log(removals, promotions)


if __name__ == "__main__":
    main()
