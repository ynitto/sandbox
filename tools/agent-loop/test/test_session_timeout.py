import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class SessionTimeoutTests(unittest.TestCase):
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
