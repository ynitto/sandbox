"""agentcore.modelselect — 呼び出し 1 件に使うエージェント・モデルを、プロンプトを見て選ぶ。

## 何をするモジュールか

「この prompt を、いま手元にある候補（agent_cli + model）のどれに任せるか」を決める。
柱 3（資源効率）の「仕事を性質で格付けし、最小で足るモデルへ流す」を、**実行直前に
prompt そのものを材料にして**行う口で、根拠は 3 つ:

- **候補の特性** — 定義（agents/<name>.json）の `relative_cost`・ローカルかクラウドか・
  自律度（single-shot / tool-loop）と、agent-audit の格付け（用途ごとの PASS 率・平均消費）
- **トークン量** — prompt の推定トークン数と、候補の文脈上限・node-budget の残量
- **利用制限** — node-budget 台帳に残る quota 観測（枯渇・レート制限と復帰時刻）

## 判断の順（フォールバック）

| 段 | 何で決めるか | 使う条件 |
|---|---|---|
| 1 `jev` | 本家 Jev（TypeSafe AI の System One API）。状態 + choice 1 問 | API キーが設定にある（`select.jev.api_key` か環境変数 `TYPESAFE_API_KEY`） |
| 2 `judge` | agent-herd judge（LAN の ollama で 1 トークン目の分布を読む） | `judge.model` が `off` でなく、指名があるか候補にローカル定義がある |
| 3 `audit` | agent-audit の格付けによる決定的な順位（PASS 率 → 平均消費 → policy の rank → relative_cost） | いつでも（最後の砦。LLM を呼ばない） |

上の段が「使えない」「答えを読めない」「確度が `select.min_confidence` に届かない」
「どれでもない（other）」のどれかなら次の段へ倒す。**どの段が決めたかを隠さない**
（結果の `stage` と `attempts` に残す）。

LLM に訊く前に決定的に落とせる候補は落とす（`prefilter`）: quota が枯渇・レート制限中、
文脈上限が prompt に足りない、node-budget 超過の縮退指定でクラウド候補を避ける。
残りが 1 件なら LLM を呼ばない——判断の要らない場面で判断のトークンを払わない。

## 使い方

- CLI: `agent-herd select --purpose worker --candidate claude --candidate ollama/gemma4:e4b < prompt`
- Python: `modelselect.select(prompt, candidates, purpose=…)`
- Resolver への差し込み: `executionresolver.resolve_execution(..., selector=modelselect.resolver_selector(prompt))`
  ——selection_policy の適格候補が複数あるとき、その中からだけ選ぶ（policy の外へは出ない）。

設計: docs/plans/2026-09-20-agent-tools-model-selection-design.md。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.error
import urllib.request

from agentcore import herdconfig, judge, nodebudget

STAGE_JEV = "jev"
STAGE_JUDGE = "judge"
STAGE_AUDIT = "audit"
STAGES = (STAGE_JEV, STAGE_JUDGE, STAGE_AUDIT)

QUESTION_NAME = "candidate"
OTHER_KEY = "none"
# 確度の下限の既定。judge の較正（§6）と同じく実測前の置き値で、設定 `select.min_confidence` が勝つ。
DEFAULT_MIN_CONFIDENCE = 0.6
# prompt を状態に載せるときの上限（先頭）。全文を送ると判断のトークンが実行のトークンに並ぶ。
EXCERPT_CHARS = 1200
# レート制限の観測に復帰時刻が無いときの失効（agent-audit usage と同じ 1 時間）。
RATE_LIMIT_TTL_SEC = 3600
CHARS_PER_TOKEN = 4.0

JEV_DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_DEFAULT_MODEL = "jev-latest"
JEV_API_KEY_ENV = "TYPESAFE_API_KEY"
JEV_TIMEOUT_SEC = 30.0

POLICY_LINE = ("Prefer the cheapest candidate whose capability, context window and remaining "
               "quota suffice for this task. Reserve costly cloud models for work that needs "
               "judgment, long context or high reliability. Avoid candidates whose quota is "
               "nearly used up when a fitting alternative exists.")


class SelectError(RuntimeError):
    """候補の形が違う・prompt が空。"""


# ---------------------------------------------------------------------------
# 候補の正規化と特性
# ---------------------------------------------------------------------------
def candidate_id(candidate: dict) -> str:
    return f"{candidate['agent_cli']}/{candidate.get('model') or ''}".rstrip("/")


def parse_candidate(text: str) -> dict:
    """`cli` / `cli/model`（model に `/` を含む綴りは最初の `/` で切る）。"""
    raw = str(text or "").strip()
    if not raw:
        raise SelectError("候補が空です")
    cli, _, model = raw.partition("/")
    cli = cli.strip().lower()
    if not cli:
        raise SelectError(f"候補の綴りが不正です: {text!r}")
    return {"agent_cli": cli, "model": model.strip()}


def normalize_candidates(candidates) -> "list[dict]":
    out: "list[dict]" = []
    seen: "set[str]" = set()
    for raw in candidates or []:
        if isinstance(raw, str):
            item = parse_candidate(raw)
        elif isinstance(raw, dict) and str(raw.get("agent_cli") or "").strip():
            item = dict(raw)
            item["agent_cli"] = str(item["agent_cli"]).strip().lower()
            item["model"] = str(item.get("model") or "").strip()
        else:
            raise SelectError(f"候補はオブジェクト（agent_cli / model）か `cli/model` の文字列です: {raw!r}")
        cid = candidate_id(item)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(item)
    if not out:
        raise SelectError("候補が 1 つもありません")
    return out


def _spec_of(candidate: dict, project_dir=None) -> "dict | None":
    from agentcore import agentcli
    try:
        return agentcli.load_cli(candidate["agent_cli"], project_dir=project_dir)
    except Exception:                       # noqa: BLE001  定義が解けない候補も選択肢には残す
        return None


def describe_candidate(candidate: dict, *, project_dir=None, quotas: "dict | None" = None,
                       ratings: "list[dict] | None" = None, purpose: str = "") -> dict:
    """候補 1 件に、判断の材料（特性・格付け・quota・文脈）を付けた dict。

    候補自身が持つ値（`relative_cost` / `context_tokens` / `rating` など）は定義より勝つ
    ——呼び出し側が実測を持っているならそちらが新しい。
    """
    from agentcore import agentcli
    spec = _spec_of(candidate, project_dir)
    model = candidate.get("model") or ((spec or {}).get("default_model") or "")
    out = {"agent_cli": candidate["agent_cli"], "model": str(model or ""),
           "id": candidate_id({"agent_cli": candidate["agent_cli"], "model": model})}
    if spec is not None:
        cost = agentcli.resolve_relative_cost(spec, model or None)
        out["relative_cost"] = cost
        out["site"] = "local" if cost == 0 else "cloud"
        out["autonomy"] = str(spec.get("headless_autonomy") or "")
        out["definition"] = str(spec.get("name") or candidate["agent_cli"])
    else:
        out["relative_cost"] = None
        out["site"] = "unknown"
        out["autonomy"] = ""
        out["definition"] = None
    for key in ("relative_cost", "context_tokens", "rank", "status", "qualification_refs",
                "site", "notes"):
        if candidate.get(key) is not None:
            out[key] = candidate[key]
    if isinstance(candidate.get("rating"), dict):
        out["rating"] = dict(candidate["rating"])
    else:
        rating = _rating_for(out, ratings, purpose)
        if rating:
            out["rating"] = rating
    quota = (quotas or {}).get(out["agent_cli"])
    if isinstance(quota, dict):
        out["quota"] = dict(quota)
    return out


def _rating_for(candidate: dict, ratings, purpose: str) -> "dict | None":
    """`agent-audit ratings --json` の rows から、この候補・用途の行を引く。

    rows の `model` は台帳の model（無ければ agent_cli）なので、model と agent_cli の両方で
    合わせる。用途の行が無ければ用途を問わない行（purpose が `(なし)`）へ落ちる。
    """
    rows = ratings.get("rows") if isinstance(ratings, dict) else ratings
    if not isinstance(rows, list):
        return None
    keys = {candidate.get("model") or "", candidate.get("agent_cli") or ""} - {""}
    matched = [r for r in rows if isinstance(r, dict) and str(r.get("model") or "") in keys]
    if not matched:
        return None
    wanted = [r for r in matched if str(r.get("purpose") or "") == str(purpose or "")]
    row = (wanted or matched)[0]
    return {"purpose": row.get("purpose"), "pass_rate": row.get("pass_rate"),
            "average_tokens": row.get("average_tokens"), "runs": row.get("outcome_runs"),
            "rank": row.get("rank")}


# ---------------------------------------------------------------------------
# 利用制限（quota 観測）と予算
# ---------------------------------------------------------------------------
def _parse_iso(value) -> "float | None":
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def quota_observations(*, dir: "str | None" = None, now: "float | None" = None,
                       period: str = "month") -> dict:
    """node-budget 台帳の quota 観測（`event: quota | quota_snapshot`）を CLI ごとに最新 1 件へ。

    戻り値: {agent_cli: {"kind": exhausted|rate_limit|None, "reset_at": ISO|None,
    "used_percent": 0-100|None, "observed_at": ISO, "blocked": bool}}。
    `reset_at` を過ぎた観測は解けたものとして kind を落とす（台帳を消す書き手は要らない）。
    """
    now_epoch = float(now if now is not None else _dt.datetime.now(_dt.timezone.utc).timestamp())
    latest: "dict[str, tuple[float, dict]]" = {}
    for rec in nodebudget.iter_ledger_records(dir, period, now=now_epoch):
        if rec.get("event") not in ("quota", "quota_snapshot"):
            continue
        cli = str(rec.get("agent_cli") or "").strip().lower()
        ts = _parse_iso(rec.get("ts"))
        if not cli or ts is None:
            continue
        if cli not in latest or ts >= latest[cli][0]:
            latest[cli] = (ts, rec)
    out: dict = {}
    for cli, (ts, rec) in latest.items():
        kind = str(rec.get("quota_kind") or "") or None
        reset_epoch = _parse_iso(rec.get("reset_at"))
        reset_at = rec.get("reset_at") or None
        if kind == "rate_limit" and reset_epoch is None:
            reset_epoch = ts + RATE_LIMIT_TTL_SEC
            reset_at = _dt.datetime.fromtimestamp(reset_epoch, _dt.timezone.utc).isoformat()
        if reset_epoch is not None and reset_epoch <= now_epoch:
            kind, reset_at = None, None
        used = rec.get("quota_used_percent")
        try:
            used = max(0, min(100, int(used))) if used is not None else None
        except (TypeError, ValueError):
            used = None
        out[cli] = {"kind": kind, "reset_at": reset_at, "used_percent": used,
                    "observed_at": rec.get("ts"), "blocked": bool(kind)}
    return out


def budget_summary(workload: str, *, dir: "str | None" = None,
                   now: "float | None" = None) -> "dict | None":
    """node-budget の要約（判断の材料）。宣言が無ければ None。"""
    try:
        st = nodebudget.compute_state(workload, dir=dir, now=now)
    except Exception:                       # noqa: BLE001  予算が読めなくても選択は止めない
        return None
    if not st.get("has_limits"):
        return None
    return {"period": st["period"], "spent_tokens": int(st["spent_tokens"]),
            "token_limit": int(st["token_limit"]),
            "workload_spent_tokens": int(st["workload_spent_tokens"]),
            "workload_token_limit": int(st["eff_wl_tokens"]),
            "spent_minutes": round(float(st["spent_min"]), 1),
            "limit_minutes": round(float(st["limit_min"]), 1),
            "soft": bool(st["soft"]), "exceeded": bool(st["exceeded"]),
            "on_exhausted": str(st["on_exhausted"])}


# ---------------------------------------------------------------------------
# prompt の要約と決定的な絞り込み
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    chars = len(text or "")
    return max(1, int(chars / CHARS_PER_TOKEN)) if chars else 0


def prompt_profile(prompt: str, *, purpose: str = "") -> dict:
    text = str(prompt or "")
    return {"purpose": str(purpose or ""), "chars": len(text),
            "estimated_tokens": estimate_tokens(text),
            "excerpt": text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else "")}


def prefilter(described: "list[dict]", profile: dict, *, budget: "dict | None" = None
              ) -> "tuple[list[dict], list[dict]]":
    """LLM に訊く前に決定的に落とす。戻り値は (残す候補, 落とした候補と理由)。

    - quota が枯渇・レート制限中（復帰前）
    - 文脈上限（`context_tokens`）が prompt の推定トークンに足りない
    - node-budget 超過で `on_exhausted: degrade` のとき、ローカル候補が残るならクラウド候補
      （相対コスト > 0）を避ける——縮退は止めずに安い候補で続ける契約
    落とし切って 0 件になる規則は適用しない（選ぶものが無くなるより、判断に回すほうがよい）。
    """
    dropped: "list[dict]" = []
    kept: "list[dict]" = []
    need = int(profile.get("estimated_tokens") or 0)
    for cand in described:
        quota = cand.get("quota") or {}
        if quota.get("blocked"):
            dropped.append({"id": cand["id"], "reason": f"quota-{quota.get('kind')}",
                            "reset_at": quota.get("reset_at")})
            continue
        limit = cand.get("context_tokens")
        if isinstance(limit, (int, float)) and limit > 0 and need > limit:
            dropped.append({"id": cand["id"], "reason": "context-too-small",
                            "context_tokens": int(limit), "estimated_tokens": need})
            continue
        kept.append(cand)
    if budget and budget.get("exceeded") and budget.get("on_exhausted") == "degrade":
        local = [c for c in kept if c.get("relative_cost") == 0]
        if local and len(local) < len(kept):
            for cand in kept:
                if cand.get("relative_cost") != 0:
                    dropped.append({"id": cand["id"], "reason": "budget-degrade"})
            kept = local
    if not kept:
        # 全部落ちたなら理由だけ残して全候補を判断へ回す（park は呼び出し側の契約）。
        return list(described), dropped
    return kept, dropped


# ---------------------------------------------------------------------------
# 状態と問い
# ---------------------------------------------------------------------------
def _criterion_line(cand: dict) -> str:
    parts = [f"{cand.get('site', 'unknown')}"]
    if cand.get("relative_cost") is not None:
        parts.append(f"relative_cost={cand['relative_cost']:g}")
    if cand.get("autonomy"):
        parts.append(cand["autonomy"])
    rating = cand.get("rating") or {}
    if rating.get("pass_rate") is not None:
        parts.append(f"pass_rate={float(rating['pass_rate']):.0%}")
    if rating.get("average_tokens") is not None:
        parts.append(f"avg_tokens={float(rating['average_tokens']):.0f}")
    if cand.get("context_tokens"):
        parts.append(f"context={int(cand['context_tokens'])}")
    quota = cand.get("quota") or {}
    if quota.get("used_percent") is not None:
        parts.append(f"quota_used={int(quota['used_percent'])}%")
    if cand.get("notes"):
        parts.append(str(cand["notes"]))
    return ", ".join(parts)


def build_state(profile: dict, fit: "list[dict]", *, budget: "dict | None" = None,
                dropped: "list[dict]" = ()) -> dict:
    return {
        "task": dict(profile),
        "budget": budget,
        "candidates": [
            {k: v for k, v in cand.items() if k in (
                "id", "agent_cli", "model", "site", "relative_cost", "autonomy",
                "context_tokens", "rating", "quota", "rank", "status", "notes")}
            for cand in fit],
        "excluded": list(dropped),
        "policy": POLICY_LINE,
    }


def build_question(fit: "list[dict]") -> dict:
    criteria = {cand["id"]: _criterion_line(cand) for cand in fit}
    return {"type": "choice",
            "instructions": "Which candidate should run this task?",
            "criteria": criteria,
            "other": "None of the listed candidates should run it."}


# ---------------------------------------------------------------------------
# 段 1: 本家 Jev
# ---------------------------------------------------------------------------
def jev_setting() -> dict:
    """設定ファイル `select.jev` と環境変数から Jev の接続情報を 1 つに。"""
    current = herdconfig.select_setting()
    jev = dict(current.get("jev") or {})
    if not jev.get("api_key"):
        env_key = os.environ.get(JEV_API_KEY_ENV, "").strip()
        if env_key:
            jev["api_key"] = env_key
            jev["source"] = "env"
    jev.setdefault("endpoint", JEV_DEFAULT_ENDPOINT)
    jev.setdefault("model", JEV_DEFAULT_MODEL)
    jev["enabled"] = bool(jev.get("api_key")) and not jev.get("off")
    return jev


def jev_payload(state: dict, question: dict, *, model: str) -> dict:
    """Jev の `/v1/systemone` の body。`other` は Jev には無いので選択肢 `none` として並べる。"""
    criteria = dict(question["criteria"])
    if question.get("other"):
        criteria[OTHER_KEY] = str(question["other"])
    return {"model": model, "state": state,
            "questions": {QUESTION_NAME: {"type": "choice",
                                          "instructions": question["instructions"],
                                          "criteria": criteria}}}


def post_jev(payload: dict, *, endpoint: str, api_key: str,
             timeout: float = JEV_TIMEOUT_SEC) -> dict:
    req = urllib.request.Request(
        endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.load(res)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise SelectError(f"Jev API error ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise SelectError(f"Jev に接続できません: {exc.reason}") from exc
    except (TimeoutError, ValueError) as exc:
        raise SelectError(f"Jev の応答を読めません: {exc}") from exc
    if not isinstance(data, dict):
        raise SelectError("Jev がオブジェクト以外を返しました")
    return data


def read_jev_answer(data: dict, question: dict) -> dict:
    """Jev の応答を judge と同じ形（choice / probabilities / confidence / method）へ。"""
    answers = data.get("answers")
    answer = answers.get(QUESTION_NAME) if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        raise SelectError("Jev の応答に答えがありません")
    probs = answer.get("probabilities")
    probs = {str(k): float(v) for k, v in probs.items()} if isinstance(probs, dict) else {}
    choice = answer.get("choice")
    if choice is None and probs:
        choice = max(probs, key=probs.get)
    choice = str(choice or "")
    known = set(question["criteria"]) | ({OTHER_KEY} if question.get("other") else set())
    if choice not in known:
        raise SelectError(f"Jev の答え {choice!r} は選択肢にありません")
    confidence = answer.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else probs.get(choice, 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {"type": "choice", "choice": choice, "probabilities": probs,
            "confidence": round(confidence, 4), "coverage": 1.0, "method": "jev",
            "model": data.get("model"),
            "usage": {"tokens_in": int(usage.get("input_tokens") or 0),
                      "tokens_out": int(usage.get("output_tokens") or 0)}}


def ask_jev(state: dict, question: dict, *, setting: "dict | None" = None,
            request=None) -> dict:
    """段 1。戻り値は judge と同形の答え。使えなければ SelectError。"""
    setting = setting if setting is not None else jev_setting()
    if not setting.get("enabled"):
        raise SelectError("Jev の API キーが設定にありません")
    payload = jev_payload(state, question, model=str(setting.get("model") or JEV_DEFAULT_MODEL))
    send = request or (lambda body: post_jev(body, endpoint=str(setting["endpoint"]),
                                             api_key=str(setting["api_key"])))
    return read_jev_answer(send(payload), question)


# ---------------------------------------------------------------------------
# 段 2: agent-herd judge
# ---------------------------------------------------------------------------
def judge_model_for(fit: "list[dict]") -> "str | None":
    """judge を使えるなら、そのモデル名。判定の門は `judge.model_for_spec` と同じ。

    `off` は None。指名があればそれ。`auto` は候補にローカル定義（相対コスト 0）があるとき
    だけ——judge は LAN の ollama を直に叩くので、クラウド候補しか無い場面で叩きに行かない。
    """
    current = judge.setting()
    if current["mode"] == "off":
        return None
    if current["mode"] == "pinned":
        return current["model"]
    local = [c for c in fit if c.get("relative_cost") == 0 and c.get("model")]
    if not local:
        return None
    return str(local[0]["model"])


def ask_judge(state: dict, question: dict, *, model: str, request=None,
              samples: int = 1) -> dict:
    """段 2。judge の答えをそのまま（`other` は judge が付けた `other` キー）。"""
    try:
        result = judge.evaluate(state, {QUESTION_NAME: question}, model=model,
                                samples=samples, request=request)
    except judge.JudgeError as exc:
        raise SelectError(str(exc)) from exc
    answer = dict(result["answers"][QUESTION_NAME])
    if answer.get("choice") == judge.OTHER_KEY:
        answer["choice"] = OTHER_KEY
    answer["model"] = result.get("model")
    answer["usage"] = dict(result.get("usage") or {})
    return answer


# ---------------------------------------------------------------------------
# 段 3: agent-audit の格付けによる決定的な順位
# ---------------------------------------------------------------------------
def audit_order(fit: "list[dict]") -> "list[dict]":
    """LLM を呼ばない順位。格付け（PASS 率が高く、平均消費が少ない）→ policy の rank →
    relative_cost（安い順）→ 宣言順。格付けの無い候補は格付けのある候補の後ろ。"""
    def key(pair):
        index, cand = pair
        rating = cand.get("rating") or {}
        has_rating = rating.get("pass_rate") is not None
        pass_rate = float(rating["pass_rate"]) if has_rating else 0.0
        avg = rating.get("average_tokens")
        avg = float(avg) if isinstance(avg, (int, float)) else float("inf")
        rank = cand.get("rank")
        rank = int(rank) if isinstance(rank, int) and not isinstance(rank, bool) else 10 ** 6
        cost = cand.get("relative_cost")
        cost = float(cost) if isinstance(cost, (int, float)) else float("inf")
        return (0 if has_rating else 1, -pass_rate, avg, rank, cost, index)
    return [cand for _i, cand in sorted(enumerate(fit), key=key)]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def min_confidence_setting() -> float:
    value = herdconfig.select_setting().get("min_confidence")
    return float(value) if isinstance(value, (int, float)) else DEFAULT_MIN_CONFIDENCE


def _pick(answer: dict, fit: "list[dict]", *, min_confidence: float) -> "tuple[dict | None, str]":
    """答えから候補を 1 件。決められないときは (None, 理由)。"""
    if answer.get("method") == judge.METHOD_TEXT:
        return None, "no-confidence"
    if float(answer.get("confidence") or 0.0) < min_confidence:
        return None, "low-confidence"
    choice = str(answer.get("choice") or "")
    if choice == OTHER_KEY:
        return None, "none-of-them"
    for cand in fit:
        if cand["id"] == choice:
            return cand, "chosen"
    return None, "unknown-choice"


def select(prompt: str, candidates, *, purpose: str = "", project_dir=None,
           ratings=None, quotas: "dict | None" = None, budget: "dict | None" = None,
           now: "float | None" = None, min_confidence: "float | None" = None,
           jev_request=None, judge_request=None, jev_setting_override: "dict | None" = None,
           judge_model: "str | None" = None, stages: "tuple[str, ...]" = STAGES) -> dict:
    """prompt と候補から 1 件を選ぶ。

    戻り値:
    {"selected": {"agent_cli", "model"} | None, "stage": jev|judge|audit|None, "confidence",
     "probabilities", "reason", "candidates": [...], "dropped": [...], "attempts": [...],
     "usage": {"tokens_in", "tokens_out"}, "state": 状態}

    `jev_request` / `judge_request` はテストと差し替え用（Jev の body / ollama の payload を受けて
    応答 dict を返す）。`quotas` を省くと node-budget 台帳の観測を読む。
    """
    text = str(prompt or "")
    if not text.strip():
        raise SelectError("prompt が空です")
    normalized = normalize_candidates(candidates)
    quotas = quotas if quotas is not None else quota_observations(now=now)
    described = [describe_candidate(c, project_dir=project_dir, quotas=quotas,
                                    ratings=ratings, purpose=purpose) for c in normalized]
    profile = prompt_profile(text, purpose=purpose)
    fit, dropped = prefilter(described, profile, budget=budget)
    state = build_state(profile, fit, budget=budget, dropped=dropped)
    threshold = min_confidence if min_confidence is not None else min_confidence_setting()
    usage = {"tokens_in": 0, "tokens_out": 0}
    attempts: "list[dict]" = []
    result = {"selected": None, "stage": None, "confidence": None, "probabilities": None,
              "reason": "", "candidates": [c["id"] for c in described],
              "dropped": dropped, "attempts": attempts, "usage": usage, "state": state}

    def done(cand: dict, stage: str, answer: "dict | None", reason: str) -> dict:
        result.update({"selected": {"agent_cli": cand["agent_cli"], "model": cand["model"]},
                       "stage": stage, "reason": reason,
                       "confidence": (answer or {}).get("confidence"),
                       "probabilities": (answer or {}).get("probabilities")})
        return result

    if len(fit) == 1:
        attempts.append({"stage": STAGE_AUDIT, "outcome": "single-candidate"})
        return done(fit[0], STAGE_AUDIT, None, "絞り込み後の候補が 1 件（判断は不要）")

    question = build_question(fit)
    for stage in stages:
        if stage == STAGE_JEV:
            setting = jev_setting_override if jev_setting_override is not None else jev_setting()
            if not setting.get("enabled"):
                attempts.append({"stage": stage, "outcome": "not-configured"})
                continue
            try:
                answer = ask_jev(state, question, setting=setting, request=jev_request)
            except SelectError as exc:
                attempts.append({"stage": stage, "outcome": "error", "detail": str(exc)[:200]})
                continue
        elif stage == STAGE_JUDGE:
            model = judge_model or judge_model_for(fit)
            if not model:
                attempts.append({"stage": stage, "outcome": "not-available"})
                continue
            try:
                answer = ask_judge(state, question, model=model, request=judge_request)
            except SelectError as exc:
                attempts.append({"stage": stage, "outcome": "error", "detail": str(exc)[:200]})
                continue
        else:
            ordered = audit_order(fit)
            attempts.append({"stage": stage, "outcome": "ranked",
                             "order": [c["id"] for c in ordered]})
            top = ordered[0]
            basis = ("agent-audit の格付け" if (top.get("rating") or {}).get("pass_rate") is not None
                     else "policy の順位" if top.get("rank") is not None
                     else "相対コストの低い順")
            return done(top, stage, None, f"{basis}で決定的に選択（上位の段は使えないか決めなかった）")
        for key in ("tokens_in", "tokens_out"):
            usage[key] += int((answer.get("usage") or {}).get(key) or 0)
        cand, why = _pick(answer, fit, min_confidence=threshold)
        attempts.append({"stage": stage, "outcome": why, "choice": answer.get("choice"),
                         "confidence": answer.get("confidence"), "method": answer.get("method"),
                         "model": answer.get("model")})
        if cand is not None:
            return done(cand, stage, answer,
                        f"{stage} が確度 {float(answer.get('confidence') or 0):.2f} で選択")
    result["reason"] = "どの段も候補を決められませんでした"
    return result


def resolver_selector(prompt: str, *, purpose: str = "", workload: "str | None" = None,
                      project_dir=None, ratings=None, quotas: "dict | None" = None,
                      budget: "dict | None" = None, **kwargs):
    """`executionresolver.resolve_execution(..., selector=…)` に渡す関数を作る。

    Resolver が適格候補（複数）を渡してくるので、その中から 1 件を選んで
    `{"agent_cli", "model", "stage", "confidence", "reason"}` を返す。決められなければ None
    （Resolver は従来どおり rank 順）。同じ候補集合への問い合わせは 1 回だけ（同じ呼び出しの
    中で Resolver が何度も解決し直しても、判断の LLM を何度も叩かない）。
    """
    memo: dict = {}
    resolved_budget = budget if budget is not None else (
        budget_summary(workload) if workload else None)

    def selector(candidates: "list[dict]") -> "dict | None":
        key = tuple(candidate_id({"agent_cli": c.get("agent_cli"), "model": c.get("model")})
                    for c in candidates)
        if key in memo:
            return memo[key]
        try:
            result = select(prompt, candidates, purpose=purpose, project_dir=project_dir,
                            ratings=ratings, quotas=quotas, budget=resolved_budget, **kwargs)
        except SelectError:
            memo[key] = None
            return None
        selected = result.get("selected")
        pick = None
        if selected:
            pick = {"agent_cli": selected["agent_cli"], "model": selected["model"],
                    "stage": result.get("stage"), "confidence": result.get("confidence"),
                    "reason": result.get("reason"),
                    "dropped": [d["id"] for d in result.get("dropped") or []]}
        memo[key] = pick
        return pick

    return selector
