"""readout_eval の決定的な部分（区間分けと集計）だけを見る。

ollama も LLM も呼ばない——呼ぶ部分（`run_one` / `main`）は ollama のある木でしか動かない。
**測定の道具が壊れていると、壊れたことがモデルの数字として台帳へ残る**ので、区間の境目と
集計の割り算はここで押さえる。
"""
from __future__ import annotations

import os
import sys
import unittest
import json
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import importlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import readout_eval  # noqa: E402


def _row(confidence, ok, coverage=0.95):
    return {"case": "F1", "run": 1, "ok": ok, "confidence": confidence, "coverage": coverage}


class BinTests(unittest.TestCase):
    def test_bins_are_half_open_and_1_0_lands_in_the_last(self):
        self.assertEqual(readout_eval.bin_of(0.0), (0.0, 0.2))
        self.assertEqual(readout_eval.bin_of(0.19), (0.0, 0.2))
        self.assertEqual(readout_eval.bin_of(0.2), (0.2, 0.4), "境目は上の区間に入る")
        self.assertEqual(readout_eval.bin_of(0.8), (0.8, 1.0))
        self.assertEqual(readout_eval.bin_of(1.0), (0.8, 1.0), "上端だけは最後の区間へ")


class SummarizeTests(unittest.TestCase):
    def test_reliability_counts_each_bin(self):
        summary = readout_eval.summarize([
            _row(0.95, True), _row(0.85, False), _row(0.9, True),
            _row(0.5, False), _row(0.1, True)])
        by_bin = {r["bin"]: r for r in summary["reliability"]}
        self.assertEqual((by_bin["0.8-1.0"]["n"], by_bin["0.8-1.0"]["ok"]), (3, 2))
        self.assertAlmostEqual(by_bin["0.8-1.0"]["accuracy"], 0.6667, places=4)
        self.assertEqual(by_bin["0.4-0.6"]["accuracy"], 0.0)
        self.assertIsNone(by_bin["0.2-0.4"]["accuracy"], "1 件も無い区間は率を作らない")
        self.assertEqual(summary["ok"], 3)

    def test_coverage_share_uses_the_floor(self):
        summary = readout_eval.summarize([
            _row(0.9, True, coverage=0.99), _row(0.9, True, coverage=0.5),
            _row(0.9, True, coverage=0.79), _row(0.9, True, coverage=0.8)])
        self.assertEqual(summary["coverage"]["below_floor"], 2, "0.8 ちょうどは割っていない")
        self.assertEqual(summary["coverage"]["share_below_floor"], 0.5)
        self.assertEqual(summary["coverage"]["median"], 0.8)

    def test_unreadable_runs_leave_the_diagram(self):
        """確度の無い回（judge が落ちた回）を信頼度図に混ぜない。件数は errors に残す。"""
        summary = readout_eval.summarize([
            _row(0.9, True), {"case": "J2", "ok": False, "confidence": None, "coverage": None}])
        self.assertEqual((summary["n"], summary["errors"]), (2, 1))
        self.assertEqual(sum(r["n"] for r in summary["reliability"]), 1)
        self.assertEqual(summary["coverage"]["n"], 1)

    def test_no_rows_is_not_a_division_by_zero(self):
        summary = readout_eval.summarize([])
        self.assertIsNone(summary["coverage"]["share_below_floor"])
        self.assertIsNone(summary["coverage"]["median"])
        self.assertIn("信頼度図", readout_eval.format_report(summary))


def calibration_row(cid="A", confidence=.8, correct=True, method="logprobs", coverage=.95):
    answer = {"type": "boolean", "value": correct, "method": method,
              "probabilities": {"yes": confidence if correct else 1-confidence,
                                "no": 1-confidence if correct else confidence},
              "confidence": 0 if method == "text" else confidence, "coverage": coverage}
    return {"schema_version": 1, "model": "fake", "source": "fake", "case": cid,
            "run": 1, "status": "ok", "answers": {"q": answer}, "expected": {"q": "yes"},
            "question_ok": {"q": correct}, "ok": correct, "wall": .1,
            "usage": {"tokens_in": None, "tokens_out": None}}


class CalibrationTests(unittest.TestCase):
    def report(self, rows, **kwargs):
        return readout_eval.calibration_report(rows, model="fake", **kwargs)

    def test_brier_binary_and_multiclass(self):
        self.assertAlmostEqual(readout_eval.brier(calibration_row()["answers"]["q"], "yes"), .08)
        self.assertAlmostEqual(readout_eval.brier({"probabilities": {"a": .6, "b": .3, "c": .1}}, "b"), .86)

    def test_sweep_abstention_boundary_and_ece(self):
        group = self.report([calibration_row(), calibration_row("B", .6, False)], min_confidence=.8)["methods"][0]
        self.assertEqual((group["answered"], group["abstained"], group["answer_rate"]), (1, 1, .5))
        self.assertEqual(group["accuracy_answered"], 1)
        self.assertAlmostEqual(group["brier"], .4)
        self.assertAlmostEqual(group["ece"], .4)
        sweep = {r["min_confidence"]: r for r in group["thresholds"]}
        self.assertEqual(sweep[.6]["accepted_cases"], 2)
        self.assertEqual(sweep[.8]["accepted_cases"], 1)
        self.assertIsNone(sweep[.9]["accuracy"])
        self.assertEqual(group["abstained_case_ids"], ["B:1:q"])

    def test_coverage_percentiles_and_bucket_endpoints(self):
        rows = [calibration_row(str(i), c, coverage=c) for i, c in enumerate([.5, .79, .8, 1.0])]
        group = self.report(rows)["methods"][0]
        self.assertAlmostEqual(group["coverage"]["p10"], .587)
        self.assertAlmostEqual(group["coverage"]["p50"], .795)
        self.assertAlmostEqual(group["coverage"]["p90"], .94)
        self.assertEqual(group["coverage"]["below_0_8"], 2)
        self.assertEqual(group["coverage"]["low_case_ids"], ["0:1:q", "1:1:q"])
        self.assertEqual(group["buckets"][-1]["n"], 2)

    def test_methods_and_mixed_cell_are_separate(self):
        mixed = calibration_row()
        mixed["answers"]["v"] = calibration_row(method="vote")["answers"]["q"]
        mixed["expected"]["v"] = "yes"
        mixed["question_ok"]["v"] = True
        report = self.report([mixed, calibration_row("T", 1, method="text", coverage=0)])
        self.assertEqual([g["cases"] for g in report["methods"]], [1, 1, 1])
        text = report["methods"][2]
        self.assertIsNone(text["brier"])
        self.assertIsNone(text["ece"])
        self.assertEqual(text["abstained"], 1)
        self.assertTrue(all(r["accepted_cases"] == 0 for r in text["thresholds"]))
        self.assertIn(["logprobs", "vote"], [g["methods"] for g in report["cell_gates"]])

    def test_empty_and_repeats_are_insufficient(self):
        self.assertEqual(self.report([])["status"], "insufficient_data")
        report = self.report([calibration_row()] * 120)
        self.assertEqual(report["status"], "insufficient_data")
        self.assertEqual(report["methods"][0]["unique_cases"], 1)
        self.assertEqual(report["methods"][0]["thresholds"][0]["status"], "insufficient_data")

    def test_failure_is_not_wrong_and_missing_usage_is_explicit(self):
        failed = calibration_row("fail")
        failed.update(status="transport_failure", error="offline")
        report = self.report([calibration_row(), failed])
        self.assertEqual(report["transport_failures"], 1)
        self.assertEqual(report["methods"][0]["accuracy_answered"], 1)
        self.assertEqual(report["usage"]["tokens_in"]["missing_cells"], 2)

    def test_fixture_oracles_and_fake_real_schema(self):
        for cid in readout_eval.CELLS:
            fake = readout_eval.calibration_run_one(cid, 1, "fake", fake=True)
            self.assertTrue(fake["ok"], cid)
            with patch.object(readout_eval.judge, "evaluate", return_value={"answers": fake["answers"]}):
                real = readout_eval.calibration_run_one(cid, 1, "fake")
            self.assertEqual(set(fake), set(real))
            self.assertEqual(set(self.report([fake])), set(self.report([real])))
            self.assertEqual(fake["expected"], real["expected"])

    def test_transport_and_response_failure(self):
        error = readout_eval.judge.JudgeError("unreachable")
        error.__cause__ = urllib.error.URLError("offline")
        with patch.object(readout_eval.judge, "post_chat", side_effect=error):
            row = readout_eval.calibration_run_one("J2", 1, "fake")
        self.assertEqual(row["status"], "transport_failure")
        with patch.object(readout_eval.judge, "post_chat", return_value={"message": {"content": ""}}):
            row = readout_eval.calibration_run_one("J2", 1, "fake")
        self.assertEqual(row["status"], "response_failure")
        self.assertIsNone(row["usage"]["tokens_in"])

    def test_usage_observes_actual_response_fields(self):
        response = {"message": {"content": "A"}, "prompt_eval_count": 17, "eval_count": 1}
        with patch.object(readout_eval.judge, "post_chat", return_value=response):
            row = readout_eval.calibration_run_one("J2", 1, "fake")
        self.assertEqual(row["usage"], {"tokens_in": 17, "tokens_out": 1})
        self.assertEqual(row["answers"]["winner"]["method"], "text")

    def test_reject_legacy_mixed_models_and_invalid_numbers(self):
        with self.assertRaises(ValueError):
            self.report([_row(.8, True)])
        row = calibration_row()
        row["answers"]["q"]["confidence"] = float("nan")
        with self.assertRaises(ValueError):
            self.report([row])
        with self.assertRaises(ValueError):
            self.report([], min_confidence=float("nan"))

    def test_oracle_refuses_ambiguous_labels(self):
        q = {"q": {"type": "boolean", "instructions": "test"}}
        with self.assertRaisesRegex(ValueError, "exactly one"):
            readout_eval.oracle(q, lambda a: a, lambda a: (True, ""))

    def test_fake_cli_and_replay_same_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "first"
            args = ["readout_eval.py", "--fake-run", "--cases", "F1,J2", "--repeat", "1",
                    "--model", "fake", "--output-dir", str(out)]
            with patch.object(sys, "argv", args):
                self.assertEqual(readout_eval.main(), 0)
            replay = Path(tmp) / "replay"
            with patch.object(sys, "argv", ["readout_eval.py", "--replay", str(out / "ledger.jsonl"),
                                          "--model", "fake", "--output-dir", str(replay)]):
                self.assertEqual(readout_eval.main(), 0)
            self.assertEqual(json.loads((out / "report.json").read_text()),
                             json.loads((replay / "report.json").read_text()))



class ProductionCalibrationTests(unittest.TestCase):
    POLICY = {"model": "gemma4:e4b", "method": "logprobs", "min_coverage": .8,
              "thresholds": {"filter": .6, "route": .8}}

    def test_filter_applies_policy_to_production_consumer(self):
        importlib.import_module("judge_eval")  # loads production agent_flow through engine
        flow = importlib.import_module("agent_flow")
        result = {"answers": {"a": {"value": True, "confidence": .65, "coverage": .9,
                                      "method": "logprobs"},
                              "b": {"value": False, "confidence": .9, "coverage": .9,
                                      "method": "logprobs"}}, "usage": {}}
        with patch.object(flow, "_effective_agent", return_value=("ollama", "gemma4:e4b")), \
             patch.object(flow._judge, "local_model", return_value="gemma4:e4b"), \
             patch.object(flow._judge.herdconfig, "calibration_setting", return_value=self.POLICY), \
             patch.object(flow._judge, "evaluate", return_value=result), \
             patch.object(flow, "_node_budget_record"):
            deps = {"a": {"output": "tests pass"}, "b": {"output": "tests fail"}}
            self.assertEqual(flow.filter_judge("keep passing", deps, None)[1]["kept"], ["a"])
            result["answers"]["a"]["confidence"] = .59
            self.assertIsNone(flow.filter_judge("keep passing", deps, None))

    def test_route_applies_policy_and_assess_is_held(self):
        ap = importlib.import_module("project_eval").ap
        result = {"answers": {"workspace": {"choice": "repo-a", "confidence": .82,
                                               "coverage": .9, "method": "logprobs"}}}
        cfg = SimpleNamespace(model="gemma4:e4b")
        with patch.object(ap, "_agent_for", return_value=("ollama", None)), \
             patch.object(ap._judge, "local_model", return_value="gemma4:e4b"), \
             patch.object(ap._judge.herdconfig, "calibration_setting", return_value=self.POLICY), \
             patch.object(ap._judge, "evaluate", return_value=result), \
             patch.object(ap, "_route_judge_questions", return_value={"workspace": {"criteria": {"repo-a": "a", "repo-b": "b"}}}), \
             patch.object(ap, "_route_judge_state", return_value="state"), \
             patch.object(ap, "_assess_material", return_value="state"):
            self.assertEqual(ap.route_judge(cfg, {}, [{}, {}]), "repo-a")
            result["answers"]["workspace"]["method"] = "vote"
            self.assertIsNone(ap.route_judge(cfg, {}, [{}, {}]))
            self.assertIsNone(ap.assess_judge(cfg, {}))

if __name__ == "__main__":
    unittest.main()
