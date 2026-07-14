from __future__ import annotations
# _head.py — 元 agent-flow.py の 26-82 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
import argparse
import atexit
import contextlib
import hashlib
import inspect
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

try:
    import fcntl  # POSIX のみ（macOS/Linux/WSL）。Windows では None。
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

# 終端 status（これに達した run は active_runs から外れ、孤児 reclaim も resume しない）。
# canceled は人の明示指示（cmd_cancel）による恒久停止。done/failed と同じく終端だが、
# 「成果あり(done)」でも「異常(failed)」でもない「意図的な打ち切り」を表す。
TERMINAL = {"done", "failed", "canceled"}


def _claim_lock_path(claim_dir: str) -> str:
    """claim 用の排他ロックファイルのパス（バス外の一時領域に置く）。
    同一マシンの同一 claim_dir には同一パスが対応し、プロセス/スレッド間で排他になる。"""
    h = hashlib.sha1(os.path.abspath(claim_dir).encode()).hexdigest()
    d = os.path.join(tempfile.gettempdir(), "agent-flow-locks")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{h}.lock")


@contextlib.contextmanager
def _file_lock(path: str):
    """fcntl があれば排他ロック。無ければ no-op（ベストエフォート）。"""
    if fcntl is None:
        yield
        return
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()

