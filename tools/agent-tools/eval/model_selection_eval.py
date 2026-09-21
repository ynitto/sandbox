#!/usr/bin/env python3
"""Offline outcome-table qualification. No production routing/config writes.

Stage observations are frozen at the ask boundary; production select does all
threshold/fallback/filtering work. Missing counterfactual observations censor a
sweep row instead of silently inventing an audit fallback. See MODEL_SELECTION.md.
"""
from __future__ import annotations

import argparse
import collections
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from unittest.mock import patch

import engine
from eval_io import new_run_dir, write_json

ms, resolver, vc = engine.selection_runtime()
HERE = Path(__file__).resolve().parent
THRESHOLDS = (.5, .6, .7, .8, .9)
OBJECTIVE = "verified-pass_then_audit-usage-policy_else_completion"


class Unobserved(RuntimeError):
    pass


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def validate(f):
    if not f.get("prompt", "").strip() or not f.get("purpose"):
        raise ValueError("prompt and purpose are required")
    if f.get("objective") != OBJECTIVE:
        raise ValueError("unsupported fixture objective")
    if f.get("horizon") not in ("short", "medium", "long"):
        raise ValueError("horizon must be short/medium/long")
    if f.get("provenance") not in ("synthetic", "measured"):
        raise ValueError("provenance must be synthetic/measured")
    if not all(isinstance(c, dict) for c in f["candidates"]):
        raise ValueError("offline candidates must be frozen metadata objects")
    candidates = ms.normalize_candidates(f["candidates"])
    if len(candidates) != len(f["candidates"]) or any(not c.get("model") for c in candidates):
        raise ValueError("unique candidates with explicit models required")
    errors = vc.plan_errors(f["verification_plan"])
    if errors:
        raise ValueError(str(errors))
    checks = f["checkpoints"]
    indices = [c["command_index"] for c in checks]
    if (not checks or len(set(indices)) != len(indices)
            or any(not isinstance(i, int) or isinstance(i, bool) or i < 0
                   or i >= len(f["verification_plan"]["commands"]) for i in indices)
            or any(not number(c["weight"]) or c["weight"] == 0 for c in checks)):
        raise ValueError("checkpoints need unique command indices and positive finite weights")


def outcome(f, cid):
    o = f.get("outcomes", {}).get(cid)
    empty = {"status": "missing-outcome", "verified_pass": None, "completion": None,
             "tokens": None, "cost": None, "currency": None, "wall_seconds": None}
    if not o:
        return empty
    result = dict(empty, status=o.get("status", "ok"),
                  tokens=o.get("tokens"), cost=o.get("cost"), currency=o.get("currency"),
                  wall_seconds=o.get("wall_seconds"))
    for key in ("tokens", "cost", "wall_seconds"):
        if not number(result[key]):
            result[key] = None
    if not result["currency"]:
        result["cost"] = None
    if result["status"] != "ok":
        return result
    receipt, rev = o.get("receipt"), o.get("result_rev")
    errors = vc.receipt_errors(receipt, plan=f["verification_plan"], expected_rev=rev or "")
    if not rev:
        errors.append("missing expected result revision")
    if errors:
        return dict(result, status="invalid-receipt", receipt_errors=errors)
    verdict = vc.receipt_overall(receipt)
    # PASS is canonical; a checkpoint never changes it. Partial completion is
    # defined only when each checkpoint was observable (not environment failure).
    commands = [receipt["commands"][c["command_index"]] for c in f["checkpoints"]]
    if all(not c.get("inconclusive") and c.get("exit_code") is not None for c in commands):
        result["completion"] = sum(
            check["weight"] * (cmd.get("exit_code") == 0 and not cmd.get("flaky"))
            for check, cmd in zip(f["checkpoints"], commands)
        ) / sum(c["weight"] for c in f["checkpoints"])
    result.update(status=verdict, verified_pass=(verdict == "pass") if verdict != "inconclusive" else None)
    return result


def replay(f, threshold):
    """Replay normalized ask results through the unchanged production selector."""
    observations = f.get("selector_observations", {})
    called = []

    def ask(stage):
        def run(*args, **kwargs):
            observation = observations.get(stage)
            called.append(stage)
            if not observation or observation.get("status") == "unobserved":
                raise Unobserved(stage)
            if observation["status"] != "answer":
                raise ms.SelectError(observation.get("detail", observation["status"]))
            return copy.deepcopy(observation["answer"])
        return run

    kwargs = dict(purpose=f["purpose"], quotas=f.get("quotas", {}),
                  ratings=f.get("ratings", []), budget=f.get("budget"), min_confidence=threshold,
                  jev_setting_override={"enabled": observations.get("jev", {}).get("status") != "not-configured"})
    fit = []
    decision = None
    selection = None
    status = "ok"
    all_dropped = False

    def choose(candidates):
        nonlocal fit, selection, all_dropped
        described = [ms.describe_candidate(c, quotas=kwargs["quotas"], ratings=kwargs["ratings"],
                                           purpose=f["purpose"]) for c in candidates]
        fit, dropped = ms.prefilter(described, ms.prompt_profile(f["prompt"], purpose=f["purpose"]),
                                    budget=f.get("budget"))
        # Retain production's all-dropped re-entry in selection, but flag its lack
        # of eligible candidates for evaluation rather than score it as an error.
        all_dropped = set(c["id"] for c in described) <= set(c["id"] for c in dropped)
        selection = ms.select(f["prompt"], candidates, **kwargs)
        selection["eval_all_dropped"] = all_dropped
        return dict(selection["selected"], stage=selection["stage"],
                    confidence=selection["confidence"], reason=selection["reason"]) if selection["selected"] else None

    # Frozen metadata must not read the user's local agent definitions or config.
    with patch.object(ms, "_spec_of", return_value=None), \
            patch.object(ms, "judge_model_for", return_value=None if observations.get("judge", {}).get("status") == "not-available" else "frozen-observation"), \
            patch.object(ms, "ask_jev", ask("jev")), patch.object(ms, "ask_judge", ask("judge")):
        try:
            if f.get("resolver"):
                # Canonical resolver owns eligibility/rank/workload/purpose gates.
                def capture(candidates):
                    nonlocal status
                    try:
                        return choose(candidates)
                    except Unobserved as exc:
                        status = "unobserved-stage:" + str(exc)
                        return None
                decision = resolver.resolve_execution(f["workload"], purpose_or_role=f["purpose"],
                                                       selector=capture, **resolver_inputs(f["resolver"]))
                if selection is None and status == "ok":
                    if decision.get("selected"):
                        selected = decision["selected"]
                        choose([next(c for c in f["candidates"] if ms.candidate_id(c) == ms.candidate_id(selected))])
                    else:
                        status = "no-eligible-candidate"
            else:
                choose(f["candidates"])
        except Unobserved as exc:
            status = "unobserved-stage:" + str(exc)
    if all_dropped:
        status = "no-eligible-candidate"
        fit = []
    if status == "ok" and not (selection or {}).get("selected"):
        status = "abstain"
    # Preserve unknowns; core's default zero is not a usage observation.
    tokens = [observations[s].get("tokens") if s in observations else None for s in called]
    wall = [observations[s].get("wall_seconds") if s in observations else None for s in called]
    return {"status": status, "selection": selection, "decision": decision, "eligible": fit,
            "selector_tokens": sum(tokens) if all(number(t) for t in tokens) else None,
            "selector_wall_seconds": sum(wall) if all(number(t) for t in wall) else None,
            "observation_statuses": {s: observations.get(s, {}).get("status", "unobserved") for s in called}}


def resolver_inputs(snapshot):
    inputs = copy.deepcopy(snapshot)
    if isinstance(inputs.get("now"), str):
        inputs["now"] = dt.datetime.fromisoformat(inputs["now"].replace("Z", "+00:00"))
        if inputs["now"].tzinfo is None:
            raise ValueError("resolver now must include a timezone")
    return inputs


def baselines(f, fit):
    if not fit:
        return dict.fromkeys(("audit-fallback", "highest-rated-eligible", "cheapest-eligible", "fixture-oracle"))
    audit = ms.audit_order(fit)
    # Highest-rated uses the canonical audit order (including its tie-breaks).
    # Cheapest is that same canonical order with rating/rank intentionally absent.
    cheapest = ms.audit_order([{k: v for k, v in c.items() if k not in ("rating", "rank")} for c in fit])
    outcomes = {c["id"]: outcome(f, c["id"]) for c in fit}
    comparable = all(o["verified_pass"] is not None and o["completion"] is not None for o in outcomes.values())
    oracle_order = []
    if comparable:
        # Same audit cost/usage policy; realized usage replaces historical means,
        # and all PASS peers have equal pass_rate. No scalar utility in core.
        realized = []
        for c in fit:
            o = outcomes[c["id"]]
            realized.append(dict(c, rating={"pass_rate": 1 if o["verified_pass"] else 0,
                                            "average_tokens": o["tokens"]}))
        ordered = ms.audit_order(realized)
        oracle_order = sorted(ordered, key=lambda c: (
            not outcomes[c["id"]]["verified_pass"],
            0 if outcomes[c["id"]]["verified_pass"] else -outcomes[c["id"]]["completion"]))
    return {"audit-fallback": audit[0]["id"], "highest-rated-eligible": audit[0]["id"],
            "cheapest-eligible": cheapest[0]["id"],
            "fixture-oracle": oracle_order[0]["id"] if oracle_order else None}


def bucket(confidence):
    if confidence is None:
        return "unknown"
    for low, high, name in ((.6, .7, "0.6–0.7"), (.7, .8, "0.7–0.8"), (.8, .9, "0.8–0.9"), (.9, 1.01, "0.9+")):
        if low <= confidence < high:
            return name
    return "below-0.6"


def evaluate(f, threshold=.6):
    validate(f)
    replayed = replay(f, threshold)
    s = replayed["selection"] or {}
    chosen = ms.candidate_id(s["selected"]) if s.get("selected") else None
    arms = baselines(f, replayed["eligible"])
    arms["selector-selected"] = chosen if replayed["status"] == "ok" else None
    rows = []
    oracle = outcome(f, arms.get("fixture-oracle"))
    for arm, cid in arms.items():
        o = outcome(f, cid)
        if not cid:
            o["status"] = replayed["status"] if arm == "selector-selected" or not replayed["eligible"] else "oracle-unavailable"
        regret = None
        if cid and arms.get("fixture-oracle") and o["verified_pass"] is not None:
            regret = {"missed_pass": int(oracle["verified_pass"] and not o["verified_pass"]),
                      "completion_gap": max(0, oracle["completion"] - o["completion"]),
                      "extra_tokens_when_both_pass": max(0, o["tokens"] - oracle["tokens"])
                      if o["verified_pass"] and oracle["verified_pass"] and number(o["tokens"]) and number(oracle["tokens"]) else None,
                      "oracle_match": cid == arms["fixture-oracle"]}
        is_selector = arm == "selector-selected"
        rows.append(dict(fixture=f["id"], horizon=f["horizon"], provenance=f["provenance"],
                         threshold=threshold, arm=arm, candidate=cid, **o, regret=regret,
                         stage=s.get("stage") if is_selector else None,
                         audit_fallback=is_selector and any(a.get("stage") == "audit" and a.get("outcome") == "ranked"
                                                            for a in s.get("attempts", [])),
                         selector_cost=None if is_selector else 0,
                         confidence_bucket=bucket(s.get("confidence")) if is_selector else None,
                         selector_tokens=replayed["selector_tokens"] if is_selector else 0,
                         selector_wall_seconds=replayed["selector_wall_seconds"] if is_selector else 0))
        rows[-1]["total_tokens"] = (o["tokens"] + rows[-1]["selector_tokens"]
                                     if number(o["tokens"]) and number(rows[-1]["selector_tokens"]) else None)
        rows[-1]["total_wall_seconds"] = (o["wall_seconds"] + rows[-1]["selector_wall_seconds"]
                                          if number(o["wall_seconds"]) and number(rows[-1]["selector_wall_seconds"]) else None)
    return {"fixture": f["id"], "threshold": threshold, "replay": replayed, "rows": rows}


def stats(rows):
    def metric(key):
        known = [r[key] for r in rows if number(r.get(key))]
        return {"known_n": len(known), "unknown_n": len(rows) - len(known),
                "mean": sum(known) / len(known) if known else None,
                "known_sum": sum(known) if known else None}
    verified = [r for r in rows if r["verified_pass"] is not None]
    out = {"n": len(rows), "verified_n": len(verified),
           "verified_pass_rate": sum(r["verified_pass"] for r in verified) / len(verified) if verified else None,
           "statuses": dict(collections.Counter(r["status"] for r in rows))}
    for key in ("completion", "tokens", "total_tokens", "wall_seconds", "total_wall_seconds", "selector_tokens", "selector_wall_seconds"):
        out[key] = metric(key)
    currencies = sorted({r["currency"] for r in rows if r.get("currency")})
    out["cost_by_currency"] = {c: stats_cost([r for r in rows if r.get("currency") == c]) for c in currencies}
    out["cost_unknown_n"] = sum(not number(r.get("cost")) for r in rows)
    regrets = [r["regret"] for r in rows if r.get("regret") is not None]
    out["regret"] = {"known_n": len(regrets), "unknown_n": len(rows) - len(regrets),
                     "missed_pass_rate": sum(r["missed_pass"] for r in regrets) / len(regrets) if regrets else None,
                     "mean_completion_gap": sum(r["completion_gap"] for r in regrets) / len(regrets) if regrets else None,
                     "oracle_match_rate": sum(r["oracle_match"] for r in regrets) / len(regrets) if regrets else None}
    extra = [r["extra_tokens_when_both_pass"] for r in regrets if number(r["extra_tokens_when_both_pass"])]
    out["regret"]["extra_tokens_when_both_pass"] = {"known_n": len(extra), "mean": sum(extra) / len(extra) if extra else None}
    selected = [r for r in rows if r["arm"] == "selector-selected"]
    out["stage_rates"] = {s: sum(r.get("stage") == s and r.get("candidate") is not None for r in selected) / len(selected)
                          if selected else None for s in ms.STAGES}
    out["jev_judge_acceptance_rate"] = sum(r.get("stage") in ("jev", "judge") and r.get("candidate") is not None for r in selected) / len(selected) if selected else None
    out["audit_fallback_rate"] = sum(r.get("audit_fallback", False) and r.get("candidate") is not None for r in selected) / len(selected) if selected else None
    out["selector_cost_unknown_n"] = sum(r.get("selector_cost") is None for r in selected)
    return out


def stats_cost(rows):
    known = [r["cost"] for r in rows if number(r.get("cost"))]
    return {"known_n": len(known), "unknown_n": len(rows) - len(known),
            "known_sum": sum(known) if known else None, "mean": sum(known) / len(known) if known else None}


def report(fixtures):
    if len({f["id"] for f in fixtures}) != len(fixtures):
        raise ValueError("fixture IDs must be unique (use distinct IDs for repeated trials)")
    if len({f["provenance"] for f in fixtures}) > 1:
        raise ValueError("do not aggregate synthetic and measured fixtures in the same report")
    runs = [evaluate(f, t) for t in THRESHOLDS for f in fixtures]
    rows = [r for run in runs for r in run["rows"]]
    default = [r for r in rows if r["threshold"] == .6]
    selectors = [r for r in default if r["arm"] == "selector-selected"]
    def grouped(items, key, values):
        return {v: stats([r for r in items if r.get(key) == v]) for v in values}
    return {"schema_version": 1, "kind": "selector-outcome-qualification", "eval_only": True,
            "provenance": fixtures[0]["provenance"] if fixtures else None,
            "runtime_sha256": {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                               for module in (ms, resolver, vc)},
            "engine_missing": engine.missing(), "fixtures": fixtures, "runs": runs,
            "by_arm": grouped(default, "arm", sorted({r["arm"] for r in default})),
            "by_stage": grouped(selectors, "stage", ms.STAGES),
            "by_confidence": grouped(selectors, "confidence_bucket", ("below-0.6", "0.6–0.7", "0.7–0.8", "0.8–0.9", "0.9+", "unknown")),
            "by_horizon": grouped(selectors, "horizon", ("short", "medium", "long")),
            "selector_events": dict(collections.Counter(
                stage + ":" + a["outcome"] for run in runs if run["threshold"] == .6
                for a in (run["replay"].get("selection") or {}).get("attempts", [])
                for stage in [a["stage"]])),
            "threshold_sweep": {str(t): stats([r for r in rows if r["threshold"] == t and r["arm"] == "selector-selected"])
                                for t in THRESHOLDS}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=HERE / "data/model-selection/fixtures.json")
    parser.add_argument("--output-root", type=Path, default=HERE / "results/model-selection")
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--real-run", action="store_true", help="Opt in to paid Agent CLIs and remote selector")
    parser.add_argument("--candidates", type=Path, help="JSON list with explicit agent_cli/model for real runs")
    parser.add_argument("--case", action="append", help="Fixture ID; repeat to select multiple")
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args(argv)
    if args.selfcheck:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_model_selection_eval.py")
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    fixtures = json.loads(args.fixtures.read_text())
    if args.case:
        unknown = set(args.case) - {f["id"] for f in fixtures}
        if unknown:
            parser.error(f"unknown case: {sorted(unknown)}")
        fixtures = [f for f in fixtures if f["id"] in args.case]
    if not fixtures:
        parser.error("no fixtures")
    for f in fixtures:
        validate(f)
    if args.real_run and (not args.candidates or not args.case or args.timeout <= 0):
        parser.error("--real-run requires --candidates, --case and positive --timeout")
    out = new_run_dir(args.output_root, "selector", "real" if args.real_run else "offline")
    if args.real_run:
        from model_selection_real import collect
        fixtures = collect(fixtures, json.loads(args.candidates.read_text()), out, args.timeout)
    result = report(fixtures)
    write_json(out / "report.json", result)
    print(out / "report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
