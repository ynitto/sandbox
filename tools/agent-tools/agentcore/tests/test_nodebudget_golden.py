"""agentcore.nodebudget.rate / row_tokens のゴールデン。

期待値は schemas/node-budget-rates.golden.json（共有フィクスチャ）に置き、ここは読むだけ。
同じフィクスチャを agent-app（台帳の書き手）と agent-audit（calibrate）のテストも読む。
置き場を schemas/ にしたのは、どのツールの持ち物でもない契約のそばに置く既存の慣習
（methods-source-hash.golden.json）に合わせたから。2 か所に写すと片方だけ直る。
**計算規則は変えない**——今の値を固定するだけ。

    PYTHONPATH=tools/agent-tools/agentcore \\
      python -m unittest discover -s tools/agent-tools/agentcore/tests -p 'test_nodebudget_golden.py'
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentcore import nodebudget as nb  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[4] / "schemas" / "node-budget-rates.golden.json"


class NodeBudgetRateGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_rate_and_row_tokens_match_the_shared_fixture(self):
        for case in self.golden["cases"]:
            row = case["row"]
            for cfg_name, want in case["expected"].items():
                cfg = self.golden["configs"][cfg_name]
                with self.subTest(case=case["name"], config=cfg_name):
                    self.assertEqual(nb.rate(cfg, row.get("agent_cli", ""), row.get("model", "")),
                                     want["rate"])
                    self.assertEqual(nb.row_tokens(row, cfg), want["row_tokens"])

    def test_rows_as_agent_app_writes_them(self):
        """agent-app が書き換えて書く行は、書いた形で読むと値が変わる（記録だけ）。"""
        for case in self.golden["cases"]:
            written = case.get("agent_app_writes")
            if not written:
                continue
            row = dict(case["row"], tokens_in=written["tokens_in"], tokens_out=written["tokens_out"])
            for cfg_name, want in written["expected"].items():
                with self.subTest(case=case["name"], config=cfg_name):
                    self.assertEqual(nb.row_tokens(row, self.golden["configs"][cfg_name]),
                                     want["row_tokens"])


if __name__ == "__main__":
    unittest.main()
