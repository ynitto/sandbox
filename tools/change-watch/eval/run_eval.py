"""Run the deterministic change-watch agent evaluations."""

import argparse
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
AGENTS = HERE.parent / "agents"
MODULES = {
    "ci-triage": AGENTS / "gitlab-ci-triage-agent" / "gitlab_ci.py",
    "iac": AGENTS / "gitlab-mr-review-agent" / "gitlab_iac.py",
}


def load_module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def extract_ci_triage(result):
    return {
        "category": result["category"],
        "patterns": sorted({
            group["pattern"] for group in result["groups"]
            if group["pattern"] is not None
        }),
    }


def extract_iac(result):
    return {"rules": sorted({finding["rule"] for finding in result["findings"]})}


def evaluate_ci_triage(agent, inputs):
    traces = {int(job_id): trace for job_id, trace in inputs["traces"].items()}
    result = agent.aggregate(
        inputs["pipeline"], inputs["jobs"], traces,
        agent.BUILTIN_PATTERNS["patterns"],
    )
    return extract_ci_triage(result)


def evaluate_iac(agent, inputs):
    files = inputs["files"]
    result = agent.inspect(inputs["diffs"], lambda path: files.get(path, ""))
    return extract_iac(result)


EVALUATORS = {"ci-triage": evaluate_ci_triage, "iac": evaluate_iac}


def compare_values(expected, actual):
    return "pass" if expected == actual else "fail"


def compare_baseline(previous_status, current_status):
    if previous_status is None:
        return "比較対象なし"
    if previous_status == current_status:
        return "不変"
    return "改善" if current_status == "pass" else "後退"


def run_target(target):
    agent = load_module(MODULES[target])
    evaluate = EVALUATORS[target]
    results = {}
    for case in sorted((HERE / "cases" / target).iterdir()):
        if not case.is_dir() or not (case / "input.json").is_file():
            continue
        inputs = read_json(case / "input.json")
        expected = read_json(case / "expected.json")
        try:
            actual = evaluate(agent, inputs)
        except Exception as error:
            results[case.name] = {
                "status": "fail",
                "expected": expected,
                "actual": None,
                "error": str(error),
            }
            continue
        results[case.name] = {
            "status": compare_values(expected, actual),
            "expected": expected,
            "actual": actual,
        }
    return results


def format_report(results, baseline=None):
    lines = []
    for target, cases in results.items():
        passed = sum(result["status"] == "pass" for result in cases.values())
        lines.append(f"{target}: {passed} pass, {len(cases) - passed} fail")
        previous_cases = {} if baseline is None else baseline.get(target, {})
        for name, result in cases.items():
            status = result["status"]
            line = f"  {name}: {status}"
            if baseline is not None:
                previous_status = previous_cases.get(name, {}).get("status")
                line += f" ({compare_baseline(previous_status, status)})"
            lines.append(line)
            if status == "fail":
                lines.append(f"    expected: {json.dumps(result['expected'], ensure_ascii=False)}")
                lines.append(f"    actual:   {json.dumps(result['actual'], ensure_ascii=False)}")
                if "error" in result:
                    lines.append(f"    error:    {result['error']}")
    return "\n".join(lines)


def baseline_snapshot(results):
    return {
        target: {
            name: {"status": result["status"], "actual": result["actual"]}
            for name, result in cases.items()
        }
        for target, cases in results.items()
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=MODULES)
    parser.add_argument("--save-baseline", type=Path, metavar="FILE")
    parser.add_argument("--baseline", type=Path, metavar="FILE")
    args = parser.parse_args(argv)

    baseline = read_json(args.baseline) if args.baseline is not None else None
    targets = (args.target,) if args.target else MODULES
    results = {target: run_target(target) for target in targets}
    print(format_report(results, baseline))
    if args.save_baseline is not None:
        snapshot = json.dumps(baseline_snapshot(results), ensure_ascii=False, indent=2)
        args.save_baseline.write_text(snapshot + "\n", encoding="utf-8")
    return int(any(
        result["status"] == "fail"
        for cases in results.values()
        for result in cases.values()
    ))


if __name__ == "__main__":
    raise SystemExit(main())
