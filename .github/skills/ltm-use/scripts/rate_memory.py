#!/usr/bin/env python3
"""
rate_memory.py - ユーザー評価・修正フィードバックを記憶に記録するスクリプト

recall した記憶が「役立った」「誤りがあった」「修正が必要だった」場合に実行する。
評価は share_score に反映され、再指示・修正が多い記憶は昇格・推薦されにくくなる。

Usage:
  # 役立った記憶を評価（user_rating +1）
  python rate_memory.py --file memories/auth/jwt.md --good
  python rate_memory.py --id mem-20260303-001 --good

  # 誤りがあった・修正が必要（user_rating -1, correction_count +1）
  python rate_memory.py --file memories/auth/jwt.md --correction --note "JWTは30分に変更"

  # 役に立たなかった（user_rating -1）
  python rate_memory.py --file memories/auth/jwt.md --bad

  # スコープ指定でIDを検索
  python rate_memory.py --id mem-20260303-001 --scope all --good
"""

import argparse
import os
import re
import sys

import memory_utils


def find_by_id(mem_id: str, scope: str) -> str | None:
    """IDでファイルを検索する（インデックスがあれば高速検索）"""
    for memory_dir in memory_utils.get_memory_dirs(scope):
        index = memory_utils.refresh_index(memory_dir)
        for entry in index.get("entries", []):
            if entry.get("id") == mem_id:
                return os.path.join(memory_dir, entry["filepath"])
    return None


def apply_rating(filepath: str, good: bool = False, bad: bool = False,
                 correction: bool = False, note: str = "") -> dict:
    """評価を適用してファイルを更新する。更新内容を辞書で返す"""
    with open(filepath, encoding="utf-8") as f:
        text = f.read()
    meta, body = memory_utils.parse_frontmatter(text)

    user_rating = int(meta.get("user_rating", 0))
    correction_count = int(meta.get("correction_count", 0))

    if good:
        user_rating += 1
    elif bad or correction:
        user_rating -= 1
    if correction:
        correction_count += 1

    today = memory_utils.today_str()
    pseudo_meta = dict(meta, user_rating=user_rating, correction_count=correction_count)
    new_score = memory_utils.compute_share_score(pseudo_meta, body)

    updates = {
        "user_rating": user_rating,
        "correction_count": correction_count,
        "share_score": new_score,
        "updated": today,
    }

    if note:
        # 修正ログを本文に追記
        if "## 修正ログ" in body:
            body = body.rstrip() + f"\n- [{today}] {note}\n"
        else:
            body = body.rstrip() + f"\n\n## 修正ログ\n- [{today}] {note}\n"
        memory_utils.update_file_with_body(filepath, updates, body)
    else:
        memory_utils.update_frontmatter_fields(filepath, updates)

    return updates


def _scope_label(memory_dir: str) -> str:
    skill_dir = memory_utils.get_skill_dir()
    home_dir = memory_utils._get_home_dir()
    if memory_dir.startswith(skill_dir):
        return "workspace"
    return os.path.relpath(memory_dir, home_dir)


def main():
    parser = argparse.ArgumentParser(
        description="記憶にユーザー評価・修正フィードバックを記録する"
    )
    # ターゲット指定（いずれか1つ）
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--file", help="対象ファイルパス")
    target.add_argument("--id", help="記憶ID（mem-YYYYMMDD-NNN 形式）")

    # 評価種別（いずれか1つ）
    rating = parser.add_mutually_exclusive_group(required=True)
    rating.add_argument("--good", action="store_true", help="役立った（user_rating +1）")
    rating.add_argument("--bad", action="store_true", help="役立たなかった（user_rating -1）")
    rating.add_argument("--correction", action="store_true",
                        help="修正が必要だった（user_rating -1, correction_count +1）")

    parser.add_argument("--note", default="",
                        help="修正内容メモ（--correction と組み合わせて使用）")
    parser.add_argument("--scope", default="all",
                        choices=["workspace", "home", "shared", "all"],
                        help="--id 検索時のスコープ (default: all)")
    args = parser.parse_args()

    # ファイル解決
    if args.file:
        filepath = os.path.abspath(args.file)
        if not os.path.exists(filepath):
            print(f"ファイルが見つかりません: {args.file}", file=sys.stderr)
            sys.exit(1)
    else:
        filepath = find_by_id(args.id, args.scope)
        if not filepath:
            print(f"ID が見つかりません: {args.id}", file=sys.stderr)
            sys.exit(1)

    # 評価を適用
    updates = apply_rating(
        filepath,
        good=args.good,
        bad=args.bad,
        correction=args.correction,
        note=args.note,
    )

    # インデックスを更新
    memory_dir = memory_utils.find_memory_dir(filepath)
    if memory_dir:
        memory_utils.update_index_entry(memory_dir, filepath)

    # 結果表示
    rating_label = "良い ✓" if args.good else ("修正必要 ⚠" if args.correction else "悪い ✗")
    print(f"評価記録: {rating_label}")
    print(f"  ファイル: {filepath}")
    print(f"  user_rating={updates['user_rating']} | "
          f"correction_count={updates['correction_count']} | "
          f"share_score={updates['share_score']}")
    if args.note:
        print(f"  修正メモ: {args.note}")


if __name__ == "__main__":
    main()
