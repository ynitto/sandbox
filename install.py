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
    5. セットアップ完了メッセージを表示

冪等: 既にインストール済みの場合はスキルを上書き更新、レジストリは既存設定を保持する。
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
        help="playwright-cli など外部スキルをインストールしない",
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


def setup_playwright_cli_skill(paths: dict[str, str]) -> bool:
    """playwright-cli npm パッケージのインストールとスキルの展開を行う。

    処理源:
      1. npm が利用不可なら警告を表示して中止する。
      2. @playwright/cli が未インストールなら npm install -g でインストールする。
      3. playwright-cli install --skills を user_home で実行する。
         (常に ~/.claude/skills/playwright-cli に展開される)
      4. ターゲットの skill_home に playwright-cli がなければコピーする。
    """
    # 1. npm の存在確認
    if not _check_npm_available():
        print("   ⚠ npm が見つかりません。playwright-cli のセットアップをスキップします")
        print("     Node.js/npm をインストール後に再実行してください")
        return False

    # 2. playwright-cli のインストール確認
    cli_available = bool(shutil.which("playwright-cli"))
    if not cli_available:
        print("   @playwright/cli は未インストールです。npm install を実行します...")
        try:
            result = subprocess.run(
                ["npm", "install", "-g", "@playwright/cli"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"   ✗ @playwright/cli のインストールに失敗しました (code {result.returncode})")
                if result.stderr:
                    print(f"     {result.stderr.strip()}")
                return False
            print("   ✓ @playwright/cli をインストールしました")
            cli_available = bool(shutil.which("playwright-cli"))
        except FileNotFoundError:
            print("   ⚠ npm が見つかりません。playwright-cli のセットアップをスキップします")
            return False
    else:
        print("   ✓ playwright-cli はインストール済みです")

    # 2. playwright-cli install --skills で ~/.claude/skills/playwright-cli に展開
    user_home = paths["user_home"]
    claude_skill_path = os.path.join(user_home, ".claude", "skills", "playwright-cli")
    print(f"   playwright-cli install --skills を実行します (CWD={user_home})...")
    try:
        result = subprocess.run(
            ["playwright-cli", "install", "--skills"],
            cwd=user_home,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"   ✗ playwright-cli install --skills に失敗しました (code {result.returncode})")
            if result.stderr:
                print(f"     {result.stderr.strip()}")
            return False
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                print(f"   {line}")
    except FileNotFoundError:
        print("   ✗ playwright-cli コマンドが見つかりません")
        return False

    if not os.path.isdir(claude_skill_path):
        print(f"   ✗ 展開先 {claude_skill_path} が見つかりません")
        return False

    # 3. ターゲットの skill_home になければコピー
    target_path = os.path.join(paths["skill_home"], "playwright-cli")
    if os.path.abspath(claude_skill_path) == os.path.abspath(target_path):
        # claude エージェント (源とターゲットが同じ)
        print(f"   ✓ {target_path} (源と同一、コピー不要)")
        return True
    if os.path.isdir(target_path):
        shutil.rmtree(target_path)
        print(f"   既存の {target_path} を削除しました（バージョン更新のため）")
    shutil.copytree(claude_skill_path, target_path)
    print(f"   ✓ {claude_skill_path} → {target_path}")
    return True


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

    # 7. playwright-cli インストール
    if args.excludes_external_skills:
        print("\n7. playwright-cli をスキップ (--excludes-external-skills が指定されました)")
    else:
        print("\n7. playwright-cli をセットアップ...")
        setup_playwright_cli_skill(paths)

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
