from __future__ import annotations

import unittest

from _shared import AuditTestCase, store


class StoreTests(AuditTestCase):
    def test_record_id_idempotent(self):
        a = store.record_id("s", "db", "sid-1")
        b = store.record_id("s", "db", "sid-1")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("aud-"))
        self.assertNotEqual(a, store.record_id("s", "db", "sid-2"))

    def test_append_record_dedup_and_survives_gc(self):
        st = self.make_store()
        rec = {"id": "aud-x", "_epoch": 1754200000.0, "ts": "2026-08-03T06:00:00Z",
               "kind": "ledger"}
        self.assertTrue(st.append_record(dict(rec)))
        self.assertFalse(st.append_record(dict(rec)))     # 収集済みは追記しない
        st.save_state()
        # records を消しても seen は残る＝再収集で LLM 段へ再投入されない（仕様書 §7）
        st2 = self.make_store()
        self.assertTrue(st2.has_record("aud-x"))

    def test_iter_records_reads_back(self):
        st = self.make_store()
        st.append_record({"id": "aud-1", "_epoch": 1754200000.0,
                          "ts": "2026-08-03T06:00:00Z", "kind": "run"})
        got = list(st.iter_records())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["id"], "aud-1")
        self.assertNotIn("_epoch", got[0])                # 内部キーは書かない

    def test_home_relative(self):
        import os
        home = os.path.expanduser("~")
        self.assertEqual(store.home_relative(os.path.join(home, "repo")), "~/repo")
        self.assertEqual(store.home_relative("/opt/x"), "/opt/x")


if __name__ == "__main__":
    unittest.main()
