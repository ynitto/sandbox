#!/usr/bin/env python3
"""Agent Skills 初回インストールスクリプト。

git clone 後に実行してコアスキルをユーザー領域にセットアップする。

使い方:
    git clone https://github.com/myorg/agent-skills.git
    python agent-skills/install.py              # デフォルト（~/.agent-skills/）
    python agent-skills/install.py --agent claude    # ~/.claude/ に展開
    python agent-skills/install.py --agent codex     # ~/.codex/ に展開
    python agent-skills/install.py --agent copilot   # ~/.copilot/ に展開（後方互換）
    python agent-skills/install.py --agent kiro      # ~/.kiro/ に展開

処理内容:
    1. <base>/skills/ と <base>/cache/ を作成
    2. コアスキルをユーザー領域にコピー
    3. skill-registry.json を初期生成（ソースリポジトリを自動登録）
    4. エージェント向け instructions ファイルをコピー
    5. セットアップ完了メッセージを表示

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

# ---- パス定義 ----

# このスクリプト自身の位置からリポジトリルートを特定
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = SCRIPT_DIR  # install.py はリポジトリルートに配置
REPO_SKILLS_DIR = os.path.join(REPO_ROOT, ".github", "skills")

# ---- エージェント設定 ----

_HOME = os.environ.get("USERPROFILE", os.path.expanduser("~"))

# エージェントごとのベースディレクトリと instructions の配置先
AGENT_CONFIGS: dict[str, dict] = {
    "claude": {
        "base_dir": os.path.join(_HOME, ".claude"),
        "instructions_dest": os.path.join(_HOME, ".claude", "CLAUDE.md"),
        "instructions_src": os.path.join(REPO_ROOT, ".github", "copilot-instructions.md"),
    },
    "codex": {
        "base_dir": os.path.join(_HOME, ".codex"),
        "instructions_dest": os.path.join(_HOME, ".codex", "instructions.md"),
        "instructions_src": os.path.join(REPO_ROOT, ".github", "copilot-instructions.md"),
    },
    "copilot": {
        "base_dir": os.path.join(_HOME, ".copilot"),
        "instructions_dest": os.path.join(_HOME, ".copilot", "copilot-instructions.md"),
        "instructions_src": os.path.join(REPO_ROOT, ".github", "copilot-instructions.md"),
    },
    "kiro": {
        "base_dir": os.path.join(_HOME, ".kiro"),
        "instructions_dest": os.path.join(_HOME, ".kiro", "instructions.md"),
        "instructions_src": os.path.join(REPO_ROOT, ".github", "copilot-instructions.md"),
    },
}

DEFAULT_BASE_DIR = os.path.join(_HOME, ".agent-skills")

CORE_SKILLS = [
    "scrum-master",
    "git-skill-manager",
    "skill-creator",
    "requirements-definer",
    "skill-recruiter",
    "skill-evaluator",
    "generating-skills-from-copilot-logs",
    "sprint-reviewer",
    "codebase-to-skill",
    "ltm-use",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agent Skills インストーラー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
エージェント別のデフォルトインストール先:
  claude  → ~/.claude/
  codex   → ~/.codex/
  copilot → ~/.copilot/
  kiro    → ~/.kiro/
  (省略時) → ~/.agent-skills/
        """,
    )
    parser.add_argument(
        "--agent",
        choices=list(AGENT_CONFIGS.keys()),
        default=None,
        help="対象エージェント (claude / codex / copilot / kiro)。省略時は ~/.agent-skills/ を使用。",
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help="インストール先ベースディレクトリを直接指定（--agent より優先）。",
    )
    return parser.parse_args()


def resolve_paths(args: argparse.Namespace) -> tuple[str, str, str, str | None]:
    """(base_dir, skill_home, cache_dir, registry_path, instructions_dest) を返す。"""
    if args.base_dir:
        base_dir = os.path.expanduser(args.base_dir)
        instructions_dest = None
    elif args.agent:
        cfg = AGENT_CONFIGS[args.agent]
        base_dir = cfg["base_dir"]
        instructions_dest = cfg["instructions_dest"]
    else:
        base_dir = DEFAULT_BASE_DIR
        instructions_dest = None

    skill_home = os.path.join(base_dir, "skills")
    cache_dir = os.path.join(base_dir, "cache")
    registry_path = os.path.join(base_dir, "skill-registry.json")
    return base_dir, skill_home, cache_dir, registry_path, instructions_dest


def ensure_directories(skill_home: str, cache_dir: str) -> None:
    """必要なディレクトリを作成する。"""
    for d in [skill_home, cache_dir]:
        os.makedirs(d, exist_ok=True)
        print(f"   {d}")


def copy_core_skills(skill_home: str) -> list[dict]:
    """コアスキルをユーザー領域にコピーする。"""
    installed = []
    for name in CORE_SKILLS:
        src = os.path.join(REPO_SKILLS_DIR, name)
        skill_md = os.path.join(src, "SKILL.md")
        if not os.path.isfile(skill_md):
            print(f"   - {name}: SKILL.md が見つかりません、スキップ")
            continue

        dest = os.path.join(skill_home, name)
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
    registry_path: str,
    installed_skills: list[dict],
    base_dir: str,
    skill_home: str,
    cache_dir: str,
    instructions_dest: str | None,
) -> None:
    """レジストリを初期生成または更新する。"""
    if os.path.isfile(registry_path):
        with open(registry_path, encoding="utf-8") as f:
            reg = json.load(f)
        print("   既存レジストリを更新します")
    else:
        reg = {
            "version": 7,
            "repositories": [],
            "installed_skills": [],
            "core_skills": list(CORE_SKILLS),
            "remote_index": {},
            "profiles": {"default": ["*"]},
            "active_profile": None,
        }
        print("   新規レジストリを作成します")

    # paths セクションを書き込む（常に最新の値で上書き）
    reg["paths"] = {
        "repo_root": REPO_ROOT,
        "base": base_dir,
        "skills": skill_home,
        "cache": cache_dir,
        "instructions": instructions_dest,
    }
    print(f"   paths.base:    {base_dir}")
    print(f"   paths.skills:  {skill_home}")

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

    os.makedirs(os.path.dirname(registry_path), exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)


def copy_agent_instructions(instructions_dest: str | None) -> bool:
    """エージェント向け instructions ファイルをコピーする。"""
    if not instructions_dest:
        return False
    src = os.path.join(REPO_ROOT, ".github", "copilot-instructions.md")
    if not os.path.isfile(src):
        return False
    os.makedirs(os.path.dirname(instructions_dest), exist_ok=True)
    shutil.copy2(src, instructions_dest)
    print(f"   {instructions_dest}")
    return True


def main() -> None:
    print("=" * 50)
    print("Agent Skills インストーラー")
    print("=" * 50)

    args = parse_args()
    base_dir, skill_home, cache_dir, registry_path, instructions_dest = resolve_paths(args)

    agent_label = args.agent or "default"
    print(f"\nエージェント: {agent_label}")
    print(f"インストール先: {base_dir}")

    # AGENT_SKILLS_HOME をサブプロセス向けに設定
    os.environ["AGENT_SKILLS_HOME"] = base_dir

    # スキルディレクトリの存在確認
    if not os.path.isdir(REPO_SKILLS_DIR):
        print(f"\nエラー: {REPO_SKILLS_DIR} が見つかりません")
        print("リポジトリのルートから実行してください:")
        print("  python install.py")
        sys.exit(1)

    # 1. ディレクトリ作成
    print("\n1. ディレクトリを作成...")
    ensure_directories(skill_home, cache_dir)

    # 2. コアスキルをコピー
    print("\n2. コアスキルをインストール...")
    installed = copy_core_skills(skill_home)
    if not installed:
        print("   エラー: インストールできるスキルがありません")
        sys.exit(1)

    # 3. レジストリ設定
    print("\n3. レジストリを設定...")
    setup_registry(registry_path, installed, base_dir, skill_home, cache_dir, instructions_dest)

    # 4. instructions ファイルをコピー
    print("\n4. エージェント instructions をコピー...")
    if not copy_agent_instructions(instructions_dest):
        if instructions_dest:
            print("   (ソースファイルが見つかりません、スキップ)")
        else:
            print("   (--agent 未指定のためスキップ)")

    # 完了
    print("\n" + "=" * 50)
    print(f"インストール完了: {len(installed)} 件のコアスキル")
    print("=" * 50)
    print(f"\nスキル:     {skill_home}")
    print(f"レジストリ: {registry_path}")
    if instructions_dest:
        print(f"Instructions: {instructions_dest}")
    print(f"\n環境変数の設定（シェル起動スクリプトに追加してください）:")
    print(f'  export AGENT_SKILLS_HOME="{base_dir}"')
    print(f"\n次のステップ:")
    print(f'  - 「スキルをpullして」で最新スキルを取得')
    print(f'  - 「スクラムして」でscrum-masterを起動')
    print(f'  - 「スキルを探して」でリポジトリ内のスキルを検索')


if __name__ == "__main__":
    main()
