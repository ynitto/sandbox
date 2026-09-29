#!/usr/bin/env python3
"""pair-align のステートマシンをリポジトリへ置く。

    python3 tools/pair-align/install.py <実装のリポジトリ> --side impl --pair ../my-design
    python3 tools/pair-align/install.py <設計書のリポジトリ> --side design --pair ../my-impl

`<リポジトリ>/.statemachine/pair_align/` に machine/ の中身を写し、pair.json を書く。
既に置いてあれば定義とスクリプトを入れ替え（古いファイルは消す）、pair.json は --side / --pair を渡したときだけ書き換える。
`.pair-align/`（計画・探した結果・graphify のグラフ）は .gitignore に足す（--no-gitignore で足さない）。
このマシン自身が graphify の索引に入らないよう、.graphifyignore に `.statemachine/pair_align/` を足す。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "machine"
DEST_REL = Path(".statemachine") / "pair_align"
IGNORE_LINE = ".pair-align/"
# 相手が graphify で知識グラフを作るとき、このマシン自身を索引に入れない。
GRAPHIFY_IGNORE_LINE = ".statemachine/pair_align/"


def install(target: Path, side: str | None, pair: str | None, gitignore: bool = True) -> Path:
    if not (target / ".git").exists():
        raise SystemExit(f"git リポジトリではありません: {target}")
    dest = target / DEST_REL
    config_file = dest / "pair.json"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else None
    if config is None and (side is None or pair is None):
        raise SystemExit("初めて置くときは --side と --pair を指定してください")

    dest.mkdir(parents=True, exist_ok=True)
    # 古い版のファイルを残さない（pair.json だけは利用者の設定なので残す）。
    for item in dest.iterdir():
        if item.name == "pair.json":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in SRC.iterdir():
        if item.name in ("pair.json", "__pycache__"):
            continue
        target_item = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target_item, dirs_exist_ok=True)
        else:
            shutil.copyfile(item, target_item)

    config = config or {"graphify": "auto"}
    if side:
        config["side"] = side
    if pair:
        config["pair_path"] = pair
    config.setdefault("graphify", "auto")
    config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if gitignore:
        append_line(target / ".gitignore", IGNORE_LINE)
    append_line(target / ".graphifyignore", GRAPHIFY_IGNORE_LINE)
    return dest


def append_line(path: Path, line: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    if line in lines:
        return
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    prefix = "" if not text or text.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{prefix}{line}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="pair-align のステートマシンをリポジトリへ置く")
    p.add_argument("target", help="置き先のリポジトリ")
    p.add_argument("--side", choices=["impl", "design"], help="このリポジトリの側（impl = 実装 / design = 設計書）")
    p.add_argument("--pair", help="相手のリポジトリのパス（置き先からの相対でも絶対でもよい）")
    p.add_argument("--no-gitignore", action="store_true", help=".gitignore に .pair-align/ を足さない")
    args = p.parse_args(argv)
    dest = install(Path(args.target).resolve(), args.side, args.pair, gitignore=not args.no_gitignore)
    config = json.loads((dest / "pair.json").read_text(encoding="utf-8"))
    print(f"置きました: {dest}")
    print(f"  この側: {config['side']}  相手: {config['pair_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
