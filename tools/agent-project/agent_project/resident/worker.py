"""resident.worker — ノード直轄ワーカー: 板落札の実行をロール共通の場所へ一本化する
（設計 §4.2・§4.3、実装計画 W1-5）。

`resident.supervisor.Supervisor` は「常駐して死んだら再起動する」子（プロジェクトループ）の
ためのもの。板で落札した仕事（flow run・amigos 手番）は寿命モデルが違う——一過性で、
終わったら再起動しない。ここが持つのは「ノード全体の `max_concurrent` で同時実行数を
律速するキュー投入」だけで、実行手段（subprocess 起動でも関数呼び出しでもよい）は
呼び出し側が `WorkItem.run` に渡す。flow の落札（subprocess 起動）も amigos の落札
（`AmigoRunner.turn_once` 呼び出し）も同じ `NodeWorkerPool` に投入できる——「ロール共通」
の実体はこの型の共有そのもの。

計数はプロセス内カウンタ（このプールが唯一の実行主体なので十分正確）。設計が言う
「status/run ファイルからの導出」は外部観測者（dashboard・doctor）向けの可視化の話で、
`status()` の中身がそのまま `engine/status.json`（W1-6）に転記される想定。

flow・amigos への実配線（`_spawn_orchestrator` や `_run_turns` をこのプールへ差し替える
こと）は本モジュールの範囲外——各エンジンの並行実行安全性（同一ミッション内の複数ロールを
並行実行してよいか等）の監査を伴うため、常駐体本体の組み立て（W1-11）で行う。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable


@dataclass
class WorkItem:
    id: str
    run: "Callable[[], None]"


class NodeWorkerPool:
    """ノード全体の `max_concurrent` セマフォで律速するワーカープール。"""

    def __init__(self, max_concurrent: int, *, thread_factory=threading.Thread,
                 on_event: "Callable[[str, str, BaseException | None], None] | None" = None):
        self._max = max(1, int(max_concurrent))
        self._thread_factory = thread_factory
        self._on_event = on_event or (lambda item_id, event, exc: None)
        self._lock = threading.Lock()
        self._inflight: "dict[str, threading.Thread]" = {}
        self._queue: "list[WorkItem]" = []

    def submit(self, item: WorkItem) -> bool:
        """空きがあれば即実行、無ければキューへ積む（`drain()` が空き次第拾う）。
        即実行できたら True。id が in-flight/キューに既にあれば二重投入せず False。"""
        with self._lock:
            self._reap()
            if item.id in self._inflight or any(q.id == item.id for q in self._queue):
                return False
            if len(self._inflight) >= self._max:
                self._queue.append(item)
                return False
            self._start(item)
            return True

    def drain(self) -> int:
        """空きスロット分だけキューから拾って起動する。`resident.scheduler` の tick から
        定期的に呼ぶ想定。起動した件数を返す。"""
        started = 0
        with self._lock:
            self._reap()
            while self._queue and len(self._inflight) < self._max:
                self._start(self._queue.pop(0))
                started += 1
        return started

    def status(self) -> dict:
        with self._lock:
            self._reap()
            return {"inflight": sorted(self._inflight), "queued": len(self._queue),
                   "max_concurrent": self._max}

    def _reap(self) -> None:
        # 呼び出し元は _lock 保持済み前提（private）
        for item_id, th in list(self._inflight.items()):
            if not th.is_alive():
                del self._inflight[item_id]

    def _start(self, item: WorkItem) -> None:
        # 呼び出し元は _lock 保持済み前提（private）
        def _run() -> None:
            try:
                item.run()
            except BaseException as e:  # noqa: BLE001 — 1 件の失敗でプールを止めない
                self._on_event(item.id, "failed", e)
            else:
                self._on_event(item.id, "done", None)
        th = self._thread_factory(target=_run, name=f"worker-{item.id}", daemon=True)
        self._inflight[item.id] = th
        self._on_event(item.id, "started", None)
        th.start()
