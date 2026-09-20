"""評価の行（agent-app の自動評価・まとめて評価）— 台帳 → 観測 → 洞察 → 集計。
評価は agent-app が書き、ここは読んで束ねるだけ。数字は足して割る以外に作らない。"""
from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from _shared import AuditTestCase, cli_main, collect, distill, extract, ledger_row, tasksout, usage
from agent_audit import rules


def evaluation_row(**over) -> dict:
    row = ledger_row(workload="evaluation", tool="agent-app", purpose="chat", ref="sess-1",
                     seconds=0, status="done",
                     used={"skills": ["statemachine-use"], "commands": ["npm test"], "tools": ["browser"]},
                     evaluation={"quality": 1, "confidence": 0.8, "issue": "skill-gap",
                                 "method": "logprobs", "judge_model": "gemma4:e4b"})
    row.update(over)
    return row


class TargetTests(unittest.TestCase):
    def test_artifact_wins_then_skill_then_tool(self):
        self.assertEqual(rules.target_of({"artifact": {"kind": "task", "name": "monthly"},
                                          "used": {"skills": ["s"]}}), {"kind": "task", "name": "monthly"})
        self.assertEqual(rules.target_of({"used": {"skills": ["s"], "tools": ["t"]}}), {"kind": "skill", "name": "s"})
        self.assertEqual(rules.target_of({"used": {"tools": ["winauto"]}}), {"kind": "tool", "name": "winauto"})
        self.assertIsNone(rules.target_of({"used": {"commands": ["ls"]}}))

    def test_evaluation_row_yields_one_observation_with_target(self):
        rec = {"tool": "agent-app", "workload": "evaluation", "agent_cli": "claude", "model": "sonnet",
               "used": {"skills": ["statemachine-use"]},
               "evaluation": {"quality": 1, "issue": "skill-gap"}}
        obs = rules.observe(rec)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["kind"], "skill-gap")
        self.assertEqual(obs[0]["target"], {"kind": "skill", "name": "statemachine-use"})
        self.assertIn("スキル statemachine-use", obs[0]["text"])
        self.assertTrue(obs[0]["group"].endswith("|skill-gap|skill:statemachine-use"))
        # 同じ対象・同じ種類なら別の会話でも同じ group（洞察へ畳める）
        again = rules.observe({**rec, "agent_cli": "claude", "model": "sonnet", "ref": "sess-2"})
        self.assertEqual(again[0]["group"], obs[0]["group"])

    def test_no_issue_or_unknown_issue_is_not_an_observation(self):
        self.assertEqual(rules.observe({"workload": "evaluation", "evaluation": {"issue": "none"}}), [])
        self.assertEqual(rules.observe({"workload": "evaluation", "evaluation": {"issue": "weird"}}), [])
        self.assertEqual(rules.evaluation_issue({"status": "failed"}), "")

    def test_tool_failure_maps_to_config_fix_insight(self):
        self.assertEqual(rules._INSIGHT_KIND["tool-failure"], "config-fix")
        self.assertIn("tool-failure", extract.OBSERVATION_KINDS)


class PipelineTests(AuditTestCase):
    def _collect(self, rows):
        self.write_ledger("20260803", rows)
        st = self.make_store()
        collect.collect_budget_ledger(self.make_args(), st)
        return st

    def test_ledger_row_keeps_used_and_evaluation(self):
        st = self._collect([evaluation_row()])
        rec = next(st.iter_records())
        self.assertEqual(rec["used"]["skills"], ["statemachine-use"])
        self.assertEqual(rec["evaluation"]["issue"], "skill-gap")
        self.assertEqual(rec["evaluation"]["judge_model"], "gemma4:e4b")

    def test_extract_takes_evaluations_regardless_of_filters_and_distills_by_target(self):
        rows = [evaluation_row(ref=f"sess-{i}", ts=f"2026-08-03T10:0{i}:00Z") for i in range(3)]
        rows.append(evaluation_row(ref="ok", evaluation={"quality": 3, "issue": "none"}))
        st = self._collect(rows)
        args = self.make_args(extract_filters=[], force=True)
        with mock.patch.object(extract, "run_llm", side_effect=AssertionError("LLM")):
            self.assertEqual(extract.cmd_extract(args), 0)
        st = self.make_store()
        obs = list(st.iter_observations())
        self.assertEqual(len(obs), 3)
        self.assertEqual(obs[0]["scope"]["target"], {"kind": "skill", "name": "statemachine-use"})
        with mock.patch.object(distill, "run_llm", side_effect=AssertionError("LLM")):
            self.assertEqual(distill.cmd_distill(self.make_args(force=True)), 0)
        st = self.make_store()
        insights = list(st.iter_insights())
        self.assertEqual(len(insights), 1)
        self.assertEqual(insights[0]["kind"], "skill-improvement")
        self.assertEqual(insights[0]["occurrences"], 3)
        self.assertEqual(insights[0]["scope"]["target"], {"kind": "skill", "name": "statemachine-use"})

    def test_usage_counts_evaluations_apart_from_runs(self):
        st = self._collect([
            ledger_row(tool="agent-app", workload="chat", agent_cli="claude", model="sonnet", seconds=10),
            evaluation_row(agent_cli="claude", evaluation={"quality": 3, "issue": "none"}),
            evaluation_row(agent_cli="claude", evaluation={"quality": 1, "issue": "tool-failure"}, used={"tools": ["browser"]}),
            evaluation_row(agent_cli="codex", evaluation={"quality": 2, "issue": "prompt-issue"}, used={}),
        ])
        rows = usage.aggregate_usage(self.make_args(), st, "total", "agent_cli")
        claude = next(r for r in rows if r["group"] == "claude")
        self.assertEqual(claude["runs"], 1)                  # 評価の行は消費に数えない
        self.assertEqual(claude["evaluations"], 2)
        self.assertEqual(claude["quality_avg"], 2.0)
        self.assertEqual(claude["issues"], 1)
        by_workload = usage.aggregate_usage(self.make_args(), st, "total", "workload")
        self.assertEqual(next(r for r in by_workload if r["group"] == "chat")["evaluations"], 3)
        ledger, _s, _r = usage.load_period_records(st, "total")
        summary = usage.evaluation_summary(ledger)
        self.assertEqual(summary["evaluations"], 3)
        self.assertEqual(summary["issues"], 2)
        self.assertEqual(summary["quality_avg"], 2.0)
        self.assertEqual(summary["by_target"], {"skill": 0, "task": 0, "workflow": 0, "tool": 1, "general": 1})
        self.assertEqual(summary["by_issue"], {"tool-failure": 1, "prompt-issue": 1})

    def test_tasks_id_marks_only_that_insight(self):
        st = self.make_store()
        for i in (1, 2):
            st.write_insight({"id": f"ins-{i}", "statement": f"s{i}", "suggested_action": "do",
                              "occurrences": 3, "confidence": "low", "observation_ids": []})
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(cli_main(["--audit-dir", self.audit_dir, "--budget-dir", self.budget_dir,
                                       "tasks", "--mark-exported", "--id", "ins-2"]), 0)
        text = out.getvalue()
        payload = json.loads(text[text.index("["):text.index("\n]") + 2])   # 前後の log 行は捨てる
        self.assertEqual([t["id"] for t in payload], ["audit-2"])
        st = self.make_store()
        exported = {i["id"]: bool(i.get("exported")) for i in st.iter_insights()}
        self.assertEqual(exported, {"ins-1": False, "ins-2": True})

    def test_scrub_command_redacts_stdin(self):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("token=abcdef1234567890abcdef\nhello")), \
             mock.patch("sys.stdout", out):
            self.assertEqual(cli_main(["--audit-dir", self.audit_dir, "scrub"]), 0)
        self.assertIn("[REDACTED]", out.getvalue())
        self.assertIn("hello", out.getvalue())


if __name__ == "__main__":
    unittest.main()


class ProposalTests(AuditTestCase):
    def test_advisory_proposal_is_review_not_skill_blame(self):
        proposal = {"schema_version": 1, "stage": "advisory", "status": "problem", "cause": "unknown",
                    "checks": [{"text": "render", "status": "unmet", "evidence_id": "e2"}]}
        row = evaluation_row(evaluation={"quality": None, "issue": "none", "proposal": proposal})
        obs = rules.observe(row)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["kind"], "quality-review")
        self.assertIn("未承認", obs[0]["text"])
        self.assertIn("e2", obs[0]["text"])
        report = usage.evaluation_summary([row])
        self.assertIsNone(report["quality_avg"])
        self.assertEqual(report["issues"], 1)
        proposal["stage"] = "shadow"
        self.assertEqual(rules.observe(row), [])
        self.assertEqual(usage.evaluation_summary([row])["issues"], 0)

    def test_collect_preserves_unknown_and_evidence(self):
        proposal = {"schema_version": 1, "stage": "advisory", "status": "unknown", "cause": "unknown",
                    "checks": [], "evidence": [{"id": "e1", "text": "done", "source": "reported_output"}]}
        self.write_ledger("20260803", [evaluation_row(evaluation={"quality": None, "issue": "none", "proposal": proposal})])
        st = self.make_store()
        collect.collect_budget_ledger(self.make_args(), st)
        ev = next(st.iter_records())["evaluation"]
        self.assertEqual(ev["proposal"], proposal)
        self.assertIsNone(ev["quality"])

    def test_proposal_distillation_cannot_generate_settings_or_call_llm(self):
        proposal = {"schema_version": 1, "stage": "advisory", "status": "problem", "cause": "unknown",
                    "checks": [{"text": "render", "status": "unmet", "evidence_id": "e2"}]}
        self.write_ledger("20260803", [evaluation_row(evaluation={"quality": None, "issue": "none", "proposal": proposal})])
        st = self.make_store()
        collect.collect_budget_ledger(self.make_args(), st)
        args = self.make_args(force=True, distill_min_occurrences=1, review=True)
        extract.cmd_extract(args)
        with mock.patch.object(distill, "agent_for", return_value=("fake-cloud", "fake")), \
             mock.patch.object(distill, "run_llm", side_effect=AssertionError("must not call LLM")):
            self.assertEqual(distill.cmd_distill(args), 0)
        insights = list(self.make_store().iter_insights())
        self.assertEqual(len(insights), 1)
        self.assertEqual(insights[0]["kind"], "quality-review")
        self.assertIsNone(insights[0]["declaration"])


class ImprovementInboxTests(AuditTestCase):
    def proposal(self):
        return {"schema_version": 1, "stage": "advisory", "status": "problem", "cause": "unknown",
                "checks": [{"text": "レポートを保存する", "status": "unmet", "evidence_id": "e1"}],
                "evidence": [{"id": "e1", "text": "レポートは未作成"}]}

    def test_requires_named_procedure_and_specific_evidence(self):
        for kind in ("skill", "task", "workflow"):
            row = evaluation_row(artifact={"kind": kind, "name": "daily"})
            self.assertEqual(rules.improvement_of(row, self.proposal())["target"], row["artifact"])
        for changes in ({"stage": "shadow"}, {"status": "unknown"}, {"truncated": True},
                        {"cause": "tool-failure"}, {"cause": "config-issue"}, {"evidence": []},
                        {"checks": []}, {"error": {"message": "failed"}}):
            self.assertIsNone(rules.improvement_of(evaluation_row(), {**self.proposal(), **changes}))
        for row in (evaluation_row(used={}), evaluation_row(used={"skills": ["a", "b"]}),
                    evaluation_row(artifact={"kind": "tool", "name": "browser"})):
            self.assertIsNone(rules.improvement_of(row, self.proposal()))

    def test_failed_verification_has_a_reproducible_command(self):
        p = self.proposal()
        p["checks"] = [{"text": "検証に合格する", "status": "unmet", "basis": "verification",
                        "receipts": [{"command": "npm test", "exitCode": 1}]}]
        self.assertIn("npm test", rules.improvement_of(evaluation_row(), p)["criteria"][0]["evidence"])
        p["checks"][0]["receipts"][0]["exitCode"] = 0
        self.assertIsNone(rules.improvement_of(evaluation_row(), p))

    def test_one_evidenced_failure_survives_collect_extract_distill(self):
        self.write_ledger("20260803", [evaluation_row(evaluation={"proposal": self.proposal()})])
        st = self.make_store()
        collect.collect_budget_ledger(self.make_args(), st)
        args = self.make_args(force=True, distill_min_occurrences=2)
        extract.cmd_extract(args)
        distill.cmd_distill(args)
        insights = list(self.make_store().iter_insights())
        self.assertEqual(len(insights), 1)
        self.assertTrue(insights[0]["actionable"])
        self.assertEqual(insights[0]["improvement"]["criteria"][0]["evidence"], "レポートは未作成")
