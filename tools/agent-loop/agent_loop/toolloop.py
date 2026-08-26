from __future__ import annotations
# toolloop.py — 限定ツール契約のハーネスへの**委譲層**。実装はここに無い。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# 正典は `agentcore.harness.toolloop`（agent-herd と共有する 1 実装）。以前はここに
# 1275 行の本文があり、次にその本文を agentcore のデータファイルから exec していた。
# どちらも「agent-loop のテストが共有名前空間を差し替える」ためで、そのテストを
# agentcore 側へ移した今は、ただ import すれば足りる。
#
# **`_tl_*` / `_sm_*` を共有名前空間へ張り直さない。** 張ると
# `mock.patch.object(agent_loop, "_tl_run_agent")` が「成功したのに効かない」——本物の
# CLI を起動しにいく静かな失敗——になる。張らなければ AttributeError で大声で落ちる。
# ハーネスの名前を使う側（scheduler / cliprofile / cli）はモジュール越しに呼ぶこと。
#
# 設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §5。

from agentcore import harness as _harness  # noqa: E402
from agentcore.harness import toolloop as _harness_toolloop  # noqa: E402,F401

# 継ぎ目（記帳・control 解決）は**呼び出しのたびに共有名前空間を引く**。関数オブジェクトを
# 直に渡すと、テストが `agent_loop._node_budget_record` を差し替えても効かなくなる。
_harness.set_hooks(
    node_budget_record=lambda *args, **kwargs: _node_budget_record(*args, **kwargs),
    control_policy_decision=lambda *args, **kwargs: _control_policy_decision(*args, **kwargs))
