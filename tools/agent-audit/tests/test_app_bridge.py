# agent-app（Windows 側）との境界: 追加台帳（audit-feed）・追加ホーム・成果物の適格性。
# 設計: docs/plans/2026-09-16-agent-app-agent-audit-split-and-artifact-sharing-design.md
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from _shared import AuditTestCase  # noqa: E402

from agent_audit import collect, qualifications, readers, stats  # noqa: E402

NOW = dt.datetime(2026, 9, 16, 6, 0, tzinfo=dt.timezone.utc)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def feed_row(**over):
    row = {"ts": "2026-09-16T05:00:00Z", "node": "win-pc", "tool": "agent-app",
           "workload": "task", "agent_cli": "claude", "model": "sonnet",
           "seconds": 12.0, "status": "done",
           "artifact": {"kind": "statemachine", "name": "daily-report",
                        "origin": "repo:sandbox"}}
    row.update(over)
    return row


class LedgerDirsTests(AuditTestCase):
    def _write(self, directory, name, rows):
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_extra_ledger_dir_collected_with_artifact(self):
        feed = os.path.join(self.tmp, "userData", "audit-feed")
        self._write(feed, "20260916.jsonl", [feed_row()])
        args = self.make_args(ledger_dirs=[feed])
        st = self.make_store()
        added = collect.collect_budget_ledger(args, st)
        st.save_state()
        self.assertEqual(added, 1)
        rec = next(iter(st.iter_records()))
        self.assertEqual(rec["kind"], "ledger")
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["artifact"],
                         {"kind": "statemachine", "name": "daily-report",
                          "origin": "repo:sandbox"})

    def test_second_pass_adds_nothing_and_new_line_is_incremental(self):
        feed = os.path.join(self.tmp, "feed")
        self._write(feed, "20260916.jsonl", [feed_row()])
        args = self.make_args(ledger_dirs=[feed])
        st = self.make_store()
        collect.collect_budget_ledger(args, st)
        self.assertEqual(collect.collect_budget_ledger(args, st), 0)
        with open(os.path.join(feed, "20260916.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(feed_row(status="failed")) + "\n")
        self.assertEqual(collect.collect_budget_ledger(args, st), 1)

    def test_missing_dir_is_skipped_and_budget_ledger_still_read(self):
        self._write(os.path.join(self.budget_dir, "ledger"), "20260916.jsonl",
                    [{"ts": "2026-09-16T04:00:00Z", "agent_cli": "claude", "seconds": 3}])
        args = self.make_args(ledger_dirs=[os.path.join(self.tmp, "nope")])
        st = self.make_store()
        self.assertEqual(collect.collect_budget_ledger(args, st), 1)

    def test_artifact_without_name_is_dropped(self):
        feed = os.path.join(self.tmp, "feed")
        self._write(feed, "20260916.jsonl", [feed_row(artifact={"kind": "skill"})])
        args = self.make_args(ledger_dirs=[feed])
        st = self.make_store()
        collect.collect_budget_ledger(args, st)
        self.assertNotIn("artifact", next(iter(st.iter_records())))


class ExtraHomesTests(AuditTestCase):
    def test_tilde_declaration_expands_to_extra_home(self):
        got = readers.expand_paths(["~/.claude/projects", "/abs/only"],
                                   ["/mnt/c/Users/me"])
        self.assertIn("/mnt/c/Users/me/.claude/projects", got)
        self.assertEqual([p for p in got if p.startswith("/abs")], ["/abs/only"])

    def test_absolute_declaration_is_not_rebased(self):
        got = readers.expand_paths(["/var/log/x"], ["/mnt/c/Users/me"])
        self.assertEqual(got, ["/var/log/x"])

    def test_collect_reads_sessions_under_extra_home(self):
        from _shared import claude_session_jsonl
        home = os.path.join(self.tmp, "winhome")
        target = os.path.join(home, ".claude", "projects")
        claude_session_jsonl(os.path.join(target, "a.jsonl"), sid="win-1")
        spec = {"session_log": {"format": "jsonl-dir", "paths": ["~/.claude/projects"],
                                "usage": True}}
        args = self.make_args(extra_homes=[home])
        st = self.make_store()
        from unittest import mock
        with mock.patch.object(collect, "agent_defs_with_session_log",
                               return_value=[("claude", spec)]):
            added = collect.collect_cli_native(args, st, with_transcripts=False)
        self.assertEqual(added, 1)
        self.assertEqual(next(iter(st.iter_records()))["session_id"], "win-1")


class VscodeChatReaderTests(AuditTestCase):
    def _session(self, directory, name="s1.json", requests=None):
        os.makedirs(directory, exist_ok=True)
        payload = {"sessionId": "vsc-1", "creationDate": 1757980800000,
                   "requests": requests if requests is not None else [
                       {"requestId": "r1", "timestamp": 1757980900000,
                        "message": {"text": "直して"}, "response": [{"value": "直した"}],
                        "modelId": "gpt-5"}]}
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path

    def test_reads_chat_session_through_glob_declaration(self):
        base = os.path.join(self.tmp, "code", "User", "workspaceStorage", "abc", "chatSessions")
        self._session(base)
        got = readers.read_sessions(
            {"format": "vscode-chat",
             "paths": [os.path.join(self.tmp, "code", "User", "workspaceStorage",
                                    "*", "chatSessions")]},
            want_messages=True)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["native_id"], "vsc-1")
        self.assertEqual(got[0]["model"], "gpt-5")
        self.assertEqual(got[0]["turns"], 2)
        self.assertEqual([r for r, _ in got[0]["messages"]], ["user", "assistant"])
        # VS Code は使用量を保存しない。実測と偽らない。
        self.assertFalse(got[0]["usage_measured"])
        self.assertIsNone(got[0]["tokens_in"])

    def test_empty_chat_is_not_collected(self):
        base = os.path.join(self.tmp, "chatSessions")
        self._session(base, requests=[])
        self.assertEqual(readers.read_sessions({"format": "vscode-chat", "paths": [base]}), [])

    def test_unreadable_file_is_skipped_not_raised(self):
        base = os.path.join(self.tmp, "chatSessions")
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "broken.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(readers.read_sessions({"format": "vscode-chat", "paths": [base]}), [])

    def test_format_is_declared(self):
        self.assertIn("vscode-chat", readers.FORMATS)

    def test_session_browser_shares_the_same_parser(self):
        from agent_audit import session_browser
        self.assertIs(session_browser.vscode_session, readers.vscode_session)


class ArtifactQualificationTests(AuditTestCase):
    def _records(self, st, *statuses, kind="statemachine", name="daily-report"):
        for i, status in enumerate(statuses):
            st.append_record({"id": f"rec-{kind}-{name}-{i}", "_epoch": NOW.timestamp() - 60,
                              "ts": "2026-09-16T05:00:00Z", "kind": "ledger",
                              "source": "budget-ledger", "agent_cli": "claude",
                              "status": status,
                              "error_class": "" if status == "done" else "verify",
                              "artifact": {"kind": kind, "name": name, "origin": "repo:s"}})

    def test_last_seen_comes_from_the_saved_timestamp(self):
        # `_epoch` は保存時に落ちるので、これを読むと last_seen が必ず空になる。
        st = self.make_store()
        self._records(st, *["done"] * 5)
        args = self.make_args(apply=True)
        got = qualifications._qualify_artifacts(args, st, now=NOW, window_days=30)
        self.assertEqual(_read(got["artifacts_file"])["artifacts"][0]["last_seen"],
                         "2026-09-16T05:00:00Z")

    def test_all_passing_becomes_qualified(self):
        st = self.make_store()
        self._records(st, *["done"] * 5)
        args = self.make_args(apply=True)
        got = qualifications._qualify_artifacts(args, st, now=NOW, window_days=30)
        self.assertTrue(got["artifacts_applied"])
        doc = _read(got["artifacts_file"])
        row = doc["artifacts"][0]
        self.assertEqual((row["kind"], row["name"]), ("statemachine", "daily-report"))
        self.assertEqual(row["status"], "qualified")
        self.assertEqual(row["samples"], 5)

    def test_failures_drop_to_trial_and_change_is_reported(self):
        st = self.make_store()
        self._records(st, "done", "done", "done", "failed", "failed")
        args = self.make_args(apply=True)
        got = qualifications._qualify_artifacts(args, st, now=NOW, window_days=30)
        self.assertEqual(got["artifact_changes"][0]["to"], "trial")
        doc = _read(got["artifacts_file"])
        self.assertEqual(doc["artifacts"][0]["failure_modes"], ["verify"])

    def test_dry_run_writes_nothing(self):
        st = self.make_store()
        self._records(st, *["done"] * 5)
        args = self.make_args(apply=False)
        got = qualifications._qualify_artifacts(args, st, now=NOW, window_days=30)
        self.assertFalse(os.path.exists(got["artifacts_file"]))
        self.assertEqual(got["observed_artifacts"], 1)

    def test_unchanged_second_run_does_not_bump_revision(self):
        st = self.make_store()
        self._records(st, *["done"] * 5)
        args = self.make_args(apply=True)
        first = qualifications._qualify_artifacts(args, st, now=NOW, window_days=30)
        rev = _read(first["artifacts_file"])["revision"]
        second = qualifications._qualify_artifacts(args, st, now=NOW, window_days=30)
        self.assertTrue(second.get("artifacts_unchanged"))
        self.assertEqual(_read(first["artifacts_file"])["revision"], rev)

    def test_artifact_never_leaks_into_candidate_qualifications(self):
        st = self.make_store()
        self._records(st, *["done"] * 5)
        args = self.make_args(apply=True,
                              qualifications_file=os.path.join(self.tmp, "q.json"))
        summary = qualifications.cmd_qualify(args, st)
        self.assertEqual(summary["observed_artifacts"], 1)
        # 候補側（Compiler が読む契約）には成果物を混ぜない
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "q.json")))

    def test_record_without_terminal_status_is_not_counted(self):
        st = self.make_store()
        st.append_record({"id": "r-x", "_epoch": NOW.timestamp(), "ts": "2026-09-16T05:00:00Z",
                          "kind": "ledger", "status": "running",
                          "artifact": {"kind": "skill", "name": "x"}})
        args = self.make_args(apply=True)
        got = qualifications._qualify_artifacts(args, st, now=NOW, window_days=30)
        self.assertEqual(got["observed_artifacts"], 0)

    def test_stale_artifact_falls_back_to_unknown(self):
        st = self.make_store()
        self._records(st, *["done"] * 5)
        args = self.make_args(apply=True)
        qualifications._qualify_artifacts(args, st, now=NOW, window_days=30)
        later = NOW + dt.timedelta(days=60)
        got = qualifications._qualify_artifacts(args, st, now=later, window_days=30)
        doc = _read(got["artifacts_file"])
        self.assertEqual(doc["artifacts"][0]["status"], "unknown")


class LedgerOutcomeTests(AuditTestCase):
    def test_counts_by_workload_and_skips_rows_without_verdict(self):
        rows = [
            {"workload": "chat", "status": "done"},
            {"workload": "chat", "status": "cancelled"},
            {"workload": "task", "status": "failed", "error_class": "verify"},
            {"workload": "task", "status": "done"},
            {"workload": "task"},                       # 消費だけの行（成否の申告なし）
            {"workload": "task", "status": "running"},   # 終端でない
        ]
        got = stats.aggregate_ledger_outcomes(rows)
        self.assertEqual(got["runs"], 4)
        self.assertEqual(got["status"], {"done": 2, "cancelled": 1, "failed": 1})
        self.assertEqual(got["error_class"], {"verify": 1})
        self.assertEqual(got["pass_rate"], 0.5)
        self.assertEqual([b["workload"] for b in got["workloads"]], ["chat", "task"])

    def test_no_rows_has_no_rate(self):
        got = stats.aggregate_ledger_outcomes([])
        self.assertEqual(got["runs"], 0)
        self.assertNotIn("pass_rate", got)

    def test_stats_reports_app_feed_without_run_records(self):
        st = self.make_store()
        st.append_record({"id": "l1", "_epoch": NOW.timestamp(), "ts": "2026-09-16T05:00:00Z",
                          "kind": "ledger", "tool": "agent-app", "workload": "chat",
                          "status": "done"})
        got = stats.aggregate_stats(st, "month")
        self.assertEqual(got["tools"], [])          # run レコードは無い
        self.assertEqual(got["ledger"]["runs"], 1)  # それでも成否は出る


if __name__ == "__main__":
    unittest.main()
