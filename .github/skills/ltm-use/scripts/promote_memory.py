#!/usr/bin/env python3
"""
promote_memory.py - ワークスペース記憶をホームまたはsharedへ昇格するスクリプト

Usage:
  # 昇格候補を表示（share_score >= 70）
  python promote_memory.py --list

  # 半自動: 候補を確認しながら昇格 (score >= 70)
  python promote_memory.py

  # 自動昇格: score >= 85 のものを自動で昇格
  python promote_memory.py --auto

  # 特定ファイルを指定して昇格
  python promote_memory.py --file memories/auth/jwt.md --target home

  # home → shared へ昇格（git commit も実施）
  python promote_memory.py --scope home --target shared --auto
"""

import argparse
import os
import shutil
import sys

import memory_utils


def load_candidate_memories(src_scope: str, threshold: int) -> list[dict]:
    """src_scope から share_score >= threshold の記憶を返す"""
    src_dir = memory_utils.get_memory_dir(src_scope)
    candidates = []
    for fpath, rel_cat in memory_utils.iter_memory_files(src_dir):
        with open(fpath, encoding="utf-8") as f:
            text = f.read()
        meta, body = memory_utils.parse_frontmatter(text)
        score = memory_utils.compute_share_score(meta, body)
        # フロントマターの share_score も更新
        if meta.get("share_score", 0) != score:
            memory_utils.update_frontmatter_fields(fpath, {"share_score": score})
            memory_utils.update_index_entry(src_dir, fpath)
        candidates.append({
            "filepath": fpath,
            "rel_cat": rel_cat,
            "title": meta.get("title", os.path.basename(fpath)),
            "summary": meta.get("summary", ""),
            "score": score,
            "status": meta.get("status", "active"),
        })
    return sorted(
        [c for c in candidates if c["score"] >= threshold],
        key=lambda x: x["score"],
        reverse=True,
    )


def promote_file(src_path: str, src_scope: str, target_scope: str) -> str:
    """記憶ファイルを src_scope → target_scope へコピーし、scope フィールドを更新する"""
    src_dir = memory_utils.get_memory_dir(src_scope)
    dst_dir = memory_utils.get_memory_dir(target_scope)
    rel = os.path.relpath(src_path, src_dir)
    dst_path = os.path.join(dst_dir, rel)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(src_path, encoding="utf-8") as f:
        src_meta, _ = memory_utils.parse_frontmatter(f.read())
    src_id = src_meta.get("id", "")
    shutil.copy2(src_path, dst_path)
    memory_utils.update_frontmatter_fields(dst_path, {
        "scope": target_scope,
        "promoted_from": src_id,
    })
    return dst_path


def main():
    parser = argparse.ArgumentParser(description="記憶を上位スコープへ昇格する")
    parser.add_argument("--scope", default="workspace",
                        choices=["workspace", "home"],
                        help="昇格元スコープ (default: workspace)")
    parser.add_argument("--target", default=None,
                        choices=["home", "shared"],
                        help="昇格先スコープ（省略時: workspace→home, home→shared）")
    parser.add_argument("--list", action="store_true", help="昇格候補を表示して終了")
    parser.add_argument("--auto", action="store_true",
                        help="share_score >= auto_promote_threshold を自動昇格")
    parser.add_argument("--file", help="特定ファイルのパスを直接指定して昇格")
    parser.add_argument("--threshold", type=int, default=None,
                        help="昇格候補の最低スコア（省略時は config 値を使用）")
    args = parser.parse_args()

    cfg = memory_utils.load_config()
    semi_threshold = args.threshold or cfg["semi_auto_promote_threshold"]
    auto_threshold = cfg["auto_promote_threshold"]

    # target 省略時の自動解決
    target_scope = args.target or ("shared" if args.scope == "home" else "home")

    # 昇格先ディレクトリを確認・作成
    dst_dir = memory_utils.get_memory_dir(target_scope)
    os.makedirs(dst_dir, exist_ok=True)

    # --- 特定ファイル指定 ---
    if args.file:
        if not os.path.exists(args.file):
            print(f"ファイルが見つかりません: {args.file}", file=sys.stderr)
            sys.exit(1)
        dst = promote_file(args.file, args.scope, target_scope)
        print(f"昇格完了: {args.file} → {dst}")
        if target_scope == "shared":
            ok, msg = memory_utils.git_commit_shared(
                dst_dir, f"feat: promote memory from {args.scope}"
            )
            print(f"git commit: {'OK' if ok else 'FAILED'} - {msg}")
        return

    # --- 候補スキャン ---
    candidates = load_candidate_memories(args.scope, semi_threshold)

    if not candidates:
        print(f"昇格候補がありません（score >= {semi_threshold}）")
        return

    print(f"昇格候補: {len(candidates)}件（{args.scope} → {target_scope}）\n")
    for i, c in enumerate(candidates, 1):
        auto_mark = " [AUTO]" if c["score"] >= auto_threshold else ""
        print(f"[{i}]{auto_mark} score={c['score']:2d} | {c['title']}")
        print(f"     {c['summary']}")
        print(f"     {c['filepath']}")
        print()

    if args.list:
        return

    promoted = []
    skipped = []

    for c in candidates:
        if args.auto and c["score"] >= auto_threshold:
            dst = promote_file(c["filepath"], args.scope, target_scope)
            promoted.append((c["filepath"], dst))
            print(f"[自動昇格] {c['title']} → {dst}")
        elif not args.auto:
            ans = input(f"「{c['title']}」(score={c['score']}) を昇格しますか？ [y/N/q] ").strip().lower()
            if ans == "q":
                print("中断しました。")
                break
            elif ans == "y":
                dst = promote_file(c["filepath"], args.scope, target_scope)
                promoted.append((c["filepath"], dst))
                print(f"  → 昇格: {dst}")
            else:
                skipped.append(c["title"])

    print(f"\n結果: 昇格 {len(promoted)}件 / スキップ {len(skipped)}件")

    if promoted and target_scope == "shared":
        ans = "y" if args.auto else input("git commit しますか？ [Y/n] ").strip().lower()
        if ans != "n":
            ok, msg = memory_utils.git_commit_shared(
                dst_dir,
                f"feat: promote {len(promoted)} memories from {args.scope}"
            )
            print(f"git commit: {'OK' if ok else 'FAILED'} - {msg}")
            if ok:
                print("共有するには: git -C ~/.agent-memory/shared push origin main")


if __name__ == "__main__":
    main()
