#!/usr/bin/env python3
from __future__ import annotations
"""
cleanup_memory.py - 不要な記憶を削除してディスク領域を節約するスクリプト

削除基準（AND条件でなくOR）:
  1. access_count == 0 かつ作成から N 日以上経過（デフォルト30日）
  2. status == archived かつ更新から N 日以上経過（デフォルト60日）
  3. status == deprecated
  4. retention_score < 0.1 かつ importance が critical/high でない（v5.0.0）
  5. 重複記憶（--duplicates-only: 類似度 >= 0.85 のペアの低品質側）
  6. 品質スコア閾値以下（--quality-threshold: 総合品質スコア）

Usage:
  # ドライラン（削除せず対象を表示）
  python cleanup_memory.py --dry-run

  # ホーム記憶をクリーンアップ（デフォルト）
  python cleanup_memory.py

  # 全スコープ
  python cleanup_memory.py --scope all

  # 基準日数をカスタマイズ
  python cleanup_memory.py --inactive-days 14 --archived-days 30 --dry-run

  # 重複検出モード（類似度 >= 0.85 のペアを検出、低品質側を削除候補に）
  python cleanup_memory.py --duplicates-only --dry-run

  # 品質スコア閾値モード（総合品質 < 30 を削除候補に）
  python cleanup_memory.py --quality-threshold 30 --dry-run

  # 非インタラクティブ（CI用）
  python cleanup_memory.py --yes
"""

import argparse
import os
import sys

import memory_utils
import similarity


def compute_quality_score(meta: dict, body: str) -> float:
    """
    総合品質スコア = share_score * 0.6 + freshness * 0.2 + uniqueness * 0.2
    
    - share_score: memory_utils.compute_share_score() (0-100)
    - freshness: 更新からの日数に基づく減衰スコア (0-100)
      - 0日: 100, 30日: 50, 180日以上: 0
    - uniqueness: エントリ長・タグ数から判定 (0-100)
      - body >= 300文字 && tags >= 3: 100
      - body >= 150文字 && tags >= 2: 50
      - 以下: 0
    """
    share = memory_utils.compute_share_score(meta, body)
    
    # Freshness
    updated = meta.get("updated", meta.get("created", ""))
    age = memory_utils.days_since(updated)
    if age <= 30:
        freshness = 100 - (age / 30) * 50  # 0日: 100, 30日: 50
    elif age <= 180:
        freshness = 50 - ((age - 30) / 150) * 50  # 30日: 50, 180日: 0
    else:
        freshness = 0
    
    # Uniqueness
    body_len = len(body.strip())
    tags_count = len(meta.get("tags", []))
    if body_len >= 300 and tags_count >= 3:
        uniqueness = 100
    elif body_len >= 150 and tags_count >= 2:
        uniqueness = 50
    else:
        uniqueness = 0
    
    return share * 0.6 + freshness * 0.2 + uniqueness * 0.2


def find_duplicate_targets(memory_dir: str, threshold: float = 0.85) -> list[dict]:
    """重複記憶ペアを検出し、品質の低い側を削除候補にする。

    コーパス構造: {"doc_vectors": {mem_id: {term: tfidf}}, "df": {...}, "total_docs": N}
    """
    import json

    corpus_path = os.path.join(memory_dir, memory_utils.CORPUS_FILENAME)
    if not os.path.exists(corpus_path):
        print(f"[警告] コーパスファイルが存在しません: {corpus_path}")
        print("       build_index.py --force を実行してコーパスを構築してください。")
        return []

    with open(corpus_path, encoding="utf-8") as f:
        corpus_data = json.load(f)

    doc_vectors = corpus_data.get("doc_vectors", {})
    if not doc_vectors:
        print("[警告] コーパスにベクトルデータがありません。build_index.py --force を実行してください。")
        return []

    # インデックスから mem_id → filepath マッピングを取得
    index = memory_utils.load_index(memory_dir)
    id_to_entry = {e["id"]: e for e in index.get("entries", []) if e.get("id")}

    mem_ids = list(doc_vectors.keys())
    targets = []
    seen_pairs: set[tuple[str, str]] = set()

    for i, id_i in enumerate(mem_ids):
        for j, id_j in enumerate(mem_ids):
            if i >= j:
                continue
            pair_key = (min(id_i, id_j), max(id_i, id_j))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            sim = similarity.cosine_similarity(doc_vectors[id_i], doc_vectors[id_j])
            if sim < threshold:
                continue

            entry_i = id_to_entry.get(id_i)
            entry_j = id_to_entry.get(id_j)
            if not entry_i or not entry_j:
                continue

            fp_i = os.path.join(memory_dir, entry_i["filepath"])
            fp_j = os.path.join(memory_dir, entry_j["filepath"])
            try:
                with open(fp_i, encoding="utf-8") as f:
                    meta_i, body_i = memory_utils.parse_frontmatter(f.read())
                with open(fp_j, encoding="utf-8") as f:
                    meta_j, body_j = memory_utils.parse_frontmatter(f.read())
            except OSError:
                continue

            quality_i = compute_quality_score(meta_i, body_i)
            quality_j = compute_quality_score(meta_j, body_j)

            if quality_i < quality_j:
                lower_fp, lower_meta, lower_body, lower_q, keep_fp = fp_i, meta_i, body_i, quality_i, fp_j
            else:
                lower_fp, lower_meta, lower_body, lower_q, keep_fp = fp_j, meta_j, body_j, quality_j, fp_i

            targets.append({
                "filepath": lower_fp,
                "title": lower_meta.get("title", os.path.basename(lower_fp)),
                "status": lower_meta.get("status", "active"),
                "access_count": int(lower_meta.get("access_count", 0)),
                "share_score": memory_utils.compute_share_score(lower_meta, lower_body),
                "quality_score": lower_q,
                "reason": f"重複記憶検出（類似度 {sim:.3f} >= {threshold}、保持: {os.path.basename(keep_fp)}）",
                "age_created": memory_utils.days_since(lower_meta.get("created", "")),
                "rel_cat": os.path.relpath(os.path.dirname(lower_fp), memory_dir),
            })

    return sorted(targets, key=lambda x: x["quality_score"])


def find_cleanup_targets(memory_dir: str, inactive_days: int, archived_days: int,
                        quality_threshold: float | None = None) -> list[dict]:
    """削除対象ファイルを検出して返す"""
    targets = []
    for fpath, rel_cat in memory_utils.iter_memory_files(memory_dir):
        with open(fpath, encoding="utf-8") as f:
            text = f.read()
        meta, body = memory_utils.parse_frontmatter(text)

        status = meta.get("status", "active")
        access_count = int(meta.get("access_count", 0))
        created = meta.get("created", "")
        updated = meta.get("updated", "")
        age_created = memory_utils.days_since(created)
        age_updated = memory_utils.days_since(updated)
        title = meta.get("title", os.path.basename(fpath))
        score = memory_utils.compute_share_score(meta, body)

        reason = None
        importance = meta.get("importance", "normal")
        if status == "deprecated":
            reason = "status=deprecated"
        elif status == "archived" and age_updated >= archived_days:
            reason = f"archived かつ {age_updated}日間更新なし（基準: {archived_days}日）"
        elif access_count == 0 and age_created >= inactive_days:
            reason = f"未参照 かつ 作成から{age_created}日経過（基準: {inactive_days}日）"
        elif importance not in ("critical", "high"):
            retention = memory_utils.compute_retention_score(meta)
            if retention < 0.1:
                reason = f"retention_score={retention:.3f} < 0.1 かつ importance={importance}"
        if reason is None and quality_threshold is not None:
            quality = compute_quality_score(meta, body)
            if quality < quality_threshold:
                reason = f"品質スコア {quality:.1f} < 閾値 {quality_threshold}"

        if reason:
            targets.append({
                "filepath": fpath,
                "title": title,
                "status": status,
                "access_count": access_count,
                "share_score": score,
                "reason": reason,
                "age_created": age_created,
                "rel_cat": rel_cat,
            })

    return sorted(targets, key=lambda x: x["age_created"], reverse=True)


def display_targets(targets: list[dict], memory_dir: str) -> None:
    print(f"削除対象: {len(targets)}件\n")
    for i, t in enumerate(targets, 1):
        rel = os.path.relpath(t["filepath"], memory_dir)
        print(f"[{i}] {t['title']}")
        print(f"     理由: {t['reason']}")
        print(f"     share_score={t['share_score']} | access_count={t['access_count']}")
        print(f"     パス: {rel}")
        print()


def main():
    parser = argparse.ArgumentParser(description="不要な記憶ファイルを削除する")
    parser.add_argument("--scope", default="home",
                        choices=["home", "all"],
                        help="対象スコープ (default: home)")
    parser.add_argument("--inactive-days", type=int, default=None,
                        help="未参照記憶の保持日数（省略時: config 値）")
    parser.add_argument("--archived-days", type=int, default=None,
                        help="archived記憶の保持日数（省略時: config 値）")
    parser.add_argument("--duplicates-only", action="store_true",
                        help="重複検出モード（類似度 >= 0.85 のペアを検出、低品質側を削除候補に）")
    parser.add_argument("--dedup-threshold", type=float, default=0.85,
                        help="重複判定の類似度閾値 (default: 0.85)")
    parser.add_argument("--quality-threshold", type=float, default=None,
                        help="品質スコア閾値（この値未満を削除候補に）")
    parser.add_argument("--dry-run", action="store_true",
                        help="削除せず対象を表示するだけ")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="確認なしで削除（CI用）")
    args = parser.parse_args()

    cfg = memory_utils.load_config()
    inactive_days = args.inactive_days or cfg["cleanup_inactive_days"]
    archived_days = args.archived_days or cfg["cleanup_archived_days"]

    all_targets = []
    for memory_dir in memory_utils.get_memory_dirs(args.scope):
        if not os.path.isdir(memory_dir):
            continue
        
        if args.duplicates_only:
            targets = find_duplicate_targets(memory_dir, args.dedup_threshold)
        else:
            targets = find_cleanup_targets(memory_dir, inactive_days, archived_days,
                                          quality_threshold=args.quality_threshold)
        
        if targets:
            home_dir = memory_utils._get_home_dir()
            scope_label = os.path.relpath(memory_dir, home_dir) \
                if memory_dir.startswith(home_dir) else memory_dir
            print(f"\n=== {scope_label} ===")
            display_targets(targets, memory_dir)
            all_targets.extend(targets)

    if not all_targets:
        print("削除対象がありません。")
        return

    if args.dry_run:
        print(f"[ドライラン] 実際には削除しません。")
        print(f"削除対象合計: {len(all_targets)}件")
        return

    # share_score が高いものは警告
    high_score = [t for t in all_targets if t["share_score"] >= 50]
    if high_score:
        print(f"警告: share_score >= 50 のファイルが {len(high_score)}件 含まれています。")
        print("削除前に promote_memory.py で昇格を検討してください。\n")

    if args.yes:
        confirm = "y"
    else:
        confirm = input(f"{len(all_targets)}件のファイルを削除しますか？ [y/N] ").strip().lower()

    if confirm != "y":
        print("キャンセルしました。")
        return

    deleted = 0
    errors = 0
    for t in all_targets:
        # share_score が高い場合はファイルごとに確認（--yes なし）
        if not args.yes and t["share_score"] >= 50:
            ans = input(f"  「{t['title']}」(score={t['share_score']}) 本当に削除？ [y/N] ").strip().lower()
            if ans != "y":
                print(f"  スキップ: {t['title']}")
                continue
        try:
            memory_dir = memory_utils.find_memory_dir(t["filepath"])
            os.remove(t["filepath"])
            # インデックスからも削除
            if memory_dir:
                memory_utils.update_index_entry(memory_dir, t["filepath"])
            # 空カテゴリディレクトリを削除（競合を無視して try/except で保護）
            cat_dir = os.path.dirname(t["filepath"])
            if os.path.isdir(cat_dir) and not any(
                f for f in os.listdir(cat_dir) if not f.startswith(".")
            ):
                try:
                    os.rmdir(cat_dir)
                except OSError:
                    pass  # 別プロセスによる割り込みなど競合時は無視
            deleted += 1
        except OSError as e:
            print(f"  削除失敗: {t['filepath']} - {e}", file=sys.stderr)
            errors += 1

    print(f"\n完了: {deleted}件削除 / {errors}件エラー")


if __name__ == "__main__":
    main()
