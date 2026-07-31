#!/usr/bin/env python3
"""push 操作: ローカルスキルをリモートリポジトリへ共有する。"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
import tempfile

from registry import (
    load_registry,
    _skill_home,
    _read_frontmatter_version,
    _version_tuple,
)
from node_identity import get_node_id


def push_skill(
    skill_path: str,
    repo_name: str,
    branch_strategy: str = "direct",
    commit_msg: str | None = None,
) -> None:
    """
    skill_path: プッシュするスキルフォルダのパス
    repo_name: プッシュ先リポジトリ名（レジストリの name）
    branch_strategy: "direct"（デフォルト: main へ直接 push）or "new_branch"（PR/MR ブランチを作成）
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
        cwd=clone_dir, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()

    shutil.rmtree(temp_work, ignore_errors=True)

    print(f"\n🚀 push 完了")
    print(f"   スキル:     {skill_name}")
    print(f"   リポジトリ: {repo_name} ({repo['url']})")
    print(f"   ブランチ:   {push_branch}")
    print(f"   コミット:   {commit_hash}")
    if branch_strategy == "new_branch":
        print(f"   💡 PR/MR を作成してマージしてください")


def push_all_skills(
    skill_names: list[str] | None = None,
    repo_names: list[str] | None = None,
    commit_msg: str | None = None,
) -> None:
    """セマンティックバージョンを比較して、ローカルが新しいスキルを登録済みリポジトリへ直接 push する。

    処理フロー:
      1. 書き込み可能なリポジトリを列挙する
      2. 各リポジトリについて:
         a. リモートの最新をクローンする
         b. スキルごとにローカルとリモートのセマンティックバージョンを比較する
         c. ローカルが新しい（またはリモートに存在しない）スキルのみをコピーする
         d. 変更をまとめて 1 コミットにして main ブランチへ直接 push する

    skill_names=None → インストール済みスキルを全て対象にする
    repo_names=None  → 書き込み可能な全リポジトリを対象にする
    """
    reg = load_registry()
    skill_home = _skill_home()

    # --- 書き込み可能リポジトリを選択 ---
    repos = [r for r in reg["repositories"] if not r.get("readonly", False)]
    if repo_names:
        repos = [r for r in repos if r["name"] in repo_names]

    if not repos:
        print("❌ push 可能なリポジトリが見つかりません（全リポジトリが readonly、または指定名が不正）")
        return

    # --- プッシュ対象スキルのパスを収集 ---
    skill_paths: list[str] = []
    if skill_names:
        for name in skill_names:
            path = os.path.join(skill_home, name)
            if os.path.isdir(path) and os.path.isfile(os.path.join(path, "SKILL.md")):
                skill_paths.append(path)
            else:
                print(f"⚠️ スキル '{name}' が見つかりません: {path}")
    else:
        if os.path.isdir(skill_home):
            for entry in sorted(os.listdir(skill_home)):
                path = os.path.join(skill_home, entry)
                if os.path.isdir(path) and os.path.isfile(os.path.join(path, "SKILL.md")):
                    skill_paths.append(path)

    if not skill_paths:
        print("❌ プッシュ対象のスキルが見つかりません")
        return

    temp_work = os.path.join(tempfile.gettempdir(), "agent-skill-push")

    for repo in repos:
        print(f"\n📦 リポジトリ: {repo['name']} ({repo['url']})")

        clone_dir = os.path.join(temp_work, f"push-{repo['name']}")
        if os.path.exists(clone_dir):
            shutil.rmtree(clone_dir)

        # --- リモートの最新をクローン ---
        print(f"  🔄 リモートの最新を取得中...")
        try:
            subprocess.run([
                "git", "clone", "--depth", "1",
                "--branch", repo["branch"],
                repo["url"], clone_dir,
            ], check=True, capture_output=True, text=True, encoding="utf-8")
        except subprocess.CalledProcessError as e:
            print(f"  ❌ クローン失敗: {e.stderr.strip()}")
            continue

        # --- バージョン比較してプッシュ対象を決定 ---
        skills_to_push: list[dict] = []
        for skill_path in skill_paths:
            skill_name = os.path.basename(skill_path.rstrip("\\/"))
            local_ver = _read_frontmatter_version(skill_path)
            remote_skill_path = os.path.join(clone_dir, repo["skill_root"], skill_name)
            remote_ver = (
                _read_frontmatter_version(remote_skill_path)
                if os.path.isdir(remote_skill_path)
                else None
            )

            if remote_ver is None or _version_tuple(local_ver) > _version_tuple(remote_ver):
                skills_to_push.append({
                    "name": skill_name,
                    "path": skill_path,
                    "local_ver": local_ver or "0.0.0",
                    "remote_ver": remote_ver,
                })

        if not skills_to_push:
            print(f"  ℹ️ プッシュが必要なスキルはありません（全て最新）")
            shutil.rmtree(clone_dir, ignore_errors=True)
            continue

        print(f"  📋 バージョン比較結果 — プッシュ対象 ({len(skills_to_push)} 件):")
        for s in skills_to_push:
            remote_label = f"v{s['remote_ver']}" if s["remote_ver"] else "(新規)"
            print(f"     {s['name']:30s}  {remote_label} → v{s['local_ver']}")

        # --- スキルフォルダをコピー ---
        for s in skills_to_push:
            dest = os.path.join(clone_dir, repo["skill_root"], s["name"])
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(s["path"], dest)

            # 不要ファイルを除外
            for pattern in ["__pycache__", ".DS_Store", "*.pyc", "node_modules"]:
                for match in glob.glob(os.path.join(dest, "**", pattern), recursive=True):
                    if os.path.isdir(match):
                        shutil.rmtree(match)
                    else:
                        os.remove(match)

        # --- コミットメッセージを生成 ---
        if commit_msg:
            msg = commit_msg
        else:
            names_str = ", ".join(s["name"] for s in skills_to_push)
            msg = f"Update skills: {names_str}"

        node_id = get_node_id()
        if node_id:
            msg = f"{msg}\n\nnode-id: {node_id}"

        # --- コミット & プッシュ ---
        subprocess.run(["git", "add", repo["skill_root"]], cwd=clone_dir, check=True)

        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=clone_dir)
        if diff.returncode == 0:
            print(f"  ℹ️ 変更がありません。プッシュをスキップします")
            shutil.rmtree(clone_dir, ignore_errors=True)
            continue

        subprocess.run(["git", "commit", "-m", msg], cwd=clone_dir, check=True)
        subprocess.run(["git", "push", "origin", repo["branch"]], cwd=clone_dir, check=True)

        commit_hash = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=clone_dir, capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()

        shutil.rmtree(clone_dir, ignore_errors=True)

        print(f"\n  🚀 push 完了")
        print(f"     ブランチ: {repo['branch']} (direct)")
        print(f"     コミット: {commit_hash}")
        for s in skills_to_push:
            remote_label = f"v{s['remote_ver']}" if s["remote_ver"] else "(新規)"
            print(f"     ✅ {s['name']:30s}  {remote_label} → v{s['local_ver']}")

    shutil.rmtree(temp_work, ignore_errors=True)
