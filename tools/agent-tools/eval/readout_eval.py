#!/usr/bin/env python3
"""agent-herd judge（1 トークン目の分布の読み出し）で既存セルを引き直し、確度の当たり方を測る。

judge_eval.py とは**別物**なので、ファイルも台帳も分ける——あちらの「judge」は評価ハーネス
上の役割名（`kind="judge"` の J1 / J2 セル）で、こちらは `agentcore.judge`（選択肢の上の確率
分布を返す判断 AI）の読み出し経路。混ぜると台帳の行がどちらの judge なのか読めなくなる。

既定の旧モードは設計 §6 の次の2つを測る。`--calibration`では問い単位の分布を
保存し、method別Brier/ECE・threshold sweep・棄権・失敗・usageまで集計する。
`--fake-run`と`--replay`はOllama不要で同じreport schemaを出す。詳細はeval README。

旧モードの集計:

1. `coverage` の分布。ラベルに落ちた質量が低い（< 0.8）割合。低ければプロンプトの形の問題で、
   確率を信じる前にそちらを直す。
2. `confidence` の区間ごとの正答率（信頼度図）。`--min-confidence` の既定の根拠になる。

ケースの入力と正解は既存セルから**借りる**（写さない）。judge_eval の F1 / J2 / CL1 / E1〜E6、
project_eval の RO1〜RO3——RO は judge_eval でなく project_eval にある。合否はそのセルの
`check` に通して決めるので、正解はこのファイルのどこにも書かない。このファイルが持つのは
「同じ入力をどう**問い**の形にするか」だけで、route は本番の問い（`_route_judge_questions`）を
そのまま呼ぶ。

旧モードはollama に届かない環境では 1 件も走らせず、台帳へ何も書かずに終了コード 0 で終わる。CI は
ollama を持たないので、**測っていないものを数字として残さない**（設計 §6 が「実測は未着手」と
書いているのと同じ作法）。

使い方: python3 readout_eval.py [--model gemma4:e4b] [--repeat 3] [--cases F1,RO1]
設計: docs/plans/2026-09-19-agent-herd-system-one-judge-design.md §4 / §6。
"""
from __future__ import annotations

import argparse
import functools
import importlib
import math
import itertools
import math
import subprocess
import shlex
import hashlib
from datetime import datetime, timezone
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))
sys.path.insert(0, str(REPO / "tools/agent-flow"))

from agentcore import judge, ollama_loop  # noqa: E402

LEDGER_DIR = Path(os.environ.get("READOUT_EVAL_DIR", "/tmp/agent-readout-eval"))
MODEL = judge.DEFAULT_MODEL
# ラベルに落ちた質量の下限。これを割る回が多ければ、確率ではなくプロンプトの形を直す（§6 の 1）。
COVERAGE_FLOOR = 0.8
BINS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))


# --------------------------------------------------------------------------- 問いの立て方
# 既存セルの入力（goal / deps / results / task）を judge の問いへ写す。**正解は写さない**
# ——合否はセルの `check` がつける。

# 候補の説明から依存の話を落とす（「pandas を追加して 30 行」→「30 行」）。素材そのものは
# 書き換えない——依存の属性は F2 / J1 / F2P / J1P の**正解**（`extra_deps`）で、消すとその
# 4 セルが測れなくなる。F1 の基準は「テストが通っている」だけなので、状態の側で落として
# 引いたときに確度が上がるかだけを見る。
_DEPS_CLAUSE = re.compile(r"(?:\S+ を追加して|標準ライブラリのみで)\s*")


def _filter_cell(case: dict, *, drop_deps: bool = False):
    """filter: 候補 1 件 = boolean の問い 1 つ（本番 agent-flow の `filter_judge` と同じ立て方）。"""
    text = case["deps"]["gen"]["output"]
    if drop_deps:
        text = _DEPS_CLAUSE.sub("", text)
    ids = re.findall(r"^\[([^\]]+)\]", text, re.M)
    criterion = " ".join(str(case["goal"]).split())
    questions = {i: {"type": "boolean",
                     "instructions": f"Does candidate [{i}] satisfy this criterion? {criterion}"}
                 for i in ids}
    return ("Candidates:\n" + text, questions,
            lambda answers: [i for i in ids if answers[i].get("value")])


def _judge_cell(case: dict):
    """judge: 候補の上の choice 1 問。選択肢の説明は候補の行そのもの。"""
    text = case["deps"]["gen"]["output"]
    criteria = dict(re.findall(r"^\[([^\]]+)\]\s*(.+)$", text, re.M))
    questions = {"winner": {"type": "choice", "criteria": criteria,
                            "instructions": " ".join(str(case["goal"]).split())}}
    return text, questions, lambda answers: {"winner": answers["winner"]["choice"]}


# 分類の 3 ラベルは**選択肢の空間**であって正解ではない（どれが正解かは `check` だけが知る）。
CLASSIFY_LABELS = {"bug": "不具合の報告（以前は動いていた・意図と違う）",
                   "feature": "機能の要望（無い機能を足してほしい）",
                   "question": "使い方の質問"}


def _classify_cell(case: dict):
    """classify: ラベルの上の choice 1 問。本番の下流が読む `class=<ラベル>` の形へ戻して渡す。"""
    questions = {"class": {"type": "choice", "criteria": CLASSIFY_LABELS,
                           "instructions": "この問い合わせはどの分類か。"}}
    return (str(case["goal"]), questions,
            lambda answers: f"class={answers['class']['choice']}")


DECISIONS = {"done": "要求を満たしており、これ以上の仕事は要らない",
             "replan": "足りない仕事があり、計画を足す必要がある"}


def _results_state(case: dict, *, closed: bool) -> str:
    """結果要約。1 行形式は本番（continuation.py）と同じ。

    `closed` は**閉世界を状態の側で明示する**——「ノードはこれがすべてで、ここに無い仕事は
    行われていない」。既定の状態はノードを並べるだけで、並んでいないものが**無い**とは
    言っていない。素材から含意を外してもモデルが実在しない段を「ある」と答え続けたので
    （2026-09-20）、その黙約を書き下して効くかを測る。
    """
    rows = [f"- {nid} ({kind}) [{status}]: {out[:160]}"
            for nid, kind, status, out in case["results"]]
    if not closed:
        return "\n".join(rows)
    return (f"このワークフローのノードは次の {len(rows)} 件がすべてで、"
            "ここに現れていない仕事は行われていない。\n" + "\n".join(rows))


def _evaluator_cell(case: dict, *, closed: bool = False):
    """evaluator: done / replan の choice 1 問。"""
    request = importlib.import_module("judge_eval").REQUEST
    state = _results_state(case, closed=closed)
    questions = {"decision": {"type": "choice", "criteria": DECISIONS,
                              "instructions": f"要求は「{request}」。この結果で要求を"
                                              "満たしたか、計画を足すべきか。"}}
    return state, questions, lambda answers: {"decision": answers["decision"]["choice"]}


def _request_stages(request: str) -> "list[str]":
    """要求の本文から段の名前を取り出す（「収集・集計・出力の 3 段で」→ 3 つ）。

    ここで段を書き写すと、要求を書き換えたときに問いだけが古いまま残る。件数が本文と
    食い違ったら、測らずに落とす。
    """
    found = re.search(r"([^。、]+?)の\s*(\d+)\s*段", request)
    if found is None:
        raise ValueError(f"要求から段を読めません: {request[:60]!r}")
    stages = [s for s in found.group(1).split("・") if s]
    if len(stages) != int(found.group(2)):
        raise ValueError(f"段の数が本文と食い違います: {stages}（本文は {found.group(2)} 段）")
    return stages


# 欠けている段を状態へ書き下す診断。**正解を入力に混ぜる**ので、これは能力の測定ではない。
# 「明示されても直らない」なら推測で埋めているのではなく読んだ上で上書きしている、という
# 別の話になる——その切り分けだけのために置く。台帳の行 id は `+told` で見分ける。
def _nodes(case: dict) -> "list[tuple[str, str, str, str]]":
    return [tuple(row) for row in case["results"]]


def _done_ids(case: dict) -> "set[str]":
    """段の成果を出しうるノード（`kind` が work で `status` が done）。

    `kind` も `status` も本番のグラフが持っている値で、モデルに訊く必要が無い——訊くのは
    「どの段か」だけにする。**verify ノードを外すのは後付けではなく、検証役は段の成果物を
    出さないから**である。初版はこの条件が抜けており、E1 の verify ノード（本文に
    「出力段が無いため」と書いてある）を「出力の段」と答えたモデルの回答が、そのまま
    「出力の段は done」として通っていた（2026-09-20 に判明）。
    """
    return {nid for nid, kind, status, _out in _nodes(case)
            if kind == "work" and status == "done"}


def _evaluator_locate_cell(case: dict):
    """段ごとに「その段をやったノードはどれか」を訊く（choice。`other` が「どれでもない」）。

    `+checklist` の boolean は「あるか」を訊くので、状態を読まずに要求から埋められる
    （2026-09-20 の E4 / E5 がそれ）。こちらは**状態に並んだノードを指させる**ので、
    答えるには行を読むしかない。判定は機械——どの段も done のノードを指していれば `done`、
    `other` を選んだ段があるか、指した先が done でなければ `replan`。
    """
    judge_eval = importlib.import_module("judge_eval")
    done = _done_ids(case)
    # 候補は work のノードだけ。検証役は段の成果物を出さないので、指させる先に置かない。
    criteria = {nid: f"[{status}] {out[:70]}" for nid, kind, status, out in _nodes(case)
                if kind == "work"}
    questions = {stage: {"type": "choice", "criteria": criteria,
                         "other": "どのノードもこの段をやっていない",
                         "instructions": f"要求は「{judge_eval.REQUEST}」。"
                                         f"このうち「{stage}」の段の成果を出したノードはどれか。"}
                 for stage in _request_stages(judge_eval.REQUEST)}
    return _results_state(case, closed=True), questions, lambda answers: {
        "decision": "done" if all(a.get("choice") in done for a in answers.values())
                    else "replan"}


def _evaluator_classify_cell(case: dict):
    """ノードごとに「どの段にあたるか」を訊き、**足りない段は機械が差集合で出す**。

    `+locate` はまだ段の側から「これをやったノードはあるか」と訊いていて、「あるはず」の
    構えが残る。こちらはモデルに欠落の話を一切させない——1 ノード 1 問で段を言わせるだけで、
    要求の段が揃っているかは機械が集合演算で決める（判定は機械・モデルは転記）。
    """
    judge_eval = importlib.import_module("judge_eval")
    stages = _request_stages(judge_eval.REQUEST)
    criteria = {stage: f"要求の「{stage}」の段" for stage in stages}
    done = _done_ids(case)
    questions = {nid: {"type": "choice", "criteria": criteria,
                       "other": "この 3 段のどれでもない（検証など）",
                       "instructions": f"要求は「{judge_eval.REQUEST}」。"
                                       f"ノード {nid} の成果は、このうちどの段にあたるか。"}
                 for nid, _kind, _status, _out in _nodes(case)}

    def to_check(answers):
        covered = {answers[nid]["choice"] for nid in answers if nid in done}
        return {"decision": "done" if set(stages) <= covered else "replan"}

    return _results_state(case, closed=True), questions, to_check


def _told_state(case: dict) -> str:
    """閉世界の状態に「その段のノードは無い」を足す（欠けている段は `expect` から読む）。

    段の名前は要求の本文から取り、そのどれを `expect` が名指しているかだけを見る。正解は
    judge_eval 側（`expect`）にあるままで、こちらへは写さない。段を名指していないケース
    （E1 / E2 / E6）では何も足さないので、`+checklist_closed` と同じ状態になる。
    """
    state = _results_state(case, closed=True)
    expect = str(case.get("expect") or "")
    missing = [s for s in _request_stages(importlib.import_module("judge_eval").REQUEST)
               if f"{s}段が無い" in expect]
    if not missing:
        return state
    return state + f"\nなお、「{missing[0]}」の段のノードはこのワークフローに無い。"


def _evaluator_told_cell(case: dict):
    """`+checklist_closed` と同じ問いを、欠けている段を明示した状態で引く（診断専用）。"""
    state, questions, to_check = _evaluator_checklist_cell(case, closed=True)
    return _told_state(case), questions, to_check


def _evaluator_stages_cell(case: dict, *, closed: bool = False):
    """evaluator のもう 1 つの問い方: 要求の段を**選択肢の側**へ出す。

    既定の問い（「この結果で要求を満たしたか」）は、全ノードが green なら要求の段が欠けて
    いても `done` へ倒れた（2026-09-20 の実測で E3〜E5 が 0/9）。モデルはノードの status
    しか読んでいない。こちらは段を選択肢に並べて「どの段の成果が出ていないか」を訊き、
    `done` 以外は機械が `replan` へ畳む——**モデルには段の有無だけを言わせ、判定は機械が
    決める**。段の名前は要求の本文から取り出すので、要求を書き換えれば選択肢も変わる。
    """
    judge_eval = importlib.import_module("judge_eval")
    state = _results_state(case, closed=closed)
    criteria = {"done": "どの段も成果が出ており、足す仕事は無い"}
    criteria.update({f"missing:{stage}": f"「{stage}」の段の成果が出ていない"
                                         "（ノードが無い・失敗している）"
                     for stage in _request_stages(judge_eval.REQUEST)})
    questions = {"decision": {
        "type": "choice", "criteria": criteria,
        "instructions": f"要求は「{judge_eval.REQUEST}」。結果に成果が出ていない段はどれか。"}}
    return state, questions, lambda answers: {
        "decision": "done" if answers["decision"]["choice"] == "done" else "replan"}


def _evaluator_checklist_cell(case: dict, *, closed: bool = False):
    """evaluator の 3 つめの問い方: 段ごとに boolean 1 問へ割る。

    `+stages` は段を選択肢に並べても 1 問のままで、モデルは「全部 green なら done」を
    選び続けた。こちらは**段の数だけ問いを立てて**、1 段ずつ「成果が出ているか」を訊く。
    判定はモデルに訊かない——全部 yes なら `done`、1 つでも no なら `replan` と機械が畳む。

    `+stages` との違いは合否の読めかたにもある: あちらは `missing:` のどれを選んでも
    `replan` へ畳まれるので、**欠けている段を取り違えても正解になる**（2026-09-20 の E4 が
    その形で当たった）。こちらは段ごとに yes / no が残るので、どの段を取り違えたかが見える。
    """
    judge_eval = importlib.import_module("judge_eval")
    state = _results_state(case, closed=closed)
    stages = _request_stages(judge_eval.REQUEST)
    questions = {stage: {"type": "boolean",
                         "instructions": f"要求は「{judge_eval.REQUEST}」。"
                                         f"このうち「{stage}」の段の成果が、結果に出ているか。"}
                 for stage in stages}
    return state, questions, lambda answers: {
        "decision": "done" if all(answers[s].get("value") for s in stages) else "replan"}


# --------------------------------------------------------------------------- ステートマシンの 2 面
def _numbered(text: str) -> "list[tuple[str, str]]":
    """出力を行番号つきの候補にする（指させる先）。空行は落とす。"""
    return [(f"L{i}", line.strip())
            for i, line in enumerate(str(text or "").splitlines(), 1) if line.strip()]


def _transition_cell(case: dict, *, locate: bool = False):
    """遷移条件。既定は**本番の問い**（`_sm_condition_questions` をそのまま呼ぶ）。

    `locate` は同じ条件を「満たしていることを示す行はどれか」に替え、`other`（そんな行は
    無い）を置く。boolean は出力を読まずにも答えられるが、行を指すには読むしかない。
    判定は機械——行を指したら満たす、`other` なら満たさない。
    """
    sm = importlib.import_module("agentcore.harness.statemachine")
    lines = dict(_numbered(case["output"]))
    if not locate:
        questions = sm._sm_condition_questions(case["conditions"])
        return case["output"], questions, lambda answers: {
            name: bool(a.get("value")) for name, a in answers.items()}
    questions = {str(c["index"]): {
        "type": "choice", "criteria": lines,
        "other": "その条件を満たしていることを示す行は無い",
        "instructions": "Which line of the completed action output shows that this "
                        f"condition is satisfied? {c['condition']}"}
        for c in case["conditions"]}
    return case["output"], questions, lambda answers: {
        name: a.get("choice") in lines for name, a in answers.items()}


# 失敗の直し先。`classify` はこの 3 つから 1 つを選ばせ、機械が fixable へ畳む
# （作業物の中だけが「やり直しで直る」）。
_TRIAGE_CAUSES = {"work": "作業物（コード・設定・成果物）の誤り",
                  "environment": "環境や前提の不足（道具・権限・接続・資源）",
                  "check": "検査そのものが動いていない"}


def _triage_cell(case: dict, *, classify: bool = False):
    """検査失敗の選別。既定は**本番の問い**（boolean「やり直せば通るか」）。

    `classify` は「失敗の直し先はどこか」を 3 択にし、`work` だけを fixable へ畳む。
    locate（行を指す）はこの面に当てはまらない——欲しいのは行の所在ではなく原因の種別で、
    指された行を種別へ落とす決定的な規則が機械の側に無い（本番の決定的な段はこの素材に
    掛からない）。指すのではなく**札を貼らせて機械が畳む**のが同じ狙いの形になる。
    """
    sm = importlib.import_module("agentcore.harness.statemachine")
    command = "Check command: " + " ".join(case["argv"])
    state = command + "\n\nCheck output:\n" + case["output"]
    if not classify:
        questions = {"fixable": {"type": "boolean", "instructions": sm._SM_TRIAGE_QUESTION
                     if hasattr(sm, "_SM_TRIAGE_QUESTION") else
                     ("Can redoing the same action (editing the work product) make this "
                      "check pass? Answer no only if the failure is caused by the "
                      "environment (missing tool or dependency, permissions, network, "
                      "the check itself cannot run). " + command)}}
        return state, questions, lambda answers: answers["fixable"].get("value")
    questions = {"cause": {"type": "choice", "criteria": _TRIAGE_CAUSES,
                           "instructions": "この検査の失敗は、どこを直せば通るようになるか。"}}
    return state, questions, lambda answers: answers["cause"].get("choice") == "work"


# assess のセルは入力をドライバの中（`ap.assess_task(…, assess_risky())`）に持っているので、
# タスクを作る関数を名前で借りる。ケース定義も正解も project_eval 側のまま。
_ASSESS_TASKS = {"AS1": "assess_risky", "AS2": "assess_clear",
                 "AS3": "assess_risky_but_clear", "AS4": "assess_vague_but_safe"}


def _assess_cell(case: dict, *, cid: str = ""):
    """投入時アセスメント。問いは本番（`_assess_judge_questions`）をそのまま呼ぶ。

    **locate は当てはまらない面である**——状態はタスクの文で、指させる行が無い。訊くのは
    段（1〜3）の上の分布で、答えは確率加重の `score`。本番と同じく四捨五入を自前で行う
    （組み込みの `round()` は偶数丸めで、ちょうど 2.5 が 2 へ落ちる。`prioritize.py` の
    `assess_judge` と同じ式）。記録の書式 `c=N r=N a=N` も本番と同じで、
    project_eval の `check_assess` がその形を読む。
    """
    project_eval = importlib.import_module("project_eval")
    ap = project_eval.ap
    task = getattr(project_eval, _ASSESS_TASKS[cid])()
    questions = ap._assess_judge_questions()

    def to_check(answers):
        scores = {}
        for axis in questions:
            value = answers.get(axis, {}).get("score")
            if value is None:
                return ""
            scores[axis] = min(3, max(1, math.floor(float(value) + 0.5)))
        return " ".join(f"{axis}={scores[axis]}" for axis in ("c", "r", "a"))

    return ap._assess_material(task), questions, to_check


def _contract_cell(case: dict):
    """契約の語。問いは本番（`_sm_contract_by_judge`）と同じ形で、語は宣言（`output_validator`）
    から取る。採否も本番と同じ——選んだ語が宣言に無いか確度が下限に届かなければ補わない。"""
    sm = importlib.import_module("agentcore.harness.statemachine")
    prefixes = sm._sm_validator_prefixes(case["rule"])
    questions = {"contract": {
        "type": "choice",
        "instructions": "Which contract word does this output's conclusion correspond to?",
        "criteria": {p: f"The output concludes '{p}'." for p in prefixes},
        "other": "The output does not clearly conclude any of these."}}

    def to_check(answers):
        answer = answers["contract"]
        picked = str(answer.get("choice") or "")
        if picked not in prefixes:
            return ""
        return picked if float(answer.get("confidence") or 0.0) >= \
            sm._SM_CONTRACT_JUDGE_MIN_CONFIDENCE else ""

    return case["output"], questions, to_check


def _state_judge_cell(case: dict):
    """判定ステート。問いも答えの読み方も本番（statemachine-use の `judge_bridge`）を呼ぶ。

    `other` と確度不足を unsure の語にするのは `judge_state_output` の仕事で、ここには
    書き写さない。スキル側のスクリプトは Python 3.10 以上が要るので、読めない木では
    このセルだけ落ちる（測らずに落ちる方がよい——別実装で測ると本番を測っていない）。
    """
    sys.path.insert(0, str(REPO / ".github/skills/statemachine-use/scripts"))
    judge_bridge = importlib.import_module("judge_bridge")
    spec = judge_bridge.normalize_judge_state(case["judge"], default_input="{{last_output}}")
    questions = judge_bridge.judge_state_question(spec)
    return (case["input"], questions,
            lambda answers: judge_bridge.judge_state_output(spec, answers) or "")


def _route_cell(case: dict):
    """route: 本番の問いと状態（`agent_project` の `_route_judge_*`）をそのまま呼ぶ。

    この面だけは本番に judge の配線が既にあるので、問いの立て方まで借りる。`check` は本番の
    受け方（`_extract_json_obj` → `workspace`）を通すので、答えを `{"workspace": …}` へ戻す。
    """
    project_eval = importlib.import_module("project_eval")
    ap = project_eval.ap
    questions = ap._route_judge_questions(project_eval.WORKSPACES)

    def to_check(answers):
        choice = str(answers["workspace"].get("choice") or "")
        return json.dumps({"workspace": "" if choice == judge.OTHER_KEY else choice})

    return ap._route_judge_state(case["task"]), questions, to_check


# セル → (ケース定義を持つモジュール, 問いの立て方)。モジュールは遅延 import する
# （project_eval は agent_project が読めない木で SystemExit する）。
# `E3+stages` のような**変種**は、同じケース（入力と正解は 1 つのまま）を別の問いの形で
# 引く。問いの立て方を変えたときに、どちらが当たるかを同じ台帳の上で比べるため。
VARIANT_SEP = "+"
CELLS = {
    "F1": ("judge_eval", _filter_cell),
    "J2": ("judge_eval", _judge_cell),
    "CL1": ("judge_eval", _classify_cell),
    "E1": ("judge_eval", _evaluator_cell),
    "E2": ("judge_eval", _evaluator_cell),
    "E3": ("judge_eval", _evaluator_cell),
    "E4": ("judge_eval", _evaluator_cell),
    "E5": ("judge_eval", _evaluator_cell),
    "E6": ("judge_eval", _evaluator_cell),
    "RO1": ("project_eval", _route_cell),
    "RO2": ("project_eval", _route_cell),
    "RO3": ("project_eval", _route_cell),
}

# 要求の段を問いの側へ出した 2 つの形（同じ E1〜E6 を別の問いで引く）。`+stages` は段を
# 選択肢に並べた 1 問、`+checklist` は段ごとの boolean。どちらも**旧モード専用**——
# 答えを機械が `done` / `replan` へ畳むので、正解を通す割り当てが複数ある。
# calibration の `oracle` は「正解を通す割り当てがちょうど 1 つ」を要求するため、多対一の
# 変種はそちらへ載せない（載せるなら期待値を集合で持つ話になる。今回は決めない）。
VARIANTS = {f"E{i}{VARIANT_SEP}{name}": ("judge_eval", build)
            for name, build in (
                ("stages", _evaluator_stages_cell),
                ("checklist", _evaluator_checklist_cell),
                # 状態の側で閉世界を明示した組（問いの立て方は上の 3 つと同じ）。
                ("closed", functools.partial(_evaluator_cell, closed=True)),
                ("stages_closed", functools.partial(_evaluator_stages_cell, closed=True)),
                ("checklist_closed",
                 functools.partial(_evaluator_checklist_cell, closed=True)))
            for i in range(1, 7)}
# ステートマシンの 2 面。既定は本番の問い、変種は指させる／札を貼らせる形。
VARIANTS.update({
    "AS1": ("project_eval", functools.partial(_assess_cell, cid="AS1")),
    "AS2": ("project_eval", functools.partial(_assess_cell, cid="AS2")),
    "AS3": ("project_eval", functools.partial(_assess_cell, cid="AS3")),
    "AS4": ("project_eval", functools.partial(_assess_cell, cid="AS4")),
    "CW1": ("statemachine_cells", _contract_cell),
    "CW2": ("statemachine_cells", _contract_cell),
    "JS1": ("statemachine_cells", _state_judge_cell),
    "JS2": ("statemachine_cells", _state_judge_cell),
    "TR1": ("statemachine_cells", _transition_cell),
    "TR3": ("statemachine_cells", _transition_cell),
    f"TR3{VARIANT_SEP}locate": ("statemachine_cells",
                                functools.partial(_transition_cell, locate=True)),
    "TR2": ("statemachine_cells", _transition_cell),
    "CT1": ("statemachine_cells", _triage_cell),
    "CT2": ("statemachine_cells", _triage_cell),
    f"TR1{VARIANT_SEP}locate": ("statemachine_cells",
                                functools.partial(_transition_cell, locate=True)),
    f"TR2{VARIANT_SEP}locate": ("statemachine_cells",
                                functools.partial(_transition_cell, locate=True)),
    f"CT1{VARIANT_SEP}classify": ("statemachine_cells",
                                  functools.partial(_triage_cell, classify=True)),
    f"CT2{VARIANT_SEP}classify": ("statemachine_cells",
                                  functools.partial(_triage_cell, classify=True))})
# 状態と突き合わせないと答えられない形（段 → ノード / ノード → 段）。
VARIANTS.update({f"E{i}{VARIANT_SEP}{name}": ("judge_eval", build)
                 for name, build in (("locate", _evaluator_locate_cell),
                                     ("classify", _evaluator_classify_cell))
                 for i in range(1, 7)})
# 欠けている段を状態に書き下した診断（正解が入力に入る。上の _told_state の注を読むこと）。
VARIANTS.update({f"E{i}{VARIANT_SEP}told": ("judge_eval", _evaluator_told_cell)
                 for i in (3, 4, 5)})
# 候補の説明から基準外の属性（依存）を落とした F1。正解は変わらない（基準はテストの合否）。
VARIANTS[f"F1{VARIANT_SEP}nodeps"] = ("judge_eval",
                                      functools.partial(_filter_cell, drop_deps=True))
ALL_CELLS = {**CELLS, **VARIANTS}


def case_of(cid: str):
    """セル id から (ケース, 問いの立て方) を解く。変種（`E3+stages`）は元のケースを指す。"""
    module, build = ALL_CELLS[cid]
    return importlib.import_module(module).CASES[cid.split(VARIANT_SEP)[0]], build


# --------------------------------------------------------------------------- 集計（ollama を呼ばない）
def bin_of(confidence: float) -> "tuple[float, float]":
    """確度を 5 区間のどれかへ。区間は下を含み上を含まない——1.0 だけは最後の区間に入れる。"""
    for low, high in BINS:
        if confidence < high:
            return (low, high)
    return BINS[-1]


def _median(values: "list[float]") -> "float | None":
    return sorted(values)[len(values) // 2] if values else None


def summarize(rows: "list[dict]") -> dict:
    """台帳の行から (1) coverage の分布 (2) 信頼度図 を作る。

    `confidence` が無い行（judge が答えを読めずに落ちた回）は信頼度図から外す——確度の
    当たり方の話に、確度の無い回を混ぜない。外した件数は `errors` に残す。
    """
    scored = [r for r in rows if r.get("confidence") is not None]
    coverages = [float(r["coverage"]) for r in scored if r.get("coverage") is not None]
    below = [c for c in coverages if c < COVERAGE_FLOOR]
    reliability = []
    for low, high in BINS:
        hit = [r for r in scored if bin_of(float(r["confidence"])) == (low, high)]
        ok = sum(1 for r in hit if r.get("ok"))
        reliability.append({"bin": f"{low:.1f}-{high:.1f}", "n": len(hit), "ok": ok,
                            "accuracy": round(ok / len(hit), 4) if hit else None})
    return {
        "n": len(rows), "errors": len(rows) - len(scored),
        "ok": sum(1 for r in scored if r.get("ok")),
        "coverage": {"n": len(coverages), "floor": COVERAGE_FLOOR,
                     "below_floor": len(below),
                     "share_below_floor": round(len(below) / len(coverages), 4) if coverages else None,
                     "median": _median(coverages)},
        "reliability": reliability,
    }


def format_report(summary: dict) -> str:
    cov = summary["coverage"]
    lines = [f"=== coverage の分布（{cov['n']} 問）",
             f"  中央値 {cov['median']}  {cov['floor']} 未満 {cov['below_floor']}/{cov['n']}"
             f"（{cov['share_below_floor']}）",
             "", "=== 信頼度図（確度の区間ごとの正答率）"]
    for row in summary["reliability"]:
        share = "—" if row["accuracy"] is None else f"{row['ok']}/{row['n']} = {row['accuracy']}"
        lines.append(f"  {row['bin']}  {share}")
    lines.append(f"\n  合計 {summary['ok']}/{summary['n'] - summary['errors']}"
                 f"  読めなかった回 {summary['errors']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 実行
def ollama_reachable(timeout: float = 3.0) -> bool:
    """`/api/tags` に届くか。届かない木では 1 件も走らせない（CI はここで止まる）。"""
    try:
        with urllib.request.urlopen(ollama_loop.host_url().rstrip("/") + "/api/tags",
                                    timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def run_one(cid: str, run: int, model: str) -> dict:
    """1 セルを judge で 1 回引く。確度と coverage は**問いをまたいだ最小**を採る。

    棄権は全問一括（`abstained` に 1 つでも載れば呼び出し側は倒れる）なので、束を代表する
    のは最も弱い問い。1 セル = 1 点として信頼度図に載せる。
    """
    case, build = case_of(cid)
    state, questions, to_check = build(case)
    started = time.time()
    row = {"case": cid, "run": run, "model": model, "questions": len(questions)}
    try:
        result = judge.evaluate(state, questions, model=model)
    except judge.JudgeError as exc:
        return dict(row, ok=False, confidence=None, coverage=None, note=str(exc),
                    wall=round(time.time() - started, 2))
    answers = result["answers"]
    ok, note = case["check"](to_check(answers))
    return dict(row, ok=bool(ok), note=note,
                confidence=min(float(a["confidence"]) for a in answers.values()),
                coverage=min(float(a["coverage"]) for a in answers.values()),
                methods=sorted({str(a["method"]) for a in answers.values()}),
                abstained=judge.abstained(answers, 0.0),
                usage=result["usage"], wall=round(time.time() - started, 2))


# Calibration mode keeps raw per-question distributions; legacy summaries above
# remain readable but cannot reconstruct a Brier score from minimum confidence.
THRESHOLDS = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9)
MIN_UNIQUE_CASES = 30  # descriptive-data floor, never an authorization to deploy


def oracle(questions, to_check, check):
    """Find the unique assignment accepted by the existing deterministic checker.

    F1 has 2**6 assignments. No labels are copied or inferred by another LLM.
    Refuse ambiguous/non-finite fixtures before making any network request.
    """
    names = list(questions)
    domains = [[k for k, _ in judge.normalize_question(n, questions[n])["options"]]
               for n in names]
    if math.prod(map(len, domains)) > 4096:
        raise ValueError("oracle search exceeds 4096 assignments")
    valid = []
    for values in itertools.product(*domains):
        answers = {n: {"choice": v, "value": v == "yes"}
                   for n, v in zip(names, values)}
        if check(to_check(answers))[0]:
            valid.append(dict(zip(names, values)))
    if len(valid) != 1:
        raise ValueError(f"oracle must accept exactly one assignment, got {len(valid)}")
    return valid[0]


def percentile(values, p):
    """Linear interpolation at (n - 1) * p; empty data is null."""
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def brier(answer, expected):
    """Multiclass sum of squared errors (0..2), including binary's two classes."""
    return sum((p - int(key == expected)) ** 2
               for key, p in answer["probabilities"].items())


def _rate(n, total):
    return n / total if total else None


def _accepted(row, threshold):
    return not judge.abstained(row["answers"], threshold)


def _sweep(rows, thresholds):
    out = []
    for threshold in thresholds:
        accepted = [r for r in rows if _accepted(r, threshold)]
        out.append({"min_confidence": threshold, "accepted_cases": len(accepted),
                    "answer_rate": _rate(len(accepted), len(rows)),
                    "accuracy": _rate(sum(r["ok"] for r in accepted), len(accepted)),
                    "unique_cases": len({r["case"] for r in accepted}),
                    "status": "insufficient_data" if len({r["case"] for r in accepted})
                    < MIN_UNIQUE_CASES else "descriptive_only"})
    return out


def calibration_report(rows, *, model, min_confidence=0.0, thresholds=THRESHOLDS):
    """Pure offline aggregation of versioned real/fake ledger rows."""
    if not 0 <= min_confidence <= 1 or any(not 0 <= t <= 1 for t in thresholds):
        raise ValueError("confidence thresholds must be finite and in [0, 1]")
    for row in rows:
        if row.get("schema_version") != 1 or row.get("model") != model:
            raise ValueError("calibration requires v1 rows from one model")
    if len({r["source"] for r in rows}) > 1:
        raise ValueError("do not pool fake and real observations")
    successful = [r for r in rows if r["status"] == "ok"]
    for row in successful:
        if not row["answers"] or set(row["answers"]) != set(row["expected"]):
            raise ValueError("incomplete answers/oracle")
        for name, answer in row["answers"].items():
            probs = answer["probabilities"]
            if (answer["method"] not in ("logprobs", "vote", "text")
                    or row["expected"][name] not in probs
                    or any(not math.isfinite(v) or not 0 <= v <= 1 for v in
                           [answer["confidence"], answer["coverage"], *probs.values()])
                    or abs(sum(probs.values()) - 1) > .002):
                raise ValueError("invalid calibration distribution")
    groups = []
    for method in ("logprobs", "vote", "text"):
        points = []
        for row in successful:
            for name, answer in row["answers"].items():
                if answer["method"] == method:
                    points.append({"case": row["case"], "id": f"{row['case']}:{row['run']}:{name}",
                                   "answers": {name: answer}, "answer": answer,
                                   "ok": row["question_ok"][name],
                                   "expected": row["expected"][name]})
        accepted = [r for r in points if _accepted(r, min_confidence)]
        calibrated = [r for r in points if method != "text" and
                      r["answer"]["type"] in ("boolean", "choice")]
        buckets = []
        for low, high in BINS:
            bucket = [r for r in calibrated if bin_of(r["answer"]["confidence"]) == (low, high)]
            buckets.append({"low": low, "high": high, "n": len(bucket),
                            "accuracy": _rate(sum(r["ok"] for r in bucket), len(bucket)),
                            "mean_confidence": _rate(sum(r["answer"]["confidence"] for r in bucket), len(bucket))})
        cov = [r["answer"]["coverage"] for r in points]
        low_ids = [r["id"] for r in points if r["answer"]["coverage"] < COVERAGE_FLOOR]
        groups.append({"method": method, "unit": "question", "cases": len(points),
                       "unique_cases": len({r["case"] for r in points}),
                       "status": "insufficient_data" if len({r["case"] for r in calibrated})
                       < MIN_UNIQUE_CASES else "descriptive_only",
                       "answered": len(accepted), "abstained": len(points) - len(accepted),
                       "abstained_case_ids": [r["id"] for r in points if not _accepted(r, min_confidence)],
                       "accuracy_answered": _rate(sum(r["ok"] for r in accepted), len(accepted)),
                       "answer_rate": _rate(len(accepted), len(points)),
                       "abstention_rate": _rate(len(points) - len(accepted), len(points)),
                       "calibration_n": len(calibrated),
                       "brier": _rate(sum(brier(r["answer"], r["expected"]) for r in calibrated), len(calibrated)),
                       "ece": _rate(sum(b["n"] * abs(b["accuracy"] - b["mean_confidence"])
                                        for b in buckets if b["n"]), len(calibrated)),
                       "buckets": buckets,
                       "coverage": {"p10": percentile(cov, .1), "p50": percentile(cov, .5),
                                    "p90": percentile(cov, .9), "below_0_8": len(low_ids),
                                    "low_case_ids": low_ids},
                       "thresholds": _sweep(points, thresholds)})
    # Mixed-method cells have their own gate group, never a pooled calibration.
    cell_groups = []
    signatures = sorted({tuple(sorted({a["method"] for a in r["answers"].values()}))
                         for r in successful})
    for signature in signatures:
        cells = [r for r in successful if tuple(sorted({a["method"] for a in r["answers"].values()})) == signature]
        baseline = _sweep(cells, (min_confidence,))[0]
        cell_groups.append({"methods": list(signature), "unit": "cell", "cases": len(cells),
                            "min_confidence": min_confidence,
                            "answered": baseline["accepted_cases"],
                            "abstained": len(cells) - baseline["accepted_cases"],
                            "answer_rate": baseline["answer_rate"],
                            "abstention_rate": 1 - baseline["answer_rate"],
                            "accuracy_answered": baseline["accuracy"],
                            "thresholds": _sweep(cells, thresholds),
                            "by_case": [{"case": cid, "thresholds": _sweep(
                                [r for r in cells if r["case"] == cid], thresholds)}
                                for cid in sorted({r["case"] for r in cells})]})
    failures = [{"case": r["case"], "run": r["run"], "status": r["status"], "error": r["error"]}
                for r in rows if r["status"] != "ok"]
    return {"schema_version": 1, "model": model,
            "source": rows[0]["source"] if rows else None,
            "status": "insufficient_data" if len({r["case"] for r in successful}) < MIN_UNIQUE_CASES
            else "descriptive_only", "minimum_unique_cases": MIN_UNIQUE_CASES,
            "min_confidence": min_confidence, "attempted_cells": len(rows),
            "successful_cells": len(successful), "failures": failures,
            "transport_failures": sum(r["status"] == "transport_failure" for r in rows),
            "response_failures": sum(r["status"] == "response_failure" for r in rows),
            "methods": groups, "cell_gates": cell_groups,
            "latency_seconds": {"p50": percentile([r["wall"] for r in rows], .5),
                                "p90": percentile([r["wall"] for r in rows], .9)},
            "usage": {key: {"observed_total": sum(r["usage"][key] for r in rows if r["usage"].get(key) is not None),
                            "missing_cells": sum(r["usage"].get(key) is None for r in rows)}
                      for key in ("tokens_in", "tokens_out")},
            "limitations": ["Repeated fixtures are not independent workload samples.",
                            "No production threshold is recommended or applied.",
                            "text is excluded from Brier/ECE; vote coverage is valid votes / samples.",
                            "Answer-rate denominators exclude request/response failures.",
                            "Brier uses sum over all classes (binary range 0..2)."]}


def calibration_run_one(cid, run, model, *, samples=1, fake=False):
    case, build = case_of(cid)
    state, questions, to_check = build(case)
    expected = oracle(questions, to_check, case["check"])
    usage = {"tokens_in": None, "tokens_out": None}
    totals = {"tokens_in": 0, "tokens_out": 0}
    missing = set()
    requests = 0
    transport = False

    def request(payload):
        nonlocal requests, transport
        try:
            data = judge.post_chat(payload)
        except (judge.JudgeError, OSError, ValueError) as exc:
            missing.update(totals)
            transport = isinstance(exc, OSError) or isinstance(exc.__cause__, (OSError, urllib.error.URLError))
            raise
        requests += 1
        for key, field in (("tokens_in", "prompt_eval_count"), ("tokens_out", "eval_count")):
            if field not in data or data[field] is None:
                missing.add(key)
            else:
                totals[key] += data[field]
        return data

    started = time.monotonic()
    row = {"schema_version": 1, "case": cid, "run": run, "model": model,
           "source": "fake" if fake else "real", "samples": samples, "expected": expected,
           "input_sha256": hashlib.sha256(json.dumps({"state": state, "questions": questions},
                                                       ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    try:
        if fake:
            answers = {}
            for name, raw in questions.items():
                normalized = judge.normalize_question(name, raw)
                options = normalized["options"]
                probs = [.75 if k == expected[name] else .25 / (len(options) - 1) for k, _ in options]
                answers[name] = judge.shape_answer(normalized, probs, method="logprobs", coverage=.95)
        else:
            answers = judge.evaluate(state, questions, model=model, samples=samples, request=request)["answers"]
        ok, note = case["check"](to_check(answers))
        row.update(status="ok", ok=bool(ok), note=note, answers=answers,
                   question_ok={n: ("yes" if a["value"] else "no") == expected[n]
                                if a["type"] == "boolean" else a["choice"] == expected[n]
                                for n, a in answers.items()})
    except (judge.JudgeError, OSError, ValueError) as exc:
        row.update(status="transport_failure" if transport else "response_failure", error=str(exc))
    if requests:
        usage = {key: None if key in missing else value for key, value in totals.items()}
    row.update(usage=usage, wall=time.monotonic() - started, successful_requests=requests)
    return row


def calibration_main(args):
    if args.fake_run and args.replay:
        raise ValueError("fake-run and replay are mutually exclusive")
    if args.repeat < 1 or args.samples < 1 or not 0 <= args.min_confidence <= 1:
        raise ValueError("repeat/samples must be positive; min-confidence must be in [0,1]")
    cids = [c.strip() for c in args.cases.split(",")]
    if any(c not in CELLS for c in cids) or len(cids) != len(set(cids)):
        raise ValueError("unknown or duplicate case ID")
    if args.replay:
        rows = [json.loads(line) for line in Path(args.replay).read_text().splitlines() if line.strip()]
    else:
        from agentcore.hostenv import load_profile_env
        if not args.fake_run:
            load_profile_env()
        rows = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = Path(args.output_dir) if args.output_dir else Path(__file__).parent / "results" / (stamp + "-calibration")
    output.mkdir(parents=True, exist_ok=False)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    manifest = {"schema_version": 1, "created_at": stamp, "revision": revision,
                "arguments": vars(args), "state": "running",
                "dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                                              capture_output=True, text=True).stdout.strip())}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "command.txt").write_text(shlex.join([sys.executable, *sys.argv]) + "\n")
    if not args.replay:
        with (output / "ledger.jsonl").open("w") as ledger:
            for cid in cids:
                for run in range(1, args.repeat + 1):
                    row = calibration_run_one(cid, run, args.model, samples=args.samples, fake=args.fake_run)
                    rows.append(row)
                    ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
                    ledger.flush()
                    print(f"{cid}-{run}: {row['status']}", file=sys.stderr)
    else:
        (output / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = calibration_report(rows, model=args.model, min_confidence=args.min_confidence)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    manifest["state"] = "completed_with_failures" if report["failures"] else "completed"
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Calibration report: {output / 'report.json'}")
    return 1 if report["failures"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--cases", default=None)
    parser.add_argument("--calibration", action="store_true", help="write versioned calibration ledger/report")
    parser.add_argument("--fake-run", action="store_true", help="synthetic smoke run, implies --calibration")
    parser.add_argument("--replay", help="recompute a v1 calibration ledger offline")
    parser.add_argument("--output-dir", help="new results directory (must not exist)")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--min-confidence", type=float, default=0.0)
    args = parser.parse_args()
    calibrating = bool(args.calibration or args.fake_run or args.replay)
    if args.cases is None:      # 変種は旧モードの既定にだけ入れる（上の VARIANTS の注）。
        args.cases = ",".join(CELLS if calibrating else ALL_CELLS)
    if calibrating:
        try:
            return calibration_main(args)
        except ValueError as exc:
            parser.error(str(exc))
    cids = [c.strip() for c in args.cases.split(",") if c.strip() in ALL_CELLS]
    if not cids:
        print(f"測れるセルがありません（選べるのは {', '.join(ALL_CELLS)}）")
        return 2
    if not ollama_reachable():
        print(f"ollama に届かないので測っていない（{ollama_loop.host_url()}）。台帳は書かない。")
        return 0

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    print(f"model={args.model} cases={cids} repeat={args.repeat}\n")
    rows = []
    for cid in cids:
        for run in range(1, args.repeat + 1):
            row = run_one(cid, run, args.model)
            rows.append(row)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            mark = "○" if row["ok"] else "×"
            print(f"  {mark} {cid}-{run} 確度 {row['confidence']} coverage {row['coverage']}"
                  f"  {row['note'][:60]}")
    print("\n" + format_report(summarize(rows)))
    print(f"\n台帳: {ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
