"""resident.scheduler の単体テスト（実装計画 W1-1）。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "agentcore"))

from agent_project.resident import Scheduler, Tick  # noqa: E402


def test_single_flight_never_overlaps():
    calls = []
    lock = threading.Lock()
    overlap = {"seen": False}

    def slow_tick():
        with lock:
            if calls and calls[-1] == "running":
                overlap["seen"] = True
            calls.append("running")
        time.sleep(0.05)
        with lock:
            calls.append("done")

    sched = Scheduler([Tick("slow", period=0.01, fn=slow_tick)], watchdog_timeout=10)
    sched.start()
    time.sleep(0.2)
    sched.stop()
    sched.join(timeout=2)

    assert not overlap["seen"]
    # 開始と終了が交互に並ぶこと（重複起動していない証拠）
    assert calls.count("running") == calls.count("done")


def test_exception_isolated_other_ticks_keep_running():
    errors = []
    good_calls = {"n": 0}

    def bad_tick():
        raise RuntimeError("boom")

    def good_tick():
        good_calls["n"] += 1

    sched = Scheduler(
        [Tick("bad", period=0.01, fn=bad_tick), Tick("good", period=0.01, fn=good_tick)],
        watchdog_timeout=10,
        on_tick_error=lambda name, exc: errors.append((name, str(exc))),
    )
    sched.start()
    time.sleep(0.15)
    sched.stop()
    sched.join(timeout=2)

    assert good_calls["n"] > 0
    assert any(name == "bad" and "boom" in msg for name, msg in errors)


def test_long_period_healthy_tick_not_aborted():
    # period が watchdog_timeout より長い健全な tick を誤って殺さない（猶予に period を上乗せ）。
    aborted = threading.Event()
    ran = {"n": 0}

    sched = Scheduler(
        [Tick("slow-period", period=0.3, fn=lambda: ran.__setitem__("n", ran["n"] + 1))],
        watchdog_timeout=0.05,   # period(0.3) より短い — 上乗せしないと即 abort
        abort_fn=aborted.set,
    )
    sched.start()
    time.sleep(0.5)
    sched.stop()
    sched.join(timeout=2)

    assert not aborted.is_set(), "健全な長周期 tick を watchdog が誤って abort した"
    assert ran["n"] > 0


def test_declared_tick_timeout_extends_watchdog_grace():
    # 正当に長い tick（外部コマンドを逐次起動する gc 等）を宣言した timeout の分だけ
    # 見逃す。宣言しないと watchdog が健全な常駐体を abort する。
    aborted = threading.Event()
    started = threading.Event()

    def slow_tick():
        started.set()
        time.sleep(0.5)      # watchdog_timeout(0.05) より十分長いが timeout 宣言内

    sched = Scheduler(
        [Tick("slow", period=0.01, fn=slow_tick, timeout=2.0)],
        watchdog_timeout=0.05,
        abort_fn=aborted.set,
    )
    sched.start()
    assert started.wait(timeout=2), "tick が起動していない"
    time.sleep(0.4)
    sched.stop()
    sched.join(timeout=3)

    assert not aborted.is_set(), "timeout を宣言した tick を watchdog が誤って abort した"


def test_notifies_systemd_ready_on_start():
    # Type=notify の unit では READY=1 が届くまで systemd が起動中と見なす。送らないと
    # 起動がタイムアウトし、Restart=always と相まって延々と再起動する。
    import socket as _socket
    tmp = tempfile.mkdtemp()
    sock_path = os.path.join(tmp, "notify.sock")
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(5)
    old = os.environ.get("NOTIFY_SOCKET")
    os.environ["NOTIFY_SOCKET"] = sock_path
    try:
        sched = Scheduler([Tick("noop", period=10, fn=lambda: None)], watchdog_timeout=10)
        sched.start()
        assert srv.recv(64) == b"READY=1"
        sched.stop()
        assert srv.recv(64) == b"STOPPING=1"   # 意図した停止であることも伝える
        sched.join(timeout=2)
    finally:
        srv.close()
        if old is None:
            os.environ.pop("NOTIFY_SOCKET", None)
        else:
            os.environ["NOTIFY_SOCKET"] = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_watchdog_interval_follows_systemd_watchdog_usec():
    # unit の WatchdogSec とこちらの周期が食い違うと、健全なのに systemd から
    # 「応答なし」と判定されて殺される。WATCHDOG_USEC の半分に合わせる。
    old = os.environ.get("WATCHDOG_USEC")
    os.environ["WATCHDOG_USEC"] = str(90 * 1_000_000)     # WatchdogSec=90
    try:
        sched = Scheduler([Tick("noop", period=10, fn=lambda: None)], watchdog_timeout=300)
        assert sched.watchdog_interval() == 45.0
    finally:
        if old is None:
            os.environ.pop("WATCHDOG_USEC", None)
        else:
            os.environ["WATCHDOG_USEC"] = old
    # 未設定なら従来どおり watchdog_timeout/3（上限 30s）
    sched = Scheduler([Tick("noop", period=10, fn=lambda: None)], watchdog_timeout=60)
    assert sched.watchdog_interval() == 20.0


def test_duplicate_tick_names_rejected():
    try:
        Scheduler([Tick("x", period=1, fn=lambda: None), Tick("x", period=1, fn=lambda: None)])
    except ValueError:
        return
    raise AssertionError("重複 tick 名を拒否するべき")


def test_join_timeout_is_a_total_budget_not_per_thread():
    # join(timeout=T) は「全スレッド合計で T 秒待つ」予算であるべき。各スレッドへ順に T を
    # 渡すと、stop() を無視して tick.fn() 実行中のまま居座るスレッドが複数あるとき、合計の
    # 待ち時間がスレッド数倍に膨らむ。各 tick を join の予算より十分長く居座らせ、per-thread
    # 実装なら 3 スレッド×0.3s≈0.9s、締切共有なら≈0.3s になる差で両者を区別する。
    release = threading.Event()

    def clinging_tick():
        release.wait(5.0)   # join の予算(0.3s)より十分長く実行中のまま居座る

    sched = Scheduler(
        [Tick("cling1", period=0.01, fn=clinging_tick),
         Tick("cling2", period=0.01, fn=clinging_tick),
         Tick("cling3", period=0.01, fn=clinging_tick)],
        watchdog_timeout=10,
    )
    sched.start()
    time.sleep(0.1)   # 各 tick が clinging_tick 実行中になるのを待つ
    sched.stop()
    started = time.monotonic()
    sched.join(timeout=0.3)
    elapsed = time.monotonic() - started
    release.set()   # 居座らせたスレッドを解放（daemon だが後始末として明示的に畳む）
    # per-thread なら 3 スレッド×0.3s≈0.9s。全体予算 0.3s に収まっていることを確認。
    assert elapsed < 0.6, f"join(timeout=0.3) が全体予算ではなく毎スレッドに適用された（{elapsed:.2f}s）"


def test_self_watchdog_aborts_on_stall():
    aborted = threading.Event()

    def hang_tick():
        time.sleep(5)  # watchdog_timeout より十分長い

    sched = Scheduler(
        [Tick("hang", period=0.01, fn=hang_tick)],
        watchdog_timeout=0.1,
        abort_fn=aborted.set,
    )
    sched.start()
    assert aborted.wait(timeout=2), "watchdog がスタール検知で abort_fn を呼ぶはず"
    sched.stop()


# モジュール直下の `def test_*` を `unittest discover` に拾わせる（既定の収集は
# `unittest.TestCase` のサブクラスだけで、関数形式は黙って無視される）。
from _functest import module_load_tests  # noqa: E402

load_tests = module_load_tests(globals())


if __name__ == "__main__":
    test_single_flight_never_overlaps()
    test_exception_isolated_other_ticks_keep_running()
    test_long_period_healthy_tick_not_aborted()
    test_notifies_systemd_ready_on_start()
    test_watchdog_interval_follows_systemd_watchdog_usec()
    test_declared_tick_timeout_extends_watchdog_grace()
    test_duplicate_tick_names_rejected()
    test_join_timeout_is_a_total_budget_not_per_thread()
    test_self_watchdog_aborts_on_stall()
    print("ok")
