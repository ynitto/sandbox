import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import run_eval


class EvalTest(unittest.TestCase):
    def test_ci_triage_extraction_and_comparison(self):
        actual = run_eval.extract_ci_triage({
            "category": "dependency",
            "groups": [
                {"pattern": "pytest-failed"},
                {"pattern": "pip-no-matching-distribution"},
                {"pattern": "pytest-failed"},
                {"pattern": None},
            ],
        })
        expected = {
            "category": "dependency",
            "patterns": ["pip-no-matching-distribution", "pytest-failed"],
        }
        self.assertEqual(run_eval.compare_values(expected, actual), "pass")
        self.assertEqual(
            run_eval.compare_values(dict(expected, category="unknown"), actual), "fail"
        )
        self.assertEqual(
            run_eval.compare_values(dict(expected, patterns=[]), actual), "fail"
        )

    def test_iac_extraction_and_comparison(self):
        actual = run_eval.extract_iac({
            "findings": [
                {"rule": "open-ingress"},
                {"rule": "iam-wildcard"},
                {"rule": "open-ingress"},
            ],
        })
        expected = {"rules": ["iam-wildcard", "open-ingress"]}
        self.assertEqual(run_eval.compare_values(expected, actual), "pass")
        self.assertEqual(run_eval.compare_values({"rules": []}, actual), "fail")

    def test_baseline_comparison(self):
        for previous, current, label in (
            ("fail", "pass", "改善"),
            ("pass", "fail", "後退"),
            ("pass", "pass", "不変"),
            ("fail", "fail", "不変"),
            (None, "pass", "比較対象なし"),
        ):
            with self.subTest(previous=previous, current=current):
                self.assertEqual(run_eval.compare_baseline(previous, current), label)

    def test_failed_case_report(self):
        results = {"iac": {"private-cidr": {
            "status": "fail",
            "expected": {"rules": []},
            "actual": {"rules": ["open-ingress"]},
        }}}
        baseline = {"iac": {"private-cidr": {"status": "pass", "actual": {"rules": []}}}}
        report = run_eval.format_report(results, baseline)
        self.assertIn("iac: 0 pass, 1 fail", report)
        self.assertIn("private-cidr: fail (後退)", report)
        self.assertIn('expected: {"rules": []}', report)
        self.assertIn('actual:   {"rules": ["open-ingress"]}', report)

    def test_run_target_continues_after_evaluator_exception(self):
        expected = {"rules": []}
        evaluate = Mock(side_effect=[RuntimeError("evaluation failed"), expected])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("01-error", "02-success"):
                case = root / "cases" / "iac" / name
                case.mkdir(parents=True)
                (case / "input.json").write_text("{}", encoding="utf-8")
                (case / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
            with patch.object(run_eval, "HERE", root), patch.dict(run_eval.EVALUATORS, iac=evaluate):
                results = run_eval.run_target("iac")

        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(results["01-error"], {
            "status": "fail",
            "expected": expected,
            "actual": None,
            "error": "evaluation failed",
        })
        self.assertEqual(results["02-success"]["status"], "pass")
        self.assertIn("error:    evaluation failed", run_eval.format_report({"iac": results}))

    def run_cli(self, *args):
        command = [sys.executable, "-B", str(run_eval.HERE / "run_eval.py"), *args]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_all_cases_and_baseline_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            report = self.run_cli("--save-baseline", str(baseline))
            snapshot = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertEqual(set(snapshot), {"ci-triage", "iac"})
            for target, cases in snapshot.items():
                self.assertEqual(len(cases), 5)
                self.assertIn(f"{target}: 5 pass, 0 fail", report)
                for result in cases.values():
                    self.assertEqual(set(result), {"status", "actual"})
                    self.assertEqual(result["status"], "pass")

            report = self.run_cli("--baseline", str(baseline))
            self.assertEqual(report.count("(不変)"), 10)
            report = self.run_cli("--target", "iac", "--baseline", str(baseline))
            self.assertEqual(report.count("(不変)"), 5)
            self.assertNotIn("ci-triage", report)
