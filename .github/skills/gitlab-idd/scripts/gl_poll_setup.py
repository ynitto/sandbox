#!/usr/bin/env python3
"""
gl_poll_setup.py — GitLab Issue Polling Daemon セットアップ

gitlab-idd ポーリングデーモンのインストール・管理を行う単独実行スクリプト。
スキルリポジトリの内外どちらからでも実行できる。

対応 CLI（自動検出、優先順）: claude → codex → kiro → amazonq
  kiro は Windows 環境では WSL2 経由で実行する。

Requirements: Python 3.11+  /  stdlib only

使い方:
  python gl_poll_setup.py [--install]        # 対話的インストール（デフォルト）
  python gl_poll_setup.py --session-start    # SessionStart フック（非対話）
  python gl_poll_setup.py --add-repo         # カレントリポジトリを追加
  python gl_poll_setup.py --restart          # デーモンを再起動
  python gl_poll_setup.py --set KEY=VALUE    # 設定を変更してデーモンを再起動
  python gl_poll_setup.py --uninstall        # サービスを削除
  python gl_poll_setup.py --status           # デーモン状態を表示
  python gl_poll_setup.py --dry-run          # インストール処理をシミュレート（副作用なし）

  # インストール済みの場所からも実行可能
  python ~/.config/gitlab-idd/gl_poll_setup.py --status
  python ~/.config/gitlab-idd/gl_poll_setup.py --set mock_cli=true

--set で変更可能なキー:
  mock_cli=true/false          CLIをモックに切り替え（プロンプトをファイルに保存）
  preferred_cli=claude/...     使用する CLI を固定
  poll_interval=300            ポーリング間隔（秒）

--install オプション:
  --allow-mock-cli    CLI が見つからない場合でもモックCLIモードでインストール

前提条件（インストール時のみ）:
  - Python 3.11+
  - エージェント CLI（claude/codex/kiro-cli/q のいずれか）
    ※ CLI がなくても --allow-mock-cli でモックCLIモードでインストール可能
  - GITLAB_TOKEN または GL_TOKEN が設定済み
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import textwrap
import urllib.parse
from pathlib import Path

from gl_common import (
    RepoConfig, DaemonConfig, DEFAULT_POLL_INTERVAL,
    AgentCLI, _CLI_CANDIDATES,
    find_available_agent_clis, find_best_agent_cli,
    get_config_dir, get_config_path,
    load_config, save_config,
)


SERVICE_NAME = "gitlab-idd-poll"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


# ---------------------------------------------------------------------------
# セットアップ固有のパス管理
# ---------------------------------------------------------------------------

def get_installed_daemon_path() -> Path:
    return get_config_dir() / "gl_poll_daemon.py"


def get_installed_setup_path() -> Path:
    return get_config_dir() / "gl_poll_setup.py"


# ---------------------------------------------------------------------------
# Git リモートからリポジトリ情報を取得
# ---------------------------------------------------------------------------

def get_current_repo_info(cwd: str | None = None) -> RepoConfig | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, cwd=cwd,
        )
        if result.returncode != 0:
            return None
        remote_url = result.stdout.strip()
    except Exception:
        return None

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

    if not host or not project:
        return None

    local_path = str(Path(cwd or ".").resolve())
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10, cwd=cwd,
        )
        if res.returncode == 0:
            local_path = res.stdout.strip()
    except Exception:
        pass

    return RepoConfig(host=host, project=project, local_path=local_path)


def get_gitlab_token() -> str:
    return os.environ.get("GITLAB_TOKEN") or os.environ.get("GL_TOKEN") or ""


# ---------------------------------------------------------------------------
# リポジトリ設定の追加・更新
# ---------------------------------------------------------------------------

def _repo_key(host: str, project: str) -> str:
    return f"{host}|{project}"


def add_repo_to_config(
    repo_info: RepoConfig, config: DaemonConfig, token: str = ""
) -> bool:
    host = repo_info["host"]
    project = repo_info["project"]
    local_path = repo_info["local_path"]
    key = _repo_key(host, project)

    for repo in config.get("repos", []):
        if _repo_key(repo["host"], repo["project"]) == key:
            changed = repo.get("local_path") != local_path
            repo["local_path"] = local_path
            if token and repo.get("token") != token:
                repo["token"] = token
                changed = True
            return changed

    entry: RepoConfig = RepoConfig(host=host, project=project, local_path=local_path)
    if token:
        entry["token"] = token
    config.setdefault("repos", []).append(entry)
    return True


# ---------------------------------------------------------------------------
# スクリプト・テンプレートのコピー（インストール先への配置）
# ---------------------------------------------------------------------------

def copy_scripts_to_config_dir(*, dry_run: bool = False) -> None:
    """
    デーモン・セットアップスクリプトとテンプレートを設定ディレクトリにコピーする。
    スキルリポジトリの移動・削除後も動作を継続できるようにする。
    """
    config_dir = get_config_dir()
    src_dir = Path(__file__).parent       # scripts/
    tmpl_src = src_dir.parent / "templates"  # templates/ (skill repo root)

    # スクリプトのコピー
    for name in ("gl_common.py", "gl_poll_daemon.py", "gl_poll_setup.py"):
        src = src_dir / name
        dst = config_dir / name
        if not src.exists():
            print(f"  警告: {src} が見つかりません（スキルリポジトリ外実行の場合はスキップ）")
            continue
        if dry_run:
            print(f"  [DRYRUN] コピー予定: {src} → {dst}")
            continue
        config_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if platform.system() != "Windows":
            try:
                os.chmod(dst, 0o755)
            except OSError:
                pass
        print(f"  コピー: {src} → {dst}")

    # テンプレートディレクトリのコピー
    if tmpl_src.is_dir():
        tmpl_dst = config_dir / "templates"
        if dry_run:
            print(f"  [DRYRUN] テンプレートコピー予定: {tmpl_src} → {tmpl_dst}")
        else:
            tmpl_dst.mkdir(parents=True, exist_ok=True)
            for f in tmpl_src.glob("*.md"):
                shutil.copy2(f, tmpl_dst / f.name)
                print(f"  テンプレートコピー: {f.name}")
    else:
        print(f"  警告: テンプレートディレクトリ未発見: {tmpl_src}")


# ---------------------------------------------------------------------------
# OS 別サービスインストール
# ---------------------------------------------------------------------------

def _run_cmd(cmd: list[str], *, dry_run: bool = False, check: bool = True) -> bool:
    if dry_run:
        print(f"  [DRYRUN] 実行予定: {' '.join(str(c) for c in cmd)}")
        return True
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and check:
            print(f"  警告: {r.stderr.strip() or r.stdout.strip()}")
        return r.returncode == 0
    except Exception as e:
        print(f"  エラー: {e}")
        return False


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"com.{SERVICE_NAME}.plist"


def install_service_macos(
    python_exe: str, daemon_path: str, interval: int,
    cli: AgentCLI, token: str, *, mock_cli: bool = False, dry_run: bool = False
) -> bool:
    env_entries = f"        <key>GITLAB_IDD_CLI</key>\n        <string>{cli.name}</string>\n"
    if token:
        env_entries += f"        <key>GITLAB_TOKEN</key>\n        <string>{token}</string>\n"
    if mock_cli:
        env_entries += "        <key>GITLAB_IDD_MOCK_CLI</key>\n        <string>true</string>\n"

    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
            "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key><string>com.{SERVICE_NAME}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{python_exe}</string>
                <string>{daemon_path}</string>
                <string>--interval</string><string>{interval}</string>
            </array>
            <key>EnvironmentVariables</key>
            <dict>
        {env_entries}    </dict>
            <key>KeepAlive</key><true/>
            <key>RunAtLoad</key><true/>
            <key>StandardOutPath</key><string>{get_config_dir() / "daemon.log"}</string>
            <key>StandardErrorPath</key><string>{get_config_dir() / "daemon.err.log"}</string>
        </dict>
        </plist>
    """)

    plist_path = _plist_path()
    if dry_run:
        print(f"  [DRYRUN] plist 作成予定: {plist_path}")
        print(f"  [DRYRUN] launchctl load {plist_path}")
        return True

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist, encoding="utf-8")
    print(f"  plist 作成: {plist_path}")
    _run_cmd(["launchctl", "unload", str(plist_path)], check=False)
    ok = _run_cmd(["launchctl", "load", str(plist_path)])
    if ok:
        print("  macOS LaunchAgent 登録完了")
    return ok


def _systemd_service_path() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return cfg / "systemd" / "user" / f"{SERVICE_NAME}.service"


def install_service_linux(
    python_exe: str, daemon_path: str, interval: int,
    cli: AgentCLI, token: str, *, mock_cli: bool = False, dry_run: bool = False
) -> bool:
    env_lines = f"Environment=GITLAB_IDD_CLI={cli.name}\n"
    if token:
        env_lines += f"Environment=GITLAB_TOKEN={token}\n"
    if mock_cli:
        env_lines += "Environment=GITLAB_IDD_MOCK_CLI=true\n"

    svc = textwrap.dedent(f"""\
        [Unit]
        Description=GitLab Issue Polling Daemon (gitlab-idd)
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={python_exe} {daemon_path} --interval {interval}
        {env_lines.rstrip()}
        Restart=on-failure
        RestartSec=30
        StandardOutput=append:{get_config_dir() / "daemon.log"}
        StandardError=append:{get_config_dir() / "daemon.err.log"}

        [Install]
        WantedBy=default.target
    """)

    svc_path = _systemd_service_path()
    if dry_run:
        print(f"  [DRYRUN] service ファイル作成予定: {svc_path}")
        print(f"  [DRYRUN] systemctl --user enable --now {SERVICE_NAME}")
        return True

    svc_path.parent.mkdir(parents=True, exist_ok=True)
    svc_path.write_text(svc, encoding="utf-8")
    print(f"  service ファイル作成: {svc_path}")

    check = subprocess.run(
        ["systemctl", "--user", "daemon-reload"], capture_output=True, text=True
    )
    if check.returncode != 0:
        print("  systemd 未使用: crontab にフォールバック")
        return _install_cron_linux(python_exe, daemon_path, interval, dry_run=dry_run)

    _run_cmd(["systemctl", "--user", "enable", SERVICE_NAME])
    ok = _run_cmd(["systemctl", "--user", "start", SERVICE_NAME])
    if ok:
        print("  Linux systemd ユーザーサービス登録完了")
    return ok


def _install_cron_linux(
    python_exe: str, daemon_path: str, interval: int, *, dry_run: bool = False
) -> bool:
    cron_cmd = f"@reboot {python_exe} {daemon_path} --interval {interval}"
    if dry_run:
        print(f"  [DRYRUN] crontab 追加予定: {cron_cmd}")
        return True
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        if daemon_path in existing:
            print("  crontab: 既に登録済み")
            return True
        new_crontab = existing.rstrip() + "\n" + cron_cmd + "\n"
        r = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True)
        if r.returncode == 0:
            print("  crontab @reboot 登録完了")
            return True
        print(f"  crontab 登録失敗: {r.stderr.strip()}")
        return False
    except Exception as e:
        print(f"  crontab エラー: {e}")
        return False


def install_service_windows(
    python_exe: str, daemon_path: str, interval: int, *, dry_run: bool = False
) -> bool:
    cmd = [
        "schtasks", "/Create", "/F",
        "/TN", SERVICE_NAME,
        "/SC", "ONLOGON",
        "/TR", f'"{python_exe}" "{daemon_path}" --interval {interval}',
        "/RL", "HIGHEST",
    ]
    if not _run_cmd(cmd, dry_run=dry_run):
        return False
    if not dry_run:
        _run_cmd(["schtasks", "/Run", "/TN", SERVICE_NAME], check=False)
        print("  Windows タスクスケジューラ登録完了")
    return True


def install_service(
    python_exe: str, daemon_path: str, interval: int,
    cli: AgentCLI, token: str, *, mock_cli: bool = False, dry_run: bool = False
) -> bool:
    system = platform.system()
    print(f"\n[サービスインストール] OS={system}  CLI={'mock' if mock_cli else cli.name}")
    match system:
        case "Darwin":
            return install_service_macos(
                python_exe, daemon_path, interval, cli, token,
                mock_cli=mock_cli, dry_run=dry_run
            )
        case "Linux":
            return install_service_linux(
                python_exe, daemon_path, interval, cli, token,
                mock_cli=mock_cli, dry_run=dry_run
            )
        case "Windows":
            return install_service_windows(python_exe, daemon_path, interval, dry_run=dry_run)
        case _:
            print(f"  未対応 OS: {system} — サービス登録をスキップ")
            return False


# ---------------------------------------------------------------------------
# サービス削除
# ---------------------------------------------------------------------------

def uninstall_service(*, dry_run: bool = False) -> None:
    match platform.system():
        case "Darwin":
            plist = _plist_path()
            _run_cmd(["launchctl", "unload", str(plist)], dry_run=dry_run, check=False)
            if not dry_run and plist.exists():
                plist.unlink()
            print(f"macOS LaunchAgent 削除: {plist}")
        case "Linux":
            _run_cmd(["systemctl", "--user", "stop",    SERVICE_NAME], dry_run=dry_run, check=False)
            _run_cmd(["systemctl", "--user", "disable", SERVICE_NAME], dry_run=dry_run, check=False)
            svc = _systemd_service_path()
            if not dry_run and svc.exists():
                svc.unlink()
            _run_cmd(["systemctl", "--user", "daemon-reload"], dry_run=dry_run, check=False)
            print(f"Linux systemd サービス削除: {svc}")
        case "Windows":
            _run_cmd(["schtasks", "/Delete", "/F", "/TN", SERVICE_NAME], dry_run=dry_run, check=False)
            print(f"Windows タスク削除: {SERVICE_NAME}")


# ---------------------------------------------------------------------------
# サービス再起動
# ---------------------------------------------------------------------------

def restart_daemon(*, dry_run: bool = False) -> bool:
    """デーモンを再起動する。設定変更後に呼び出す。"""
    system = platform.system()
    print(f"デーモン再起動 ({system})...")
    match system:
        case "Darwin":
            plist = _plist_path()
            if not plist.exists():
                print("  plist が見つかりません。先に --install を実行してください。")
                return False
            _run_cmd(["launchctl", "stop",  f"com.{SERVICE_NAME}"], dry_run=dry_run, check=False)
            ok = _run_cmd(["launchctl", "start", f"com.{SERVICE_NAME}"], dry_run=dry_run)
            if ok:
                print("  macOS LaunchAgent 再起動完了")
            return ok
        case "Linux":
            svc = _systemd_service_path()
            if not svc.exists():
                print("  service ファイルが見つかりません。先に --install を実行してください。")
                return False
            ok = _run_cmd(["systemctl", "--user", "restart", SERVICE_NAME], dry_run=dry_run)
            if ok:
                print("  Linux systemd 再起動完了")
            return ok
        case "Windows":
            _run_cmd(["schtasks", "/End", "/TN", SERVICE_NAME], dry_run=dry_run, check=False)
            ok = _run_cmd(["schtasks", "/Run", "/TN", SERVICE_NAME], dry_run=dry_run)
            if ok:
                print("  Windows タスク再起動完了")
            return ok
        case _:
            print(f"  未対応 OS: {system}")
            return False


# ---------------------------------------------------------------------------
# 設定変更
# ---------------------------------------------------------------------------

_SETTABLE_KEYS = {
    "mock_cli":           "bool",
    "preferred_cli":      "str",
    "poll_interval":      "int",
    "poll_interval_seconds": "int",
}


def run_set_config(key_value: str, *, dry_run: bool = False) -> None:
    """
    KEY=VALUE 形式で config.json を変更し、デーモンを再起動する。
    例:
      --set mock_cli=true
      --set preferred_cli=claude
      --set poll_interval=60
    """
    if "=" not in key_value:
        print(f"ERROR: KEY=VALUE 形式で指定してください。例: --set mock_cli=true")
        print(f"変更可能なキー: {', '.join(_SETTABLE_KEYS)}")
        sys.exit(1)

    key, value = key_value.split("=", 1)
    key = key.strip()
    value = value.strip()

    if key not in _SETTABLE_KEYS:
        print(f"ERROR: 不明なキー: {key!r}")
        print(f"変更可能なキー: {', '.join(_SETTABLE_KEYS)}")
        sys.exit(1)

    config = load_config()
    vtype = _SETTABLE_KEYS[key]

    # 型変換 & 設定
    match key:
        case "mock_cli":
            parsed = value.lower() in ("true", "1", "yes", "on")
            config["mock_cli"] = parsed
            config_key = "mock_cli"
        case "preferred_cli":
            valid = {name for name, _, _ in _CLI_CANDIDATES}
            if value not in valid:
                print(f"ERROR: 不明な CLI: {value!r}  有効値: {', '.join(sorted(valid))}")
                sys.exit(1)
            config["preferred_cli"] = value
            config_key = "preferred_cli"
        case "poll_interval" | "poll_interval_seconds":
            try:
                secs = int(value)
            except ValueError:
                print(f"ERROR: 数値を指定してください: {value!r}")
                sys.exit(1)
            config["poll_interval_seconds"] = secs
            config_key = "poll_interval_seconds"
        case _:
            config_key = key

    save_config(config, dry_run=dry_run)
    print(f"{'[DRYRUN] ' if dry_run else ''}設定変更: {config_key} = {config.get(config_key)}")

    # mock_cli の状態をわかりやすく表示
    if config_key == "mock_cli":
        if config.get("mock_cli"):
            print(
                "  モック CLI モード ON: イシュー検出時にエージェント CLI は起動せず、\n"
                f"  プロンプトを {get_config_dir() / 'mock-prompts'} に保存します。"
            )
        else:
            print("  モック CLI モード OFF: 通常の CLI が使用されます。")

    # デーモン再起動
    restart_daemon(dry_run=dry_run)


# ---------------------------------------------------------------------------
# SessionStart フック設定
# ---------------------------------------------------------------------------

def configure_session_hook(
    setup_script_path: str, python_exe: str, *, dry_run: bool = False
) -> None:
    settings_path = CLAUDE_SETTINGS_PATH
    hook_command = f"{python_exe} {setup_script_path} --session-start"

    settings: dict = {}
    if settings_path.exists():
        try:
            with open(settings_path, encoding="utf-8") as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            settings = {}

    hooks = settings.setdefault("hooks", {})
    session_hooks: list = hooks.setdefault("SessionStart", [])

    for entry in session_hooks:
        for h in entry.get("hooks", []):
            if h.get("command") == hook_command:
                print("  SessionStart フック: 既に登録済み")
                return

    session_hooks.append({"hooks": [{"type": "command", "command": hook_command}]})

    if dry_run:
        print(f"  [DRYRUN] SessionStart フック追加予定: {settings_path}")
        print(f"  [DRYRUN] コマンド: {hook_command}")
        return

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    print(f"  SessionStart フック登録: {settings_path}")


# ---------------------------------------------------------------------------
# デーモン状態確認
# ---------------------------------------------------------------------------

def show_status() -> None:
    config = load_config()
    config_path = get_config_path()
    daemon_path = get_installed_daemon_path()
    clis = find_available_agent_clis()

    print("=" * 60)
    print("gitlab-idd ポーリングデーモン状態")
    print("=" * 60)
    print(f"設定ファイル   : {config_path} ({'存在' if config_path.exists() else '未作成'})")
    print(f"デーモンスクリプト: {daemon_path} ({'存在' if daemon_path.exists() else '未コピー'})")
    print(f"テンプレート   : {get_config_dir() / 'templates'}")
    print(f"ポーリング間隔  : {config.get('poll_interval_seconds', DEFAULT_POLL_INTERVAL)} 秒")

    mock_cli = config.get("mock_cli", False)
    print(f"モック CLI モード: {'ON ⚠ — CLI は起動せずプロンプトをファイルに保存' if mock_cli else 'OFF'}")
    if mock_cli:
        print(f"  mock-prompts: {get_config_dir() / 'mock-prompts'}")

    print(f"\n利用可能な CLI ({len(clis)} 件):")
    preferred = config.get("preferred_cli")
    for cli in clis:
        tag = " ← 優先" if cli.name == preferred else ""
        wsl_tag = " (WSL2経由)" if cli.via_wsl else ""
        print(f"  ✓ {cli.name:<10} {cli.binary}{wsl_tag}{tag}")
    if not clis:
        print("  ✗ なし — claude/codex/kiro-cli/q のいずれかをインストールしてください")
        if mock_cli:
            print("    ※ モック CLI モード ON のため動作は継続中")

    print(f"\nポーリング対象リポジトリ ({len(config.get('repos', []))} 件):")
    for repo in config.get("repos", []):
        print(f"  - {repo['host']}/{repo['project']}")
        print(f"    path: {repo.get('local_path', '?')}")

    system = platform.system()
    print(f"\nOS サービス ({system}):")
    match system:
        case "Darwin":
            r = subprocess.run(
                ["launchctl", "list", f"com.{SERVICE_NAME}"],
                capture_output=True, text=True,
            )
            print("  " + (r.stdout.strip() or r.stderr.strip() or "未登録"))
        case "Linux":
            r = subprocess.run(
                ["systemctl", "--user", "status", SERVICE_NAME, "--no-pager"],
                capture_output=True, text=True,
            )
            for line in r.stdout.splitlines()[:8]:
                print("  " + line)
            if not r.stdout.strip():
                print("  未登録または systemd 未使用")
        case "Windows":
            r = subprocess.run(
                ["schtasks", "/Query", "/TN", SERVICE_NAME, "/FO", "LIST"],
                capture_output=True, text=True,
            )
            print("  " + (r.stdout.strip() or "未登録"))

    # --set のヒント
    print("\n設定変更のヒント:")
    print("  モック CLI に切り替え : python gl_poll_setup.py --set mock_cli=true")
    print("  通常 CLI に戻す       : python gl_poll_setup.py --set mock_cli=false")
    print("  再起動               : python gl_poll_setup.py --restart")


# ---------------------------------------------------------------------------
# セッション開始モード（非対話）
# ---------------------------------------------------------------------------

def run_session_start() -> None:
    repo_info = get_current_repo_info()
    if repo_info is None:
        return

    config = load_config()
    token = get_gitlab_token()
    changed = add_repo_to_config(repo_info, config, token)
    if changed:
        save_config(config)
        print(f"[gitlab-idd] ポーリング対象に追加: {repo_info['host']}/{repo_info['project']}")

    _ensure_daemon_running()


def _ensure_daemon_running() -> None:
    if not get_installed_daemon_path().exists():
        return
    try:
        match platform.system():
            case "Darwin":
                if _plist_path().exists():
                    subprocess.run(
                        ["launchctl", "start", f"com.{SERVICE_NAME}"], capture_output=True
                    )
            case "Linux":
                if _systemd_service_path().exists():
                    subprocess.run(
                        ["systemctl", "--user", "start", SERVICE_NAME], capture_output=True
                    )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# リポジトリ追加のみ
# ---------------------------------------------------------------------------

def run_add_repo(*, dry_run: bool = False) -> None:
    repo_info = get_current_repo_info()
    if repo_info is None:
        print("ERROR: git リポジトリではないか remote origin がありません。")
        sys.exit(1)

    config = load_config()
    token = get_gitlab_token()
    changed = add_repo_to_config(repo_info, config, token)
    host = repo_info["host"]
    project = repo_info["project"]

    if changed:
        save_config(config, dry_run=dry_run)
        print(f"{'[DRYRUN] ' if dry_run else ''}追加: {host}/{project}")
        print(f"  ローカルパス: {repo_info['local_path']}")
    else:
        print(f"既に登録済み: {host}/{project}")


# ---------------------------------------------------------------------------
# 対話的インストール
# ---------------------------------------------------------------------------

def run_install(*, dry_run: bool = False, allow_mock_cli: bool = False) -> None:
    """
    対話的インストール。
    LLM がユーザーの同意を得てから呼び出す（SKILL.md 参照）。
    CLI が見つからない場合 --allow-mock-cli を付けるとモックCLIでインストール可能。
    """
    tag = "[DRYRUN] " if dry_run else ""
    print("=" * 60)
    print(f"{tag}gitlab-idd ポーリングデーモン インストーラー")
    print("=" * 60)

    # 1. エージェント CLI チェック
    print(f"\n[1/5] エージェント CLI の確認...")
    clis = find_available_agent_clis()
    use_mock_cli = False

    if clis:
        for cli in clis:
            wsl_tag = " (WSL2経由)" if cli.via_wsl else ""
            print(f"  ✓ {cli.name}{wsl_tag}")
        best_cli = clis[0]
        print(f"  使用 CLI: {best_cli.name}")
    else:
        if allow_mock_cli:
            print("  ⚠ エージェント CLI が見つかりません。モック CLI モードでインストールします。")
            print(f"  プロンプトは {get_config_dir() / 'mock-prompts'} に保存されます。")
            print("  CLI インストール後: python gl_poll_setup.py --set mock_cli=false --restart")
            use_mock_cli = True
            # ダミー CLI（状態確認用）
            best_cli = AgentCLI("mock", "", [])
        else:
            print(
                "ERROR: 対応エージェント CLI が見つかりません。\n"
                "以下のいずれかをインストールしてください:\n"
                "  claude  : npm install -g @anthropic-ai/claude-code\n"
                "  codex   : npm install -g @openai/codex\n"
                "  kiro    : インストール後 kiro-cli --version を確認\n"
                "  amazonq : q --version を確認\n\n"
                "CLI なしでモックCLIモードでインストールする場合:\n"
                "  python gl_poll_setup.py --install --allow-mock-cli"
            )
            sys.exit(1)

    # 2. カレントリポジトリ確認
    print(f"\n[2/5] カレントリポジトリの確認...")
    repo_info = get_current_repo_info()
    if repo_info is None:
        print("  警告: git リポジトリではないか remote origin がありません。")
        print("  後で --add-repo で追加できます。")
    else:
        print(f"  リポジトリ: {repo_info['host']}/{repo_info['project']}")
        print(f"  ローカルパス: {repo_info['local_path']}")

    # 3. スクリプト・テンプレートをコピー
    print(f"\n[3/5] スクリプト・テンプレートをコピー...")
    copy_scripts_to_config_dir(dry_run=dry_run)

    # 4. 設定ファイル更新
    print(f"\n[4/5] 設定ファイルを更新...")
    config = load_config()
    token = get_gitlab_token()
    if not token:
        print("  警告: GITLAB_TOKEN 未設定。後で環境変数を設定してください。")
    if repo_info:
        add_repo_to_config(repo_info, config, token)
    if not use_mock_cli:
        config["preferred_cli"] = best_cli.name
    config["mock_cli"] = use_mock_cli
    save_config(config, dry_run=dry_run)
    print(f"  {tag}設定保存: {get_config_path()}")

    # 5. OS サービス登録
    python_exe = sys.executable
    daemon_path = str(get_installed_daemon_path())
    interval = config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
    ok = install_service(
        python_exe, daemon_path, interval, best_cli, token,
        mock_cli=use_mock_cli, dry_run=dry_run,
    )

    # SessionStart フック設定
    setup_path = str(get_installed_setup_path())
    configure_session_hook(setup_path, python_exe, dry_run=dry_run)

    print("\n" + "=" * 60)
    if ok:
        print(f"{tag}インストール完了!")
        print(f"  {tag}デーモンログ  : {get_config_dir() / 'daemon.log'}")
        print(f"  {tag}設定ファイル  : {get_config_path()}")
        print(f"  {tag}CLI モード    : {'モック (プロンプト保存のみ)' if use_mock_cli else best_cli.name}")
        if dry_run:
            print("\n  ※ DRYRUN: 実際には何も変更されていません")
        if use_mock_cli:
            print(f"\n  次のステップ:")
            print(f"    1. CLI をインストール")
            print(f"    2. python gl_poll_setup.py --set mock_cli=false")
    else:
        print(f"{tag}インストールに一部問題が発生しました。")
    print("=" * 60)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "gitlab-idd polling daemon setup (Python 3.11+, stdlib only)\n"
            "スキルリポジトリ内外どちらからでも実行できます。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install",       action="store_true", help="対話的インストール（デフォルト）")
    group.add_argument("--session-start", action="store_true", help="SessionStart フックモード（非対話）")
    group.add_argument("--add-repo",      action="store_true", help="カレントリポジトリを追加")
    group.add_argument("--restart",       action="store_true", help="デーモンを再起動")
    group.add_argument("--set",           metavar="KEY=VALUE",  help="設定変更してデーモンを再起動")
    group.add_argument("--uninstall",     action="store_true", help="デーモンサービスを削除")
    group.add_argument("--status",        action="store_true", help="デーモン状態を表示")

    parser.add_argument("--dry-run",       action="store_true", help="副作用なしでシミュレート（install/set/add-repo で有効）")
    parser.add_argument("--allow-mock-cli", action="store_true", help="CLI 未インストールでもモック CLI でインストール（--install と併用）")

    args = parser.parse_args()

    match True:
        case _ if args.session_start:
            run_session_start()
        case _ if args.add_repo:
            run_add_repo(dry_run=args.dry_run)
        case _ if args.restart:
            restart_daemon(dry_run=args.dry_run)
        case _ if args.set is not None:
            run_set_config(args.set, dry_run=args.dry_run)
        case _ if args.uninstall:
            uninstall_service(dry_run=args.dry_run)
            if not args.dry_run:
                print("サービスを削除しました。設定ファイルは保持されています。")
                print(f"完全削除: rm -rf {get_config_dir()}")
        case _ if args.status:
            show_status()
        case _:
            run_install(dry_run=args.dry_run, allow_mock_cli=args.allow_mock_cli)


if __name__ == "__main__":
    main()
