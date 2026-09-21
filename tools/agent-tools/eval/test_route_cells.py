"""route の較正セル（route_cells / readout_eval の RT1〜RT4）の決定的な部分。

ollama も LLM も呼ばない。押さえるのは 3 つ: 標本の形（id の一意・正解の整合）、oracle が
各セルで 1 つに決まること（fake run が通る）、hold の掃引の数え方。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import readout_eval  # noqa: E402
import route_cells  # noqa: E402


class CorpusTests(unittest.TestCase):
    def test_corpus_has_40_cases_and_4_families(self):
        self.assertEqual(len(route_cells.CORPUS["cases"]), 40)
        for family in route_cells.FAMILIES:
            self.assertEqual(len(route_cells.cell_ids(family)), 40)
            self.assertIn(family, readout_eval.FAMILIES)
        self.assertIn("RT1-n25", readout_eval.ALL_CELLS)
        self.assertNotIn("RT1-n25", readout_eval.CELLS, "既定の calibration 集合には載せない")

    def test_gold_is_consistent(self):
        gold = route_cells.CASES["RT1-n25"]["gold"]
        self.assertEqual(gold, {"handling": "task", "task": "daily-report", "skills": [], "routine": True})
        self.assertEqual(route_cells.CASES["RT2-n01"]["expected"], "other", "流用しない依頼の task は other")
        self.assertEqual(route_cells.CASES["RT3-n13"]["expected"], ["api-designer"])
        self.assertEqual(route_cells.CASES["RT4-n26"]["expected"], "yes")

    def test_fake_run_passes_every_family(self):
        for cid in ("RT1-n01", "RT1-n25", "RT2-n25", "RT2-n13", "RT3-n13", "RT3-n15", "RT4-n25", "RT4-n40"):
            row = readout_eval.calibration_run_one(cid, 1, "fake", fake=True)
            self.assertEqual(row["status"], "ok", cid)
            self.assertTrue(row["ok"], (cid, row.get("note")))
        handling = readout_eval.calibration_run_one("RT1-n25", 1, "fake", fake=True)
        self.assertEqual(list(handling["answers"]), ["handling"], "RT1 は handling だけを引く")
        skills = readout_eval.calibration_run_one("RT3-n13", 1, "fake", fake=True)
        self.assertEqual(len(skills["answers"]), 6, "RT3 はスキル 6 件の boolean")


class HoldSweepTests(unittest.TestCase):
    @staticmethod
    def _row(cid, name, choice, confidence, method="logprobs"):
        return {"status": "ok", "case": cid, "run": 1,
                "answers": {name: {"type": "choice", "choice": choice, "confidence": confidence,
                                   "method": method, "probabilities": {}}}}

    def test_counts_held_correct_wrong_and_missed(self):
        rows = [
            # n25: 正解 task/daily-report。0.82 / 0.77 → 0.75 で止まり正しい、0.8 では task 側が足りず止まらない
            self._row("RT1-n25", "handling", "task", 0.82), self._row("RT2-n25", "task", "daily-report", 0.77),
            # n01: 正解 answer だが task と言った → 止めれば誤り
            self._row("RT1-n01", "handling", "task", 0.9), self._row("RT2-n01", "task", "lint-fix", 0.9),
            # n26: 正解 task だが converse と言った → 止まらない（missed）
            self._row("RT1-n26", "handling", "converse", 0.9), self._row("RT2-n26", "task", "daily-report", 0.9),
            # n27: text 読み（確度なし）は数えない
            self._row("RT1-n27", "handling", "task", 0.0, method="text"), self._row("RT2-n27", "task", "lint-fix", 0.0, method="text"),
        ]
        result = route_cells.hold_sweep(rows, thresholds=(0.75, 0.8))
        self.assertEqual(result["pairs"], 4)
        at75, at80 = result["thresholds"]
        self.assertEqual((at75["held"], at75["correct"], at75["wrong_hold"], at75["missed"]), (2, 1, 1, 2))
        self.assertEqual(at75["precision"], 0.5)
        self.assertEqual((at80["held"], at80["correct"], at80["wrong_hold"], at80["missed"]), (1, 0, 1, 3))


if __name__ == "__main__":
    unittest.main()
