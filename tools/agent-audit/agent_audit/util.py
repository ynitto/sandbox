"""共通ユーティリティ（JSON 入出力・時刻・ログ）。"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import tempfile

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def log(tag: str, msg: str) -> None:
    print(f"[agent-audit] {tag}: {msg}", flush=True)


def elog(msg: str) -> None:
    print(f"[agent-audit] {msg}", file=sys.stderr)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts) -> "float | None":
    """ISO8601（Z / オフセット / 秒未満あり・なし）を epoch 秒へ。読めなければ None。"""
    if isinstance(ts, (int, float)):
        return float(ts)
    if not isinstance(ts, str) or not ts:
        return None
    s = ts.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def epoch_to_iso(sec: float) -> str:
    return _dt.datetime.fromtimestamp(sec, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_day(sec: float) -> str:
    return _dt.datetime.fromtimestamp(sec, _dt.timezone.utc).strftime("%Y%m%d")


def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json_atomic(path: str, data) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_jsonl(path: str, record: dict) -> None:
    """追記専用 JSONL（O_APPEND・1 行 1 レコード。台帳と同じ規律で書き換え・削除はしない）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def iter_jsonl(path: str):
    """JSONL を 1 行ずつ dict で返す。壊れた行は黙って飛ばす（追記中の尻切れに耐える）。"""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")
