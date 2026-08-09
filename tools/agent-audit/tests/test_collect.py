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

    def test_measured_flag_follows_tokens(self):
        self.write_ledger("20260803", [ledger_row(tokens_in=100, tokens_out=10),
                                       ledger_row(ts="2026-08-03T11:00:00Z")])
        st = self.make_store()
        collect.collect_budget_ledger(self.make_args(), st)
        measured = [r["measured"] for r in st.iter_records()]
        self.assertEqual(sorted(measured), [False, True])


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
        result = recs["r3/n1"]
        self.assertEqual((result["purpose"], result["model"], result["verify"]),
                         ("work", "sonnet", "pass"))

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
