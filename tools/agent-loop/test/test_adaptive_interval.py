#!/usr/bin/env python3
"""adaptive interval: opt-in / idle backoff / activity reset / cron skip。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class AdaptiveIntervalUnitTests(unittest.TestCase):
    def test_activity_resets_to_min(self):
        cfg = {"min_interval_seconds": 60, "max_interval_seconds": 1800,
               "backoff_factor": 2.0, "idle_threshold": 2, "jitter": 0}
        state = {"interval_seconds": 240, "idle_count": 5}
        interval, new = al.next_adaptive_interval(cfg, state, outcome="activity")
        self.assertEqual(interval, 60)
        self.assertEqual(new["idle_count"], 0)

    def test_idle_backoff_after_threshold(self):
        cfg = {"min_interval_seconds": 60, "max_interval_seconds": 1800,
               "backoff_factor": 2.0, "idle_threshold": 2, "jitter": 0}
        state = {"interval_seconds": 60, "idle_count": 0}
        interval, state = al.next_adaptive_interval(cfg, state, outcome="idle")
        self.assertEqual(interval, 60)
        self.assertEqual(state["idle_count"], 1)
        interval, state = al.next_adaptive_interval(cfg, state, outcome="idle")
        self.assertEqual(interval, 120)

    def test_error_short_retry_not_idle(self):
        cfg = {"min_interval_seconds": 60, "max_interval_seconds": 1800,
               "backoff_factor": 1.5, "idle_threshold": 2, "jitter": 0}
        state = {"interval_seconds": 60, "idle_count": 1}
        interval, new = al.next_adaptive_interval(cfg, state, outcome="error")
        self.assertEqual(interval, 90)
        self.assertEqual(new["idle_count"], 1)  # unchanged


class AdaptiveSchedulerTests(unittest.TestCase):
    def test_opt_in_only(self):
        entry = {"id": "e1", "name": "n", "interval_minutes": 5, "cron": None}
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        # adaptive 無し → interval_minutes
        nxt = s._next_run_at_for_entry(entry, outcome="idle")
        self.assertAlmostEqual(nxt, al.time.time() + 300, delta=2)

    def test_cron_skips_adaptive(self):
        entry = {
            "id": "e1", "name": "n", "cron": "0 * * * *",
            "adaptive": {"enabled": True, "min_interval_seconds": 60,
                         "max_interval_seconds": 120, "backoff_factor": 2,
                         "idle_threshold": 1, "jitter": 0},
        }
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        with mock.patch.object(al, "CronExpression") as CE:
            CE.return_value.next_run.return_value.timestamp.return_value = 9999.0
            nxt = s._next_run_at_for_entry(entry, outcome="idle")
        self.assertEqual(nxt, 9999.0)

    def test_adaptive_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {"min_interval_seconds": 10, "max_interval_seconds": 100,
                   "backoff_factor": 2, "idle_threshold": 1, "jitter": 0}
            interval, state = al.next_adaptive_interval(cfg, {}, outcome="idle")
            al.save_adaptive_state("e1", state, base_dir=root)
            loaded = al.load_adaptive_state("e1", base_dir=root)
            self.assertEqual(loaded.get("interval_seconds"), interval)


if __name__ == "__main__":
    unittest.main()


class HookOutcomeTests(unittest.TestCase):
    """壊れたフックを「無風」と取り違えないこと。

    `_call_hook_check` は「やることが無い」も「フックが壊れている」も None を返す。
    adaptive interval がこれを分けないと、毎回例外を投げるフックが無風とまったく同じ
    経路で max_interval_seconds まで後退し、症状が「静かだ」としか見えなくなる。
    """

    def _scheduler(self):
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        s._hook_cache = {}
        s._hook_cache_lock = al.threading.Lock()
        s._hook_quarantine = set()
        s._hook_check_errors = set()
        s._lock = al.threading.Lock()
        s._workspace = ""
        return s

    def _hook(self, tmp, body):
        path = Path(tmp) / "hook.py"
        path.write_text(body, encoding="utf-8")
        return path

    def test_quiet_hook_is_not_marked_as_error(self):
        s = self._scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._hook(tmp, "def check():\n    return None\n")
            entry = {"name": "n", "id": "1", "hooks": [str(path)]}
            self.assertIsNone(s._call_hook_check(entry, str(path)))
        self.assertEqual(s._hook_check_errors, set())

    def test_raising_hook_is_marked_as_error(self):
        s = self._scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._hook(tmp, "def check():\n    raise RuntimeError('boom')\n")
            entry = {"name": "n", "id": "1", "hooks": [str(path)]}
            self.assertIsNone(s._call_hook_check(entry, str(path)))
        self.assertEqual(len(s._hook_check_errors), 1)

    def test_missing_hook_file_is_marked_as_error(self):
        s = self._scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "nope.py")
            self.assertIsNone(s._call_hook_check({"name": "n", "id": "1"}, missing))
        self.assertEqual(len(s._hook_check_errors), 1)

    def test_contract_violation_is_marked_as_error(self):
        """prompt の無い dict は「無風」ではなく設定ミス。"""
        s = self._scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._hook(tmp, "def check():\n    return {'cwd': '/tmp'}\n")
            entry = {"name": "n", "id": "1", "hooks": [str(path)]}
            self.assertIsNone(s._call_hook_check(entry, str(path)))
        self.assertEqual(len(s._hook_check_errors), 1)

    def test_timeout_is_marked_as_error(self):
        s = self._scheduler()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._hook(tmp, "import time\ndef check():\n    time.sleep(60)\n")
            entry = {"name": "n", "id": "1", "hooks": [str(path)]}
            with mock.patch.object(al, "_HOOK_TIMEOUT_SEC", 0.05):
                self.assertIsNone(s._call_hook_check(entry, str(path)))
        self.assertEqual(len(s._hook_check_errors), 1)


class AdaptiveOutcomeFromScheduleTests(unittest.TestCase):
    """_fire_due_schedules が adaptive へ渡す outcome。"""

    ADAPTIVE = {"enabled": True, "min_interval_seconds": 60,
                "max_interval_seconds": 1800, "backoff_factor": 2.0,
                "idle_threshold": 1, "jitter": 0}

    def _scheduler(self, entry):
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        s._entries = [entry]
        s._lock = al.threading.Lock()
        s._hook_quarantine = set()
        s._hook_check_errors = set()
        s._hook_cache = {}
        s._hook_cache_lock = al.threading.Lock()
        s._workspace = ""
        s._update_entry = mock.Mock()
        s._accept_request = mock.Mock()
        return s

    def _outcome_for(self, check_returns=None, check_raises=False):
        entry = {"id": "e1", "name": "n", "prompt": "", "hooks": ["hook.py"],
                 "next_run_at": 0, "enabled": True, "scheduled": True,
                 "interval_minutes": 5, "adaptive": dict(self.ADAPTIVE)}
        s = self._scheduler(entry)

        def _check(_entry, hook):
            if check_raises:
                s._note_hook_check_error(hook)
                return None
            return check_returns

        s._call_hook_check = _check
        captured = {}

        def _next(e, *, outcome="idle"):
            captured["outcome"] = outcome
            return 0.0

        s._next_run_at_for_entry = _next
        s._fire_due_schedules(100.0)
        return captured, s

    def test_quiet_hook_backs_off_as_idle(self):
        captured, _ = self._outcome_for(check_returns=None)
        self.assertEqual(captured["outcome"], "idle")

    def test_broken_hook_uses_error_outcome(self):
        captured, _ = self._outcome_for(check_raises=True)
        self.assertEqual(captured["outcome"], "error")

    def test_working_hook_accepts_the_request(self):
        """仕事があるときは error にならない。

        受付時の advance は idle だが、これは保留中の二重発火を防ぐための暫定値で、
        配送が完了した時点で activity として上書きされる（`_try_dispatch` 側）。
        ここで確かめたいのは、仕事があるのに error 扱いされないことだけ。
        """
        captured, s = self._outcome_for(check_returns={"prompt": "やること"})
        s._accept_request.assert_called_once()
        self.assertEqual(captured["outcome"], "idle")

    def test_error_outcome_keeps_the_interval_near_the_floor(self):
        """error は idle と違って後退しない——壊れたフックを叩き続けて気づけるようにする。"""
        cfg = dict(self.ADAPTIVE)
        state = {"interval_seconds": 1600, "idle_count": 9}
        interval, new = al.next_adaptive_interval(cfg, state, outcome="error")
        self.assertEqual(interval, 120)          # min * factor であって current * factor ではない
        self.assertEqual(new["idle_count"], 9)   # idle の数え上げに混ぜない
        self.assertEqual(new["last_outcome"], "error")
