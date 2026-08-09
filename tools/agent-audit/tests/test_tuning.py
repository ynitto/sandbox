from __future__ import annotations

import json
import os
import unittest

from _shared import AuditTestCase, tuning, util


class TuningLoopTests(AuditTestCase):
    def _seed(self):
        st = self.make_store()
        ts = util.now_iso()
        st.append_record({"id": "l1", "ts": ts, "kind": "ledger", "purpose": "work",
                          "agent_cli": "kiro", "model": "m1", "tokens_in": 100})
        for i in range(3):
            st.append_record({"id": f"r{i}", "ts": ts, "kind": "result", "purpose": "work",
                              "model": "m1", "status": "done"})
        st.write_insight({
            "id": "ins-abc", "statement": "圧縮が効く", "kind": "usage-optimization",
            "scope": {"purpose": "work", "model": "m1"},
            "observation_ids": ["o1", "o2", "o3"], "occurrences": 3,
            "confidence": "high", "review": {"verdict": "supported"},
            "expires_when": {"evaluation_runs": 1, "max_quality_drop": 0.05,
                             "min_pass_rate": 0.8, "max_age_days": 30},
            "declaration": {"target": "agent-tuning", "op": "set",
                            "path": "profiles.default.injections", "value": ["compact"]},
        })
        st.save_state()
        return st

    def test_reproduced_candidate_promotes_then_quality_regression_retires(self):
        st = self._seed()
        target = os.path.join(self.tmp, "tuning.json")
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "revision": 1, "profiles": {
                "default": {"injections": [], "env": []},
                "external-facing": {"injections": [], "env": []},
            }}, f)
        args = self.make_args(tuning_file=target, period="total", tune_min_outcomes=1,
                              tune_evaluation_runs=1)
        decisions = tuning.run_tuning(args, st, apply=True)
        self.assertEqual(decisions[0]["status"], "promoted")
        self.assertEqual(util.read_json(target)["profiles"]["default"]["injections"], ["compact"])
        self.assertEqual(decisions[0]["evidence"], ["ins-abc", "o1", "o2", "o3"])

        st.append_record({"id": "r3", "ts": util.now_iso(), "kind": "result",
                          "purpose": "work", "model": "m1", "status": "failed"})
        decisions = tuning.run_tuning(args, st, apply=True)
        self.assertEqual(decisions[0]["status"], "retired")
        self.assertEqual(decisions[0]["retire_reason"], "quality-floor")
        self.assertEqual(util.read_json(target)["profiles"]["default"]["injections"], [])

    def test_external_facing_cannot_receive_injections(self):
        st = self._seed()
        insight = next(st.iter_insights())
        insight["id"] = "ins-unsafe"
        insight["declaration"]["path"] = "profiles.external-facing.injections"
        st.write_insight(insight)
        target = os.path.join(self.tmp, "tuning.json")
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"profiles": {"external-facing": {"injections": []}}}, f)
        args = self.make_args(tuning_file=target, period="total", tune_min_outcomes=1,
                              tune_max_promotions_per_run=2)
        decisions = tuning.run_tuning(args, st, apply=True)
        unsafe = next(d for d in decisions if d["id"] == "tune-unsafe")
        self.assertEqual(unsafe["status"], "proposed")
        self.assertEqual(unsafe["blocked_reason"], "path-or-value-not-allowed")


if __name__ == "__main__":
    unittest.main()
