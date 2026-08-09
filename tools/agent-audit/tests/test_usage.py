from __future__ import annotations

import json
import os
import unittest

from _shared import AuditTestCase, collect, ledger_row, stats, usage, util


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
        st = self._seed(with_session=True, rates={"per_cli": {"claude": 10.0}})
        args = self.make_args()
        rates = usage.calibration_rates(args, st)
        self.assertAlmostEqual(rates["claude"], 20.0)       # (1000+200)/60
        args.write = True
        self.assertEqual(usage.cmd_calibrate(args), 0)
        cfg = util.read_json(os.path.join(self.budget_dir, "config.json"))
        self.assertEqual(cfg["updated_by"], "agent-audit")
        self.assertAlmostEqual(cfg["rates"]["per_cli"]["claude"], 20.0)
        drift = [r for r in st.iter_records() if r.get("kind") == "calibration"]
        self.assertEqual(len(drift), 2)  # claude と claude:sonnet
        cli = next(r for r in drift if r.get("model") == "")
        self.assertEqual(cli["estimated_tokens_per_second"], 10.0)
        self.assertEqual(cli["measured_tokens_per_second"], 20.0)
        self.assertEqual(cli["delta_ratio"], 1.0)

    def test_ratings_golden_by_purpose_and_model(self):
        st = self.make_store()
        ts = _iso_now(-60)
        records = [
            {"id": "l1", "ts": ts, "kind": "ledger", "purpose": "work",
             "agent_cli": "claude", "model": "m-a", "seconds": 1,
             "tokens_in": 100, "tokens_out": 0},
            {"id": "l2", "ts": ts, "kind": "ledger", "purpose": "work",
             "agent_cli": "claude", "model": "m-a", "seconds": 1,
             "tokens_in": 300, "tokens_out": 0},
            {"id": "l3", "ts": ts, "kind": "ledger", "purpose": "work",
             "agent_cli": "ollama", "model": "m-b", "seconds": 1,
             "tokens_in": 50, "tokens_out": 0},
            {"id": "r1", "ts": ts, "kind": "result", "purpose": "work",
             "model": "m-a", "verify": "pass"},
            {"id": "r2", "ts": ts, "kind": "result", "purpose": "work",
             "model": "m-a", "verify": "fail"},
            {"id": "r3", "ts": ts, "kind": "result", "purpose": "work",
             "model": "m-b", "verify": "pass"},
        ]
        for rec in records:
            st.append_record(rec)
        st.save_state()
        rows = stats.aggregate_ratings(self.make_args(), st, "total")
        self.assertEqual(rows, [
            {"rank": 1, "purpose": "work", "model": "m-b", "usage_runs": 1,
             "total_tokens": 50.0, "verify_runs": 1, "verify_pass": 1,
             "average_tokens": 50.0, "pass_rate": 1.0},
            {"rank": 2, "purpose": "work", "model": "m-a", "usage_runs": 2,
             "total_tokens": 400.0, "verify_runs": 2, "verify_pass": 1,
             "average_tokens": 200.0, "pass_rate": 0.5},
        ])


if __name__ == "__main__":
    unittest.main()
