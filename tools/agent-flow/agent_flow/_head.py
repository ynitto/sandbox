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
try:
    import msvcrt  # Windows のみ。POSIX では None（fcntl を使う）。
except ImportError:
    msvcrt = None  # type: ignore

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
    """プロセス間の排他ロック。POSIX は fcntl.flock、Windows は msvcrt.locking で実装する。
    以前は fcntl 非対応環境（Windows）で no-op だったため、claim の直列化・二重勝者防止が
    Windows で一切効かず二重実行の温床になっていた。どちらも無い環境のみ no-op に落ちる。"""
    if fcntl is None and msvcrt is None:  # pragma: no cover — 想定外の環境のみ
        yield
        return
    f = open(path, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        else:  # Windows: 先頭 1 バイトの領域ロックで排他（獲得までブロッキング再試行）
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # 最大 ~10 秒待って例外
                    break
                except OSError:
                    time.sleep(0.2)
        try:
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(f, fcntl.LOCK_UN)
                else:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        f.close()


# エージェント共通ホームのディレクトリ名。`.agent` から `.agents` へ改名した
# （複数のエージェントが相乗りする持ち物であることを名前で示す）。
# 旧ホームが残っている環境では、新ホームがまだ無い間だけ旧ホームを使う——両方へ書くと
# 実行制御や予算の状態が分裂し、「どちらか片方だけ正しい」状況が生まれる。
AGENT_HOME = ".agents"
AGENT_HOME_LEGACY = ".agent"


def agent_home_dir(root=None) -> str:
    """エージェント共通ホーム（既定 ~/.agents）。旧 ~/.agent しか無ければそちらを返す。"""
    base = os.path.expanduser(root) if root else os.path.expanduser("~")
    new, old = os.path.join(base, AGENT_HOME), os.path.join(base, AGENT_HOME_LEGACY)
    return old if (not os.path.isdir(new) and os.path.isdir(old)) else new


def agent_home_subdir(env_var: str, *parts: str) -> str:
    """共通ホーム配下の状態ディレクトリ（`$<env_var>` があればそれを最優先）。

    **判定はサブディレクトリ単位で行う。** ホーム単位で見ると、`.agents/skills` だけ先に
    作られた環境（スキル導入が先行した）で「新ホームは在る」と判断され、まだ移していない
    `.agent/control` を見失う。項目ごとに実在する方へ寄せれば、移行が部分的に進んだ
    状態でも状態は 1 か所に定まる。"""
    override = os.environ.get(env_var)
    if override:
        return os.path.expanduser(override)
    home = os.path.expanduser("~")
    new = os.path.join(home, AGENT_HOME, *parts)
    old = os.path.join(home, AGENT_HOME_LEGACY, *parts)
    return old if (not os.path.exists(new) and os.path.exists(old)) else new

