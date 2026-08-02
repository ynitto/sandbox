#!/usr/bin/env python3
"""event hook の同時更新と配送保留の回帰テスト。"""
import collections
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import agent_loop as al  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load("gitlab_issue_hook_test", HERE.parent / "hooks" / "gitlab-issue-hook.py")


class GitLabIssueHookTests(unittest.TestCase):
    def test_simultaneous_updates_are_returned_over_successive_checks(self):
        issues = [
            {"iid": 1, "updated_at": "2026-08-02T00:00:01Z"},
            {"iid": 2, "updated_at": "2026-08-02T00:00:02Z"},
        ]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(hook, "STATE_FILE", pathlib.Path(tmp, "state.json")), \
             mock.patch.object(hook, "_get_issues", return_value=issues):
            first = hook.check()
            second = hook.check()
        self.assertIn('"iid": 2', first)
        self.assertIn('"iid": 1', second)


class EventQueueTests(unittest.TestCase):
    def test_event_hook_result_enters_retryable_queue(self):
        entry = {"id": "p1", "name": "issues", "prompt": "", "event_hook": "hook.py",
                 "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
                 "fresh_context": False}
        scheduler = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        scheduler._session_mgr = types.SimpleNamespace(ensure_session=mock.Mock(return_value=False))
        scheduler._semaphore = None
        scheduler._slot_monitor = None
        scheduler._entries = [entry]
        scheduler._external_queues = {}
        scheduler._lock = al.threading.Lock()
        scheduler._stop_event = mock.Mock()
        scheduler._stop_event.wait.side_effect = [False, True]
        scheduler._node_budget_warned_at = 0.0
        scheduler._call_hook_check = mock.Mock(return_value="prompt")
        scheduler.enqueue_external = mock.Mock(return_value=True)
        scheduler._drain_external_one = mock.Mock(side_effect=[False, True])
        with mock.patch.object(al, "_control_lifecycle", return_value="run"), \
             mock.patch.object(al, "_node_budget_state", return_value=None), \
             mock.patch.object(al, "_write_status"), \
             mock.patch.object(al.time, "time", return_value=100):
            scheduler._run_loop()
        scheduler.enqueue_external.assert_called_once_with("issues", "prompt")
        self.assertEqual(scheduler._drain_external_one.call_count, 2)

    def test_slot_or_send_failure_keeps_prompt_for_next_drain(self):
        entry = {"id": "p1", "name": "issues", "exclude_from_concurrency": False}
        session = types.SimpleNamespace(
            ensure_session=mock.Mock(return_value=True),
            get_pane_id=mock.Mock(return_value="%1"),
            send_prompt=mock.Mock(side_effect=[False, True]),
            restart_pane=mock.Mock(),
        )
        semaphore = types.SimpleNamespace(
            slot_timeout=60,
            slot_elapsed=mock.Mock(return_value=None),
            cooldown_remaining=mock.Mock(return_value=0),
            acquire=mock.Mock(side_effect=[False, True, True]),
            release=mock.Mock(),
        )
        scheduler = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        scheduler._session_mgr = session
        scheduler._semaphore = semaphore
        scheduler._slot_monitor = None
        scheduler._entries = [entry]
        scheduler._external_queues = {"issues": collections.deque(["prompt"])}
        scheduler._lock = al.threading.Lock()
        scheduler._stop_event = al.threading.Event()

        self.assertTrue(scheduler._drain_external_one(entry))
        self.assertEqual(list(scheduler._external_queues["issues"]), ["prompt"])
        self.assertTrue(scheduler._drain_external_one(entry))
        self.assertEqual(list(scheduler._external_queues["issues"]), ["prompt"])
        self.assertTrue(scheduler._drain_external_one(entry))
        self.assertEqual(session.send_prompt.call_count, 2)
        self.assertEqual(list(scheduler._external_queues["issues"]), [])


if __name__ == "__main__":
    unittest.main()
