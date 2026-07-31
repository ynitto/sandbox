#!/usr/bin/env python3
"""
Windows Terminal Window Manager

指定した名前のタイトルを持つ Windows Terminal ウィンドウを管理する。
同名のウィンドウが既に存在する場合は閉じてから新しいウィンドウを開く。

使用方法:
    wt_manager.py --name <名前> [wt オプション ...]

例:
    wt_manager.py --name "Dev" new-tab --title "Dev" -- pwsh
    wt_manager.py -n "MyApp" ; new-tab --title "MyApp" -- bash
"""

import subprocess
import sys
import time

try:
    import win32con
    import win32gui
    import win32process

    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

_WT_PROCESS_NAME = "windowsterminal.exe"
_CLOSE_TIMEOUT = 10.0
_POLL_INTERVAL = 0.1


def _is_wt_process(pid: int) -> bool:
    if not HAS_PSUTIL:
        return True  # psutil なしの場合はタイトル一致のみで判定
    try:
        return psutil.Process(pid).name().lower() == _WT_PROCESS_NAME
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _find_wt_windows_by_title(title: str) -> list[int]:
    """指定タイトルを持つ Windows Terminal のウィンドウハンドル一覧を返す。"""
    if not HAS_WIN32:
        return []

    found: list[int] = []

    def _callback(hwnd: int, _) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetWindowText(hwnd) != title:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if _is_wt_process(pid):
                found.append(hwnd)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_callback, None)
    return found


def _close_and_wait(hwnds: list[int], timeout: float = _CLOSE_TIMEOUT) -> None:
    """ウィンドウを閉じてすべてが消えるまで待機する。"""
    for hwnd in hwnds:
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = [h for h in hwnds if win32gui.IsWindow(h)]
        if not alive:
            return
        time.sleep(_POLL_INTERVAL)


def _parse_name(argv: list[str]) -> tuple[str | None, list[str]]:
    """argv から --name/-n を取り出し、残りの引数と共に返す。"""
    name: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] in ("--name", "-n") and i + 1 < len(argv):
            name = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    return name, rest


def main() -> None:
    name, passthrough = _parse_name(sys.argv[1:])

    if name and HAS_WIN32:
        hwnds = _find_wt_windows_by_title(name)
        if hwnds:
            _close_and_wait(hwnds)

    cmd = ["wt.exe"]
    if name:
        cmd += ["--title", name]
    cmd += passthrough

    # 親プロセスから切り離して起動（コンソールウィンドウ非表示）
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008
    subprocess.Popen(
        cmd,
        creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
        close_fds=True,
    )


if __name__ == "__main__":
    main()
