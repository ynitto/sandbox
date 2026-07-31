#!/usr/bin/env python3
"""
list_memories.py - 記憶一覧を表示するスクリプト

Usage:
  python list_memories.py                        # ホーム記憶（active）
  python list_memories.py --scope all            # 全スコープ
  python list_memories.py --scope shared         # 共有記憶
  python list_memories.py --category auth        # カテゴリ絞り込み
  python list_memories.py --status archived      # ステータス絞り込み
  python list_memories.py --tag jwt              # タグ絞り込み
  python list_memories.py --stats                # 統計のみ
  python list_memories.py --promote-candidates   # 昇格候補のみ表示
"""

import argparse
import os
import sys
from collections import defaultdict

import memory_utils


def load_all_memories(scope: str = "home", category: str = None,
                      status_filter: str = None, tag: str = None) -> dict:
    """スコープ→カテゴリ→記憶 の入れ子辞書で返す"""
    result = defaultdict(lambda: defaultdict(list))

    for memory_dir in memory_utils.get_memory_dirs(scope):
        if not os.path.isdir(memory_dir):
            continue
        scope_label = _scope_label(memory_dir)

        for fpath, rel_cat in memory_utils.iter_memory_files(memory_dir, category):
            with open(fpath, encoding="utf-8") as f:
                text = f.read()
            meta, body = memory_utils.parse_frontmatter(text)
            status = meta.get("status", "active")
            tags = meta.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]

            if status_filter and status_filter != "all" and status != status_filter:
                continue
            if tag and tag not in tags:
                continue

            result[scope_label][rel_cat].append({
                "filepath": fpath,
                "id": meta.get("id", ""),
                "title": meta.get("title", os.path.basename(fpath)),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "status": status,
                "scope": meta.get("scope", "home"),
                "tags": tags,
                "access_count": int(meta.get("access_count", 0)),
                "share_score": int(meta.get("share_score", 0)),
                "summary": meta.get("summary", ""),
            })

    return {k: dict(v) for k, v in result.items()}


def _scope_label(memory_dir: str) -> str:
    home_root = memory_utils.HOME_MEMORY_ROOT
    if memory_dir.startswith(home_root):
        rel = os.path.relpath(memory_dir, home_root)
        return f"$HOME/.copilot/memory/{rel.replace(os.sep, '/')}"
    return memory_dir


def print_stats(result: dict) -> None:
    total = sum(len(m) for cats in result.values() for m in cats.values())
    active = sum(
        1 for cats in result.values()
        for mems in cats.values()
        for m in mems if m["status"] == "active"
    )
    print(f"総記憶数: {total}件 (active: {active}件)")
    for scope_label, cats in sorted(result.items()):
        cat_count = len(cats)
        mem_count = sum(len(v) for v in cats.values())
        print(f"\n  [{scope_label}] {mem_count}件 / {cat_count}カテゴリ")
        for cat in sorted(cats.keys()):
            print(f"    {cat}/  ({len(cats[cat])}件)")


def print_list(result: dict, verbose: bool = False,
               promote_candidates: bool = False) -> None:
    if not result:
        print("記憶が見つかりませんでした。")
        return

    total = sum(len(m) for cats in result.values() for m in cats.values())
    print(f"記憶一覧: {total}件\n")

    for scope_label, cats in sorted(result.items()):
        print(f"### スコープ: {scope_label}")
        for cat in sorted(cats.keys()):
            mems = cats[cat]
            if promote_candidates:
                mems = [m for m in mems if m["share_score"] >= 70]
                if not mems:
                    continue
            print(f"\n## {cat}/ ({len(mems)}件)")
            for m in sorted(mems, key=lambda x: x["updated"], reverse=True):
                rel = os.path.relpath(m["filepath"],
                                      memory_utils.get_memory_dir(m["scope"]))
                status_icon = {"active": "●", "archived": "○", "deprecated": "✗"}.get(
                    m["status"], "?"
                )
                promote_mark = " [昇格候補]" if m["share_score"] >= 70 else ""
                auto_mark = " [自動昇格対象]" if m["share_score"] >= 85 else ""
                print(f"  {status_icon} [{m['id']}] {m['title']}{promote_mark}{auto_mark}")
                print(f"      {m['summary']}")
                if verbose:
                    print(f"      更新: {m['updated']} | access: {m['access_count']} "
                          f"| share_score: {m['share_score']}")
                    print(f"      タグ: {', '.join(m['tags'])}")
                    print(f"      パス: {rel}")
        print()


def main():
    parser = argparse.ArgumentParser(description="記憶の一覧を表示する")
    parser.add_argument("--scope", default="home",
                        choices=["home", "shared", "all"],
                        help="スコープ (default: home)")
    parser.add_argument("--category", help="カテゴリを絞り込む")
    parser.add_argument("--status", default="active",
                        choices=["active", "archived", "deprecated", "all"],
                        help="ステータスフィルタ (default: active)")
    parser.add_argument("--tag", help="タグで絞り込む")
    parser.add_argument("--stats", action="store_true", help="統計情報のみ表示")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細表示")
    parser.add_argument("--promote-candidates", action="store_true",
                        help="share_score >= 70 の昇格候補のみ表示")
    args = parser.parse_args()

    status = None if args.status == "all" else args.status
    result = load_all_memories(
        scope=args.scope, category=args.category,
        status_filter=status, tag=args.tag,
    )

    if args.stats:
        print_stats(result)
    else:
        print_list(result, verbose=args.verbose,
                   promote_candidates=args.promote_candidates)


if __name__ == "__main__":
    main()
