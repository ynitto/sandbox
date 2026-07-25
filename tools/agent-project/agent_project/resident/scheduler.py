"""resident.scheduler — 常駐体の周期表ランナー（設計 §4.2、実装計画 W1-1）。

周期はコード定数（呼び出し側の `Tick.period`）で、yaml では変えない（設計 §2 原則5）。
各 tick 種別は専用スレッドで逐次ループするため single-flight は構造上自明（前回の呼び出し
中は次の呼び出しが始まらない＝重複起動しない）。tick 内の例外は隔離し、スケジューラ本体や
他 tick を道連れにしない。`Tick.timeout` は観測用の超過検知（ログ・callback）に留める —
実際の強制打ち切りは tick 側が持つサブプロセス（git 等）の timeout に委ねる。Python スレッドは
安全に kill できないため（設計 §4.2「git はサブプロセスなので kill で確実に打ち切れる」）。

self-watchdog: 各 tick の直近の呼び出し開始時刻を心拍として扱い、いずれかが
`watchdog_timeout` 秒を超えて更新されなければスケジューラ自身がハングしたとみなし、
自プロセスを abort する（起動系の再起動に乗る。設計 §4.2）。systemd 配下では
`NOTIFY_SOCKET` があれば毎 watchdog 周期で `WATCHDOG=1` も送る（二重の保険）。
"""
from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Tick:
    name: str
    period: float
    fn: "Callable[[], None]"
    timeout: "float | None" = None   # 観測用の超過検知のみ（強制打ち切りはしない）


class TickTimeout(Exception):
    """tick の呼び出しが `Tick.timeout` を超過した（観測用。呼び出しは止めない）。"""


class Scheduler:
    """周期表を回す常駐体本体。`start()` で tick ごとのスレッドと watchdog スレッドを
    起動し、`stop()` + `join()` で終える。"""

    def __init__(self, ticks: "list[Tick]", *, watchdog_timeout: float = 300.0,
                 on_tick_error: "Callable[[str, BaseException], None] | None" = None,
                 abort_fn: "Callable[[], None] | None" = None):
        if not ticks:
            raise ValueError("ticks は 1 個以上必要")
        names = [t.name for t in ticks]
        if len(names) != len(set(names)):
            raise ValueError(f"tick 名が重複しています: {names}")
        self._ticks = list(ticks)
        self._watchdog_timeout = watchdog_timeout
        self._on_tick_error = on_tick_error
        self._abort_fn = abort_fn or (lambda: os._exit(1))  # noqa: SLF001 — 意図的（§4.2）
        self._stop = threading.Event()
        self._threads: "list[threading.Thread]" = []
        now = time.monotonic()
        self._last_alive = {t.name: now for t in ticks}
        self._lock = threading.Lock()

    def _report_error(self, name: str, exc: BaseException) -> None:
        if self._on_tick_error is None:
            return
        try:
            self._on_tick_error(name, exc)
        except Exception:  # noqa: BLE001 — エラー通知自体の失敗でスケジューラを止めない
            pass

    def _run_tick_once(self, tick: Tick) -> None:
        started = time.monotonic()
        try:
            tick.fn()
        except BaseException as e:  # noqa: BLE001 — tick の例外は隔離する（設計 §4.2）
            self._report_error(tick.name, e)
            return
        if tick.timeout is not None:
            elapsed = time.monotonic() - started
            if elapsed > tick.timeout:
                self._report_error(tick.name, TickTimeout(
                    f"{tick.name} が {tick.timeout}s を超過しました（実測 {elapsed:.1f}s）"))

    def _tick_loop(self, tick: Tick) -> None:
        while not self._stop.is_set():
            with self._lock:
                self._last_alive[tick.name] = time.monotonic()
            self._run_tick_once(tick)
            self._stop.wait(tick.period)

    def _notify_systemd(self) -> None:
        sock_path = os.environ.get("NOTIFY_SOCKET")
        if not sock_path:
            return
        addr = ("\0" + sock_path[1:]) if sock_path.startswith("@") else sock_path
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
                s.connect(addr)
                s.sendall(b"WATCHDOG=1")
        except OSError:
            pass

    def _watchdog_loop(self) -> None:
        interval = max(1.0, min(self._watchdog_timeout / 3, 30.0))
        while not self._stop.is_set():
            if self._stop.wait(interval):
                return
            with self._lock:
                now = time.monotonic()
                stalled = [name for name, ts in self._last_alive.items()
                          if now - ts > self._watchdog_timeout]
            if stalled:
                self._abort_fn()
                return
            self._notify_systemd()

    def start(self) -> None:
        for tick in self._ticks:
            th = threading.Thread(target=self._tick_loop, args=(tick,),
                                  name=f"tick-{tick.name}", daemon=True)
            th.start()
            self._threads.append(th)
        wd = threading.Thread(target=self._watchdog_loop, name="resident-watchdog", daemon=True)
        wd.start()
        self._threads.append(wd)

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: "float | None" = None) -> None:
        for th in self._threads:
            th.join(timeout)
