"""Three-arm reports. Missing cost is unknown, never zero."""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path

ARMS = ("single", "cascade", "critique")


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def summarize(rows):
    result = {}
    for arm in ARMS:
        group = [r for r in rows if r["arm"] == arm]
        if not group:
            continue
        passes = sum(r["passed"] for r in group)
        costs = [r.get("cost_usd") for r in group]
        complete = all(c is not None for c in costs)
        findings = [f for r in group for f in r.get("findings", [])]
        # Adoption requires human-confirmed evidence, not the solver's own claim.
        confirmed = [f for f in findings if f.get("adoption_confirmed") is not None]
        result[arm] = {
            "started": len(group), "passes": passes, "pass_rate": passes / len(group),
            "cost_usd": sum(costs) if complete else None,
            "cost_per_pass": (sum(costs) / passes if passes else "infinity") if complete else None,
            "unmeasured_runs": sum(c is None for c in costs),
            "wall_median": statistics.median(r["wall"] for r in group),
            "wall_p95": percentile([r["wall"] for r in group], .95),
            "escalation_rate": sum(r.get("escalated", False) for r in group) / len(group)
            if arm == "cascade" else None,
            "critic_adoption_rate": sum(f["adoption_confirmed"] for f in confirmed) / len(findings)
            if findings and len(confirmed) == len(findings) else None,
            "findings": len(findings), "unconfirmed_findings": len(findings) - len(confirmed),
            "gate_false_accepts": sum(r.get("gate_passed") is True and not r["passed"] for r in group),
            "rescued": sum(r.get("escalated", False) and r["passed"] for r in group),
            "critique_regressions": sum(r.get("draft_passed") is True and not r["passed"] for r in group),
            "critique_repairs": sum(r.get("draft_passed") is False and r["passed"] for r in group),
            "errors": {s: sum(r["status"] == s for r in group)
                       for s in sorted({r["status"] for r in group})},
        }
    # Resample tasks, retaining repeats and paired arms together.
    pairs = {}
    for arm in ("cascade", "critique"):
        by_key = {(r["task"], r["repeat"], r["arm"]): r for r in rows}
        deltas = []
        for task in sorted({r["task"] for r in rows}):
            repeats = sorted({r["repeat"] for r in rows if r["task"] == task})
            if not all((task, i, a) in by_key for i in repeats for a in ("single", arm)):
                continue
            deltas.append(statistics.mean(int(by_key[task, i, arm]["passed"])
                                         - int(by_key[task, i, "single"]["passed"])
                                         for i in repeats))
        rng = random.Random(0)
        boot = [statistics.mean(rng.choices(deltas, k=len(deltas))) for _ in range(2000)] if len(deltas) >= 2 else []
        pairs[arm] = {"paired_tasks": len(deltas),
                      "pass_rate_difference": statistics.mean(deltas) if deltas else None,
                      "task_bootstrap_95": [percentile(boot, .025), percentile(boot, .975)] if boot else None,
                      "decision": "insufficient" if len(deltas) < 12 else "requires_holdout"}
    return {"arms": result, "paired_vs_single": pairs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--adoptions", type=Path, help="Human-reviewed finding decisions; does not modify the ledger")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.ledger.read_text().splitlines() if line.strip()]
    keys = [(r["task"], r["repeat"], r["arm"]) for r in rows]
    if len(keys) != len(set(keys)):
        parser.error("duplicate task/repeat/arm: do not merge or replay runs silently")
    if args.adoptions:
        try:
            apply_adoptions(rows, json.loads(args.adoptions.read_text()))
        except (ValueError, KeyError, TypeError) as exc:
            parser.error(str(exc))
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2, allow_nan=False))


def apply_adoptions(rows, decisions):
    """A separate human annotation file keeps original experiment evidence immutable."""
    findings = {(r["run_id"], f["id"]): (r, f) for r in rows for f in r.get("findings", [])}
    seen = set()
    for decision in decisions:
        key = (decision["run_id"], decision["finding_id"])
        if key not in findings or key in seen:
            raise ValueError(f"unknown or duplicate adoption decision: {key}")
        row, finding = findings[key]
        if type(decision.get("adopted")) is not bool or not decision.get("evidence", "").strip():
            raise ValueError("adoption needs a boolean adopted and human-reviewed evidence")
        if decision["adopted"] and finding["path"] not in row.get("revision_changed_paths", []):
            raise ValueError("adopted finding must reference a changed file")
        finding["adoption_confirmed"] = decision["adopted"]
        finding["adoption_evidence"] = decision["evidence"]
        seen.add(key)


if __name__ == "__main__":
    main()
