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
import re

# ``` フェンスの中身（言語タグは任意）。道具ループを回すモデルは、成果物をフェンスに入れて
# その前後に作業報告を書く——**後置きの散文に角括弧が 1 つあるだけ**で、素朴な
# 「最初の `[` から最後の `]`」は散文ごと巻き込んで壊れる（実測 2026-08-30: map の
# 不合格 4/5 がこれ。どの出力にも正しい JSON 配列がフェンスの中にあった）。
_FENCE = re.compile(r"```[A-Za-z0-9_+-]*\s*\n(.*?)```", re.DOTALL)
# 行内のバッククォート引用。フェンスを使わず `["a","b"]` と本文へ埋めて返す癖があり、
# その周りの散文にも角括弧が出る（実測 2026-08-30: map の 1 本がこれで、中身は正しかった）。
_INLINE = re.compile(r"`([^`\n]+)`")


def _reparse(value):
    """二重エンコードを 1 段だけ剥がす。JSON 文字列の中身がまた JSON、という形が実際に出る
    （実測 2026-08-30: doctor の 1 本がフェンスの中へ `"[{...}]"` を書いた）。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _pick(blocks):
    """候補（フェンス／行内引用）から 1 つ選び `(値,)` で返す。1 つも無ければ None。

    **後ろほど優先**（道具ループでは途中経過が先・最終成果が最後に来る）、そのうえで
    **器（オブジェクト / 配列）を優先**する。器を見ずに最後の 1 つを採ると、末尾の
    引用文（ただの文字列）が本物の配列を隠す。
    """
    container = last = None
    for block in blocks:
        body = block.strip()
        if not body:
            continue
        try:
            value = _reparse(json.loads(body))
        except json.JSONDecodeError:
            continue
        last = (value,)
        if isinstance(value, (dict, list)):
            container = (value,)
    return container or last


# ツールループ CLI が本文の後ろへ足す制御封筒。**payload ではない**ので、本文に本物の
# 成果 JSON があるのにこれが後続すると、素朴な「最初の { から最後の }」は 2 つをまたいで
# 壊れる（実測 2026-08-30: amigos の conductor / acceptance / role-actions がこれで
# 全滅していた——モデルは正しい JSON を返していた）。
_CONTROL_KEYS = ({"ok"}, {"ok", "issues"})


def _is_control_envelope(value) -> bool:
    return isinstance(value, dict) and set(value) in _CONTROL_KEYS


def _scan(text: str):
    """連結された JSON 値を走査し、payload として使える最後の器を `(値,)` で返す。

    後ろほど優先するのは他の段と同じ（最終成果が最後に来る）。ただし制御封筒は飛ばす
    ——あれは CLI が足すものでモデルの成果ではない。1 つも無ければ None。
    """
    decoder = json.JSONDecoder()
    payload = control = None
    i, n = 0, len(text)
    while i < n:
        if text[i] not in "{[":
            i += 1
            continue
        try:
            value, end = decoder.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        # 読めた値の**中**は走査しない（`issues: ["x"]` のような入れ子を成果と取り違える）
        i = max(end, i + 1)
        if not isinstance(value, (dict, list)):
            continue
        if _is_control_envelope(value):
            control = (value,)
        else:
            payload = (value,)
    return payload or control


def extract_json(text: str, *, what: str = "エージェント出力"):
    """LLM 出力から JSON を寛容に取り出す。

    順に試す: (1) 素の `json.loads`、(2) ``` フェンスの中身（**最後に現れる**成功を採る
    ——道具ループでは途中経過が先に、最終成果が最後に来る）、(3) 最初の `{`／`[` から
    最後の `}`／`]` までの切り出し（フェンスが無い出力のための従来の寛容さ）。

    `what` は失敗時のメッセージにだけ使う。呼び出し側が「何の出力か」を言えると、
    ログを読む人が prompt を特定できる（agent-flow は "planner 出力"、agent-amigos は
    "エージェント出力"）。
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    picked = (_pick(_FENCE.findall(text or "")) or _pick(_INLINE.findall(text or ""))
              or _scan(text or ""))
    if picked is not None:
        return picked[0]
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
