"""プラグインの読み込み。プラグインはコマンド行の `--plugin` で指定したときだけ読み込む。

    --plugin /path/to/foo.py

プラグインは `register(api)` を持つ 1 つの Python ファイルで、api を通じて次を足す:

    api.add_command(name, fn, help) -> parser   サブコマンド。fn(cfg, store, args) -> 終了コード
    api.add_filter(name, fn)                    テンプレートのフィルタ。fn(value, arg) -> 値（changewatch.hooks）

どの口も名前で登録し、本体は使う時点で名前から探す。プラグインは本体の内部（DB・取得の仕組み）に直接触らない。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

from .. import hooks
from ..util import ConfigError


class PluginAPI:
    def __init__(self, plugin: str, add_command: Callable | None):
        self.plugin = plugin
        self._add_command = add_command

    def add_filter(self, name: str, fn) -> None:
        hooks.add_filter(name, fn, f"プラグイン {self.plugin}")

    def add_command(self, name: str, fn, help: str):
        if self._add_command is None:
            raise ConfigError(f"プラグイン {self.plugin}: ここではコマンドを足せません")
        return self._add_command(name, fn, help)


def _import(spec: str) -> ModuleType:
    path = Path(spec).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"プラグインのファイルが見つかりません: {path}（--plugin には .py のパスを書いてください）")
    modname = f"changewatch_plugin_{re.sub(r'[^A-Za-z0-9_]', '_', path.stem)}"
    mspec = importlib.util.spec_from_file_location(modname, path)
    if mspec is None or mspec.loader is None:
        raise ConfigError(f"プラグインを読み込めません: {path}")
    mod = importlib.util.module_from_spec(mspec)
    sys.modules[modname] = mod          # dataclass などは実行中に sys.modules から自分のモジュールを引く
    try:
        mspec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(modname, None)
        raise
    return mod


def load(specs: list[str], add_command: Callable | None) -> list[ModuleType]:
    loaded = []
    for spec in dict.fromkeys(specs):
        mod = _import(spec)
        if not hasattr(mod, "register"):
            raise ConfigError(f"プラグイン {spec} に register(api) がありません")
        mod.register(PluginAPI(spec, add_command))
        loaded.append(mod)
    return loaded
