import os
import signal
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class ProcessCleanupTests(unittest.TestCase):
    def _make_manager(self) -> al.SessionManager:
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {"prompt-1": "%42"}
        mgr._prompt_names = {"prompt-1": "test"}
        mgr._tmux_names = {}
        mgr._prompt_cwds = {}
        mgr._owners = {}
        mgr._instr_rev = {}
        mgr._lock = __import__("threading").Lock()
        mgr._split_direction = "horizontal"
        mgr._target_path = "/tmp"
        mgr._pane_exists = lambda pane: True  # type: ignore[method-assign]
        mgr._window_target_from_pane = lambda pane: "sess:0"  # type: ignore[method-assign]
        mgr.write_state = mock.Mock()  # type: ignore[method-assign]
        return mgr

    def test_enumerate_descendants_depth_order(self):
        ps_output = "  100   1\n  200 100\n  300 200\n"
        with mock.patch.object(al.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=ps_output)):
            ordered = al.SessionManager._enumerate_descendant_pids(100)
        self.assertEqual(ordered, [300, 200, 100])

    def test_kill_process_tree_sends_term_then_kill(self):
        mgr = self._make_manager()
        signals: list[tuple[int, signal.Signals]] = []

        def fake_signal(pid, sig):
            signals.append((pid, sig))

        with (
            mock.patch.object(mgr, "get_pane_pid", return_value=100),
            mock.patch.object(mgr, "_enumerate_descendant_pids", return_value=[200, 100]),
            mock.patch.object(mgr, "_signal_pids", side_effect=lambda pids, sig: [fake_signal(p, sig) for p in pids]),
            mock.patch.object(mgr, "_pid_is_alive", return_value=True),
            mock.patch.object(al.time, "sleep"),
            mock.patch.object(al, "_tmux_cmd"),
        ):
            mgr.kill_process_tree("%42", grace_seconds=0.01)

        self.assertEqual(signals[0], (200, signal.SIGTERM))
        self.assertEqual(signals[1], (100, signal.SIGTERM))
        self.assertEqual(signals[2], (200, signal.SIGKILL))
        self.assertEqual(signals[3], (100, signal.SIGKILL))

    def test_kill_process_tree_pid_fail_only_kill_pane(self):
        mgr = self._make_manager()
        with (
            mock.patch.object(mgr, "get_pane_pid", return_value=None),
            mock.patch.object(mgr, "_signal_pids") as signal_mock,
            mock.patch.object(al, "_tmux_cmd") as tmux_mock,
        ):
            mgr.kill_process_tree("%42")
        signal_mock.assert_not_called()
        tmux_mock.assert_called()

    def test_kill_process_tree_skips_unmanaged_pane(self):
        mgr = self._make_manager()
        mgr._panes = {}
        with mock.patch.object(mgr, "get_pane_pid") as pid_mock:
            mgr.kill_process_tree("%99")
        pid_mock.assert_not_called()

    def test_cleanup_managed_pane_removes_registration(self):
        mgr = self._make_manager()
        with mock.patch.object(mgr, "kill_process_tree") as kill_mock:
            ok = mgr.cleanup_managed_pane("prompt-1")
        self.assertTrue(ok)
        kill_mock.assert_called_once_with("%42", grace_seconds=2.0)
        self.assertNotIn("prompt-1", mgr._panes)
        mgr.write_state.assert_called_once()

    def test_capture_visible_input_tail(self):
        content = "line1\n\n  > \n"
        with mock.patch.object(al, "_capture_pane", return_value=content):
            tail = al.SessionManager.capture_visible_input_tail("%1", lines=2)
        self.assertEqual(tail, "line1\n  >")

    def test_cancel_releases_monitor_slot_and_managed_pane(self):
        session = self._make_manager()
        monitor = mock.Mock()
        semaphore = mock.Mock()
        scheduler = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        scheduler._entries = [{"id": "prompt-1", "name": "test"}]
        scheduler._lock = al.threading.Lock()
        scheduler._session_mgr = session
        scheduler._slot_monitor = monitor
        scheduler._semaphore = semaphore
        session.cleanup_managed_pane = mock.Mock(return_value=True)

        self.assertTrue(scheduler._cancel_target("prompt-1"))

        monitor.fail.assert_called_once_with("%42")
        semaphore.release.assert_not_called()
        session.cleanup_managed_pane.assert_called_once_with("prompt-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
