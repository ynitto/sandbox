#!/usr/bin/env python3
"""codd-statemachine を端末に入れる（端末ごとに 1 回と、新しい版を入れるとき）。

    python3 tools/codd-statemachine/install.py

本体（machine/ と init.py）をホームの `~/.statemachine/codd-statemachine/` に写し、リポジトリの初期設定をする
`codd-init` コマンドを `~/.local/bin/` に置く（Windows では `codd-init.cmd`）。どちらも --dest / --bin-dir で変えられる。
入れたあと、リポジトリごとに次を実行する（初期設定。本体をリポジトリの `.statemachine/codd/` に写し、codd.json を書く）。

    codd-init <リポジトリ> --side impl --ref docs=../my-design

新しい版を入れたら、各リポジトリで `codd-init <リポジトリ>` をもう一度実行すると本体が入れ替わる（codd.json は残る）。
graphify はこのリポジトリのルートの install.py（外部ツールのセットアップ）で入る。

以前の使い方（`install.py <リポジトリ> --side … --ref …`）で呼ばれたときは、端末に入れたうえで、そのまま初期設定もする。
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DEST = Path.home() / ".statemachine" / "codd-statemachine"
DEFAULT_BIN = Path.home() / ".local" / "bin"
PARTS = ("machine", "init.py")


def install(dest: Path = DEFAULT_DEST, bin_dir: Path = DEFAULT_BIN) -> Path:
    """本体をホームへ写し、codd-init を置く。返すのは置いた codd-init のパス。"""
    if dest.resolve() != HERE:
        dest.mkdir(parents=True, exist_ok=True)
        for name in PARTS:   # 古い版のファイルを残さない
            target = dest / name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        shutil.copytree(HERE / "machine", dest / "machine", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(HERE / "init.py", dest / "init.py")
    bin_dir.mkdir(parents=True, exist_ok=True)
    init = dest / "init.py"
    if os.name == "nt":
        launcher = bin_dir / "codd-init.cmd"
        launcher.write_text(f'@"{sys.executable}" "{init}" %*\r\n', encoding="utf-8")
    else:
        launcher = bin_dir / "codd-init"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{init}" "$@"\n', encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def on_path(folder: Path) -> bool:
    return any(Path(p).expanduser().resolve() == folder.resolve()
               for p in os.environ.get("PATH", "").split(os.pathsep) if p)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(description="codd-statemachine を端末に入れる（リポジトリの初期設定は codd-init）",
                                allow_abbrev=False)
    p.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"本体を置くフォルダ（既定 {DEFAULT_DEST}）")
    p.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN, help=f"codd-init を置くフォルダ（既定 {DEFAULT_BIN}）")
    args, rest = p.parse_known_args(argv)
    launcher = install(args.dest.expanduser(), args.bin_dir.expanduser())
    print(f"端末に入れました: {args.dest}")
    print(f"  リポジトリの初期設定: {launcher.name} <リポジトリ> --side impl --ref docs=../my-design")
    if not on_path(args.bin_dir.expanduser()):
        print(f"  {args.bin_dir} が PATH にありません。PATH に足すか、{launcher} をそのまま呼んでください")
    if rest:
        # 以前の使い方（リポジトリを渡す）。初期設定も続けて行う。
        print("リポジトリの初期設定は codd-init に分けました。今回は続けて初期設定もします。")
        sys.path.insert(0, str(args.dest.expanduser()))
        import init as repo_init
        return repo_init.main(rest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
