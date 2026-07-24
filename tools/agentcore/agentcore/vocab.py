"""agentcore.vocab — 完了語彙の単一定義。

flow / amigos / project / board で綴りが割れていた完了語彙（`canceled` 米式 と
`cancelled` 英式）を 1 箇所に統一する（設計 §1.1・R1）。以後どのツールも
このモジュールの定数だけを使い、独自の終端集合・翻訳マップ・二重 endswith 判定は持たない。
"""
from __future__ import annotations

DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL = frozenset({DONE, FAILED, CANCELLED})


def is_terminal(status: "str | None") -> bool:
    """status が終端語彙（done/failed/cancelled）のいずれかか。"""
    return str(status or "") in TERMINAL
