#!/usr/bin/env python3
"""
recall_memory.py - キーワードで記憶を検索するスクリプト

Usage:
  python recall_memory.py "JWT 認証"
  python recall_memory.py "バグ" --category bug-investigation
  python recall_memory.py "API" --status active --limit 5
  python recall_memory.py "デプロイ" --full   # 全文表示
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional


MEMORIES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "memories")


@dataclass
class Memory:
    filepath: str
    id: str = ""
    title: str = ""
    created: str = ""
    updated: str = ""
    status: str = "active"
    tags: list = field(default_factory=list)
    summary: str = ""
    body: str = ""
    score: int = 0


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """YAMLフロントマターをパースする（PyYAML不要のシンプル実装）"""
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


def load_memories(category: str = None, status_filter: str = None) -> list[Memory]:
    """memories/ 以下の全記憶ファイルを読み込む"""
    memories = []
    if not os.path.isdir(MEMORIES_DIR):
        return memories

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

            meta, body = parse_frontmatter(text)
            mem = Memory(
                filepath=fpath,
                id=meta.get("id", ""),
                title=meta.get("title", fname),
                created=meta.get("created", ""),
                updated=meta.get("updated", ""),
                status=meta.get("status", "active"),
                tags=meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
                summary=meta.get("summary", ""),
                body=body,
            )
            if status_filter and mem.status != status_filter:
                continue
            memories.append(mem)

    return memories


def score_memory(mem: Memory, keywords: list[str]) -> int:
    """キーワードとの関連度スコアを計算する"""
    score = 0
    text_title = mem.title.lower()
    text_summary = mem.summary.lower()
    text_tags = " ".join(mem.tags).lower()
    text_body = mem.body.lower()

    for kw in keywords:
        kw = kw.lower()
        if kw in text_title:
            score += 10  # タイトルマッチは高スコア
        if kw in text_summary:
            score += 6   # サマリーマッチ
        if kw in text_tags:
            score += 4   # タグマッチ
        count = text_body.count(kw)
        score += min(count * 1, 5)  # 本文マッチ（上限5点）

    return score


def recall(query: str, category: str = None, status_filter: str = "active",
           limit: int = 10, full: bool = False) -> list[Memory]:
    """クエリに関連する記憶を検索して返す"""
    keywords = query.split()
    memories = load_memories(category=category, status_filter=status_filter)

    for mem in memories:
        mem.score = score_memory(mem, keywords)

    # スコア0（無関係）を除外し、スコア降順でソート
    relevant = [m for m in memories if m.score > 0]
    relevant.sort(key=lambda m: m.score, reverse=True)

    return relevant[:limit]


def format_result(mem: Memory, index: int, full: bool = False) -> str:
    rel_path = os.path.relpath(mem.filepath, MEMORIES_DIR)
    lines = [
        f"[{index}] {mem.title} (score: {mem.score})",
        f"    Path: memories/{rel_path}",
        f"    Created: {mem.created} | Updated: {mem.updated} | Status: {mem.status}",
        f"    Tags: {', '.join(mem.tags) if mem.tags else 'なし'}",
        f"    Summary: {mem.summary}",
    ]
    if full:
        lines.append("")
        lines.append("    --- 全文 ---")
        for line in mem.body.splitlines():
            lines.append(f"    {line}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="記憶をキーワード検索する")
    parser.add_argument("query", help="検索クエリ（スペース区切りで複数キーワード）")
    parser.add_argument("--category", help="カテゴリを絞り込む")
    parser.add_argument("--status", default="active",
                        choices=["active", "archived", "deprecated", "all"],
                        help="ステータスフィルタ (default: active)")
    parser.add_argument("--limit", type=int, default=10, help="最大表示件数 (default: 10)")
    parser.add_argument("--full", action="store_true", help="全文を表示する")
    args = parser.parse_args()

    status = None if args.status == "all" else args.status
    results = recall(args.query, category=args.category,
                     status_filter=status, limit=args.limit, full=args.full)

    if not results:
        print(f"「{args.query}」に関連する記憶が見つかりませんでした。")
        sys.exit(0)

    print(f"「{args.query}」の検索結果: {len(results)}件\n")
    for i, mem in enumerate(results, 1):
        print(format_result(mem, i, full=args.full))
        print()


if __name__ == "__main__":
    main()
