#!/usr/bin/env python3
"""
gl_poll_setup.py — GitLab Issue Polling Daemon セットアップ

gitlab-idd ポーリングデーモンのインストール・管理を行う。
スキル実行時およびセッション開始時に呼び出される。

使い方:
  python gl_poll_setup.py [--install]        # 対話的インストール
  python gl_poll_setup.py --session-start    # セッション開始フック（非対話）
  python gl_poll_setup.py --add-repo         # 現在のリポジトリを追加（デーモン再起動なし）
  python gl_poll_setup.py --uninstall        # サービスを削除
  python gl_poll_setup.py --status           # デーモン状態を表示

前提条件:
  - Python 3.8+
  - claude CLI が利用可能（インストール時のみ）
  - GITLAB_TOKEN または GL_TOKEN が設定済み
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
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

DAEMON_SCRIPT_NAME = "gl_poll_daemon.py"
SETUP_SCRIPT_NAME = "gl_poll_setup.py"
SERVICE_NAME = "gitlab-idd-poll"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

DEFAULT_POLL_INTERVAL = 300  # 5 分


# ---------------------------------------------------------------------------
# 設定ディレクトリ・パス
# ---------------------------------------------------------------------------


def get_config_dir() -> Path:
    """OS に応じた設定ディレクトリを返す。"""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "gitlab-idd"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def get_installed_daemon_path() -> Path:
    return get_config_dir() / DAEMON_SCRIPT_NAME


def get_installed_setup_path() -> Path:
    return get_config_dir() / SETUP_SCRIPT_NAME


def load_config() -> dict:
    path = get_config_path()
    if not path.exists():
        return {
            "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
            "repos": [],
            "seen_issues": {},
        }
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    if platform.system() != "Windows":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# エージェント CLI 可用性チェック
# ---------------------------------------------------------------------------


def find_claude() -> Optional[str]:
    """claude コマンドのフルパスを返す。見つからなければ None。"""
    return shutil.which("claude")


def check_agent_cli_available() -> bool:
    """claude CLI がインストールされ動作可能かを確認する。"""
    claude = find_claude()
    if not claude:
        return False
    try:
        result = subprocess.run(
            [claude, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Git リモートからリポジトリ情報取得
# ---------------------------------------------------------------------------


def get_current_repo_info(cwd: Optional[str] = None) -> Optional[dict]:
    """
    カレントディレクトリの git remote origin から
    GitLab リポジトリ情報を取得する。

    Returns:
        {"host": str, "project": str, "local_path": str} または None
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
            cwd=cwd,
        )
        if result.returncode != 0:
            return None
        remote_url = result.stdout.strip()
    except Exception:
        return None

    # SSH: git@gitlab.com:namespace/repo.git
    if remote_url.startswith("git@"):
        without_prefix = remote_url[4:]
        if ":" not in without_prefix:
            return None
        host, path = without_prefix.split(":", 1)
        project = path.rstrip("/").removesuffix(".git")
    elif "://" in remote_url:
        parsed = urllib.parse.urlparse(remote_url)
        host = parsed.hostname or ""
        project = parsed.path.lstrip("/").removesuffix(".git")
    else:
        return None

    # GitLab インスタンスかどうかを簡易チェック（ホスト名に gitlab が含まれるか、
    # または独自ドメインの場合は通過させる）
    if not host or not project:
        return None

    local_path = str(Path(cwd or ".").resolve())
    # git root を取得
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
            cwd=cwd,
        )
        if res.returncode == 0:
            local_path = res.stdout.strip()
    except Exception:
        pass

    return {"host": host, "project": project, "local_path": local_path}


def get_gitlab_token() -> str:
    return os.environ.get("GITLAB_TOKEN") or os.environ.get("GL_TOKEN") or ""


# ---------------------------------------------------------------------------
# リポジトリ設定の追加
# ---------------------------------------------------------------------------


def repo_key(host: str, project: str) -> str:
    return f"{host}|{project}"


def add_repo_to_config(repo_info: dict, config: dict, token: str = "") -> bool:
    """
    リポジトリを設定に追加する。
    既に存在する場合は local_path のみ更新。
    Returns True if config was modified.
    """
    host = repo_info["host"]
    project = repo_info["project"]
    local_path = repo_info["local_path"]
    key = repo_key(host, project)

    for repo in config.get("repos", []):
        if repo_key(repo["host"], repo["project"]) == key:
            changed = repo.get("local_path") != local_path
            repo["local_path"] = local_path
            if token and not repo.get("token"):
                repo["token"] = token
                changed = True
            return changed

    # 新規追加
    entry: dict = {"host": host, "project": project, "local_path": local_path}
    if token:
        entry["token"] = token
    config.setdefault("repos", []).append(entry)
    return True


# ---------------------------------------------------------------------------
# OS 別サービスインストール
# ---------------------------------------------------------------------------


def copy_scripts_to_config_dir() -> None:
    """
    デーモンスクリプトとセットアップスクリプトを設定ディレクトリにコピーする。
    これによりスキルリポジトリの移動・削除後も動作を継続できる。
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    src_dir = Path(__file__).parent

    for name in (DAEMON_SCRIPT_NAME, SETUP_SCRIPT_NAME):
        src = src_dir / name
        dst = config_dir / name
        if src.exists():
            shutil.copy2(src, dst)
            if platform.system() != "Windows":
                os.chmod(dst, 0o755)
            print(f"  コピー: {src} → {dst}")
        elif name == DAEMON_SCRIPT_NAME:
            print(f"  警告: {src} が見つかりません")


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"com.{SERVICE_NAME}.plist"


def install_service_macos(python_exe: str, daemon_path: str, interval: int) -> bool:
    """macOS: LaunchAgent plist を作成して launchctl に登録する。"""
    plist_path = _plist_path()
    token = get_gitlab_token()
    env_block = ""
    if token:
        env_block = textwrap.dedent(f"""\
            <key>EnvironmentVariables</key>
            <dict>
                <key>GITLAB_TOKEN</key>
                <string>{token}</string>
            </dict>
        """)

    plist_content = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
            "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>com.{SERVICE_NAME}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{python_exe}</string>
                <string>{daemon_path}</string>
                <string>--interval</string>
                <string>{interval}</string>
            </array>
            {env_block}
            <key>KeepAlive</key>
            <true/>
            <key>RunAtLoad</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{get_config_dir() / "daemon.log"}</string>
            <key>StandardErrorPath</key>
            <string>{get_config_dir() / "daemon.err.log"}</string>
        </dict>
        </plist>
    """)

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content, encoding="utf-8")
    print(f"  plist 作成: {plist_path}")

    # 既存サービスをアンロードしてから再ロード
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  警告: launchctl load 失敗: {result.stderr.strip()}")
        return False
    print("  macOS LaunchAgent 登録完了")
    return True


def _systemd_service_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "systemd" / "user" / f"{SERVICE_NAME}.service"


def install_service_linux(python_exe: str, daemon_path: str, interval: int) -> bool:
    """Linux: systemd ユーザーサービスをインストールして起動する。"""
    service_path = _systemd_service_path()
    token = get_gitlab_token()
    env_line = f"Environment=GITLAB_TOKEN={token}" if token else ""

    service_content = textwrap.dedent(f"""\
        [Unit]
        Description=GitLab Issue Polling Daemon (gitlab-idd)
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={python_exe} {daemon_path} --interval {interval}
        {env_line}
        Restart=on-failure
        RestartSec=30
        StandardOutput=append:{get_config_dir() / "daemon.log"}
        StandardError=append:{get_config_dir() / "daemon.err.log"}

        [Install]
        WantedBy=default.target
    """)

    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(service_content, encoding="utf-8")
    print(f"  service ファイル作成: {service_path}")

    # systemd ユーザーインスタンスが利用可能か確認
    check = subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        # systemd 不使用環境（cron フォールバック）
        print("  systemd 未使用: cron に登録します")
        return _install_cron_linux(python_exe, daemon_path, interval)

    subprocess.run(["systemctl", "--user", "enable", SERVICE_NAME], capture_output=True)
    result = subprocess.run(
        ["systemctl", "--user", "start", SERVICE_NAME],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  警告: systemd start 失敗: {result.stderr.strip()}")
        return False
    print("  Linux systemd ユーザーサービス登録完了")
    return True


def _install_cron_linux(python_exe: str, daemon_path: str, interval: int) -> bool:
    """systemd が使えない環境向けに crontab で起動を登録する。"""
    cron_cmd = f"@reboot {python_exe} {daemon_path} --interval {interval}"
    try:
        existing = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True,
        ).stdout
        if daemon_path in existing:
            print("  crontab: 既に登録済み")
            return True
        new_crontab = existing.rstrip() + "\n" + cron_cmd + "\n"
        proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True)
        if proc.returncode != 0:
            print(f"  警告: crontab 登録失敗: {proc.stderr.strip()}")
            return False
        print("  crontab @reboot 登録完了")
        return True
    except Exception as e:
        print(f"  crontab エラー: {e}")
        return False


def _windows_task_xml(python_exe: str, daemon_path: str, interval: int) -> str:
    """Windows タスクスケジューラ用 XML を生成する。"""
    token = get_gitlab_token()
    env_cmd = f'cmd /c "set GITLAB_TOKEN={token} && ' if token else ""
    env_cmd_end = '"' if token else ""
    run_cmd = f'{env_cmd}{python_exe} {daemon_path} --interval {interval}{env_cmd_end}'

    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-16"?>
        <Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <Triggers>
            <LogonTrigger>
              <Enabled>true</Enabled>
            </LogonTrigger>
          </Triggers>
          <Actions Context="Author">
            <Exec>
              <Command>{python_exe}</Command>
              <Arguments>"{daemon_path}" --interval {interval}</Arguments>
            </Exec>
          </Actions>
          <Settings>
            <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
            <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
            <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
            <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
          </Settings>
        </Task>
    """)


def install_service_windows(python_exe: str, daemon_path: str, interval: int) -> bool:
    """Windows: タスクスケジューラにログオン時起動タスクを登録する。"""
    result = subprocess.run(
        [
            "schtasks", "/Create", "/F",
            "/TN", SERVICE_NAME,
            "/SC", "ONLOGON",
            "/TR", f'"{python_exe}" "{daemon_path}" --interval {interval}',
            "/RL", "HIGHEST",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  警告: schtasks 登録失敗: {result.stderr.strip()}")
        return False

    # 即座に起動
    subprocess.run(["schtasks", "/Run", "/TN", SERVICE_NAME], capture_output=True)
    print("  Windows タスクスケジューラ登録完了")
    return True


def install_service(python_exe: str, daemon_path: str, interval: int) -> bool:
    """現在の OS に合わせてサービスをインストールする。"""
    system = platform.system()
    print(f"\n[サービスインストール] OS: {system}")
    if system == "Darwin":
        return install_service_macos(python_exe, daemon_path, interval)
    elif system == "Linux":
        return install_service_linux(python_exe, daemon_path, interval)
    elif system == "Windows":
        return install_service_windows(python_exe, daemon_path, interval)
    else:
        print(f"  未対応 OS: {system} — サービス登録をスキップ")
        return False


# ---------------------------------------------------------------------------
# サービス削除
# ---------------------------------------------------------------------------


def uninstall_service() -> None:
    """OS のサービスを削除する。"""
    system = platform.system()
    if system == "Darwin":
        plist = _plist_path()
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        if plist.exists():
            plist.unlink()
        print(f"macOS LaunchAgent 削除: {plist}")
    elif system == "Linux":
        subprocess.run(["systemctl", "--user", "stop", SERVICE_NAME], capture_output=True)
        subprocess.run(["systemctl", "--user", "disable", SERVICE_NAME], capture_output=True)
        svc = _systemd_service_path()
        if svc.exists():
            svc.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        print(f"Linux systemd サービス削除: {svc}")
    elif system == "Windows":
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", SERVICE_NAME], capture_output=True)
        print(f"Windows タスク削除: {SERVICE_NAME}")


# ---------------------------------------------------------------------------
# サービス状態確認
# ---------------------------------------------------------------------------


def show_status() -> None:
    """デーモンの状態を表示する。"""
    config = load_config()
    config_path = get_config_path()
    daemon_path = get_installed_daemon_path()

    print("=== gitlab-idd ポーリングデーモン状態 ===")
    print(f"設定ファイル   : {config_path} ({'存在' if config_path.exists() else '未作成'})")
    print(f"デーモンスクリプト: {daemon_path} ({'存在' if daemon_path.exists() else '未コピー'})")
    print(f"ポーリング間隔  : {config.get('poll_interval_seconds', DEFAULT_POLL_INTERVAL)} 秒")
    print(f"登録リポジトリ  : {len(config.get('repos', []))} 件")
    for repo in config.get("repos", []):
        print(f"  - {repo['host']}/{repo['project']} ({repo.get('local_path', '?')})")

    system = platform.system()
    print(f"\nOS サービス状態 ({system}):")
    if system == "Darwin":
        result = subprocess.run(
            ["launchctl", "list", f"com.{SERVICE_NAME}"],
            capture_output=True, text=True,
        )
        print("  " + (result.stdout.strip() or result.stderr.strip() or "サービス未登録"))
    elif system == "Linux":
        result = subprocess.run(
            ["systemctl", "--user", "status", SERVICE_NAME, "--no-pager"],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines()[:10]:
            print("  " + line)
    elif system == "Windows":
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", SERVICE_NAME, "/FO", "LIST"],
            capture_output=True, text=True,
        )
        print("  " + (result.stdout.strip() or "タスク未登録"))


# ---------------------------------------------------------------------------
# セッション開始フックの設定
# ---------------------------------------------------------------------------


def configure_session_hook(setup_script_path: str, python_exe: str) -> None:
    """
    ~/.claude/settings.json に SessionStart フックを追加する。
    既に登録済みの場合はスキップ。
    """
    settings_path = CLAUDE_SETTINGS_PATH
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.exists():
        try:
            with open(settings_path, encoding="utf-8") as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            settings = {}

    hook_command = f"{python_exe} {setup_script_path} --session-start"

    hooks = settings.setdefault("hooks", {})
    session_hooks: list = hooks.setdefault("SessionStart", [])

    # 既に同じコマンドが登録されていればスキップ
    for entry in session_hooks:
        for h in entry.get("hooks", []):
            if h.get("command") == hook_command:
                print(f"  SessionStart フック: 既に登録済み ({settings_path})")
                return

    session_hooks.append({
        "hooks": [
            {"type": "command", "command": hook_command}
        ]
    })

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    print(f"  SessionStart フック登録: {settings_path}")


# ---------------------------------------------------------------------------
# セッション開始モード（非対話、静かに実行）
# ---------------------------------------------------------------------------


def run_session_start() -> None:
    """
    セッション開始フックとして呼ばれる。
    - カレントリポジトリを設定に追加
    - デーモンが停止していれば再起動
    """
    repo_info = get_current_repo_info()
    if repo_info is None:
        # git リポジトリでなければ何もしない
        return

    # GitLab リポジトリかどうか簡易確認（gitlab が含まれるか独自ドメイン）
    # ここでは無条件に追加を試みる（non-GitLab remote は API で弾かれる）
    config = load_config()
    token = get_gitlab_token()
    changed = add_repo_to_config(repo_info, config, token)
    if changed:
        save_config(config)
        print(
            f"[gitlab-idd] ポーリング対象に追加: "
            f"{repo_info['host']}/{repo_info['project']}"
        )

    # デーモンが動いていなければ起動試行
    _ensure_daemon_running()


def _ensure_daemon_running() -> None:
    """デーモンプロセスが実行中でなければ起動する（best-effort）。"""
    daemon_path = get_installed_daemon_path()
    if not daemon_path.exists():
        return  # 未インストール

    system = platform.system()
    try:
        if system == "Darwin":
            plist = _plist_path()
            if plist.exists():
                subprocess.run(
                    ["launchctl", "start", f"com.{SERVICE_NAME}"],
                    capture_output=True,
                )
        elif system == "Linux":
            svc = _systemd_service_path()
            if svc.exists():
                subprocess.run(
                    ["systemctl", "--user", "start", SERVICE_NAME],
                    capture_output=True,
                )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 対話的インストール
# ---------------------------------------------------------------------------


def run_install() -> None:
    """
    対話的インストール。
    スキル実行時に呼ばれる想定。LLM がユーザー許可を取ってからこのスクリプトを実行する。
    """
    print("=" * 60)
    print("gitlab-idd ポーリングデーモン インストーラー")
    print("=" * 60)

    # 1. claude CLI チェック
    print("\n[1/5] エージェント CLI (claude) の確認...")
    if not check_agent_cli_available():
        print(
            "ERROR: claude コマンドが見つかりません。\n"
            "  インストール: npm install -g @anthropic-ai/claude-code\n"
            "ポーリングデーモンは claude CLI が必要なためインストールを中止します。"
        )
        sys.exit(1)
    claude_path = find_claude()
    print(f"  OK: claude = {claude_path}")

    # 2. カレントリポジトリ確認
    print("\n[2/5] カレントリポジトリの確認...")
    repo_info = get_current_repo_info()
    if repo_info is None:
        print("  警告: カレントディレクトリが git リポジトリではないか、remote origin がありません。")
        print("  リポジトリなしで続行します（後で --add-repo で追加できます）。")
    else:
        print(f"  リポジトリ: {repo_info['host']}/{repo_info['project']}")
        print(f"  ローカルパス: {repo_info['local_path']}")

    # 3. スクリプトをコピー
    print("\n[3/5] デーモンスクリプトをインストールディレクトリへコピー...")
    copy_scripts_to_config_dir()

    # 4. 設定ファイルを更新
    print("\n[4/5] 設定ファイルを更新...")
    config = load_config()
    token = get_gitlab_token()
    if not token:
        print("  警告: GITLAB_TOKEN 未設定。デーモン起動後に環境変数を設定してください。")
    if repo_info:
        add_repo_to_config(repo_info, config, token)
    save_config(config)
    print(f"  設定保存: {get_config_path()}")

    # 5. OS サービスとして登録
    print("\n[5/5] OS サービスへ登録...")
    python_exe = sys.executable
    daemon_path = str(get_installed_daemon_path())
    interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
    ok = install_service(python_exe, daemon_path, interval)

    # SessionStart フック設定
    setup_path = str(get_installed_setup_path())
    configure_session_hook(setup_path, python_exe)

    print("\n" + "=" * 60)
    if ok:
        print("インストール完了！")
        print(f"  デーモンログ: {get_config_dir() / 'daemon.log'}")
        print(f"  設定ファイル: {get_config_path()}")
        print(f"  ポーリング間隔: {interval} 秒")
        print("\n新しいリポジトリを追加するには、そのリポジトリで:")
        print("  python scripts/gl_poll_setup.py --add-repo")
    else:
        print("インストールに一部問題が発生しました。ログを確認してください。")
    print("=" * 60)


# ---------------------------------------------------------------------------
# リポジトリ追加のみ
# ---------------------------------------------------------------------------


def run_add_repo() -> None:
    """カレントリポジトリをポーリング対象に追加する（サービス再起動なし）。"""
    repo_info = get_current_repo_info()
    if repo_info is None:
        print("ERROR: カレントディレクトリが git リポジトリではないか、remote origin がありません。")
        sys.exit(1)

    config = load_config()
    token = get_gitlab_token()
    changed = add_repo_to_config(repo_info, config, token)
    if changed:
        save_config(config)
        print(f"追加: {repo_info['host']}/{repo_info['project']}")
        print(f"  ローカルパス: {repo_info['local_path']}")
        print(f"設定ファイル更新: {get_config_path()}")
    else:
        print(f"既に登録済み: {repo_info['host']}/{repo_info['project']}")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="gitlab-idd polling daemon setup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--install", action="store_true",
        help="対話的インストール（デフォルト動作）",
    )
    group.add_argument(
        "--session-start", action="store_true",
        help="セッション開始フックモード（非対話）",
    )
    group.add_argument(
        "--add-repo", action="store_true",
        help="カレントリポジトリをポーリング対象に追加",
    )
    group.add_argument(
        "--uninstall", action="store_true",
        help="デーモンサービスを削除",
    )
    group.add_argument(
        "--status", action="store_true",
        help="デーモン状態を表示",
    )
    args = parser.parse_args()

    if args.session_start:
        run_session_start()
    elif args.add_repo:
        run_add_repo()
    elif args.uninstall:
        uninstall_service()
        print("サービスを削除しました。設定ファイルは保持されています。")
        print(f"完全削除: rm -rf {get_config_dir()}")
    elif args.status:
        show_status()
    else:
        # --install または引数なし
        run_install()


if __name__ == "__main__":
    main()
