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
                capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "reset", "--hard", f"origin/{repo['branch']}"],
                cwd=repo_cache, check=True,
                capture_output=True, text=True,
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
