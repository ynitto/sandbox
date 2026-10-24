"""固定サンプルの再生と、取りこぼし・ノイズ・検査の手間の回帰検出。"""
from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

EVAL = Path(__file__).resolve().parents[1] / "eval"
spec = importlib.util.spec_from_file_location("codd_impact_eval", EVAL / "impact_eval.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class ImpactEvaluationTest(unittest.TestCase):
    def test_fixed_cases_pass_the_committed_baseline(self) -> None:
        # 本物の init/explore/guide/verify-plan/judge を独立した一時リポジトリで通す。
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            proc = subprocess.run([sys.executable, str(EVAL / "impact_eval.py"), "--repeat", "1", "--output", str(output)],
                                  capture_output=True, text=True, timeout=180)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["regressions"], [])
        self.assertEqual(len(result["cases"]), 3)
        self.assertTrue(all(row["recall"] == 1 for row in result["cases"]))

    def test_a_real_search_miss_is_not_hidden_by_a_passing_plan(self) -> None:
        # 呼び出し元から測る語だけを消す。計画検査が通っても、独立した正解に対する漏れは残る。
        case = json.loads(evaluation.FIXTURE.read_text(encoding="utf-8"))[0]
        files = evaluation.fixture_files()
        files["app"]["src/print/usePrint.ts"] = files["app"]["src/print/usePrint.ts"].replace("sourceId", "documentId")
        cwd = Path.cwd()
        try:
            with mock.patch.dict(os.environ), mock.patch.object(evaluation, "fixture_files", return_value=files):
                row = evaluation.run_case(case)
        finally:
            os.chdir(cwd)
        self.assertEqual(row["missed_files"], ["app:src/print/usePrint.ts"])
        self.assertEqual(row["recall"], 0.5)
        baseline = json.loads(evaluation.BASELINE.read_text(encoding="utf-8"))
        report = copy.deepcopy(baseline)
        report["cases"][0] = row
        self.assertTrue(any("recall" in failure for failure in evaluation.compare(report, baseline)))


class BaselineComparisonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = json.loads(evaluation.BASELINE.read_text(encoding="utf-8"))
        self.report = copy.deepcopy(self.baseline)

    def test_regressions_are_checked_per_case(self) -> None:
        for metric in ("recall", "noise_count", "pending_rows", "pending_files", "plan_checks"):
            with self.subTest(metric=metric):
                report = copy.deepcopy(self.report)
                report["cases"][0][metric] += -0.5 if metric == "recall" else 1
                failures = evaluation.compare(report, self.baseline)
                self.assertEqual(len(failures), 1)
                self.assertIn(metric, failures[0])

    def test_improvement_in_another_case_does_not_mask_a_regression(self) -> None:
        self.report["cases"][0]["noise_count"] += 1
        self.report["cases"][1]["noise_count"] -= 1
        self.assertEqual(len(evaluation.compare(self.report, self.baseline)), 1)

    def test_time_is_informational_until_a_ratio_is_requested(self) -> None:
        self.report["cases"][0]["elapsed_seconds"] *= 10
        self.assertEqual(evaluation.compare(self.report, self.baseline), [])
        self.assertIn("elapsed_seconds", evaluation.compare(self.report, self.baseline, 1.5)[0])

    def test_different_fixtures_and_missing_cases_cannot_pass(self) -> None:
        self.report["suite_digest"] = "different"
        with self.assertRaises(ValueError):
            evaluation.compare(self.report, self.baseline)
        self.report = copy.deepcopy(self.baseline)
        self.report["cases"].pop()
        with self.assertRaises(ValueError):
            evaluation.compare(self.report, self.baseline)

    def test_invalid_baseline_is_not_silently_accepted(self) -> None:
        self.baseline["cases"][0]["recall"] = float("nan")
        with self.assertRaises(ValueError):
            evaluation.compare(self.report, self.baseline)

    def test_cli_returns_a_failure_and_keeps_the_baseline_on_regression(self) -> None:
        rows = copy.deepcopy(self.report["cases"])
        rows[0]["noise_count"] += 1
        workers = [subprocess.CompletedProcess([], 0, json.dumps(row), "") for row in rows]
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            output = Path(tmp) / "report.json"
            original = json.dumps(self.baseline)
            baseline.write_text(original, encoding="utf-8")
            with mock.patch.object(evaluation.subprocess, "run", side_effect=workers), \
                 mock.patch.object(evaluation, "git", return_value="git test"), \
                 mock.patch.object(evaluation.platform, "platform", return_value="test"), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = evaluation.main(["--repeat", "1", "--baseline", str(baseline), "--output", str(output)])
            self.assertEqual(rc, 1)
            self.assertEqual(baseline.read_text(encoding="utf-8"), original)
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["regressions"])

    def test_cli_cannot_save_a_missed_file_as_a_new_baseline(self) -> None:
        rows = copy.deepcopy(self.report["cases"])
        rows[0]["missed_files"] = ["app:src/print/usePrint.ts"]
        rows[0]["recall"] = 0.5
        workers = [subprocess.CompletedProcess([], 0, json.dumps(row), "") for row in rows]
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text("existing baseline", encoding="utf-8")
            with mock.patch.object(evaluation.subprocess, "run", side_effect=workers), \
                 mock.patch.object(evaluation, "git", return_value="git test"), \
                 mock.patch.object(evaluation.platform, "platform", return_value="test"), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = evaluation.main(["--repeat", "1", "--baseline", str(baseline), "--write-baseline"])
            self.assertEqual(rc, 2)
            self.assertEqual(baseline.read_text(encoding="utf-8"), "existing baseline")

    def test_report_output_cannot_overwrite_the_baseline(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            evaluation.main(["--output", str(evaluation.BASELINE)])
        self.assertEqual(stopped.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
