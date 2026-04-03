#!/usr/bin/env python3
"""リポジトリ管理: add / clone_or_fetch / update_remote_index。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime

from registry import load_registry, save_registry, _cache_dir


def add_repo(
    name: str,
    url: str,
    branch: str = "main",
    skill_root: str = "skills",
    description: str = "",
    readonly: bool = False,
    priority: int = 100,
) -> None:
    reg = load_registry()
    if any(r["name"] == name for r in reg["repositories"]):
        print(f"'{name}' は既に登録済みです")
        return
    reg["repositories"].append({
        "name": name,
        "url": url,
        "branch": branch,
        "skill_root": skill_root,
        "description": description,
        "readonly": readonly,
        "priority": priority,
    })
    save_registry(reg)
    print(f"✅ リポジトリ '{name}' を登録しました（priority: {priority}）")


def clone_or_fetch(repo: dict) -> str:
    """
    キャッシュディレクトリにリポジトリを取得する。
    初回: git clone --depth 1
    2回目以降: git fetch + git reset --hard（高速）
    キャッシュが破損している場合: 削除して再clone
    """
    cache_dir = _cache_dir()
    repo_cache = os.path.join(cache_dir, repo["name"])
    os.makedirs(cache_dir, exist_ok=True)

    if os.path.isdir(os.path.join(repo_cache, ".git")):
        try:
            subprocess.run(
                ["git", "fetch", "origin", repo["branch"]],
                cwd=repo_cache, check=True,
                capture_output=True, text=True, encoding="utf-8",
            )
            subprocess.run(
                ["git", "reset", "--hard", f"origin/{repo['branch']}"],
                cwd=repo_cache, check=True,
                capture_output=True, text=True, encoding="utf-8",
            )
            return repo_cache
        except subprocess.CalledProcessError:
            shutil.rmtree(repo_cache, ignore_errors=True)

    subprocess.run([
        "git", "clone", "--depth", "1",
        "--branch", repo["branch"],
        repo["url"], repo_cache,
    ], check=True)
    return repo_cache


def update_remote_index(
    reg: dict, repo_name: str, repo_cache: str, skill_root: str
) -> None:
    """リモートインデックスを更新する。pull / search --refresh 時に呼ばれる。"""
    remote_index = reg.setdefault("remote_index", {})
    root = os.path.join(repo_cache, skill_root)
    if not os.path.isdir(root):
        return

    skills_list = []
    for entry in sorted(os.listdir(root)):
        skill_md = os.path.join(root, entry, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        with open(skill_md, encoding="utf-8") as f:
            content = f.read()
        desc = ""
        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).splitlines():
                if line.startswith("description:"):
                    desc = line[len("description:"):].strip()
                    break
        skills_list.append({"name": entry, "description": desc[:200]})

    remote_index[repo_name] = {
        "updated_at": datetime.now().isoformat(),
        "skills": skills_list,
    }


def remove_repo(name: str) -> None:
    reg = load_registry()
    before = len(reg["repositories"])
    reg["repositories"] = [r for r in reg["repositories"] if r["name"] != name]
    if len(reg["repositories"]) == before:
        print(f"❌ リポジトリ '{name}' が見つかりません")
        return
    save_registry(reg)
    print(f"✅ リポジトリ '{name}' を削除しました")


def list_repos() -> None:
    reg = load_registry()
    repos = reg.get("repositories", [])
    if not repos:
        print("   (登録済みリポジトリなし)")
        return
    print("📋 登録済みリポジトリ一覧\n")
    for r in repos:
        ro = "  [readonly]" if r.get("readonly") else ""
        print(f"   {r['name']:20s}  {r['url']}  (priority: {r.get('priority', 100)}){ro}")


# ---------------------------------------------------------------------------
# CLI エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="スキルリポジトリの登録・一覧・削除",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python repo.py add team-skills https://github.com/myorg/skills.git
  python repo.py add team-skills https://github.com/myorg/skills.git --readonly
  python repo.py add team-skills https://github.com/myorg/skills.git --priority 1 --skill-root .github/skills
  python repo.py list
  python repo.py remove team-skills
""",
    )
    sub = parser.add_subparsers(dest="command")

    add_p = sub.add_parser("add", help="リポジトリを登録する")
    add_p.add_argument("name", help="リポジトリの識別名")
    add_p.add_argument("url", help="リポジトリURL")
    add_p.add_argument("--branch", default="main", help="ブランチ名（デフォルト: main）")
    add_p.add_argument("--skill-root", default=".github/skills", dest="skill_root",
                       help="スキルのルートディレクトリ（デフォルト: .github/skills）")
    add_p.add_argument("--desc", default="", help="説明")
    add_p.add_argument("--readonly", action="store_true", help="参照専用にする")
    add_p.add_argument("--priority", type=int, default=100,
                       help="優先度（数値が小さいほど高優先。デフォルト: 100）")

    sub.add_parser("list", help="登録済みリポジトリを一覧表示する")

    rm_p = sub.add_parser("remove", help="リポジトリを削除する")
    rm_p.add_argument("name", help="削除するリポジトリの識別名")

    args = parser.parse_args()

    if args.command == "add":
        add_repo(
            name=args.name,
            url=args.url,
            branch=args.branch,
            skill_root=args.skill_root,
            description=args.desc,
            readonly=args.readonly,
            priority=args.priority,
        )
    elif args.command == "list":
        list_repos()
    elif args.command == "remove":
        remove_repo(args.name)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
