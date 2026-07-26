"""resident.supervisor の単体テスト（実装計画 W1-4）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "agentcore"))

from agent_project.resident import ChildSpec, Supervisor, graceful_shutdown  # noqa: E402


def _argv_exit(code: int) -> "list[str]":
    return [sys.executable, "-c", f"import sys; sys.exit({code})"]


def _argv_sleep(sec: float) -> "list[str]":
    return [sys.executable, "-c", f"import time; time.sleep({sec})"]


def _argv_ignore_sigterm_then_sleep(sec: float) -> "list[str]":
    return [sys.executable, "-c",
           f"import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
           f"time.sleep({sec})"]


def test_start_and_crash_is_restarted():
    sup = Supervisor()
    sup.add(ChildSpec("c1", _argv_exit(1), backoff_base=0.01, backoff_max=0.01))
    sup.start("c1")
    assert sup.status()["c1"]["alive"]
    sup.names()[0]
    # 死ぬまで待つ
    deadline = time.time() + 5
    while sup.status()["c1"]["alive"] and time.time() < deadline:
        time.sleep(0.05)
    assert not sup.status()["c1"]["alive"]
    # 1 回目の check_health は死亡を検知して backoff を武装するだけ（この tick では再起動しない）。
    sup.check_health()
    assert not sup.status()["c1"]["alive"]
    time.sleep(0.02)   # backoff（0.01s）を超えるまで待つ
    sup.check_health()   # 2 回目で backoff 経過を見て再起動
    assert sup.status()["c1"]["alive"], "backoff 後に自動再起動するはず"
    sup.stop("c1")


def test_hang_detected_via_is_healthy_and_restarted():
    healthy = {"v": True}
    sup = Supervisor()
    sup.add(ChildSpec("c1", _argv_sleep(30), is_healthy=lambda: healthy["v"],
                      backoff_base=0.01, backoff_max=0.01, stop_grace=1.0))
    sup.start("c1")
    assert sup.status()["c1"]["alive"]
    healthy["v"] = False   # ハングを模擬
    sup.check_health()     # 検知して kill（この tick では backoff 武装のみ・再起動しない）
    assert not sup.status()["c1"]["alive"]
    healthy["v"] = True
    time.sleep(0.02)       # backoff（0.01s）を超えるまで待つ
    sup.check_health()     # backoff 経過を見て再起動
    assert sup.status()["c1"]["alive"]
    sup.stop("c1")


def test_quarantine_after_repeated_deaths():
    # check_health は「死亡検知 → backoff 武装」と「backoff 経過後の再起動」を別 tick で行う
    # （resident.scheduler の周期呼び出しを模す）。小刻みに呼び続けて収束させる。
    events = []
    sup = Supervisor(on_event=lambda name, ev: events.append((name, ev)))
    sup.add(ChildSpec("c1", _argv_exit(1), backoff_base=0.001, backoff_max=0.001,
                      quarantine_after=2, quarantine_window=600.0))
    sup.start("c1")
    deadline = time.time() + 10
    while not sup.status()["c1"]["quarantined"] and time.time() < deadline:
        time.sleep(0.02)
        sup.check_health()
    assert sup.status()["c1"]["quarantined"]
    assert ("c1", "quarantined") in events

    # 隔離中は check_health を呼んでも再起動しない
    sup.check_health()
    assert not sup.status()["c1"]["alive"]

    sup.unquarantine("c1")
    assert not sup.status()["c1"]["quarantined"]
    for _ in range(10):
        sup.check_health()
        if sup.status()["c1"]["alive"]:
            break
        time.sleep(0.02)
    assert sup.status()["c1"]["alive"], "隔離解除後は再起動できる"
    sup.stop("c1")


def test_stop_sends_sigterm_and_escalates_to_sigkill_on_ignore():
    sup = Supervisor()
    sup.add(ChildSpec("stubborn", _argv_ignore_sigterm_then_sleep(30), stop_grace=0.3))
    sup.start("stubborn")
    assert sup.status()["stubborn"]["alive"]
    t0 = time.time()
    sup.stop("stubborn")
    elapsed = time.time() - t0
    assert not sup.status()["stubborn"]["alive"]
    assert elapsed < 3.0, "SIGTERM を無視する子は stop_grace 後に SIGKILL で確実に落ちるはず"


def test_double_start_does_not_spawn_second_process():
    sup = Supervisor()
    sup.add(ChildSpec("c1", _argv_sleep(30)))
    sup.start("c1")
    first_pid = sup._children["c1"].proc.pid  # noqa: SLF001 — テスト内部確認
    sup.start("c1")   # 生きているので何もしない
    assert sup._children["c1"].proc.pid == first_pid  # noqa: SLF001
    sup.stop("c1")


def test_graceful_shutdown_sequences_all_steps_after_stopping_children():
    sup = Supervisor()
    sup.add(ChildSpec("c1", _argv_sleep(30)))
    sup.start("c1")
    order = []
    graceful_shutdown(
        sup,
        release_claims=lambda: order.append("claims"),
        release_lease=lambda: order.append("lease"),
        announce_away=lambda: order.append("away"),
        final_push=lambda: order.append("push"),
    )
    assert order == ["claims", "lease", "away", "push"]
    assert not sup.status()["c1"]["alive"], "子を止めてから後始末するはず"


def test_graceful_shutdown_skips_none_steps():
    sup = Supervisor()
    sup.add(ChildSpec("c1", _argv_exit(0)))
    sup.start("c1")
    time.sleep(0.2)
    graceful_shutdown(sup)   # 全ステップ省略でも例外にならない


# モジュール直下の `def test_*` を `unittest discover` に拾わせる（既定の収集は
# `unittest.TestCase` のサブクラスだけで、関数形式は黙って無視される）。
from _functest import module_load_tests  # noqa: E402

load_tests = module_load_tests(globals())


if __name__ == "__main__":
    test_start_and_crash_is_restarted()
    test_hang_detected_via_is_healthy_and_restarted()
    test_quarantine_after_repeated_deaths()
    test_stop_sends_sigterm_and_escalates_to_sigkill_on_ignore()
    test_double_start_does_not_spawn_second_process()
    test_graceful_shutdown_sequences_all_steps_after_stopping_children()
    test_graceful_shutdown_skips_none_steps()
    print("ok")
