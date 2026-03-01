#!/usr/bin/env python3
"""push 操作: ローカルスキルをリモートリポジトリへ共有する。"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess

from registry import load_registry
from node_identity import get_node_id


def push_skill(
    skill_path: str,
    repo_name: str,
    branch_strategy: str = "new_branch",
    commit_msg: str | None = None,
) -> None:
    """
    skill_path: プッシュするスキルフォルダのパス
    repo_name: プッシュ先リポジトリ名（レジストリの name）
    branch_strategy: "new_branch" or "direct"
    """
    reg = load_registry()
    repo = next((r for r in reg["repositories"] if r["name"] == repo_name), None)
    if not repo:
        print(f"❌ リポジトリ '{repo_name}' が見つかりません")
        return

    if repo.get("readonly", False):
        print(f"❌ リポジトリ '{repo_name}' は readonly です。push できません")
        return

    skill_md = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md):
        print(f"❌ SKILL.md が見つかりません: {skill_path}")
        return

    skill_name = os.path.basename(skill_path.rstrip("\\/"))

    # push 用は一時ディレクトリを使用（キャッシュとは別）
    temp_work = os.path.join(
        os.environ.get("TEMP", os.environ.get("TMPDIR", "/tmp")),
        "agent-skill-push",
    )
    clone_dir = os.path.join(temp_work, f"push-{repo_name}")
    if os.path.exists(clone_dir):
        shutil.rmtree(clone_dir)

    subprocess.run([
        "git", "clone", "--depth", "1",
        "--branch", repo["branch"],
        repo["url"], clone_dir,
    ], check=True)

    push_branch = repo["branch"]
    if branch_strategy == "new_branch":
        push_branch = f"add-skill/{skill_name}"
        subprocess.run(["git", "checkout", "-b", push_branch], cwd=clone_dir, check=True)

    dest = os.path.join(clone_dir, repo["skill_root"], skill_name)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(skill_path, dest)

    # 不要ファイル除外
    for pattern in ["__pycache__", ".DS_Store", "*.pyc", "node_modules"]:
        for match in glob.glob(os.path.join(dest, "**", pattern), recursive=True):
            if os.path.isdir(match):
                shutil.rmtree(match)
            else:
                os.remove(match)

    if not commit_msg:
        commit_msg = f"Add skill: {skill_name}"

    # Node IDをコミットメッセージに付与してノード追跡を可能にする
    node_id = get_node_id()
    if node_id:
        commit_msg = f"{commit_msg}\n\nnode-id: {node_id}"

    subprocess.run(["git", "add", "."], cwd=clone_dir, check=True)

    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=clone_dir)
    if diff.returncode == 0:
        print("ℹ️ 変更がありません。プッシュをスキップします")
        shutil.rmtree(temp_work, ignore_errors=True)
        return

    subprocess.run(["git", "commit", "-m", commit_msg], cwd=clone_dir, check=True)
    subprocess.run(["git", "push", "origin", push_branch], cwd=clone_dir, check=True)

    commit_hash = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=clone_dir, capture_output=True, text=True,
    ).stdout.strip()

    shutil.rmtree(temp_work, ignore_errors=True)

    print(f"\n🚀 push 完了")
    print(f"   スキル:     {skill_name}")
    print(f"   リポジトリ: {repo_name} ({repo['url']})")
    print(f"   ブランチ:   {push_branch}")
    print(f"   コミット:   {commit_hash}")
    if branch_strategy == "new_branch":
        print(f"   💡 PR/MR を作成してマージしてください")
