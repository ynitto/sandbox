from __future__ import annotations
# statemachine.py — ステートマシン実行ハーネスへの**委譲層**。実装はここに無い。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# 正典は `agentcore.harness.statemachine`。理由と経緯は toolloop.py 冒頭を見よ
# （`_sm_*` も共有名前空間へ張らない——静かに効かないパッチを作らないため）。
#
# 設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §5。

from agentcore.harness import statemachine as _harness_statemachine  # noqa: E402,F401
# entry の `statemachine:` / `input:` の読み方は agent-herd・dashboard と同じ 1 実装。
from agentcore import loopentry as _loopentry  # noqa: E402,F401
