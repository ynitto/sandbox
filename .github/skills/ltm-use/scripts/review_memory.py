#!/usr/bin/env python3
"""
review_memory.py - 記憶の定期レビュー（忘却曲線・固定化候補・クリーンアップ候補）

v5.0.0 🧠 海馬リプレイモデルによる記憶の棚卸し

睡眠中の海馬リプレイ（記憶再活性化）をモデル化。
忘却リスク・固定化候補・クリーンアップ候補を一括提示し、記憶の健全性を維持する。

Usage:
  # 全体レビュー
  python review_memory.py

  # 全スコープ
  python review_memory.py --scope all

  # 特定の観点のみ
  python review_memory.py --consolidation-only
  python review_memory.py --forgetting-only
  python review_memory.py --cleanup-only

  # retention_score を一括更新（レビュー表示なし）
  python review_memory.py --update-retention
"""

import argparse
import os

import memory_utils


def find_consolidation_candidates(memory_dir: str, min_episodes: int = 5) -> list[dict]:
    """同カテゴリに episodic 記憶が min_episodes 件以上あるカテゴリを返す。"""
    by_cat: dict[str, list[dict]] = {}
    for fpath, rel_cat in memory_utils.iter_memory_files(memory_dir):
        with open(fpath, encoding="utf-8") as f:
            meta, _ = memory_utils.parse_frontmatter(f.read())
        if (meta.get("memory_type", "") == "episodic"
                and meta.get("status", "active") == "active"):
            by_cat.setdefault(rel_cat, []).append({
                "id": meta.get("id", ""),
                "title": meta.get("title", ""),
                "filepath": fpath,
            })
    return [
        {"category": cat, "count": len(eps), "episodes": eps}
        for cat, eps in by_cat.items()
        if len(eps) >= min_episodes
    ]


def find_forgetting_risks(
    memory_dir: str,
    retention_threshold: float = 0.3,
    min_score: int = 30,
) -> list[dict]:
    """retention < retention_threshold かつ share_score >= min_score の記憶を返す。"""
    risks = []
    for fpath, _ in memory_utils.iter_memory_files(memory_dir):
        with open(fpath, encoding="utf-8") as f:
            meta, body = memory_utils.parse_frontmatter(f.read())
        if meta.get("status", "active") != "active":
            continue
        if meta.get("importance", "normal") == "critical":
            continue  # critical は忘却しない
        retention = memory_utils.compute_retention_score(meta)
        score = memory_utils.compute_share_score(meta, body)
        if retention < retention_threshold and score >= min_score:
            risks.append({
                "filepath": fpath,
                "id": meta.get("id", ""),
                "title": meta.get("title", ""),
                "retention": retention,
                "share_score": score,
                "importance": meta.get("importance", "normal"),
                "last_accessed": meta.get("last_accessed", meta.get("updated", "")),
            })
    return sorted(risks, key=lambda x: x["retention"])


def find_cleanup_candidates(
    memory_dir: str,
    retention_threshold: float = 0.1,
    max_score: int = 20,
) -> list[dict]:
    """retention < retention_threshold かつ share_score <= max_score の記憶を返す。"""
    candidates = []
    for fpath, _ in memory_utils.iter_memory_files(memory_dir):
        with open(fpath, encoding="utf-8") as f:
            meta, body = memory_utils.parse_frontmatter(f.read())
        if meta.get("status", "active") in ("archived", "deprecated"):
            continue
        if meta.get("importance", "normal") == "critical":
            continue
        retention = memory_utils.compute_retention_score(meta)
        score = memory_utils.compute_share_score(meta, body)
        if retention < retention_threshold and score <= max_score:
            candidates.append({
                "filepath": fpath,
                "id": meta.get("id", ""),
                "title": meta.get("title", ""),
                "retention": retention,
                "share_score": score,
                "importance": meta.get("importance", "normal"),
            })
    return sorted(candidates, key=lambda x: x["retention"])


def update_retention_scores(memory_dir: str) -> int:
    """全記憶の retention_score を再計算して保存する。"""
    updated = 0
    for fpath, _ in memory_utils.iter_memory_files(memory_dir):
        with open(fpath, encoding="utf-8") as f:
            meta, _ = memory_utils.parse_frontmatter(f.read())
        retention = memory_utils.compute_retention_score(meta)
        old = float(meta.get("retention_score", -1.0))
        if abs(old - retention) > 0.005:
            memory_utils.update_frontmatter_fields(fpath, {
                "retention_score": round(retention, 3),
            })
            updated += 1
    return updated


def get_memory_stats(memory_dir: str) -> dict:
    """active 記憶の統計を返す。"""
    stats: dict = {
        "total": 0,
        "episodic": 0, "semantic": 0, "procedural": 0,
        "critical": 0, "high": 0, "normal": 0, "low": 0,
        "total_retention": 0.0,
        "total_score": 0,
    }
    for fpath, _ in memory_utils.iter_memory_files(memory_dir):
        with open(fpath, encoding="utf-8") as f:
            meta, body = memory_utils.parse_frontmatter(f.read())
        if meta.get("status", "active") != "active":
            continue
        stats["total"] += 1
        mtype = meta.get("memory_type", "semantic")
        importance = meta.get("importance", "normal")
        stats[mtype] = stats.get(mtype, 0) + 1
        stats[importance] = stats.get(importance, 0) + 1
        stats["total_retention"] += memory_utils.compute_retention_score(meta)
        stats["total_score"] += memory_utils.compute_share_score(meta, body)
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="記憶の定期レビュー（忘却曲線・固定化候補・クリーンアップ候補）"
    )
    parser.add_argument("--scope", default="workspace",
                        choices=["workspace", "home", "all"],
                        help="対象スコープ (default: workspace)")
    parser.add_argument("--category",
                        help="特定カテゴリのみ（固定化候補検出でのみ使用）")
    parser.add_argument("--consolidation-only", action="store_true",
                        help="固定化候補のみ表示")
    parser.add_argument("--forgetting-only", action="store_true",
                        help="忘却リスクのみ表示")
    parser.add_argument("--cleanup-only", action="store_true",
                        help="クリーンアップ候補のみ表示")
    parser.add_argument("--update-retention", action="store_true",
                        help="retention_score を一括更新して終了")
    args = parser.parse_args()

    for memory_dir in memory_utils.get_memory_dirs(args.scope):
        if not os.path.isdir(memory_dir):
            continue

        home_dir = memory_utils._get_home_dir()
        label = (os.path.relpath(memory_dir, home_dir)
                 if memory_dir.startswith(home_dir) else memory_dir)
        print(f"\n=== 🧠 記憶レビュー（{label}） ===\n")

        if args.update_retention:
            n = update_retention_scores(memory_dir)
            print(f"retention_score を {n}件更新しました。")
            continue

        show_all = not (args.consolidation_only or args.forgetting_only or args.cleanup_only)

        # 固定化候補
        if show_all or args.consolidation_only:
            cfg = memory_utils.load_config()
            min_eps = cfg.get("consolidation_threshold", 5)
            candidates = find_consolidation_candidates(memory_dir, min_eps)
            if candidates:
                print("📌 固定化候補（エピソード→意味記憶への蒸留推奨）:\n")
                for c in candidates:
                    cat = c["category"]
                    print(f"  [{cat}] カテゴリに {c['count']}件のエピソード記憶")
                    print(f"    → python consolidate_memory.py --category {cat}")
                print()
            elif show_all:
                print("📌 固定化候補: なし\n")

        # 忘却リスク
        if show_all or args.forgetting_only:
            risks = find_forgetting_risks(memory_dir)
            if risks:
                print("⚠ 忘却リスク（retention < 0.3 かつ価値のある記憶）:\n")
                for r in risks[:10]:
                    last = r["last_accessed"] or "未参照"
                    print(f"  {r['id']} \"{r['title']}\"")
                    print(f"    retention: {r['retention']:.2f} | share_score: {r['share_score']}"
                          f" | importance: {r['importance']} | last: {last}")
                    print(f"    → recall して再活性化、または importance を high に変更を推奨")
                if len(risks) > 10:
                    print(f"  ... 他 {len(risks) - 10}件")
                print()
            elif show_all:
                print("⚠ 忘却リスク: なし\n")

        # クリーンアップ候補
        if show_all or args.cleanup_only:
            cleanup = find_cleanup_candidates(memory_dir)
            if cleanup:
                print("🗑 クリーンアップ候補（retention < 0.1 かつ低スコア）:\n")
                for c in cleanup[:10]:
                    print(f"  {c['id']} \"{c['title']}\"")
                    print(f"    retention: {c['retention']:.2f} | share_score: {c['share_score']}")
                    print(f"    → python save_memory.py --update {c['filepath']} --status archived")
                if len(cleanup) > 10:
                    print(f"  ... 他 {len(cleanup) - 10}件")
                print()
            elif show_all:
                print("🗑 クリーンアップ候補: なし\n")

        # 統計
        if show_all:
            stats = get_memory_stats(memory_dir)
            if stats["total"] > 0:
                avg_ret = stats["total_retention"] / stats["total"]
                avg_score = stats["total_score"] / stats["total"]
                print("📊 統計サマリー:\n")
                print(f"  episodic: {stats['episodic']}件"
                      f" | semantic: {stats['semantic']}件"
                      f" | procedural: {stats['procedural']}件")
                print(f"  critical: {stats['critical']}件"
                      f" | high: {stats['high']}件"
                      f" | normal: {stats['normal']}件"
                      f" | low: {stats['low']}件")
                print(f"  平均 retention: {avg_ret:.2f}"
                      f" | 平均 share_score: {avg_score:.1f}")
            else:
                print("記憶がありません。")


if __name__ == "__main__":
    main()
