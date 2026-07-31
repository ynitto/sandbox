"""resident.gc — gc tick: 登録済みスイーパーを順に呼び、結果を集約する
（設計 §4.2、実装計画 W1-6）。

**掃除の実装はここに持たない**（R1: 重複実装の根絶）。agent-flow には既に
`run_cleanup`/`cmd_gc`（ロック・tmp・孤立クローン・古い run の掃除）が実装済みで、
ここで作り直すと「同じ仕事をするコードが複数箇所に別々に書かれる」という、この
プロジェクト全体が消そうとしている問題をこのモジュール自身が再演してしまう。
`run_gc()` は各エンジン・板・agentcore の既存掃除関数を薄いラッパ（`(名前, 引数無し
callable)` の組）として受け取り、順に呼んで例外を隔離するだけの orchestration。"""
from __future__ import annotations

from typing import Callable


def run_gc(sweepers: "list[tuple[str, Callable[[], dict | None]]]",
          *, on_event: "Callable[[str, str, BaseException | None], None] | None" = None
          ) -> dict:
    """1 巡分の gc: `(名前, スイーパー)` を順に呼び、返ってきた `{項目: 件数}` を
    `<名前>.<項目>` キーで集約する。1 件の失敗は隔離し、残りのスイーパーは
    続行する（tick の実行規約 — 設計 §4.2「例外は tick 内に隔離しループを殺さない」）。

    名前を呼び出し側から受け取るのは、スイーパーが既存関数への薄いラッパ
    （`lambda: run_cleanup(bus, ...)`）になるため。`__name__` から採ると全て `<lambda>` に
    潰れ、集計キーが混ざって「どのエンジンの掃除か」も失敗の特定もできなくなる。"""
    totals: dict = {}
    for name, sweep in sweepers:
        try:
            result = sweep() or {}
        except BaseException as e:  # noqa: BLE001 — 1 スイーパーの失敗で gc 全体を止めない
            if on_event:
                on_event(name, "failed", e)
            continue
        if on_event:
            on_event(name, "ok", None)
        if isinstance(result, dict):
            for k, v in result.items():
                key = f"{name}.{k}"
                totals[key] = totals.get(key, 0) + v
    return totals
