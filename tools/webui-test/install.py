#!/usr/bin/env python3
"""webui-test のインストーラを Python から呼ぶ入口。Windows は install.ps1、ほかは install.sh に渡す。

    python tools/webui-test/install.py              # 足りないものを入れる
    python tools/webui-test/install.py --check      # 入れたあと、同梱のサンプルで確かめる
    python tools/webui-test/install.py --skip-browser

install.ps1 は PowerShell のスクリプトなので、`python install.ps1` では動かない（SyntaxError になる）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# install.sh の指定 → install.ps1 の指定
PS_FLAGS = {"--check": "-Check", "--skip-browser": "-SkipBrowser"}


def command(argv: list[str], windows: bool) -> list[str]:
    if not windows:
        return ["bash", str(HERE / "install.sh"), *argv]
    unknown = [a for a in argv if a not in PS_FLAGS]
    if unknown:
        raise SystemExit(f"Windows では使えない指定です: {' '.join(unknown)}（使えるのは {' '.join(PS_FLAGS)}）")
    shell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "install.ps1"),
            *(PS_FLAGS[a] for a in argv)]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    return subprocess.call(command(argv, os.name == "nt"))


if __name__ == "__main__":
    sys.exit(main())
