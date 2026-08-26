"""ハーネスの名前を差し替えるときの作法を 1 か所に閉じる（テスト用・discover 対象外）。

ハーネスの本文は 2 つのモジュールに分かれて置かれ、`statemachine` は `toolloop` の名前を
import して使う。そのため「どちらの名前空間を差し替えれば効くか」は**綴りでは決まらない**。
決めるのは、その名前を読む関数がどちらの本文にあるかである:

    sm._sm_run_control is tl._tl_run_control     # 別名は import 時に束縛される
    sm._sm_run_control.__globals__ is vars(tl)   # 読む名前は toolloop 側にある

つまり `_sm_run_control` を差し替えるなら statemachine 側、その中が読む
`_TL_CONTROL_RETRIES` を差し替えるなら toolloop 側——という判断がテストのたびに要る。
間違えても mock は成功するので**静かに効かない**（本物の CLI を起動しにいく）。

そこでここでは、**その名前を持つモジュール全部**を同じ値で差し替える。agent_loop の
単一名前空間へ差し込んでいた頃と同じ意味になり、呼ぶ側は経路を知らなくてよい。

    with patch_harness("_tl_run_agent", side_effect=responses) as agent:
        ...
"""
from __future__ import annotations

import contextlib
from unittest import mock

from agentcore.harness import statemachine as sm
from agentcore.harness import toolloop as tl

_MODULES = (tl, sm)
_UNSET = object()


@contextlib.contextmanager
def patch_harness(name, new=_UNSET, **kwargs):
    """`name` を持つハーネスのモジュール全部を、同じ差し替え物へ向ける。

    `new` を渡せばその値（定数の差し替え）、省略すれば `kwargs` を与えた MagicMock。
    どちらの名前空間にも無い名前は綴り間違いとして弾く——存在しない名前を黙って
    差し替えると、テストが「効いているつもり」で通ってしまう。
    """
    targets = [module for module in _MODULES if hasattr(module, name)]
    if not targets:
        raise AttributeError(f"ハーネスにその名前は無い: {name}")
    if new is _UNSET:
        new = mock.MagicMock(**kwargs)
    elif kwargs:
        raise TypeError("差し替え物と mock の引数は同時に渡せない")
    with contextlib.ExitStack() as stack:
        for module in targets:
            stack.enter_context(mock.patch.object(module, name, new))
        yield new
