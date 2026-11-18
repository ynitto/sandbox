"""プラグインの読み込み。プラグインはコマンド行の `--plugin` で指定したときだけ読み込む。

    --plugin /path/to/foo.py       .py のパス
    --plugin foo                   名前（foo か foo.py）。入れてあるエージェントのプログラムから探す（agents/<エージェント>/foo.py）

プラグインは `register(api)` を持つ 1 つの Python ファイルで、api を通じて次を足す:

    api.add_command(name, fn, help) -> parser   サブコマンド。fn(cfg, store, args) -> 終了コード
        needs_config=False を付けると、設定と状態を読まずに fn(None, None, args) を呼ぶ（--config が要らない。
        エージェントに固有の、書くものを作るだけのコマンドなど）。返る parser に引数（やサブコマンド）を足す
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

    def add_command(self, name: str, fn, help: str, needs_config: bool = True):
        if self._add_command is None:
            raise ConfigError(f"プラグイン {self.plugin}: ここではコマンドを足せません")
        return self._add_command(name, fn, help, needs_config)


def agent_dirs() -> list[Path]:
    """エージェントのプログラムの置き場所（install.py install --agent が置くところ）。
    change-watch のとなりの agents/（Windows と、ソースの木）と、~/.local/share/change-watch/agents/（Linux など）。"""
    root = Path(__file__).resolve().parents[2]          # ソースの木なら tools/change-watch、1 ファイルなら change-watch.pyz
    beside = (root.parent if root.is_file() else root) / "agents"
    return list(dict.fromkeys([beside, Path.home() / ".local" / "share" / "change-watch" / "agents"]))


def resolve(spec: str) -> Path:
    """--plugin の値を .py のパスにする。パスでなければ、エージェントのプログラムの名前として探す。"""
    path = Path(spec).expanduser()
    if path.is_file():
        return path.resolve()
    if "/" in spec or "\\" in spec:
        raise ConfigError(f"プラグインのファイルが見つかりません: {path.resolve()}")
    name = spec if spec.endswith(".py") else spec + ".py"
    dirs = agent_dirs()
    for d in dirs:                                       # 先に見つかった置き場所のものを使う
        found = sorted(d.glob(f"*/{name}"))
        if len(found) > 1:
            raise ConfigError(f"プラグイン {spec} が 2 つ以上あります（{'・'.join(str(f) for f in found)}）。パスで書いてください")
        if found:
            return found[0].resolve()
    raise ConfigError(f"プラグイン {spec} が見つかりません（{'・'.join(str(d) for d in dirs)} の <エージェント>/{name} を探しました。"
                      "--plugin には、入れてあるエージェントのプログラムの名前か .py のパスを書いてください）")


def _import(spec: str) -> ModuleType:
    path = resolve(spec)
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
        mod = _import(str(spec))
        if not hasattr(mod, "register"):
            raise ConfigError(f"プラグイン {spec} に register(api) がありません")
        mod.register(PluginAPI(spec, add_command))
        loaded.append(mod)
    return loaded
