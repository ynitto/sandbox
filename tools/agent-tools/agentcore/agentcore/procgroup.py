"""子プロセスを**グループごと**起こして落とす 1 実装。

`subprocess.run(timeout=…)` の打ち切りは**直接の子だけ**を殺す。子がさらに孫（aider が
起こす ollama クライアント、plan.py が起こすエージェント CLI）を持っていると、孫は
パイプを握ったまま生き残り、`communicate()` が EOF を待ち続ける——**上限が効かない**。

実測（2026-08-29）: 壁時計上限 900 秒の `planner_eval` が 70 分走り続け、時計は 900s の
ままだった。同じ夜、殺したはずの推論が朝まで 70% を占め、次の実行が順番待ちになった。

だから起こすときは `start_new_session=True`（子を新しいプロセスグループの長にする）、
落とすときは `os.killpg` で group ごと。POSIX 以外では group を作れないので、従来どおり
直接の子だけを落とす（`kill_group` は嘘をつかず、そのときも黙って動く）。
"""
from __future__ import annotations

import os
import signal
import subprocess

GRACE_SEC = 5.0
"""SIGTERM のあと SIGKILL へ進むまでの猶予。"""

DRAIN_SEC = 30.0
"""打ち切ったあと出力を拾い切るまでの待ち。ここを超えるのは孫がまだ握っている形。"""

_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def popen(args, **kwargs) -> subprocess.Popen:
    """`subprocess.Popen` と同じ。POSIX では子を新しいセッション（＝プロセスグループ）の
    長にして起こすので、`kill_group` が孫まで届く。"""
    if os.name == "posix":
        kwargs.setdefault("start_new_session", True)
    return subprocess.Popen(args, **kwargs)


def _signal_group(proc: subprocess.Popen, sig: int) -> bool:
    """group へ送れたら True。既に居なければ False（呼び出し側は諦めてよい）。"""
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), sig)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            pass        # group を取れない（他所で起こされた子）——直接の子へ倒す
    try:
        proc.kill() if sig == _SIGKILL else proc.terminate()
    except OSError:
        return False
    return True


def kill_group(proc: subprocess.Popen, grace: float = GRACE_SEC) -> None:
    """子とその子孫をまとめて落とす。SIGTERM で待ち、残れば SIGKILL。"""
    if proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, _SIGKILL):
        if not _signal_group(proc, sig):
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def run(args, *, timeout=None, input=None, capture_output=False,
        drain_sec: float = DRAIN_SEC, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` の差し替え。上限で group ごと落とすところだけが違う。

    上限に当たったときは `subprocess.TimeoutExpired` を送出する（呼び出し側の except は
    そのまま）。孫がパイプを握ったままで出力を拾い切れないときは、`output` / `stderr` を
    None にして返す——**待ち続けない**のがこの関数の目的である。
    """
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    if input is not None:
        kwargs.setdefault("stdin", subprocess.PIPE)
    proc = popen(args, **kwargs)
    try:
        out, err = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        try:
            out, err = proc.communicate(timeout=drain_sec)
        except subprocess.TimeoutExpired:
            out = err = None
        raise subprocess.TimeoutExpired(args, timeout, output=out, stderr=err) from None
    except BaseException:
        kill_group(proc)
        raise
    return subprocess.CompletedProcess(args, proc.returncode, out, err)
