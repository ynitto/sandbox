"""状態ごと候補を巡回させる案の定数（choice_retry と同一）。"""
import sys, json, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/agent-tools/agentcore"))
from agentcore import judge, modelselect as ms, modelfit

REQ = modelfit.REQUIREMENTS   # 本番と同じ要求水準の問い
POLICY = ("Select the model with the reasoning capacity appropriate to the request. First distinguish "
          "routine transformations or factual replies from tasks requiring investigation, synthesis, "
          "judgment or multiple dependent steps. For routine tasks, minimize cost. For demanding tasks, "
          "prioritize reasoning quality and reliability rather than low price. Tool-loop only means tool "
          "access, not equal intelligence. Neither a short request nor a low token estimate implies a "
          "simple task. Do not choose a larger model for a quoted task when only translation is requested. "
          "Model capability may be uncertain: consider the supplied model identity and relevant evaluation "
          "evidence without inventing scores.")
ASK = ("Given the assessed task requirements, which candidate's reasoning capacity best matches this task? "
       "Avoid spending on unnecessary capability for routine work; avoid trading away needed reasoning "
       "quality for demanding work.")

holdout = json.loads((ROOT / "docs/experiments/2026-09-21-selection/independent-holdout-input.json").read_text())
dev = [("greeting", "こんにちは", "local"), ("translation", "「追加機能を提案してほしい」を英訳して", "local"),
       ("fact", "日本の首都はどこですか", "local"), ("proposal", "agent-appの追加機能を提案してほしい", "cloud"),
       ("debug", "agent-appで送信すると画面が崩れる。コードとログを調査し、原因を特定して修正し、回帰テストで検証して。", "cloud"),
       ("architecture", "複数ノード間のジョブ重複実行を防ぐ設計を提案して。ネットワーク分断、再起動、冪等性、既存DBとの整合性を考慮して。", "cloud")]
CASES = [(c["id"], c["prompt"], "local" if c["group"] == "simple" else "cloud") for c in holdout] + dev
NAMES = ["cursor", "codex", "claude", "ollama/gemma4:e4b"]
MODEL, MIN_CONF = "gemma4:e4b", 0.6


def build(prompt, fit, requirements):
    state = ms.build_state(ms.prompt_profile(prompt, purpose="work"), fit)
    state.pop("policy", None)
    costs = {c["id"]: {"cost": c.pop("relative_cost", None), "quota": c.pop("quota", None)}
             for c in state["candidates"]}
    for c in state["candidates"]: c.pop("site", None)
    state["economics"], state["policy"] = costs, POLICY
    state["task"]["requirements"] = requirements
    q = ms.build_question(fit)
    q["instructions"] = ASK
    q["criteria"] = {c["id"]: "Use the candidate's model and evidence; execution cost is listed separately." for c in fit}
    return state, judge.normalize_question(ms.QUESTION_NAME, q)

def select(prompt, fit, turns):
    req = judge.evaluate({"request": prompt}, {"requirements": REQ}, model=MODEL, rotations=1)
    requirements = req["answers"]["requirements"]
    ids = [c["id"] for c in fit]
    readings = []
    for k in range(turns):
        rotated = fit[k:] + fit[:k]                       # 状態も問いも同じ並びで回す
        state, q = build(prompt, [dict(c) for c in rotated], requirements)
        data = judge.post_chat(judge._payload(MODEL, judge.build_prompt(judge.render_state(state), q),
                                              think=False, options=None, readout_mode=True))
        read = judge.readout(data.get("logprobs"), len(q["options"]))
        if read is None: continue
        probs = judge._normalize(read[0])
        # 宣言順（ids + other）へ戻す
        canonical = [0.0] * len(q["options"])
        for pos, (key, _) in enumerate(q["options"]):
            canonical[ids.index(key) if key in ids else len(ids)] = probs[pos]
        readings.append(canonical)
    if not readings: return None, None, 0.0
    probs, agreement = (readings[0], 1.0) if len(readings) == 1 else judge.average_orderings(readings)
    top = max(range(len(probs)), key=lambda i: probs[i])
    pick = (ids + [ms.OTHER_KEY])[top]
    conf = probs[top]
    if conf < MIN_CONF or pick == ms.OTHER_KEY:
        ordered = ms.audit_order(fit)
        pick = ordered[0]["id"] if ordered else None
    return pick, agreement, conf

rows = []
for order in ("normal", "reverse"):
    base = [ms.describe_candidate(c, project_dir=ROOT, quotas={}) for c in ms.normalize_candidates(NAMES)]
    if order == "reverse": base.reverse()
    site = {c["id"]: c["site"] for c in base}
    for turns in (2, 4):
        for cid, prompt, expected in CASES:
            t = time.monotonic()
            try:
                pick, agree, conf = select(prompt, [dict(c) for c in base], turns)
                err = None
            except Exception as exc:
                pick, agree, conf, err = None, None, None, f"{type(exc).__name__}: {exc}"
            rows.append({"order": order, "variant": f"state_r{turns}", "case": cid, "expected": expected,
                         "selected": pick, "actual": site.get(pick), "agreement": agree,
                         "confidence": conf, "wall": round(time.monotonic() - t, 2), "error": err})
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
Path(sys.argv[1]).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
