#!/usr/bin/env python3
"""preflight: false blocks; timeout/exception fail-open。"""
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class PreflightTests(unittest.TestCase):
    def _sched(self):
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        s._preflight_cache = {}
        return s

    def test_false_blocks(self):
        s = self._sched()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pf.py"
            path.write_text(textwrap.dedent('''
                def should_dispatch(request, cfg):
                    return False
            '''), encoding="utf-8")
            entry = {"id": "e", "preflight": str(path)}
            req = al.make_dispatch_request(source="schedule", entry_id="e", prompt="p")
            self.assertFalse(s._run_preflight(req, entry))

    def test_exception_fail_open(self):
        s = self._sched()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pf.py"
            path.write_text(textwrap.dedent('''
                def should_dispatch(request, cfg):
                    raise RuntimeError("boom")
            '''), encoding="utf-8")
            entry = {"id": "e", "preflight": str(path)}
            req = al.make_dispatch_request(source="schedule", entry_id="e", prompt="p")
            self.assertTrue(s._run_preflight(req, entry))

    def test_timeout_fail_open(self):
        s = self._sched()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pf.py"
            path.write_text(textwrap.dedent('''
                import time
                def should_dispatch(request, cfg):
                    time.sleep(60)
                    return False
            '''), encoding="utf-8")
            entry = {"id": "e", "preflight": str(path)}
            req = al.make_dispatch_request(source="schedule", entry_id="e", prompt="p")
            with mock.patch.object(al, "_PREFLIGHT_TIMEOUT_SEC", 0.05):
                self.assertTrue(s._run_preflight(req, entry))


if __name__ == "__main__":
    unittest.main()
