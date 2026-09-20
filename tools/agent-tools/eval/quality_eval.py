#!/usr/bin/env python3
"""Paired legacy/evidence quality eval using production JS and existing E-cell oracles."""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))
from agentcore import judge, hostenv
JS = REPO / "tools/agent-app/src/main/quality-evaluation.js"
CELLS = {"E1": "development", "E2": "development", "E3": "development",
         "E4": "holdout", "E5": "holdout", "E6": "holdout"}


def js(action, value):
    proc = subprocess.run(["node", str(JS), action], input=json.dumps(value, ensure_ascii=False),
                          text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


def fixture(cid):
    module = importlib.import_module("judge_eval")
    case = module.CASES[cid]
    # These literal requirement names come from REQUEST, not the expected result.
    stage_clause = module.REQUEST.split("。", 1)[1].split("の", 1)[0].strip()
    criteria = stage_clause.split("・")
    assert len(criteria) == 3
    data = {"prompt": module.REQUEST, "criteria": criteria, "inventoryComplete": True,
            "answer": "\n".join(f"{n} ({kind}) [{status}]: {out}" for n, kind, status, out in case["results"])}
    expected = case["check"]({"decision": "done"})[0]
    assert expected != case["check"]({"decision": "replan"})[0]
    return data, expected


def run_one(cid, run, model, fake=False):
    data, expected = fixture(cid)
    rows = []
    for arm in ("legacy", "evidence"):
        started = time.monotonic()
        calls = []
        def ask(state, questions):
            result = judge.evaluate(state, questions, model=model)
            calls.append(result)
            return result
        row = {"schema_version": 1, "case": cid, "run": run, "split": CELLS[cid],
               "arm": arm, "source": "fake" if fake else "real", "model": model,
               "expected_clear": expected,
               "input_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()}
        try:
            if fake:
                outcome = "supported" if expected else "problem"
                row.update(outcome=outcome, proposal=None)
            elif arm == "legacy":
                request = js("baseline", data)
                result = ask(request["state"], request["questions"])
                result["abstained"] = judge.abstained(result["answers"], .55)
                parsed = js("baseline-finalize", result)
                outcome = "unknown" if parsed is None else "supported" if parsed["quality"] == 3 and parsed["issue"] == "none" else "problem"
                row.update(outcome=outcome, proposal=parsed)
            else:
                plan = js("prepare", data)
                result = ask(plan["state"], plan["questions"]) if plan["questions"] else {}
                proposal = js("finalize", {"plan": plan, "response": result})
                cause = js("cause", proposal)
                if cause:
                    try:
                        proposal = js("finalize-cause", {"proposal": proposal, "response": ask(cause["state"], cause["questions"])})
                    except judge.JudgeError as exc:
                        proposal["cause_error"] = str(exc)
                row.update(outcome=proposal["status"], proposal=proposal)
            row["status"] = "ok"
        except judge.JudgeError as exc:
            row.update(status="request_failure", outcome=None, error=str(exc))
        row.update(wall=time.monotonic() - started, calls=calls)
        rows.append(row)
    return rows


def summarize(rows):
    groups = []
    for split in ("development", "holdout"):
        for arm in ("legacy", "evidence"):
            all_rows = [r for r in rows if r["split"] == split and r["arm"] == arm]
            good = [r for r in all_rows if r["status"] == "ok"]
            bad_cases = [r for r in good if r["expected_clear"] is False]
            resolved = [r for r in good if r["outcome"] != "unknown"]
            def rate(n, d): return n / d if d else None
            groups.append({"split": split, "arm": arm, "cases": len(all_rows),
                "unique_cases": len({r["case"] for r in good}),
                "failures": len(all_rows) - len(good),
                "false_clear_count": sum(r["outcome"] == "supported" for r in bad_cases),
                "problem_cases": len(bad_cases),
                "false_clear_rate": rate(sum(r["outcome"] == "supported" for r in bad_cases), len(bad_cases)),
                "problem_detection_rate": rate(sum(r["outcome"] == "problem" for r in bad_cases), len(bad_cases)),
                "resolved_rate": rate(len(resolved), len(good)),
                "unknown_count": sum(r["outcome"] == "unknown" for r in good),
                "accuracy_resolved": rate(sum((r["outcome"] == "supported") == r["expected_clear"] for r in resolved), len(resolved)),
                "false_alarm_count": sum(r["outcome"] == "problem" and r["expected_clear"] for r in good)})
    return {"schema_version": 1, "status": "insufficient_data", "groups": groups,
            "limitations": ["Constructed fixtures, not human-labeled production workload.",
                            "Repeated inputs are not independent samples.",
                            "Cause correctness is not labeled or calibrated.",
                            "Supported means reported evidence, not verified task completion."]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default="gemma4:e4b")
    p.add_argument("--cases", default=",".join(CELLS))
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--fake-run", action="store_true")
    p.add_argument("--output-dir", required=True)
    a = p.parse_args()
    ids = a.cases.split(",")
    if a.repeat < 1 or any(cid not in CELLS for cid in ids): p.error("invalid cases/repeat")
    hostenv.load_profile_env()
    dest = Path(a.output_dir); dest.mkdir(parents=True, exist_ok=False)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "arguments": vars(a),
                "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                "builder_sha256": hashlib.sha256(JS.read_bytes()).hexdigest(), "state": "running"}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    import shlex
    (dest / "command.txt").write_text(shlex.join([sys.executable, *sys.argv])+"\n")
    rows = []
    with (dest / "ledger.jsonl").open("w") as ledger:
        for cid in ids:
            for i in range(1, a.repeat+1):
                for row in run_one(cid, i, a.model, a.fake_run):
                    rows.append(row); ledger.write(json.dumps(row, ensure_ascii=False)+"\n"); ledger.flush()
                    print(cid, i, row["arm"], row["outcome"], flush=True)
    (dest / "report.json").write_text(json.dumps(summarize(rows), indent=2)+"\n")
    manifest["state"] = "completed_with_failures" if any(r["status"] != "ok" for r in rows) else "completed"
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    return int(manifest["state"] != "completed")

if __name__ == "__main__": raise SystemExit(main())
