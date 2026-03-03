#!/usr/bin/env python3
"""
list_memories.py - 記憶一覧を表示するスクリプト

Usage:
  python list_memories.py                    # 全記憶を一覧表示
  python list_memories.py --category auth    # カテゴリ絞り込み
  python list_memories.py --status archived  # ステータス絞り込み
  python list_memories.py --tag jwt          # タグ絞り込み
  python list_memories.py --stats            # 統計のみ表示
"""

import argparse
import os
import sys
from collections import defaultdict


MEMORIES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "memories")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    meta = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2].strip()
            for line in fm_text.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip().strip('"')
                    if val.startswith("[") and val.endswith("]"):
                        inner = val[1:-1]
                        meta[key] = [v.strip() for v in inner.split(",") if v.strip()]
                    else:
                        meta[key] = val
    return meta, body


def load_all_memories(category: str = None, status_filter: str = None, tag: str = None):
    memories_by_cat = defaultdict(list)
    if not os.path.isdir(MEMORIES_DIR):
        print(f"memories/ ディレクトリが存在しません: {MEMORIES_DIR}")
        return {}

    for root, dirs, files in os.walk(MEMORIES_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            rel_cat = os.path.relpath(root, MEMORIES_DIR)

            if category and rel_cat != category:
                continue

            with open(fpath, "r", encoding="utf-8") as f:
                text = f.read()

            meta, _ = parse_frontmatter(text)
            status = meta.get("status", "active")
            tags = meta.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]

            if status_filter and status_filter != "all" and status != status_filter:
                continue
            if tag and tag not in tags:
                continue

            memories_by_cat[rel_cat].append({
                "filepath": fpath,
                "id": meta.get("id", ""),
                "title": meta.get("title", fname),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "status": status,
                "tags": tags,
                "summary": meta.get("summary", ""),
            })

    return dict(memories_by_cat)


def print_stats(memories_by_cat: dict) -> None:
    total = sum(len(v) for v in memories_by_cat.values())
    active = sum(
        1 for mems in memories_by_cat.values()
        for m in mems if m["status"] == "active"
    )
    print(f"総記憶数: {total}件 (active: {active}件, categories: {len(memories_by_cat)})")
    print()
    print("カテゴリ別:")
    for cat in sorted(memories_by_cat.keys()):
        count = len(memories_by_cat[cat])
        print(f"  {cat}/  ({count}件)")


def print_list(memories_by_cat: dict, verbose: bool = False) -> None:
    if not memories_by_cat:
        print("記憶が見つかりませんでした。")
        return

    total = sum(len(v) for v in memories_by_cat.values())
    print(f"記憶一覧: {total}件\n")

    for cat in sorted(memories_by_cat.keys()):
        mems = memories_by_cat[cat]
        print(f"## {cat}/ ({len(mems)}件)")
        for m in sorted(mems, key=lambda x: x["updated"], reverse=True):
            rel = os.path.relpath(m["filepath"], MEMORIES_DIR)
            status_icon = {"active": "●", "archived": "○", "deprecated": "✗"}.get(
                m["status"], "?"
            )
            print(f"  {status_icon} [{m['id']}] {m['title']}")
            print(f"      {m['summary']}")
            if verbose:
                print(f"      更新: {m['updated']} | タグ: {', '.join(m['tags'])}")
                print(f"      パス: memories/{rel}")
        print()


def main():
    parser = argparse.ArgumentParser(description="記憶の一覧を表示する")
    parser.add_argument("--category", help="カテゴリを絞り込む")
    parser.add_argument("--status", default="active",
                        choices=["active", "archived", "deprecated", "all"],
                        help="ステータスフィルタ (default: active)")
    parser.add_argument("--tag", help="タグで絞り込む")
    parser.add_argument("--stats", action="store_true", help="統計情報のみ表示")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細表示")
    args = parser.parse_args()

    status = None if args.status == "all" else args.status
    memories_by_cat = load_all_memories(
        category=args.category, status_filter=status, tag=args.tag
    )

    if args.stats:
        print_stats(memories_by_cat)
    else:
        print_list(memories_by_cat, verbose=args.verbose)


if __name__ == "__main__":
    main()
