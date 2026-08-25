"""agentcore.llmjson — LLM の応答から JSON を寛容に取り出す、唯一の実装。

## なぜここに 1 実装を置くか

同じ「モデルが返した本文から JSON を取り出す」を engine が別々に持っていた:

- `agent_flow/util.py` … `extract_json` / `unwrap_list` / `extract_list`
- `agent_amigos/util.py` … `extract_json`（agent-flow の写し。違いは例外文だけ）
- `agent-tools/eval/engine.py` … 既に agent-flow へ委譲する薄い層（写しではない）

`hostenv` や `agentcli` と同型の問題である。寛容さの規則がツールによってずれると、
**同じモデル応答が engine によって通ったり落ちたりする**——しかも落ちた側は「モデルが
悪い」に見えるので、原因が最後まで分からない。

## ローカル LLM 固有の事情がここに集まっている

`unwrap_list` が要るのは、ollama の JSON モード（`--format json`）が**トップレベルを必ず
オブジェクトにする**ため。プロンプトで「配列だけ返せ」と書いても `{"data": [...]}` で返る
——engine 側の仕様であってモデルの能力ではない。受け側が厳密なままだと split は原理的に
契約を満たせず、形式修復リトライも必ず空振りして 1 回分の呼び出しを捨てる（C9・C10）。

`extract_list` が拾う `["a","b"], ["c","d"]`（外側の配列なし）は Thinking を使うモデルの癖。
どちらも「安いモデルほど手が掛かる」（コンセプト正典 §2-7）の具体形で、その手当てを
engine ごとに書き分けると調整が人手に落ちる。

設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §5.5。
"""
from __future__ import annotations

import json


def extract_json(text: str, *, what: str = "エージェント出力"):
    """LLM 出力から JSON を寛容に取り出す。

    素の `json.loads` で通ればそれを返し、駄目なら最初の `{`／`[` から最後の `}`／`]` までを
    切り出して試す（前置き・後置きの散文や ``` フェンスを跨いで拾うため）。

    `what` は失敗時のメッセージにだけ使う。呼び出し側が「何の出力か」を言えると、
    ログを読む人が prompt を特定できる（agent-flow は "planner 出力"、agent-amigos は
    "エージェント出力"）。
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opn, cls in (("{", "}"), ("[", "]")):
        i, j = text.find(opn), text.rfind(cls)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"{what}から JSON を抽出できませんでした")


def unwrap_list(data):
    """配列を求める契約で、配列 1 本を包んだオブジェクトを配列として受ける。

    剥がすのは**配列値がちょうど 1 つ**のときだけ——2 つ以上あるとどれが答えか決まらず、
    黙って別のリストを採ると分解対象を取り違える。それ以外は素通しする。
    """
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    return data
