"""resident.worker — ノード直轄ワーカー: 板落札の実行をロール共通の場所へ一本化する
（設計 §4.2・§4.3、実装計画 W1-5）。

`resident.supervisor.Supervisor` は「常駐して死んだら再起動する」子（プロジェクトループ）の
ためのもの。板で落札した仕事（flow run・amigos 手番）は寿命モデルが違う——一過性で、
終わったら再起動しない。ここが持つのは「ノード全体の `max_concurrent` で同時実行数を
律速するキュー投入」だけで、実行手段（subprocess 起動でも関数呼び出しでもよい）は
呼び出し側が `WorkItem.run` に渡す。flow の落札（subprocess 起動）も amigos の落札
（`AmigoRunner.turn_once` 呼び出し）も同じ `NodeWorkerPool` に投入できる——「ロール共通」
の実体はこの型の共有そのもの。

計数はプロセス内カウンタ + **外部観測**（`external_inflight`）。常駐体はノード唯一の実行主体
ではない——設計 §1.3 C14 はスキル起動の単発実行（人が `agent-amigos run --once` を直接叩く）
との併走を明示的に許すため、プロセス内だけで数えるとノード全体の `max_concurrent` を超える。
外部観測は設計が言う「status/run ファイルからの導出」で、常駐体が
`resident_cli._external_amigos_inflight`（amigos が実行中だけ置く手番マーカーを読む）を
注入する。**観測対象は「走っているか」を表すファイルに限る**——在籍状態を表すファイル
（バスの `status/<who>.json` は手番が終わっても `working` のまま）を流用すると、終わった
仕事を走行中と誤読し、`submit` の二重実行回避が自分の次の仕事を永久に弾く。
`status()` の中身はそのまま `engine/status.json`（W1-6）へ転記される。

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

    def __init__(self, max_concurrent: "int | None", *, thread_factory=threading.Thread,
                 on_event: "Callable[[str, str, BaseException | None], None] | None" = None,
                 external_inflight: "Callable[[], set] | None" = None):
        """`external_inflight` は「このプールが起こしたのではないが、同じノードで走っている
        仕事」の id 集合を返す観測子（実装計画 W1-5「計数は status/run ファイルから導出」）。

        プロセス内カウンタだけだと、スキル起動の単発実行（人が `agent-amigos run --once` や
        `agent-flow run` を直接叩く。設計 §1.3 C14 が明示的に許す併走）が計数に入らず、
        ノード全体の `max_concurrent` を超える。id 空間は投入側と揃える（重複は自分の分と
        みなして二重計上しない）。

        `max_concurrent` は **0 / None で「上限なし」**（板の語彙・P2-3）。以前は
        `max(1, int(...))` で 0 を 1 に潰しており、`board.schema.json` が
        「0/省略 = 無制限」と宣言しているのと真逆だった。**「未宣言なら 4」は呼び出し側の
        既定**で、ここの仕事ではない——プールが既定を持つと宣言を読む場所が 2 つになる。"""
        n = None if max_concurrent is None else int(max_concurrent)
        self._max = None if (n is None or n <= 0) else n
        self._thread_factory = thread_factory
        self._on_event = on_event or (lambda item_id, event, exc: None)
        self._external_inflight = external_inflight
        self._lock = threading.Lock()
        self._inflight: "dict[str, threading.Thread]" = {}
        self._queue: "list[WorkItem]" = []

    def _external_ids(self) -> set:
        """外部（このプール以外）で走っている仕事の id。呼び出し元は _lock 保持済み前提。
        観測に失敗したら空集合とみなす——外部観測の失敗で新規投入を止めると、常駐体が
        仕事をしなくなる方が害が大きい。"""
        if self._external_inflight is None:
            return set()
        try:
            return {str(x) for x in (self._external_inflight() or set())}
        except Exception:  # noqa: BLE001 — 観測の失敗はプールを止めない
            return set()

    def _used(self, external: "set | None" = None) -> int:
        """占有中のスロット数（自分の in-flight ∪ 外部で走っている分）。
        自分が起こした仕事も外部観測（手番マーカー）に現れるため、和集合で数えて
        二重計上しない。呼び出し元は _lock 保持済み前提（private）。

        `external` は既に観測済みの集合の使い回し。観測はファイル走査なので、1 回の
        判断の中で 2 度呼ばない（呼ぶと同じディレクトリを 2 度読む）。"""
        ext = self._external_ids() if external is None else external
        return len(set(self._inflight) | ext)

    def submit(self, item: WorkItem) -> bool:
        """空きがあれば即実行、無ければキューへ積む（`drain()` が空き次第拾う）。
        即実行できたら True。id が in-flight/キューに既にあれば二重投入せず False。"""
        with self._lock:
            self._reap()
            if item.id in self._inflight or any(q.id == item.id for q in self._queue):
                return False
            external = self._external_ids()
            if item.id in external:
                # 同じ仕事を**別プロセスが既に走らせている**（スキル起動の単発実行）。
                # 積むと外部の完了後に二重実行になるので、キューにも入れず捨てる
                # ——次の tick で外部が終わっていれば、そのとき改めて投入される。
                return False
            if self._max is not None and self._used(external) >= self._max:
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
            external = self._external_ids()      # ループ内で走査を繰り返さない
            while self._queue and (self._max is None or self._used(external) < self._max):
                self._start(self._queue.pop(0))
                started += 1
        return started

    def status(self) -> dict:
        with self._lock:
            self._reap()
            # `max_concurrent` は**板と同じ語彙で出す**（0 = 無制限）。engine/status.json を
            # 読む dashboard に、この画面だけの別語彙を覚えさせない。
            return {"inflight": sorted(self._inflight), "queued": len(self._queue),
                   "max_concurrent": self._max or 0, "used": self._used()}

    def busy_ids(self) -> "set[str]":
        """走っている ＋ 起動待ちの仕事 id。「この常駐体が面倒を見ている」集合であって、
        `status()["inflight"]`（走っている分だけ）とは違う。

        投入元へ「もう見ているので二重に寄越すな」と伝えるための集合。キューで待っている
        分を落とすと、投入元は毎周それを『誰も見ていない』と読んで再投入・再判断を繰り返す
        ——flow の受理では、待機中の run が孤児と誤判定されて再開回数を焼き切る。"""
        with self._lock:
            self._reap()
            return set(self._inflight) | {q.id for q in self._queue}

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
