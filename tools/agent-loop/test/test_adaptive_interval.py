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
