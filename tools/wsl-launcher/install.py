#!/usr/bin/env python3
"""
WSL Terminal Launcher インストーラー

Send.ps1 と config.json を指定フォルダにコピーし、
PC ログイン時に Send.ps1 が自動実行されるようタスクスケジューラに登録します。

使用方法:
    python install.py
    python install.py --install-dir "C:\\tools\\wsl-launcher"
    python install.py --delay 30 --task-name "WslTerminalLauncher"
"""

import argparse
import os
import shutil
import subprocess
import sys

DEFAULT_INSTALL_DIR = r"C:\tools\wsl-launcher"
DEFAULT_TASK_NAME = "WslTerminalLauncher"
DEFAULT_DELAY_SECONDS = 30
DEFAULT_EXECUTION_LIMIT_MINUTES = 5


def copy_files(script_dir: str, install_dir: str) -> tuple[str, str]:
    """Send.ps1、config.json をインストール先にコピーする。"""
    os.makedirs(install_dir, exist_ok=True)

    # Send.ps1 をコピー
    src_send = os.path.join(script_dir, "Send.ps1")
    dst_send = os.path.join(install_dir, "Send.ps1")
    if not os.path.exists(src_send):
        print(f"[エラー] Send.ps1 が見つかりません: {src_send}", file=sys.stderr)
        sys.exit(1)
    shutil.copy2(src_send, dst_send)
    print(f"[コピー] {src_send}")
    print(f"     -> {dst_send}")

    # config.json をコピー (既存は上書きしない)
    src_config = os.path.join(script_dir, "config.json")
    dst_config = os.path.join(install_dir, "config.json")
    if not os.path.exists(src_config):
        print(f"[エラー] config.json が見つかりません: {src_config}", file=sys.stderr)
        sys.exit(1)
    if os.path.exists(dst_config):
        print(f"[スキップ] config.json は既に存在するため上書きしません: {dst_config}")
    else:
        shutil.copy2(src_config, dst_config)
        print(f"[コピー] {src_config}")
        print(f"     -> {dst_config}")

    return dst_send, dst_config


def register_task(
    launcher_path: str,
    config_path: str,
    working_dir: str,
    task_name: str,
    delay_seconds: int,
    execution_limit_minutes: int = DEFAULT_EXECUTION_LIMIT_MINUTES,
) -> None:
    """タスクスケジューラに Send.ps1 をログイン時自動起動として登録する。"""

    ps_args = (
        f"-NonInteractive -NoProfile -ExecutionPolicy Bypass "
        f"-WindowStyle Hidden -File \"{launcher_path}\" -ConfigPath \"{config_path}\""
    )

    # PowerShell スクリプトを組み立て
    ps_script = f"""
$ErrorActionPreference = 'Stop'

# 既存タスクがあれば削除して再登録
$existing = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue
if ($existing) {{
    Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false
    Write-Host "[削除] 既存タスク '{task_name}' を削除しました。"
}}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument '{ps_args}' `
    -WorkingDirectory '{working_dir}'

# ログオン時トリガー + 遅延
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = 'PT{delay_seconds}S'

# 現在のユーザーで高特権 (RunLevel Highest) 実行 / ログオン状態に関わらず実行
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType S4U `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes {execution_limit_minutes}) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName '{task_name}' `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'WSL ターミナルランチャー: ログイン時に複数の WSL ターミナルを起動します。' `
    | Out-Null

Write-Host "[登録] タスク '{task_name}' を登録しました。"
Write-Host "[情報] 次回ログイン時 ({delay_seconds} 秒後) に自動起動します。"
Write-Host "[情報] 手動テスト: Start-ScheduledTask -TaskName '{task_name}'"
"""

    result = subprocess.run(
        [
            "powershell.exe",
            "-NonInteractive",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_script,
        ],
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    if result.returncode != 0:
        print("[エラー] タスクスケジューラへの登録に失敗しました。", file=sys.stderr)
        print("[ヒント] 管理者権限で実行してください。", file=sys.stderr)
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WSL Terminal Launcher インストーラー",
        epilog=(
            "使用例:\n"
            "  python install.py\n"
            "  python install.py --install-dir \"C:\\\\tools\\\\wsl-launcher\"\n"
            "  python install.py --delay 30 --task-name \"WslTerminalLauncher\"\n"
            "  python install.py --execution-limit 10"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--install-dir",
        default=DEFAULT_INSTALL_DIR,
        metavar="DIR",
        help=f"インストール先フォルダ (デフォルト: {DEFAULT_INSTALL_DIR})",
    )
    parser.add_argument(
        "--task-name",
        default=DEFAULT_TASK_NAME,
        metavar="NAME",
        help=f"タスクスケジューラのタスク名 (デフォルト: {DEFAULT_TASK_NAME})",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=DEFAULT_DELAY_SECONDS,
        metavar="SEC",
        help=f"ログイン後の起動遅延秒数 (デフォルト: {DEFAULT_DELAY_SECONDS})",
    )
    parser.add_argument(
        "--execution-limit",
        type=int,
        default=DEFAULT_EXECUTION_LIMIT_MINUTES,
        metavar="MIN",
        help=f"タスクの最大実行時間（分）(デフォルト: {DEFAULT_EXECUTION_LIMIT_MINUTES})",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    install_dir = args.install_dir

    print("=" * 55)
    print("  WSL Terminal Launcher インストーラー")
    print("=" * 55)
    print(f"インストール先 : {install_dir}")
    print(f"タスク名       : {args.task_name}")
    print(f"起動遅延       : {args.delay} 秒")
    print(f"実行時間制限   : {args.execution_limit} 分")
    print()

    # ファイルのコピー
    launcher_path, config_path = copy_files(script_dir, install_dir)
    print()

    # タスクスケジューラへの登録
    register_task(launcher_path, config_path, install_dir, args.task_name, args.delay, args.execution_limit)
    print()
    print("インストール完了。")


if __name__ == "__main__":
    main()
