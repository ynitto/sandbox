from __future__ import annotations

import os
import time
import unittest

from _shared import AuditTestCase, gccmd, scrub


class GcTests(AuditTestCase):
    def _write_dated(self, dir_path, day, name=None):
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, name or f"{day}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}\n")
        return path

    def test_keep_days_per_type_and_insights_untouched(self):
        st = self.make_store()
        old = self._write_dated(st.records_dir, "20200101")
        new = self._write_dated(st.records_dir, time.strftime("%Y%m%d", time.gmtime()))
        os.makedirs(st.insights_dir, exist_ok=True)
        ins = os.path.join(st.insights_dir, "ins-x.json")
        with open(ins, "w", encoding="utf-8") as f:
            f.write("{}")
        args = self.make_args(gc_keep_days={"records": 30, "transcripts": 0,
                                            "observations": 30, "reports": 30})
        result = gccmd.run_gc(args, st)
        self.assertEqual(result["records"], 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertTrue(os.path.exists(ins))          # insights は gc 対象外

    def test_zero_means_keep_forever(self):
        st = self.make_store()
        old = self._write_dated(st.records_dir, "20200101")
        args = self.make_args(gc_keep_days={"records": 0})
        result = gccmd.run_gc(args, st)
        self.assertEqual(result["records"], 0)
        self.assertTrue(os.path.exists(old))

    def test_dry_run_deletes_nothing(self):
        st = self.make_store()
        old = self._write_dated(st.records_dir, "20200101")
        result = gccmd.run_gc(self.make_args(), st, dry_run=True)
        self.assertEqual(result["records"], 1)
        self.assertTrue(os.path.exists(old))

    def test_auto_gc_respects_interval(self):
        st = self.make_store()
        self._write_dated(st.records_dir, "20200101")
        args = self.make_args(gc_interval_hours=24.0)
        st.note_run("gc")                              # たった今 gc した
        gccmd.auto_gc(args, st)
        self.assertTrue(os.path.exists(os.path.join(st.records_dir, "20200101.jsonl")))
        st.state["last"]["gc"] = 0.0                   # 十分昔
        gccmd.auto_gc(args, st)
        self.assertFalse(os.path.exists(os.path.join(st.records_dir, "20200101.jsonl")))


class ScrubTests(unittest.TestCase):
    def test_secret_patterns_redacted(self):
        s = scrub.scrub_text("token=ghp-abcdefghijklmnop and Bearer: xyz password: hunter2")
        self.assertNotIn("hunter2", s)
        self.assertIn("[REDACTED]", s)

    def test_home_path_relativized(self):
        home = os.path.expanduser("~")
        self.assertEqual(scrub.scrub_text(f"see {home}/repo/file.py"), "see ~/repo/file.py")


if __name__ == "__main__":
    unittest.main()
