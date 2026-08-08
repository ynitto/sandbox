#!/usr/bin/env python3
"""transactional reload: invalid keeps old; same id inherits next_run_at。"""
import os
import sys
import types
import unittest
import collections
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class TransactionalReloadTests(unittest.TestCase):
    def _sched(self, entries):
        session = types.SimpleNamespace(
            sync_entries=mock.Mock(),
            _target_path="/tmp",
        )
        s = al.PeriodicScheduler(session, entries, semaphore=None, slot_monitor=None, workspace="/tmp")
        return s

    def test_invalid_keeps_old(self):
        s = self._sched([
            {"name": "a", "prompt": "p", "interval_minutes": 10, "id": "id-a", "enabled": True},
        ])
        old_ids = [e["id"] for e in s._entries]
        old_next = s._entries[0]["next_run_at"]
        ok = s.set_entries("not-a-list")  # type: ignore[arg-type]
        self.assertFalse(ok)
        self.assertEqual([e["id"] for e in s._entries], old_ids)
        self.assertEqual(s._entries[0]["next_run_at"], old_next)

    def test_same_id_inherits_next_run_at(self):
        s = self._sched([
            {"name": "a", "prompt": "p", "interval_minutes": 10, "id": "id-a", "enabled": True},
        ])
        s._update_entry("id-a", next_run_at=12345.0)
        ok = s.set_entries([
            {"name": "a", "prompt": "p2", "interval_minutes": 30, "id": "id-a", "enabled": True},
        ])
        self.assertTrue(ok)
        self.assertEqual(s._entries[0]["prompt"], "p2")
        self.assertEqual(s._entries[0]["next_run_at"], 12345.0)

    def test_validate_entries_pure(self):
        out = al.validate_entries([
            {"name": "a", "prompt": "p", "interval_minutes": 5, "enabled": True},
            {"name": "bad", "prompt": "", "enabled": True},  # skipped
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "a")

    def test_request_reload_applies_next_tick_inherit(self):
        s = self._sched([
            {"name": "a", "prompt": "p", "interval_minutes": 10, "id": "id-a", "enabled": True},
        ])
        s._update_entry("id-a", next_run_at=42.0)
        ok = s.request_reload([
            {"name": "a", "prompt": "new", "interval_minutes": 10, "id": "id-a", "enabled": True},
        ])
        self.assertTrue(ok)
        self.assertIsNotNone(s._reload_entries)
        # apply
        s._apply_normalized_entries(s._reload_entries)
        s._reload_entries = None
        self.assertEqual(s._entries[0]["prompt"], "new")
        self.assertEqual(s._entries[0]["next_run_at"], 42.0)

    def test_same_id_keeps_webhook_queue_when_name_changes(self):
        s = self._sched([
            {"name": "old", "prompt": "p", "interval_minutes": 10,
             "id": "id-a", "enabled": True, "webhook": {}},
        ])
        s._external_queues["old"] = collections.deque(["event"])

        self.assertTrue(s.set_entries([
            {"name": "new", "prompt": "p", "interval_minutes": 10,
             "id": "id-a", "enabled": True, "webhook": {}},
        ]))

        self.assertEqual(list(s._external_queues["new"]), ["event"])
        self.assertNotIn("old", s._external_queues)

    def test_reload_applies_phase2_runtime_config_with_entries(self):
        s = self._sched([
            {"name": "a", "prompt": "p", "interval_minutes": 10, "id": "id-a", "enabled": True},
        ])
        self.assertTrue(s.request_reload(
            [{"name": "a", "prompt": "new", "interval_minutes": 10, "id": "id-a", "enabled": True}],
            external_panes=[{"name": "reviewer", "tmux_target": "rev:0.1"}],
            environment_handoff={"prompt": True, "token_env_names": ["TOKEN"]},
        ))
        s._apply_pending_reload()
        self.assertEqual(s._entries[0]["prompt"], "new")
        self.assertIn("reviewer", s._external_panes)
        self.assertTrue(s._environment_handoff["prompt"])

    def test_invalid_external_pane_rejects_whole_reload(self):
        s = self._sched([
            {"name": "a", "prompt": "old", "interval_minutes": 10, "id": "id-a", "enabled": True},
        ])
        self.assertFalse(s.request_reload(
            [{"name": "a", "prompt": "new", "interval_minutes": 10, "id": "id-a", "enabled": True}],
            external_panes=[{"name": "broken"}],
        ))
        self.assertIsNone(s._reload_entries)
        self.assertEqual(s._entries[0]["prompt"], "old")


if __name__ == "__main__":
    unittest.main()
