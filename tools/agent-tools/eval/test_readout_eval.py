"""readout_eval の決定的な部分（区間分けと集計）だけを見る。

ollama も LLM も呼ばない——呼ぶ部分（`run_one` / `main`）は ollama のある木でしか動かない。
**測定の道具が壊れていると、壊れたことがモデルの数字として台帳へ残る**ので、区間の境目と
集計の割り算はここで押さえる。
"""
from __future__ import annotations

import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
