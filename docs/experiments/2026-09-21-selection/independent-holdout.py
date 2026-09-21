"""Frozen holdout evaluation; does not tune thresholds or execute selected agents."""
import json, time
from pathlib import Path
from experiment_output import output_path
import independent as experiment
ms, tune = experiment.ms, experiment.tune
MIN_ADEQUACY, EQUIVALENCE_MARGIN = .6, .01
HERE = Path(__file__).parent
cases = json.loads((HERE / "independent-holdout-input.json").read_text())
base = [ms.describe_candidate(c, project_dir=tune.ROOT, quotas={}) for c in ms.normalize_candidates(["cursor", "codex", "claude", "ollama/gemma4:e4b"])]
output = output_path("independent-holdout.jsonl")
if output.exists():
    raise SystemExit("Refusing to overwrite existing evaluation")
for order in ("normal", "reverse"):
    candidates = base if order == "normal" else list(reversed(base))
    for case in cases:
        started = time.monotonic()
        row = {**case, "order": order, "expected": "local" if case["group"] == "simple" else "cloud", "candidates": candidates, "minimum_adequacy": MIN_ADEQUACY, "equivalence_margin": EQUIVALENCE_MARGIN}
        try:
            requirements, fits = experiment.evaluate(case["prompt"], candidates)
            row.update(requirements=requirements, fits=fits)
            best = max(answer["probability"] for answer in fits.values())
            eligible = [c for c in candidates if fits[c["id"]]["probability"] >= MIN_ADEQUACY and fits[c["id"]]["probability"] >= best - EQUIVALENCE_MARGIN]
            ordered = ms.audit_order(sorted(eligible, key=lambda c: c["id"]))
            picked = ordered[0] if ordered else None
            row.update(eligible=[c["id"] for c in ordered], selected=picked["id"] if picked else None, actual=picked["site"] if picked else None, status="selected" if picked else "no_adequate_candidate")
        except Exception as exc:
            row.update(status="error", error=f"{type(exc).__name__}: {exc}", selected=None, actual=None)
        row["seconds"] = round(time.monotonic() - started, 2)
        row["expected_match"] = row["actual"] == row["expected"]
        with output.open("a") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps({k:v for k,v in row.items() if k != "candidates"}, ensure_ascii=False), flush=True)
