"""小道具 — 時刻・ID・JSON 入出力・寛容 JSON 抽出。

agent-flow の同名ヘルパと同じ流儀（stdlib のみ・決定的）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_ts_lock = threading.Lock()
_last_ts = 0.0


def unique_ts() -> float:
    """プロセス内で厳密に増加するタイムスタンプ（claim / メッセージ ID 用）。
    同値 ts による決定的タイブレークの食い違いを防ぐ（agent-flow と同じ理屈）。"""
    global _last_ts
    with _ts_lock:
        t = time.time()
        if t <= _last_ts:
            t = _last_ts + 1e-6
        _last_ts = t
        return t


def ulid() -> str:
    """時系列順に整列する ID（`<マイクロ秒 16 進 14 桁>-<pid 下 4 桁>`）。
    ファイル名の辞書順 = 生成順になることだけを保証する簡易 ULID。"""
    t = unique_ts()
    return f"{int(t * 1e6):014x}-{os.getpid() % 10000:04d}"


def log(who: str, msg: str) -> None:
    print(f"[{now_iso()}] [{who}] {msg}", flush=True)


def read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json_atomic(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_jsonl(path: str, record: dict) -> None:
    """追記専用ログへ 1 行足す。書くのは自分名義のファイルだけ（§4.2 の所有権規律）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> list:
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return out


def extract_json(text: str):
    """LLM 出力から JSON を寛容に取り出す（agent-flow / hermes-kiro-acp の作法）。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opn, cls in (("{", "}"), ("[", "]")):
        i, j = text.find(opn), text.rfind(cls)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("エージェント出力から JSON を抽出できませんでした")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def safe_relpath(path: str) -> str:
    """アクション封筒のパスを検証する: 相対・`..` なし・絶対パス禁止（§4.2 の代書規律）。"""
    p = str(path or "").replace("\\", "/").strip()
    if not p or p.startswith("/") or p.startswith("~"):
        raise ValueError(f"不正なパスです（相対パスのみ許可）: {path!r}")
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if any(seg == ".." for seg in parts):
        raise ValueError(f"不正なパスです（.. は許可しない）: {path!r}")
    if not parts:
        raise ValueError(f"不正なパスです: {path!r}")
    return "/".join(parts)
