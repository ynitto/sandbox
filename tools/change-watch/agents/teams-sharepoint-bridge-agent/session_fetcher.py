"""対話セッションの同期フォルダからサービス側の受信フォルダへ依頼を運ぶ。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat()


def _invalid_constant(value):
    raise ValueError(f"JSON に使えない値です: {value}")


def _read_ledger(path):
    try:
        ledger = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_invalid_constant)
    except FileNotFoundError:
        return {}
    if not isinstance(ledger, dict):
        raise ValueError("台帳の JSON はオブジェクトである必要があります")
    files = ledger.get("files", {})
    if not isinstance(files, dict) or any(not isinstance(record, dict) for record in files.values()):
        raise ValueError("台帳の files にはファイル名ごとの記録が必要です")
    return files


def _write_atomic(path, data):
    tmp_path = None
    try:
        # 取得元の *.json に当たらない一時名で、正式名と同じフォルダに書く。
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _write_json(path, data):
    _write_atomic(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _matches(path, digest):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except FileNotFoundError:
        return False


def _fetch_files(paths, dest, files, settle_seconds):
    result = {"fetched": [], "skipped_settling": [], "already": 0, "errors": []}
    for path in paths:
        name = path.name
        if name.startswith(".") or not name.endswith(".json"):
            continue
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                continue
            if time.time() - before.st_mtime < settle_seconds:
                result["skipped_settling"].append(name)
                continue
            data = path.read_bytes()
            after = path.stat()
            # 読み取りの途中で同期が進んだファイルは次回に回す。
            if (before.st_mtime_ns, before.st_size, before.st_ino) != (
                after.st_mtime_ns, after.st_size, after.st_ino
            ) or len(data) != after.st_size:
                result["skipped_settling"].append(name)
                continue
            digest = hashlib.sha256(data).hexdigest()
            target = dest / name
            if files.get(name, {}).get("sha256") == digest and _matches(target, digest):
                result["already"] += 1
                continue
            _write_atomic(target, data)
            files[name] = {
                "sha256": digest,
                "size": len(data),
                "fetched_at": _now(),
                "source_mtime": datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat(),
            }
            result["fetched"].append(name)
        except (OSError, ValueError, OverflowError) as e:
            result["errors"].append({"file": name, "error": str(e)})
    return result


def _fetch_once(args):
    source = args.source
    try:
        if not source.is_dir():
            raise ValueError(f"同期フォルダがありません: {source}")
        source_root = source.resolve()
        outputs = [args.dest, args.state]
        if args.heartbeat is not None:
            outputs.append(args.heartbeat)
        for path in outputs:
            resolved = path.resolve()
            if resolved == source_root or source_root in resolved.parents:
                raise ValueError(f"出力先は同期フォルダの外に指定してください: {path}")

        args.dest.mkdir(parents=True, exist_ok=True)
        args.state.parent.mkdir(parents=True, exist_ok=True)
        files = _read_ledger(args.state)
        result = _fetch_files(sorted(source.iterdir(), key=lambda path: path.name),
                              args.dest, files, args.settle_seconds)
        if result["fetched"]:
            _write_json(args.state, {"files": files})
        if args.heartbeat is not None:
            args.heartbeat.parent.mkdir(parents=True, exist_ok=True)
            _write_json(args.heartbeat, {
                "last_run": _now(),
                "fetched": len(result["fetched"]),
                "skipped_settling": len(result["skipped_settling"]),
                "errors": len(result["errors"]),
            })
    except (OSError, ValueError, RuntimeError) as e:
        print(f"フェッチャの実行に失敗しました: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["errors"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch-once", help="同期済みの依頼を一度だけコピーする")
    fetch.add_argument("--source", required=True, type=Path)
    fetch.add_argument("--dest", required=True, type=Path)
    fetch.add_argument("--state", required=True, type=Path)
    fetch.add_argument("--settle-seconds", type=int, default=10)
    fetch.add_argument("--heartbeat", type=Path)
    args = parser.parse_args(argv)
    return _fetch_once(args)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
