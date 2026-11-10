"""プラグインが足す「フィルタ」の登録先。本体はこの口だけを知る。

    フィルタ  テンプレートの {{ 値 | 名前: 引数 }} で使う関数。fn(value, arg) -> 値
              arg は「:」の後ろを JSON として読んだもの（無ければ None）。設定のどのテンプレートでも、
              call の URL でも使える

名前で登録し、使う時点で探す（--plugin を付け忘れたら、その時点で案内して止める）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .util import ConfigError


@dataclass
class _Entry:
    fn: Callable
    provider: str


_FILTERS: dict[str, _Entry] = {}


def add_filter(name: str, fn: Callable[[Any, Any], Any], provider: str) -> None:
    if not name.replace("_", "").replace(".", "").isalnum():
        raise ConfigError(f"フィルタの名前は英数字・_ . で書いてください: {name}")
    if name in _FILTERS and _FILTERS[name].provider != provider:
        raise ConfigError(f"フィルタ {name} が 2 か所（{_FILTERS[name].provider}・{provider}）で登録されています")
    _FILTERS[name] = _Entry(fn, provider)


def get_filter(name: str) -> Callable | None:
    e = _FILTERS.get(name)
    return e.fn if e else None


def reset_plugins() -> None:
    for name in [n for n, e in _FILTERS.items() if e.provider != "本体"]:
        del _FILTERS[name]
