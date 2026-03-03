#!/usr/bin/env python3
"""
recall_memory.py - キーワードで記憶を検索するスクリプト

recall すると access_count が加算され、share_score も自動更新される。
ワークスペース記憶で見つからない場合、home/shared を自動検索する。

Usage:
  python recall_memory.py "JWT 認証"
  python recall_memory.py "バグ" --category bug-investigation
  python recall_memory.py "API" --scope workspace --limit 5
  python recall_memory.py "デプロイ" --scope all  # 全スコープ検索
  python recall_memory.py "デプロイ" --full        # 全文表示
  python recall_memory.py "設計" --no-track        # access_count 更新しない
"""

import argparse
import os
import sys
from dataclasses import dataclass, field

import memory_utils


@dataclass
class Memory:
    filepath: str
    id: str = ""
    title: str = ""
    created: str = ""
    updated: str = ""
    status: str = "active"
    scope: str = "workspace"
    tags: list = field(default_factory=list)
    access_count: int = 0
    share_score: int = 0
    summary: str = ""
    body: str = ""
    score: int = 0
    memory_dir: str = ""   # どのスコープのメモリーディレクトリか


def load_memories(scope: str, category: str = None, status_filter: str = None) -> list[Memory]:
    memories = []
    for memory_dir in memory_utils.get_memory_dirs(scope):
        if not os.path.isdir(memory_dir):
            continue
        for fpath, rel_cat in memory_utils.iter_memory_files(memory_dir, category):
            with open(fpath, encoding="utf-8") as f:
                text = f.read()
            meta, body = memory_utils.parse_frontmatter(text)
            status = meta.get("status", "active")
            if status_filter and status != status_filter:
                continue
            tags = meta.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            memories.append(Memory(
                filepath=fpath,
                id=meta.get("id", ""),
                title=meta.get("title", os.path.basename(fpath)),
                created=meta.get("created", ""),
                updated=meta.get("updated", ""),
                status=status,
                scope=meta.get("scope", "workspace"),
                tags=tags,
                access_count=int(meta.get("access_count", 0)),
                share_score=int(meta.get("share_score", 0)),
                summary=meta.get("summary", ""),
                body=body,
                memory_dir=memory_dir,
            ))
    return memories


def score_memory(mem: Memory, keywords: list[str]) -> int:
    score = 0
    text_title = mem.title.lower()
    text_summary = mem.summary.lower()
    text_tags = " ".join(mem.tags).lower()
    text_body = mem.body.lower()

    for kw in keywords:
        kw = kw.lower()
        if kw in text_title:
            score += 10
        if kw in text_summary:
            score += 6
        if kw in text_tags:
            score += 4
        score += min(text_body.count(kw), 5)
    return score


def track_access(mem: Memory) -> None:
    """access_count をインクリメントし share_score を再計算する"""
    new_count = mem.access_count + 1
    today = memory_utils.today_str()
    # 新しい share_score を計算（access_count 更新後）
    pseudo_meta = {
        "tags": mem.tags,
        "access_count": new_count,
        "status": mem.status,
    }
    new_score = memory_utils.compute_share_score(pseudo_meta, mem.body)
    memory_utils.update_frontmatter_fields(mem.filepath, {
        "access_count": new_count,
        "last_accessed": today,
        "share_score": new_score,
    })


def format_result(mem: Memory, index: int, memory_dir: str, full: bool = False) -> str:
    rel_path = os.path.relpath(mem.filepath, memory_dir)
    scope_label = f"[{mem.scope}]"
    lines = [
        f"[{index}] {scope_label} {mem.title} (match_score: {mem.score}, share_score: {mem.share_score})",
        f"    Path: {rel_path}",
        f"    Created: {mem.created} | Updated: {mem.updated} | Status: {mem.status}",
        f"    Tags: {', '.join(mem.tags) if mem.tags else 'なし'} | access_count: {mem.access_count}",
        f"    Summary: {mem.summary}",
    ]
    if full:
        lines += ["", "    --- 全文 ---"] + [f"    {line}" for line in mem.body.splitlines()]
    return "\n".join(lines)


def auto_sync_and_retry(query: str, keywords: list[str], limit: int) -> list[Memory]:
    """ワークスペースで見つからない場合、shared を自動同期して検索する"""
    cfg = memory_utils.load_config()
    shared_dir = memory_utils.get_memory_dir("shared")

    # まず shared のローカルキャッシュから検索
    if os.path.isdir(shared_dir):
        memories = load_memories("shared", status_filter="active")
        for m in memories:
            m.score = score_memory(m, keywords)
        results = sorted([m for m in memories if m.score > 0],
                         key=lambda m: m.score, reverse=True)[:limit]
        if results:
            return results

    # shared がない/見つからなければ git pull を試みる
    remote = cfg.get("shared_remote", "")
    if remote:
        print("  → shared を git pull して再検索します...")
        ok, msg = memory_utils.git_pull_shared(
            shared_dir, remote, cfg.get("shared_branch", "main")
        )
        if ok:
            memories = load_memories("shared", status_filter="active")
            for m in memories:
                m.score = score_memory(m, keywords)
            results = sorted([m for m in memories if m.score > 0],
                             key=lambda m: m.score, reverse=True)[:limit]
            if results:
                return results
    return []


def main():
    parser = argparse.ArgumentParser(description="記憶をキーワード検索する")
    parser.add_argument("query", help="検索クエリ（スペース区切りで複数キーワード）")
    parser.add_argument("--category", help="カテゴリを絞り込む")
    parser.add_argument("--scope", default="workspace",
                        choices=["workspace", "home", "shared", "all"],
                        help="検索スコープ (default: workspace)")
    parser.add_argument("--status", default="active",
                        choices=["active", "archived", "deprecated", "all"],
                        help="ステータスフィルタ (default: active)")
    parser.add_argument("--limit", type=int, default=10, help="最大表示件数 (default: 10)")
    parser.add_argument("--full", action="store_true", help="全文を表示する")
    parser.add_argument("--no-track", action="store_true",
                        help="access_count を更新しない（参照ログを残さない）")
    args = parser.parse_args()

    keywords = args.query.split()
    status = None if args.status == "all" else args.status

    memories = load_memories(args.scope, category=args.category, status_filter=status)
    for mem in memories:
        mem.score = score_memory(mem, keywords)

    results = sorted([m for m in memories if m.score > 0],
                     key=lambda m: m.score, reverse=True)[:args.limit]

    # ワークスペース検索で0件の場合は自動フォールバック
    auto_synced = False
    if not results and args.scope == "workspace":
        print(f"「{args.query}」: ワークスペースに記憶なし → home/shared を検索します...\n")
        results = auto_sync_and_retry(args.query, keywords, args.limit)
        auto_synced = bool(results)

    if not results:
        print(f"「{args.query}」に関連する記憶が見つかりませんでした。")
        if args.scope == "workspace":
            print("保存するには: python save_memory.py --title '...' --summary '...'")
        sys.exit(0)

    source_note = "（shared からフォールバック）" if auto_synced else ""
    print(f"「{args.query}」の検索結果: {len(results)}件{source_note}\n")

    for i, mem in enumerate(results, 1):
        mem_dir = mem.memory_dir if mem.memory_dir else memory_utils.get_memory_dir(mem.scope)
        print(format_result(mem, i, mem_dir, full=args.full))
        print()

    # access_count 追跡（--no-track でスキップ）
    if not args.no_track:
        for mem in results:
            try:
                track_access(mem)
            except Exception:
                pass  # トラッキング失敗はサイレントに無視


if __name__ == "__main__":
    main()
