"""受け渡し先のアダプタ。本体（取得・判定・状態・受け渡しの順序と再送）はこの口だけを知る。

アダプタは名前で登録する。本体が持つのは `command` だけで、ほかはプラグイン（`--plugin`）が登録する。
アダプタは次の 2 つの関数で表す:

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
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ..util import ConfigError

DONE = "done"
FAILED = "failed"


class SendError(Exception):
    """受け渡せなかった（時間をおいて同じ run_id で渡し直す）。"""


class Adapter(Protocol):
    timeout_sec: float

    def enqueue(self, run_id: str, workflow: str, params: dict[str, str], queue: str | None) -> str: ...

    def retry(self, run_id: str, workflow: str) -> None: ...

    def status(self, run_id: str, workflow: str) -> str | None: ...


@dataclass
class AdapterType:
    name: str
    parse: Callable[[dict, str], Any]
    create: Callable[[Any, Any], Adapter]
    provider: str          # どこが登録したか（"本体" / プラグイン名）


_REGISTRY: dict[str, AdapterType] = {}


def register(name: str, parse: Callable[[dict, str], Any], create: Callable[[Any, Any], Adapter],
             provider: str) -> None:
    if name in _REGISTRY and _REGISTRY[name].provider != provider:
        raise ConfigError(f"受け渡しのアダプタ {name} が 2 か所（{_REGISTRY[name].provider}・{provider}）で登録されています")
    _REGISTRY[name] = AdapterType(name, parse, create, provider)


def get(name: str) -> AdapterType:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "なし"
        raise ConfigError(f"受け渡しのアダプタ {name} がありません（使えるもの: {known}）。"
                          f"プラグインのアダプタなら --plugin {name} を付けて実行してください")
    return _REGISTRY[name]


def reset_plugins() -> None:
    """プラグインが登録したアダプタを外す（1 回の実行の始めに呼ぶ。指定されていないプラグインを使わせない）。"""
    for name in [n for n, t in _REGISTRY.items() if t.provider != "本体"]:
        del _REGISTRY[name]


def names() -> list[str]:
    return sorted(_REGISTRY)


def _builtin() -> None:
    from . import command
    register("command", command.parse, command.create, "本体")


_builtin()
