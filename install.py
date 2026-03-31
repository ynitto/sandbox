#!/usr/bin/env python3
"""Agent Skills 初回インストールスクリプト。

git clone 後に実行してコアスキルをユーザー領域にセットアップする。

使い方:
    git clone https://github.com/myorg/agent-skills.git
    python agent-skills/install.py
    python agent-skills/install.py --agent claude   # Claude Code 用
    python agent-skills/install.py --agent codex    # Codex 用
    python agent-skills/install.py --agent kiro     # Kiro 用

処理内容:
    1. <agent_home>/skills/ と <agent_home>/cache/ を作成
    2. コアスキルをユーザー領域にコピー
    3. skill-registry.json を初期生成（ソースリポジトリ・パスを自動登録）
    4. セットアップ完了メッセージを表示

冪等: 既にインストール済みの場合はスキルを上書き更新、レジストリは既存設定を保持する。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

# ---- エージェント種別とインストール先ディレクトリ名のマッピング ----

AGENT_DIRS: dict[str, str] = {
    "copilot": ".copilot",
    "claude": ".claude",
    "codex": ".codex",
    "kiro": ".kiro",
}

# ---- このスクリプト自身の位置からリポジトリルートを特定 ----

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = SCRIPT_DIR  # install.py はリポジトリルートに配置
REPO_SKILLS_DIR = os.path.join(REPO_ROOT, ".github", "skills")

CORE_SKILLS = [
    "scrum-master",
    "git-skill-manager",
    "skill-creator",
    "requirements-definer",
    "skill-evaluator",
    "sprint-reviewer",
    "ltm-use",
]


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパースする。"""
    parser = argparse.ArgumentParser(
        description="Agent Skills インストーラー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
対応エージェント:
  copilot  GitHub Copilot  → ~/.copilot/  (デフォルト)
  claude   Claude Code     → ~/.claude/
  codex    Codex           → ~/.codex/
  kiro     Kiro            → ~/.kiro/
""",
    )
    parser.add_argument(
        "--agent",
        choices=list(AGENT_DIRS.keys()),
        default="copilot",
        metavar="AGENT",
        help="インストール対象エージェント: %(choices)s (default: %(default)s)",
    )
    return parser.parse_args()


def resolve_paths(agent_type: str) -> dict[str, str]:
    """エージェント種別に応じたインストールパスを解決する。

    Returns:
        {
            "user_home":    ユーザーホームディレクトリ,
            "agent_home":   エージェント専用ディレクトリ (~/.copilot 等),
            "skill_home":   スキルインストール先ディレクトリ,
            "cache_dir":    キャッシュディレクトリ,
            "registry_path": skill-registry.json のパス,
            "install_dir":  このリポジトリのルートディレクトリ,
        }
    """
    user_home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    agent_home = os.path.join(user_home, AGENT_DIRS[agent_type])
    return {
        "user_home": user_home,
        "agent_home": agent_home,
        "skill_home": os.path.join(agent_home, "skills"),
        "cache_dir": os.path.join(agent_home, "cache"),
        "registry_path": os.path.join(agent_home, "skill-registry.json"),
        "install_dir": REPO_ROOT,
    }


def ensure_directories(paths: dict[str, str]) -> None:
    """必要なディレクトリを作成する。"""
    for d in [paths["skill_home"], paths["cache_dir"]]:
        os.makedirs(d, exist_ok=True)
        print(f"   {d}")


def copy_core_skills(paths: dict[str, str]) -> list[dict]:
    """コアスキルをユーザー領域にコピーする。"""
    installed = []
    for name in CORE_SKILLS:
        src = os.path.join(REPO_SKILLS_DIR, name)
        skill_md = os.path.join(src, "SKILL.md")
        if not os.path.isfile(skill_md):
            print(f"   - {name}: SKILL.md が見つかりません、スキップ")
            continue

        dest = os.path.join(paths["skill_home"], name)
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


def setup_registry(
    installed_skills: list[dict],
    paths: dict[str, str],
    agent_type: str,
) -> None:
    """レジストリを初期生成または更新する。

    registry v6 の追加フィールド:
        agent_type:  インストール対象エージェント種別
        user_home:   ユーザーホームディレクトリ
        install_dir: このリポジトリのルートディレクトリ（自動更新の参照元）
        skill_home:  スキルインストール先ディレクトリ
    """
    registry_path = paths["registry_path"]

    if os.path.isfile(registry_path):
        with open(registry_path, encoding="utf-8") as f:
            reg = json.load(f)
        print("   既存レジストリを更新します")
    else:
        reg = {
            "version": 7,
            "agent_type": agent_type,
            "user_home": paths["user_home"],
            "install_dir": paths["install_dir"],
            "skill_home": paths["skill_home"],
            "repositories": [],
            "installed_skills": [],
            "core_skills": list(CORE_SKILLS),
            "remote_index": {},
            "profiles": {"default": ["*"]},
            "active_profile": None,
        }
        print("   新規レジストリを作成します")

    # バージョン・パス情報を常に最新に更新
    reg["version"] = 7
    reg["agent_type"] = agent_type
    reg["user_home"] = paths["user_home"]
    reg["install_dir"] = paths["install_dir"]
    reg["skill_home"] = paths["skill_home"]
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
            print("   リポジトリ 'origin' は登録済み")

    os.makedirs(os.path.dirname(registry_path), exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)


def copy_agent_instructions(paths: dict[str, str]) -> bool:
    """リポジトリ配下の指示ファイルをエージェント領域へコピーする。"""
    src_dir = os.path.join(REPO_ROOT, ".github", "instructions")
    dest_dir = os.path.join(paths["agent_home"], "instructions")

    copied = False
    if not os.path.isdir(src_dir):
        return copied

    for name in sorted(os.listdir(src_dir)):
        if not name.endswith(".md"):
            continue

        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            continue

        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, name)
        shutil.copy2(src, dest)
        print(f"   {dest}")
        copied = True

    return copied


def main() -> None:
    args = parse_args()
    agent_type = args.agent
    paths = resolve_paths(agent_type)

    print("=" * 50)
    print(f"Agent Skills インストーラー  [エージェント: {agent_type}]")
    print("=" * 50)

    # スキルディレクトリの存在確認
    if not os.path.isdir(REPO_SKILLS_DIR):
        print(f"\nエラー: {REPO_SKILLS_DIR} が見つかりません")
        print("リポジトリのルートから実行してください:")
        print("  python install.py")
        sys.exit(1)

    # 1. ディレクトリ作成
    print("\n1. ディレクトリを作成...")
    ensure_directories(paths)

    # 2. コアスキルをコピー
    print("\n2. コアスキルをインストール...")
    installed = copy_core_skills(paths)
    if not installed:
        print("   エラー: インストールできるスキルがありません")
        sys.exit(1)

    # 3. レジストリ設定
    print("\n3. レジストリを設定...")
    setup_registry(installed, paths, agent_type)

    # 4. 指示ファイルをコピー
    print("\n4. エージェント指示ファイルをコピー...")
    if not copy_agent_instructions(paths):
        print("   (対応するファイルが見つかりません、スキップ)")

    # 完了
    print("\n" + "=" * 50)
    print(f"インストール完了: {len(installed)} 件のコアスキル")
    print("=" * 50)
    print(f"\nエージェント:   {agent_type}")
    print(f"スキル:         {paths['skill_home']}")
    print(f"レジストリ:     {paths['registry_path']}")
    print(f"インストール元: {paths['install_dir']}")
    print("\n次のステップ:")
    print('  - 「スキルをpullして」で最新スキルを取得')
    print('  - 「スクラムして」でscrum-masterを起動')
    print('  - 「スキルを探して」でリポジトリ内のスキルを検索')


if __name__ == "__main__":
    main()
