# claim の消化順（計画 2026-08-22 案 3 — 同役割の直列バッチ化）。
#
# 縛るのは 2 つ。**同じ種別を先に見る**ことと、**それ以外は何も変わらない**こと
# （元の順は random.shuffle ＝任意で、任意さを保ったままキャッシュに寄せるだけ）。
# 実測の根拠は eval/prefix_cache_probe.py（prefill 合計で 3.0 倍差）。
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(__file__))
from _shared import *  # noqa: E402,F401,F403

import shutil
import tempfile
import unittest


class ClaimOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-order-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")
        nodes = {f"n{i}": {"goal": "g", "deps": [], "kind": kind}
                 for i, kind in enumerate(("work", "judge", "work", "judge"), 1)}
        self.bus.write_graph({"nodes": nodes, "iteration": 0})
        for nid, node in nodes.items():
            self.bus.write_task({"id": nid, **node})

    def _kinds_of(self, prefer, tries=12):
        return {kf.pick_claimable(self.bus, prefer)[1].get("kind") for _ in range(tries)}

    def test_prefers_the_kind_just_executed(self):
        # 何度引いても直前と同じ種別だけが返る（接頭辞キャッシュに当てるのが目的）。
        self.assertEqual(self._kinds_of("judge"), {"judge"})
        self.assertEqual(self._kinds_of("work"), {"work"})

    def test_without_a_preference_the_order_stays_arbitrary(self):
        # 従来どおり。shuffle のままなので、十分な試行で両方の種別が出る。
        self.assertEqual(self._kinds_of(""), {"work", "judge"})

    def test_unknown_preference_does_not_starve_anything(self):
        # 直前の種別が今の graph に無くても、claim できるものは返る（優先は当たらないだけ）。
        self.assertEqual(self._kinds_of("verify"), {"work", "judge"})

    def test_preference_never_returns_an_unclaimable_node(self):
        # 優先は claim 可否の判定より後ろ。judge を全部終わらせても work が返る。
        for nid in ("n2", "n4"):
            self.bus.write_result(nid, "w1", "done", "ok", None, kind="judge")
        for _ in range(6):
            picked = kf.pick_claimable(self.bus, "judge")
            self.assertIsNotNone(picked)
            self.assertEqual(picked[1].get("kind"), "work")


if __name__ == "__main__":
    unittest.main()
