#!/usr/bin/env python3
"""
winauto インストーラー

winauto CLI と依存ライブラリを現在の環境にセットアップします。
Windows native・WSL・macOS/Linux に対応。

使い方:
  python install.py                          # 自動検出でインストール
  python install.py --install-dir C:\\tools  # Windows: インストール先指定
  python install.py --prefix ~/.local/bin    # Linux/WSL: インストール先指定
  python install.py --dry-run               # 実行内容の確認のみ
  python install.py --skip-deps             # 依存ライブラリのインストールをスキップ
"""

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

MIN_PYTHON = (3, 9)
TOOL_NAME = "winauto"
SCRIPT_NAME = "winauto.py"

DEFAULT_PREFIX_UNIX = Path.home() / ".local" / "bin"
DEFAULT_INSTALL_DIR_WINDOWS = (
    Path(os.environ.get("USERPROFILE", r"C:\Users\Public")) / ".local" / "bin"
)

WINDOWS_DEPS = [
    "pywinauto>=0.6.9",
    "Pillow>=9.0.0",
    "comtypes>=1.4.0",
    "pywin32>=306",
]


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


def _env_label() -> str:
    if _is_wsl():
        return "WSL"
    if _is_windows():
        return "Windows"
    return platform.system()


# ---------------------------------------------------------------------------
# カラー出力
# ---------------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty() and not _is_windows()
_COLORS = {
    "cyan":   "\033[0;36m",
    "green":  "\033[0;32m",
    "yellow": "\033[1;33m",
    "red":    "\033[0;31m",
    "reset":  "\033[0m",
}


def _colored(color: str, text: str) -> str:
    return f"{_COLORS[color]}{text}{_COLORS['reset']}" if _USE_COLOR else text


def info(msg: str) -> None:
    print(f"{_colored('cyan', '[INFO]')}  {msg}")


def ok(msg: str) -> None:
    print(f"{_colored('green', '[OK]')}    {msg}")


def warn(msg: str) -> None:
    print(f"{_colored('yellow', '[WARN]')}  {msg}")


def die(msg: str) -> None:
    print(f"{_colored('red', '[ERROR]')} {msg}", file=sys.stderr)
    sys.exit(1)


def step(msg: str) -> None:
    print(f"\n{_colored('cyan', '>>>')} {msg}")


# ---------------------------------------------------------------------------
# チェック
# ---------------------------------------------------------------------------

def check_python() -> None:
    ver = sys.version_info[:2]
    if ver < MIN_PYTHON:
        die(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 以上が必要です（現在: {ver[0]}.{ver[1]}）\n"
            "  ダウンロード: https://www.python.org/downloads/"
        )
    ok(f"Python {ver[0]}.{ver[1]}: {sys.executable}")


def check_source(script_dir: Path) -> Path:
    src = script_dir / SCRIPT_NAME
    if not src.exists():
        die(f"{SCRIPT_NAME} が見つかりません: {src}")
    return src


# ---------------------------------------------------------------------------
# Windows Python の検出（WSL から利用）
# ---------------------------------------------------------------------------

def find_windows_python() -> str | None:
    """WSL から Windows 側の python.exe を探す。"""
    # 1. where.exe で検索
    try:
        result = subprocess.run(
            ["cmd.exe", "/c", "where", "python"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and "python.exe" in line.lower() and "WindowsApps" not in line:
                return line
    except Exception:
        pass

    # 2. 典型的なインストールパスを探す
    candidates = [
        r"C:\Python312\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Python310\python.exe",
        r"C:\Python39\python.exe",
    ]
    # USERPROFILE 配下の Python も探す
    userprofile = os.environ.get("USERPROFILE", "")
    if not userprofile:
        try:
            result = subprocess.run(
                ["cmd.exe", "/c", "echo", "%USERPROFILE%"],
                capture_output=True, text=True, timeout=3
            )
            userprofile = result.stdout.strip()
        except Exception:
            pass

    if userprofile:
        for ver in ["312", "311", "310", "39"]:
            candidates.append(
                rf"{userprofile}\AppData\Local\Programs\Python\Python{ver}\python.exe"
            )

    for path in candidates:
        try:
            wsl_path = subprocess.run(
                ["wslpath", path], capture_output=True, text=True
            ).stdout.strip()
            if Path(wsl_path).exists():
                return path
        except Exception:
            pass

    return None


def wslpath_to_windows(unix_path: str) -> str:
    """WSL パスを Windows パスに変換する。"""
    result = subprocess.run(
        ["wslpath", "-w", unix_path],
        capture_output=True, text=True
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 依存ライブラリのインストール
# ---------------------------------------------------------------------------

def install_deps_windows(dry_run: bool) -> None:
    """Windows 環境に pywinauto 依存をインストールする。"""
    step("依存ライブラリをインストールしています...")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + WINDOWS_DEPS
    info(f"実行: {' '.join(cmd)}")
    if not dry_run:
        result = subprocess.run(cmd)
        if result.returncode != 0:
            die("pip install が失敗しました。")
        ok("依存ライブラリのインストール完了")
    else:
        info("[DRY-RUN] スキップ")


def install_deps_wsl(win_python: str, dry_run: bool) -> None:
    """WSL から Windows 側 pip で pywinauto 依存をインストールする。"""
    step("Windows 側 Python に依存ライブラリをインストールしています...")
    deps_str = " ".join(WINDOWS_DEPS)
    cmd = ["cmd.exe", "/c", win_python, "-m", "pip", "install", "--upgrade"] + WINDOWS_DEPS
    info(f"Windows Python: {win_python}")
    info(f"実行: {' '.join(cmd)}")
    if not dry_run:
        result = subprocess.run(cmd)
        if result.returncode != 0:
            warn("pip install が完全には成功しませんでした。手動でインストールしてください:")
            warn(f"  Windows PowerShell: pip install {deps_str}")
        else:
            ok("依存ライブラリのインストール完了")
    else:
        info("[DRY-RUN] スキップ")


# ---------------------------------------------------------------------------
# Windows ネイティブインストール
# ---------------------------------------------------------------------------

def install_windows(src: Path, install_dir: Path, dry_run: bool) -> Path:
    """Windows 用: winauto.py をコピーして .bat ラッパーを作成する。"""
    step(f"winauto をインストールしています: {install_dir}")

    if not dry_run:
        install_dir.mkdir(parents=True, exist_ok=True)

    # winauto.py をコピー
    dst_py = install_dir / SCRIPT_NAME
    info(f"コピー: {src} → {dst_py}")
    if not dry_run:
        shutil.copy2(src, dst_py)
        ok(f"winauto.py をコピーしました")

    # winauto.bat ラッパーを作成
    dst_bat = install_dir / f"{TOOL_NAME}.bat"
    bat_content = f'@echo off\n"{sys.executable}" "{dst_py}" %*\n'
    info(f"作成: {dst_bat}")
    if not dry_run:
        dst_bat.write_text(bat_content, encoding="utf-8")
        ok(f"winauto.bat を作成しました")

    return dst_bat


# ---------------------------------------------------------------------------
# WSL インストール
# ---------------------------------------------------------------------------

WSL_WRAPPER_TEMPLATE = """\
#!/bin/bash
# winauto WSL wrapper — Windows 側の winauto.py を呼び出す
# インストール先: {install_dir}/winauto
# Windows Python: {win_python}
# Windows スクリプト: {win_script}
exec cmd.exe /c "{win_python}" "{win_script}" "$@"
"""


def install_wsl(src: Path, prefix: Path, win_python: str, dry_run: bool) -> Path:
    """WSL 用: Windows 側に winauto.py をコピーし、bash ラッパーを作成する。"""
    step(f"WSL 用 winauto をインストールしています...")

    # Windows 側のインストール先を決定
    # USERPROFILE から決定する（WSL では /mnt/c/Users/<name> 相当）
    try:
        result = subprocess.run(
            ["cmd.exe", "/c", "echo", "%USERPROFILE%"],
            capture_output=True, text=True, timeout=3
        )
        win_userprofile = result.stdout.strip()
    except Exception:
        win_userprofile = r"C:\Users\Public"

    win_install_dir = rf"{win_userprofile}\.local\bin\winauto"
    win_script_win = rf"{win_install_dir}\{SCRIPT_NAME}"

    # Windows 側ディレクトリを作成
    info(f"Windows 側インストール先: {win_install_dir}")
    if not dry_run:
        subprocess.run(
            ["cmd.exe", "/c", "mkdir", win_install_dir],
            capture_output=True
        )

    # winauto.py を Windows 側へコピー
    win_script_unix = subprocess.run(
        ["wslpath", win_script_win], capture_output=True, text=True
    ).stdout.strip()
    info(f"コピー先: {win_script_unix}")
    if not dry_run:
        Path(win_script_unix).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, win_script_unix)
        ok("winauto.py を Windows 側へコピーしました")

    # WSL 側の bash ラッパーを作成
    prefix.mkdir(parents=True, exist_ok=True)
    dst = prefix / TOOL_NAME
    wrapper = WSL_WRAPPER_TEMPLATE.format(
        install_dir=win_install_dir,
        win_python=win_python,
        win_script=win_script_win,
    )
    info(f"ラッパー作成: {dst}")
    if not dry_run:
        dst.write_text(wrapper, encoding="utf-8")
        mode = dst.stat().st_mode
        dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        ok("winauto ラッパーを作成しました")

    return dst


# ---------------------------------------------------------------------------
# Unix (non-WSL) インストール ― 情報提供のみ
# ---------------------------------------------------------------------------

def install_unix_info(src: Path) -> None:
    """macOS/Linux: 情報提供のみ（pywinauto は Windows 専用）。"""
    warn("pywinauto は Windows 専用ライブラリです。")
    warn("このスクリプトは macOS/Linux ネイティブ環境では動作しません。")
    print()
    print("  利用可能な代替案:")
    print("  - Windows VM / RDP 上で winauto を実行する")
    print("  - WSL2 環境から winauto をインストールする（Windows Python 経由）")
    print()
    print("  WSL2 でのインストール方法:")
    print("    1. Windows に Python をインストールする")
    print("    2. WSL2 端末で: python install.py  （本スクリプト）")
    print()


# ---------------------------------------------------------------------------
# PATH チェック
# ---------------------------------------------------------------------------

def check_path_unix(prefix: Path) -> None:
    paths = os.environ.get("PATH", "").split(":")
    if str(prefix) in paths:
        ok(f"{prefix} は PATH に含まれています")
        return
    warn(f"{prefix} が PATH に含まれていません")
    shell_rc = "~/.zshrc" if os.environ.get("SHELL", "").endswith("zsh") else "~/.bashrc"
    print()
    print(f"  次の行を {shell_rc} に追加してください:")
    print()
    print(f'    export PATH="$HOME/.local/bin:$PATH"')
    print()
    print("  追加後:")
    print(f"    source {shell_rc}")
    print()


def check_path_windows(install_dir: Path) -> None:
    user_path = os.environ.get("PATH", "")
    if str(install_dir) in user_path:
        ok(f"{install_dir} は PATH に含まれています")
        return
    warn(f"{install_dir} が PATH に含まれていません")
    print()
    print("  PowerShell（管理者不要）で PATH に追加:")
    print()
    escaped = str(install_dir).replace("'", "''")
    print(f"    $dir = '{escaped}'")
    print(r"    $cur = [System.Environment]::GetEnvironmentVariable('Path','User')")
    print(r"    [System.Environment]::SetEnvironmentVariable('Path', $cur + ';' + $dir, 'User')")
    print()


# ---------------------------------------------------------------------------
# 完了メッセージ
# ---------------------------------------------------------------------------

def print_done_windows(install_dir: Path) -> None:
    print()
    print("=" * 60)
    print("[OK]    winauto インストール完了！")
    print("=" * 60)
    print()
    print("  基本コマンド:")
    print("    winauto apps                         # 起動中アプリ一覧")
    print("    winauto tree --app notepad           # UIツリー表示")
    print("    winauto click \"name:=OK\" --app myapp # ボタンクリック")
    print("    winauto codegen myapp.exe            # スクリプト生成")
    print("    winauto screenshot --app myapp       # スクリーンショット")
    print()
    print("  ヘルプ:")
    print("    winauto --help")
    print("    winauto tree --help")
    print()


def print_done_wsl(dst: Path) -> None:
    print()
    print("=" * 60)
    ok("winauto WSL インストール完了！")
    print("=" * 60)
    print()
    print("  基本コマンド（WSL 端末から実行）:")
    print("    winauto apps                         # 起動中アプリ一覧")
    print("    winauto tree --app notepad           # UIツリー表示")
    print("    winauto click \"name:=OK\" --app myapp # ボタンクリック")
    print("    winauto screenshot --app myapp --output /tmp/sc.png")
    print()
    print("  スクリプトは WSL パスで指定できます:")
    print("    winauto run my_automation.py")
    print()
    print(f"  インストール先: {dst}")
    print()


# ---------------------------------------------------------------------------
# 動作確認
# ---------------------------------------------------------------------------

def verify_installation(dry_run: bool) -> None:
    step("インストール確認中...")
    if dry_run:
        info("[DRY-RUN] スキップ")
        return

    if _is_windows():
        result = subprocess.run(
            ["winauto", "--version"], capture_output=True, text=True
        )
        if result.returncode == 0:
            ok(f"winauto コマンド確認: {result.stdout.strip()}")
        else:
            warn("winauto コマンドが PATH 上で見つかりません。PATH を確認してください。")
    elif _is_wsl():
        result = subprocess.run(
            ["winauto", "--version"], capture_output=True, text=True
        )
        if result.returncode == 0:
            ok(f"winauto コマンド確認: {result.stdout.strip()}")
        else:
            warn("新しい端末を開いて winauto --version を実行して確認してください。")


# ---------------------------------------------------------------------------
# CLI パーサー
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    is_win = _is_windows()
    default_dir = str(DEFAULT_INSTALL_DIR_WINDOWS if is_win else DEFAULT_PREFIX_UNIX)

    p = argparse.ArgumentParser(
        prog="install.py",
        description="winauto インストーラー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使い方:
  python install.py                          # 自動検出でインストール
  python install.py --install-dir C:\\tools  # Windows: インストール先指定
  python install.py --prefix ~/.local/bin    # WSL/Linux: インストール先指定
  python install.py --dry-run               # 実行内容の確認のみ
  python install.py --skip-deps             # pip install をスキップ
""".strip(),
    )

    if is_win:
        p.add_argument("--install-dir", default=default_dir, metavar="DIR",
                       help=f"インストール先フォルダ (デフォルト: {default_dir})")
    else:
        p.add_argument("--prefix", default=default_dir, metavar="DIR",
                       help=f"インストール先ディレクトリ (デフォルト: {default_dir})")

    p.add_argument("--dry-run", action="store_true",
                   help="実行内容を表示するだけでインストールしない")
    p.add_argument("--skip-deps", action="store_true",
                   help="依存ライブラリのインストールをスキップする")
    return p


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    args = build_parser().parse_args()
    script_dir = Path(__file__).parent.resolve()

    print()
    print("=" * 60)
    print("  winauto インストーラー")
    print("=" * 60)
    print(f"  実行環境: {_env_label()} / Python {sys.version.split()[0]}")
    if getattr(args, "dry_run", False):
        print(f"  モード: DRY-RUN（実際のインストールは行いません）")
    print()

    # Python バージョン確認
    step("環境チェック")
    check_python()

    src = check_source(script_dir)
    info(f"ソース: {src}")

    dry_run = getattr(args, "dry_run", False)

    # ─── Windows ネイティブ ───────────────────────────────────────────────
    if _is_windows():
        install_dir = Path(args.install_dir).expanduser().resolve()

        if not args.skip_deps:
            install_deps_windows(dry_run)

        dst = install_windows(src, install_dir, dry_run)

        step("PATH チェック")
        check_path_windows(install_dir)

        verify_installation(dry_run)
        print_done_windows(install_dir)

    # ─── WSL ─────────────────────────────────────────────────────────────
    elif _is_wsl():
        prefix = Path(args.prefix).expanduser().resolve()

        step("Windows Python を探しています...")
        win_python = find_windows_python()
        if win_python is None:
            die(
                "Windows 側の python.exe が見つかりませんでした。\n"
                "  Windows に Python 3.9 以上をインストールし、\n"
                "  PATH に追加してから再実行してください。\n"
                "  ダウンロード: https://www.python.org/downloads/"
            )
        ok(f"Windows Python: {win_python}")

        if not args.skip_deps:
            install_deps_wsl(win_python, dry_run)

        dst = install_wsl(src, prefix, win_python, dry_run)

        step("PATH チェック")
        check_path_unix(prefix)

        verify_installation(dry_run)
        print_done_wsl(dst)

    # ─── macOS / Linux ネイティブ ─────────────────────────────────────────
    else:
        install_unix_info(src)


if __name__ == "__main__":
    main()
