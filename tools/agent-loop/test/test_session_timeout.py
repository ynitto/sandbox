import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class SessionTimeoutTests(unittest.TestCase):
    def test_hold_slot_processing_timeout_is_terminal_failure(self):
        semaphore = mock.Mock()
        monitor = al.SlotMonitor(semaphore, slot_timeout_seconds=1)
        failed = mock.Mock()
        profile = mock.Mock()
        profile.is_idle.return_value = False
        monitor.track("%1", on_failure=failed, hold_slot=True, profile=profile)
        monitor._pending["%1"]["state"] = "processing"
        monitor._pending["%1"]["acquired_at"] = 0.0
        with mock.patch.object(al.subprocess, "run", return_value=mock.Mock(returncode=0)), \
             mock.patch.object(al, "_capture_pane", return_value="busy"), \
             mock.patch.object(al.time, "time", return_value=10.0):
            monitor._check_pane("%1")
        failed.assert_called_once_with()
        self.assertFalse(monitor.is_tracking("%1"))

    def test_chat_startup_wait_uses_configured_timeout(self):
        manager = al.SessionManager.__new__(al.SessionManager)
        manager._startup_timeout = 1
        now = [0.0]
        captures = []

        with (
            mock.patch.object(al.time, "time", side_effect=lambda: now[0]),
            mock.patch.object(al.time, "sleep", side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)),
            mock.patch.object(al, "_capture_pane", side_effect=lambda pane: captures.append(pane) or ""),
        ):
            manager._send_session_chat_commands("%1", "/work")

        self.assertEqual(captures, ["%1", "%1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
