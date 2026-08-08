#!/usr/bin/env python3
"""Phase 2A --force boundary tests."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class ForceTests(unittest.TestCase):
    def test_force_ralph_cli_error(self):
        args = SimpleNamespace(
            prompt=["hi"], session="%1", dir=None, priority="normal",
            wait=False, response_timeout=None, model=None,
            sandbox=False, force=True, ralph=True, max_iterations=2,
        )
        with mock.patch.object(al, "load_config", return_value=({}, None, False)), \
             mock.patch.object(al, "_init_cli_profile_from_config", return_value=None), \
             mock.patch.object(al.shutil, "which", return_value="/usr/bin/kiro-cli"):
            with self.assertRaises(SystemExit) as cm:
                al.cmd_send(args, Path("/tmp"))
            self.assertEqual(cm.exception.code, 2)

    def test_force_bypasses_ready_not_tracking(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {"e1": "%1"}
        mgr._owners = {"e1": "scheduled"}
        mgr._effective_model = {"e1": None}
        mgr._generation = {"e1": 1}
        mgr._prompt_cwds = {"e1": "/tmp"}
        mgr._lock = threading.Lock()
        mgr._target_path = "/tmp"
        mgr.sync_entries = mock.Mock()
        mgr.set_state_extras = mock.Mock()
        mgr.ensure_session = mock.Mock(return_value=True)
        mgr.get_pane_id = mock.Mock(return_value="%1")
        mgr.get_generation = mock.Mock(return_value=1)
        mgr.get_effective_model = mock.Mock(return_value=None)
        mgr.send_prompt = mock.Mock(return_value=True)
        mgr.restart_pane = mock.Mock()
        sched = al.PeriodicScheduler(mgr, [{
            "id": "e1", "name": "n", "prompt": "p", "interval_minutes": 10, "enabled": True,
        }], workspace="/tmp")
        monitor = mock.Mock()
        monitor.is_tracking.return_value = False
        sched._slot_monitor = monitor
        with mock.patch.object(al, "_capture_pane", return_value="BUSY"), \
             mock.patch.object(al, "_CLI_PROFILE") as prof:
            prof.is_ready.return_value = False  # would block without force
            prof.is_legacy = True
            prof.argv = None
            prof.name = "kiro"
            prof.clear_command = ""
            req = al.make_dispatch_request(
                source="send", entry_id="e1", prompt="p",
                meta={"force": True, "execution": {"force_ready": True, "skip_preflight": True}},
            )
            result = sched._try_dispatch_request(req)
            self.assertEqual(result, "done")

    def test_force_does_not_bypass_active_tracking(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {"e1": "%1"}
        mgr._owners = {"e1": "scheduled"}
        mgr._effective_model = {"e1": None}
        mgr._generation = {"e1": 1}
        mgr._prompt_cwds = {"e1": "/tmp"}
        mgr._lock = threading.Lock()
        mgr._target_path = "/tmp"
        mgr.sync_entries = mock.Mock()
        mgr.set_state_extras = mock.Mock()
        mgr.ensure_session = mock.Mock(return_value=True)
        mgr.get_pane_id = mock.Mock(return_value="%1")
        mgr.get_generation = mock.Mock(return_value=1)
        mgr.get_effective_model = mock.Mock(return_value=None)
        sched = al.PeriodicScheduler(mgr, [{
            "id": "e1", "name": "n", "prompt": "p", "interval_minutes": 10, "enabled": True,
        }], workspace="/tmp")
        monitor = mock.Mock()
        monitor.is_tracking.return_value = True
        sched._slot_monitor = monitor
        with mock.patch.object(al, "_CLI_PROFILE") as prof:
            prof.is_legacy = True
            prof.argv = None
            req = al.make_dispatch_request(
                source="send", entry_id="e1", prompt="p",
                meta={"force": True, "execution": {"force_ready": True}},
            )
            result = sched._try_dispatch_request(req)
            self.assertEqual(result, "defer")


if __name__ == "__main__":
    unittest.main()
