#!/usr/bin/env python3
"""event hook hardening: arity / 戻り値 / cwd / timeout quarantine / ack。"""
import os
import sys
import tempfile
import textwrap
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


def _base_scheduler():
    s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
    s._hook_cache = {}
    s._hook_cache_lock = al.threading.Lock()
    s._hook_quarantine = set()
    s._lock = al.threading.Lock()
    s._workspace = ""
    return s


class HookHardeningTests(unittest.TestCase):
    def _write_hook(self, tmp: str, body: str) -> Path:
        path = Path(tmp) / "hook.py"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_zero_arg_check_str(self):
        s = _base_scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_hook(tmp, '''
                def check():
                    return "hello"
            ''')
            entry = {"name": "n", "id": "1", "event_hook": str(path)}
            result = s._call_hook_check(entry)
        self.assertEqual(result, {"prompt": "hello"})

    def test_one_arg_check_dict(self):
        s = _base_scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_hook(tmp, '''
                def check(cfg):
                    return {"prompt": "Issue {n}", "vars": {"n": cfg["name"]}}
            ''')
            entry = {"name": "n", "id": "1", "event_hook": str(path)}
            result = s._call_hook_check(entry)
        self.assertEqual(result["prompt"], "Issue n")

    def test_none_skips(self):
        s = _base_scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_hook(tmp, '''
                def check():
                    return None
            ''')
            entry = {"name": "n", "id": "1", "event_hook": str(path)}
            self.assertIsNone(s._call_hook_check(entry))

    def test_cwd_reject_missing(self):
        s = _base_scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_hook(tmp, '''
                def check():
                    return {"prompt": "x", "cwd": "/no/such/dir/agent-loop-test"}
            ''')
            entry = {"name": "n", "id": "1", "event_hook": str(path)}
            self.assertIsNone(s._call_hook_check(entry))

    def test_timeout_quarantine(self):
        s = _base_scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_hook(tmp, '''
                import time
                def check():
                    time.sleep(60)
                    return "late"
            ''')
            entry = {"name": "n", "id": "1", "event_hook": str(path)}
            with mock.patch.object(al, "_HOOK_TIMEOUT_SEC", 0.05):
                self.assertIsNone(s._call_hook_check(entry))
            self.assertIn((str(path.resolve()), "1"), s._hook_quarantine)
            # 隔離中は再実行しない
            self.assertIsNone(s._call_hook_check(entry))

    def test_shared_hook_file_has_entry_local_runtime(self):
        s = _base_scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_hook(tmp, '''
                count = 0
                def check(_cfg):
                    global count
                    count += 1
                    return str(count)
            ''')
            a = {"name": "a", "id": "a", "event_hook": str(path)}
            b = {"name": "b", "id": "b", "event_hook": str(path)}
            self.assertEqual(s._call_hook_check(a), {"prompt": "1"})
            self.assertEqual(s._call_hook_check(b), {"prompt": "1"})
            self.assertEqual(s._call_hook_check(a), {"prompt": "2"})

    def test_timeout_does_not_stop_other_entries(self):
        s = _base_scheduler()
        s._pending = []
        s._draining = False
        s._debouncer = al.RequestDebouncer()
        s._update_entry = mock.Mock()
        s._next_run_at_for_entry = mock.Mock(return_value=999)
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_hook(tmp, '''
                import time
                def check():
                    time.sleep(1)
                    return "late"
            ''')
            s._entries = [
                {"id": "slow", "name": "slow", "prompt": "", "event_hook": str(path),
                 "next_run_at": 0, "scheduled": True, "enabled": True},
                {"id": "normal", "name": "normal", "prompt": "ok", "event_hook": None,
                 "next_run_at": 0, "scheduled": True, "enabled": True},
            ]
            s._workspace = ""
            with mock.patch.object(al, "_HOOK_TIMEOUT_SEC", 0.01):
                s._fire_due_schedules(1)

        self.assertEqual([req["entry_id"] for req in s._pending], ["normal"])

    def test_ack_only_on_success(self):
        s = _base_scheduler()
        s._session_mgr = types.SimpleNamespace(
            ensure_session=mock.Mock(return_value=True),
            get_pane_id=mock.Mock(return_value="%1"),
            _lock=al.threading.Lock(),
            _prompt_cwds={},
        )
        s._semaphore = None
        s._slot_monitor = None
        s._active_count = 0
        s._active_ids = set()
        s._inflight_ack_paths = set()
        s._input_recovery = False
        s._stop_event = al.threading.Event()
        s._preflight_cache = {}
        s._run_preflight = mock.Mock(return_value=True)
        s._call_hook_ack = mock.Mock()
        entry = {"id": "p1", "name": "n", "event_hook": "h.py",
                 "exclude_from_concurrency": False, "slash": []}
        s._entries = [entry]
        s._find_entry = mock.Mock(return_value=entry)

        req = al.make_dispatch_request(source="hook", entry_id="p1", prompt="x")
        s._dispatch_prompt = mock.Mock(return_value=False)
        with mock.patch.object(al, "_capture_pane", return_value="> "):
            self.assertEqual(s._try_dispatch_request(req), "defer")
        s._call_hook_ack.assert_not_called()

        s._dispatch_prompt = mock.Mock(return_value=True)
        with mock.patch.object(al, "_capture_pane", return_value="> "):
            self.assertEqual(s._try_dispatch_request(req), "done")
        s._call_hook_ack.assert_called_once()


if __name__ == "__main__":
    unittest.main()
