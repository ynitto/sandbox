"""agentcore.limits — 呼び出し回数の上限の単一定義（計画 2026-08-29 A1 / レビュー P1）。

上限そのものは 3 層（`harness/toolloop` / `harness/statemachine` / `ollama_loop`）に定数として
既にあった。無かったのは**設定可能にする口**で、腕を引くたびにワークフローや CLI 定義を
書き換えると「どの上限で切られたのか」が後から読めなくなる。ここが持つのは決め方 1 つだけで、
判定（何周したら止めるか）は各層の既存実装のままである——同じ判定を層へ重複させない。

決まり方は **宣言 ＞ 環境変数 ＞ 層の既定**。

- **宣言**は運用の意思（statemachine の state キー `max_tool_rounds`、`agent-ollama` の
  `--max-rounds`、CLI 定義の `write_args`）。書いてあれば必ず勝つ。
- **環境変数**は測定の条件。`AGENT_MAX_TOOL_ROUNDS_WRITE` は**編集する周だけ**に効き、
  `AGENT_MAX_TOOL_ROUNDS` は全部に効く。分けてあるのは、小型モデルの推奨帯（編集は 2〜3 周）が
  編集の周にだけ効く帯だからである——read 系・判定系まで一律に締めると調査の周が足りなくなる。
- **層の既定**は変えていない（toolloop 8 / ollama_loop 12）。

`vocab.py`（完了語彙）・`stopreason.py`（停止理由）と同じ形の、名前と決め方だけを持つ層である。
"""
from __future__ import annotations

import os

MAX_ROUNDS_ENV = "AGENT_MAX_TOOL_ROUNDS"
MAX_ROUNDS_WRITE_ENV = "AGENT_MAX_TOOL_ROUNDS_WRITE"
# toolloop / statemachine の既定。ここは「1 ステートのツールループ」の周回数。
DEFAULT_TOOL_ROUNDS = 8


def positive_int(value: "str | int | None") -> "int | None":
    """1 以上の整数として読めれば返す。読めない・0 以下は「宣言なし」と同じ扱い。"""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return number if number >= 1 else None


def max_rounds(declared: "str | int | None" = None, *, write: bool = False,
               default: "int | None" = None) -> int:
    """このループで許すモデル呼び出し回数（上の優先順）。

    `write` は「その周が編集しうるか」。statemachine では `write:` を宣言したステート、
    `agent-ollama` では書き込める toolset を指す。`default` は呼び手の層が持つ既定。
    """
    for candidate in (declared,
                      os.environ.get(MAX_ROUNDS_WRITE_ENV) if write else None,
                      os.environ.get(MAX_ROUNDS_ENV)):
        rounds = positive_int(candidate)
        if rounds is not None:
            return rounds
    return positive_int(default) or DEFAULT_TOOL_ROUNDS
