"""プラグインの読み込み。プラグインはコマンド行の `--plugin` で指定したときだけ読み込む。

    --plugin dagu              同梱のプラグイン（plugins/dagu.py）
    --plugin /path/to/foo.py   ファイルを直接

プラグインは `register(api)` を持つ 1 つの Python ファイルで、api を通じて次を足す:

    api.add_adapter(name, parse, create)        受け渡しのアダプタ（apiwatch.adapters の口）
    api.add_command(name, fn, help) -> parser   サブコマンド。fn(cfg, store, args) -> 終了コード

本体はプラグインを import しない（指定されたときに読み込むだけ）。
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

from . import adapters
from .util import ConfigError

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PluginAPI:
    def __init__(self, plugin: str, add_command: Callable | None):
        self.plugin = plugin
        self._add_command = add_command

    def add_adapter(self, name: str, parse, create) -> None:
        adapters.register(name, parse, create, f"プラグイン {self.plugin}")

    def add_command(self, name: str, fn, help: str):
        if self._add_command is None:
            raise ConfigError(f"プラグイン {self.plugin}: ここではコマンドを足せません")
        return self._add_command(name, fn, help)


def _import(spec: str) -> ModuleType:
    if spec.endswith(".py") or "/" in spec or "\\" in spec:
        path = Path(spec).expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f"プラグインのファイルが見つかりません: {path}")
        modname = f"apiwatch_plugin_{re.sub(r'[^A-Za-z0-9_]', '_', path.stem)}"
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
    if not _NAME.match(spec):
        raise ConfigError(f"プラグインの名前が正しくありません: {spec}")
    try:
        return importlib.import_module(f"plugins.{spec}")   # 同梱のもの（api_watch.py / .pyz と同じ場所の plugins/）
    except ModuleNotFoundError as e:
        if e.name not in ("plugins", f"plugins.{spec}"):
            raise
        raise ConfigError(f"同梱のプラグイン {spec} がありません（plugins/{spec}.py）") from e


def load(specs: list[str], add_command: Callable | None) -> list[ModuleType]:
    loaded = []
    for spec in dict.fromkeys(specs):
        mod = _import(spec)
        if not hasattr(mod, "register"):
            raise ConfigError(f"プラグイン {spec} に register(api) がありません")
        mod.register(PluginAPI(spec, add_command))
        loaded.append(mod)
    return loaded
