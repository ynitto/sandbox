"""rates 較正（agent-audit calibrate）のゴールデン。

agent-app は自前の較正器を持たず、監査の連鎖（src/main/audit.js の STEPS）で
`agent-audit calibrate --write` を呼ぶ。ここは今日の往復——ollama と aider の実測行から
秒レートを出し、実測が入る CLI（session_log.usage: true）の鍵を外す——を固定する。
**挙動は変えない**。期待値は schemas/node-budget-rates.golden.json の calibration に置き、
agentcore（rate / row_tokens）と agent-app（台帳の書き手）のテストと 1 つを共有する。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _shared import AuditTestCase, usage, util

GOLDEN = Path(__file__).resolve().parents[3] / "schemas" / "node-budget-rates.golden.json"


class CalibrateGoldenTests(AuditTestCase):
    def setUp(self):
        super().setUp()
        self.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["calibration"]
        st = self.make_store()
        ts = util.now_iso()
        for i, row in enumerate(self.golden["rows"]):
            st.append_record(dict(row, id=f"golden-{i}", _epoch=util.parse_iso(ts), ts=ts,
                                  kind="ledger", workload="flow", tool="agent-flow"))
        st.save_state()
        self.store = st
        with open(os.path.join(self.budget_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 2, "rates": {"per_cli": dict(self.golden["prior_per_cli"])}}, f)

    def _measured(self):
        # measured_clis は同梱の agents/*.json から引く。差し替え可能にして CLI 定義の変化から切り離す。
        return mock.patch.object(usage, "measured_clis",
                                 return_value=set(self.golden["measured_clis"]))

    def test_proposed_rates_carry_both_key_granularities(self):
        """除外の前の提案は cli:model と cli の両方の鍵を持つ。"""
        self.assertEqual(usage.calibration_rates(self.make_args(), self.store),
                         self.golden["expected"]["proposed"])

    def test_write_drops_measured_cli_keys_and_leaves_default_unset(self):
        """実測が入る ollama の鍵は、書かれていても消える。default は書かない。"""
        args = self.make_args(write=True)
        with self._measured(), redirect_stdout(io.StringIO()):
            self.assertEqual(usage.cmd_calibrate(args), 0)
        cfg = util.read_json(os.path.join(self.budget_dir, "config.json"))
        self.assertEqual(cfg["rates"]["per_cli"], self.golden["expected"]["per_cli"])
        self.assertEqual(cfg["rates"].get("default_tokens_per_second"),
                         self.golden["expected"]["default_tokens_per_second"])


if __name__ == "__main__":
    unittest.main()
