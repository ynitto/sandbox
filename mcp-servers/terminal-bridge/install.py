#!/usr/bin/env python3
"""Terminal Bridge MCP Server インストーラー。

VS Code 拡張機能 ``vscode-terminal-bridge`` と組で動く MCP サーバーを、
指定エージェントのユーザーレベル設定に登録する。

ふるまい:
    1. server.py を ``~/.mcp-servers/terminal-bridge/`` にコピー
    2. （省略可）VS Code 拡張機能をビルドして ``code --install-extension`` で導入
    3. 指定エージェントのユーザー設定ファイル（mcp.json 等）にエントリをマージ

使い方:
    python install.py                       # インストール済みエージェントを自動検出
    python install.py --agent claude        # Claude Code ユーザー設定
    python install.py --agent copilot       # VS Code ユーザー設定 (Copilot / Codex)
    python install.py --agent codex
    python install.py --agent kiro          # Kiro ユーザー設定
    python install.py --all                 # 全エージェント
    python install.py --skip-extension      # 拡張機能のビルド・インストールを省略

書き込み先（ユーザーレベル）:
    claude   → ~/.claude/.mcp.json
    copilot  → VS Code User データ配下 mcp.json
    codex    → VS Code User データ配下 mcp.json
    kiro     → ~/.kiro/settings/mcp.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SRC = os.path.join(SCRIPT_DIR, "server.py")
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
EXTENSION_DIR = os.path.join(REPO_ROOT, "vscode-extensions", "terminal-bridge")

SERVER_NAME = "terminal-bridge"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_user_home() -> str:
    return os.environ.get("USERPROFILE", os.path.expanduser("~"))


def get_server_install_dir() -> str:
    return os.path.join(get_user_home(), ".mcp-servers", "terminal-bridge")


def get_server_install_path() -> str:
    return os.path.join(get_server_install_dir(), "server.py")


def get_vscode_user_mcp_path() -> str:
    """VS Code のユーザーレベル mcp.json の絶対パスを返す。"""
    system = platform.system()
    if system == "Darwin":
        base = os.path.expanduser("~/Library/Application Support/Code/User")
    elif system == "Windows":
        base = os.path.join(os.environ.get("APPDATA", ""), "Code", "User")
    else:
        base = os.path.expanduser("~/.config/Code/User")
    return os.path.join(base, "mcp.json")


# ---------------------------------------------------------------------------
# Agent configuration definitions
# ---------------------------------------------------------------------------

# format: "vscode"    → {"inputs":[], "servers": {...}}    (Copilot / Codex in VS Code)
#         "anthropic" → {"mcpServers": {...}}              (Claude Code / Kiro)
AGENTS: dict[str, dict] = {
    "claude": {
        "description": "Claude Code",
        "format": "anthropic",
        "config_path": lambda: os.path.join(get_user_home(), ".claude", ".mcp.json"),
    },
    "copilot": {
        "description": "GitHub Copilot (VS Code)",
        "format": "vscode",
        "config_path": get_vscode_user_mcp_path,
    },
    "codex": {
        "description": "OpenAI Codex (VS Code)",
        "format": "vscode",
        "config_path": get_vscode_user_mcp_path,
    },
    "kiro": {
        "description": "Kiro (AWS)",
        "format": "anthropic",
        "config_path": lambda: os.path.join(
            get_user_home(), ".kiro", "settings", "mcp.json"
        ),
    },
}

VSCODE_AGENTS = {"copilot", "codex"}

DETECT_COMMANDS = {
    "claude":  ["claude", "--version"],
    "copilot": ["gh", "copilot", "--version"],
    "codex":   ["codex", "--version"],
    "kiro":    ["kiro-cli", "--version"],
}


# ---------------------------------------------------------------------------
# MCP config entry builders
# ---------------------------------------------------------------------------

def _server_entry_vscode(server_path: str) -> dict:
    return {
        "type": "stdio",
        "command": "uv",
        "args": ["run", server_path],
        "env": {},
    }


def _server_entry_anthropic(server_path: str) -> dict:
    return {
        "command": "uv",
        "args": ["run", server_path],
        "env": {},
    }


def build_vscode_config(existing: dict, server_path: str) -> dict:
    config = dict(existing)
    servers = dict(config.get("servers", {}))
    servers[SERVER_NAME] = _server_entry_vscode(server_path)
    config["servers"] = servers
    config.setdefault("inputs", [])
    return config


def build_anthropic_config(existing: dict, server_path: str) -> dict:
    config = dict(existing)
    servers = dict(config.get("mcpServers", {}))
    servers[SERVER_NAME] = _server_entry_anthropic(server_path)
    config["mcpServers"] = servers
    return config


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_existing(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"   警告: {path} の JSON 解析に失敗しました。上書きします。")
        return {}


def write_config(path: str, config: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def install_server() -> str:
    dest_dir = get_server_install_dir()
    dest_path = get_server_install_path()
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(SERVER_SRC, dest_path)
    if platform.system() != "Windows":
        os.chmod(dest_path, 0o755)
    return dest_path


# ---------------------------------------------------------------------------
# VS Code extension build / install
# ---------------------------------------------------------------------------

def install_vscode_extension() -> bool:
    """vsce で .vsix を作って ``code --install-extension`` する。"""
    if not os.path.isdir(EXTENSION_DIR):
        print(f"   警告: 拡張機能ソースが見つかりません: {EXTENSION_DIR}")
        return False
    if not shutil.which("npm"):
        print("   警告: npm が見つかりません。拡張機能のビルドをスキップします")
        print("         手動で `npm install && npm run compile && vsce package` してください")
        return False
    if not shutil.which("code"):
        print("   警告: code コマンドが見つかりません。")
        print("         VS Code の 'Shell Command: Install code in PATH' を実行してから再試行してください")
        return False

    print(f"   → npm install ({EXTENSION_DIR})")
    res = subprocess.run(["npm", "install"], cwd=EXTENSION_DIR)
    if res.returncode != 0:
        print("   ✗ npm install に失敗しました")
        return False

    print("   → npm run compile")
    res = subprocess.run(["npm", "run", "compile"], cwd=EXTENSION_DIR)
    if res.returncode != 0:
        print("   ✗ TypeScript のコンパイルに失敗しました")
        return False

    vsix_path = os.path.join(EXTENSION_DIR, "terminal-bridge.vsix")
    print("   → vsce package")
    npx = "npx.cmd" if platform.system() == "Windows" else "npx"
    res = subprocess.run(
        [npx, "--yes", "@vscode/vsce", "package", "--out", vsix_path],
        cwd=EXTENSION_DIR,
    )
    if res.returncode != 0:
        print("   ✗ vsce package に失敗しました")
        return False

    print(f"   → code --install-extension {vsix_path}")
    res = subprocess.run(
        ["code", "--install-extension", vsix_path, "--force"],
    )
    if res.returncode != 0:
        print("   ✗ code --install-extension に失敗しました")
        return False

    print("   ✓ Terminal Bridge 拡張機能をインストールしました")
    return True


# ---------------------------------------------------------------------------
# Per-agent installation
# ---------------------------------------------------------------------------

def _config_path_for(agent: str) -> str:
    return AGENTS[agent]["config_path"]()


def install_agent(agent: str, server_path: str) -> str:
    spec = AGENTS[agent]
    config_path = _config_path_for(agent)
    existing = load_existing(config_path)
    if spec["format"] == "vscode":
        config = build_vscode_config(existing, server_path)
    else:
        config = build_anthropic_config(existing, server_path)
    write_config(config_path, config)
    return config_path


def install_vscode_user(server_path: str) -> str:
    """VS Code ユーザー設定の mcp.json を 1 回だけ書き込む。"""
    config_path = get_vscode_user_mcp_path()
    existing = load_existing(config_path)
    config = build_vscode_config(existing, server_path)
    write_config(config_path, config)
    return config_path


def detect_agents() -> list[str]:
    found = []
    for agent, cmd in DETECT_COMMANDS.items():
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=5)
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
        description="Terminal Bridge MCP Server インストーラー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
対応エージェント:
  claude   Claude Code     → ~/.claude/.mcp.json
  copilot  GitHub Copilot  → VS Code User mcp.json
  codex    OpenAI Codex    → VS Code User mcp.json
  kiro     Kiro            → ~/.kiro/settings/mcp.json
""",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--agent",
        choices=list(AGENTS.keys()),
        metavar="AGENT",
        help="登録対象エージェント: %(choices)s",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="全エージェントのユーザー設定に登録する",
    )
    parser.add_argument(
        "--skip-extension",
        action="store_true",
        help="VS Code 拡張機能のビルド・インストールを省略する",
    )
    return parser.parse_args()


def _resolve_targets(args: argparse.Namespace) -> list[str]:
    if args.all:
        print("\nモード: 全エージェント")
        return list(AGENTS.keys())
    if args.agent:
        print(f"\nモード: {args.agent} ({AGENTS[args.agent]['description']})")
        return [args.agent]
    print("\nインストール済みエージェントを検出中...")
    targets = detect_agents()
    if not targets:
        print("  検出されたエージェントがありません。")
        print("  --agent または --all で対象を指定してください。")
        sys.exit(1)
    for a in targets:
        print(f"  検出: {a} ({AGENTS[a]['description']})")
    return targets


def main() -> None:
    args = parse_args()

    print("=" * 54)
    print("  Terminal Bridge MCP Server インストーラー")
    print("=" * 54)

    if not os.path.isfile(SERVER_SRC):
        print(f"\nエラー: server.py が見つかりません: {SERVER_SRC}")
        sys.exit(1)

    targets = _resolve_targets(args)

    print("\n1. VS Code 拡張機能をセットアップ...")
    if args.skip_extension:
        print("   --skip-extension が指定されました。スキップします")
    else:
        install_vscode_extension()

    print("\n2. server.py を共有ディレクトリにコピー...")
    server_path = install_server()
    print(f"   → {server_path}")

    print("\n3. MCP 設定にエントリを追加...")
    vscode_targets = [a for a in targets if a in VSCODE_AGENTS]
    other_targets = [a for a in targets if a not in VSCODE_AGENTS]
    generated: list[tuple[str, str]] = []

    if vscode_targets:
        path = install_vscode_user(server_path)
        label = " / ".join(AGENTS[a]["description"] for a in vscode_targets)
        generated.append((label, path))

    for agent in other_targets:
        path = install_agent(agent, server_path)
        generated.append((AGENTS[agent]["description"], path))

    for label, path in generated:
        print(f"   [{label}] → {path}")

    print("\n" + "=" * 54)
    print("  インストール完了")
    print("=" * 54)
    print(f"\nserver.py     : {server_path}")
    if not args.skip_extension:
        print(f"拡張機能ソース: {EXTENSION_DIR}")
    print()
    _print_next_steps(targets, args.skip_extension)


def _print_next_steps(agents: list[str], skipped_extension: bool) -> None:
    print("次のステップ:")
    if skipped_extension:
        print()
        print("  [VS Code 拡張機能]")
        print(f"    cd {EXTENSION_DIR}")
        print("    npm install && npm run compile")
        print("    npx --yes @vscode/vsce package")
        print("    code --install-extension terminal-bridge.vsix --force")
    print()
    print("  [動作確認]")
    print("    VS Code を再起動した後、シェルから:")
    print("      curl http://127.0.0.1:52718/api/health")
    print("    が {\"status\":\"ok\", ...} を返せばブリッジは稼働中。")

    if any(a in VSCODE_AGENTS for a in agents):
        print()
        print("  [GitHub Copilot / Codex]")
        print("    Copilot Chat を Agent モードで開き、ツール一覧に")
        print("    terminal-bridge の各ツールが表示されることを確認。")
    if "claude" in agents:
        print()
        print("  [Claude Code]")
        print("    `claude` を再起動すると ~/.claude/.mcp.json から")
        print("    terminal-bridge が読み込まれます。")
    if "kiro" in agents:
        print()
        print("  [Kiro]")
        print("    ~/.kiro/settings/mcp.json から自動的に読み込まれます。")

    print()
    print("  uv がない場合: pip install uv  または  https://docs.astral.sh/uv/")


if __name__ == "__main__":
    main()
