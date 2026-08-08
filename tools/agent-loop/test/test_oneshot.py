#!/usr/bin/env python3
"""Phase 2A oneshot contract tests."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class OneshotTests(unittest.TestCase):
    def _sched(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {}
        mgr._prompt_names = {}
        mgr._tmux_names = {}
        mgr._prompt_cwds = {}
        mgr._owners = {}
        mgr._ownership = {}
        mgr._generation = {}
        mgr._effective_model = {}
        mgr._launch_fingerprint = {}
        mgr._instr_rev = {}
        mgr._restart_locks = {}
        mgr._state_extras = {}
        mgr._lock = threading.Lock()
        mgr._target_path = "/tmp"
        mgr._startup_timeout = 30
        mgr.sync_entries = mock.Mock()
        mgr.ensure_session = mock.Mock(return_value=True)
        mgr.get_pane_id = mock.Mock(return_value=None)
        mgr.get_generation = mock.Mock(return_value=1)
        mgr.cleanup_managed_pane = mock.Mock(return_value=True)
        mgr.set_state_extras = mock.Mock()
        entries = [{
            "id": "o1", "name": "oneshot", "prompt": "review",
            "oneshot": True, "interval_minutes": 60, "enabled": True,
        }]
        return al.PeriodicScheduler(mgr, entries, workspace="/tmp"), mgr

    def test_sync_entries_skips_oneshot_panes(self):
        # validate_entries marks oneshot; SessionManager.sync_entries skips
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {}
        mgr._prompt_names = {}
        mgr._tmux_names = {}
        mgr._prompt_cwds = {}
        mgr._owners = {}
        mgr._lock = threading.Lock()
        mgr._start_pane = mock.Mock(return_value=True)
        mgr.write_state = mock.Mock()
        mgr.sync_entries([{"id": "o1", "name": "x", "oneshot": True, "cwd": None}])
        mgr._start_pane.assert_not_called()

    def test_overlap_coalesce_keeps_one(self):
        sched, _ = self._sched()
        sched._update_entry("o1", oneshot_state="PROCESSING")
        r1 = al.make_dispatch_request(source="schedule", entry_id="o1", prompt="a")
        r2 = al.make_dispatch_request(source="schedule", entry_id="o1", prompt="b")
        r3 = al.make_dispatch_request(source="schedule", entry_id="o1", prompt="c")
        self.assertTrue(sched._accept_request(r1))
        self.assertTrue(sched._accept_request(r2))
        self.assertTrue(sched._accept_request(r3))
        entry = sched._find_entry("o1")
        self.assertIsNotNone(entry["overlap_pending"])
        self.assertEqual(entry["overlap_pending"]["prompt"], "a")
        self.assertEqual(sched.queue_depth(), 0)

    def test_dropped_overlap_completes_waiter_and_ack(self):
        sched, _ = self._sched()
        sched._update_entry("o1", oneshot_state="PROCESSING")
        kept = al.make_dispatch_request(source="send", entry_id="o1", prompt="a")
        dropped = al.make_dispatch_request(
            source="send", entry_id="o1", prompt="b", meta={"wait": True}, request_id="drop",
        )
        with mock.patch.object(sched, "_finalize_ack") as ack, \
             mock.patch.object(al, "write_send_response") as response, \
             mock.patch.object(al, "send_response_path") as path:
            path.return_value.is_file.return_value = True
            self.assertTrue(sched._accept_request(kept))
            self.assertTrue(sched._accept_request(dropped))
        response.assert_called_once_with("drop", "completed")
        ack.assert_called_once_with(dropped, success=True)

    def test_finish_oneshot_runs_overlap(self):
        sched, _ = self._sched()
        pending = al.make_dispatch_request(source="schedule", entry_id="o1", prompt="kept")
        sched._update_entry("o1", oneshot_state="COMPLETING", overlap_pending=pending)
        sched._finish_oneshot("o1")
        entry = sched._find_entry("o1")
        self.assertEqual(entry["oneshot_state"], "IDLE")
        self.assertIsNone(entry["overlap_pending"])
        self.assertEqual(sched.queue_depth(), 1)

    def test_prewarm_no_slot(self):
        sched, mgr = self._sched()
        now = 1000.0
        sched._update_entry("o1", next_run_at=now + 10, oneshot_state="IDLE")
        with mock.patch.object(sched, "_build_model_launch_spec", return_value=None):
            sched._prewarm_oneshot(now)
        mgr.ensure_session.assert_called_once()
        entry = sched._find_entry("o1")
        self.assertEqual(entry["oneshot_state"], "READY")


if __name__ == "__main__":
    unittest.main()
