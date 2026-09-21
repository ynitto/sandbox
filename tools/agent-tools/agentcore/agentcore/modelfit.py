"""Price-blind local model suitability assessment.

Each candidate is evaluated in isolation. Values are judge estimates, not measured
task success rates. Price is consulted only within FIT_TIE_MARGIN of the best fit.
"""
from __future__ import annotations
import time
from agentcore import judge

FIT_TIE_MARGIN = 0.01
FIT_TIMEOUT_SECONDS = 75.0
REQUIREMENTS = {"type":"choice","instructions":"Assess the reasoning needed to perform the actual request, not the task mentioned inside quoted text. Translation, extraction and reformatting of supplied text are routine even when that text discusses complex work.",
"criteria":{"routine":"Self-contained factual reply or straightforward transformation of supplied content.",
"demanding":"Requires investigation, synthesis, design judgment, or multiple dependent steps."},
"other":"Insufficient information."}
FIT = {"type":"boolean","instructions":"Is this execution candidate a good capability match for completing the actual request correctly? Assess the required reasoning, investigation and verification against the model and relevant evidence. Tool access does not establish reasoning quality. Do not treat missing evaluations as proof of either competence or failure. Evaluate only this candidate; ignore its brand prestige and deployment location. Answer yes for a credible adequate match, no if the task needs capabilities or reliability it is unlikely to supply."}

def evaluate(state, *, model, rank, min_confidence, request=None, samples=1):
    usage = {"tokens_in": 0, "tokens_out": 0}
    deadline = time.monotonic() + FIT_TIMEOUT_SECONDS
    def ask(material, questions):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise judge.JudgeError("候補ごとの適合判定が制限時間を超えました")
        result = judge.evaluate(material, questions, model=model, request=request, samples=samples,
                                timeout=min(remaining, judge._request_timeout_sec()))
        for key in usage:
            usage[key] += int((result.get("usage") or {}).get(key) or 0)
        return result["answers"]

    prompt = (state.get("task") or {}).get("excerpt", "")
    requirements = ask({"request": prompt}, {"requirements": REQUIREMENTS})["requirements"]
    reliable = (requirements.get("method") != judge.METHOD_TEXT
                and float(requirements.get("confidence") or 0) >= min_confidence
                and requirements.get("choice") in ("routine", "demanding"))
    context = requirements if reliable else {"choice": "unknown", "reason": "requirements not confidently assessed"}
    fits, scores = {}, {}
    candidates = state.get("candidates") or []
    for candidate in candidates:
        # No other candidate, price, location, quota or ranking enters this probe.
        material = {"request": prompt, "requirements": context, "candidate": {
            k: v for k, v in candidate.items()
            if k not in ("site", "relative_cost", "quota", "rank")
        }}
        if isinstance(material["candidate"].get("rating"), dict):
            material["candidate"]["rating"] = {
                k: v for k, v in material["candidate"]["rating"].items()
                if k not in ("average_tokens", "rank")
            }
        material["candidate"]["model_identity"] = {
            "model": candidate.get("model") or None,
            "source": "explicit-or-definition-default" if candidate.get("model") else "provider-default-not-resolved",
        }
        material["candidate"]["measurement_status"] = (
            "available" if (candidate.get("rating") or {}).get("runs", 0) else "not measured"
        )
        answer = ask(material, {"fit": FIT})["fit"]
        fits[candidate["id"]] = answer
        if answer.get("method") != judge.METHOD_TEXT:
            scores[candidate["id"]] = float(answer.get("probability") or 0)

    best = max(scores.values(), default=0)
    eligible = [c for c in candidates if c["id"] in scores
                and scores[c["id"]] >= max(min_confidence, best - FIT_TIE_MARGIN)]
    # Stable tie-break: declared cost/ratings still apply, input order never does.
    chosen = rank(sorted(eligible, key=lambda c: c["id"]))[0] if eligible else None
    return {
        "type": "choice", "choice": chosen["id"] if chosen else "none",
        "confidence": scores[chosen["id"]] if chosen else 0,
        "probabilities": None, "method": "independent-fit" if scores else judge.METHOD_TEXT,
        "requirements": requirements, "candidate_fits": fits, "fit_tie_margin": FIT_TIE_MARGIN,
        # A completed negative suitability assessment must not become a cheapest-model fallback.
        "abstain": bool(not chosen and scores),
        "usage": usage, "model": model,
    }
