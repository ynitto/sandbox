"""resident.scheduler の単体テスト（実装計画 W1-1）。"""
from __future__ import annotations

import sys
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


def test_duplicate_tick_names_rejected():
    try:
        Scheduler([Tick("x", period=1, fn=lambda: None), Tick("x", period=1, fn=lambda: None)])
    except ValueError:
        return
    raise AssertionError("重複 tick 名を拒否するべき")


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


if __name__ == "__main__":
    test_single_flight_never_overlaps()
    test_exception_isolated_other_ticks_keep_running()
    test_duplicate_tick_names_rejected()
    test_self_watchdog_aborts_on_stall()
    print("ok")
