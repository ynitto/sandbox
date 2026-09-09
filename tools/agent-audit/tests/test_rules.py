"""rules（決定的 extract / distill）— LLM を 1 回も呼ばずに records → insights → tasks が通る。"""
from __future__ import annotations

import unittest
from unittest import mock

from _shared import AuditTestCase, configfile, distill, extract, tasksout, util
from agent_audit import rules


def _forbid_llm(*_a, **_k):
    raise AssertionError("rules 既定で LLM が呼ばれた")


class ObserveTests(unittest.TestCase):
    def test_failed_run_yields_avoid_with_group(self):
        obs = rules.observe({"kind": "run", "tool": "agent-flow", "workload": "flow",
                             "status": "failed", "error_class": "transient",
                             "agent_cli": "claude", "model": "sonnet"})
        self.assertEqual([o["kind"] for o in obs], ["avoid"])
        self.assertEqual(obs[0]["key"], "rule:failed:transient")
        self.assertIn("[agent-error:transient]", obs[0]["text"])
        self.assertEqual(obs[0]["group"], "avoid|failed|agent-flow/flow|claude:sonnet|transient")

    def test_same_failure_different_record_same_group(self):
        base = {"kind": "run", "tool": "agent-flow", "workload": "flow",
                "status": "failed", "error_class": "quota", "agent_cli": "codex", "model": ""}
        a = rules.observe({**base, "id": "aud-1", "ref": "run-1"})
        b = rules.observe({**base, "id": "aud-2", "ref": "run-2", "retries": 5})
        self.assertEqual(a[0]["group"], b[0]["group"])
        self.assertEqual([o["kind"] for o in b], ["avoid", "prompt-issue"])

    def test_session_unmeasured_and_long(self):
        obs = rules.observe({"kind": "session", "source": "codex-native", "agent_cli": "codex",
                             "model": "", "measured": False, "turns": 674})
        self.assertEqual({o["kind"] for o in obs}, {"config-issue", "learn"})
        short = rules.observe({"kind": "session", "source": "claude-native", "agent_cli": "claude",
                               "model": "claude-opus-5", "measured": True, "turns": 3})
        self.assertEqual(short, [])

    def test_decision_mismatch_is_per_decision(self):
        obs = rules.observe({"kind": "run", "tool": "agent-flow", "workload": "flow",
                             "status": "done", "decision_comparisons": [
                                 {"decision": "route", "agree": False},
                                 {"decision": "split", "agree": True},
                                 {"decision": "protect", "agree": False}]})
        self.assertEqual(sorted(o["key"] for o in obs),
                         ["rule:decision-mismatch:protect", "rule:decision-mismatch:route"])

    def test_agent_for_defaults_to_rules_only_without_override(self):
        class A:
            agent_cli = "claude"
            model = None
            agents = {"extract": {}, "distill": {"model": "opus"}}
        self.assertEqual(configfile.agent_for(A(), "extract"), ("rules", None))
        self.assertEqual(configfile.agent_for(A(), "distill"), ("claude", "opus"))
        self.assertEqual(configfile.agent_for(A(), "review"), ("claude", None))


class RulesPipelineTests(AuditTestCase):
    def _seed(self, n, **over):
        st = self.make_store()
        for i in range(n):
            rec = {"id": f"aud-r{i}", "_epoch": 1754200000.0 + i * 86400,
                   "ts": util.epoch_to_iso(1754200000.0 + i * 86400),
                   "kind": "run", "tool": "agent-flow", "workload": "flow",
                   "status": "failed", "error_class": "transient",
                   "agent_cli": "claude", "model": "sonnet",
                   "excerpt_ref": f"transcripts/claude/s{i}.jsonl"}
            rec.update(over)
            st.append_record(rec)
        st.save_state()
        return st

    def test_records_to_tasks_without_llm(self):
        self._seed(12)
        with mock.patch.object(extract, "run_llm", side_effect=_forbid_llm), \
                mock.patch.object(distill, "run_llm", side_effect=_forbid_llm):
            self.assertEqual(extract.cmd_extract(self.make_args(force=True)), 0)
            self.assertEqual(distill.cmd_distill(self.make_args(force=True, review=True)), 0)
        st = self.make_store()
        obs = list(st.iter_observations())
        self.assertEqual(len(obs), 12)
        self.assertTrue(all(o["extract_agent"] == "rules" and o["group"] for o in obs))
        self.assertEqual(len(st.state["extracted"]), 12)
        insights = list(st.iter_insights())
        self.assertEqual(len(insights), 1)
        ins = insights[0]
        self.assertEqual(ins["occurrences"], 12)
        self.assertEqual(ins["confidence"], "high")
        self.assertEqual(ins["kind"], "rule-candidate")
        self.assertIsNone(ins["declaration"])
        self.assertIsNone(ins["review"])            # --review は rules では飛ばす
        self.assertEqual(ins["distill_agent"], "rules")
        self.assertIn("2025-08-03〜2025-08-14", ins["statement"])
        self.assertEqual(ins["scope"], {"purpose": "", "model": "sonnet"})
        tasks, ids = tasksout.insight_tasks(st)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(ids, [ins["id"]])
        self.assertTrue(tasks[0]["desc"])

    def test_rerun_is_idempotent_and_cluster_growth_revises(self):
        self._seed(3)
        args = lambda: self.make_args(force=True)          # noqa: E731
        extract.cmd_extract(args())
        distill.cmd_distill(args())
        st = self.make_store()
        first = list(st.iter_insights())[0]
        self.assertEqual(first["confidence"], "low")
        # 何も増えていなければ洞察は書き直さない
        extract.cmd_extract(args())
        distill.cmd_distill(args())
        self.assertEqual(list(st.iter_insights())[0]["ts"], first["ts"])
        # 同じ失敗が育ったら同じ id を改訂する
        for i in range(3, 8):
            st.append_record({"id": f"aud-r{i}", "_epoch": 1754200000.0 + i,
                              "ts": util.epoch_to_iso(1754200000.0 + i),
                              "kind": "run", "tool": "agent-flow", "workload": "flow",
                              "status": "failed", "error_class": "transient",
                              "agent_cli": "claude", "model": "sonnet"})
        st.save_state()
        extract.cmd_extract(args())
        distill.cmd_distill(args())
        grown = list(st.iter_insights())
        self.assertEqual(len(grown), 1)
        self.assertEqual(grown[0]["id"], first["id"])
        self.assertEqual(grown[0]["occurrences"], 8)
        self.assertEqual(grown[0]["confidence"], "medium")

    def test_group_clusters_do_not_merge_across_error_class(self):
        self._seed(2)
        st = self.make_store()
        for i in range(2, 4):
            st.append_record({"id": f"aud-q{i}", "_epoch": 1754200000.0 + i,
                              "ts": util.epoch_to_iso(1754200000.0 + i),
                              "kind": "run", "tool": "agent-flow", "workload": "flow",
                              "status": "failed", "error_class": "quota",
                              "agent_cli": "claude", "model": "sonnet"})
        st.save_state()
        extract.cmd_extract(self.make_args(force=True))
        clusters = distill.cluster_observations(list(st.iter_observations()))
        self.assertEqual(sorted(len(c["observations"]) for c in clusters), [2, 2])
        self.assertNotEqual(clusters[0]["cluster_id"], clusters[1]["cluster_id"])

    def test_session_correction_rows_count_once_with_latest_values(self):
        st = self.make_store()
        base = {"id": "aud-s1", "kind": "session", "source": "codex-native",
                "agent_cli": "codex", "model": "", "measured": False}
        st.append_record({**base, "_epoch": 1754200000.0, "ts": util.epoch_to_iso(1754200000.0),
                          "turns": 40})
        # 補正行: seen 済みなので append_record は弾く。records へ直接 2 行目を書く
        util.append_jsonl(f"{self.audit_dir}/records/20250804.jsonl",
                          {**base, "ts": util.epoch_to_iso(1754300000.0),
                           "turns": 700, "model": "gpt-5", "measured": True})
        st.save_state()
        extract.cmd_extract(self.make_args(force=True, extract_min_records=0))
        st = self.make_store()                       # state はディスクから読み直す
        obs = list(st.iter_observations())
        self.assertEqual(len(st.state["extracted"]), 1)
        # 最新行（model あり）で観測するので unmeasured ではなく long-session だけ
        self.assertEqual([o["kind"] for o in obs], ["learn"])
        self.assertIn("codex:gpt-5", obs[0]["text"])

    def test_limit_caps_records_not_calls(self):
        self._seed(5)
        extract.cmd_extract(self.make_args(force=True, limit=2))
        st = self.make_store()
        self.assertEqual(len(st.state["extracted"]), 2)


if __name__ == "__main__":
    unittest.main()
