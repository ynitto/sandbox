#!/usr/bin/env python3
"""
build_index.py - 記憶インデックスを再構築するスクリプト

通常は recall/save/rate 時に自動的に増分更新される。
インデックスが壊れた場合や大量ファイルを手動追加した後に使用する。

Usage:
  python build_index.py                   # home を増分更新
  python build_index.py --scope all       # 全スコープを増分更新
  python build_index.py --force           # 完全再構築（既存インデックス破棄）
  python build_index.py --stats           # 統計表示のみ（再構築なし）
  python build_index.py --embeddings      # 埋め込み索引を作る / 補修する（ollama が要る）
"""

import argparse
import os
import time

import embeddings
import memory_utils
import similarity


def full_rebuild(memory_dir: str, rebuild_corpus: bool = True) -> dict:
    """インデックスを完全再構築する（stale チェックなし）
    
    Args:
        memory_dir: メモリーディレクトリのパス
        rebuild_corpus: コーパスも再構築するか（デフォルト: True）
    """
    entries = []
    skipped = 0
    for fpath, _ in memory_utils.iter_memory_files(memory_dir):
        try:
            entry = memory_utils.build_index_entry(fpath, memory_dir)
            entries.append(entry)
        except Exception as e:
            print(f"  警告: 読み込み失敗 {os.path.basename(fpath)} - {e}")
            skipped += 1
    index = {"version": 2, "entries": entries}
    memory_utils.save_index(memory_dir, index)
    if skipped:
        print(f"  スキップ: {skipped}件")
    
    # コーパスも再構築
    if rebuild_corpus and entries:
        print("  コーパス再構築中...")
        try:
            similarity.build_corpus(memory_dir)
        except Exception as e:
            print(f"  警告: コーパス構築失敗 - {e}")
    
    return index


def print_stats(memory_dir: str) -> None:
    index = memory_utils.load_index(memory_dir)
    if not index.get("entries"):
        index = memory_utils.refresh_index(memory_dir)

    entries = index.get("entries", [])
    if not entries:
        print(f"  記憶なし")
        return

    total = len(entries)
    active = sum(1 for e in entries if e.get("status") == "active")
    avg_score = sum(e.get("share_score", 0) for e in entries) / total
    top_score = max(e.get("share_score", 0) for e in entries)
    
    # コーパス統計
    corpus = similarity.load_corpus(memory_dir)
    if corpus.get("doc_vectors"):
        corpus_docs = len(corpus.get("doc_vectors", {}))
        corpus_terms = len(corpus.get("df", {}))
        corpus_built = corpus.get("built_at", "未構築")
        print(f"  コーパス    : {corpus_docs}件 / 語彙数={corpus_terms} / 更新={corpus_built}")
    else:
        print(f"  コーパス    : 未構築（build_index --force で構築）")
    emb = embeddings.status(memory_dir)
    if emb.get("enabled"):
        print(f"  埋め込み    : {emb['indexed']}件 / model={emb['model']} / 更新={emb['built_at']}"
              + (f" / 要補修 {emb['stale'] + emb['missing']}件（build_index --embeddings）"
                 if emb["stale"] + emb["missing"] else ""))
    else:
        print(f"  埋め込み    : 未構築（build_index --embeddings で構築。無くても recall は従来どおり）")
    total_access = sum(e.get("access_count", 0) for e in entries)
    total_corrections = sum(e.get("correction_count", 0) for e in entries)
    rated_positive = sum(1 for e in entries if e.get("user_rating", 0) > 0)
    rated_negative = sum(1 for e in entries if e.get("user_rating", 0) < 0)
    cfg = memory_utils.load_config()
    promote_threshold = cfg["semi_auto_promote_threshold"]
    promote_candidates = sum(1 for e in entries if e.get("share_score", 0) >= promote_threshold)

    print(f"  総記憶数   : {total}件 (active: {active}件)")
    print(f"  share_score: avg={avg_score:.1f} / max={top_score} / 昇格候補={promote_candidates}件")
    print(f"  参照数合計 : {total_access} 回")
    print(f"  ユーザー評価: +{rated_positive}件 / -{rated_negative}件")
    print(f"  修正回数合計: {total_corrections} 回")
    print(f"  インデックス: {index.get('built_at', '未構築')} ({index.get('count', 0)}件)")


def main():
    parser = argparse.ArgumentParser(description="記憶インデックスを再構築・確認する")
    parser.add_argument("--scope", default="home",
                        choices=["home", "shared", "all"],
                        help="対象スコープ (default: home)")
    parser.add_argument("--force", action="store_true",
                        help="既存インデックスを無視して完全再構築")
    parser.add_argument("--stats", action="store_true",
                        help="統計情報のみ表示（再構築しない）")
    parser.add_argument("--embeddings", action="store_true",
                        help="埋め込み索引を作る / 補修する（新規・本文が変わった記憶だけ埋め込む）")
    parser.add_argument("--embedding-model", default=None,
                        help="埋め込みモデル（既定は設定 embedding_model = bge-m3）")
    args = parser.parse_args()

    for memory_dir in memory_utils.get_memory_dirs(args.scope):
        home_dir = memory_utils._get_home_dir()
        rel = (os.path.relpath(memory_dir, home_dir)
               if memory_dir.startswith(home_dir) else memory_dir)
        print(f"\n=== {rel} ===")

        if not os.path.isdir(memory_dir):
            print("  ディレクトリが存在しません。スキップ。")
            continue

        if args.stats:
            print_stats(memory_dir)
            continue

        start = time.perf_counter()
        if args.force:
            print("  完全再構築中...")
            index = full_rebuild(memory_dir)
        else:
            print("  増分更新中...")
            index = memory_utils.refresh_index(memory_dir)
        elapsed = time.perf_counter() - start

        count = len(index.get("entries", []))
        print(f"  完了: {count}件 ({elapsed*1000:.0f}ms)")
        if args.embeddings or (args.force and embeddings.enabled(memory_dir)):
            model = args.embedding_model or memory_utils.load_config().get("embedding_model", "bge-m3")
            print(f"  埋め込み索引を更新中（{model}・新規と本文変更分だけ）...")
            t0 = time.perf_counter()
            try:
                st = embeddings.build(memory_dir, model)
            except Exception as e:  # noqa: BLE001 — ollama 停止 / モデル未取得 / タイムアウト
                print(f"  警告: 埋め込み索引を作れません（{e}）。recall は従来どおり動きます。"
                      " ollama serve と `ollama pull {model}` を確認してください")
            else:
                print(f"  完了: {st['embedded']}件を埋め込み / 索引 {st['indexed']}件 "
                      f"({time.perf_counter() - t0:.1f}s)")
        print_stats(memory_dir)


if __name__ == "__main__":
    main()
