#!/usr/bin/env python3
"""Phase 2A send --model contract tests."""
import os
import sys
import threading
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class SendModelTests(unittest.TestCase):
    def test_existing_session_rejects_cwd_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old"
            new = Path(tmp) / "new"
            old.mkdir()
            new.mkdir()
            mgr = al.SessionManager.__new__(al.SessionManager)
            mgr._panes = {"e1": "%1"}
            mgr._prompt_cwds = {"e1": str(old)}
            mgr._owners = {"e1": "scheduled"}
            mgr._effective_model = {"e1": None}
            mgr._lock = threading.Lock()
            mgr._target_path = str(old)
            self.assertFalse(mgr.ensure_session("e1", "n", owner="scheduled", cwd=str(new)))

    def test_model_requires_daemon(self):
        args = SimpleNamespace(
            prompt=["hi"], session="%1", dir=None, priority="normal",
            wait=False, response_timeout=None, model="m1",
            sandbox=False, force=False, ralph=False, max_iterations=None,
        )
        with mock.patch.object(al, "load_config", return_value=({}, None, False)), \
             mock.patch.object(al, "_init_cli_profile_from_config", return_value=None), \
             mock.patch.object(al.shutil, "which", return_value="/usr/bin/kiro-cli"), \
             mock.patch.object(al, "_tmux_cmd", return_value=mock.Mock(returncode=0, stdout="%1")), \
             mock.patch.object(al, "_resolve_prompt_text", return_value="hi"), \
             mock.patch.object(al, "_resolve_send_entry_id", return_value=("%1", "hi")), \
             mock.patch.object(al, "_find_running_daemon", return_value=None):
            with self.assertRaises(SystemExit) as cm:
                al.cmd_send(args, Path("/tmp"))
            self.assertEqual(cm.exception.code, 1)

    def test_model_mismatch_fails(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {"e1": "%1"}
        mgr._owners = {"e1": "scheduled"}
        mgr._effective_model = {"e1": "old"}
        mgr._generation = {"e1": 1}
        mgr._lock = threading.Lock()
        mgr._target_path = "/tmp"
        mgr.sync_entries = mock.Mock()
        mgr.set_state_extras = mock.Mock()
        mgr.get_pane_id = mock.Mock(return_value="%1")
        mgr.get_effective_model = mock.Mock(return_value="old")
        mgr.get_generation = mock.Mock(return_value=1)
        mgr.ensure_session = mock.Mock(return_value=False)
        sched = al.PeriodicScheduler(mgr, [{
            "id": "e1", "name": "n", "prompt": "p", "interval_minutes": 10, "enabled": True,
        }], workspace="/tmp")
        with mock.patch.object(sched, "_build_model_launch_spec", return_value={
            "argv": ["cli"], "cwd": "/tmp", "profile_name": "cli",
            "effective_model": "new", "ownership": "managed-persistent", "env": {},
        }), mock.patch.object(al, "_capture_pane", return_value=">"), \
             mock.patch.object(al, "_CLI_PROFILE") as prof, \
             mock.patch.object(sched, "_fail_execution") as fail:
            prof.is_ready.return_value = True
            prof.is_legacy = False
            prof.argv = ["cli"]
            prof.name = "cli"
            req = al.make_dispatch_request(
                source="schedule", entry_id="e1", prompt="p",
                meta={"execution": {"model": "new", "session_key": "e1"}},
            )
            result = sched._try_dispatch_request(req)
            self.assertEqual(result, "discard")
            fail.assert_called()
            self.assertEqual(fail.call_args.kwargs.get("reason"), "model_mismatch")


if __name__ == "__main__":
    unittest.main()
