#!/usr/bin/env python3
"""GitLab MCP Server インストーラー。

GitLab MCP Server (server.py) を共有ディレクトリにコピーし、
各 AI エージェント向けの MCP 設定ファイルをカレントディレクトリ（GitLab プロジェクトルート）に生成する。

使い方:
    # GitLab プロジェクトのルートで実行する
    cd /path/to/your-gitlab-project
    python /path/to/agent-skills/mcp-servers/gitlab/install.py
    python /path/to/agent-skills/mcp-servers/gitlab/install.py --agent claude
    python /path/to/agent-skills/mcp-servers/gitlab/install.py --agent copilot
    python /path/to/agent-skills/mcp-servers/gitlab/install.py --agent codex
    python /path/to/agent-skills/mcp-servers/gitlab/install.py --agent amazonq
    python /path/to/agent-skills/mcp-servers/gitlab/install.py --agent kiro
    python /path/to/agent-skills/mcp-servers/gitlab/install.py --all

生成されるファイル:
    claude   → .mcp.json                    (Claude Code プロジェクト設定)
    copilot  → .vscode/mcp.json             (GitHub Copilot / VSCode)
    codex    → .vscode/mcp.json             (OpenAI Codex in VSCode)
    amazonq  → .vscode/mcp.json             (Amazon Q Developer in VSCode)
    kiro     → .kiro/settings/mcp.json      (Kiro by AWS)

server.py の共有インストール先:
    Linux/macOS: ~/.mcp-servers/gitlab/server.py
    Windows:     $env:USERPROFILE\\.mcp-servers\\gitlab\\server.py
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SRC = os.path.join(SCRIPT_DIR, "server.py")

# ---------------------------------------------------------------------------
# エージェント設定定義
# ---------------------------------------------------------------------------

# 各エージェントの設定：(説明, 設定ファイルのCWD相対パス, 設定ファイル形式)
# format: "vscode" = VSCode形式 {"inputs":[], "servers":{}}
#         "anthropic" = {"mcpServers":{}} (Claude / Kiro)
AGENTS: dict[str, dict] = {
    "claude": {
        "description": "Claude Code",
        "config_path": ".mcp.json",
        "format": "anthropic",
    },
    "copilot": {
        "description": "GitHub Copilot (VSCode)",
        "config_path": os.path.join(".vscode", "mcp.json"),
        "format": "vscode",
    },
    "codex": {
        "description": "OpenAI Codex CLI (VSCode)",
        "config_path": os.path.join(".vscode", "mcp.json"),
        "format": "vscode",
    },
    "amazonq": {
        "description": "Amazon Q Developer (VSCode)",
        "config_path": os.path.join(".vscode", "mcp.json"),
        "format": "vscode",
    },
    "kiro": {
        "description": "Kiro (AWS, VSCode)",
        "config_path": os.path.join(".kiro", "settings", "mcp.json"),
        "format": "anthropic",
    },
}

# .vscode/mcp.json を使う複数エージェントのラベル（マージ表示用）
VSCODE_AGENTS = {"copilot", "codex", "amazonq"}


def get_server_install_dir() -> str:
    """server.py の共有インストール先ディレクトリを返す。"""
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".mcp-servers", "gitlab")


def get_server_install_path() -> str:
    return os.path.join(get_server_install_dir(), "server.py")


# ---------------------------------------------------------------------------
# MCP 設定ファイルの生成
# ---------------------------------------------------------------------------

def _server_entry_vscode(server_path: str) -> dict:
    """VSCode形式 (servers.*) のサーバーエントリを返す。"""
    return {
        "type": "stdio",
        "command": "uv",
        "args": ["run", server_path],
        "cwd": "${workspaceFolder}",
        "env": {
            "GITLAB_TOKEN": "${input:gitlab-token}",
        },
    }


def _server_entry_anthropic(server_path: str) -> dict:
    """Anthropic形式 (mcpServers.*) のサーバーエントリを返す。"""
    return {
        "command": "uv",
        "args": ["run", server_path],
        "cwd": "${workspaceFolder}",
        "env": {
            "GITLAB_TOKEN": "${input:gitlab-token}",
        },
    }


def _input_entry() -> dict:
    """トークン入力定義を返す。"""
    return {
        "id": "gitlab-token",
        "type": "promptString",
        "description": "GitLab Personal Access Token (glpat-...)",
        "password": True,
    }


def build_vscode_config(existing: dict, server_path: str) -> dict:
    """既存の .vscode/mcp.json にgitlabエントリをマージした設定を返す。"""
    config = dict(existing)

    # inputs セクション
    inputs = config.get("inputs", [])
    if not any(i.get("id") == "gitlab-token" for i in inputs):
        inputs.append(_input_entry())
    config["inputs"] = inputs

    # servers セクション
    servers = config.get("servers", {})
    servers["gitlab"] = _server_entry_vscode(server_path)
    config["servers"] = servers

    return config


def build_anthropic_config(existing: dict, server_path: str) -> dict:
    """既存の .mcp.json / kiro mcp.json にgitlabエントリをマージした設定を返す。"""
    config = dict(existing)

    # inputs セクション（Claude Code と Kiro は inputs をサポート）
    inputs = config.get("inputs", [])
    if not any(i.get("id") == "gitlab-token" for i in inputs):
        inputs.append(_input_entry())
    config["inputs"] = inputs

    # mcpServers セクション
    servers = config.get("mcpServers", {})
    servers["gitlab"] = _server_entry_anthropic(server_path)
    config["mcpServers"] = servers

    return config


# ---------------------------------------------------------------------------
# インストール処理
# ---------------------------------------------------------------------------

def install_server() -> str:
    """server.py を共有インストール先にコピーし、パスを返す。"""
    dest_dir = get_server_install_dir()
    dest_path = get_server_install_path()

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(SERVER_SRC, dest_path)
    # 実行権限を付与 (Unix系)
    if platform.system() != "Windows":
        os.chmod(dest_path, 0o755)

    return dest_path


def write_config(config_path: str, config: dict) -> None:
    """設定ファイルをJSONで書き込む（ディレクトリを自動作成）。"""
    os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_existing(config_path: str) -> dict:
    """既存の設定ファイルを読み込む（存在しない場合は空dict）。"""
    if os.path.isfile(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"   警告: {config_path} のJSON解析に失敗しました。上書きします。")
    return {}


def install_agent(agent: str, server_path: str, cwd: str) -> str:
    """指定エージェントの設定ファイルを生成してcwd配下に書き込む。"""
    spec = AGENTS[agent]
    config_path = os.path.join(cwd, spec["config_path"])
    existing = load_existing(config_path)

    if spec["format"] == "vscode":
        config = build_vscode_config(existing, server_path)
    else:
        config = build_anthropic_config(existing, server_path)

    write_config(config_path, config)
    return config_path


# ---------------------------------------------------------------------------
# エージェント自動検出
# ---------------------------------------------------------------------------

DETECT_COMMANDS = {
    "claude":  ["claude", "--version"],
    "copilot": ["gh", "copilot", "--version"],
    "codex":   ["codex", "--version"],
    "amazonq": ["q", "--version"],
    "kiro":    ["kiro-cli", "--version"],
}


def detect_agents() -> list[str]:
    """インストール済みエージェントを検出して返す。"""
    import subprocess
    found = []
    for agent, cmd in DETECT_COMMANDS.items():
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=5
            )
            if result.returncode == 0:
                found.append(agent)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return found


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GitLab MCP Server インストーラー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
対応エージェント:
  claude   Claude Code           → .mcp.json
  copilot  GitHub Copilot        → .vscode/mcp.json
  codex    OpenAI Codex CLI      → .vscode/mcp.json
  amazonq  Amazon Q Developer    → .vscode/mcp.json
  kiro     Kiro (AWS)            → .kiro/settings/mcp.json

例:
  # カレントディレクトリ（GitLabプロジェクト）でcopilot向けにインストール
  python install.py --agent copilot

  # 全エージェントに一括インストール
  python install.py --all

  # 自動検出（インストール済みのエージェントを検出してインストール）
  python install.py
""",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--agent",
        choices=list(AGENTS.keys()),
        metavar="AGENT",
        help="インストール対象エージェント: %(choices)s",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="全エージェント向けに設定ファイルを生成する",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("=" * 54)
    print("  GitLab MCP Server インストーラー")
    print("=" * 54)

    # server.py の存在確認
    if not os.path.isfile(SERVER_SRC):
        print(f"\nエラー: server.py が見つかりません: {SERVER_SRC}")
        print("このスクリプトは mcp-servers/gitlab/ ディレクトリから実行してください。")
        sys.exit(1)

    # インストール対象エージェントの決定
    if args.all:
        target_agents = list(AGENTS.keys())
        print("\nモード: 全エージェント")
    elif args.agent:
        target_agents = [args.agent]
        print(f"\nモード: {args.agent} ({AGENTS[args.agent]['description']})")
    else:
        print("\nインストール済みエージェントを検出中...")
        target_agents = detect_agents()
        if not target_agents:
            print("  検出されたエージェントがありません。")
            print("  --agent または --all で対象を指定してください。")
            sys.exit(1)
        for a in target_agents:
            print(f"  検出: {a} ({AGENTS[a]['description']})")

    # 1. server.py のインストール
    print(f"\n1. server.py をインストール中...")
    server_path = install_server()
    print(f"   → {server_path}")

    # 2. 設定ファイルの生成
    print("\n2. MCP 設定ファイルを生成中...")
    cwd = os.getcwd()

    # .vscode/mcp.json を使うエージェントをまとめて処理（重複書き込み防止）
    vscode_targets = [a for a in target_agents if a in VSCODE_AGENTS]
    other_targets = [a for a in target_agents if a not in VSCODE_AGENTS]

    generated: list[tuple[str, str]] = []  # (agents_label, config_path)

    # .vscode/mcp.json: 複数エージェントが同一ファイルを共有するので1回だけ書く
    if vscode_targets:
        vscode_config_path = os.path.join(cwd, AGENTS["copilot"]["config_path"])
        existing = load_existing(vscode_config_path)
        config = build_vscode_config(existing, server_path)
        write_config(vscode_config_path, config)
        label = " / ".join(AGENTS[a]["description"] for a in vscode_targets)
        generated.append((label, vscode_config_path))

    # それ以外のエージェント（claude, kiro）
    for agent in other_targets:
        config_path = install_agent(agent, server_path, cwd)
        generated.append((AGENTS[agent]["description"], config_path))

    for label, path in generated:
        rel = os.path.relpath(path, cwd)
        print(f"   [{label}] → {rel}")

    # 完了
    print("\n" + "=" * 54)
    print("  インストール完了")
    print("=" * 54)
    print(f"\nserver.py : {server_path}")
    print(f"プロジェクト: {cwd}")
    print()
    _print_next_steps(target_agents)


def _print_next_steps(agents: list[str]) -> None:
    print("次のステップ:")

    if "claude" in agents:
        print()
        print("  [Claude Code]")
        print("    GITLAB_TOKEN を設定して claude を起動すると")
        print("    .mcp.json のプロンプトでトークン入力が求められます。")
        print("    または: export GITLAB_TOKEN=glpat-xxxxxxxxxxxx")

    if any(a in agents for a in VSCODE_AGENTS):
        print()
        print("  [GitHub Copilot / Codex / Amazon Q (VSCode)]")
        print("    VSCode でこのプロジェクトを開くと、起動時に")
        print("    GitLab Personal Access Token の入力を求められます。")

    if "kiro" in agents:
        print()
        print("  [Kiro]")
        print("    VSCode + Kiro でこのプロジェクトを開くと")
        print("    .kiro/settings/mcp.json が自動的に読み込まれます。")
        print("    起動時に GitLab Personal Access Token の入力を求められます。")

    print()
    print("  トークンの発行: GitLab → User Settings → Access Tokens")
    print("  必要スコープ: api (read_api + write)")
    print()
    print("  uv がない場合: pip install uv  または  https://docs.astral.sh/uv/")


if __name__ == "__main__":
    main()
