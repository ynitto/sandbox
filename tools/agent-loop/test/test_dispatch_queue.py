#!/usr/bin/env python3
"""dispatch queue: priority / FIFO / debounce / schedule coalesce / send-request accept。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class DispatchQueueTests(unittest.TestCase):
    def _sched(self):
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        s._lock = al.threading.Lock()
        s._pending = []
        s._debouncer = al.RequestDebouncer(window_seconds=3.0)
        s._draining = False
        s._workspace = "/workspace/a"
        return s

    def test_priority_high_before_normal(self):
        s = self._sched()
        r1 = al.make_dispatch_request(source="send", entry_id="e", prompt="a", priority="normal")
        r2 = al.make_dispatch_request(source="send", entry_id="e", prompt="b", priority="high")
        s._accept_request(r1)
        s._accept_request(r2)
        self.assertEqual([r["prompt"] for r in s._pending], ["b", "a"])

    def test_fifo_within_same_priority(self):
        s = self._sched()
        for p in ("1", "2", "3"):
            s._accept_request(al.make_dispatch_request(
                source="send", entry_id="e", prompt=p, priority="normal"))
        self.assertEqual([r["prompt"] for r in s._pending], ["1", "2", "3"])

    def test_debounce_same_entry_prompt(self):
        s = self._sched()
        r1 = al.make_dispatch_request(source="send", entry_id="e", prompt="same")
        r2 = al.make_dispatch_request(source="send", entry_id="e", prompt="same")
        self.assertTrue(s._accept_request(r1))
        self.assertTrue(s._accept_request(r2))  # success-discard
        self.assertEqual(len(s._pending), 1)

    def test_schedule_coalesce_max_one_per_entry(self):
        s = self._sched()
        s._debouncer = mock.Mock()
        s._debouncer.is_duplicate.return_value = False
        a = al.make_dispatch_request(source="schedule", entry_id="e1", prompt="p1")
        b = al.make_dispatch_request(source="schedule", entry_id="e1", prompt="p2")
        c = al.make_dispatch_request(source="schedule", entry_id="e2", prompt="p3")
        s._accept_request(a)
        s._accept_request(b)
        s._accept_request(c)
        self.assertEqual(len(s._pending), 2)
        self.assertEqual({r["entry_id"] for r in s._pending}, {"e1", "e2"})

    def test_send_request_accepted_deletes_file(self):
        s = self._sched()
        s._debouncer = mock.Mock()
        s._debouncer.is_duplicate.return_value = False
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            req = al.write_send_request(
                entry_id="e", prompt="hello", base_dir=root)
            path = Path(req["meta"].get("_path") or "")
            # reload with path meta
            loaded = al.load_send_requests(base_dir=root)
            self.assertEqual(len(loaded), 1)
            self.assertTrue(Path(loaded[0]["meta"]["_path"]).is_file())
            # simulate crash between write and accept: file still there
            self.assertTrue(list(root.glob("*.json")))
            # accept into memory + delete (daemon path)
            ok = s._accept_request(loaded[0])
            self.assertTrue(ok)
            al.remove_send_request_file(loaded[0])
            self.assertEqual(list(root.glob("*.json")), [])
            self.assertEqual(len(s._pending), 1)

    def test_send_request_claim_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al.write_send_request(entry_id="e", prompt="hello", base_dir=root)
            first = al.load_send_requests(base_dir=root)[0]
            second = al.load_send_requests(base_dir=root)[0]

            self.assertTrue(al.claim_send_request(first, "daemon-1"))
            self.assertFalse(al.claim_send_request(second, "daemon-2"))

    def test_other_workspace_request_is_not_consumed(self):
        s = self._sched()
        req = al.make_dispatch_request(
            source="send",
            entry_id="e",
            prompt="hello",
            meta={"workspace": "/workspace/b", "_path": "/tmp/request.json"},
        )
        with mock.patch.object(al, "load_send_requests", return_value=[req]), \
             mock.patch.object(al, "claim_send_request") as claim, \
             mock.patch.object(al, "remove_send_request_file") as remove:
            s._drain_send_requests()

        claim.assert_not_called()
        remove.assert_not_called()
        self.assertEqual(s._pending, [])


if __name__ == "__main__":
    unittest.main()
