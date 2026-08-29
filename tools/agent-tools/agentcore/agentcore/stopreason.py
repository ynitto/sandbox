"""agentcore.stopreason — 停止理由の単一定義。

「なぜ止まったか」は 3 層（`harness/toolloop` / `harness/statemachine` / `ollama_loop`）が
それぞれ判定していて、**判定は既にあるのに綴りが層ごとに違った**。同じ「空回りで止めた」を
`no_progress` と呼ぶ層と、無変更 write の拒否として例外にする層があり、読み手（agent-loop の
RESULT 行・dashboard・台帳）は層ごとに別の読み方を持たされていた。ここは語彙だけを 1 か所へ
集める（`vocab.py` が完了語彙に対してしているのと同じこと）。

**判定そのものは移していない。** 上限も反復検知も進展判定も、各層の既存実装がそのまま持つ
——同じ判定を 3 層に重複させないため（レビュー §3-8）。このモジュールが持つのは名前と、
「その名前は上位へ回す種類か」の 1 判定だけである。

読み方は 2 つに分かれる:

- **完了側**（`FINAL` / `VERIFIED` / `TERMINAL_STATE`）… 成果が出た。ok の根拠は従来どおり
  機械層の証跡ゲートであって、この名前ではない。
- **打ち切り側**（`ESCALATING`）… 成果が出ないまま止めた。**偽の完了にしない**のが目的で、
  上位の候補か人へ回すシグナルになる。
"""
from __future__ import annotations

# --- 完了側 ---------------------------------------------------------------
FINAL = "final"                        # モデルが final を返した
VERIFIED = "verified"                  # 機械層の受入が通ったので final を待たずに終えた
TERMINAL_STATE = "terminal_state"      # ステートマシンが終端ステートへ到達した

# --- 打ち切り側 -----------------------------------------------------------
MAX_ROUNDS = "max_rounds"              # 1 ステート内のツールループが呼び出し上限へ到達
MAX_STEPS = "max_steps"                # ステート遷移が上限へ到達
CHECK_EXHAUSTED = "check_exhausted"    # 宣言された検査の再投入を使い切った
NO_PROGRESS = "no_progress"            # 同じ操作が同じ結果で続いた（層ごとの既存検知）
CONTEXT_EXHAUSTED = "context_exhausted"  # 文脈が尽きた（黙って切り捨てさせない）
TOOL_DENIED = "tool_denied"            # 許可されていない道具を要求し続けた
RUN_ERROR = "run_error"                # 実行したコマンドが失敗したまま終わった
NO_COMMAND = "no_command"              # 規約どおりのコマンドを最後まで出さなかった
# 許可されていないファイルの変更。**まだどの層も配線していない**——証跡ゲートは受入条件が
# 名指しした path しか見ず、ハーネスは git を使わない。段 9b（git 差分観測）に依存する
# 名前を先に置くのは、配線する日に綴りを決め直さないためである（レビュー §3-9）。
OUT_OF_SCOPE = "out_of_scope"

COMPLETED = frozenset({FINAL, VERIFIED, TERMINAL_STATE})
ESCALATING = frozenset({MAX_ROUNDS, MAX_STEPS, CHECK_EXHAUSTED, NO_PROGRESS,
                        CONTEXT_EXHAUSTED, TOOL_DENIED, RUN_ERROR, NO_COMMAND,
                        OUT_OF_SCOPE})
ALL = frozenset(COMPLETED | ESCALATING)

# 既存の綴りからの写像。**層の内部語彙は変えない**（`ollama_loop` の `status` は replay と
# イベントの契約になっていて、改名すると過去のログが読めなくなる）。ここで正典へ寄せる。
_ALIASES = {
    "done": FINAL,
    "escalate": CHECK_EXHAUSTED,
}


def normalize(value: "str | None") -> str:
    """層が持っている綴りを正典の停止理由へ寄せる。未知の値は空文字（名乗らせない）。"""
    name = str(value or "").strip()
    name = _ALIASES.get(name, name)
    return name if name in ALL else ""


def is_escalating(value: "str | None") -> bool:
    """成果が出ないまま止めた側か（＝上位の候補か人へ回すべきか）。"""
    return normalize(value) in ESCALATING
