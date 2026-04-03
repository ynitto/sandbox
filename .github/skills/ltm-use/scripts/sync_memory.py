#!/usr/bin/env python3
"""
sync_memory.py - git共有領域からナレッジを自動更新するスクリプト

skill-registry.json に登録されたリポジトリ（git-skill-manager と共通）を使用して
shared 記憶を同期する。複数リポジトリ・readonly 対応。

skill-registry.json に repositories が未登録の場合は、
<AGENT_HOME>/memory/config.json の shared_remote をフォールバックとして使用する。

Usage:
  # 全リポジトリを pull して差分確認
  python sync_memory.py

  # 特定リポジトリのみ同期
  python sync_memory.py --repo origin

  # 新しい shared 記憶を home に取り込む
  python sync_memory.py --import-to-home

  # git push（readonly でないリポジトリへ）
  python sync_memory.py --push [--repo origin]

  # 全 shared 記憶をキーワード検索
  python sync_memory.py --search "JWT 認証"

  # git remote を config.json に設定（skill-registry.json 未設定時のフォールバック用）
  python sync_memory.py --set-remote git@github.com:org/shared-memories.git
"""

import argparse
import os
import shutil
import sys

import memory_utils


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


def search_shared(query: str, repos: list[dict]) -> list[dict]:
    """全 shared リポジトリからキーワード検索する"""
    keywords = query.lower().split()
    results = []
    for repo in repos:
        shared_dir = repo["memory_dir"]
        if not os.path.isdir(shared_dir):
            continue
        for fpath, rel_cat in memory_utils.iter_memory_files(shared_dir):
            with open(fpath, encoding="utf-8") as f:
                text = f.read()
            meta, body = memory_utils.parse_frontmatter(text)
            title = meta.get("title", "").lower()
            summary = meta.get("summary", "").lower()
            tags = " ".join(meta.get("tags", [])).lower()
            full = title + " " + summary + " " + tags + " " + body.lower()

            score = 0
            for kw in keywords:
                if kw in title:    score += 10
                if kw in summary:  score += 6
                if kw in tags:     score += 4
                score += min(full.count(kw), 5)

            if score > 0:
                results.append({
                    "filepath": fpath,
                    "repo": repo["name"],
                    "title": meta.get("title", ""),
                    "summary": meta.get("summary", ""),
                    "score": score,
                })
    return sorted(results, key=lambda x: x["score"], reverse=True)


def main():
    parser = argparse.ArgumentParser(description="git共有領域の記憶を同期する")
    parser.add_argument("--set-remote", metavar="URL",
                        help="git remote URL を config.json に設定して終了"
                             "（skill-registry.json 未設定時のフォールバック用）")
    parser.add_argument("--repo", metavar="NAME",
                        help="対象リポジトリ名を指定（省略時: 全リポジトリ）")
    parser.add_argument("--search", metavar="QUERY",
                        help="全 shared 記憶をキーワード検索")
    parser.add_argument("--import-to-home", action="store_true",
                        help="shared にあって home にない記憶を home に取り込む")
    parser.add_argument("--push", action="store_true",
                        help="書き込み可能なリポジトリに git push する")
    parser.add_argument("--no-pull", action="store_true",
                        help="git pull をスキップ（ローカルの shared のみ確認）")
    args = parser.parse_args()

    # remote 設定（フォールバック用: skill-registry.json が未設定の場合に使用）
    if args.set_remote:
        cfg = memory_utils.load_config()
        cfg["shared_remote"] = args.set_remote
        memory_utils.save_config(cfg)
        print(f"shared_remote を設定しました: {args.set_remote}")
        print(f"設定ファイル: {os.path.join(memory_utils.HOME_MEMORY_ROOT, 'config.json')}")
        print(f"注意: skill-registry.json に repositories が設定されている場合はそちらが優先されます。")
        return

    # リポジトリ一覧を取得
    repos = memory_utils.get_shared_repos()
    if not repos:
        print("共有リポジトリが設定されていません。")
        print("skill-registry.json の repositories に追加するか、以下で設定してください:")
        print("  python sync_memory.py --set-remote <URL>")
        sys.exit(1)

    # --repo で絞り込み
    if args.repo:
        repos = [r for r in repos if r["name"] == args.repo]
        if not repos:
            print(f"リポジトリ '{args.repo}' が見つかりません。", file=sys.stderr)
            sys.exit(1)

    # キーワード検索モード（pull なし）
    if args.search:
        results = search_shared(args.search, repos)
        if not results:
            print(f"「{args.search}」に一致する shared 記憶が見つかりませんでした。")
        else:
            print(f"「{args.search}」の shared 検索結果: {len(results)}件\n")
            for i, r in enumerate(results, 1):
                repo_label = f" [{r['repo']}]" if len(repos) > 1 else ""
                print(f"[{i}]{repo_label} {r['title']} (score: {r['score']})")
                print(f"     {r['summary']}")
        return

    # push モード
    if args.push:
        for repo in repos:
            label = f"[{repo['name']}]"
            if repo["readonly"]:
                print(f"{label} readonly のためスキップ")
                continue
            local_dir = repo["local_dir"]
            if not os.path.isdir(os.path.join(local_dir, ".git")):
                print(f"{label} git リポジトリが見つかりません（先に sync が必要）")
                continue
            ok, msg = memory_utils.git_push_repo(repo)
            print(f"{label} push {'成功' if ok else '失敗'}: {msg}")
        return

    # --- 通常同期（pull + 差分確認）---
    if not args.no_pull:
        for repo in repos:
            label = f"[{repo['name']}]"
            suffix = " (readonly)" if repo["readonly"] else ""
            print(f"{label} git pull: {repo['url']} ({repo['branch']}){suffix}")
            ok, msg = memory_utils.git_pull_repo(repo)
            print(f"  {'成功' if ok else '失敗'}: {msg}\n")

    home_dir = memory_utils.get_memory_dir("home")
    os.makedirs(home_dir, exist_ok=True)

    all_new: list[dict] = []
    for repo in repos:
        shared_dir = repo["memory_dir"]
        if not os.path.isdir(shared_dir):
            continue
        new_memories = find_new_memories(shared_dir, home_dir)
        if new_memories:
            label = f"[{repo['name']}] " if len(repos) > 1 else ""
            print(f"{label}新しい shared 記憶: {len(new_memories)}件\n")
            for m in new_memories:
                print(f"  [{m['id']}] {m['title']}")
                print(f"        {m['summary']}")
            print()
            all_new.extend(new_memories)

    if not all_new:
        print("新しい shared 記憶はありません（home と同期済み）。")
        return

    if args.import_to_home:
        imported = 0
        for repo in repos:
            shared_dir = repo["memory_dir"]
            repo_new = [m for m in all_new
                        if m["filepath"].startswith(shared_dir + os.sep)]
            if repo_new:
                n = import_to_home(repo_new, shared_dir, home_dir)
                imported += n
        print(f"{imported}件を home に取り込みました。")
    else:
        print("home に取り込むには: python sync_memory.py --import-to-home")


if __name__ == "__main__":
    main()
