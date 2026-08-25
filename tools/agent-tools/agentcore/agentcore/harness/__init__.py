"""agentcore.harness — ツールを持たない CLI に実行能力を供給するハーネス（移植）。

## これは何か

`agent_loop/toolloop.py` と `agent_loop/statemachine.py` の**移植**である。元は
agent_loop の「単一名前空間フラグメント合成」断片（`__init__.py` が共有名前空間へ順に
exec する方式）で、単体 import ができず、agent-loop のデーモン一式を介さないと使えなかった。

ここに移植したことで、tmux もデーモンも無しに `agent-herd harness …` から直接回せる。

## 移植であって移行ではない — 元は消していない

`agent_loop/` の断片は**そのまま残っている**。agent-loop は従来どおり自分の断片を使い、
コマンドも証跡も 1 バイトも変わらない。したがって現在この 2 つは**意図的な写し**である。

写しが黙ってずれるのが唯一の危険なので、`tests/test_harness_parity.py` が
**全 top-level 定義の AST を突き合わせて**一致を縛る。片方だけ直せば必ず落ちる
（agentcore が `hostenv` を 1 実装へ畳む前に `test_adapter_env_parity.py` がやっていたのと
同じ流儀）。将来どちらか 1 つへ寄せるとき、そのテストが安全網になる。

## 移植で変えたところ（3 点だけ）

本体は**逐語コピー**で、変えたのは断片が共有名前空間から借りていた名前の供給だけである
（借用は stdlib を除くと 4 つしか無かった）:

- `agent_home_subdir` … `_borrowed` へ逐語移植（`AGENT_HOME = ".agents"` ごと）
- `_import_agentcli` … agentcore の中にいるので `from agentcore import agentcli` に落ちる
- `_node_budget_record` … 既定は**何もしない**（agentcore に agent-loop の台帳は無い）
- `_control_policy_decision` … 既定は **None**（selection_policy 無し＝従来の pin/既定経路）

後ろ 2 つは :func:`agentcore.harness.set_hooks` で host が差し込める。差し込まない限り
記帳と control 解決は起こらない——「どこかの台帳へ黙って書く」より「書かない」を既定にする。

設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §5。
"""
from __future__ import annotations

from agentcore.harness._borrowed import set_hooks  # noqa: F401

__all__ = ["set_hooks"]
