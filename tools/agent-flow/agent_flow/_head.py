from __future__ import annotations
# _head.py — 元 agent-flow.py の 26-82 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
import argparse
import atexit
import contextlib
import hashlib
import inspect
import io
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
# cancelled は人の明示指示（cmd_cancel）による恒久停止。done/failed と同じく終端だが、
# 「成果あり(done)」でも「異常(failed)」でもない「意図的な打ち切り」を表す。
# 常駐一本化 P0・W0-9 で語彙統一（旧 "canceled" 米式 → "cancelled" 英式。板・amigos と揃える）。
# 実体は agentcore.vocab（flow/amigos/project/板で共通の完了語彙 — 設計 §4.1・R1）。
# ここで読み取り用の集合（正典 + 旧綴り）を使うのは、TERMINAL の参照がすべて
# 「バス上の既存 meta.status が終端か」の判定だからである。改称前に cancel された run が
# 旧綴りのままバスに残っており、それを非終端と読むと active_runs → 孤児回収で蘇る。
# **書き込みは常に正典**（cancelled）で、旧綴りを書く箇所はもう存在しない。
from agentcore.vocab import TERMINAL_READ as TERMINAL  # noqa: E402
# node_id（PC の身元）の正規化は 3 ツール共通の 1 実装に寄せる（実装計画 W1-10）。
# エンジンごとの綴り替えは同じ PC を板に 2 ノードとして登録してしまう。
from agentcore.nodeid import normalize_node_id, default_node_id  # noqa: E402
# git URL の正規化一致と「このノードのローカルクローン」解決（S3）。gitcache の `_same_repo` と
# board の `_norm_repo_url` は同じ判定の別実装で、agent-project 側とも吸収規則が食い違っていた。
from agentcore import repolocal as _repolocal  # noqa: E402
from agentcore import verifycontract as _verifycontract  # noqa: E402
from agentcore import interaction as _interaction  # noqa: E402
from agentcore import nodecontract as _nodecontract  # noqa: E402
from agentcore import executionresolver as _executionresolver  # noqa: E402
# エージェント CLI 定義（agents/<name>.json）の読み込みと argv 組み立て（S9）。組み込み
# （kiro/claude/copilot/codex）を含む全 CLI がこの定義で動く。以前は同じ argv 知識が
# agent-project / agent-flow / agent-amigos / dashboard に重複していた（repolocal と同型）。
from agentcore import agentcli as _agentcli  # noqa: E402
# プロンプトキャッシュに適合する注入順の正規化（案 H）。安定部（プロジェクト文脈）→
# 可変部（タスク固有）の順に決定的な区切りで連結する 1 実装（agent-project と共有）。
from agentcore import promptcompose as _promptcompose  # noqa: E402
# リトライのバックオフ待ちの唯一の seam（agentcore.transport.backoff_sleep）。素の time.sleep を
# 差し替えると stdlib の subprocess 内部（プロセス終了の 0.001s 倍々ポーリング）にも効いてしまい、
# 高負荷時だけテストが壊れる。リトライ経路はこの名前を通す。
from agentcore.transport import backoff_sleep  # noqa: E402


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


AGENT_HOME = ".agents"
AGENT_HOME_LEGACY = ".agent"


def agent_home_dir(root=None) -> str:
    """エージェント共通ホーム `~/.agents`。"""
    base = os.path.expanduser(root) if root else os.path.expanduser("~")
    return os.path.join(base, AGENT_HOME)


def agent_home_subdir(env_var: str, *parts: str) -> str:
    """共通ホーム配下の状態ディレクトリ（`$<env_var>` があればそれを最優先）。"""
    override = os.environ.get(env_var)
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), AGENT_HOME, *parts)
