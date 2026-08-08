#!/usr/bin/env python3
"""Phase 2A clean_session contract tests."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class CleanSessionTests(unittest.TestCase):
    def test_cli_profile_exposes_optional_commands(self):
        p = al.CliProfile(name="x", spec={
            "interactive": {
                "ready_pattern": ">",
                "save_command": "/save",
                "clear_command": "",
                "exit_command": "/quit",
            },
        })
        self.assertEqual(p.save_command, "/save")
        self.assertEqual(p.clear_command, "")
        self.assertEqual(p.exit_command, "/quit")

    def test_success_count_triggers_cleanup(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {"e1": "%1"}
        mgr._lock = threading.Lock()
        mgr._startup_timeout = 1
        mgr.send_prompt = mock.Mock(return_value=True)
        mgr.is_pane_alive = mock.Mock(return_value=False)
        mgr.cleanup_managed_pane = mock.Mock(return_value=True)
        mgr.get_pane_id = mock.Mock(return_value=None)
        mgr.sync_entries = mock.Mock()
        mgr.set_state_extras = mock.Mock()
        entries = [{
            "id": "e1", "name": "w", "prompt": "p",
            "clean_session": 2, "interval_minutes": 10, "enabled": True,
        }]
        sched = al.PeriodicScheduler(mgr, entries, workspace="/tmp")
        sched._session_runtime("e1")["success_count"] = 1
        req = al.make_dispatch_request(
            source="schedule", entry_id="e1", prompt="p",
            meta={"execution": {"session_key": "e1", "root_id": "r1", "kind": "normal"}},
            request_id="r1",
        )
        with mock.patch.object(al, "_CLI_PROFILE") as prof, \
             mock.patch.object(sched, "_release_slot") as rel, \
             mock.patch.object(al, "_capture_pane", return_value=">"):
            prof.save_command = "/save"
            prof.clear_command = "/clear"
            prof.exit_command = ""
            prof.is_ready.return_value = True
            sched._on_execution_complete(req, "%1")
            # success_count was 1 → becomes 2 → cleanup
            mgr.cleanup_managed_pane.assert_called()
            mgr.send_prompt.assert_any_call("e1", "/save")
            mgr.send_prompt.assert_any_call("e1", "/clear")
            rel.assert_called()

    def test_failed_does_not_increment(self):
        mgr = mock.Mock()
        mgr.sync_entries = mock.Mock()
        mgr.set_state_extras = mock.Mock()
        mgr._target_path = "/tmp"
        sched = al.PeriodicScheduler(mgr, [{
            "id": "e1", "name": "w", "prompt": "p",
            "clean_session": 2, "interval_minutes": 10, "enabled": True,
        }], workspace="/tmp")
        sess = sched._session_runtime("e1")
        before = sess["success_count"]
        req = al.make_dispatch_request(
            source="schedule", entry_id="e1", prompt="p",
            meta={"execution": {"session_key": "e1", "root_id": "r", "kind": "normal"}},
            request_id="r",
        )
        with mock.patch.object(sched, "_release_slot"):
            sched._fail_execution(req, "%1", reason="x")
        self.assertEqual(sched._session_runtime("e1")["success_count"], before)

    def test_missing_commands_skip(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {"e1": "%1"}
        mgr._lock = threading.Lock()
        mgr._startup_timeout = 1
        mgr.send_prompt = mock.Mock(return_value=True)
        mgr.is_pane_alive = mock.Mock(return_value=False)
        mgr.cleanup_managed_pane = mock.Mock(return_value=True)
        mgr.get_pane_id = mock.Mock(return_value=None)
        mgr.sync_entries = mock.Mock()
        mgr.set_state_extras = mock.Mock()
        sched = al.PeriodicScheduler(mgr, [{
            "id": "e1", "name": "w", "prompt": "p",
            "clean_session": 1, "interval_minutes": 10, "enabled": True,
        }], workspace="/tmp")
        req = al.make_dispatch_request(
            source="schedule", entry_id="e1", prompt="p",
            meta={"execution": {"session_key": "e1", "root_id": "r", "kind": "normal"}},
            request_id="r",
        )
        with mock.patch.object(al, "_CLI_PROFILE") as prof, \
             mock.patch.object(sched, "_release_slot"):
            prof.save_command = ""
            prof.clear_command = ""
            prof.exit_command = ""
            sched._on_execution_complete(req, "%1")
            mgr.send_prompt.assert_not_called()
            mgr.cleanup_managed_pane.assert_called()


if __name__ == "__main__":
    unittest.main()
