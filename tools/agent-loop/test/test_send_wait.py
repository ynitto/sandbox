#!/usr/bin/env python3
"""send --wait exit codes（pane classify をモック）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class _R:
    def __init__(self, code=0, stdout="%1"):
        self.returncode = code
        self.stdout = stdout


class SendWaitTests(unittest.TestCase):
    def test_ready_after_busy_exit_0(self):
        calls = {"n": 0}

        def is_busy(_):
            calls["n"] += 1
            return calls["n"] < 3

        def has_prompt(_):
            return calls["n"] >= 3

        with mock.patch.object(al, "_tmux_cmd", return_value=_R()), \
             mock.patch.object(al, "_pane_is_busy", side_effect=is_busy), \
             mock.patch.object(al, "_capture_pane", return_value="x"), \
             mock.patch.object(al, "_pane_has_prompt", side_effect=has_prompt), \
             mock.patch.object(al.time, "sleep"):
            code = al._wait_for_send_completion("%1", response_timeout=5, failure_pattern=None)
        self.assertEqual(code, 0)

    def test_pane_death_exit_1(self):
        with mock.patch.object(al, "_tmux_cmd", return_value=_R(code=1, stdout="")), \
             mock.patch.object(al.time, "sleep"):
            code = al._wait_for_send_completion("%1", response_timeout=5, failure_pattern=None)
        self.assertEqual(code, 1)

    def test_timeout_exit_2(self):
        with mock.patch.object(al, "_tmux_cmd", return_value=_R()), \
             mock.patch.object(al, "_pane_is_busy", return_value=True), \
             mock.patch.object(al, "_capture_pane", return_value="busy"), \
             mock.patch.object(al, "_pane_has_prompt", return_value=False), \
             mock.patch.object(al.time, "sleep"), \
             mock.patch.object(al.time, "time", side_effect=[0, 0.1, 100]):
            code = al._wait_for_send_completion("%1", response_timeout=1, failure_pattern=None)
        self.assertEqual(code, 2)

    def test_failure_pattern_exit_1(self):
        with mock.patch.object(al, "_tmux_cmd", return_value=_R()), \
             mock.patch.object(al, "_pane_is_busy", return_value=True), \
             mock.patch.object(al, "_capture_pane", return_value="ERROR: failed hard"), \
             mock.patch.object(al, "_pane_has_prompt", return_value=False), \
             mock.patch.object(al.time, "sleep"):
            code = al._wait_for_send_completion(
                "%1", response_timeout=5, failure_pattern=r"ERROR:")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
