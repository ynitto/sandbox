# 候補単位 receipt の収集と qualifications 自動昇格・降格（実装計画 2026-08-15 E5）。
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from _shared import AuditTestCase  # noqa: E402

from agent_audit import collect, qualifications  # noqa: E402

DECISION = {"agent_cli": "aider", "model": "gemma4:e4b",
            "selection_source": "qualified-candidate",
            "qualification_id": "aider-gemma4-e4b-existing-test-repair-v1",
            "control_revision": 42, "rank": 1, "reason": "r", "fallback_from": None}
NOW = dt.datetime(2026, 8, 15, 6, 0, tzinfo=dt.timezone.utc)


class ReceiptCollectTests(AuditTestCase):
    def _flow_run(self, bus, rid, results):
        run = os.path.join(bus, "runs", rid)
        os.makedirs(os.path.join(run, "results"), exist_ok=True)
        with open(os.path.join(run, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"status": "done", "updated_at": "2026-08-15T00:00:00Z"}, f)
        for result in results:
            with open(os.path.join(run, "results", f"{result['id']}.json"),
                      "w", encoding="utf-8") as f:
                json.dump(result, f)

    def test_flow_result_receipt_fields_collected(self):
        bus = os.path.join(self.tmp, "bus")
        self._flow_run(bus, "r1", [{
            "id": "n1", "kind": "work", "status": "done",
            "agent_cli": "aider", "model": "gemma4:e4b",
            "operation_class": "existing-test-repair",
            "local_patch_blockers": ["x"],
            "execution_decision": DECISION,
        }])
        st = self.make_store()
        collect.collect_flow_buses(self.make_args(flow_buses=[bus]), st)
        rec = {r["ref"]: r for r in st.iter_records()}["r1/n1"]
        self.assertEqual(rec["operation_class"], "existing-test-repair")
        self.assertEqual(rec["execution_decision"]["rank"], 1)
        self.assertEqual(rec["local_patch_blockers"], ["x"])

    def test_amigos_turn_receipts_collected(self):
        bus = os.path.join(self.tmp, "amigos")
        mdir = os.path.join(bus, "missions", "am-1")
        os.makedirs(os.path.join(mdir, "events"), exist_ok=True)
        with open(os.path.join(mdir, "final.json"), "w", encoding="utf-8") as f:
            json.dump({"accepted": True}, f)
        with open(os.path.join(mdir, "events", "pc--arch.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-08-15T00:00:00Z", "turn": 1,
                                "cli_seconds": 2.0, "actions": 1, "rejected": 0}) + "\n")
            f.write(json.dumps({"ts": "2026-08-15T00:01:00Z", "turn": 2,
                                "cli_seconds": 3.0, "actions": 1, "rejected": 0,
                                "agent_cli": "ollama", "model": "gemma4:e4b",
                                "operation_class": "role-turn",
                                "execution_decision": dict(DECISION, agent_cli="ollama")}) + "\n")
        st = self.make_store()
        collect.collect_amigos_buses(self.make_args(amigos_buses=[bus]), st)
        recs = list(st.iter_records())
        turn = next(r for r in recs if r["kind"] == "event")
        self.assertEqual(turn["agent_cli"], "ollama")
        self.assertEqual(turn["operation_class"], "role-turn")
        self.assertEqual(turn["status"], "done")
        # ミッション集計（既存の run レコード）も維持
        self.assertTrue(any(r["kind"] == "run" for r in recs))

    def test_statemachine_log_collected_when_terminal(self):
        root = os.path.join(self.tmp, "proj")
        logs = os.path.join(root, ".statemachine-use", "logs")
        os.makedirs(logs, exist_ok=True)
        done = os.path.join(logs, "agent-loop-1-100.jsonl")
        with open(done, "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "execution_decision", **DECISION}) + "\n")
            f.write(json.dumps({"event": "check", "state": "s1", "check_ok": "false"}) + "\n")
            f.write(json.dumps({"event": "check", "state": "s1", "check_ok": "true"}) + "\n")
            f.write(json.dumps({"event": "terminal", "state": "done"}) + "\n")
        running = os.path.join(logs, "agent-loop-2-200.jsonl")
        with open(running, "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "execution_decision", **DECISION}) + "\n")
        st = self.make_store()
        added = collect.collect_project_roots(self.make_args(project_roots=[root]), st)
        self.assertEqual(added, 1)   # 走行中ログは持ち越し
        rec = next(iter(st.iter_records()))
        self.assertEqual((rec["source"], rec["status"]), ("statemachine-log", "done"))
        self.assertEqual((rec["checks"], rec["check_failures"]), (2, 1))
        self.assertEqual(rec["execution_decision"]["agent_cli"], "aider")


class BuildQualificationsTests(unittest.TestCase):
    def entry(self, samples, passed, timeouts=0, modes=()):
        return {"samples": samples, "passed": passed, "timeouts": timeouts,
                "failure_modes": set(modes)}

    def test_promotion_demotion_and_trial(self):
        stats = {
            ("aider", "gemma4:e4b", "existing-test-repair"): self.entry(6, 6),
            ("aider", "gemma4:e4b", "bounded-review"): self.entry(2, 2),
            ("ollama", "gemma4:12b", "extract"): self.entry(6, 2),
        }
        doc = qualifications.build_qualifications({}, stats, now=NOW)
        index = {(c["agent_cli"], c["model"]): c["qualifications"] for c in doc["candidates"]}
        repair = index[("aider", "gemma4:e4b")]["existing-test-repair"]
        self.assertEqual(repair["status"], "qualified")
        self.assertEqual(repair["source"], "receipt")
        self.assertTrue(repair["valid_until"] > "2026-08-15")
        self.assertEqual(index[("aider", "gemma4:e4b")]["bounded-review"]["status"], "trial")
        self.assertEqual(index[("ollama", "gemma4:12b")]["extract"]["status"], "trial")
        self.assertEqual(doc["revision"], 1)

    def test_forbidden_failure_mode_blocks(self):
        profiles = {"existing-test-repair": {
            "min_samples": 3, "min_pass_rate": 0.5, "max_timeout_rate": 1.0,
            "window_days": 30, "valid_for_days": 7,
            "forbidden_failure_modes": ["silent-scope-violation"]}}
        existing = {"revision": 4, "evaluation_profiles": profiles, "candidates": []}
        stats = {("aider", "gemma4:e4b", "existing-test-repair"):
                 self.entry(5, 4, modes=["silent-scope-violation"])}
        doc = qualifications.build_qualifications(existing, stats, now=NOW)
        qual = doc["candidates"][0]["qualifications"]["existing-test-repair"]
        self.assertEqual(qual["status"], "blocked")
        self.assertEqual(doc["revision"], 5)

    def test_expired_qualified_without_new_data_becomes_unknown(self):
        existing = {"revision": 1, "evaluation_profiles": {}, "candidates": [{
            "agent_cli": "aider", "model": "gemma4:e4b",
            "qualifications": {"existing-test-repair": {
                "qualification_id": "q1", "status": "qualified",
                "valid_until": "2026-08-01T00:00:00Z"}}}]}
        doc = qualifications.build_qualifications(existing, {}, now=NOW)
        qual = doc["candidates"][0]["qualifications"]["existing-test-repair"]
        self.assertEqual(qual["status"], "unknown")

    def test_qualified_valid_until_does_not_slide_on_requalify(self):
        existing = {"revision": 1, "evaluation_profiles": {}, "candidates": [{
            "agent_cli": "aider", "model": "gemma4:e4b",
            "qualifications": {"existing-test-repair": {
                "qualification_id": "q1", "status": "qualified",
                "valid_until": "2026-08-20T00:00:00Z"}}}]}
        stats = {("aider", "gemma4:e4b", "existing-test-repair"): self.entry(6, 6)}
        doc = qualifications.build_qualifications(existing, stats, now=NOW)
        qual = doc["candidates"][0]["qualifications"]["existing-test-repair"]
        self.assertEqual(qual["status"], "qualified")
        self.assertEqual(qual["valid_until"], "2026-08-20T00:00:00Z")

    def test_document_passes_contract_validation(self):
        from agentcore.executioncontract import qualifications_errors
        stats = {("aider", "gemma4:e4b", "existing-test-repair"): self.entry(6, 6)}
        doc = qualifications.build_qualifications({}, stats, now=NOW)
        self.assertEqual(qualifications_errors(doc), [])


class CmdQualifyTests(AuditTestCase):
    def _store_with_receipts(self, n_pass=6):
        st = self.make_store()
        for i in range(n_pass):
            st.append_record({
                "id": f"aud-r{i}", "_epoch": NOW.timestamp() - 60,
                "kind": "result", "source": "flow-bus", "workload": "flow",
                "agent_cli": "aider", "model": "gemma4:e4b",
                "operation_class": "existing-test-repair", "status": "done",
                "execution_decision": dict(DECISION)})
        return st

    def test_dry_run_reports_without_write(self):
        args = self.make_args(apply=False, window_days=0,
                              qualifications_file=os.path.join(self.tmp, "q.json"))
        summary = qualifications.cmd_qualify(args, self._store_with_receipts())
        self.assertFalse(summary["applied"])
        self.assertEqual(summary["observed_cells"], 1)
        self.assertEqual(summary["status_changes"][0]["to"], "qualified")
        self.assertFalse(os.path.exists(args.qualifications_file))

    def test_apply_writes_and_bumps_revision(self):
        path = os.path.join(self.tmp, "q.json")
        args = self.make_args(apply=True, window_days=0, qualifications_file=path)
        store = self._store_with_receipts()
        summary = qualifications.cmd_qualify(args, store)
        self.assertTrue(summary["applied"])
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["revision"], 1)
        summary2 = qualifications.cmd_qualify(args, store)
        self.assertFalse(summary2["applied"])
        self.assertTrue(summary2["unchanged"])
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["revision"], 1)

    def test_apply_does_not_create_empty_file_without_receipts(self):
        path = os.path.join(self.tmp, "q.json")
        args = self.make_args(apply=True, window_days=0, qualifications_file=path)
        summary = qualifications.cmd_qualify(args, self.make_store())
        self.assertFalse(summary["applied"])
        self.assertTrue(summary["unchanged"])
        self.assertFalse(os.path.exists(path))

    def test_apply_writes_when_samples_change(self):
        path = os.path.join(self.tmp, "q.json")
        args = self.make_args(apply=True, window_days=0, qualifications_file=path)
        store = self._store_with_receipts(6)
        qualifications.cmd_qualify(args, store)
        store.append_record({
            "id": "aud-r6", "_epoch": NOW.timestamp() - 60,
            "kind": "result", "source": "flow-bus", "workload": "flow",
            "agent_cli": "aider", "model": "gemma4:e4b",
            "operation_class": "existing-test-repair", "status": "done",
            "execution_decision": dict(DECISION)})
        summary = qualifications.cmd_qualify(args, store)
        self.assertTrue(summary["applied"])
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["revision"], 2)


if __name__ == "__main__":
    unittest.main()
