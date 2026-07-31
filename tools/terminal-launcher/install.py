#!/usr/bin/env python3
"""
Multi Terminal Launcher インストーラー

Send.ps1 と config.json を指定フォルダにコピーし、
PC ログイン時に Send.ps1 が自動実行されるようタスクスケジューラに登録します。

使用方法:
    python install.py
    python install.py --install-dir "C:\\tools\\terminal-launcher"
    python install.py --install-dir "C:¥tools¥terminal-launcher"
    python install.py --delay 30 --task-name "TerminalLauncher"
    python install.py --force
"""

import argparse
import os
import shutil
import subprocess
import sys

DEFAULT_INSTALL_DIR = r"C:\tools\terminal-launcher"
DEFAULT_TASK_NAME = "TerminalLauncher"
DEFAULT_DELAY_SECONDS = 30


def normalize_windows_path(path: str) -> str:
    """日本語キーボード由来の ¥/￥ を Windows 区切り文字として解釈する。"""
    # U+00A5(¥) と U+FFE5(￥) の両方を許容する。
    normalized = path.replace("¥", "\\").replace("￥", "\\")
    return os.path.expandvars(os.path.expanduser(normalized))


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
    task_name: str,
    delay_seconds: int,
    force: bool = False,
) -> None:
    """タスクスケジューラに Send.ps1 をスタートアップ時自動起動として登録する。"""

    # 既存タスク確認
    check = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        capture_output=True,
        text=True,
    )
    task_exists = check.returncode == 0

    if task_exists:
        if not force:
            print(f"[スキップ] タスク '{task_name}' は既に存在します。再作成するには --force を指定してください。")
            return
        del_result = subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True,
            text=True,
        )
        if del_result.returncode != 0:
            if del_result.stderr:
                print(del_result.stderr.rstrip(), file=sys.stderr)
            print("[エラー] 既存タスクの削除に失敗しました。", file=sys.stderr)
            sys.exit(del_result.returncode)
        print(f"[削除] 既存タスク '{task_name}' を削除しました。")

    # 遅延を HH:MM:SS 形式に変換
    delay_h = delay_seconds // 3600
    delay_m = (delay_seconds % 3600) // 60
    delay_s = delay_seconds % 60
    delay_str = f"{delay_h:02d}:{delay_m:02d}:{delay_s:02d}"

    tr_command = (
        f"powershell.exe -NonInteractive -NoProfile -ExecutionPolicy Bypass "
        f"-WindowStyle Hidden -File \"{launcher_path}\" -ConfigPath \"{config_path}\""
    )

    create_result = subprocess.run(
        [
            "schtasks", "/Create",
            "/TN", task_name,
            "/TR", tr_command,
            "/SC", "ONSTART",
            "/DELAY", delay_str,
            "/RU", "SYSTEM",
            "/RL", "HIGHEST",
            "/F",
        ],
        capture_output=True,
        text=True,
    )

    if create_result.stdout:
        print(create_result.stdout.rstrip())
    if create_result.stderr:
        print(create_result.stderr.rstrip(), file=sys.stderr)

    if create_result.returncode != 0:
        print("[エラー] タスクスケジューラへの登録に失敗しました。", file=sys.stderr)
        print("[ヒント] 管理者権限で実行してください。", file=sys.stderr)
        sys.exit(create_result.returncode)

    print(f"[登録] タスク '{task_name}' を登録しました。")
    print(f"[情報] 次回スタートアップ時 ({delay_seconds} 秒後) に自動起動します。")
    print(f"[情報] 手動テスト: schtasks /Run /TN \"{task_name}\"")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Terminal Launcher インストーラー",
        epilog=(
            "使用例:\n"
            "  python install.py\n"
            "  python install.py --install-dir \"C:\\\\tools\\\\terminal-launcher\"\n"
            "  python install.py --install-dir \"C:¥tools¥terminal-launcher\"\n"
            "  python install.py --delay 30 --task-name \"TerminalLauncher\"\n"
            "  python install.py --force"
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
        help=f"スタートアップ後の起動遅延秒数 (デフォルト: {DEFAULT_DELAY_SECONDS})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既存タスクを削除して強制的に再作成する",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    install_dir = normalize_windows_path(args.install_dir)

    print("=" * 55)
    print("  Terminal Launcher インストーラー")
    print("=" * 55)
    print(f"インストール先 : {install_dir}")
    print(f"タスク名       : {args.task_name}")
    print(f"起動遅延       : {args.delay} 秒")
    print(f"強制再作成     : {'有効' if args.force else '無効'}")
    print()

    # ファイルのコピー
    launcher_path, config_path = copy_files(script_dir, install_dir)
    print()

    # タスクスケジューラへの登録
    register_task(launcher_path, config_path, args.task_name, args.delay, args.force)
    print()
    print("インストール完了。")


if __name__ == "__main__":
    main()
