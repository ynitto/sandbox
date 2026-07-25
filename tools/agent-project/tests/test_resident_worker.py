"""resident.worker（NodeWorkerPool）の単体テスト（実装計画 W1-5）。"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "agentcore"))

from agent_project.resident import NodeWorkerPool, WorkItem  # noqa: E402


def _blocker():
    """submit 直後に一斉起動を確認できるよう、外から解除するまで待つ WorkItem を作る。"""
    gate = threading.Event()
    started = threading.Event()

    def run():
        started.set()
        gate.wait(timeout=5)
    return run, gate, started


def test_submit_within_capacity_runs_immediately():
    pool = NodeWorkerPool(max_concurrent=2)
    done = threading.Event()
    accepted = pool.submit(WorkItem("a", lambda: done.set()))
    assert accepted
    assert done.wait(timeout=2)


def test_submit_over_capacity_queues_and_drain_starts_it():
    pool = NodeWorkerPool(max_concurrent=1)
    run1, gate1, started1 = _blocker()
    accepted1 = pool.submit(WorkItem("a", run1))
    assert accepted1
    assert started1.wait(timeout=2)

    done2 = threading.Event()
    accepted2 = pool.submit(WorkItem("b", lambda: done2.set()))
    assert not accepted2   # 満杯なのでキュー行き
    assert pool.status()["queued"] == 1

    gate1.set()   # a を終わらせる
    deadline = time.time() + 2
    while pool.status()["inflight"] and time.time() < deadline:
        time.sleep(0.02)

    started = pool.drain()
    assert started == 1
    assert done2.wait(timeout=2)


def test_max_concurrent_never_exceeded_under_burst():
    pool = NodeWorkerPool(max_concurrent=3)
    max_seen = {"n": 0}
    lock = threading.Lock()
    gate = threading.Event()

    def make_run():
        def run():
            with lock:
                max_seen["n"] = max(max_seen["n"], len(pool.status()["inflight"]))
            gate.wait(timeout=5)
        return run

    for i in range(10):
        pool.submit(WorkItem(f"w{i}", make_run()))
    time.sleep(0.2)
    assert len(pool.status()["inflight"]) <= 3
    assert pool.status()["queued"] == 7
    gate.set()
    deadline = time.time() + 2
    while pool.status()["inflight"] and time.time() < deadline:
        time.sleep(0.02)
    assert max_seen["n"] <= 3


def test_duplicate_id_rejected_while_inflight_or_queued():
    pool = NodeWorkerPool(max_concurrent=1)
    run1, gate1, started1 = _blocker()
    pool.submit(WorkItem("dup", run1))
    assert started1.wait(timeout=2)
    assert not pool.submit(WorkItem("dup", lambda: None))   # in-flight と同じ id
    gate1.set()


def test_failed_item_reported_via_on_event_without_killing_pool():
    events = []
    pool = NodeWorkerPool(max_concurrent=2,
                          on_event=lambda item_id, ev, exc: events.append((item_id, ev)))

    def boom():
        raise RuntimeError("boom")

    pool.submit(WorkItem("bad", boom))
    time.sleep(0.2)
    assert ("bad", "failed") in events

    done = threading.Event()
    assert pool.submit(WorkItem("good", lambda: done.set()))
    assert done.wait(timeout=2)


if __name__ == "__main__":
    test_submit_within_capacity_runs_immediately()
    test_submit_over_capacity_queues_and_drain_starts_it()
    test_max_concurrent_never_exceeded_under_burst()
    test_duplicate_id_rejected_while_inflight_or_queued()
    test_failed_item_reported_via_on_event_without_killing_pool()
    print("ok")
