from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone

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

    def test_ambiguous_native_sessions_are_not_counted_again_as_estimate(self):
        st = self._seed(rates={"per_cli": {"claude:sonnet": 100.0}})
        led = next(r for r in st.iter_records() if r["kind"] == "ledger")
        for i, offset in enumerate((-90, -80), 1):
            st.append_record({"id": f"aud-s{i}", "ts": _iso_now(offset),
                              "started_at": _iso_now(offset - 60), "kind": "session",
                              "agent_cli": "claude", "model": "claude-sonnet-4",
                              "tokens_in": 1000, "tokens_out": 100, "measured": True})
        rows = usage.aggregate_usage(self.make_args(), st, "month", "workload")
        flow = next(r for r in rows if r["group"] == "flow")
        self.assertEqual(flow["estimated_tokens"], 0)
        self.assertEqual(flow["unmeasured_runs"], 1)
        self.assertEqual(sum(r["measured_in"] + r["measured_out"] for r in rows), 2200)

    def test_non_llm_agent_audit_row_is_never_estimated(self):
        st = self.make_store()
        ts = _iso_now(-60)
        st.append_record({"id": "audit-collect", "ts": ts, "kind": "ledger",
                          "workload": "audit", "tool": "agent-audit", "ref": "collect",
                          "agent_cli": "claude", "seconds": 60})
        os.makedirs(self.budget_dir, exist_ok=True)
        with open(os.path.join(self.budget_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"rates": {"per_cli": {"claude": 100.0}}}, f)
        row = usage.aggregate_usage(self.make_args(), st, "month", "tool")[0]
        self.assertEqual(row["estimated_tokens"], 0)
        self.assertEqual(row["unmeasured_runs"], 1)

    def test_agent_audit_llm_uses_stage_samples_not_generic_cli_rate(self):
        st = self.make_store()
        for i, offset in enumerate((-3600, -3000, -2400, -1800), 1):
            ended = _iso_now(offset)
            st.append_record({"id": f"audit-l{i}", "ts": ended, "kind": "ledger",
                              "workload": "audit", "tool": "agent-audit", "ref": "extract",
                              "usage_kind": "llm", "agent_cli": "claude", "seconds": 10})
            if i <= 3:
                st.append_record({"id": f"audit-s{i}", "ts": ended,
                                  "started_at": _iso_now(offset - 10), "kind": "session",
                                  "agent_cli": "claude", "model": "opus",
                                  "session_id": f"native-{i}", "tokens_in": 100,
                                  "tokens_out": 10, "measured": True})
        os.makedirs(self.budget_dir, exist_ok=True)
        with open(os.path.join(self.budget_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"rates": {"per_cli": {"claude": 1000.0}}}, f)
        row = usage.aggregate_usage(self.make_args(), st, "month", "tool")[0]
        self.assertEqual((row["measured_in"], row["measured_out"]), (300, 30))
        self.assertEqual(row["estimated_tokens"], 110)
        self.assertEqual(row["unmeasured_runs"], 1)

    def test_session_usage_revision_replaces_old_session_measurement(self):
        st = self.make_store()
        ts = _iso_now(-60)
        common = {"ts": ts, "started_at": _iso_now(-120), "agent_cli": "claude",
                  "model": "opus", "session_id": "native-1", "measured": True}
        st.append_record({"id": "old", "kind": "session", "tokens_in": 200,
                          "tokens_out": 20, **common})
        st.append_record({"id": "fixed", "kind": "session-usage", "parser_revision": 2,
                          "tokens_in": 100, "tokens_out": 10, **common})
        _ledger, sessions, _runs = usage.load_period_records(st, "month")
        self.assertEqual(len(sessions), 1)
        self.assertEqual((sessions[0]["tokens_in"], sessions[0]["tokens_out"]), (100, 10))

    def test_agent_limits_combine_declared_cap_and_quota_deadline(self):
        now = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
        ts = "2026-08-12T00:00:00Z"
        self.write_ledger("20260812", [{"ts": ts, "workload": "flow", "seconds": 0,
                                         "event": "quota", "agent_cli": "claude",
                                         "quota_kind": "rate_limit",
                                         "reset_at": "2026-08-12T01:00:00Z"}])
        os.makedirs(self.budget_dir, exist_ok=True)
        with open(os.path.join(self.budget_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"period": "month", "allocation": {"agents": {
                "claude": {"max_tokens": 5000}, "codex": {"max_tokens": 2000}}}}, f)
        rows = [{"group": "claude", "measured_in": 3000, "measured_out": 500,
                 "estimated_tokens": 0}]
        limits = {item["agent_cli"]: item for item in usage.aggregate_agent_limits(
            self.make_args(), self.make_store(), rows, rows_period="month", now=now)}
        self.assertEqual(limits["claude"]["max_tokens"], 5000)
        self.assertEqual(limits["claude"]["remaining_tokens"], 1500)
        self.assertEqual(limits["claude"]["reset_at"], "2026-08-12T01:00:00Z")
        self.assertTrue(limits["claude"]["blocked"])
        self.assertEqual(limits["codex"]["reset_at"], "2026-09-01T00:00:00Z")
        self.assertEqual(limits["codex"]["reset_source"], "period")
        self.assertFalse(limits["codex"]["blocked"])

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

    def test_calibrate_uses_linked_session_duration_with_session_tokens(self):
        st = self.make_store()
        ended = _iso_now(-60)
        st.append_record({"id": "aud-l1", "ts": ended, "kind": "ledger",
                          "agent_cli": "claude", "model": "sonnet", "seconds": 0.001})
        st.append_record({"id": "aud-s1", "ts": ended, "started_at": _iso_now(-960),
                          "kind": "session", "agent_cli": "claude",
                          "model": "claude-sonnet-4", "tokens_in": 9000,
                          "tokens_out": 0, "measured": True})
        rates = usage.calibration_rates(self.make_args(), st)
        self.assertEqual(rates["claude"], 10.0)
        self.assertEqual(rates["claude:sonnet"], 10.0)

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
            # 品質はノード自身の結末（status）で数える。run 全体の統一 verify
            # （run_verify）は同じ run の全ノードに同じ値が付くので、モデル別の
            # PASS 率の入力にすると planner とワーカーが同じ判定を貰ってしまう。
            {"id": "r1", "ts": ts, "kind": "result", "purpose": "work",
             "model": "m-a", "status": "done", "run_verify": "fail"},
            {"id": "r2", "ts": ts, "kind": "result", "purpose": "work",
             "model": "m-a", "status": "failed", "run_verify": "fail"},
            {"id": "r3", "ts": ts, "kind": "result", "purpose": "work",
             "model": "m-b", "status": "done", "run_verify": "fail"},
        ]
        for rec in records:
            st.append_record(rec)
        st.save_state()
        rows = stats.aggregate_ratings(self.make_args(), st, "total")
        self.assertEqual(rows, [
            {"rank": 1, "purpose": "work", "model": "m-b", "usage_runs": 1,
             "total_tokens": 50.0, "outcome_runs": 1, "outcome_ok": 1,
             "average_tokens": 50.0, "pass_rate": 1.0},
            {"rank": 2, "purpose": "work", "model": "m-a", "usage_runs": 2,
             "total_tokens": 400.0, "outcome_runs": 2, "outcome_ok": 1,
             "average_tokens": 200.0, "pass_rate": 0.5},
        ])

    def test_stats_reports_agreement_rate_per_decision(self):
        st = self.make_store()
        ts = _iso_now(-60)
        for i, agree in enumerate((True, False, True), 1):
            st.append_record({
                "id": f"run-{i}", "ts": ts, "kind": "run", "tool": "agent-flow",
                "status": "done", "decision_comparisons": [
                    {"decision": "planner.pattern", "agree": agree}]})
        data = stats.aggregate_stats(st, "total")
        self.assertEqual(data["decisions"], [{
            "decision": "planner.pattern", "samples": 3, "matches": 2,
            "agreement_rate": 0.6667}])

    def _seed_trial(self, st, arms):
        ts = _iso_now(-60)
        for variant, method, tokens, statuses in arms:
            for i, status in enumerate(statuses):
                evidence = {"trial": {"id": "trial-1", "variant": variant},
                            "methods": [method]}
                st.append_record({"id": f"l-{variant}-{method}-{i}", "ts": ts, "kind": "ledger",
                                  "purpose": "work", "model": "m", "tokens_in": tokens,
                                  "tokens_out": 0, **evidence})
                st.append_record({"id": f"r-{variant}-{method}-{i}", "ts": ts, "kind": "result",
                                  "purpose": "work", "model": "m", "status": status,
                                  **evidence})
        st.save_state()

    def test_method_axis_and_trial_comparison(self):
        st = self.make_store()
        self._seed_trial(st, (
            ("baseline", "plan-first", 100, ("done", "failed", "done", "failed")),
            ("candidate", "test-first", 80, ("done", "done", "done", "done"))))
        ratings = stats.aggregate_ratings(self.make_args(), st, "total", by_methods=True)
        self.assertEqual({tuple(r["methods"]) for r in ratings},
                         {("plan-first",), ("test-first",)})
        data = stats.aggregate_trials(self.make_args(), st, "total")
        self.assertEqual(data["comparisons"], [{
            "trial": "trial-1", "purpose": "work", "model": "m",
            "baseline": "baseline", "candidate": "candidate",
            "min_outcome_runs": 4,
            "pass_rate_delta": 0.5, "average_tokens_delta": -20.0,
            "verdict": "effective"}])

    def test_small_samples_do_not_produce_a_verdict(self):
        """n=1 でも PASS 差は ±1.0 になる。**標本の少なさが差の大きさに化ける**ので、
        下限を割る比較は判定語を出さない（S12 の昇格ゲートと同じ規律）。"""
        st = self.make_store()
        self._seed_trial(st, (("baseline", "plan-first", 100, ("failed",)),
                              ("candidate", "test-first", 80, ("done",))))
        row = stats.aggregate_trials(self.make_args(), st, "total")["comparisons"][0]
        self.assertEqual(row["verdict"], "insufficient")
        self.assertIsNone(row["pass_rate_delta"])
        self.assertIn("下限", row["reason"])

    def test_quality_gain_that_costs_tokens_is_not_called_effective(self):
        st = self.make_store()
        self._seed_trial(st, (("baseline", "plan-first", 100, ("done", "failed", "done", "failed")),
                              ("candidate", "test-first", 400, ("done", "done", "done", "done"))))
        row = stats.aggregate_trials(self.make_args(), st, "total")["comparisons"][0]
        self.assertEqual(row["verdict"], "mixed", "品質は上がったがトークンは増えた")

    def test_missing_baseline_variant_reports_instead_of_guessing(self):
        """基準が名前で決まらないと差分の符号が辞書順に左右される。数字を出さない。"""
        st = self.make_store()
        self._seed_trial(st, (("armA", "plan-first", 100, ("done", "done", "done")),
                              ("armB", "test-first", 80, ("done", "done", "done"))))
        row = stats.aggregate_trials(self.make_args(), st, "total")["comparisons"][0]
        self.assertEqual(row["verdict"], "no-baseline")

    def test_split_variant_rows_are_reported_not_silently_dropped(self):
        """同じ trial で手法セットが分岐すると 3 行以上になる。黙って消すと
        「データが無い」と「捨てた」を読み手が区別できない。"""
        st = self.make_store()
        self._seed_trial(st, (("baseline", "plan-first", 100, ("done",)),
                              ("candidate", "test-first", 80, ("done",)),
                              ("candidate", "spec-first", 80, ("done",))))
        row = stats.aggregate_trials(self.make_args(), st, "total")["comparisons"][0]
        self.assertEqual(row["verdict"], "ambiguous")
        self.assertIn("3 件", row["reason"])


if __name__ == "__main__":
    unittest.main()
