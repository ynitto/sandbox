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
import re
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


# ─────────────────────────────────────────────
#  ステートの中で使う 3 つの口
#  （判定だけのステート / 出力契約の正規化 / 検査失敗の選別）
#  どれも「決定的な手段 → 判定 AI → 生成」の順で、判定 AI が無くてもトークンが最小になる形。
# ─────────────────────────────────────────────
STATE_JUDGE_QUESTION = "answer"
STATE_JUDGE_UNSURE = "UNSURE"
STATE_JUDGE_OTHER = "None of the choices applies, or it cannot be decided from the input."
STATE_JUDGE_MIN_CONFIDENCE = 0.0


def normalize_judge_state(value, *, default_input: str) -> "dict | None":
    """state の `judge:` 宣言を正規化する。無ければ None、形が違えば ValueError。

    受ける形:
      judge:
        question: "このイシューの種類はどれか"
        choices: {BUG: "動作の不具合", FEATURE: "機能の要望"}   # 順序つき。list でもよい
        input: "{{input}}"          # 判定 AI が読む状態（省略時は初期ステートなら input、他は last_output）
        unsure: "UNSURE"            # どれでもない・確度不足のときに出す語（省略時 UNSURE）
        min_confidence: 0.0         # これ未満なら unsure に倒す

    出力の契約は「選択肢のキー（か unsure）を第 1 行に 1 語」。`output_validator` を書かなければ
    ここから作る（`startswith:BUG,FEATURE,UNSURE`）。
    """
    if value is None or value == "":
        return None
    if not isinstance(value, dict):
        raise ValueError("judge はオブジェクト（question / choices …）で書きます")
    question = str(value.get("question") or value.get("instructions") or "").strip()
    if not question:
        raise ValueError("judge.question（問いの文）が必要です")
    raw = value.get("choices") if value.get("choices") is not None else value.get("criteria")
    choices: "list[tuple[str, str]]" = []
    if isinstance(raw, dict):
        choices = [(str(k).strip(), "" if v is None else str(v).strip()) for k, v in raw.items()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                key = item.get("key", item.get("name"))
                if key is None:
                    continue
                choices.append((str(key).strip(), str(item.get("description") or "").strip()))
            else:
                choices.append((str(item).strip(), ""))
    choices = [(k, d) for k, d in choices if k]
    if len(choices) < 2:
        raise ValueError("judge.choices には選択肢が 2 つ以上必要です")
    if len({k for k, _ in choices}) != len(choices):
        raise ValueError("judge.choices のキーが重複しています")
    if any(" " in k or "\n" in k for k, _ in choices):
        raise ValueError("judge.choices のキーは空白を含まない 1 語にします（出力の第 1 行になる）")
    unsure = str(value.get("unsure") or STATE_JUDGE_UNSURE).strip()
    if unsure in {k for k, _ in choices}:
        raise ValueError(f"judge.unsure（{unsure}）が choices と重なっています")
    try:
        min_confidence = float(value.get("min_confidence", STATE_JUDGE_MIN_CONFIDENCE) or 0.0)
    except (TypeError, ValueError):
        raise ValueError("judge.min_confidence は数です")
    return {"question": question, "choices": choices, "unsure": unsure,
            "input": str(value.get("input") or default_input),
            "min_confidence": min(1.0, max(0.0, min_confidence))}


def judge_state_keys(spec: dict) -> "list[str]":
    return [k for k, _ in spec["choices"]] + [spec["unsure"]]


def judge_state_validator(spec: dict) -> str:
    return "startswith:" + ",".join(judge_state_keys(spec))


def judge_state_question(spec: dict) -> dict:
    """`agent-herd judge --questions` にそのまま渡せる 1 問（choice + other）。"""
    return {STATE_JUDGE_QUESTION: {
        "type": "choice", "instructions": spec["question"],
        "criteria": {k: d for k, d in spec["choices"]}, "other": STATE_JUDGE_OTHER}}


def judge_state_output(spec: dict, answers: "dict | None") -> "str | None":
    """判定 AI の答えをステートの出力（キー 1 語）へ。決めていなければ None。

    `other` と確度不足は unsure の語にする——「決められない」を黙って最頻の選択肢に
    倒さない（決められないと言えることが judge を使う理由の 1 つ）。
    """
    if not isinstance(answers, dict):
        return None
    answer = answers.get(STATE_JUDGE_QUESTION)
    if not isinstance(answer, dict):
        return None
    picked = str(answer.get("choice") or "")
    keys = {k for k, _ in spec["choices"]}
    if not picked or (picked != "other" and picked not in keys):
        return None
    confidence = float(answer.get("confidence") or 0.0)
    if picked == "other" or confidence < spec["min_confidence"]:
        return spec["unsure"]
    return picked


def judge_state_fallback_action(spec: dict) -> str:
    """判定 AI が無いときの生成用プロンプト。**短く、答えは 1 語**——これが生成経路で
    いちばん安い形（本文も理由も書かせない）。"""
    lines = [spec["question"], "", "Input:", "<<<", spec["input"], ">>>", "", "Choices:"]
    for key, desc in spec["choices"]:
        lines.append(f"- {key}" + (f": {desc}" if desc else ""))
    lines.append(f"- {spec['unsure']}: none of the above / cannot be decided")
    lines += ["", "Answer with exactly one choice key on the first line. No explanation."]
    return "\n".join(lines)


# ── 出力契約の正規化（output_validator: startswith:A,B,C）──────────────────
def validator_prefixes(rule) -> "list[str]":
    text = str(rule or "")
    if not text.startswith("startswith:"):
        return []
    return [p.strip() for p in text[len("startswith:"):].split(",") if p.strip()]


def normalize_contract_line(output: str, prefixes: "list[str]") -> "str | None":
    """契約の語が第 1 行の先頭に無い出力を、再生成せずに直す（決定的）。

    直せる形: 契約の語が第 1 行の途中にある（「結論: APPROVED。」）、大文字小文字が違う、
    契約の語で始まる行が後ろにある。直せなければ None（呼び出し側が判定 AI か再生成へ）。
    """
    text = str(output or "").strip()
    if not text or not prefixes:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = lines[0]
    if any(first.startswith(p) for p in prefixes):
        return text
    # 第 1 行の中に語として現れる（前後が英数字でない）。長い語から試す（PASS と PASSED）。
    for prefix in sorted(prefixes, key=len, reverse=True):
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(prefix) + r"(?![A-Za-z0-9_])", re.I)
        if pattern.search(first):
            return "\n".join([prefix, *lines[1:]]) if len(lines) > 1 else prefix
    # 契約の語で始まる行が後ろにある（前置きを書いた）。その行から採る。
    for i, line in enumerate(lines[1:4], start=1):
        for prefix in prefixes:
            if line.upper().startswith(prefix.upper()):
                return "\n".join([prefix + line[len(prefix):], *lines[i + 1:]])
    return None


CONTRACT_QUESTION = "contract"


def contract_question(prefixes: "list[str]") -> dict:
    """出力がどの契約の語に当たるかを判定 AI に訊く 1 問（other = どれでもない → 再生成）。"""
    return {CONTRACT_QUESTION: {
        "type": "choice",
        "instructions": "Which contract word does this output's conclusion correspond to?",
        "criteria": {p: f"The output concludes '{p}'." for p in prefixes},
        "other": "The output does not clearly conclude any of these."}}


def contract_from_answers(output: str, prefixes: "list[str]", answers: "dict | None",
                          *, min_confidence: float = 0.6) -> "str | None":
    if not isinstance(answers, dict):
        return None
    answer = answers.get(CONTRACT_QUESTION) or {}
    picked = str(answer.get("choice") or "")
    if picked not in prefixes or float(answer.get("confidence") or 0.0) < min_confidence:
        return None
    return "\n".join([picked, *str(output or "").strip().splitlines()])


# ── 検査（check）失敗の選別 ───────────────────────────────────────────────
# 同じアクションをやり直しても直らない失敗の印。検査コマンド自体が動いていない形で、
# どれも実出力に現れる定型句。ここに無い失敗は「直るかもしれない」側（再投入）。
_ENVIRONMENT_PATTERNS = (
    re.compile(r"command not found|No such file or directory|not recognized as an internal", re.I),
    re.compile(r"No module named|ModuleNotFoundError|cannot find module|Cannot find package", re.I),
    re.compile(r"Permission denied|EACCES", re.I),
    re.compile(r"検査コマンドを実行できません|検査コマンドがタイムアウトしました", re.I),
    re.compile(r"ENOENT|EADDRINUSE|Connection refused|Could not resolve host", re.I),
)
CHECK_TRIAGE_QUESTION = "fixable"
CHECK_TRIAGE_MIN_CONFIDENCE = 0.85


def check_failure_environment(detail: str) -> "str | None":
    """検査の出力が環境の失敗（やり直しても直らない）なら、その根拠の 1 行。無ければ None。"""
    for line in str(detail or "").splitlines():
        for pattern in _ENVIRONMENT_PATTERNS:
            if pattern.search(line):
                return line.strip()[:200]
    return None


def check_triage_question(argv: "list[str]", detail: str) -> dict:
    """「同じアクションをやり直せば直る失敗か」を boolean で訊く 1 問。"""
    return {CHECK_TRIAGE_QUESTION: {
        "type": "boolean",
        "instructions": ("Can redoing the same action (editing the work product) make this "
                         "check pass? Answer no only if the failure is caused by the "
                         "environment (missing tool or dependency, permissions, network, "
                         "the check itself cannot run). Check command: " + " ".join(argv)),
    }}


def check_triage_verdict(answers: "dict | None",
                         *, min_confidence: float = CHECK_TRIAGE_MIN_CONFIDENCE) -> "bool | None":
    """False = やり直しても直らない（確度が十分なときだけ）。True / None = 従来どおり再投入。"""
    if not isinstance(answers, dict):
        return None
    answer = answers.get(CHECK_TRIAGE_QUESTION) or {}
    if answer.get("value") is None:
        return None
    if answer.get("value") is False and float(answer.get("confidence") or 0.0) >= min_confidence:
        return False
    return True


def check_detail(result: dict) -> str:
    return "\n".join(x for x in (result.get("error", ""), result.get("stderr", ""),
                                 result.get("stdout", "")) if x).strip()
