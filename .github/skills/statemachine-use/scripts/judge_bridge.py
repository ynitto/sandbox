"""judge_bridge.py — 遷移条件の評価を agent-herd の judge（判定 AI）へ橋渡しする。

## なにを解くか

遷移条件の LLM 評価は、これまで「条件文を 1 つずつ YES / NO で答えさせる」形だった。
答えは 1 語でも、prefill（状態と条件文の読み込み）は条件の数だけ走り、答える側が
クラウドの CLI なら毎回そのトークンを払う。agent-herd の `judge` は文章を生成せず、
選択肢の上の確率分布を 1 トークン目の読み出しで返す——判定 1 件が prefill 1 回 + 4 トークンで
終わり、JSON が壊れる・散文が混じるという故障が原理的に無い。

この橋は 2 つのことをする:

1. **問いを組む**（`judge_questions`）。全候補に `outcome`（結果の短い名前）があれば
   「結果はどれか」を **choice 1 問**にする——N 件の boolean が 1 件の choice になり、
   候補の間で答えが矛盾する形（2 つの条件が同時に真）が構造として消える。無ければ
   条件 1 件に boolean 1 問（多基準を 1 問で訊かない）。
2. **agent-herd に訊く**（`resolve_judge` / `JudgeClient`）。`agent-herd` が PATH にあり、
   設定（`agent-herd config --check judge`）が判定をモデル指名で回す形なら使う。無ければ
   None を返し、呼び出し側は従来の YES / NO 生成に留まる。**どちらでも同じ定義が回る。**

問いの形と答えの読み方は agentcore の `harness/statemachine.py`（`_sm_condition_questions`）と
同じにしてある——外部ハーネスで回してもこのスクリプトで回しても、同じ workflow が
同じ問いを judge に投げる。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

HERD = "agent-herd"
# 遷移先を 1 問の choice で選ぶときの問いの名前（条件の index と衝突しない綴り）。
OUTCOME_QUESTION = "__outcome__"
OUTCOME_OTHER = "None of the outcomes above applies."
OUTCOME_INSTRUCTIONS = "Which outcome does the completed action output show?"
BOOLEAN_INSTRUCTIONS = "Does the completed action output satisfy this condition? "
JUDGE_TIMEOUT_SEC = 600


# ─────────────────────────────────────────────
#  問いを組む / 答えを読む
# ─────────────────────────────────────────────
def outcomes_of(conditions: "list[dict]") -> "dict[str, str] | None":
    """全候補が `outcome` を持ち、互いに違うときだけ index → outcome。そうでなければ None。"""
    if len(conditions) < 2:
        return None
    outcomes = {str(c.get("index")): str(c.get("outcome") or "").strip() for c in conditions}
    if not all(outcomes.values()) or len(set(outcomes.values())) != len(outcomes):
        return None
    return outcomes


def judge_questions(conditions: "list[dict]") -> dict:
    """needs_llm_eval の条件（next_state.py --auto-eval の entry）を judge の問いへ。

    返り値は `agent-herd judge --questions` にそのまま渡せる {名前: 問い}。名前は
    choice なら OUTCOME_QUESTION、boolean なら条件の index（--eval の鍵）。
    """
    outcomes = outcomes_of(conditions)
    if outcomes:
        return {OUTCOME_QUESTION: {"type": "choice", "instructions": OUTCOME_INSTRUCTIONS,
                                   "criteria": dict(outcomes), "other": OUTCOME_OTHER}}
    questions: dict = {}
    for cond in conditions:
        text = str(cond.get("condition") or "").strip()
        desc = str(cond.get("description") or "").strip()
        if desc and desc != text:
            text = f"{text}（{desc}）" if text else desc
        if not text:
            text = str(cond.get("outcome") or "").strip()
        if not text:
            continue
        questions[str(cond.get("index"))] = {"type": "boolean",
                                             "instructions": BOOLEAN_INSTRUCTIONS + text}
    return questions


def evals_from_answers(questions: dict, answers: dict) -> dict:
    """judge の答え（`answers` の中身）を `--eval` の形（index → bool）へ。"""
    if OUTCOME_QUESTION in questions:
        picked = str((answers.get(OUTCOME_QUESTION) or {}).get("choice") or "")
        return {index: index == picked for index in questions[OUTCOME_QUESTION]["criteria"]}
    return {name: bool((answers.get(name) or {}).get("value"))
            for name in questions if name in answers}


def evals_from_judge_output(questions: dict, output: "str | dict") -> "dict | None":
    """`agent-herd judge` の stdout（{"answers":…, "abstained":[…]}）から evals を作る。

    abstained（確度不足）があれば None——決めていない答えを黙って採用しない。
    """
    data = json.loads(output) if isinstance(output, str) else output
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        return None
    if data.get("abstained"):
        return None
    evals = evals_from_answers(questions, data["answers"])
    return evals or None


# ─────────────────────────────────────────────
#  agent-herd に訊く
# ─────────────────────────────────────────────
def herd_path() -> "str | None":
    return shutil.which(HERD)


def judge_configured(*, run=subprocess.run) -> "dict | None":
    """`agent-herd config --check judge` が 0 なら設定（{"mode","model"}）、それ以外は None。"""
    exe = herd_path()
    if not exe:
        return None
    try:
        proc = run([exe, "config", "--check", "judge"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        info = json.loads(proc.stdout.strip() or "{}")
    except ValueError:
        return None
    return info if isinstance(info, dict) and info.get("mode") == "pinned" else None


class JudgeClient:
    """`agent-herd judge` を 1 回ずつ起こす。失敗は None で返し、以後は呼ばない（黙って
    再試行を積まない——落ちる理由はだいたい接続で、条件ごとに待つと実行が溶ける）。"""

    def __init__(self, exe: str, *, model: "str | None" = None, run=subprocess.run, log=None):
        self.exe = exe
        self.model = model
        self._run = run
        self._log = log or (lambda msg: None)
        self.disabled_reason: "str | None" = None

    def evaluate(self, state_text: str, questions: dict) -> "dict | None":
        """答えの dict（`answers`）。使えない・読めない・確度不足なら None。"""
        if self.disabled_reason or not questions:
            return None
        argv = [self.exe, "judge", "--questions", json.dumps(questions, ensure_ascii=False)]
        if self.model:
            argv += ["--model", self.model]
        try:
            proc = self._run(argv, input=state_text, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=JUDGE_TIMEOUT_SEC)
        except (OSError, subprocess.SubprocessError) as exc:
            self.disabled_reason = f"{HERD} judge を起こせません: {exc}"
            self._log(self.disabled_reason)
            return None
        try:
            data = json.loads(proc.stdout.strip() or "{}")
        except ValueError:
            data = {}
        if proc.returncode == 0 and isinstance(data.get("answers"), dict):
            return data["answers"]
        if proc.returncode == 1 and data.get("abstained"):
            self._log(f"judge が確度不足で決めなかった条件: {data['abstained']}")
            return None
        first = (proc.stderr or proc.stdout or "").strip().splitlines()
        self.disabled_reason = f"{HERD} judge が失敗しました（rc={proc.returncode}）"
        if first:
            self.disabled_reason += f": {first[0][:160]}"
        self._log(self.disabled_reason)
        return None


def resolve_judge(mode: str = "auto", *, run=subprocess.run, log=None) -> "JudgeClient | None":
    """--judge の値から JudgeClient を作る。

    auto … agent-herd が在り、設定が判定をモデル指名で回す形（config --check judge = 0）なら使う
    herd … agent-herd が在れば使う（設定が auto でも、judge の既定モデルで）
    off  … 使わない
    """
    if mode == "off":
        return None
    exe = herd_path()
    if not exe:
        return None
    if mode == "herd":
        return JudgeClient(exe, run=run, log=log)
    info = judge_configured(run=run)
    if info is None:
        return None
    return JudgeClient(exe, model=info.get("model"), run=run, log=log)


def state_text(ctx: "dict[str, Any]") -> str:
    """judge に渡す状態: 最後の出力と、履歴以外のコンテキスト変数。"""
    last_output = str(ctx.get("last_output", ""))
    rest = {k: v for k, v in ctx.items() if k not in ("history", "last_output")}
    return ("Last output:\n" + last_output + "\n\nContext:\n"
            + json.dumps(rest, ensure_ascii=False, indent=2, default=str))
