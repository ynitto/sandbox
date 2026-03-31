#!/usr/bin/env python3
"""
consolidate_memory.py - エピソード記憶を意味/手続き記憶に蒸留するスクリプト

v5.0.0 🧠 固定化（海馬→新皮質: エピソード記憶→意味記憶）

海馬に蓄積したエピソード記憶群を、新皮質の意味記憶または手続き記憶として
蒸留・統合する。具体的な文脈情報を剥落させ、一般化された知識として再構成する。

Usage:
  # 固定化候補を確認（ドライラン）
  python consolidate_memory.py --dry-run

  # カテゴリ指定で固定化
  python consolidate_memory.py --category auth

  # 特定のエピソード記憶群を固定化
  python consolidate_memory.py --ids mem-20260301-001,mem-20260305-002

  # 生成される記憶タイプを指定
  python consolidate_memory.py --category auth --output-type procedural

  # 確認なし（自動実行）
  python consolidate_memory.py --category auth --yes
"""

import argparse
import os
import sys
from collections import Counter

import memory_utils
import save_memory as sm
import similarity


def find_consolidation_clusters(
    memory_dir: str,
    category: str | None = None,
    threshold: int = 5,
    sim_threshold: float = 0.5,
    ids: list[str] | None = None,
) -> list[list[dict]]:
    """固定化候補クラスタを検出して返す。

    条件1: 同一カテゴリ内にエピソード記憶が threshold 件以上
    条件2: TF-IDF 類似度 >= sim_threshold のクラスタが 3件以上（5件未満の場合）
    """
    episodes = []
    for fpath, rel_cat in memory_utils.iter_memory_files(memory_dir, category):
        with open(fpath, encoding="utf-8") as f:
            text = f.read()
        meta, body = memory_utils.parse_frontmatter(text)
        # memory_type が未設定の場合はコンテンツから自動推定する
        effective_type = meta.get("memory_type") or memory_utils.detect_memory_type(
            body, meta.get("title", ""), meta.get("summary", "")
        )
        if effective_type != "episodic":
            continue
        if meta.get("status", "active") != "active":
            continue
        if ids and meta.get("id", "") not in ids:
            continue
        episodes.append({
            "filepath": fpath,
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "summary": meta.get("summary", ""),
            "tags": meta.get("tags", []),
            "meta": meta,
            "body": body,
            "rel_cat": rel_cat,
        })

    if ids:
        # IDs 指定の場合は 1 クラスタとして返す（最低 2 件必要）
        return [episodes] if len(episodes) >= 2 else []

    # カテゴリ別グループ化
    by_cat: dict[str, list[dict]] = {}
    for ep in episodes:
        by_cat.setdefault(ep["rel_cat"], []).append(ep)

    clusters = []
    for cat, cat_eps in by_cat.items():
        if len(cat_eps) >= threshold:
            clusters.append(cat_eps)
            continue
        if len(cat_eps) < 3:
            continue
        # 3〜(threshold-1) 件: TF-IDF クラスタリングで補完
        corpus = similarity.load_corpus(memory_dir)
        doc_vectors = corpus.get("doc_vectors", {})
        if not doc_vectors:
            continue
        ungrouped = list(cat_eps)
        while len(ungrouped) >= 3:
            anchor = ungrouped.pop(0)
            cluster = [anchor]
            remaining = []
            for ep in ungrouped:
                v1 = doc_vectors.get(anchor["id"], {})
                v2 = doc_vectors.get(ep["id"], {})
                if v1 and v2 and similarity.cosine_similarity(v1, v2) >= sim_threshold:
                    cluster.append(ep)
                else:
                    remaining.append(ep)
            if len(cluster) >= 3:
                clusters.append(cluster)
            ungrouped = remaining

    return clusters


def generate_consolidated_content(episodes: list[dict], output_type: str) -> dict:
    """エピソード群から蒸留記憶のコンテンツを生成する。"""
    # 共通タグ収集
    all_tags: list[str] = []
    for ep in episodes:
        tags = ep["tags"]
        if isinstance(tags, list):
            all_tags.extend(tags)
        elif isinstance(tags, str):
            all_tags.extend(t.strip() for t in tags.split(",") if t.strip())
    common_tags = [t for t, _ in Counter(all_tags).most_common(5)]

    # タイトル生成
    cat = episodes[0]["rel_cat"].replace("/", " ").replace("-", " ").replace("_", " ").strip()
    type_label = "設計知見" if output_type == "semantic" else "手順・パターン"
    title = f"{cat} {type_label}" if cat and cat != "." else type_label

    # サマリー
    episode_count = len(episodes)
    kind = "知識" if output_type == "semantic" else "手順"
    summary = f"{episode_count}件のエピソード記憶から蒸留された{kind}。"

    # 本文: 各エピソードの学び・結論を列挙
    parts = [f"## 蒸留元エピソード（{episode_count}件）\n"]
    for ep in episodes:
        parts.append(f"### {ep['title']}")
        parts.append(ep["summary"])
        # 「学び・結論」セクションを抽出
        body = ep["body"]
        if "## 学び・結論" in body:
            conclusion = body.split("## 学び・結論", 1)[1].strip()
            if "## " in conclusion:
                conclusion = conclusion.split("## ", 1)[0].strip()
            if conclusion and conclusion != "(作成時に記録なし)":
                parts.append(f"\n**学び**: {conclusion}")
        parts.append("")
    content = "\n".join(parts)

    # share_score: 元の平均 × 1.2
    scores = [memory_utils.compute_share_score(ep["meta"], ep["body"]) for ep in episodes]
    avg_score = sum(scores) / len(scores) if scores else 0
    consolidated_score = min(100, int(avg_score * 1.2))

    # importance: 元の最高レベルを継承
    order = {"critical": 3, "high": 2, "normal": 1, "low": 0}
    names = {3: "critical", 2: "high", 1: "normal", 0: "low"}
    max_imp = max((order.get(ep["meta"].get("importance", "normal"), 1) for ep in episodes), default=1)

    return {
        "title": title,
        "summary": summary,
        "content": content,
        "tags": common_tags,
        "share_score": consolidated_score,
        "importance": names[max_imp],
        "output_type": output_type,
        "source_ids": [ep["id"] for ep in episodes],
    }


def main():
    parser = argparse.ArgumentParser(description="エピソード記憶を意味/手続き記憶に蒸留する")
    parser.add_argument("--scope", default="home",
                        choices=["home"],
                        help="対象スコープ (default: home)")
    parser.add_argument("--category", help="固定化対象カテゴリ")
    parser.add_argument("--ids", help="固定化対象エピソードID（カンマ区切り）")
    parser.add_argument("--output-type", default="semantic",
                        choices=["semantic", "procedural"],
                        help="蒸留後の記憶タイプ (default: semantic)")
    parser.add_argument("--threshold", type=int, default=5,
                        help="固定化に必要な最低エピソード数 (default: 5)")
    parser.add_argument("--sim-threshold", type=float, default=0.5,
                        help="TF-IDF クラスタリング閾値 (default: 0.5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="候補を表示して終了（実際には固定化しない）")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="確認プロンプトをスキップ")
    args = parser.parse_args()

    memory_dir = memory_utils.get_memory_dir(args.scope)
    ids = [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None

    clusters = find_consolidation_clusters(
        memory_dir, args.category, args.threshold, args.sim_threshold, ids
    )

    if not clusters:
        print("固定化候補のエピソード記憶クラスタが見つかりませんでした。")
        print(f"  （条件: memory_type=episodic かつ status=active かつ同カテゴリ {args.threshold}件以上）")
        return

    print(f"🧠 固定化候補: {len(clusters)}クラスタ\n")
    for i, cluster in enumerate(clusters, 1):
        cat = cluster[0]["rel_cat"]
        print(f"[クラスタ {i}] {cat} カテゴリ（{len(cluster)}件のエピソード記憶）")
        for ep in cluster:
            print(f"  - {ep['id']} \"{ep['title']}\"")
        print()

    if args.dry_run:
        print("[ドライラン] 実際には固定化しません。")
        return

    consolidated = 0
    for cluster in clusters:
        result = generate_consolidated_content(cluster, args.output_type)
        type_label = "意味" if args.output_type == "semantic" else "手続き"
        print(f"\n→ 蒸留された{type_label}記憶:")
        print(f"  title: {result['title']}")
        print(f"  memory_type: {args.output_type}")
        print(f"  summary: {result['summary']}")
        print(f"  importance: {result['importance']}")
        print(f"  蒸留元: {', '.join(result['source_ids'])}")

        if not args.yes:
            ans = input("\n固定化を実行しますか？ [y/N] ").strip().lower()
            if ans != "y":
                print("スキップしました。")
                continue

        # 蒸留記憶を保存
        cat = cluster[0]["rel_cat"]
        consolidated_filepath = sm.save_memory(
            category=cat,
            title=result["title"],
            summary=result["summary"],
            content=result["content"],
            tags=result["tags"],
            scope=args.scope,
            memory_type=args.output_type,
            importance=result["importance"],
        )
        # share_score と consolidated_from を追記
        with open(consolidated_filepath, encoding="utf-8") as f:
            c_meta, _ = memory_utils.parse_frontmatter(f.read())
        consolidated_id = c_meta.get("id", "")
        memory_utils.update_frontmatter_fields(consolidated_filepath, {
            "share_score": result["share_score"],
            "consolidated_from": result["source_ids"],
        })

        # 元エピソードをアーカイブ
        for ep in cluster:
            memory_utils.update_frontmatter_fields(ep["filepath"], {
                "status": "archived",
                "consolidated_to": consolidated_id,
            })
            memory_utils.update_index_entry(memory_dir, ep["filepath"])

        memory_utils.update_index_entry(memory_dir, consolidated_filepath)
        print(f"✅ 固定化完了: {consolidated_filepath}")
        consolidated += 1

    if consolidated:
        print(f"\n{consolidated}件のクラスタを固定化しました。")
        print("昇格するには: python promote_memory.py --scope home --auto")


if __name__ == "__main__":
    main()
