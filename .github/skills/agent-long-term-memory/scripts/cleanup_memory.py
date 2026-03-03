#!/usr/bin/env python3
"""
cleanup_memory.py - 不要な記憶を削除してディスク領域を節約するスクリプト

削除基準（AND条件でなくOR）:
  1. access_count == 0 かつ作成から N 日以上経過（デフォルト30日）
  2. status == archived かつ更新から N 日以上経過（デフォルト60日）
  3. status == deprecated

Usage:
  # ドライラン（削除せず対象を表示）
  python cleanup_memory.py --dry-run

  # ワークスペース記憶をクリーンアップ
  python cleanup_memory.py

  # ホーム記憶もクリーンアップ
  python cleanup_memory.py --scope home

  # 全スコープ
  python cleanup_memory.py --scope all

  # 基準日数をカスタマイズ
  python cleanup_memory.py --inactive-days 14 --archived-days 30 --dry-run

  # 非インタラクティブ（CI用）
  python cleanup_memory.py --yes
"""

import argparse
import os
import sys

import memory_utils


def find_cleanup_targets(memory_dir: str, inactive_days: int, archived_days: int) -> list[dict]:
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
        if status == "deprecated":
            reason = f"status=deprecated"
        elif status == "archived" and age_updated >= archived_days:
            reason = f"archived かつ {age_updated}日間更新なし（基準: {archived_days}日）"
        elif access_count == 0 and age_created >= inactive_days:
            reason = f"未参照 かつ 作成から{age_created}日経過（基準: {inactive_days}日）"

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
    parser.add_argument("--scope", default="workspace",
                        choices=["workspace", "home", "all"],
                        help="対象スコープ (default: workspace)")
    parser.add_argument("--inactive-days", type=int, default=None,
                        help="未参照記憶の保持日数（省略時: config 値）")
    parser.add_argument("--archived-days", type=int, default=None,
                        help="archived記憶の保持日数（省略時: config 値）")
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
        targets = find_cleanup_targets(memory_dir, inactive_days, archived_days)
        if targets:
            scope_label = os.path.relpath(memory_dir, os.path.expanduser("~")) \
                if memory_dir.startswith(os.path.expanduser("~")) else memory_dir
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
            # 空カテゴリディレクトリを削除
            cat_dir = os.path.dirname(t["filepath"])
            if os.path.isdir(cat_dir) and not any(
                f for f in os.listdir(cat_dir) if not f.startswith(".")
            ):
                os.rmdir(cat_dir)
            deleted += 1
        except OSError as e:
            print(f"  削除失敗: {t['filepath']} - {e}", file=sys.stderr)
            errors += 1

    print(f"\n完了: {deleted}件削除 / {errors}件エラー")


if __name__ == "__main__":
    main()
