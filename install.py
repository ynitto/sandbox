#!/usr/bin/env python3
"""Agent Skills 初回インストールスクリプト。

git clone 後に実行してコアスキルをユーザー領域にセットアップする。

使い方:
    git clone https://github.com/myorg/agent-skills.git
    python agent-skills/install.py

処理内容:
    1. ~/.copilot/skills/ と ~/.copilot/cache/ を作成
    2. コアスキルをユーザー領域にコピー
    3. skill-registry.json を初期生成（ソースリポジトリを自動登録）
    4. セットアップ完了メッセージを表示

冪等: 既にインストール済みの場合はスキルを上書き更新、レジストリは既存設定を保持する。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

# ---- パス定義 ----

HOME = os.environ.get("USERPROFILE", os.path.expanduser("~"))
COPILOT_DIR = os.path.join(HOME, ".copilot")
SKILL_HOME = os.path.join(COPILOT_DIR, "skills")
CACHE_DIR = os.path.join(COPILOT_DIR, "cache")
REGISTRY_PATH = os.path.join(COPILOT_DIR, "skill-registry.json")
GITHUB_DIR = os.path.join(HOME, ".github")

# このスクリプト自身の位置からリポジトリルートを特定
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = SCRIPT_DIR  # install.py はリポジトリルートに配置
REPO_SKILLS_DIR = os.path.join(REPO_ROOT, ".github", "skills")

CORE_SKILLS = [
    "scrum-master",
    "git-skill-manager",
    "skill-creator",
    "skill-evaluator",
    "sprint-reviewer",
    "codebase-to-skill",
]


def ensure_directories() -> None:
    """必要なディレクトリを作成する。"""
    for d in [SKILL_HOME, CACHE_DIR]:
        os.makedirs(d, exist_ok=True)
        print(f"   {d}")


def copy_core_skills() -> list[dict]:
    """コアスキルをユーザー領域にコピーする。"""
    installed = []
    for name in CORE_SKILLS:
        src = os.path.join(REPO_SKILLS_DIR, name)
        skill_md = os.path.join(src, "SKILL.md")
        if not os.path.isfile(skill_md):
            print(f"   - {name}: SKILL.md が見つかりません、スキップ")
            continue

        dest = os.path.join(SKILL_HOME, name)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

        # コミットハッシュを取得
        commit_hash = "-"
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                commit_hash = result.stdout.strip()
        except FileNotFoundError:
            pass

        installed.append({
            "name": name,
            "source_repo": "origin",
            "source_path": f".github/skills/{name}",
            "commit_hash": commit_hash,
            "installed_at": datetime.now().isoformat(),
            "enabled": True,
            "pinned_commit": None,
        })
        print(f"   + {name}")

    return installed


def detect_repo_url() -> str | None:
    """clone 元リポジトリの URL を取得する。"""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return None


def setup_registry(installed_skills: list[dict]) -> None:
    """レジストリを初期生成または更新する。"""
    if os.path.isfile(REGISTRY_PATH):
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            reg = json.load(f)
        print("   既存レジストリを更新します")
    else:
        reg = {
            "version": 4,
            "repositories": [],
            "installed_skills": [],
            "core_skills": list(CORE_SKILLS),
            "remote_index": {},
            "profiles": {"default": ["*"]},
            "active_profile": None,
        }
        print("   新規レジストリを作成します")

    # core_skills を最新に
    reg["core_skills"] = list(CORE_SKILLS)

    # installed_skills を更新（既存エントリは上書き、新規は追加）
    existing = {s["name"]: s for s in reg.get("installed_skills", [])}
    for s in installed_skills:
        existing[s["name"]] = s
    reg["installed_skills"] = list(existing.values())

    # ソースリポジトリを自動登録
    repo_url = detect_repo_url()
    if repo_url:
        repo_names = [r["name"] for r in reg.get("repositories", [])]
        if "origin" not in repo_names:
            reg["repositories"].append({
                "name": "origin",
                "url": repo_url,
                "branch": "main",
                "skill_root": ".github/skills",
                "description": "インストール元リポジトリ（自動登録）",
                "readonly": False,
                "priority": 1,
            })
            print(f"   リポジトリ 'origin' を登録: {repo_url}")
        else:
            print(f"   リポジトリ 'origin' は登録済み")

    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)


def copy_copilot_instructions() -> bool:
    """copilot-instructions.md をユーザーホームにコピーする。"""
    src = os.path.join(REPO_ROOT, ".github", "copilot-instructions.md")
    if not os.path.isfile(src):
        return False
    os.makedirs(GITHUB_DIR, exist_ok=True)
    dest = os.path.join(GITHUB_DIR, "copilot-instructions.md")
    shutil.copy2(src, dest)
    print(f"   📋 {dest}")
    return True


def main() -> None:
    print("=" * 50)
    print("Agent Skills インストーラー")
    print("=" * 50)

    # スキルディレクトリの存在確認
    if not os.path.isdir(REPO_SKILLS_DIR):
        print(f"\nエラー: {REPO_SKILLS_DIR} が見つかりません")
        print("リポジトリのルートから実行してください:")
        print("  python install.py")
        sys.exit(1)

    # 1. ディレクトリ作成
    print("\n1. ディレクトリを作成...")
    ensure_directories()

    # 2. コアスキルをコピー
    print("\n2. コアスキルをインストール...")
    installed = copy_core_skills()
    if not installed:
        print("   エラー: インストールできるスキルがありません")
        sys.exit(1)

    # 3. レジストリ設定
    print("\n3. レジストリを設定...")
    setup_registry(installed)

    # 4. copilot-instructions.md をコピー
    print("\n4. copilot-instructions.md をコピー...")
    if not copy_copilot_instructions():
        print("   (ファイルが見つかりません、スキップ)")

    # 5. 完了
    print("\n" + "=" * 50)
    print(f"インストール完了: {len(installed)} 件のコアスキル")
    print("=" * 50)
    print(f"\nスキル:     {SKILL_HOME}")
    print(f"レジストリ: {REGISTRY_PATH}")
    print(f"\n次のステップ:")
    print(f'  - 「スキルをpullして」で最新スキルを取得')
    print(f'  - 「スクラムして」でscrum-masterを起動')
    print(f'  - 「スキルを探して」でリポジトリ内のスキルを検索')


if __name__ == "__main__":
    main()
