"""受け渡し先のアダプタ。本体（取得・判定・状態・受け渡しの順序と再送）はこの口だけを知る。

アダプタは設定の `delivery.adapter` で選ぶモジュールで、次を持つ:

    parse(options: dict, where: str) -> spec       設定の delivery（adapter 以外の項目）を検査して返す
    create(spec, connections) -> Adapter           受け渡しの口を作る（connections は接続設定）

Adapter は次の 3 つと `timeout_sec` を持つ:

    enqueue(run_id, workflow, params, queue) -> "ok" | "duplicate"
        workflow を run_id で起動（またはキューに投入）する。同じ run_id が既にあれば "duplicate"。
        渡せなければ SendError。run_id は英数字・- _ の 64 文字以内（本体が作る受け渡しの ID）
    retry(run_id, workflow) -> None
        失敗した実行を同じ run_id のまま再実行させる。できなければ SendError
    status(run_id, workflow) -> "done" | "failed" | None
        後続処理の状態。分からない・まだ終わっていなければ None

本体は名前でモジュールを読み込むだけで、アダプタを import しない。`dagu` は別のパッケージ
（apiwatch_dagu）にあり、置いていなければ dagu アダプタだけが使えない。
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Protocol

from ..util import ConfigError

DONE = "done"
FAILED = "failed"

# 名前 → モジュール。外のパッケージのアダプタは "パッケージ.モジュール" をそのまま書いてもよい
BUILTIN = {
    "command": "apiwatch.adapters.command",
    "dagu": "apiwatch_dagu.adapter",
}


class SendError(Exception):
    """受け渡せなかった（時間をおいて同じ run_id で渡し直す）。"""


class Adapter(Protocol):
    timeout_sec: float

    def enqueue(self, run_id: str, workflow: str, params: dict[str, str], queue: str | None) -> str: ...

    def retry(self, run_id: str, workflow: str) -> None: ...

    def status(self, run_id: str, workflow: str) -> str | None: ...


def load_module(name: str) -> ModuleType:
    modname = BUILTIN.get(name, name)
    try:
        mod = importlib.import_module(modname)
    except ImportError as e:
        hint = "（apiwatch_dagu フォルダを api_watch.py と同じ場所に置いてください）" if name == "dagu" else ""
        raise ConfigError(f"受け渡しのアダプタ {name} を読み込めません: {e}{hint}") from e
    for attr in ("parse", "create"):
        if not hasattr(mod, attr):
            raise ConfigError(f"受け渡しのアダプタ {name}（{modname}）に {attr} がありません")
    return mod
