from __future__ import annotations

import json
import os
import unittest

from _shared import AuditTestCase, collect, ledger_row, usage, util


def _iso_now(offset_sec: float = 0.0) -> str:
    import time
    return util.epoch_to_iso(time.time() + offset_sec)


class UsageTests(AuditTestCase):
    def _seed(self, *, with_session=False, rates=None):
        """当月内の時刻で台帳（+セッション）レコードを直接ストアへ入れる。"""
        st = self.make_store()
        ts = _iso_now(-60)
        st.append_record({"id": "aud-l1", "_epoch": util.parse_iso(ts), "ts": ts,
                          "kind": "ledger", "workload": "flow", "tool": "agent-flow",
                          "agent_cli": "claude", "model": "sonnet", "seconds": 60.0,
                          "measured": False})
        if with_session:
            st.append_record({"id": "aud-s1", "_epoch": util.parse_iso(ts),
                              "ts": ts, "started_at": _iso_now(-120),
                              "kind": "session", "agent_cli": "claude",
                              "model": "claude-sonnet-4", "tokens_in": 1000,
                              "tokens_out": 200, "measured": True})
        if rates is not None:
            os.makedirs(self.budget_dir, exist_ok=True)
            with open(os.path.join(self.budget_dir, "config.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"rates": rates}, f)
        st.save_state()
        return st

    def test_estimated_from_rates_never_mixed_with_measured(self):
        st = self._seed(rates={"per_cli": {"claude:sonnet": 100.0}})
        rows = usage.aggregate_usage(self.make_args(), st, "month", "workload")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["estimated_tokens"], 6000)      # 60s × 100 tokens/s
        self.assertEqual(r["measured_in"], 0)              # 実測は混ぜない

    def test_linked_session_backfills_measured(self):
        st = self._seed(with_session=True)
        rows = usage.aggregate_usage(self.make_args(), st, "month", "workload")
        r = {row["group"]: row for row in rows}["flow"]
        self.assertEqual((r["measured_in"], r["measured_out"]), (1000, 200))
        self.assertEqual(r["estimated_tokens"], 0)
        # linked セッションは独立行として二重計上されない
        self.assertNotIn("(session)", {row["group"] for row in rows})

    def test_no_rate_counts_unmeasured(self):
        st = self._seed()
        rows = usage.aggregate_usage(self.make_args(), st, "month", "workload")
        self.assertEqual(rows[0]["unmeasured_runs"], 1)

    def test_calibrate_median_and_write(self):
        st = self._seed(with_session=True)
        args = self.make_args()
        rates = usage.calibration_rates(args, st)
        self.assertAlmostEqual(rates["claude"], 20.0)       # (1000+200)/60
        args.write = True
        self.assertEqual(usage.cmd_calibrate(args), 0)
        cfg = util.read_json(os.path.join(self.budget_dir, "config.json"))
        self.assertEqual(cfg["updated_by"], "agent-audit")
        self.assertAlmostEqual(cfg["rates"]["per_cli"]["claude"], 20.0)


if __name__ == "__main__":
    unittest.main()
