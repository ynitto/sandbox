"""プラグインの読み込み。プラグインはコマンド行の `--plugin` で指定したときだけ読み込む。

    --plugin dagu              同梱のプラグイン（changewatch/plugins/dagu.py）
    --plugin /path/to/foo.py   ファイルを直接

プラグインは `register(api)` を持つ 1 つの Python ファイルで、api を通じて次を足す:

    api.add_adapter(name, parse, create)        受け渡しのアダプタ（changewatch.adapters の口）
    api.add_command(name, fn, help) -> parser   サブコマンド。fn(cfg, store, args) -> 終了コード
    api.add_filter(name, fn)                    テンプレートのフィルタ。fn(value, arg) -> 値（changewatch.hooks）
    api.add_check(name, fn)                     受け渡し先の実行の前後の判定。fn(ctx: CheckContext) -> bool（changewatch.hooks）

どの口も名前で登録し、本体は使う時点で名前から探す。プラグインは本体の内部（DB・取得の仕組み）に直接触らず、
チェックの HTTP も CheckContext.get_json を通す（取得元の接続・要求の間隔・失敗の待ちを分け合うため）。

このパッケージ（changewatch.plugins）は読み込みの仕組みだけを持ち、同梱のプラグインを import しない
（指定されたときに名前で読み込むだけ）。
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

from .. import adapters, hooks
from ..util import ConfigError

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PluginAPI:
    def __init__(self, plugin: str, add_command: Callable | None):
        self.plugin = plugin
        self._add_command = add_command

    def add_adapter(self, name: str, parse, create) -> None:
        adapters.register(name, parse, create, f"プラグイン {self.plugin}")

    def add_filter(self, name: str, fn) -> None:
        hooks.add_filter(name, fn, f"プラグイン {self.plugin}")

    def add_check(self, name: str, fn) -> None:
        hooks.add_check(name, fn, f"プラグイン {self.plugin}")

    def add_command(self, name: str, fn, help: str):
        if self._add_command is None:
            raise ConfigError(f"プラグイン {self.plugin}: ここではコマンドを足せません")
        return self._add_command(name, fn, help)


def _import(spec: str) -> ModuleType:
    if spec.endswith(".py") or "/" in spec or "\\" in spec:
        path = Path(spec).expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f"プラグインのファイルが見つかりません: {path}")
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
    if not _NAME.match(spec):
        raise ConfigError(f"プラグインの名前が正しくありません: {spec}")
    try:
        return importlib.import_module(f"{__name__}.{spec}")   # 同梱のもの（changewatch/plugins/<名前>.py）
    except ModuleNotFoundError as e:
        if e.name != f"{__name__}.{spec}":
            raise
        raise ConfigError(f"同梱のプラグイン {spec} がありません（changewatch/plugins/{spec}.py）") from e


def load(specs: list[str], add_command: Callable | None) -> list[ModuleType]:
    loaded = []
    for spec in dict.fromkeys(specs):
        mod = _import(spec)
        if not hasattr(mod, "register"):
            raise ConfigError(f"プラグイン {spec} に register(api) がありません")
        mod.register(PluginAPI(spec, add_command))
        loaded.append(mod)
    return loaded
