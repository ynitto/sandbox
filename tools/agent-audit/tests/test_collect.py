from __future__ import annotations

import json
import os
import unittest

from _shared import AuditTestCase, claude_session_jsonl, collect, ledger_row


class LedgerCollectTests(AuditTestCase):
    def test_incremental_by_offset(self):
        self.write_ledger("20260803", [ledger_row(), ledger_row(ts="2026-08-03T11:00:00Z")])
        st = self.make_store()
        args = self.make_args()
        self.assertEqual(collect.collect_budget_ledger(args, st), 2)
        self.assertEqual(collect.collect_budget_ledger(args, st), 0)   # 冪等
        self.write_ledger("20260803", [ledger_row(ts="2026-08-03T12:00:00Z")])
        self.assertEqual(collect.collect_budget_ledger(args, st), 1)   # 追記分だけ
        recs = list(st.iter_records())
        self.assertEqual(len(recs), 3)
        self.assertTrue(all(r["kind"] == "ledger" for r in recs))

    def test_observation_rows_are_kept_but_not_as_consumption(self):
        """観測行（消費 0）は落とさず `kind: event` で入れる。

        `kind: ledger` に混ぜると、消費 0 の行が実行回数を水増しして平均消費を下げる
        ——**コストを測るために書いた行がコストの数字を動かす**。落とさないのは、
        昇格や枠当たりが何回起きたかを後から数えたいため。
        """
        self.write_ledger("20260803", [
            ledger_row(),
            ledger_row(ts="2026-08-03T11:00:00Z", seconds=0.0, ref="", event="quota",
                       quota_kind="rate_limit", reset_at="2026-08-03T12:00:00Z"),
            ledger_row(ts="2026-08-03T11:05:00Z", seconds=0.0, ref="", event="model_escalation",
                       escalation={"agent_cli": "claude", "from_agent_cli": "ollama"}),
        ])
        st = self.make_store()
        self.assertEqual(collect.collect_budget_ledger(self.make_args(), st), 3)
        by_kind = {}
        for rec in st.iter_records():
            by_kind.setdefault(rec["kind"], []).append(rec)
        self.assertEqual([r["ts"] for r in by_kind["ledger"]], ["2026-08-03T10:00:00Z"])
        self.assertEqual({r["event"] for r in by_kind["event"]}, {"quota", "model_escalation"})

    def test_legacy_quota_rows_without_event_are_dropped(self):
        """`event` を付ける前の世代の quota 行は、消費に混ざらないよう従来どおり落とす。"""
        self.write_ledger("20260803", [
            ledger_row(ts="2026-08-03T11:00:00Z", seconds=0.0, ref="", quota_kind="exhausted"),
        ])
        st = self.make_store()
        self.assertEqual(collect.collect_budget_ledger(self.make_args(), st), 0)

    def test_measured_flag_follows_tokens(self):
        self.write_ledger("20260803", [ledger_row(tokens_in=100, tokens_out=10),
                                       ledger_row(ts="2026-08-03T11:00:00Z")])
        st = self.make_store()
        collect.collect_budget_ledger(self.make_args(), st)
        measured = [r["measured"] for r in st.iter_records()]
        self.assertEqual(sorted(measured), [False, True])

    def test_method_and_trial_evidence_are_preserved(self):
        self.write_ledger("20260803", [ledger_row(
            run_id="run-1", flow_node="n1", methods=["test-first"],
            trial={"id": "t1", "variant": "candidate"})])
        st = self.make_store()
        collect.collect_budget_ledger(self.make_args(), st)
        rec = next(st.iter_records())
        self.assertEqual(rec["methods"], ["test-first"])
        self.assertEqual(rec["trial"], {"id": "t1", "variant": "candidate"})


class FlowBusCollectTests(AuditTestCase):
    def _make_run(self, bus, run_id, status, failure="", events=()):
        rd = os.path.join(bus, "runs", run_id)
        os.makedirs(os.path.join(rd, "events"), exist_ok=True)
        meta = {"status": status, "updated_at": "2026-08-03T10:00:00Z"}
        if failure:
            meta["failure_reason"] = failure
        with open(os.path.join(rd, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        with open(os.path.join(rd, "events", "w1.jsonl"), "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

    def test_terminal_runs_only_and_error_class(self):
        bus = os.path.join(self.tmp, "bus")
        self._make_run(bus, "r1", "failed", failure="[agent-error:quota] 上限",
                       events=[{"kind": "result", "status": "failed"},
                               {"kind": "result", "status": "failed"}])
        self._make_run(bus, "r2", "running")
        self._make_run(bus, "r3", "done",
                       events=[{"kind": "verify", "verdict": "pass"}])
        results = os.path.join(bus, "runs", "r3", "results")
        os.makedirs(results)
        with open(os.path.join(results, "n1.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "n1", "kind": "work", "status": "done",
                       "agent_cli": "claude", "model": "sonnet"}, f)
        st = self.make_store()
        args = self.make_args(flow_buses=[bus])
        self.assertEqual(collect.collect_flow_buses(args, st), 3)   # terminal run 2 + result 1
        recs = {r["ref"]: r for r in st.iter_records()}
        self.assertEqual(recs["r1"]["error_class"], "quota")
        self.assertEqual(recs["r1"]["retries"], 2)
        self.assertEqual(recs["r3"]["verify"], "pass")
        # run の verdict は run スコープの名前で持ち、ノードの品質は status で見る
        result = recs["r3/n1"]
        self.assertEqual((result["purpose"], result["model"], result["status"], result["run_verify"]),
                         ("work", "sonnet", "done", "pass"))
        self.assertNotIn("verify", result, "run 単位の判定をノードの verify として配らない")

    def test_node_outcomes_are_per_node_not_the_run_verdict(self):
        """1 つの run に別モデルのノードが並ぶとき、品質はノードごとに分かれる。

        run 全体の統一 verify を各ノードへ配ると、失敗したノードのモデルまで pass を
        貰い、格付け（モデル別 PASS 率）が測れなくなる。
        """
        bus = os.path.join(self.tmp, "bus")
        self._make_run(bus, "r1", "done", events=[{"kind": "verify", "verdict": "pass"}])
        results = os.path.join(bus, "runs", "r1", "results")
        os.makedirs(results)
        for node, model, status in (("n1", "opus", "done"), ("n2", "qwen3", "failed")):
            with open(os.path.join(results, f"{node}.json"), "w", encoding="utf-8") as f:
                json.dump({"id": node, "kind": "work", "status": status, "model": model}, f)
        st = self.make_store()
        collect.collect_flow_buses(self.make_args(flow_buses=[bus]), st)
        recs = {r["ref"]: r for r in st.iter_records()}
        self.assertEqual(recs["r1/n1"]["status"], "done")
        self.assertEqual(recs["r1/n2"]["status"], "failed")
        self.assertEqual({recs["r1/n1"]["run_verify"], recs["r1/n2"]["run_verify"]}, {"pass"})

    def test_context_allocation_hit_is_collected(self):
        bus = os.path.join(self.tmp, "bus")
        self._make_run(bus, "r1", "done")
        results = os.path.join(bus, "runs", "r1", "results")
        os.makedirs(results)
        with open(os.path.join(results, "n1.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "n1", "kind": "work", "status": "done",
                       "context_allocation": {"reported": True, "hit": False,
                                              "outside_reads": 2}}, f)
        st = self.make_store()
        collect.collect_flow_buses(self.make_args(flow_buses=[bus]), st)
        rec = next(r for r in st.iter_records() if r["ref"] == "r1/n1")
        self.assertFalse(rec["allocation_hit"])
        self.assertEqual(rec["outside_reads"], 2)

    def test_planner_rule_comparisons_are_collected(self):
        bus = os.path.join(self.tmp, "bus")
        self._make_run(bus, "r1", "done")
        graph = {"strategy": {"decision_comparisons": [
            {"decision": "planner.pattern", "llm": "tournament",
             "rule": "fan-out-and-synthesize", "agree": False}]}}
        with open(os.path.join(bus, "runs", "r1", "graph.json"), "w", encoding="utf-8") as f:
            json.dump(graph, f)
        st = self.make_store()
        collect.collect_flow_buses(self.make_args(flow_buses=[bus]), st)
        rec = next(r for r in st.iter_records() if r["ref"] == "r1")
        self.assertEqual(rec["decision_comparisons"], graph["strategy"]["decision_comparisons"])

    def test_method_and_trial_evidence_are_collected(self):
        bus = os.path.join(self.tmp, "bus")
        self._make_run(bus, "r1", "done")
        graph = {"strategy": {"methods": ["plan-first"],
                              "trial": {"id": "t1", "variant": "baseline"}}}
        with open(os.path.join(bus, "runs", "r1", "graph.json"), "w", encoding="utf-8") as f:
            json.dump(graph, f)
        results = os.path.join(bus, "runs", "r1", "results")
        os.makedirs(results)
        with open(os.path.join(results, "n1.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "n1", "kind": "work", "status": "done",
                       "methods": ["test-first"],
                       "trial": {"id": "t1", "variant": "baseline"}}, f)
        st = self.make_store()
        collect.collect_flow_buses(self.make_args(flow_buses=[bus]), st)
        recs = {r["ref"]: r for r in st.iter_records()}
        self.assertEqual(recs["r1"]["methods"], ["plan-first", "test-first"])
        self.assertEqual(recs["r1/n1"]["methods"], ["test-first"])
        self.assertEqual(recs["r1/n1"]["trial"], {"id": "t1", "variant": "baseline"})

    def test_collected_runs_are_not_rescanned(self):
        """収集済みの終端 run は results/ ごと読み飛ばす（run 数に比例して重くならない）。"""
        bus = os.path.join(self.tmp, "bus")
        self._make_run(bus, "r1", "done")
        results = os.path.join(bus, "runs", "r1", "results")
        os.makedirs(results)
        with open(os.path.join(results, "n1.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "n1", "kind": "work", "status": "done"}, f)
        st = self.make_store()
        args = self.make_args(flow_buses=[bus])
        self.assertEqual(collect.collect_flow_buses(args, st), 2)
        os.chmod(results, 0o000)       # 2 回目に触ったら glob が空になって差が出る
        try:
            self.assertEqual(collect.collect_flow_buses(args, st), 0)
        finally:
            os.chmod(results, 0o755)

    def test_missing_configured_source_fails_closed(self):
        args = self.make_args(flow_buses=[os.path.join(self.tmp, "no-such-bus")])
        st = self.make_store()
        with self.assertRaises(collect.SourceError):
            collect.collect_flow_buses(args, st)


class ProjectRunlogCollectTests(AuditTestCase):
    def test_runlog_rows(self):
        root = os.path.join(self.tmp, "proj")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "run-log.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": "r-1", "node": "pc-a",
                                "ts": "2026-08-03T10:30:00", "reason": "idle",
                                "tokens": 41000, "cost": 0.21, "duration_s": 1802.5,
                                "done": 4, "escalations": 1}) + "\n")
        st = self.make_store()
        args = self.make_args(project_roots=[root])
        self.assertEqual(collect.collect_project_roots(args, st), 1)
        rec = next(iter(st.iter_records()))
        self.assertEqual(rec["tool"], "agent-project")
        self.assertEqual(rec["tokens_out"], 41000)
        self.assertEqual(rec["escalations"], 1)


class CorrelateTests(AuditTestCase):
    def test_unique_match_links(self):
        led = {"id": "aud-l1", "ts": "2026-08-03T10:01:00Z", "seconds": 60.0,
               "agent_cli": "claude", "model": "sonnet"}
        sess = {"id": "aud-s1", "ts": "2026-08-03T10:00:30Z",
                "started_at": "2026-08-03T09:59:30Z",
                "agent_cli": "claude", "model": "claude-sonnet-4"}
        links = collect.correlate([led], [sess])
        self.assertEqual(links, {"aud-l1": "aud-s1"})

    def test_ambiguous_match_links_nothing(self):
        led = {"id": "aud-l1", "ts": "2026-08-03T10:01:00Z", "seconds": 60.0,
               "agent_cli": "claude", "model": ""}
        s1 = {"id": "aud-s1", "ts": "2026-08-03T10:00:30Z",
              "started_at": "2026-08-03T09:59:30Z", "agent_cli": "claude", "model": ""}
        s2 = {"id": "aud-s2", "ts": "2026-08-03T10:00:40Z",
              "started_at": "2026-08-03T09:59:40Z", "agent_cli": "claude", "model": ""}
        self.assertEqual(collect.correlate([led], [s1, s2]), {})   # 偽の実測を作らない

    def test_cli_mismatch_links_nothing(self):
        led = {"id": "aud-l1", "ts": "2026-08-03T10:01:00Z", "seconds": 60.0,
               "agent_cli": "codex", "model": ""}
        sess = {"id": "aud-s1", "ts": "2026-08-03T10:00:30Z",
                "started_at": "2026-08-03T09:59:30Z", "agent_cli": "claude", "model": ""}
        self.assertEqual(collect.correlate([led], [sess]), {})


class CliNativeCollectTests(AuditTestCase):
    def test_collect_with_declared_session_log(self):
        agents_dir = os.path.join(self.tmp, "agents")
        os.makedirs(agents_dir, exist_ok=True)
        sess_root = os.path.join(self.tmp, "claude-projects")
        claude_session_jsonl(os.path.join(sess_root, "p", "sess-1.jsonl"))
        with open(os.path.join(agents_dir, "claude.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "claude", "command": ["claude"],
                       "session_log": {"format": "jsonl-dir", "paths": [sess_root],
                                       "usage": True}}, f)
        os.environ["KIRO_AGENTS_DIR"] = agents_dir     # プラグイン契約の探索 env（agents 側の契約）
        try:
            st = self.make_store()
            args = self.make_args()
            n = collect.collect_cli_native(args, st, with_transcripts=True)
        finally:
            os.environ["KIRO_AGENTS_DIR"] = os.path.join(self.tmp, "no-agents")
        self.assertEqual(n, 1)
        rec = next(iter(st.iter_records()))
        self.assertEqual(rec["kind"], "session")
        self.assertEqual(rec["agent_cli"], "claude")
        self.assertTrue(rec["measured"])
        transcript = os.path.join(st.root, rec["excerpt_ref"])
        self.assertTrue(os.path.isfile(transcript))


if __name__ == "__main__":
    unittest.main()
