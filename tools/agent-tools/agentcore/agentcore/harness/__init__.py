"""agentcore.harness — ツールを持たない CLI に実行能力を供給するハーネス（正典）。

## これは何か

限定ツール契約（:mod:`~agentcore.harness.toolloop`）と、その上のステートマシン実行
（:mod:`~agentcore.harness.statemachine`）。**本文はこの 2 モジュールにしか無い。**

入口は 2 つある:

- `agent-herd harness …` … tmux もデーモンも設定ファイルも要らない素の実行
- `agent-loop run / statemachine …` … tmux の中で「動いている様子が見える」実行

`agent_loop/{toolloop,statemachine}.py` は**このモジュールへ委譲するだけの層**で、
実装は持たない。同じハーネスの 2 つの見せ方であって 2 実装ではない。

## ここへ来るまで（3 段）

1. **移植**（2026-08-25）… agent_loop の exec 合成断片を逐語コピーし、AST パリティ
   テストで写しのずれを縛った。tmux 無しで回せるようになったのはこの段。
2. **本文の共有** … 写しを畳み、本文を `_toolloop_body.py` というデータファイルにして
   agent_loop と agentcore の**両方が exec** した。import 委譲にしなかったのは、
   agent-loop のテストが共有名前空間を差し替えていたから（63 箇所）。
3. **委譲**（この形）… そのテストを `agentcore/tests/` と `harnesspatch` へ移し、
   agent_loop 側を**ただの import** にした。exec も写しもデータファイルも無くなり、
   traceback も `inspect.getsource` も素直に本文を指す。

設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §5。

## 継ぎ目 — 記帳と control 解決

本文が host 固有の状態に触るのはこの 2 つだけで、既定は**何もしない / None** である。
`agent-herd harness` は差し込まない（台帳も control も持たない単独実行）。agent-loop は
合成時に :func:`set_hooks` で自分の実装を差し込む——「どこかの台帳へ黙って書く」より
「書かない」を既定にする。
"""
from __future__ import annotations

from agentcore.harness._borrowed import set_hooks  # noqa: F401

__all__ = ["set_hooks"]
