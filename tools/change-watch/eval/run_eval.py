"""Run the deterministic change-watch agent evaluations."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
AGENTS = HERE.parent / "agents"
MODULES = {
    "ci-triage": AGENTS / "gitlab-ci-triage-agent" / "gitlab_ci.py",
    "iac": AGENTS / "gitlab-mr-review-agent" / "gitlab_iac.py",
    "honesty": HERE / "honesty.py",
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
    return {"rules": sorted({
        finding["rule"] for finding in result["findings"]
        if finding["rule"] is not None
    })}


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


def evaluate_honesty(agent, inputs):
    return agent.extract(inputs)


EVALUATORS = {
    "ci-triage": evaluate_ci_triage,
    "iac": evaluate_iac,
    "honesty": evaluate_honesty,
}


def compare_values(expected, actual):
    return "pass" if expected == actual else "fail"


def compare_baseline(previous, current):
    if not isinstance(previous, dict) or previous.get("status") not in ("pass", "fail"):
        return "比較対象なし"
    if previous["status"] == current["status"]:
        if "actual" in previous and previous["actual"] != current["actual"]:
            return "不変（値の変化あり）"
        return "不変"
    return "改善" if current["status"] == "pass" else "後退"


def run_target(target):
    agent = load_module(MODULES[target])
    evaluate = EVALUATORS[target]
    results = {}
    for case in sorted((HERE / "cases" / target).iterdir()):
        if not case.is_dir():
            continue
        expected = None
        try:
            expected = read_json(case / "expected.json")
            inputs = read_json(case / "input.json")
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
    strict_passed = 0
    for target, cases in results.items():
        passed = sum(result["status"] == "pass" for result in cases.values())
        lines.append(f"{target}: {passed} pass, {len(cases) - passed} fail")
        if cases and passed == len(cases):
            strict_passed += 1
            lines.append("  strict: pass")
        else:
            lines.append("  strict: fail")
        previous_cases = baseline.get(target) if isinstance(baseline, dict) else None
        if not isinstance(previous_cases, dict):
            previous_cases = {}
        for name, result in cases.items():
            status = result["status"]
            line = f"  {name}: {status}"
            if baseline is not None:
                line += f" ({compare_baseline(previous_cases.get(name), result)})"
            lines.append(line)
            if status == "fail":
                lines.append(f"    expected: {json.dumps(result['expected'], ensure_ascii=False)}")
                lines.append(f"    actual:   {json.dumps(result['actual'], ensure_ascii=False)}")
                if "error" in result:
                    lines.append(f"    error:    {result['error']}")
        for name in sorted(previous_cases.keys() - cases.keys()):
            lines.append(f"  {name}: 未実行（ベースラインにのみ存在）")
    strict_status = "pass" if results and strict_passed == len(results) else "fail"
    lines.append(f"strict: {strict_passed}/{len(results)} targets ({strict_status})")
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

    baseline = None
    if args.baseline is not None:
        try:
            baseline = read_json(args.baseline)
        except (OSError, ValueError) as error:
            print(f"ベースラインを読み込めません: {error}", file=sys.stderr)
        if not isinstance(baseline, dict):
            baseline = {}
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
