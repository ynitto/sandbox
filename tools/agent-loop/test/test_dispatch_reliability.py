#!/usr/bin/env python3
"""Phase 1 dispatch gate のbusy保留・inflight・完了追跡。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class _Session:
    def __init__(self):
        self.sent = []
        self._lock = al.threading.Lock()
        self._prompt_cwds = {}

    def ensure_session(self, *_args, **_kwargs):
        return True

    def get_pane_id(self, _prompt_id):
        return "%1"

    def send_prompt(self, prompt_id, prompt):
        self.sent.append((prompt_id, prompt))
        return True


class _Monitor:
    def __init__(self):
        self.on_complete = None
        self.on_failure = None
        self.tracking = False

    def track(self, _pane_id, on_complete=None, on_failure=None, **_kwargs):
        self.on_complete = on_complete
        self.on_failure = on_failure
        self.tracking = True

    def is_tracking(self, _pane_id):
        return self.tracking


def _scheduler():
    session = _Session()
    monitor = _Monitor()
    scheduler = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
    scheduler._session_mgr = session
    scheduler._slot_monitor = monitor
    scheduler._semaphore = None
    scheduler._lock = al.threading.Lock()
    scheduler._pending = []
    scheduler._draining = False
    scheduler._active_count = 0
    scheduler._active_ids = set()
    scheduler._inflight_ack_paths = set()
    scheduler._stop_event = al.threading.Event()
    scheduler._input_recovery = False
    scheduler._entries = [{
        "id": "e1", "name": "entry", "prompt": "hello", "slash": [],
        "fresh_context": False, "exclude_from_concurrency": False,
    }]
    return scheduler, session, monitor


class DispatchReliabilityTests(unittest.TestCase):
    def test_busy_pane_is_deferred_without_send(self):
        scheduler, session, _ = _scheduler()
        req = al.make_dispatch_request(source="schedule", entry_id="e1", prompt="hello")
        with mock.patch.object(scheduler, "_run_preflight", return_value=True), \
             mock.patch.object(al, "_capture_pane", return_value="working"):
            result = scheduler._try_dispatch_request(req)
        self.assertEqual(result, "defer")
        self.assertEqual(session.sent, [])

    def test_active_request_lives_until_monitor_completion(self):
        scheduler, _, monitor = _scheduler()
        req = al.make_dispatch_request(source="schedule", entry_id="e1", prompt="hello")
        with mock.patch.object(scheduler, "_run_preflight", return_value=True), \
             mock.patch.object(al, "_capture_pane", return_value="> "):
            self.assertEqual(scheduler._try_dispatch_request(req), "done")
        self.assertEqual(scheduler.active_count(), 1)
        self.assertIsNotNone(monitor.on_complete)
        monitor.on_complete()
        self.assertEqual(scheduler.active_count(), 0)

    def test_second_request_waits_even_if_pane_still_looks_ready(self):
        scheduler, session, _ = _scheduler()
        first = al.make_dispatch_request(source="schedule", entry_id="e1", prompt="one")
        second = al.make_dispatch_request(source="schedule", entry_id="e1", prompt="two")
        with mock.patch.object(scheduler, "_run_preflight", return_value=True), \
             mock.patch.object(al, "_capture_pane", return_value="> "):
            self.assertEqual(scheduler._try_dispatch_request(first), "done")
            self.assertEqual(scheduler._try_dispatch_request(second), "defer")
        self.assertEqual(session.sent, [("e1", "one")])

    def test_inbox_path_is_inflight_while_dispatching(self):
        scheduler, _, _ = _scheduler()
        scheduler._run_state = "run"
        req = al.make_dispatch_request(
            source="inbox", entry_id="inbox-1", prompt="hello",
            ack={"kind": "inbox_file", "path": "/tmp/m1.json", "processed_dir": "/tmp/done"},
            meta={"prompt_id": "inbox-1", "session_name": "inbox"},
        )
        scheduler._pending = [req]

        def dispatch(current):
            self.assertTrue(scheduler.has_pending_ack_path("/tmp/m1.json"))
            return "defer"

        with mock.patch.object(scheduler, "_try_dispatch_request", side_effect=dispatch):
            scheduler._process_pending()

        self.assertTrue(scheduler.has_pending_ack_path("/tmp/m1.json"))

    def test_input_recovery_does_not_resend_after_busy_seen(self):
        scheduler, session, _ = _scheduler()
        req = al.make_dispatch_request(source="send", entry_id="e1", prompt="hello")
        with mock.patch.object(al, "_capture_pane", side_effect=["> ", "working"]), \
             mock.patch.object(al.time, "sleep"):
            self.assertTrue(scheduler._maybe_input_recovery(
                "%1", "e1", "hello", req))
        self.assertEqual(session.sent, [])


if __name__ == "__main__":
    unittest.main()
