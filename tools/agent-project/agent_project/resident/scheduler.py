"""resident.scheduler — 常駐体の周期表ランナー（設計 §4.2、実装計画 W1-1）。

周期はコード定数（呼び出し側の `Tick.period`）で、yaml では変えない（設計 §2 原則5）。
各 tick 種別は専用スレッドで逐次ループするため single-flight は構造上自明（前回の呼び出し
中は次の呼び出しが始まらない＝重複起動しない）。tick 内の例外は隔離し、スケジューラ本体や
他 tick を道連れにしない。`Tick.timeout` は観測用の超過検知（ログ・callback）に留める —
実際の強制打ち切りは tick 側が持つサブプロセス（git 等）の timeout に委ねる。Python スレッドは
安全に kill できないため（設計 §4.2「git はサブプロセスなので kill で確実に打ち切れる」）。

self-watchdog: 各 tick の直近の呼び出し開始時刻を心拍として扱い、いずれかが
`period + watchdog_timeout` 秒を超えて更新されなければスケジューラ自身がハングしたとみなし、
自プロセスを abort する（起動系の再起動に乗る。設計 §4.2）。心拍は各巡の**開始時**にだけ
更新するため、健全な tick でも次巡までの静止時間は `period + fn 実行時間` になる。周期を
無視して固定 `watchdog_timeout` と比べると、周期の長い tick（gc・cleanup 等）を静止＝ハングと
誤検知して健全なプロセスを殺す。よって tick ごとに `period` を加えた猶予で判定する
（`watchdog_timeout` は「その周期で再点火すべき時刻からさらにこれだけ遅れたら死」の意味）。
systemd 配下では `NOTIFY_SOCKET` があれば毎 watchdog 周期で `WATCHDOG=1` も送る（二重の保険）。
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
    # 1 回の呼び出しに掛かってよい想定時間の上限。超過は観測（ログ・callback）に留め、強制
    # 打ち切りはしない。**self-watchdog の猶予にも足す**——正当に長い tick（例: gc が
    # プロジェクトごとに外部コマンドを逐次起動する）を「ハング」と誤判定して健全な
    # 常駐体を abort させないため。未指定は 0 として扱う。
    timeout: "float | None" = None


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
        # 猶予 = period（次の再点火まで正当に静止する時間）+ timeout（1 回の呼び出しに
        # 掛かってよい時間）+ watchdog_timeout（そこからさらに遅れたら死とみなす余裕）。
        self._grace = {t.name: t.period + float(t.timeout or 0.0) for t in ticks}
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
                # 猶予は tick ごとに period + timeout を上乗せする。健全でも「次巡までの
                # 静止（period）」と「1 回の呼び出しに掛かる時間（timeout）」の分は心拍が
                # 更新されないため、固定 watchdog_timeout だと周期の長い tick・正当に重い
                # tick を誤ってハング判定して健全な常駐体を abort させる。
                stalled = [name for name, ts in self._last_alive.items()
                          if now - ts > self._grace[name] + self._watchdog_timeout]
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
        """全スレッドの終了を待つ。`timeout` は呼び出し全体の予算（各スレッドへの
        個別の値ではない）— 単純に毎スレッドへ同じ timeout を渡すと合計の待ち時間が
        スレッド数倍に膨らむため、締切を共有して残り時間を配分する。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        for th in self._threads:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            th.join(remaining)
