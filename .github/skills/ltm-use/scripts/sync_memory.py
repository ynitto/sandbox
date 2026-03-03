#!/usr/bin/env python3
"""
sync_memory.py - git共有領域からナレッジを自動更新するスクリプト

git remote (shared_remote) の記憶を ~/.agent-memory/shared/ に pull し、
ローカルの home 記憶と差分をレポートする。

Usage:
  # 共有領域を更新して差分を確認
  python sync_memory.py

  # 新しい記憶を home にも取り込む
  python sync_memory.py --import-to-home

  # git remote を設定する（初回）
  python sync_memory.py --set-remote git@github.com:org/shared-memories.git

  # 特定キーワードに関連する shared 記憶を検索
  python sync_memory.py --search "JWT 認証"

  # push（明示的な許可が必要）
  python sync_memory.py --push
"""

import argparse
import os
import shutil
import sys

import memory_utils


def sync_from_remote(cfg: dict) -> tuple[bool, str]:
    """git remote から shared ディレクトリを更新する"""
    shared_dir = memory_utils.get_memory_dir("shared")
    remote = cfg.get("shared_remote", "")
    branch = cfg.get("shared_branch", "main")
    return memory_utils.git_pull_shared(shared_dir, remote, branch)


def find_new_memories(shared_dir: str, home_dir: str) -> list[dict]:
    """shared にあって home にない記憶を返す"""
    home_ids = set()
    for fpath, _ in memory_utils.iter_memory_files(home_dir):
        with open(fpath, encoding="utf-8") as f:
            meta, _ = memory_utils.parse_frontmatter(f.read())
        home_ids.add(meta.get("id", ""))
        home_ids.add(meta.get("promoted_from", ""))  # 昇格元も追跡

    new_memories = []
    for fpath, rel_cat in memory_utils.iter_memory_files(shared_dir):
        with open(fpath, encoding="utf-8") as f:
            text = f.read()
        meta, body = memory_utils.parse_frontmatter(text)
        mem_id = meta.get("id", "")
        if mem_id not in home_ids:
            new_memories.append({
                "filepath": fpath,
                "rel_cat": rel_cat,
                "id": mem_id,
                "title": meta.get("title", ""),
                "summary": meta.get("summary", ""),
                "share_score": meta.get("share_score", 0),
            })
    return new_memories


def import_to_home(memories: list[dict], shared_dir: str, home_dir: str) -> int:
    """shared の記憶を home にコピーする"""
    imported = 0
    for m in memories:
        rel = os.path.relpath(m["filepath"], shared_dir)
        dst = os.path.join(home_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(m["filepath"], dst)
        memory_utils.update_frontmatter_fields(dst, {
            "scope": "home",
            "promoted_from": m["id"],
        })
        imported += 1
    return imported


def search_shared(query: str, shared_dir: str) -> list[dict]:
    """shared ディレクトリからキーワード検索する（スコアリング）"""
    keywords = query.lower().split()
    results = []
    for fpath, rel_cat in memory_utils.iter_memory_files(shared_dir):
        with open(fpath, encoding="utf-8") as f:
            text = f.read()
        meta, body = memory_utils.parse_frontmatter(text)
        title = meta.get("title", "").lower()
        summary = meta.get("summary", "").lower()
        tags = " ".join(meta.get("tags", [])).lower()
        full = (title + " " + summary + " " + tags + " " + body.lower())

        score = 0
        for kw in keywords:
            if kw in title:
                score += 10
            if kw in summary:
                score += 6
            if kw in tags:
                score += 4
            score += min(full.count(kw), 5)

        if score > 0:
            results.append({
                "filepath": fpath,
                "title": meta.get("title", ""),
                "summary": meta.get("summary", ""),
                "score": score,
            })
    return sorted(results, key=lambda x: x["score"], reverse=True)


def push_shared(shared_dir: str, branch: str) -> None:
    """shared ディレクトリを git push する"""
    import subprocess
    result = subprocess.run(
        ["git", "-C", shared_dir, "push", "origin", branch],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        print(f"push 成功: {result.stdout.strip()}")
    else:
        print(f"push 失敗: {result.stderr.strip()}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="git共有領域の記憶を同期する")
    parser.add_argument("--set-remote", metavar="URL",
                        help="git remote URL を設定して終了")
    parser.add_argument("--search", metavar="QUERY",
                        help="shared 記憶をキーワード検索")
    parser.add_argument("--import-to-home", action="store_true",
                        help="shared にあって home にない記憶を home に取り込む")
    parser.add_argument("--push", action="store_true",
                        help="shared の変更を git push する")
    parser.add_argument("--no-pull", action="store_true",
                        help="git pull をスキップ（ローカルの shared のみ確認）")
    args = parser.parse_args()

    # remote 設定
    if args.set_remote:
        cfg = memory_utils.load_config()
        cfg["shared_remote"] = args.set_remote
        memory_utils.save_config(cfg)
        print(f"shared_remote を設定しました: {args.set_remote}")
        print(f"設定ファイル: ~/.agent-memory/config.json")
        return

    cfg = memory_utils.load_config()
    shared_dir = memory_utils.get_memory_dir("shared")
    home_dir = memory_utils.get_memory_dir("home")

    # キーワード検索モード（pull なし）
    if args.search:
        if not os.path.isdir(shared_dir):
            print("shared ディレクトリが存在しません。先に同期してください。")
            sys.exit(1)
        results = search_shared(args.search, shared_dir)
        if not results:
            print(f"「{args.search}」に一致する shared 記憶が見つかりませんでした。")
        else:
            print(f"「{args.search}」の shared 検索結果: {len(results)}件\n")
            for i, r in enumerate(results, 1):
                print(f"[{i}] {r['title']} (score: {r['score']})")
                print(f"     {r['summary']}")
        return

    # push モード
    if args.push:
        if not os.path.isdir(os.path.join(shared_dir, ".git")):
            print("shared ディレクトリが git リポジトリではありません。", file=sys.stderr)
            sys.exit(1)
        branch = cfg.get("shared_branch", "main")
        push_shared(shared_dir, branch)
        return

    # --- 通常同期 ---
    if not args.no_pull:
        remote = cfg.get("shared_remote", "")
        if not remote:
            print("shared_remote が未設定です。")
            print("設定方法: python sync_memory.py --set-remote <URL>")
            print("\nローカルの shared ディレクトリのみ確認します。\n")
        else:
            print(f"git pull: {remote} ({cfg.get('shared_branch', 'main')})")
            ok, msg = sync_from_remote(cfg)
            print(f"  {'成功' if ok else '失敗'}: {msg}\n")

    if not os.path.isdir(shared_dir):
        print("shared ディレクトリが存在しません。")
        return

    # 差分チェック
    os.makedirs(home_dir, exist_ok=True)
    new_memories = find_new_memories(shared_dir, home_dir)

    if not new_memories:
        print("新しい shared 記憶はありません（home と同期済み）。")
        return

    print(f"新しい shared 記憶: {len(new_memories)}件\n")
    for m in new_memories:
        print(f"  [{m['id']}] {m['title']}")
        print(f"        {m['summary']}")
    print()

    if args.import_to_home:
        n = import_to_home(new_memories, shared_dir, home_dir)
        print(f"{n}件を home に取り込みました。")
    else:
        print("home に取り込むには: python sync_memory.py --import-to-home")


if __name__ == "__main__":
    main()
