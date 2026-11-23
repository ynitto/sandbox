"""同期フォルダから受信フォルダへ依頼を運ぶ無状態のコピー役。

settle 判定と重複検出は change-watch の folder 取得元が担う。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


def _copy_atomic(source, target, before):
    tmp_path = None
    try:
        # 正式名と同じフォルダに、*.json に当たらない一時名で書く。
        with source.open("rb") as original:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".tmp-", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                shutil.copyfileobj(original, tmp)
                tmp.flush()
                os.fsync(tmp.fileno())
        try:
            os.chmod(tmp_path, 0o644)
        except OSError:
            pass
        # コピー中に同期元が変わったら、正式名で公開する前に一時コピーを破棄する。
        after = source.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise OSError(f"コピー中に同期元のファイルが更新されました: {source}")
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _fetch_once(args):
    source = args.source
    try:
        if not source.is_dir():
            raise ValueError(f"同期フォルダがありません: {source}")
        source_root = source.resolve()
        dest = args.dest.resolve()
        if dest == source_root or source_root in dest.parents:
            raise ValueError(f"出力先は同期フォルダの外に指定してください: {args.dest}")
        dest.mkdir(parents=True, exist_ok=True)
        # 強制終了で残った一時ファイルを片付ける。
        for path in dest.iterdir():
            if path.name.startswith(".tmp-") and not path.is_symlink() and path.is_file():
                path.unlink(missing_ok=True)
        paths = sorted(source_root.iterdir(), key=lambda path: path.name)
    except (OSError, ValueError) as e:
        print(f"フェッチャの実行に失敗しました: {e}", file=sys.stderr)
        return 2

    result = {"copied": [], "errors": []}
    for path in paths:
        name = path.name
        if name.startswith(".") or not name.endswith(".json"):
            continue
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                continue
            _copy_atomic(path, dest / name, before)
            result["copied"].append(name)
        # 想定外の例外もファイル単位で記録し、残りのコピーを続ける。
        except Exception as e:
            result["errors"].append({"file": name, "error": str(e)})
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["errors"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch-once", help="同期済みの依頼を一度だけコピーする")
    fetch.add_argument("--source", required=True, type=Path)
    fetch.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args(argv)
    return _fetch_once(args)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
