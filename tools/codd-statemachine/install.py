#!/usr/bin/env python3
"""codd-statemachine が使う外部のミドルウェアを端末に入れる（端末ごとに 1 回と、更新するとき）。

    python3 tools/codd-statemachine/install.py              # 足りないものを入れる
    python3 tools/codd-statemachine/install.py --upgrade    # 入っているものも最新にする

入れるもの:
  graphify   参照先を探すときの知識グラフ（任意。無ければ文字列検索だけで動く）。uv → pipx → pip の順で入れる

確かめるだけのもの（入れ方は表示する）:
  git        必須
  webui-test 画面のテストを変えたあとの検査に使うとき（`tools/webui-test/install.py`）

codd そのものは端末に入れない。リポジトリごとに `init.py` で置く（リポジトリだけで動くように）。

    python3 tools/codd-statemachine/init.py <リポジトリ> --side impl --ref docs=../my-design

以前の使い方（`install.py <リポジトリ> --side … --ref …`）で呼ばれたときは、そのまま init.py に渡す。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT = 600

# (名前, 入っているかを見るコマンド, 入れるコマンドの候補, 最新にするコマンドの候補)
GRAPHIFY = ("graphify", ["graphify", "--version"],
            [["uv", "tool", "install", "graphifyy"], ["pipx", "install", "graphifyy"],
             [sys.executable, "-m", "pip", "install", "--user", "graphifyy"]],
            [["uv", "tool", "upgrade", "graphifyy"], ["pipx", "upgrade", "graphifyy"],
             [sys.executable, "-m", "pip", "install", "--user", "-U", "graphifyy"]])
CHECK_ONLY = (("git", ["git"], "https://git-scm.com/downloads から入れる（必須）"),
              ("webui-test", ["webui-test"],
               "画面のテストを検査に使うなら python3 tools/webui-test/install.py で入れる（任意）"))


def version(cmd: list[str]) -> str | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout.strip() or r.stderr.strip() or "入っている").splitlines()[0] if r.returncode == 0 else None


def run_first(candidates: list[list[str]]) -> str | None:
    """使える道具で順に試し、最初に通ったものの名前を返す。"""
    for cmd in candidates:
        exe = shutil.which(cmd[0])
        if not exe:
            continue
        print(f"  {' '.join(cmd)}")
        try:
            r = subprocess.run([exe, *cmd[1:]], timeout=TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return Path(cmd[0]).name
    return None


def install_middleware(upgrade: bool = False) -> int:
    name, probe, installs, upgrades = GRAPHIFY
    current = version(probe)
    failed = 0
    if current and not upgrade:
        print(f"✓ {name}: {current}")
    else:
        print(f"{name} を{'最新にします' if current else '入れます'}")
        if run_first(upgrades if current else installs):
            print(f"✓ {name}: {version(probe) or '入れた（PATH を確かめてください）'}")
        else:
            failed += 1
            print(f"✗ {name} を入れられませんでした（uv・pipx・pip のどれかが要ります）。無くても文字列検索だけで動きます")
    for name, probe, how in CHECK_ONLY:
        found = shutil.which(probe[0])
        print(f"✓ {name}: {found}" if found else f"- {name}: ありません。{how}")
    print("codd はリポジトリごとに置きます: python3 tools/codd-statemachine/init.py <リポジトリ> --side … --ref …")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(not a.startswith("-") for a in argv) or any(a.startswith(("--side", "--ref")) for a in argv):
        # 以前の使い方（リポジトリを渡す）。リポジトリへ置くのは init.py の仕事。
        print("リポジトリへ置くのは init.py に分けました（install.py は外部のミドルウェアを入れます）。init.py に渡します。")
        sys.path.insert(0, str(HERE))
        import init as repo_init
        return repo_init.main(argv)
    p = argparse.ArgumentParser(description="codd-statemachine が使う外部のミドルウェア（graphify など）を端末に入れる")
    p.add_argument("--upgrade", action="store_true", help="入っているものも最新にする")
    args = p.parse_args(argv)
    return install_middleware(args.upgrade)


if __name__ == "__main__":
    sys.exit(main())
