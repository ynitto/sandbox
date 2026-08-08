#!/usr/bin/env python3
"""lifecycle priority: stop > drain > control pause > budget > local pause。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class LifecycleTests(unittest.TestCase):
    def _sched(self, workspace: str):
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        s._workspace = workspace
        s._draining = False
        s._run_state = "run"
        s._mem_paused = False
        s._node_budget_warned_at = 0.0
        s._pending = []
        s._lock = al.threading.Lock()
        s._external_queues = {}
        s._active_count = 0
        s._health = {}
        s._mem_ok_streak = 0
        return s

    def test_stop_highest(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sched(tmp)
            s._draining = True
            with mock.patch.object(al, "_control_lifecycle", return_value="stop"), \
                 mock.patch.object(al, "_node_budget_state", return_value=None), \
                 mock.patch.object(al, "load_local_pause", return_value=True):
                self.assertEqual(s._lifecycle_gate(1000.0), "stop")

    def test_drain_before_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sched(tmp)
            s._draining = True
            s._active_count = 1
            with mock.patch.object(al, "_control_lifecycle", return_value="pause"), \
                 mock.patch.object(al, "_node_budget_state", return_value=None), \
                 mock.patch.object(al, "load_local_pause", return_value=False):
                self.assertEqual(s._lifecycle_gate(1000.0), "pause")
                self.assertEqual(s._run_state, "draining")

    def test_control_pause_before_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sched(tmp)
            nb = {"exceeded": True, "on_exhausted": "pause", "spent_min": 1,
                  "limit_min": 1, "spent_tokens": 0, "token_limit": 0, "period": "d"}
            with mock.patch.object(al, "_control_lifecycle", return_value="pause"), \
                 mock.patch.object(al, "_node_budget_state", return_value=nb), \
                 mock.patch.object(al, "load_local_pause", return_value=False):
                self.assertEqual(s._lifecycle_gate(1000.0), "pause")
                self.assertEqual(s._run_state, "paused")

    def test_budget_before_local_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sched(tmp)
            nb = {"exceeded": True, "on_exhausted": "pause", "spent_min": 1,
                  "limit_min": 1, "spent_tokens": 0, "token_limit": 0, "period": "d"}
            with mock.patch.object(al, "_control_lifecycle", return_value="run"), \
                 mock.patch.object(al, "_node_budget_state", return_value=nb), \
                 mock.patch.object(al, "load_local_pause", return_value=True):
                self.assertEqual(s._lifecycle_gate(1000.0), "pause")

    def test_local_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sched(tmp)
            with mock.patch.object(al, "_control_lifecycle", return_value="run"), \
                 mock.patch.object(al, "_node_budget_state", return_value=None), \
                 mock.patch.object(al, "load_local_pause", return_value=True):
                self.assertEqual(s._lifecycle_gate(1000.0), "pause")

    def test_drain_discards_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sched(tmp)
            s._pending = [
                al.make_dispatch_request(source="send", entry_id="e", prompt="x"),
            ]
            with mock.patch.object(al, "load_send_requests", return_value=[]), \
                 mock.patch.object(al, "remove_send_request_file"):
                s._enter_drain()
            self.assertEqual(s._pending, [])
            self.assertTrue(s._draining)

    def test_drain_exit_when_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sched(tmp)
            s._draining = True
            s._active_count = 0
            with mock.patch.object(al, "_control_lifecycle", return_value="run"), \
                 mock.patch.object(al, "_node_budget_state", return_value=None), \
                 mock.patch.object(al, "load_local_pause", return_value=False):
                self.assertEqual(s._lifecycle_gate(1000.0), "drain_exit")

    def test_memory_pause_requires_two_healthy_checks_to_resume(self):
        s = self._sched("/tmp")
        s._health = {"min_free_memory_mb": 100}
        with mock.patch.object(al, "_get_free_memory_mb", side_effect=[50, 150, 150]):
            s.check_memory_pressure()
            self.assertTrue(s._mem_paused)
            s.check_memory_pressure()
            self.assertTrue(s._mem_paused)
            s.check_memory_pressure()
            self.assertFalse(s._mem_paused)


if __name__ == "__main__":
    unittest.main()
