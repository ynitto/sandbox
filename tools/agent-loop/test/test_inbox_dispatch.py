"""InboxWatcherのdispatch gate委譲とSlotMonitor完了通知。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class _FakeSemaphore:
    def __init__(self):
        self.released = []

    def release(self, pane_id):
        self.released.append(pane_id)


class InboxDispatchTests(unittest.TestCase):
    def test_message_is_enqueued_through_scheduler(self):
        scheduler = mock.Mock()
        scheduler.enqueue_request.return_value = True
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(al, "_AGENTS_DIR", Path(tmp)):
            watcher = al.InboxWatcher("agent-a", mock.Mock(), scheduler)
            path = Path(tmp) / "message.json"
            self.assertTrue(watcher._enqueue_message(
                {"id": "m1", "from": "agent-b", "body": "hi"}, path))

        req = scheduler.enqueue_request.call_args.args[0]
        self.assertEqual(req["source"], "inbox")
        self.assertEqual(req["ack"]["path"], str(path))

    def test_start_timeout_reports_failure(self):
        semaphore = _FakeSemaphore()
        monitor = al.SlotMonitor(semaphore)
        on_complete = mock.Mock()
        on_failure = mock.Mock()
        monitor.track("%1", on_complete=on_complete, on_failure=on_failure)
        monitor._pending["%1"]["acquired_at"] = 0

        with mock.patch.object(al.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(al, "_capture_pane", return_value="> "), \
             mock.patch.object(al.time, "time", return_value=61):
            monitor._check_pane("%1")

        on_complete.assert_not_called()
        on_failure.assert_called_once_with()
        self.assertEqual(semaphore.released, ["%1"])

    def test_processing_timeout_keeps_monitor_until_completion(self):
        semaphore = _FakeSemaphore()
        monitor = al.SlotMonitor(semaphore, slot_timeout_seconds=10)
        on_complete = mock.Mock()
        monitor.track("%1", on_complete=on_complete)
        monitor._pending["%1"].update(state="processing", acquired_at=0)

        with mock.patch.object(al.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(al, "_capture_pane", return_value="working"), \
             mock.patch.object(al.time, "time", return_value=11):
            monitor._check_pane("%1")
        on_complete.assert_not_called()
        self.assertIn("%1", monitor._pending)

        with mock.patch.object(al.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(al, "_capture_pane", return_value="> "):
            monitor._check_pane("%1")
        on_complete.assert_called_once_with()
        self.assertNotIn("%1", monitor._pending)

    def test_fast_completion_detected_from_content_change(self):
        semaphore = _FakeSemaphore()
        monitor = al.SlotMonitor(semaphore)
        on_complete = mock.Mock()
        before = al.hashlib.sha256(b"> ").hexdigest()
        monitor.track("%1", on_complete=on_complete, initial_content_hash=before)

        with mock.patch.object(al.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(al, "_capture_pane", return_value="done\n> "):
            monitor._check_pane("%1")

        on_complete.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
