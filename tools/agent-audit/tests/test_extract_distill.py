from __future__ import annotations

import json
import unittest
from unittest import mock

from _shared import AuditTestCase, distill, extract, tasksout, util


class ExtractPipelineTests(AuditTestCase):
    def _seed_failed_records(self, n):
        st = self.make_store()
        for i in range(n):
            st.append_record({"id": f"aud-f{i}", "_epoch": 1754200000.0 + i,
                              "ts": util.epoch_to_iso(1754200000.0 + i),
                              "kind": "run", "tool": "agent-flow", "status": "failed",
                              "error_class": "transient"})
        st.save_state()
        return st

    def test_extract_with_stub_llm(self):
        self._seed_failed_records(12)
        args = self.make_args(force=True, limit=0,
                              agents={"extract": {"agent_cli": "ollama", "model": "qwen3"}})
        reply = json.dumps({"observations": [
            {"kind": "avoid", "text": "タイムアウトが短すぎて transient 失敗が続く"}]})
        with mock.patch.object(extract, "run_llm", return_value=reply) as m:
            self.assertEqual(extract.cmd_extract(args), 0)
        self.assertEqual(m.call_count, 12)
        st = self.make_store()
        obs = list(st.iter_observations())
        self.assertEqual(len(obs), 12)
        self.assertEqual(obs[0]["kind"], "avoid")
        self.assertEqual(obs[0]["extract_agent"], "ollama")   # 既定の agents.extract
        # 冪等: もう一度走らせても処理済みは再抽出しない
        with mock.patch.object(extract, "run_llm", return_value=reply) as m2:
            args2 = self.make_args(force=True)
            extract.cmd_extract(args2)
        self.assertEqual(m2.call_count, 0)

    def test_extract_repair_retry_then_drop(self):
        self._seed_failed_records(1)
        args = self.make_args(force=True, extract_min_records=0)
        with mock.patch.object(extract, "run_llm", return_value="not json") as m:
            self.assertEqual(extract.cmd_extract(args), 0)
        self.assertEqual(m.call_count, 2)                     # 本試行 + 修復 1 回で打ち止め
        st = self.make_store()
        self.assertEqual(list(st.iter_observations()), [])
        self.assertNotIn("aud-f0", st.state["extracted"])     # 未抽出のまま次回へ

    def test_extract_max_calls_cap(self):
        self._seed_failed_records(5)
        args = self.make_args(force=True, extract_max_calls=2)
        reply = json.dumps({"observations": []})
        with mock.patch.object(extract, "run_llm", return_value=reply) as m:
            extract.cmd_extract(args)
        self.assertEqual(m.call_count, 2)


class ClusterTests(unittest.TestCase):
    def _obs(self, oid, kind, text):
        return {"id": oid, "kind": kind, "text": text, "evidence": []}

    def test_similar_texts_cluster_deterministically(self):
        obs = [
            self._obs("obs-a", "avoid", "verify コマンドの timeout 不足で transient 失敗"),
            self._obs("obs-b", "avoid", "verify の timeout 不足により transient 失敗が発生"),
            self._obs("obs-c", "learn", "spill 退避で長大プロンプトが安定した"),
        ]
        c1 = distill.cluster_observations(obs)
        c2 = distill.cluster_observations(list(reversed(obs)))
        self.assertEqual(len(c1), 2)
        ids1 = sorted(c["cluster_id"] for c in c1)
        ids2 = sorted(c["cluster_id"] for c in c2)
        self.assertEqual(ids1, ids2)                          # 入力順に依らず決定的

    def test_different_kind_never_merges(self):
        obs = [self._obs("obs-a", "avoid", "timeout 不足で失敗"),
               self._obs("obs-b", "learn", "timeout 不足で失敗")]
        self.assertEqual(len(distill.cluster_observations(obs)), 2)


class DistillPipelineTests(AuditTestCase):
    def _seed_observations(self, n, text="verify の timeout 不足で transient 失敗"):
        st = self.make_store()
        for i in range(n):
            st.append_observation({"id": f"obs-{i:02d}", "record_id": f"aud-{i}",
                                   "ts": util.now_iso(), "kind": "avoid",
                                   "text": f"{text}（事例 {i}）", "evidence": [f"aud-{i}"]})
        st.save_state()
        return st

    def test_distill_with_stub_llm_and_tasks_export(self):
        self._seed_observations(6)
        args = self.make_args(force=True, review=False, limit=0)
        reply = json.dumps({"statement": "verify 系の timeout 既定を見直すべき",
                            "kind": "config-fix",
                            "suggested_action": "agent_timeout を 600 へ引き上げる",
                            "confidence": "medium"})
        with mock.patch.object(distill, "run_llm", return_value=reply):
            self.assertEqual(distill.cmd_distill(args), 0)
        st = self.make_store()
        insights = list(st.iter_insights())
        self.assertEqual(len(insights), 1)
        ins = insights[0]
        self.assertEqual(ins["occurrences"], 6)
        self.assertFalse(ins["exported"])
        # 同じ観測集合では再蒸留しない（クラスタが育っていない）
        with mock.patch.object(distill, "run_llm", return_value=reply) as m:
            distill.cmd_distill(self.make_args(force=True))
        self.assertEqual(m.call_count, 0)
        # tasks 出力（task.schema.json 形・冪等 id・洞察参照つき）
        tasks, insight_ids = tasksout.insight_tasks(st)
        self.assertEqual(len(tasks), 1)
        self.assertTrue(tasks[0]["id"].startswith("audit-"))
        self.assertLessEqual(len(tasks[0]["id"]), 48)
        self.assertEqual(tasks[0]["source"], "agent-audit")
        self.assertEqual(insight_ids, [ins["id"]])

    def test_min_occurrences_blocks_singletons(self):
        self._seed_observations(1)
        args = self.make_args(force=True)
        with mock.patch.object(distill, "run_llm") as m:
            distill.cmd_distill(args)
        self.assertEqual(m.call_count, 0)


if __name__ == "__main__":
    unittest.main()
