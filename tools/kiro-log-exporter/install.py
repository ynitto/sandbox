#!/usr/bin/env python3
"""
kiro-log-exporter インストーラー

kiro_log_exporter.py を指定フォルダへコピーし、実行可能な状態にセットアップします。
Linux / WSL / Windows に対応しています。

使い方:
  python install.py
  python install.py --prefix ~/.local/bin
  python install.py --prefix /usr/local/bin
  python install.py --install-dir "C:\\tools\\kiro-log-exporter"   # Windows
"""

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

MIN_PYTHON = (3, 9)
SCRIPT_NAME = "kiro_log_exporter.py"
COMMAND_NAME = "kiro-log-exporter"
DEFAULT_PREFIX_UNIX = Path.home() / ".local" / "bin"
DEFAULT_INSTALL_DIR_WINDOWS = Path(os.environ.get("USERPROFILE", r"C:\Users\Public")) / ".local" / "bin"


# ---------------------------------------------------------------------------
# OS 検出
# ---------------------------------------------------------------------------

def _is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
    except OSError:
        return False


def _is_windows() -> bool:
    return sys.platform == "win32"


# ---------------------------------------------------------------------------
# カラー出力
# ---------------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty() and not _is_windows()
_C = {
    "cyan":   "\033[0;36m",
    "green":  "\033[0;32m",
    "yellow": "\033[1;33m",
    "red":    "\033[0;31m",
    "reset":  "\033[0m",
}


def _c(color: str, text: str) -> str:
    return f"{_C[color]}{text}{_C['reset']}" if _USE_COLOR else text


def info(msg: str) -> None:
    print(f"{_c('cyan', '[INFO]')}  {msg}")


def ok(msg: str) -> None:
    print(f"{_c('green', '[OK]')}    {msg}")


def warn(msg: str) -> None:
    print(f"{_c('yellow', '[WARN]')}  {msg}")


def die(msg: str) -> None:
    print(f"{_c('red', '[ERROR]')} {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# チェック処理
# ---------------------------------------------------------------------------

def check_python() -> None:
    """Python バージョンが要件を満たすか確認する。"""
    info("Python バージョンを確認しています...")
    ver = sys.version_info[:2]
    if ver < MIN_PYTHON:
        die(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 以上が必要です。"
            f"現在: {ver[0]}.{ver[1]}\n"
            "  インストール先: https://www.python.org/downloads/"
        )
    ok(f"Python {ver[0]}.{ver[1]} を検出しました: {sys.executable}")


def check_source(script_dir: Path) -> Path:
    """インストール元スクリプトが存在するか確認する。"""
    src = script_dir / SCRIPT_NAME
    if not src.exists():
        die(f"{SCRIPT_NAME} が見つかりません: {src}")
    return src


# ---------------------------------------------------------------------------
# インストール処理
# ---------------------------------------------------------------------------

def _fix_shebang(dst: Path) -> None:
    """shebang を現在の Python 実行パスに書き換える。"""
    python_path = sys.executable
    try:
        text = dst.read_text(encoding="utf-8")
        new_text = re.sub(r"^#!.*", f"#!/usr/bin/env python3", text, count=1)
        dst.write_text(new_text, encoding="utf-8")
    except OSError:
        pass


def install_unix(src: Path, prefix: Path) -> Path:
    """Linux / WSL 用: コマンドとしてインストールする。"""
    prefix.mkdir(parents=True, exist_ok=True)
    dst = prefix / COMMAND_NAME

    shutil.copy2(src, dst)

    # 実行権限を付与
    mode = dst.stat().st_mode
    dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # shebang を修正
    _fix_shebang(dst)

    ok(f"インストールしました: {dst}")
    return dst


def install_windows(src: Path, install_dir: Path) -> Path:
    """Windows 用: .py ファイルとしてコピーする。"""
    install_dir.mkdir(parents=True, exist_ok=True)
    dst = install_dir / SCRIPT_NAME

    shutil.copy2(src, dst)
    ok(f"インストールしました: {dst}")
    return dst


# ---------------------------------------------------------------------------
# PATH チェック
# ---------------------------------------------------------------------------

def check_path_unix(prefix: Path) -> None:
    """PATH に prefix が含まれているか確認する。"""
    info("PATH を確認しています...")
    paths = os.environ.get("PATH", "").split(":")
    if str(prefix) in paths:
        ok(f"{prefix} は PATH に含まれています。")
    else:
        warn(f"{prefix} が PATH に含まれていません。")
        shell_rc = "~/.bashrc"
        if os.environ.get("SHELL", "").endswith("zsh"):
            shell_rc = "~/.zshrc"
        print()
        print("  以下を " + shell_rc + " に追加してください:")
        print()
        print(f'    export PATH="$HOME/.local/bin:$PATH"')
        print()


# ---------------------------------------------------------------------------
# 完了メッセージ
# ---------------------------------------------------------------------------

def _print_done_unix(dst: Path) -> None:
    print()
    print("=" * 55)
    ok("インストール完了！")
    print("=" * 55)
    print()
    print("  使い方:")
    print(f"    {COMMAND_NAME} ~/kiro-logs")
    print(f"    {COMMAND_NAME} ~/kiro-logs --source cli")
    print(f"    {COMMAND_NAME} ~/kiro-logs --source ide")
    print(f"    {COMMAND_NAME} ~/kiro-logs -v")
    print()
    print("  定期実行（cron 設定例）:")
    print("    crontab -e  で以下を追加")
    print(f"    */15 * * * *  {dst} ~/kiro-logs")
    print()


def _print_done_windows(dst: Path) -> None:
    python = sys.executable
    print()
    print("=" * 55)
    print("[OK]    インストール完了！")
    print("=" * 55)
    print()
    print("  使い方:")
    print(f"    python \"{dst}\" C:\\kiro-logs")
    print(f"    python \"{dst}\" C:\\kiro-logs --source cli")
    print(f"    python \"{dst}\" C:\\kiro-logs --source ide")
    print(f"    python \"{dst}\" C:\\kiro-logs -v")
    print()
    print("  定期実行（タスクスケジューラ登録例）:")
    task_cmd = (
        f'Register-ScheduledTask -TaskName "KiroLogExporter" '
        f'-Action (New-ScheduledTaskAction -Execute "{python}" '
        f'-Argument "\\"{dst}\\" C:\\kiro-logs") '
        f'-Trigger (New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) -Once -At 00:00) '
        f'-RunLevel Highest -Force'
    )
    print("    PowerShell（管理者）で以下を実行:")
    print(f"    {task_cmd}")
    print()


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    if _is_windows():
        default_dir = str(DEFAULT_INSTALL_DIR_WINDOWS)
        dir_help = f"インストール先フォルダ (デフォルト: {default_dir})"
    else:
        default_dir = str(DEFAULT_PREFIX_UNIX)
        dir_help = f"インストール先ディレクトリ (デフォルト: {default_dir})"

    p = argparse.ArgumentParser(
        prog="install.py",
        description="kiro-log-exporter インストーラー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使い方:
  python install.py                           # デフォルト設定でインストール
  python install.py --prefix ~/.local/bin     # Linux/WSL: インストール先を指定
  python install.py --install-dir C:\\tools   # Windows: インストール先を指定
""".strip(),
    )

    if _is_windows():
        p.add_argument(
            "--install-dir",
            default=default_dir,
            metavar="DIR",
            help=dir_help,
        )
    else:
        p.add_argument(
            "--prefix",
            default=default_dir,
            metavar="DIR",
            help=dir_help,
        )

    return p


def main() -> None:
    args = _build_parser().parse_args()
    script_dir = Path(__file__).parent.resolve()

    env_label = "WSL" if _is_wsl() else ("Windows" if _is_windows() else platform.system())

    print()
    print("=" * 55)
    print("  kiro-log-exporter インストーラー")
    print("=" * 55)
    print(f"  実行環境: {env_label} / Python {sys.version.split()[0]}")
    print()

    # 1. Python バージョン確認
    check_python()

    # 2. ソーススクリプト確認
    src = check_source(script_dir)
    info(f"ソース: {src}")

    print()

    # 3. インストール
    if _is_windows():
        install_dir = Path(args.install_dir).expanduser().resolve()
        info(f"インストール先: {install_dir}")
        dst = install_windows(src, install_dir)
        _print_done_windows(dst)
    else:
        prefix = Path(args.prefix).expanduser().resolve()
        info(f"インストール先: {prefix}")
        dst = install_unix(src, prefix)
        check_path_unix(prefix)
        _print_done_unix(dst)


if __name__ == "__main__":
    main()
