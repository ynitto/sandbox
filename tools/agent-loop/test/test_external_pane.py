#!/usr/bin/env python3
"""Phase 2A external pane contract tests."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class ExternalPaneTests(unittest.TestCase):
    def _sched(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {}
        mgr._owners = {}
        mgr._lock = threading.Lock()
        mgr._target_path = "/tmp"
        mgr.sync_entries = mock.Mock()
        mgr.set_state_extras = mock.Mock()
        mgr.cleanup_managed_pane = mock.Mock()
        entries = [{
            "id": "e1", "name": "ext", "prompt": "review",
            "target": "reviewer", "interval_minutes": 60, "enabled": True,
        }]
        sched = al.PeriodicScheduler(mgr, entries, workspace="/tmp")
        self.assertTrue(sched.set_external_panes([
            {"name": "reviewer", "tmux_target": "rev:0.1"},
        ]))
        return sched, mgr

    def test_target_resolve_and_send_without_slot(self):
        sched, mgr = self._sched()
        monitor = mock.Mock()
        monitor.is_tracking.return_value = False
        sched._slot_monitor = monitor
        sem = mock.Mock()
        sched._semaphore = sem
        with mock.patch.object(al, "resolve_external_tmux_pane", return_value="%9") as resolve, \
             mock.patch.object(al, "_capture_pane", return_value="> "), \
             mock.patch.object(al, "send_prompt_to_session", return_value=True) as send, \
             mock.patch.object(al, "_CLI_PROFILE") as prof:
            prof.is_ready.return_value = True
            prof.is_legacy = True
            req = al.make_dispatch_request(
                source="schedule", entry_id="e1", prompt="review",
                meta={"execution": {
                    "target_kind": "external", "target": "reviewer",
                    "session_policy": "external",
                }},
            )
            result = sched._try_dispatch_request(req)
            self.assertEqual(result, "done")
            resolve.assert_called_with("rev:0.1")
            send.assert_called()
            sem.acquire.assert_not_called()
            mgr.ensure_session = getattr(mgr, "ensure_session", mock.Mock())

    def test_dead_target_keeps_pending(self):
        sched, _ = self._sched()
        with mock.patch.object(al, "resolve_external_tmux_pane", return_value=None):
            req = al.make_dispatch_request(
                source="schedule", entry_id="e1", prompt="review",
                meta={"execution": {
                    "target_kind": "external", "target": "reviewer",
                    "session_policy": "external",
                }},
            )
            result = sched._try_dispatch_request(req)
            self.assertEqual(result, "defer")

    def test_cancel_rejects_external(self):
        sched, mgr = self._sched()
        ok = sched._cancel_target("reviewer")
        self.assertFalse(ok)
        mgr.cleanup_managed_pane.assert_not_called()

    def test_sync_entries_skips_external(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {}
        mgr._prompt_names = {}
        mgr._tmux_names = {}
        mgr._prompt_cwds = {}
        mgr._owners = {}
        mgr._lock = threading.Lock()
        mgr._start_pane = mock.Mock()
        mgr.write_state = mock.Mock()
        mgr.sync_entries([{"id": "e1", "name": "x", "target": "reviewer"}])
        mgr._start_pane.assert_not_called()


if __name__ == "__main__":
    unittest.main()
