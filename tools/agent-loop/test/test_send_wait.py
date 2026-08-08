#!/usr/bin/env python3
"""send --wait exit codes（request 単位の完了状態）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class SendWaitTests(unittest.TestCase):
    def test_no_daemon_uses_standalone_send(self):
        args = SimpleNamespace(
            prompt=["hello"], session=None, dir=None, priority="normal",
            wait=False, response_timeout=None,
        )
        with mock.patch.object(al, "load_config", return_value=({}, None, False)), \
             mock.patch.object(al, "_init_cli_profile_from_config", return_value=None), \
             mock.patch.object(al.shutil, "which", return_value="/usr/bin/kiro-cli"), \
             mock.patch.object(al, "_read_all_states", return_value=[]), \
             mock.patch.object(al, "_session_name_exists", return_value=False), \
             mock.patch.object(al, "ensure_cli_session", return_value=True) as ensure, \
             mock.patch.object(al, "_resolve_target_pane", return_value="%1"), \
             mock.patch.object(al, "_resolve_send_entry_id", return_value=("%1", "hello")), \
             mock.patch.object(al, "_find_running_daemon", return_value=None), \
             mock.patch.object(al, "_pane_is_busy", return_value=False), \
             mock.patch.object(al, "_pane_has_prompt", return_value=True), \
             mock.patch.object(al, "_capture_pane", return_value="> "), \
             mock.patch.object(al, "_try_acquire_slot_for_send", return_value=True), \
             mock.patch.object(al, "send_prompt_to_session", return_value=True) as send, \
             mock.patch.object(al, "write_send_request") as queue:
            al.cmd_send(args, Path("/tmp"))

        ensure.assert_called_once()
        send.assert_called_once_with("%1", "hello")
        queue.assert_not_called()

    def test_own_request_completion_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al.write_send_response("other", "completed", base_dir=root)
            al.write_send_response("mine", "completed", base_dir=root)
            code = al._wait_for_send_completion(
                "mine", response_timeout=5, failure_pattern=None, base_dir=root)
        self.assertEqual(code, 0)

    def test_request_failure_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al.write_send_response("mine", "failed", base_dir=root)
            code = al._wait_for_send_completion(
                "mine", response_timeout=5, failure_pattern=None, base_dir=root)
        self.assertEqual(code, 1)

    def test_timeout_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(al.time, "sleep"), \
             mock.patch.object(al.time, "time", side_effect=[0, 0.1, 100]):
            code = al._wait_for_send_completion(
                "mine", response_timeout=1, failure_pattern=None, base_dir=Path(tmp))
        self.assertEqual(code, 2)

    def test_failure_pattern_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al.write_send_response("mine", "processing", pane_id="%1", base_dir=root)
            with mock.patch.object(al, "_capture_pane", return_value="ERROR: failed hard"):
                code = al._wait_for_send_completion(
                    "mine", response_timeout=5, failure_pattern=r"ERROR:", base_dir=root)
        self.assertEqual(code, 1)

    def test_completed_between_polls_does_not_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al.write_send_response("mine", "completed", pane_id="%1", base_dir=root)
            code = al._wait_for_send_completion(
                "mine", response_timeout=5, failure_pattern=None, base_dir=root)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
