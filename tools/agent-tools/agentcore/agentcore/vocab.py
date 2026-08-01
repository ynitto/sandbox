"""agentcore.vocab — 完了語彙の単一定義。

flow / amigos / project / board で綴りが割れていた完了語彙（`canceled` 米式 と
`cancelled` 英式）を 1 箇所に統一する（設計 §1.1・R1）。以後どのツールも
このモジュールの定数だけを使い、独自の終端集合・翻訳マップ・二重 endswith 判定は持たない。

**書く語彙と読む語彙は別**である点に注意する:

- 書き込みは常に正典（`TERMINAL` / `CANCELLED`）のみ。翻訳マップは持たない。
- 読み取りは `TERMINAL_READ`（正典 + 旧綴り `canceled`）を使う。W0-9 の改称は静止点で
  一斉に行うが、**バス上に既にある過去の run の meta.json は書き換わらない**。旧綴りを
  終端と認めないと、改称前に人が cancel した run が「非終端＝実行中」に見え、
  active_runs → 孤児回収 → failed 化（さらに auto-heal 経路）で蘇る。旧リビジョンへの
  バイナリ戻し（実装計画 §0-5）でも、新綴りで書かれた run を旧実装が同様に誤読するため、
  読み取り側の寛容さは移行期の両方向で効く。
"""
from __future__ import annotations

DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
# 旧・米式綴り。**書かない**（読み取り互換のためだけに残す。W0-9 以前のバス上の run が持つ）。
CANCELLED_LEGACY = "canceled"

TERMINAL = frozenset({DONE, FAILED, CANCELLED})
TERMINAL_READ = frozenset(TERMINAL | {CANCELLED_LEGACY})


def is_terminal(status: "str | None") -> bool:
    """status が正典の終端語彙（done/failed/cancelled）のいずれかか（書き手側の判定）。"""
    return str(status or "") in TERMINAL


def is_terminal_read(status: "str | None") -> bool:
    """既存データを読むときの終端判定（正典 + 旧綴り `canceled`）。"""
    return str(status or "") in TERMINAL_READ
