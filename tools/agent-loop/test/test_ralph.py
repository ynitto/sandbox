#!/usr/bin/env python3
"""Phase 2A Ralph loop contract tests."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


def _stub_session():
    mgr = al.SessionManager.__new__(al.SessionManager)
    mgr._panes = {"e1": "%1"}
    mgr._prompt_names = {"e1": "ralph"}
    mgr._tmux_names = {}
    mgr._prompt_cwds = {"e1": "/tmp"}
    mgr._owners = {"e1": "scheduled"}
    mgr._ownership = {"e1": "managed-persistent"}
    mgr._generation = {"e1": 1}
    mgr._effective_model = {"e1": None}
    mgr._launch_fingerprint = {}
    mgr._instr_rev = {}
    mgr._restart_locks = {}
    mgr._state_extras = {}
    mgr._lock = threading.Lock()
    mgr._target_path = "/tmp"
    mgr._startup_timeout = 5
    mgr.ensure_session = mock.Mock(return_value=True)
    mgr.get_pane_id = mock.Mock(return_value="%1")
    mgr.get_generation = mock.Mock(return_value=1)
    mgr.get_effective_model = mock.Mock(return_value=None)
    mgr.send_prompt = mock.Mock(return_value=True)
    mgr.is_pane_alive = mock.Mock(return_value=True)
    mgr.cleanup_managed_pane = mock.Mock(return_value=True)
    mgr.restart_pane = mock.Mock()
    mgr.sync_entries = mock.Mock()
    mgr.set_state_extras = mock.Mock()
    return mgr


class RalphTests(unittest.TestCase):
    def _scheduler(self, max_iterations=3):
        mgr = _stub_session()
        entries = [{
            "id": "e1", "name": "ralph", "prompt": "改善して",
            "mode": "ralph", "max_iterations": max_iterations,
            "interval_minutes": 60, "enabled": True,
        }]
        sched = al.PeriodicScheduler(mgr, entries, semaphore=None, slot_monitor=None, workspace="/tmp")
        return sched, mgr

    def test_n1_goes_finalizing(self):
        sched, mgr = self._scheduler(1)
        with mock.patch.object(al, "_capture_pane", return_value="> "), \
             mock.patch.object(al, "_CLI_PROFILE") as prof:
            prof.is_ready.return_value = True
            prof.is_legacy = True
            prof.argv = None
            prof.name = "kiro"
            prof.clear_command = ""
            req = al.make_dispatch_request(
                source="schedule", entry_id="e1", prompt="改善して",
                meta={"execution": {"kind": "ralph", "max_steps": 1, "session_key": "e1"}},
            )
            # no slot monitor → complete() runs immediately → DONE
            result = sched._try_dispatch_request(req)
            self.assertEqual(result, "done")
            sent = mgr.send_prompt.call_args[0][1]
            self.assertIn("最終反復", sent)

    def test_n_gt1_enqueues_child_with_hold_slot(self):
        sched, mgr = self._scheduler(3)
        monitor = mock.Mock()
        monitor.is_tracking.return_value = False
        sched._slot_monitor = monitor
        with mock.patch.object(al, "_capture_pane", return_value="> "), \
             mock.patch.object(al, "_CLI_PROFILE") as prof:
            prof.is_ready.return_value = True
            prof.is_legacy = True
            prof.argv = None
            prof.name = "kiro"
            prof.clear_command = ""
            req = al.make_dispatch_request(
                source="schedule", entry_id="e1", prompt="改善して",
                meta={
                    "execution": {
                        "kind": "ralph", "max_steps": 3, "step": 1,
                        "session_key": "e1", "root_id": "root1",
                    },
                    "original_prompt": "改善して",
                },
                request_id="root1",
            )
            result = sched._try_dispatch_request(req)
            self.assertEqual(result, "done")
            # track は hold_slot=True
            kwargs = monitor.track.call_args.kwargs
            self.assertTrue(kwargs.get("hold_slot"))

    def test_natural_text_send_uses_managed_ralph_path(self):
        sched, mgr = self._scheduler(2)
        monitor = mock.Mock()
        monitor.is_tracking.return_value = False
        sched._slot_monitor = monitor
        req = al.make_dispatch_request(
            source="send", entry_id="%1", prompt="改善して", request_id="root-send",
            meta={
                "target": "%1",
                "original_prompt": "改善して",
                "execution": {
                    "kind": "ralph", "root_id": "root-send", "step": 1,
                    "max_steps": 2, "session_key": "%1",
                },
            },
        )
        with mock.patch.object(al, "_capture_pane", return_value="> "), \
             mock.patch.object(al, "_CLI_PROFILE") as prof, \
             mock.patch.object(al, "send_prompt_to_session") as adhoc_send:
            prof.is_ready.return_value = True
            prof.is_legacy = True
            prof.argv = None
            prof.name = "kiro"
            prof.clear_command = ""
            self.assertEqual(sched._try_dispatch_request(req), "done")
        adhoc_send.assert_not_called()
        self.assertIn("iteration 1/2", mgr.send_prompt.call_args.args[1])
        self.assertEqual(req["entry_id"], "e1")

    def test_child_skips_preflight_and_reuses_lease(self):
        sched, mgr = self._scheduler(3)
        sched._upsert_execution("root1", slot_lease="%1", state="ITERATING", step=1, max_steps=3)
        with mock.patch.object(al, "_capture_pane", return_value="> "), \
             mock.patch.object(sched, "_run_preflight") as preflight, \
             mock.patch.object(al, "_CLI_PROFILE") as prof, \
             mock.patch.object(sched, "_try_acquire_slot") as acq:
            prof.is_ready.return_value = True
            prof.is_legacy = True
            prof.argv = None
            prof.name = "kiro"
            prof.clear_command = ""
            child = al.make_dispatch_request(
                source="ralph", entry_id="e1",
                prompt=al.build_ralph_prompt("改善して", 2, 3),
                meta={
                    "execution": {
                        "kind": "ralph", "max_steps": 3, "step": 2,
                        "session_key": "e1", "root_id": "root1",
                    },
                    "original_prompt": "改善して",
                },
            )
            sched._slot_monitor = None
            result = sched._try_dispatch_request(child)
            self.assertEqual(result, "done")
            preflight.assert_not_called()
            acq.assert_not_called()

    def test_three_iterations_reach_terminal_state(self):
        sched, mgr = self._scheduler(3)
        monitor = mock.Mock()
        monitor.is_tracking.return_value = False
        sched._slot_monitor = monitor
        root = al.make_dispatch_request(
            source="schedule", entry_id="e1", prompt="改善して", request_id="root3",
            meta={
                "original_prompt": "改善して",
                "execution": {
                    "kind": "ralph", "root_id": "root3", "step": 1,
                    "max_steps": 3, "session_key": "e1",
                },
            },
        )
        with mock.patch.object(al, "_capture_pane", return_value="> "), \
             mock.patch.object(al, "_CLI_PROFILE") as prof:
            prof.is_ready.return_value = True
            prof.is_legacy = True
            prof.argv = None
            prof.name = "kiro"
            prof.clear_command = ""
            self.assertEqual(sched._try_dispatch_request(root), "done")
            for _ in range(2):
                monitor.track.call_args.kwargs["on_complete"]()
                child = sched._pending.pop(0)
                monitor.reset_mock()
                monitor.is_tracking.return_value = False
                self.assertEqual(sched._try_dispatch_request(child), "done")
            monitor.track.call_args.kwargs["on_complete"]()
        self.assertEqual(mgr.send_prompt.call_count, 3)
        self.assertNotIn("root3", sched._executions)

    def test_callback_exception_fails_execution(self):
        sched, mgr = self._scheduler(2)
        req = al.make_dispatch_request(
            source="schedule", entry_id="e1", prompt="x",
            meta={
                "execution": {
                    "kind": "ralph", "max_steps": 2, "step": 1,
                    "root_id": "r", "session_key": "e1",
                },
                "original_prompt": "x",
            },
            request_id="r",
        )
        sched._upsert_execution("r", slot_lease="%1", state="ITERATING")
        monitor = mock.Mock()
        sched._slot_monitor = monitor
        with mock.patch.object(sched, "_enqueue_ralph_child", side_effect=RuntimeError("boom")), \
             mock.patch.object(sched, "_release_slot") as rel:
            sched._track_active(req, "%1", hold_slot=True)
            monitor.track.call_args.kwargs["on_complete"]()
            rel.assert_called()
            self.assertNotIn("r", sched._executions)

    def test_cancel_clears_ralph_pending(self):
        sched, mgr = self._scheduler(5)
        sched._upsert_execution("root1", entry_id="e1", pane_id="%1", state="ITERATING")
        child = al.make_dispatch_request(
            source="ralph", entry_id="e1", prompt="c",
            meta={"execution": {"kind": "ralph", "root_id": "root1", "step": 2, "max_steps": 5}},
        )
        with sched._lock:
            sched._pending.append(child)
        ok = sched._cancel_target("e1")
        self.assertTrue(ok)
        self.assertEqual(sched._pending, [])


if __name__ == "__main__":
    unittest.main()
