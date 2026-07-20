#!/usr/bin/env python3
"""Agent Skills 初回インストールスクリプト。

git clone 後に実行してコアスキルをユーザー領域にセットアップする。

使い方:
    git clone https://github.com/myorg/agent-skills.git
    python agent-skills/install.py
    python agent-skills/install.py --agent claude              # Claude Code 用
    python agent-skills/install.py --agent codex               # Codex 用
    python agent-skills/install.py --agent kiro                # Kiro 用
    python agent-skills/install.py --all-skills                # 全スキルをインストール
    python agent-skills/install.py --agent claude --all-skills # Claude Code + 全スキル

処理内容:
    1. <agent_home>/skills/ と <agent_home>/cache/ を作成
    2. コアスキルをユーザー領域にコピー
    3. skill-registry.json を初期生成（ソースリポジトリ・パスを自動登録）
    4. MCP 設定（Filesystem MCP を含む）をエージェントの MCP 設定にマージ
    5. 外部ツール（playwright-cli / codegraph / graphify / caveman / rtk / ponytail / headroom）をセットアップ
       - 導入済みかつ最新ならスキップ、古い場合のみ更新
       - 可能なものは --agent で指定したエージェントのみに連携
       - 非対応の OS・エージェントはスキップ
       - --force-external でチェックを無視して再実行
    6. セットアップ完了メッセージを表示

冪等: 既にインストール済みの場合はスキルを上書き更新、レジストリは既存設定を保持する。
      外部ツールは導入済み・最新ならスキップする（--force-external で強制）。
      Filesystem MCP は既存の許可ディレクトリ設定を表示し、変更するか確認する。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from urllib import error as urllib_error
from urllib import request as urllib_request

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
MCP_CONFIG_SRC = os.path.join(REPO_ROOT, ".github", "mcp", "mcp.json")

def _discover_skills_by_tier(skills_dir: str, tier: str) -> list[str]:
    """SKILL.md の metadata.tier が指定値に一致するスキルを動的収集する。"""
    result = []
    if not os.path.isdir(skills_dir):
        return result
    for name in sorted(os.listdir(skills_dir)):
        skill_md = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        with open(skill_md, encoding="utf-8") as f:
            content = f.read()
        fm = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if fm and re.search(rf'^\s+tier:\s*{re.escape(tier)}\s*$', fm.group(1), re.MULTILINE):
            result.append(name)
    return result


def _discover_all_skills(skills_dir: str) -> list[str]:
    """SKILL.md を持つ全スキルを動的収集する。"""
    result = []
    if not os.path.isdir(skills_dir):
        return result
    for name in sorted(os.listdir(skills_dir)):
        skill_md = os.path.join(skills_dir, name, "SKILL.md")
        if os.path.isfile(skill_md):
            result.append(name)
    return result


# コアスキルは後方互換のため維持
def _discover_core_skills(skills_dir: str) -> list[str]:
    """SKILL.md の metadata.tier: core を持つスキルを動的収集する。"""
    return _discover_skills_by_tier(skills_dir, "core")


def _get_skill_agents(skill_dir: str) -> list[str] | None:
    """SKILL.md の metadata.agents フィールドを返す。フィールドがなければ None（全エージェント対応）。"""
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None
    with open(skill_md, encoding="utf-8") as f:
        content = f.read()
    fm = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not fm:
        return None
    fm_body = fm.group(1)
    # インライン形式: agents: [claude, kiro]
    m = re.search(r'^\s+agents:\s*\[([^\]]+)\]', fm_body, re.MULTILINE)
    if m:
        return [a.strip() for a in m.group(1).split(',')]
    # リスト形式:
    #   agents:
    #     - claude
    if re.search(r'^\s+agents:\s*$', fm_body, re.MULTILINE):
        agents = []
        in_agents = False
        for line in fm_body.split('\n'):
            if re.match(r'^\s+agents:\s*$', line):
                in_agents = True
                continue
            if in_agents:
                item_m = re.match(r'^\s+-\s+(\S+)', line)
                if item_m:
                    agents.append(item_m.group(1))
                else:
                    break
        return agents if agents else None
    return None


def _is_skill_for_agent(skill_dir: str, agent_type: str) -> bool:
    """スキルが指定エージェントに対応しているか確認する。

    metadata.agents フィールドがない場合は全エージェント対応とみなす。
    """
    agents = _get_skill_agents(skill_dir)
    return agents is None or agent_type in agents


def _discover_agent_specific_skills(skills_dir: str, agent_type: str) -> list[str]:
    """指定エージェント専用スキル（metadata.agents フィールドあり）を収集する。"""
    result = []
    if not os.path.isdir(skills_dir):
        return result
    for name in sorted(os.listdir(skills_dir)):
        skill_dir = os.path.join(skills_dir, name)
        agents = _get_skill_agents(skill_dir)
        if agents is not None and agent_type in agents:
            result.append(name)
    return result


CORE_SKILLS = _discover_core_skills(REPO_SKILLS_DIR)


# ---------------------------------------------------------------------------
# MCP 設定
#
# サーバーの雛形は .github/mcp/mcp.json に定義し、install.py が環境（Windows 判定）
# とユーザー入力（filesystem の許可ディレクトリ）に応じて書き換えてから
# エージェントの MCP 設定にマージする。
# ---------------------------------------------------------------------------

# mcp.json の args 内で許可ディレクトリ一覧へ展開されるプレースホルダ
ALLOWED_DIRS_PLACEHOLDER = "${ALLOWED_DIRS}"


def _get_vscode_user_mcp_path() -> str:
    """VS Code ユーザーレベルの mcp.json パスを返す。"""
    system = platform.system()
    if system == "Darwin":
        base = os.path.expanduser("~/Library/Application Support/Code/User")
    elif system == "Windows":
        base = os.path.join(os.environ.get("APPDATA", ""), "Code", "User")
    else:
        base = os.path.expanduser("~/.config/Code/User")
    return os.path.join(base, "mcp.json")


def _mcp_target_path(agent_type: str, paths: dict[str, str]) -> str:
    """エージェント種別に応じた MCP 設定ファイルのパスを返す。

    claude  → ~/.claude/.mcp.json          (Claude Code ユーザーレベル設定)
    kiro    → ~/.kiro/settings/mcp.json    (Kiro 設定)
    copilot → VS Code ユーザーデータ mcp.json  (GitHub Copilot 向け)
    codex   → VS Code ユーザーデータ mcp.json  (Codex 向け)
    """
    agent_home = paths["agent_home"]
    if agent_type == "claude":
        return os.path.join(agent_home, ".mcp.json")
    elif agent_type == "kiro":
        return os.path.join(agent_home, "settings", "mcp.json")
    else:  # copilot, codex
        return _get_vscode_user_mcp_path()


def _wrap_npx_for_windows(entry: dict) -> dict:
    """Windows では npx 起動エントリを cmd /c でラップする。

    Windows の npx は npx.cmd（バッチファイル）として配布される。MCP クライアントは
    シェルを介さずプロセスを spawn するため、バッチファイルを直接起動できず
    `spawn npx ENOENT` で失敗する。実行ファイルである cmd.exe 経由で起動する。
    """
    if platform.system() != "Windows" or entry.get("command") != "npx":
        return entry
    return {
        **entry,
        "command": "cmd",
        "args": ["/c", "npx", *entry.get("args", [])],
    }


def _mcp_servers_key(agent_type: str) -> str:
    """エージェント種別に応じた MCP サーバー定義のキー名を返す。

    claude / kiro   → mcpServers (Anthropic 形式)
    copilot / codex → servers    (VSCode 形式)
    """
    return "mcpServers" if agent_type in ("claude", "kiro") else "servers"


def _load_mcp_config(path: str) -> dict:
    """既存の MCP 設定ファイルを読み込む。存在しなければ空 dict を返す。"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"   警告: {path} のJSON解析に失敗しました。上書きします。")
        return {}


def _package_before_placeholder(args: list[str]) -> str | None:
    """args 内の ${ALLOWED_DIRS} 直前の要素（パッケージ名）を返す。"""
    if ALLOWED_DIRS_PLACEHOLDER in args:
        idx = args.index(ALLOWED_DIRS_PLACEHOLDER)
        if idx > 0:
            return args[idx - 1]
    return None


def _installed_allowed_dirs(entry: dict, package: str | None) -> list[str]:
    """インストール済みエントリの args から package 以降の許可ディレクトリを抽出する。"""
    args = entry.get("args", [])
    if package and package in args:
        idx = args.index(package)
        return list(args[idx + 1:])
    return []


def _prompt_filesystem_dirs(defaults: list[str]) -> list[str]:
    """許可ディレクトリを対話入力する。空入力ならデフォルトを使う。"""
    default_str = ", ".join(defaults)
    raw = input(
        "   アクセスを許可するディレクトリを入力してください"
        f"（カンマ区切りで複数指定可）[{default_str}]: "
    ).strip()
    if not raw:
        return defaults
    dirs = []
    for part in raw.split(","):
        p = part.strip()
        if p:
            dirs.append(os.path.abspath(os.path.expanduser(p)))
    return dirs or defaults


def _resolve_allowed_dirs(
    current_dirs: list[str], default_dir: str, skip_config: bool,
) -> list[str]:
    """filesystem の許可ディレクトリを既存設定・対話入力・デフォルトから決定する。

    - skip_config 指定時 : 対話せず既存設定を保持し、なければデフォルトを使う
    - 既存設定あり        : 現在の許可ディレクトリを表示し、変更するか確認する
    - 既存設定なし        : 対話入力する（デフォルトはユーザーホーム）
    """
    if skip_config:
        return current_dirs or [default_dir]
    if current_dirs:
        print("   filesystem MCP は既に設定されています。")
        print("   現在の許可ディレクトリ:")
        for d in current_dirs:
            print(f"     - {d}")
        answer = input("   許可ディレクトリを変更しますか? [y/N]: ").strip().lower()
        if answer == "y":
            return _prompt_filesystem_dirs(current_dirs)
        print("   現在の設定を保持します")
        return current_dirs
    return _prompt_filesystem_dirs([default_dir])


def _render_server_entry(
    entry: dict, existing_entry: dict | None,
    paths: dict[str, str], skip_config: bool,
) -> dict:
    """mcp.json のサーバー雛形を環境・ユーザー入力に応じて書き換える。

    - ${ALLOWED_DIRS} : 許可ディレクトリ一覧へ展開する（ユーザー入力）
    - Windows         : npx 起動を cmd /c でラップする（環境）
    """
    args = entry.get("args", [])
    if ALLOWED_DIRS_PLACEHOLDER in args:
        package = _package_before_placeholder(args)
        current_dirs = (
            _installed_allowed_dirs(existing_entry, package) if existing_entry else []
        )
        dirs = _resolve_allowed_dirs(current_dirs, paths["user_home"], skip_config)
        expanded: list[str] = []
        for a in args:
            if a == ALLOWED_DIRS_PLACEHOLDER:
                expanded.extend(dirs)
            else:
                expanded.append(a)
        entry = {**entry, "args": expanded}
        print("   filesystem MCP の許可ディレクトリ:")
        for d in dirs:
            print(f"     - {d}")
    return _wrap_npx_for_windows(entry)


def setup_mcp_config(
    paths: dict[str, str], agent_type: str, skip_config: bool,
) -> bool:
    """`.github/mcp/mcp.json` のサーバー雛形を書き換えてエージェントの MCP 設定にマージする。

    各サーバー雛形は環境（Windows 判定）とユーザー入力（filesystem の許可
    ディレクトリ）に応じて _render_server_entry で書き換えてからマージする。

    - claude / kiro   : Anthropic 形式の mcpServers キーに追記
    - copilot / codex : VSCode 形式の servers キーに追記
    """
    if not os.path.isfile(MCP_CONFIG_SRC):
        return False

    with open(MCP_CONFIG_SRC, encoding="utf-8") as f:
        src = json.load(f)

    src_servers: dict = src.get("servers", {})
    if not src_servers:
        return False

    target_path = _mcp_target_path(agent_type, paths)
    servers_key = _mcp_servers_key(agent_type)
    existing = _load_mcp_config(target_path)
    existing_servers = existing.get(servers_key, {})

    rendered: dict = {}
    for name, entry in src_servers.items():
        rendered[name] = _render_server_entry(
            entry, existing_servers.get(name), paths, skip_config,
        )

    existing.setdefault(servers_key, {}).update(rendered)

    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"   → {target_path}")
    return True


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
    parser.add_argument(
        "--all-skills",
        action="store_true",
        default=False,
        help="全スキル（tier 問わず）をインストールする (default: コアスキルのみ)",
    )
    parser.add_argument(
        "--skip-config",
        action="store_true",
        default=False,
        help="スキル設定プロンプトをスキップする（CI環境など非対話実行時に使用）",
    )
    parser.add_argument(
        "--excludes-external-skills",
        action="store_true",
        default=False,
        help="playwright-cli / codegraph / graphify / caveman / rtk / ponytail / headroom など外部スキルをインストールしない",
    )
    parser.add_argument(
        "--force-external",
        action="store_true",
        default=False,
        help="外部ツールの導入済み・最新チェックを無視して再インストールする",
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


def copy_skills(paths: dict[str, str], skill_names: list[str]) -> list[dict]:
    """指定スキルをユーザー領域にコピーする。"""
    installed = []
    for name in skill_names:
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
        json.dump(reg, f, indent=2, ensure_ascii=True)


def _transform_frontmatter_for_kiro(content: str) -> str:
    """Kiro steering 向けにフロントマターの applyTo を inclusion に変換する。

    - applyTo: "**"       → inclusion: always
    - applyTo: "<pattern>" → inclusion: fileMatch
                              fileMatchPattern: "<pattern>"
    """
    fm_match = re.match(r'^(---[ \t]*\n)(.*?)(\n---)', content, re.DOTALL)
    if not fm_match:
        return content

    fm_body = fm_match.group(2)

    apply_to: str | None = None
    m = re.search(r'^applyTo:\s*"([^"]*)"', fm_body, re.MULTILINE)
    if m:
        apply_to = m.group(1)
    else:
        m = re.search(r"^applyTo:\s*'([^']*)'", fm_body, re.MULTILINE)
        if m:
            apply_to = m.group(1)
        else:
            m = re.search(r'^applyTo:\s*(\S.*?)\s*$', fm_body, re.MULTILINE)
            if m:
                apply_to = m.group(1).strip()

    if apply_to is None:
        return content

    if apply_to == "**":
        new_fm_body = re.sub(
            r'^applyTo:.*$', 'inclusion: always',
            fm_body, count=1, flags=re.MULTILINE,
        )
    else:
        replacement = f'inclusion: fileMatch\nfileMatchPattern: "{apply_to}"'
        new_fm_body = re.sub(
            r'^applyTo:.*$', replacement,
            fm_body, count=1, flags=re.MULTILINE,
        )

    return fm_match.group(1) + new_fm_body + fm_match.group(3) + content[fm_match.end():]


def _transform_frontmatter_for_claude(content: str) -> str:
    """Claude rules 向けにフロントマターの applyTo を paths に変換する。

    - applyTo: "**"        → # paths なし（すべてのファイルに適用）
    - applyTo: "<pattern>" → paths:\n  - <pattern>
    """
    fm_match = re.match(r'^(---[ \t]*\n)(.*?)(\n---)', content, re.DOTALL)
    if not fm_match:
        return content

    fm_body = fm_match.group(2)

    apply_to: str | None = None
    m = re.search(r'^applyTo:\s*"([^"]*)"', fm_body, re.MULTILINE)
    if m:
        apply_to = m.group(1)
    else:
        m = re.search(r"^applyTo:\s*'([^']*)'\s*$", fm_body, re.MULTILINE)
        if m:
            apply_to = m.group(1)
        else:
            m = re.search(r'^applyTo:\s*(\S.*?)\s*$', fm_body, re.MULTILINE)
            if m:
                apply_to = m.group(1).strip()

    if apply_to is None:
        return content

    if apply_to == "**":
        new_fm_body = re.sub(
            r'^applyTo:.*$', '# paths なし（すべてのファイルに適用）',
            fm_body, count=1, flags=re.MULTILINE,
        )
    else:
        replacement = f'paths:\n  - {apply_to}'
        new_fm_body = re.sub(
            r'^applyTo:.*$', replacement,
            fm_body, count=1, flags=re.MULTILINE,
        )

    return fm_match.group(1) + new_fm_body + fm_match.group(3) + content[fm_match.end():]


def copy_agent_instructions(paths: dict[str, str], agent_type: str = "copilot") -> bool:
    """リポジトリ配下の指示ファイルをエージェント領域へコピーする。

    kiro の場合は ~/.kiro/steering/ にコピーし、フロントマターの
    applyTo を Kiro 形式の inclusion に変換する。
    claude の場合は ~/.claude/rules/ にコピーし、フロントマターの
    applyTo を Claude rules 形式の paths に変換する。
    """
    src_dir = os.path.join(REPO_ROOT, ".github", "instructions")
    if agent_type == "kiro":
        dest_dir = os.path.join(paths["agent_home"], "steering")
    elif agent_type == "claude":
        dest_dir = os.path.join(paths["agent_home"], "rules")
    else:
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
        if agent_type == "kiro":
            with open(src, encoding="utf-8") as f:
                content = f.read()
            transformed = _transform_frontmatter_for_kiro(content)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(transformed)
        elif agent_type == "claude":
            with open(src, encoding="utf-8") as f:
                content = f.read()
            transformed = _transform_frontmatter_for_claude(content)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(transformed)
        else:
            shutil.copy2(src, dest)
        print(f"   {dest}")
        copied = True

    return copied


def _get_skill_config_script(skill_dir: str) -> str | None:
    """SKILL.md の metadata.config_script を返す。フィールドがなければ None。"""
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None
    with open(skill_md, encoding="utf-8") as f:
        content = f.read()
    fm = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not fm:
        return None
    m = re.search(r'^\s+config_script:\s*(.+?)\s*$', fm.group(1), re.MULTILINE)
    return m.group(1) if m else None


def _get_existing_skill_config(skill_name: str, registry_path: str) -> dict | None:
    """skill-registry.json の skill_configs[skill_name] を返す。なければ None。"""
    if not os.path.isfile(registry_path):
        return None
    with open(registry_path, encoding="utf-8") as f:
        reg = json.load(f)
    return reg.get("skill_configs", {}).get(skill_name) or None


def prompt_skill_configs(installed: list[dict], paths: dict[str, str]) -> None:
    """設定が必要なスキルを検出し、ユーザーに設定を促す。

    - 初回インストール: 「今すぐ設定しますか?」と聞く
    - 上書きインストール: 現在の設定を表示して「変更しますか?」と聞く
    """
    needs_config = []
    for skill in installed:
        skill_dir = os.path.join(paths["skill_home"], skill["name"])
        config_script = _get_skill_config_script(skill_dir)
        if not config_script:
            continue
        existing = _get_existing_skill_config(skill["name"], paths["registry_path"])
        needs_config.append({
            "name": skill["name"],
            "script": os.path.join(skill_dir, config_script),
            "existing": existing,
        })

    if not needs_config:
        return

    print("\n" + "=" * 50)
    print("スキル設定")
    print("=" * 50)

    for item in needs_config:
        print(f"\n■ {item['name']}")

        if item["existing"]:
            print("  現在の設定:")
            for k, v in item["existing"].items():
                print(f"    {k}: {v}")
            answer = input("  設定を変更しますか? [y/N]: ").strip().lower()
            if answer != "y":
                print("  スキップしました（後でスキルの init スクリプトを実行して変更できます）")
                continue
        else:
            answer = input("  初期設定が必要です。今すぐ設定しますか? [Y/n]: ").strip().lower()
            if answer == "n":
                print("  スキップしました（後でスキルの init スクリプトを実行して設定できます）")
                continue

        print("  設定スクリプトを実行します...")
        try:
            subprocess.run([sys.executable, item["script"]], check=False)
        except Exception as e:
            print(f"  [エラー] スクリプト実行に失敗しました: {e}")


def setup_claude_hooks(paths: dict[str, str]) -> bool:
    """Claude Code の settings.json に Stop hook を設定する。

    Stop hook でセッション終了後に ltm-use のインデックスを再構築する。
    既存設定は保持したまま hooks エントリのみ追加・更新する。
    """
    settings_path = os.path.join(paths["agent_home"], "settings.json")
    skill_home = paths["skill_home"]

    hook_command = (
        f"python {skill_home}/ltm-use/scripts/build_index.py --scope all"
        " 2>/dev/null || true"
    )
    new_hook_entry = {
        "matcher": "",
        "hooks": [{"type": "command", "command": hook_command}],
    }

    if os.path.isfile(settings_path):
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    stop_hooks = hooks.get("Stop", [])

    # 既存エントリに同じスクリプトパスがあれば上書き、なければ追加
    ltm_script = f"{skill_home}/ltm-use/scripts/build_index.py"
    updated = False
    for entry in stop_hooks:
        for h in entry.get("hooks", []):
            if ltm_script in h.get("command", ""):
                h["command"] = hook_command
                updated = True
    if not updated:
        stop_hooks.append(new_hook_entry)

    hooks["Stop"] = stop_hooks
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
    print(f"   {settings_path}")
    return True


def setup_lsp_for_kiro() -> None:
    """Kiro エージェント向けに LSP サーバーをインストールする。

    - typescript-language-server, typescript (npm グローバル)
    - pyright (pip)
    """
    commands = [
        (["npm", "install", "-g", "typescript-language-server", "typescript"],
         "typescript-language-server / typescript"),
        (["pip", "install", "pyright"],
         "pyright"),
    ]
    for cmd, label in commands:
        print(f"   インストール中: {label}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"   ✓ {label}")
            else:
                print(f"   ✗ {label} (終了コード {result.returncode})")
                if result.stderr:
                    print(f"     {result.stderr.strip()}")
        except FileNotFoundError:
            print(f"   ✗ {label}: コマンドが見つかりません ({cmd[0]})")


def _check_npm_available() -> bool:
    """npm が実行可能かどうか確認する。"""
    return bool(shutil.which("npm"))


# ---------------------------------------------------------------------------
# 外部ツール共通: バージョン比較・レジストリ照会
# ---------------------------------------------------------------------------

def _parse_version(text: str | None) -> tuple[int, ...] | None:
    """文字列から先頭の semver 風バージョンを抽出する。"""
    if not text:
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not m:
        m = re.search(r"(\d+)\.(\d+)", text)
    if not m:
        return None
    return tuple(int(p) for p in m.groups())


def _version_outdated(current: str | None, latest: str | None) -> bool | None:
    """current < latest なら True。比較不能なら None。"""
    cur = _parse_version(current)
    lat = _parse_version(latest)
    if cur is None or lat is None:
        return None
    # 長さを揃える
    n = max(len(cur), len(lat))
    cur = cur + (0,) * (n - len(cur))
    lat = lat + (0,) * (n - len(lat))
    return cur < lat


def _run_text(cmd: list[str], *, timeout: int = 60) -> tuple[int, str, str]:
    """コマンドを実行し (returncode, stdout, stderr) を返す。"""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 127, "", ""


def _npm_global_version(package: str) -> str | None:
    """npm グローバルに入っているパッケージのバージョン。"""
    if not _check_npm_available():
        return None
    rc, out, _ = _run_text(
        ["npm", "list", "-g", package, "--depth=0", "--json"],
    )
    if not out.strip():
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    dep = (data.get("dependencies") or {}).get(package) or {}
    ver = dep.get("version")
    return str(ver) if ver else None


def _npm_latest_version(package: str) -> str | None:
    """npm registry の latest バージョン。"""
    if not _check_npm_available():
        return None
    rc, out, _ = _run_text(["npm", "view", package, "version"])
    if rc != 0:
        return None
    ver = out.strip().splitlines()[-1].strip() if out.strip() else ""
    return ver or None


def _pypi_latest_version(package: str) -> str | None:
    """PyPI の latest バージョン。"""
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        req = urllib_request.Request(url, headers={"User-Agent": "agent-skills-install"})
        with urllib_request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        ver = data.get("info", {}).get("version")
        return str(ver) if ver else None
    except (urllib_error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _github_latest_release_tag(repo: str) -> str | None:
    """GitHub releases/latest の tag_name（v プレフィックス除去）。"""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        req = urllib_request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "agent-skills-install",
            },
        )
        with urllib_request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        tag = data.get("tag_name") or ""
        return tag.lstrip("v") if tag else None
    except (urllib_error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _cli_version_string(cmd: list[str]) -> str | None:
    """CLI の --version 出力からバージョン文字列を拾う。"""
    rc, out, err = _run_text(cmd)
    text = (out or err).strip()
    if rc != 0 and not text:
        return None
    parsed = _parse_version(text)
    if parsed is None:
        return None
    return ".".join(str(p) for p in parsed)


def _mcp_has_server(config_path: str, server_name: str) -> bool:
    """MCP 設定ファイルに server_name があるか。"""
    if not os.path.isfile(config_path):
        return False
    if config_path.endswith(".toml"):
        try:
            with open(config_path, encoding="utf-8") as f:
                return server_name in f.read()
        except OSError:
            return False
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    servers = data.get("mcpServers") or data.get("servers") or {}
    return server_name in servers


def setup_playwright_cli_skill(
    paths: dict[str, str], force: bool = False,
) -> bool:
    """playwright-cli を入れ、--agent の skill_home へスキルを展開する。

    - CLI 未導入 → install
    - CLI 古い → npm で更新してから skills 再展開
    - CLI 最新かつ skill_home にスキルあり → スキップ
    """
    if not _check_npm_available():
        print("   ⚠ npm が見つかりません。playwright-cli のセットアップをスキップします")
        print("     Node.js/npm をインストール後に再実行してください")
        return False

    pkg = "@playwright/cli"
    current = _npm_global_version(pkg)
    latest = _npm_latest_version(pkg)
    cli_path = shutil.which("playwright-cli")
    outdated = _version_outdated(current, latest)

    need_cli = force or not cli_path or not current or outdated is True
    if current and latest:
        print(f"   CLI: {current} (latest: {latest})")
    elif current:
        print(f"   CLI: {current} (latest: 不明)")
    elif cli_path:
        print("   CLI: PATH 上に存在するが npm グローバル版を確認できません")

    if need_cli:
        action = "更新" if current else "インストール"
        print(f"   @playwright/cli を{action}します...")
        rc, _, err = _run_text(
            ["npm", "install", "-g", f"{pkg}@latest"],
            timeout=300,
        )
        if rc != 0:
            print(f"   ✗ @playwright/cli の{action}に失敗しました (code {rc})")
            if err:
                print(f"     {err.strip()}")
            return False
        print(f"   ✓ @playwright/cli を{action}しました")
        cli_path = shutil.which("playwright-cli")
    else:
        print("   ✓ playwright-cli は最新です")

    if not cli_path and not shutil.which("playwright-cli"):
        print("   ✗ playwright-cli コマンドが見つかりません")
        return False

    target_path = os.path.join(paths["skill_home"], "playwright-cli")
    target_skill = os.path.join(target_path, "SKILL.md")
    skill_ok = os.path.isfile(target_skill)

    if skill_ok and not need_cli and not force:
        print(f"   ✓ スキルは導入済みです → スキップ ({target_path})")
        return True

    user_home = paths["user_home"]
    claude_skill_path = os.path.join(user_home, ".claude", "skills", "playwright-cli")
    print(f"   playwright-cli install --skills を実行します (CWD={user_home})...")
    try:
        result = subprocess.run(
            ["playwright-cli", "install", "--skills"],
            cwd=user_home,
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("   ✗ playwright-cli コマンドが見つかりません")
        return False
    if result.returncode != 0:
        print(f"   ✗ playwright-cli install --skills に失敗しました (code {result.returncode})")
        if result.stderr:
            print(f"     {result.stderr.strip()}")
        return False
    if result.stdout:
        for line in result.stdout.strip().splitlines():
            print(f"   {line}")

    if not os.path.isdir(claude_skill_path):
        print(f"   ✗ 展開先 {claude_skill_path} が見つかりません")
        return False

    if os.path.abspath(claude_skill_path) == os.path.abspath(target_path):
        print(f"   ✓ {target_path} (源と同一、コピー不要)")
        return True
    if os.path.isdir(target_path):
        shutil.rmtree(target_path)
    shutil.copytree(claude_skill_path, target_path)
    print(f"   ✓ {claude_skill_path} → {target_path}")
    return True


# codegraph: install.py --agent → codegraph --target
# （copilot は公式ターゲット外）
CODEGRAPH_AGENT_TARGETS: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "kiro": "kiro",
}


def _codegraph_agent_configured(agent_type: str, paths: dict[str, str]) -> bool:
    """指定エージェントに codegraph MCP が既に入っているか。"""
    user_home = paths["user_home"]
    if agent_type == "claude":
        return _mcp_has_server(os.path.join(user_home, ".claude.json"), "codegraph")
    if agent_type == "codex":
        return _mcp_has_server(
            os.path.join(user_home, ".codex", "config.toml"), "codegraph",
        )
    if agent_type == "kiro":
        return _mcp_has_server(
            os.path.join(user_home, ".kiro", "settings", "mcp.json"), "codegraph",
        )
    return False


def _codegraph_update_available() -> bool | None:
    """codegraph upgrade --check の結果。不明なら None。"""
    if not shutil.which("codegraph"):
        return None
    rc, out, err = _run_text(["codegraph", "upgrade", "--check"])
    text = (out + "\n" + err).lower()
    if "up to date" in text or "already on" in text or "no update" in text:
        return False
    if "update available" in text or "newer version" in text or "available:" in text:
        return True
    if "could not resolve" in text or "network" in text:
        return None
    # exit 0 で明確な文言が無い場合は最新扱い
    if rc == 0:
        return False
    return None


def setup_codegraph(agent_type: str, force: bool = False) -> bool:
    """codegraph を --agent 向けにインストール／更新する。

    copilot など非対応エージェントはスキップ。
    CLI が最新かつ対象エージェントに MCP 設定済みならスキップ。
    """
    target = CODEGRAPH_AGENT_TARGETS.get(agent_type)
    if target is None:
        print(
            f"   ⚠ codegraph はエージェント '{agent_type}' に未対応のためスキップします"
            f" (対応: {', '.join(sorted(CODEGRAPH_AGENT_TARGETS))})"
        )
        return False

    if shutil.which("npx") is None and not shutil.which("codegraph"):
        print("   ⚠ npx / codegraph が見つかりません。スキップします")
        return False

    paths = resolve_paths(agent_type)
    cli_present = bool(shutil.which("codegraph"))
    current = _cli_version_string(["codegraph", "version"]) if cli_present else None
    if current is None and cli_present:
        current = _cli_version_string(["codegraph", "--version"])
    latest_npm = _npm_latest_version("@colbymchenry/codegraph")
    outdated = _version_outdated(current, latest_npm)
    update_flag = _codegraph_update_available() if cli_present else None

    if current:
        print(f"   CLI: {current}" + (f" (npm latest: {latest_npm})" if latest_npm else ""))

    need_cli_install = force or not cli_present
    need_cli_upgrade = force or outdated is True or update_flag is True
    agent_ok = _codegraph_agent_configured(agent_type, paths)

    if cli_present and not need_cli_upgrade and agent_ok and not force:
        print(f"   ✓ codegraph は最新で {agent_type} にも設定済み → スキップ")
        return True

    if need_cli_install:
        print("   codegraph CLI をインストールします (npm)...")
        if not _check_npm_available():
            print("   ✗ npm が必要です")
            return False
        rc, _, err = _run_text(
            ["npm", "install", "-g", "@colbymchenry/codegraph@latest"],
            timeout=300,
        )
        if rc != 0:
            print(f"   ✗ codegraph CLI のインストールに失敗しました (code {rc})")
            if err:
                print(f"     {err.strip()}")
            return False
        print("   ✓ codegraph CLI をインストールしました")
    elif need_cli_upgrade:
        print("   codegraph をアップグレードします...")
        rc, out, err = _run_text(["codegraph", "upgrade"], timeout=300)
        if rc != 0:
            # npm 経由のフォールバック
            print("   codegraph upgrade 失敗。npm で再試行します...")
            rc2, _, err2 = _run_text(
                ["npm", "install", "-g", "@colbymchenry/codegraph@latest"],
                timeout=300,
            )
            if rc2 != 0:
                print(f"   ✗ codegraph の更新に失敗しました")
                if err or err2:
                    print(f"     {(err or err2).strip()}")
                return False
        print("   ✓ codegraph CLI を更新しました")
    else:
        if update_flag is None and outdated is None:
            print("   ✓ codegraph CLI あり（更新可否は判定不能のため現状維持）")
        else:
            print("   ✓ codegraph CLI は最新です")

    if agent_ok and not force and not need_cli_upgrade and not need_cli_install:
        print(f"   ✓ {agent_type} 向け設定済み → スキップ")
        return True

    cmd = [
        "codegraph", "install",
        "--target", target,
        "--location", "global",
        "--yes",
    ]
    print(f"   エージェント連携: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        print("   ✗ codegraph コマンドが見つかりません")
        return False
    if result.returncode == 0:
        print(f"   ✓ codegraph を {agent_type} 向けにセットアップしました")
        return True
    print(f"   ✗ codegraph install に失敗しました (code {result.returncode})")
    return False


# graphify: install.py --agent → graphify --platform
GRAPHIFY_AGENT_PLATFORMS: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "copilot": "copilot",
    "kiro": "kiro",
}


def _graphify_skill_paths(agent_type: str) -> tuple[str, str]:
    """(SKILL.md パス, .graphify_version パス) を返す。"""
    platform_name = GRAPHIFY_AGENT_PLATFORMS[agent_type]
    skill_md = os.path.join(
        os.path.expanduser("~"), f".{platform_name}", "skills", "graphify", "SKILL.md",
    )
    # graphify は skill_dst.parent / .graphify_version
    version_file = os.path.join(os.path.dirname(skill_md), ".graphify_version")
    return skill_md, version_file


def _graphify_skill_version(agent_type: str) -> str | None:
    _, version_file = _graphify_skill_paths(agent_type)
    if not os.path.isfile(version_file):
        return None
    try:
        with open(version_file, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _upgrade_graphify_cli() -> bool:
    """利用可能な手段で graphifyy を最新化する。"""
    upgraders = [
        ("uv", ["uv", "tool", "upgrade", "graphifyy"]),
        ("pipx", ["pipx", "upgrade", "graphifyy"]),
        ("pip", [sys.executable, "-m", "pip", "install", "-U", "graphifyy"]),
    ]
    for name, cmd in upgraders:
        if shutil.which(cmd[0]) is None:
            continue
        print(f"   {name} で graphify を更新中...")
        rc, _, err = _run_text(cmd, timeout=300)
        if rc == 0:
            print(f"   ✓ graphify を更新しました ({name})")
            return True
        print(f"   ✗ {name} での更新に失敗 (code {rc})")
        if err:
            print(f"     {err.strip()}")
    return False


def setup_graphify(agent_type: str, force: bool = False) -> bool:
    """graphify を --agent 向け platform にインストール／更新する。"""
    platform_name = GRAPHIFY_AGENT_PLATFORMS.get(agent_type)
    if platform_name is None:
        print(
            f"   ⚠ graphify はエージェント '{agent_type}' に未対応のためスキップします"
            f" (対応: {', '.join(sorted(GRAPHIFY_AGENT_PLATFORMS))})"
        )
        return False

    current = _cli_version_string(["graphify", "--version"])
    latest = _pypi_latest_version("graphifyy")
    outdated = _version_outdated(current, latest)
    skill_md, _ = _graphify_skill_paths(agent_type)
    skill_ver = _graphify_skill_version(agent_type)
    skill_ok = os.path.isfile(skill_md)
    skill_stale = (
        skill_ok
        and current is not None
        and skill_ver is not None
        and _version_outdated(skill_ver, current) is True
    )

    if current:
        print(f"   CLI: {current}" + (f" (PyPI: {latest})" if latest else ""))
    if skill_ok:
        print(f"   スキル: {skill_md}" + (f" (marker: {skill_ver})" if skill_ver else ""))

    need_cli = force or current is None
    need_upgrade = force or outdated is True

    if need_cli:
        installers = [
            ("uv", ["uv", "tool", "install", "graphifyy"]),
            ("pipx", ["pipx", "install", "graphifyy"]),
            ("pip", [sys.executable, "-m", "pip", "install", "graphifyy"]),
        ]
        installed = False
        for name, cmd in installers:
            if shutil.which(cmd[0]) is None:
                continue
            print(f"   {name} で graphify (graphifyy) をインストール中...")
            rc, _, err = _run_text(cmd, timeout=300)
            if rc == 0:
                print(f"   ✓ graphify をインストールしました ({name})")
                installed = True
                break
            print(f"   ✗ {name} でのインストールに失敗 (code {rc})")
            if err:
                print(f"     {err.strip()}")
        if not installed:
            print("   ✗ graphify をインストールできませんでした")
            return False
        current = _cli_version_string(["graphify", "--version"])
    elif need_upgrade:
        if not _upgrade_graphify_cli():
            return False
        current = _cli_version_string(["graphify", "--version"])
        skill_stale = True  # CLI 更新後はスキルも合わせる
    else:
        if outdated is None and latest is None:
            print("   ✓ graphify CLI あり（更新可否は判定不能のため現状維持）")
        else:
            print("   ✓ graphify CLI は最新です")

    if skill_ok and not skill_stale and not force and not need_cli and not need_upgrade:
        print(f"   ✓ {agent_type} 向けスキルは導入済み → スキップ")
        return True

    cmd = ["graphify", "install", "--platform", platform_name]
    print(f"   スキル登録: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        print("   ✗ graphify コマンドが見つかりません")
        return False
    if result.returncode == 0:
        print(f"   ✓ graphify を {agent_type} 向けに登録しました")
        return True
    print(f"   ✗ graphify install に失敗しました (code {result.returncode})")
    return False


CAVEMAN_REPO = "JuliusBrussee/caveman"

# install.py --agent → caveman 公式 --only / skills -a プロファイル
# skills 系は必ず -g（ホーム＝グローバル）へ入れる。プロジェクト配下には書かない。
CAVEMAN_AGENT_IDS: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "kiro": "kiro",
    "copilot": "copilot",
}

CAVEMAN_SKILLS_PROFILES: dict[str, str] = {
    "codex": "codex",          # → ~/.codex/skills/
    "kiro": "kiro-cli",        # → ~/.kiro/skills/
    "copilot": "github-copilot",  # → ~/.copilot/skills/
}


def _caveman_global_skill_dirs(agent_type: str, paths: dict[str, str]) -> list[str]:
    """ホーム配下の caveman スキル候補パスを返す。"""
    home = paths["user_home"]
    agent_home = paths["agent_home"]
    dirs = [
        os.path.join(agent_home, "skills", "caveman"),
        os.path.join(home, ".agents", "skills", "caveman"),
    ]
    if agent_type == "codex":
        dirs.insert(0, os.path.join(home, ".codex", "skills", "caveman"))
    elif agent_type == "kiro":
        dirs.insert(0, os.path.join(home, ".kiro", "skills", "caveman"))
    elif agent_type == "copilot":
        dirs.insert(0, os.path.join(home, ".copilot", "skills", "caveman"))
    elif agent_type == "claude":
        dirs.insert(0, os.path.join(home, ".claude", "skills", "caveman"))
    return dirs


def _caveman_installed_for_agent(agent_type: str, paths: dict[str, str]) -> bool:
    """caveman がホーム（グローバル）に入っているか（ベストエフォート）。"""
    for d in _caveman_global_skill_dirs(agent_type, paths):
        if os.path.isfile(os.path.join(d, "SKILL.md")):
            return True
    if agent_type == "claude":
        claude_markers = [
            os.path.join(paths["agent_home"], "hooks", "caveman-activate.js"),
            os.path.join(paths["agent_home"], ".caveman-active"),
            os.path.join(paths["user_home"], ".claude", "plugins", "marketplaces", "caveman"),
            os.path.join(paths["user_home"], ".claude", "plugins", "cache", "caveman"),
        ]
        return any(os.path.exists(p) for p in claude_markers)
    return False


def _install_caveman_claude(force: bool, paths: dict[str, str]) -> bool:
    """Claude Code: 公式インストーラで ~/.claude に plugin + hooks を入れる。"""
    cmd = [
        "npx", "-y", f"github:{CAVEMAN_REPO}",
        "--",
        "--only", "claude",
        "--non-interactive",
    ]
    if force:
        cmd.append("--force")
    print("   caveman をホーム (~/.claude) にインストール中...")
    print(f"   $ {' '.join(cmd)}")
    # プロジェクト CWD だと skills フォールバックが project スコープになるため HOME で実行
    try:
        result = subprocess.run(cmd, cwd=paths["user_home"])
    except FileNotFoundError:
        print("   ✗ npx が見つかりません (Node.js をインストールしてください)")
        return False
    if result.returncode == 0:
        print("   ✓ caveman を claude 向けにホームへインストールしました")
        return True
    print(f"   ✗ caveman のインストールに失敗しました (code {result.returncode})")
    return False


def _install_caveman_skills_global(agent_type: str, force: bool, paths: dict[str, str]) -> bool:
    """codex / kiro / copilot: ``npx skills add -g`` でホームに入れる。"""
    profile = CAVEMAN_SKILLS_PROFILES[agent_type]
    cmd = [
        "npx", "-y", "skills", "add", CAVEMAN_REPO,
        "--skill", "*",
        "-a", profile,
        "--yes",
        "-g",  # ユーザーホーム（グローバル）。プロジェクトの .agents/skills には書かない
    ]
    print(f"   caveman をホームへインストール中 (skills -g, profile={profile})...")
    print(f"   $ {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=paths["user_home"])
    except FileNotFoundError:
        print("   ✗ npx が見つかりません (Node.js をインストールしてください)")
        return False
    if result.returncode == 0:
        # 導入先を表示
        for d in _caveman_global_skill_dirs(agent_type, paths):
            if os.path.isfile(os.path.join(d, "SKILL.md")):
                print(f"   ✓ {d}")
                break
        else:
            print(f"   ✓ caveman を {agent_type} 向けにホームへインストールしました")
        return True
    print(f"   ✗ caveman のインストールに失敗しました (code {result.returncode})")
    if force:
        print("     tip: npx skills remove caveman -g のあと再実行してください")
    return False


def setup_caveman(agent_type: str, force: bool = False) -> bool:
    """juliusbrussee/caveman を --agent 向けにホーム（グローバル）へインストールする。

    - claude: 公式インストーラ → ~/.claude（plugin + hooks）
    - codex / kiro / copilot: ``npx skills add -g`` → ~/.codex|~/.kiro|~/.copilot/skills/
    - プロジェクトローカルには書かない（``--with-init`` は使わない）
    - 導入済みならスキップ（``--force-external`` で再実行）
    """
    if agent_type not in CAVEMAN_AGENT_IDS:
        print(f"   ⚠ caveman はエージェント '{agent_type}' に未対応のためスキップします")
        return False

    if shutil.which("npx") is None:
        print("   ⚠ npx が見つかりません。caveman のセットアップをスキップします")
        print("     Node.js (≥18) をインストール後に再実行してください")
        return False

    paths = resolve_paths(agent_type)
    if _caveman_installed_for_agent(agent_type, paths) and not force:
        print(f"   ✓ caveman は {agent_type} 向けにホームへ導入済み → スキップ")
        for d in _caveman_global_skill_dirs(agent_type, paths):
            if os.path.isfile(os.path.join(d, "SKILL.md")):
                print(f"     {d}")
                break
        print("     再インストールする場合は --force-external を指定してください")
        return True

    if agent_type == "claude":
        return _install_caveman_claude(force=force, paths=paths)
    return _install_caveman_skills_global(agent_type, force=force, paths=paths)


RTK_INSTALL_SH = (
    "https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh"
)

# --auto-patch は Claude settings.json 用。--codex / --copilot とは併用不可。
RTK_AGENT_INIT_ARGS: dict[str, list[str]] = {
    "claude": ["-g", "--auto-patch"],
    "copilot": ["-g", "--copilot"],
    "codex": ["-g", "--codex"],
}

RTK_SUPPORTED_ENVS = frozenset({"mac", "linux", "wsl"})


def _is_wsl() -> bool:
    """WSL 上の Linux かどうかを判定する。"""
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/version", encoding="utf-8") as f:
            ver = f.read().lower()
        return "microsoft" in ver or "wsl" in ver
    except OSError:
        return False


def _rtk_env_name() -> str | None:
    """現在の実行環境名を返す。RTK 非対応なら None。"""
    system = platform.system()
    if system == "Darwin":
        return "mac"
    if system == "Linux":
        return "wsl" if _is_wsl() else "linux"
    return None


def _rtk_bin_path() -> str | None:
    """PATH または ~/.local/bin 上の rtk バイナリパスを返す。"""
    found = shutil.which("rtk")
    if found:
        return found
    candidate = os.path.join(os.path.expanduser("~"), ".local", "bin", "rtk")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    if platform.system() == "Windows":
        win = candidate + ".exe"
        if os.path.isfile(win):
            return win
    return None


def _rtk_is_token_killer(rtk_bin: str) -> bool:
    """正しい RTK (Rust Token Killer) かどうかを ``rtk gain`` で確認する。"""
    rc, _, _ = _run_text([rtk_bin, "gain"])
    return rc == 0


def _rtk_agent_configured(agent_type: str, paths: dict[str, str]) -> bool:
    """rtk init 相当の成果物があるか（ベストエフォート）。"""
    home = paths["user_home"]
    agent_home = paths["agent_home"]
    markers = [
        os.path.join(agent_home, "RTK.md"),
        os.path.join(agent_home, "hooks", "rtk-rewrite.sh"),
    ]
    if agent_type == "claude":
        markers.append(os.path.join(home, ".claude", "hooks", "rtk-rewrite.sh"))
        markers.append(os.path.join(home, ".claude", "RTK.md"))
    if agent_type == "codex":
        markers.append(os.path.join(home, ".codex", "RTK.md"))
        agents_md = os.path.join(home, ".codex", "AGENTS.md")
        if os.path.isfile(agents_md):
            try:
                with open(agents_md, encoding="utf-8") as f:
                    if "rtk" in f.read().lower():
                        return True
            except OSError:
                pass
    if agent_type == "copilot":
        markers.append(os.path.join(home, ".copilot", "RTK.md"))
    return any(os.path.exists(p) for p in markers)


def _install_rtk_binary() -> str | None:
    """公式 install.sh で rtk バイナリを入れ、パスを返す。失敗時は None。"""
    if shutil.which("curl") is None:
        print("   ⚠ curl が見つかりません。rtk バイナリのインストールをスキップします")
        return None

    print("   install.sh を取得して実行します...")
    try:
        curl = subprocess.run(
            ["curl", "-fsSL", RTK_INSTALL_SH],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("   ✗ curl が見つかりません")
        return None
    if curl.returncode != 0:
        print(f"   ✗ install.sh の取得に失敗しました (code {curl.returncode})")
        if curl.stderr:
            print(f"     {curl.stderr.strip()}")
        return None

    try:
        result = subprocess.run(["sh", "-s"], input=curl.stdout, text=True)
    except FileNotFoundError:
        print("   ✗ sh が見つかりません")
        return None
    if result.returncode != 0:
        print(f"   ✗ rtk バイナリのインストールに失敗しました (code {result.returncode})")
        return None

    rtk_bin = _rtk_bin_path()
    if rtk_bin is None:
        print("   ✗ rtk バイナリがインストール先に見つかりません (~/.local/bin)")
        return None
    if not _rtk_is_token_killer(rtk_bin):
        print("   ✗ インストールされた rtk が Token Killer ではない可能性があります")
        return None
    print(f"   ✓ rtk バイナリをインストールしました: {rtk_bin}")
    return rtk_bin


def setup_rtk(agent_type: str, force: bool = False) -> bool:
    """rtk-ai/rtk を --agent 向けにインストール／更新する。

    - 非対応 OS / エージェントはスキップ
    - バイナリ最新かつエージェント連携済みならスキップ
    """
    env_name = _rtk_env_name()
    if env_name is None or env_name not in RTK_SUPPORTED_ENVS:
        system = platform.system() or "unknown"
        print(
            f"   ⚠ rtk は環境 '{system}' に未対応のためスキップします"
            " (対応: mac / linux / wsl)"
        )
        return False

    init_args = RTK_AGENT_INIT_ARGS.get(agent_type)
    if init_args is None:
        print(
            f"   ⚠ rtk はエージェント '{agent_type}' に未対応のためスキップします"
            f" (対応: {', '.join(sorted(RTK_AGENT_INIT_ARGS))})"
        )
        return False

    paths = resolve_paths(agent_type)
    print(f"   環境: {env_name}, エージェント: {agent_type}")

    rtk_bin = _rtk_bin_path()
    current = _cli_version_string([rtk_bin, "--version"]) if rtk_bin else None
    latest = _github_latest_release_tag("rtk-ai/rtk")
    outdated = _version_outdated(current, latest)

    if rtk_bin and not _rtk_is_token_killer(rtk_bin):
        print(f"   ⚠ {rtk_bin} はあるが Token Killer ではないため再インストールします")
        rtk_bin = None
        current = None
        outdated = True

    if current:
        print(f"   CLI: {current}" + (f" (latest: {latest})" if latest else " (latest: 不明)"))

    need_binary = force or rtk_bin is None or outdated is True
    agent_ok = _rtk_agent_configured(agent_type, paths) if rtk_bin else False

    if rtk_bin and not need_binary and agent_ok and not force:
        print(f"   ✓ rtk は最新で {agent_type} にも設定済み → スキップ")
        return True

    if need_binary:
        print("   rtk バイナリをインストール／更新します...")
        rtk_bin = _install_rtk_binary()
        if rtk_bin is None:
            return False
        agent_ok = False  # バイナリ更新後は init をやり直す
    else:
        if outdated is None:
            print("   ✓ rtk バイナリあり（更新可否は判定不能のため現状維持）")
        else:
            print(f"   ✓ rtk バイナリは最新です ({rtk_bin})")

    if agent_ok and not force:
        print(f"   ✓ {agent_type} 向け連携済み → スキップ")
        return True

    cmd = [rtk_bin, "init", *init_args]
    print("   エージェント連携を設定中...")
    print(f"   $ {' '.join(cmd)}")
    env = os.environ.copy()
    local_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
    env["PATH"] = local_bin + os.pathsep + env.get("PATH", "")
    try:
        result = subprocess.run(cmd, env=env)
    except OSError as e:
        print(f"   ✗ rtk init の実行に失敗しました: {e}")
        return False

    if result.returncode == 0:
        print(f"   ✓ rtk を {agent_type} 向けにセットアップしました")
        return True
    print(f"   ✗ rtk init に失敗しました (code {result.returncode})")
    return False


PONYTAIL_REPO = "DietrichGebert/ponytail"
PONYTAIL_PACKAGE_JSON = (
    f"https://raw.githubusercontent.com/{PONYTAIL_REPO}/main/package.json"
)
PONYTAIL_KIRO_STEERING = (
    f"https://raw.githubusercontent.com/{PONYTAIL_REPO}/main/.kiro/steering/ponytail.md"
)

# install.py --agent → インストール方式
# claude/codex/copilot: 各 CLI の plugin 機構 / kiro: steering ファイル
PONYTAIL_SUPPORTED_AGENTS = frozenset({"claude", "codex", "copilot", "kiro"})


def _fetch_url_text(url: str) -> str | None:
    """URL の本文を取得する。失敗時は None。"""
    try:
        req = urllib_request.Request(url, headers={"User-Agent": "agent-skills-install"})
        with urllib_request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8")
    except (urllib_error.URLError, TimeoutError, OSError):
        return None


def _ponytail_remote_version() -> str | None:
    """GitHub 上の ponytail package.json の version。"""
    body = _fetch_url_text(PONYTAIL_PACKAGE_JSON)
    if not body:
        return None
    try:
        ver = json.loads(body).get("version")
        return str(ver) if ver else None
    except json.JSONDecodeError:
        return None


def _ponytail_plugin_list_text(cli: str) -> str:
    """``<cli> plugin list`` の出力を返す。"""
    rc, out, err = _run_text([cli, "plugin", "list"])
    return (out or "") + "\n" + (err or "")


def _ponytail_plugin_installed(cli: str) -> bool:
    """plugin list に ponytail があるか。"""
    return "ponytail" in _ponytail_plugin_list_text(cli).lower()


def _ponytail_plugin_installed_version(cli: str) -> str | None:
    """plugin list 出力から ponytail のバージョンを拾う（ベストエフォート）。"""
    text = _ponytail_plugin_list_text(cli)
    for line in text.splitlines():
        if "ponytail" not in line.lower():
            continue
        ver = _parse_version(line)
        if ver is not None:
            return ".".join(str(p) for p in ver)
    return None


def _ponytail_kiro_paths(paths: dict[str, str]) -> tuple[str, str]:
    """(steering ファイル, バージョンマーカー) を返す。"""
    steering = os.path.join(paths["agent_home"], "steering", "ponytail.md")
    marker = os.path.join(paths["agent_home"], "steering", ".ponytail_version")
    return steering, marker


def _ponytail_kiro_installed_version(paths: dict[str, str]) -> str | None:
    _, marker = _ponytail_kiro_paths(paths)
    if not os.path.isfile(marker):
        return None
    try:
        with open(marker, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _install_ponytail_plugin(agent_type: str, force: bool) -> bool:
    """claude / codex / copilot の plugin CLI で ponytail を入れる。"""
    cli_map = {
        "claude": "claude",
        "codex": "codex",
        "copilot": "copilot",
    }
    cli = cli_map[agent_type]
    if shutil.which(cli) is None:
        print(f"   ⚠ {cli} が見つかりません。ponytail をスキップします")
        return False

    if shutil.which("node") is None:
        print("   ⚠ node が PATH にありません（hooks は無効のまま、スキル自体は使えます）")

    remote = _ponytail_remote_version()
    installed = _ponytail_plugin_installed(cli)
    local = _ponytail_plugin_installed_version(cli) if installed else None
    outdated = _version_outdated(local, remote) if installed else None

    if installed:
        print(
            f"   plugin: 導入済み"
            + (f" ({local})" if local else "")
            + (f" / remote: {remote}" if remote else "")
        )
    else:
        print("   plugin: 未導入" + (f" (remote: {remote})" if remote else ""))

    if installed and not force and outdated is not True:
        if outdated is None and remote and not local:
            print("   ✓ ponytail は導入済み（バージョン比較不能のため現状維持）→ スキップ")
        else:
            print(f"   ✓ ponytail は {agent_type} 向けに最新 → スキップ")
        return True

    # marketplace 登録（既にあっても失敗しにくい想定。失敗しても install を試す）
    print(f"   marketplace を追加: {PONYTAIL_REPO}")
    rc_m, _, err_m = _run_text(
        [cli, "plugin", "marketplace", "add", PONYTAIL_REPO],
        timeout=120,
    )
    if rc_m != 0:
        print(f"   ⚠ marketplace add が非ゼロ (code {rc_m}) — install を続行します")
        if err_m.strip():
            print(f"     {err_m.strip().splitlines()[-1]}")

    if force or outdated is True:
        # カタログ更新（対応している CLI のみ）
        _run_text([cli, "plugin", "marketplace", "update"], timeout=120)

    if agent_type == "codex":
        install_cmd = [cli, "plugin", "add", "ponytail@ponytail"]
    else:
        install_cmd = [cli, "plugin", "install", "ponytail@ponytail"]
        if agent_type == "claude":
            install_cmd.extend(["-s", "user"])

    print(f"   $ {' '.join(install_cmd)}")
    try:
        result = subprocess.run(install_cmd)
    except FileNotFoundError:
        print(f"   ✗ {cli} が見つかりません")
        return False

    if result.returncode == 0:
        print(f"   ✓ ponytail を {agent_type} 向けにインストールしました")
        if agent_type == "codex":
            print("   メモ: codex で /hooks を開き、lifecycle hooks を trust してください")
        return True

    # copilot は marketplace 名が違う場合があるので repo 直指定でフォールバック
    if agent_type == "copilot":
        fallback = [cli, "plugin", "install", PONYTAIL_REPO]
        print(f"   marketplace 経由が失敗したためフォールバック: {' '.join(fallback)}")
        result2 = subprocess.run(fallback)
        if result2.returncode == 0:
            print(f"   ✓ ponytail を {agent_type} 向けにインストールしました")
            return True

    print(f"   ✗ ponytail のインストールに失敗しました (code {result.returncode})")
    return False


def _install_ponytail_kiro(paths: dict[str, str], force: bool) -> bool:
    """Kiro 向けに steering ファイルをコピーする。"""
    dest, marker = _ponytail_kiro_paths(paths)
    remote = _ponytail_remote_version()
    local = _ponytail_kiro_installed_version(paths)
    present = os.path.isfile(dest)
    outdated = _version_outdated(local, remote) if present else None

    if present:
        print(
            f"   steering: {dest}"
            + (f" ({local})" if local else "")
            + (f" / remote: {remote}" if remote else "")
        )
    if present and not force and outdated is not True:
        print("   ✓ ponytail は kiro 向けに導入済み → スキップ")
        return True

    print(f"   steering を取得: {PONYTAIL_KIRO_STEERING}")
    body = _fetch_url_text(PONYTAIL_KIRO_STEERING)
    if not body:
        print("   ✗ ponytail.md の取得に失敗しました")
        return False

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(body)
        if not body.endswith("\n"):
            f.write("\n")
    if remote:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(remote + "\n")
    print(f"   ✓ {dest}" + (f" (v{remote})" if remote else ""))
    return True


def setup_ponytail(agent_type: str, force: bool = False) -> bool:
    """DietrichGebert/ponytail を --agent 向けにインストールする。

    - claude / codex / copilot: 各 CLI の ``plugin marketplace add`` + install
    - kiro: ``~/.kiro/steering/ponytail.md`` を配置
    - 導入済みかつ最新ならスキップ（``--force-external`` で再実行）
    """
    if agent_type not in PONYTAIL_SUPPORTED_AGENTS:
        print(
            f"   ⚠ ponytail はエージェント '{agent_type}' に未対応のためスキップします"
            f" (対応: {', '.join(sorted(PONYTAIL_SUPPORTED_AGENTS))})"
        )
        return False

    paths = resolve_paths(agent_type)
    if agent_type == "kiro":
        return _install_ponytail_kiro(paths, force=force)
    return _install_ponytail_plugin(agent_type, force=force)


HEADROOM_PKG = "headroom-ai[all]"
HEADROOM_PYPI = "headroom-ai"

# wrap 対応 + MCP 配線可能なエージェント（kiro は wrap 非対応だが MCP は可）
HEADROOM_SUPPORTED_AGENTS = frozenset({"claude", "codex", "copilot", "kiro"})


def _headroom_env_supported() -> bool:
    """公式ホイールが現実的に使える環境か。

    README: macOS Apple Silicon / Linux。Intel Mac は Docker 前提のためスキップ。
    """
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        return True
    if system == "Darwin":
        return machine in ("arm64", "aarch64")
    if system == "Windows":
        return True
    return False


def _headroom_bin_path() -> str | None:
    """PATH またはよくある場所の headroom バイナリ。"""
    found = shutil.which("headroom")
    if found:
        return found
    candidates = [
        os.path.join(os.path.expanduser("~"), ".local", "bin", "headroom"),
        os.path.join(os.path.expanduser("~"), ".cargo", "bin", "headroom"),
    ]
    if platform.system() == "Windows":
        candidates.append(
            os.path.join(os.environ.get("USERPROFILE", ""), ".local", "bin", "headroom.exe")
        )
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _install_headroom_cli() -> str | None:
    """uv → pipx → pip の順で headroom-ai[all] を入れ、バイナリパスを返す。"""
    installers: list[tuple[str, list[str]]] = []
    if shutil.which("uv"):
        # README: macOS では 3.13 を明示（LiteLLM / ホイール互換）
        installers.append((
            "uv",
            ["uv", "tool", "install", "--python", "3.13", HEADROOM_PKG],
        ))
        installers.append(("uv", ["uv", "tool", "install", HEADROOM_PKG]))
    if shutil.which("pipx"):
        py313 = shutil.which("python3.13")
        if py313:
            installers.append((
                "pipx",
                ["pipx", "install", "--python", py313, HEADROOM_PKG],
            ))
        installers.append(("pipx", ["pipx", "install", HEADROOM_PKG]))
    installers.append((
        "pip",
        [sys.executable, "-m", "pip", "install", HEADROOM_PKG],
    ))

    seen: set[tuple[str, ...]] = set()
    for name, cmd in installers:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        print(f"   {name} で {HEADROOM_PKG} をインストール中...")
        print(f"   $ {' '.join(cmd)}")
        rc, _, err = _run_text(cmd, timeout=600)
        if rc == 0:
            bin_path = _headroom_bin_path()
            if bin_path:
                print(f"   ✓ headroom CLI: {bin_path}")
                return bin_path
            print("   ⚠ インストール成功だが headroom コマンドが見つかりません (PATH を確認)")
            return None
        print(f"   ✗ {name} でのインストールに失敗 (code {rc})")
        if err:
            print(f"     {err.strip().splitlines()[-1]}")
    return None


def _upgrade_headroom_cli(headroom_bin: str) -> bool:
    """headroom update、だめなら uv/pipx/pip で更新。"""
    print("   headroom update を試行...")
    rc, out, err = _run_text([headroom_bin, "update"], timeout=600)
    if rc == 0:
        print("   ✓ headroom を更新しました (headroom update)")
        return True
    upgraders = [
        ("uv", ["uv", "tool", "upgrade", "headroom-ai"]),
        ("pipx", ["pipx", "upgrade", "headroom-ai"]),
        ("pip", [sys.executable, "-m", "pip", "install", "-U", HEADROOM_PKG]),
    ]
    for name, cmd in upgraders:
        if shutil.which(cmd[0]) is None:
            continue
        print(f"   {name} で更新中...")
        rc2, _, err2 = _run_text(cmd, timeout=600)
        if rc2 == 0:
            print(f"   ✓ headroom を更新しました ({name})")
            return True
        print(f"   ✗ {name} 更新失敗 (code {rc2})")
        if err2:
            print(f"     {err2.strip().splitlines()[-1]}")
    if err or out:
        print(f"   ✗ headroom update 失敗: {(err or out).strip().splitlines()[-1]}")
    return False


def _headroom_mcp_configured(agent_type: str, paths: dict[str, str]) -> bool:
    """対象エージェントに headroom MCP が入っているか。"""
    home = paths["user_home"]
    if agent_type == "claude":
        return (
            _mcp_has_server(os.path.join(home, ".claude.json"), "headroom")
            or _mcp_has_server(os.path.join(paths["agent_home"], ".mcp.json"), "headroom")
        )
    if agent_type == "codex":
        return _mcp_has_server(os.path.join(home, ".codex", "config.toml"), "headroom")
    if agent_type == "kiro":
        return _mcp_has_server(
            os.path.join(paths["agent_home"], "settings", "mcp.json"), "headroom",
        )
    if agent_type == "copilot":
        return _mcp_has_server(_get_vscode_user_mcp_path(), "headroom")
    return False


def _upsert_json_mcp_headroom(config_path: str, servers_key: str, headroom_bin: str) -> bool:
    """JSON MCP 設定に headroom サーバーを追加／更新する。"""
    existing = _load_mcp_config(config_path)
    servers = existing.setdefault(servers_key, {})
    servers["headroom"] = {
        "command": headroom_bin,
        "args": ["mcp", "serve"],
    }
    os.makedirs(os.path.dirname(os.path.abspath(config_path)) or ".", exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"   ✓ MCP: {config_path}")
    return True


def _upsert_codex_toml_headroom(headroom_bin: str, force: bool) -> bool:
    """~/.codex/config.toml に headroom MCP を書く。"""
    path = os.path.join(os.path.expanduser("~"), ".codex", "config.toml")
    block = (
        "[mcp_servers.headroom]\n"
        f'command = "{headroom_bin}"\n'
        'args = ["mcp", "serve"]\n'
    )
    text = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if "[mcp_servers.headroom]" in text and not force:
            print(f"   ✓ Codex MCP は設定済み → スキップ ({path})")
            return True
        # 既存セクションを除去してから付け直す
        text = re.sub(
            r"\n?\[mcp_servers\.headroom\](?:\n(?:[^\[\n][^\n]*)?)*",
            "",
            text,
        )

    text = (text.rstrip() + "\n\n" + block) if text.strip() else block
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    print(f"   ✓ MCP: {path}")
    return True


def _wire_headroom_mcp(
    agent_type: str, paths: dict[str, str], headroom_bin: str, force: bool,
) -> bool:
    """--agent 向けに headroom MCP を配線する（wrap は対話起動のため使わない）。"""
    if agent_type == "claude":
        cmd = [headroom_bin, "mcp", "install"]
        if force:
            cmd.append("--force")
        print(f"   $ {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd)
        except OSError as e:
            print(f"   ✗ headroom mcp install 失敗: {e}")
            return False
        if result.returncode == 0:
            print("   ✓ headroom MCP を claude 向けに登録しました")
            return True
        print(f"   ⚠ mcp install 失敗 (code {result.returncode}) — JSON へフォールバック")
        # ~/.claude.json と .mcp.json の両方に書いておく
        ok1 = _upsert_json_mcp_headroom(
            os.path.join(paths["user_home"], ".claude.json"), "mcpServers", headroom_bin,
        )
        ok2 = _upsert_json_mcp_headroom(
            os.path.join(paths["agent_home"], ".mcp.json"), "mcpServers", headroom_bin,
        )
        return ok1 or ok2

    if agent_type == "codex":
        return _upsert_codex_toml_headroom(headroom_bin, force=force)

    if agent_type == "kiro":
        target = os.path.join(paths["agent_home"], "settings", "mcp.json")
        return _upsert_json_mcp_headroom(target, "mcpServers", headroom_bin)

    if agent_type == "copilot":
        return _upsert_json_mcp_headroom(
            _get_vscode_user_mcp_path(), "servers", headroom_bin,
        )

    return False


def setup_headroom(agent_type: str, force: bool = False) -> bool:
    """headroomlabs-ai/headroom (PyPI: headroom-ai) を --agent 向けにセットアップする。

    - CLI を uv/pipx/pip で導入（最新ならスキップ、古ければ更新）
    - ``headroom wrap`` は対話セッション起動のため使わない
    - MCP (``headroom mcp serve``) をエージェント設定に配線
    - Intel Mac など非対応環境・未対応エージェントはスキップ
    """
    if agent_type not in HEADROOM_SUPPORTED_AGENTS:
        print(
            f"   ⚠ headroom はエージェント '{agent_type}' に未対応のためスキップします"
            f" (対応: {', '.join(sorted(HEADROOM_SUPPORTED_AGENTS))})"
        )
        return False

    if not _headroom_env_supported():
        system = platform.system()
        machine = platform.machine()
        print(
            f"   ⚠ headroom は環境 {system}/{machine} に未対応のためスキップします"
            " (対応: macOS Apple Silicon / Linux / Windows)"
        )
        return False

    paths = resolve_paths(agent_type)
    headroom_bin = _headroom_bin_path()
    current = _cli_version_string([headroom_bin, "--version"]) if headroom_bin else None
    if current is None and headroom_bin:
        current = _cli_version_string([headroom_bin, "version"])
    latest = _pypi_latest_version(HEADROOM_PYPI)
    outdated = _version_outdated(current, latest)
    mcp_ok = _headroom_mcp_configured(agent_type, paths) if headroom_bin else False

    if current:
        print(f"   CLI: {current}" + (f" (PyPI: {latest})" if latest else ""))
    else:
        print("   CLI: 未導入" + (f" (PyPI: {latest})" if latest else ""))

    need_install = force or headroom_bin is None
    need_upgrade = force or outdated is True

    if headroom_bin and not need_upgrade and mcp_ok and not force:
        print(f"   ✓ headroom は最新で {agent_type} にも設定済み → スキップ")
        return True

    if need_install:
        headroom_bin = _install_headroom_cli()
        if headroom_bin is None:
            return False
        mcp_ok = False
    elif need_upgrade:
        if not _upgrade_headroom_cli(headroom_bin):
            return False
        headroom_bin = _headroom_bin_path() or headroom_bin
        mcp_ok = False
    else:
        if outdated is None:
            print("   ✓ headroom CLI あり（更新可否は判定不能のため現状維持）")
        else:
            print(f"   ✓ headroom CLI は最新です ({headroom_bin})")

    if mcp_ok and not force:
        print(f"   ✓ {agent_type} 向け MCP 設定済み → スキップ")
        return True

    return _wire_headroom_mcp(agent_type, paths, headroom_bin, force=force)


def main() -> None:
    args = parse_args()
    agent_type = args.agent
    install_all = args.all_skills
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

    # 2. スキルをコピー
    if install_all:
        # 全スキルのうち、このエージェントに対応しているものをインストール
        all_skills = _discover_all_skills(REPO_SKILLS_DIR)
        target_skills = [
            name for name in all_skills
            if _is_skill_for_agent(os.path.join(REPO_SKILLS_DIR, name), agent_type)
        ]
        print(f"\n2. 全スキルをインストール ({len(target_skills)} 件)...")
    else:
        # コアスキル + このエージェント専用スキル
        agent_specific = _discover_agent_specific_skills(REPO_SKILLS_DIR, agent_type)
        # 重複を除きつつ順序を保持
        seen: set[str] = set()
        target_skills = []
        for name in list(CORE_SKILLS) + agent_specific:
            if name not in seen:
                seen.add(name)
                target_skills.append(name)
        if agent_specific:
            print(f"\n2. コアスキル + {agent_type} 固有スキル ({len(target_skills)} 件) をインストール...")
        else:
            print("\n2. コアスキルをインストール...")

    installed = copy_skills(paths, target_skills)
    if not installed:
        print("   エラー: インストールできるスキルがありません")
        sys.exit(1)

    # 3. レジストリ設定
    print("\n3. レジストリを設定...")
    setup_registry(installed, paths, agent_type)

    # 4. 指示ファイルをコピー
    print("\n4. エージェント指示ファイルをコピー...")
    if not copy_agent_instructions(paths, agent_type):
        print("   (対応するファイルが見つかりません、スキップ)")

    # 5. MCP 設定
    print("\n5. MCP 設定をセットアップ...")
    if not setup_mcp_config(paths, agent_type, args.skip_config):
        print("   (.github/mcp/mcp.json が見つかりません、スキップ)")

    # 6. エージェント固有のセットアップ
    if agent_type == "kiro":
        print("\n6. LSP をセットアップ (Kiro)...")
        setup_lsp_for_kiro()
    elif agent_type == "claude":
        print("\n6. Claude Code hooks を設定...")
        if setup_claude_hooks(paths):
            print("   Stop hook (ltm-use build_index) を登録しました")
        # Kiro と GitHub Copilot はセッション停止フックの仕組みを持たないため
        # common.instructions.md の指示でセッション終了時の記憶保存を行う

    force_external = args.force_external

    # 7. playwright-cli インストール（--agent の skill_home へ展開）
    if args.excludes_external_skills:
        print("\n7. playwright-cli をスキップ (--excludes-external-skills が指定されました)")
    else:
        print(f"\n7. playwright-cli をセットアップ (agent={agent_type})...")
        setup_playwright_cli_skill(paths, force=force_external)

    # 8. codegraph インストール（対応エージェントのみ）
    if args.excludes_external_skills:
        print("\n8. codegraph をスキップ (--excludes-external-skills が指定されました)")
    else:
        print(f"\n8. codegraph をセットアップ (agent={agent_type})...")
        setup_codegraph(agent_type, force=force_external)

    # 9. graphify インストール（--platform でエージェント限定）
    if args.excludes_external_skills:
        print("\n9. graphify をスキップ (--excludes-external-skills が指定されました)")
    else:
        print(f"\n9. graphify をセットアップ (agent={agent_type})...")
        setup_graphify(agent_type, force=force_external)

    # 10. caveman インストール（--agent で指定したエージェントのみ）
    if args.excludes_external_skills:
        print("\n10. caveman をスキップ (--excludes-external-skills が指定されました)")
    else:
        print(f"\n10. caveman をセットアップ (agent={agent_type})...")
        setup_caveman(agent_type, force=force_external)

    # 11. rtk インストール（対応 OS・エージェントのみ）
    if args.excludes_external_skills:
        print("\n11. rtk をスキップ (--excludes-external-skills が指定されました)")
    else:
        print(f"\n11. rtk をセットアップ (agent={agent_type})...")
        setup_rtk(agent_type, force=force_external)

    # 12. ponytail インストール（--agent 向け plugin / steering）
    if args.excludes_external_skills:
        print("\n12. ponytail をスキップ (--excludes-external-skills が指定されました)")
    else:
        print(f"\n12. ponytail をセットアップ (agent={agent_type})...")
        setup_ponytail(agent_type, force=force_external)

    # 13. headroom インストール（CLI + --agent 向け MCP）
    if args.excludes_external_skills:
        print("\n13. headroom をスキップ (--excludes-external-skills が指定されました)")
    else:
        print(f"\n13. headroom をセットアップ (agent={agent_type})...")
        setup_headroom(agent_type, force=force_external)

    # 完了
    print("\n" + "=" * 50)
    label = "全" if install_all else "コア"
    print(f"インストール完了: {len(installed)} 件の{label}スキル")
    print("=" * 50)
    print(f"\nエージェント:   {agent_type}")
    print(f"スキル:         {paths['skill_home']}")
    print(f"レジストリ:     {paths['registry_path']}")
    print(f"インストール元: {paths['install_dir']}")
    print("\n次のステップ:")
    print('  - 「スキルをpullして」で最新スキルを取得')
    print('  - 「スクラムして」でscrum-masterを起動')
    print('  - 「スキルを探して」でリポジトリ内のスキルを検索')

    # 6. スキル設定プロンプト
    if not args.skip_config:
        prompt_skill_configs(installed, paths)


if __name__ == "__main__":
    main()
