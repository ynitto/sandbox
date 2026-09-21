"""agentcore.route — 依頼をどう扱うかを、Jev 型の判断 AI に訊く。

## 何をするモジュールか

agent-app の入力欄（会話画面）に入った依頼 1 件について、**モデルに送る前に**扱いを決める。
問いは 1 基準 1 問で、同じ状態（依頼の先頭 + 候補）を先に置いて接頭辞キャッシュに乗せる:

| 問い | 型 | 答え |
|---|---|---|
| `handling` | choice | `answer`（実行せず読み取りだけで答える）/ `converse`（会話の中で実行）/ `task`（候補タスクの流用）/ `flow`（候補ワークフローの流用）/ other（どれとも言えない） |
| `task` / `flow` | choice | 流用するならどれか（候補 + other）。候補が無ければ組まない |
| `skill:<name>` | boolean | そのスキルを添えると依頼の質が上がるか（候補ごとに 1 問） |
| `routine` | boolean | 日付や対象などの入力だけ替えて今後も繰り返す形か（定型化の提案） |

判断の順は `select`（`modelselect`）と同じ jev → judge で、段の試行は `modelselect.ask_stages`
を共有する。**決定的な 3 段目は持たない。** 決めなければ従来の動き（会話で実行、スキルは
文字列の一致）へ倒すのが呼び出し側の契約で、`agent-herd route` は終了コード 1 で「決めず」を
伝える。候補の絞り込み（上限は各 8 件・6 件）と、答えを画面の動きに写すのも呼び出し側。

`hold` は「会話を送らずに止めてよいか」。`handling` が task / flow を指し、その確度と流用先の
確度がどちらも `route.hold_min_confidence`（既定 0.75）以上のときだけ真。止めるほうが人の手数を
増やすので、下限を別に持つ。

設計: docs/plans/2026-09-21-agent-app-judge-request-routing-design.md §2。
"""
from __future__ import annotations

from agentcore import herdconfig, judge, modelselect

STAGES = (modelselect.STAGE_JEV, modelselect.STAGE_JUDGE)
OTHER_KEY = modelselect.OTHER_KEY

QUESTION_HANDLING = "handling"
QUESTION_TASK = "task"
QUESTION_FLOW = "flow"
QUESTION_ROUTINE = "routine"
SKILL_PREFIX = "skill:"

HANDLING_ANSWER = "answer"
HANDLING_CONVERSE = "converse"
HANDLING_TASK = "task"
HANDLING_FLOW = "flow"
HANDLINGS = {
    HANDLING_ANSWER: "Answer from reading only; nothing is executed or changed.",
    HANDLING_CONVERSE: "Let the assistant execute it inside the conversation "
                       "(edit files, run commands, one-off work).",
    HANDLING_TASK: "One of the listed tasks does the same work; rerun it with new inputs.",
    HANDLING_FLOW: "One of the listed workflows does the same work; rerun it with new inputs.",
}
HANDLING_OTHER = "None of these fits, or it cannot be told from the request."

# choice のラベルは A〜Z。other の分を 1 つ空ける。
MAX_CHOICES = judge.MAX_OPTIONS - 1
DEFAULT_HOLD_MIN_CONFIDENCE = 0.75
POLICY_LINE = ("Decide how to handle the request before any model runs it. Prefer answering "
               "without execution when reading suffices; reuse a listed task or workflow when "
               "it does the same work with different inputs; otherwise let the conversation "
               "execute it. Attach a skill only when it clearly raises the quality of this "
               "request.")


class RouteError(RuntimeError):
    """候補の形が違う・依頼が空。"""


# ---------------------------------------------------------------------------
# 候補と状態
# ---------------------------------------------------------------------------
def _items(raw, kind: str, *, key: str) -> "list[dict]":
    items = raw.get(kind) if isinstance(raw, dict) else None
    if items is None:
        return []
    if not isinstance(items, list):
        raise RouteError(f"candidates.{kind} は配列です")
    out: "list[dict]" = []
    seen: set = set()
    for item in items:
        if not isinstance(item, dict):
            raise RouteError(f"candidates.{kind} の要素はオブジェクトです")
        ident = str(item.get(key) or "").strip()
        if not ident:
            raise RouteError(f"candidates.{kind} の要素に {key} が要ります")
        if ident in seen:
            continue
        seen.add(ident)
        out.append({"id": ident, "name": str(item.get("name") or ident).strip(),
                    "description": str(item.get("description") or "").strip()})
    if len(out) > MAX_CHOICES:
        raise RouteError(f"candidates.{kind} は {MAX_CHOICES} 件までです（呼び出し側で絞る）")
    return out


def normalize_candidates(raw) -> dict:
    """{"tasks": [{id,name,description}], "flows": [...], "skills": [{name,description}],
    "context": {"repo", "attachments", "readonly"}} を検査して揃える。"""
    if not isinstance(raw, dict):
        raise RouteError("candidates はオブジェクト（tasks / flows / skills / context）です")
    context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
    attachments = context.get("attachments")
    return {"tasks": _items(raw, "tasks", key="id"),
            "flows": _items(raw, "flows", key="id"),
            "skills": _items(raw, "skills", key="name"),
            "context": {"repo": str(context.get("repo") or "").strip(),
                        "attachments": [str(a) for a in attachments] if isinstance(attachments, list) else [],
                        "readonly": bool(context.get("readonly"))}}


def build_state(prompt: str, candidates: dict) -> dict:
    text = str(prompt or "")
    excerpt = text[:modelselect.EXCERPT_CHARS] + ("…" if len(text) > modelselect.EXCERPT_CHARS else "")
    context = candidates["context"]
    return {"request": {"excerpt": excerpt, "chars": len(text),
                        "attachments": list(context["attachments"]),
                        "repo": context["repo"], "readonly": context["readonly"]},
            "tasks": [dict(t) for t in candidates["tasks"]],
            "flows": [dict(f) for f in candidates["flows"]],
            "skills": [dict(s) for s in candidates["skills"]],
            "policy": POLICY_LINE}


def _describe(item: dict) -> str:
    return f"{item['name']}: {item['description']}" if item["description"] else item["name"]


def build_questions(candidates: dict) -> dict:
    """候補から問いの集合を組む。読み取り専用の依頼は `handling` を訊かない（answer と
    converse の差が無い）。候補の無い種類の問いと選択肢は組まない。"""
    questions: dict = {}
    tasks, flows, skills = candidates["tasks"], candidates["flows"], candidates["skills"]
    if not candidates["context"]["readonly"]:
        criteria = {HANDLING_ANSWER: HANDLINGS[HANDLING_ANSWER],
                    HANDLING_CONVERSE: HANDLINGS[HANDLING_CONVERSE]}
        if tasks:
            criteria[HANDLING_TASK] = HANDLINGS[HANDLING_TASK]
        if flows:
            criteria[HANDLING_FLOW] = HANDLINGS[HANDLING_FLOW]
        questions[QUESTION_HANDLING] = {
            "type": "choice", "instructions": "How should this request be handled?",
            "criteria": criteria, "other": HANDLING_OTHER}
    for name, items, label in ((QUESTION_TASK, tasks, "task"), (QUESTION_FLOW, flows, "workflow")):
        if not items:
            continue
        if len(items) == 1:
            # judge の choice は選択肢 2 つ以上。候補 1 件は「それか、違うか」の boolean で訊く。
            questions[name] = {
                "type": "boolean", "candidate": items[0]["id"],
                "instructions": f"Is the listed {label} '{_describe(items[0])}' the same work "
                                "as this request, with only its inputs differing?"}
            continue
        questions[name] = {
            "type": "choice", "instructions": f"If a listed {label} is reused, which one?",
            "criteria": {item["id"]: _describe(item) for item in items},
            "other": f"None of the listed {label}s does this work."}
    for skill in skills:
        questions[SKILL_PREFIX + skill["id"]] = {
            "type": "boolean",
            "instructions": f"Would attaching the skill '{_describe(skill)}' clearly raise "
                            "the quality of the result for this request?"}
    questions[QUESTION_ROUTINE] = {
        "type": "boolean",
        "instructions": "Is this request a recurring shape that will be repeated later with "
                        "only its inputs (dates, targets, names) changed?"}
    return questions


# ---------------------------------------------------------------------------
# 答えの整形
# ---------------------------------------------------------------------------
def _usable(answer: "dict | None", threshold: float) -> "tuple[bool, str]":
    if not isinstance(answer, dict):
        return False, "missing"
    if answer.get("method") == judge.METHOD_TEXT:
        return False, "no-confidence"
    if float(answer.get("confidence") or 0.0) < threshold:
        return False, "low-confidence"
    return True, "ok"


def _choice(answer: "dict | None", threshold: float) -> "tuple[dict | None, str]":
    usable, why = _usable(answer, threshold)
    if not usable:
        return None, why
    choice = str(answer.get("choice") or "")
    if choice == OTHER_KEY:
        return None, "none-of-them"
    return {"choice": choice, "confidence": answer.get("confidence"),
            "probabilities": answer.get("probabilities")}, "chosen"


def _boolean(answer: "dict | None", threshold: float) -> "tuple[dict | None, str]":
    usable, why = _usable(answer, threshold)
    if not usable:
        return None, why
    return {"value": bool(answer.get("value")), "probability": answer.get("probability"),
            "confidence": answer.get("confidence")}, "ok"


def _reuse(answer: "dict | None", question: dict, threshold: float) -> "tuple[dict | None, str]":
    """task / flow の答え。候補 1 件の boolean は yes をその候補の choice に写す。"""
    if question.get("type") != "boolean":
        return _choice(answer, threshold)
    picked, why = _boolean(answer, threshold)
    if picked is None:
        return None, why
    if not picked["value"]:
        return None, "none-of-them"
    return {"choice": str(question["candidate"]), "confidence": picked["confidence"],
            "probabilities": (answer or {}).get("probabilities")}, "chosen"


# 「決めていない」理由。決めた上での「どれでもない」「no」は棄権ではない。
ABSTAIN_REASONS = ("missing", "no-confidence", "low-confidence")


def shape(answers: dict, questions: dict, *, threshold: float, hold_threshold: float) -> dict:
    """1 つの段の答えの集合を結果の形へ。`decided` は `handling` を決めたか（訊いていなければ真）。
    `abstained` は確度が足りず決めていない問い（「どれでもない」「no」は入れない）。"""
    abstained: "list[str]" = []
    out: dict = {"handling": None, "task": None, "flow": None, "skills": [], "routine": None,
                 "hold": False, "abstained": abstained, "decided": False, "outcome": ""}

    def note(name: str, why: str) -> None:
        if why in ABSTAIN_REASONS:
            abstained.append(name)

    if QUESTION_HANDLING in questions:
        picked, why = _choice(answers.get(QUESTION_HANDLING), threshold)
        out["handling"], out["outcome"], out["decided"] = picked, why, picked is not None
        note(QUESTION_HANDLING, why)
    else:
        out["decided"], out["outcome"] = True, "no-handling-question"
    for name in (QUESTION_TASK, QUESTION_FLOW):
        if name in questions:
            out[name], why = _reuse(answers.get(name), questions[name], threshold)
            note(name, why)
    for name in questions:
        if name.startswith(SKILL_PREFIX):
            picked, why = _boolean(answers.get(name), threshold)
            note(name, why)
            if picked and picked["value"]:
                out["skills"].append({"name": name[len(SKILL_PREFIX):],
                                      "probability": picked["probability"]})
    out["skills"].sort(key=lambda s: -float(s["probability"] or 0.0))
    if QUESTION_ROUTINE in questions:
        out["routine"], why = _boolean(answers.get(QUESTION_ROUTINE), threshold)
        note(QUESTION_ROUTINE, why)
    handling = out["handling"]
    if handling and handling["choice"] in (HANDLING_TASK, HANDLING_FLOW):
        target = out[handling["choice"]]
        out["hold"] = bool(target) and float(handling["confidence"] or 0.0) >= hold_threshold \
            and float(target["confidence"] or 0.0) >= hold_threshold
    return out


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def min_confidence_setting() -> float:
    value = herdconfig.route_setting().get("min_confidence")
    return float(value) if value is not None else modelselect.min_confidence_setting()


def hold_min_confidence_setting() -> float:
    value = herdconfig.route_setting().get("hold_min_confidence")
    return float(value) if value is not None else DEFAULT_HOLD_MIN_CONFIDENCE


def judge_model_setting() -> "str | None":
    """judge を使えるならそのモデル名。`off` は None、指名があればそれ、`auto` は既定モデル
    （`agent-herd judge` と同じ。実行の定義が無いので「ローカル候補があるとき」の門は持たない）。"""
    current = judge.setting()
    if current["mode"] == "off":
        return None
    return current["model"] if current["mode"] == "pinned" else judge.DEFAULT_MODEL


def route(prompt: str, candidates, *, min_confidence: "float | None" = None,
          hold_min_confidence: "float | None" = None, stages: "tuple[str, ...]" = STAGES,
          jev_request=None, judge_request=None, jev_setting_override: "dict | None" = None,
          judge_model: "str | None" = None) -> dict:
    """依頼と候補から扱いを決める。

    戻り値:
    {"handling": {"choice", "confidence", "probabilities"} | None, "task": 同 | None,
     "flow": 同 | None, "skills": [{"name", "probability"}], "routine": {"value", "probability"} | None,
     "hold": bool, "stage": jev|judge|None, "abstained": [問いの名前], "attempts": [...],
     "usage": {"tokens_in", "tokens_out"}, "state": 状態, "questions": [問いの名前]}

    `stage` が None なら決めていない（呼び出し側は従来の動きへ倒す）。
    `jev_request` / `judge_request` はテストと差し替え用。
    """
    text = str(prompt or "")
    if not text.strip():
        raise RouteError("prompt が空です")
    normalized = normalize_candidates(candidates)
    state = build_state(text, normalized)
    questions = build_questions(normalized)
    threshold = min_confidence if min_confidence is not None else min_confidence_setting()
    hold = hold_min_confidence if hold_min_confidence is not None else hold_min_confidence_setting()
    usage = {"tokens_in": 0, "tokens_out": 0}
    attempts: "list[dict]" = []
    result = {"handling": None, "task": None, "flow": None, "skills": [], "routine": None,
              "hold": False, "stage": None, "abstained": [], "reason": "",
              "attempts": attempts, "usage": usage, "state": state,
              "questions": list(questions)}
    model = judge_model or judge_model_setting()
    asked = modelselect.ask_stages(state, questions, stages=stages, attempts=attempts,
                                   usage=usage, jev_setting_override=jev_setting_override,
                                   jev_request=jev_request, judge_model=model,
                                   judge_request=judge_request)
    for stage, answers in asked:
        if answers is None:
            continue
        shaped = shape(answers, questions, threshold=threshold, hold_threshold=hold)
        handling = shaped["handling"] or {}
        # 記録は生の答え（決めなかった段でも、何をどの確度で指したかが読めるように）。
        raw = answers.get(QUESTION_HANDLING) or next(iter(answers.values()))
        attempts.append({"stage": stage, "outcome": shaped.pop("outcome"),
                         "handling": raw.get("choice") if QUESTION_HANDLING in answers else None,
                         "confidence": raw.get("confidence"), "method": raw.get("method"),
                         "abstained": list(shaped["abstained"])})
        if shaped.pop("decided"):
            result.update(shaped)
            result["stage"] = stage
            result["reason"] = f"{stage} が確度 {float(handling.get('confidence') or 0):.2f} で扱いを決定" \
                if handling else f"{stage} が答えた（handling は訊いていない）"
            return result
    result["reason"] = "どの段も扱いを決められませんでした"
    return result
