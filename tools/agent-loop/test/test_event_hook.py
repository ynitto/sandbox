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


def _scheduler(entries):
    scheduler = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
    scheduler._session_mgr = types.SimpleNamespace(
        ensure_session=mock.Mock(return_value=True),
        get_pane_id=mock.Mock(return_value="%1"),
        send_prompt=mock.Mock(return_value=True),
        restart_pane=mock.Mock(),
        sync_entries=mock.Mock(),
        set_state_extras=mock.Mock(),
        _lock=al.threading.Lock(),
        _prompt_cwds={},
        _panes={"p1": "%1"},
    )
    scheduler._semaphore = None
    scheduler._slot_monitor = None
    scheduler._entries = entries
    scheduler._external_queues = {}
    scheduler._lock = al.threading.Lock()
    scheduler._stop_event = mock.Mock()
    scheduler._stop_event.wait.side_effect = [False, True]
    scheduler._node_budget_warned_at = 0.0
    scheduler._pending = []
    scheduler._debouncer = mock.Mock()
    scheduler._debouncer.is_duplicate.return_value = False
    scheduler._draining = False
    scheduler._run_state = "run"
    scheduler._reload_entries = None
    scheduler._hook_quarantine = set()
    scheduler._active_count = 0
    scheduler._active_ids = set()
    scheduler._inflight_ack_paths = set()
    scheduler._health = {}
    scheduler._input_recovery = False
    scheduler._mem_ok_streak = 0
    scheduler._mem_paused = False
    scheduler._workspace = "/tmp"
    scheduler._preflight_cache = {}
    scheduler._hook_cache = {}
    scheduler._hook_cache_lock = al.threading.Lock()
    return scheduler


def _run_once(scheduler):
    with mock.patch.object(al, "_control_lifecycle", return_value="run"), \
         mock.patch.object(al, "_node_budget_state", return_value=None), \
         mock.patch.object(al, "_write_status"), \
         mock.patch.object(al, "load_local_pause", return_value=False), \
         mock.patch.object(al, "drain_loop_commands", return_value=[]), \
         mock.patch.object(al, "load_send_requests", return_value=[]), \
         mock.patch.object(al.time, "time", return_value=100):
        scheduler._run_loop()


class GitLabIssueHookTests(unittest.TestCase):
    def test_update_advances_only_after_ack(self):
        issues = [
            {"iid": 1, "updated_at": "2026-08-02T00:00:01Z"},
            {"iid": 2, "updated_at": "2026-08-02T00:00:02Z"},
        ]
        with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(hook, "STATE_FILE", pathlib.Path(tmp, "state.json")), \
             mock.patch.object(hook, "_get_issues", return_value=issues):
            first = hook.check()
            retried = hook.check()
            hook.ack()
            second = hook.check()
        self.assertIn('"iid": 2', first)
        self.assertIn('"iid": 2', retried)
        self.assertIn('"iid": 1', second)


class EventHookDispatchTests(unittest.TestCase):
    def test_successful_event_dispatch_preserves_fresh_context_and_acks(self):
        entry = {"id": "p1", "name": "issues", "prompt": "", "event_hook": "hook.py",
                 "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
                 "fresh_context": True, "fresh_context_interval_minutes": None,
                 "interval_minutes": 1, "scheduled": True}
        scheduler = _scheduler([entry])
        scheduler._call_hook_check = mock.Mock(return_value={"prompt": "prompt"})
        scheduler._call_hook_ack = mock.Mock()
        scheduler._dispatch_prompt = mock.Mock(return_value=True)
        scheduler._run_preflight = mock.Mock(return_value=True)
        _run_once(scheduler)
        dispatched = scheduler._dispatch_prompt.call_args.args[0]
        self.assertEqual(dispatched["prompt"], "prompt")
        self.assertTrue(dispatched["_should_clear"])
        scheduler._call_hook_ack.assert_called_once()

    def test_failed_event_dispatch_does_not_ack(self):
        entry = {"id": "p1", "name": "issues", "prompt": "", "event_hook": "hook.py",
                 "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
                 "fresh_context": False, "interval_minutes": 1, "scheduled": True}
        scheduler = _scheduler([entry])
        scheduler._call_hook_check = mock.Mock(return_value={"prompt": "prompt"})
        scheduler._call_hook_ack = mock.Mock()
        scheduler._dispatch_prompt = mock.Mock(return_value=False)
        scheduler._run_preflight = mock.Mock(return_value=True)
        _run_once(scheduler)
        scheduler._call_hook_ack.assert_not_called()

    def test_slot_rejection_does_not_ack(self):
        entry = {"id": "p1", "name": "issues", "prompt": "", "event_hook": "hook.py",
                 "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
                 "fresh_context": False, "interval_minutes": 1, "scheduled": True}
        scheduler = _scheduler([entry])
        scheduler._semaphore = object()
        scheduler._call_hook_check = mock.Mock(return_value={"prompt": "prompt"})
        scheduler._call_hook_ack = mock.Mock()
        scheduler._dispatch_prompt = mock.Mock(return_value=True)
        scheduler._try_acquire_slot = mock.Mock(return_value="defer")
        scheduler._run_preflight = mock.Mock(return_value=True)
        _run_once(scheduler)
        scheduler._dispatch_prompt.assert_not_called()
        scheduler._call_hook_ack.assert_not_called()
        # defer → pending に戻る
        self.assertEqual(len(scheduler._pending), 1)

    def test_same_name_entries_keep_their_own_event_prompt(self):
        entries = [
            {"id": "p1", "name": "issues", "prompt": "", "event_hook": "one.py",
             "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
             "fresh_context": False, "interval_minutes": 1, "scheduled": True},
            {"id": "p2", "name": "issues", "prompt": "", "event_hook": "two.py",
             "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
             "fresh_context": False, "interval_minutes": 1, "scheduled": True},
        ]
        scheduler = _scheduler(entries)
        scheduler._call_hook_check = mock.Mock(side_effect=lambda entry: {"prompt": entry["id"] + "-prompt"})
        scheduler._call_hook_ack = mock.Mock()
        scheduler._dispatch_prompt = mock.Mock(return_value=True)
        scheduler._run_preflight = mock.Mock(return_value=True)
        _run_once(scheduler)
        sent = [(call.args[0]["id"], call.args[0]["prompt"])
                for call in scheduler._dispatch_prompt.call_args_list]
        self.assertEqual(sent, [("p1", "p1-prompt"), ("p2", "p2-prompt")])

    def test_event_hook_without_ack_remains_supported(self):
        scheduler = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        scheduler._load_hook_module = mock.Mock(return_value=types.SimpleNamespace(check=lambda: "x"))
        scheduler._call_hook_ack({"event_hook": "hook.py", "name": "legacy"})

    def test_slot_or_send_failure_keeps_prompt_for_next_drain(self):
        entry = {"id": "p1", "name": "issues", "exclude_from_concurrency": False}
        session = types.SimpleNamespace(
            ensure_session=mock.Mock(return_value=True),
            get_pane_id=mock.Mock(return_value="%1"),
            send_prompt=mock.Mock(side_effect=[False, True]),
            restart_pane=mock.Mock(),
            _lock=al.threading.Lock(),
            _prompt_cwds={},
        )
        semaphore = types.SimpleNamespace(
            slot_timeout=60,
            max_concurrent=1,
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
        scheduler._pending = []
        scheduler._debouncer = al.RequestDebouncer(window_seconds=0)
        scheduler._draining = False
        scheduler._run_state = "run"
        scheduler._hook_quarantine = set()
        scheduler._active_count = 0
        scheduler._active_ids = set()
        scheduler._inflight_ack_paths = set()
        scheduler._health = {}
        scheduler._input_recovery = False
        scheduler._preflight_cache = {}
        scheduler._hook_cache = {}
        scheduler._hook_cache_lock = al.threading.Lock()
        scheduler._run_preflight = mock.Mock(return_value=True)

        self.assertTrue(scheduler._drain_external_one(entry))
        self.assertEqual(list(scheduler._external_queues["issues"]), ["prompt"])
        self.assertTrue(scheduler._drain_external_one(entry))
        self.assertEqual(list(scheduler._external_queues["issues"]), ["prompt"])
        self.assertTrue(scheduler._drain_external_one(entry))
        self.assertEqual(session.send_prompt.call_count, 2)
        self.assertEqual(list(scheduler._external_queues["issues"]), [])


if __name__ == "__main__":
    unittest.main()
