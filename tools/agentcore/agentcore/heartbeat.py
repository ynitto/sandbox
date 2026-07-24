"""agentcore.heartbeat — 心拍・鮮度の共通実装。

心拍レコードは `heartbeat`（UTC ISO8601 文字列）を持つ任意の dict。lease を伴うレコードは
加えて `lease_until`（unix epoch 秒）を持つ。このモジュールはレコードの読み書きは持たず
（呼び出し側が protocol.py の claim/lease か自前の JSON I/O で行う）、鮮度・生死の判定だけを
共通化する。"""
from __future__ import annotations

import time
from datetime import datetime, timezone

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime(_ISO_FMT)


def parse_iso(ts: "str | None") -> "float | None":
    """ISO8601 UTC 文字列を unix epoch 秒へ。パース不能なら None。"""
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _ISO_FMT).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def is_fresh(record: "dict | None", fresh_after_sec: float, now: "float | None" = None) -> bool:
    """record の heartbeat が fresh_after_sec 秒以内に更新されているか。
    record が無い・heartbeat が無い・パース不能ならすべて False（生死不明は死んでいる扱い）。"""
    if not record:
        return False
    ts = parse_iso(record.get("heartbeat"))
    if ts is None:
        return False
    now = now if now is not None else time.time()
    return (now - ts) <= fresh_after_sec


def is_lease_alive(record: "dict | None", now: "float | None" = None) -> bool:
    """record の lease_until がまだ未来（失効していない）か。"""
    if not record:
        return False
    lease_until = record.get("lease_until")
    if lease_until is None:
        return False
    now = now if now is not None else time.time()
    try:
        return float(lease_until) >= now
    except (TypeError, ValueError):
        return False
