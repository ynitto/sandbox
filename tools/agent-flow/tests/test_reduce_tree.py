"""階層 reduce（案3a）の単体テスト。

単段集約は map 件数に比例して 1 ノードへ成果が集中し規模で破綻する。幅を超えたぶんを
中間 reduce へ畳んで木構造にする一方、**幅以下では従来と完全に同一の構造**（id を含む）を
保つことを固定する。id は `-r<数字>` を含んではならない（_retry_depth が作り直し回数と誤読する）。
"""
from __future__ import annotations

import os
import sys
import unittest

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root not in sys.path:
    sys.path.insert(0, root)

import agent_flow as kf  # noqa: E402


def _split_nodes(n_items: int):
    """完了した split ノード 1 つと、その結果（n_items 件のリスト）を作る。"""
    nodes = {"split1": {"goal": "分解", "deps": [], "kind": "split"}}
    results = {"split1": {"status": "done", "output": "[split]",
                          "data": [f"item{i+1}" for i in range(n_items)]}}
    return nodes, results


def _by_id(tasks):
    return {t["id"]: t for t in tasks}


class NarrowFanoutKeepsLegacyShapeTests(unittest.TestCase):
    """幅以下は従来構造のまま（回帰させない）。"""

    def test_single_reduce_depends_on_all_maps(self):
        nodes, results = _split_nodes(5)
        tasks = kf._expand_splits(nodes, results, 50, False, "req", False, 8)
        ids = _by_id(tasks)
        self.assertEqual(sorted(i for i in ids if i.startswith("split1-m")),
                         [f"split1-m{i}" for i in range(1, 6)])
        self.assertEqual(ids["split1-reduce"]["deps"],
                         [f"split1-m{i}" for i in range(1, 6)])
        self.assertNotIn("split1-rc1", ids)

    def test_review_gate_stays_single(self):
        nodes, results = _split_nodes(5)
        tasks = kf._expand_splits(nodes, results, 50, True, "req", False, 8)
        ids = _by_id(tasks)
        self.assertIn("split1-gate", ids)
        self.assertEqual(ids["split1-gate"]["deps"], [f"split1-m{i}" for i in range(1, 6)])
        self.assertIn("split1-gate", ids["split1-reduce"]["deps"])

    def test_exactly_at_width_is_not_chunked(self):
        nodes, results = _split_nodes(8)
        tasks = kf._expand_splits(nodes, results, 50, False, "req", False, 8)
        ids = _by_id(tasks)
        self.assertNotIn("split1-rc1", ids)
        self.assertEqual(len(ids["split1-reduce"]["deps"]), 8)


class HierarchicalReduceTests(unittest.TestCase):
    """幅を超えたら中間 reduce を挟む。"""

    def test_final_reduce_only_sees_intermediate_reduces(self):
        nodes, results = _split_nodes(20)
        tasks = kf._expand_splits(nodes, results, 50, False, "req", False, 8)
        ids = _by_id(tasks)
        deps = ids["split1-reduce"]["deps"]
        self.assertEqual(deps, ["split1-rc1", "split1-rc2", "split1-rc3"])
        for d in deps:                      # 中間 reduce は map だけを受ける
            self.assertEqual(ids[d]["kind"], "reduce")
            self.assertTrue(all(x.startswith("split1-m") for x in ids[d]["deps"]))

    def test_every_map_is_covered_exactly_once(self):
        nodes, results = _split_nodes(20)
        tasks = kf._expand_splits(nodes, results, 50, False, "req", False, 8)
        ids = _by_id(tasks)
        covered = [x for d in ids["split1-reduce"]["deps"] for x in ids[d]["deps"]]
        self.assertEqual(sorted(covered), sorted(f"split1-m{i}" for i in range(1, 21)))
        self.assertEqual(len(covered), len(set(covered)))

    def test_chunks_are_balanced_not_back_loaded(self):
        """20 件・幅 8 は 8/8/4 ではなく 7/7/6（端数を最後へ寄せない）。"""
        nodes, results = _split_nodes(20)
        tasks = kf._expand_splits(nodes, results, 50, False, "req", False, 8)
        ids = _by_id(tasks)
        sizes = [len(ids[d]["deps"]) for d in ids["split1-reduce"]["deps"]]
        self.assertEqual(sizes, [7, 7, 6])

    def test_no_chunk_exceeds_width(self):
        for n in (9, 17, 33, 50):
            nodes, results = _split_nodes(n)
            tasks = kf._expand_splits(nodes, results, 50, False, "req", False, 8)
            ids = _by_id(tasks)
            for nid, node in ids.items():
                if node["kind"] == "reduce":
                    self.assertLessEqual(len(node["deps"]), 8, f"{nid} (n={n})")

    def test_review_gates_are_chunked_too(self):
        nodes, results = _split_nodes(20)
        tasks = kf._expand_splits(nodes, results, 50, True, "req", False, 8)
        ids = _by_id(tasks)
        gates = [i for i in ids if i.startswith("split1-gate")]
        self.assertEqual(sorted(gates), ["split1-gate1", "split1-gate2", "split1-gate3"])
        for i, g in enumerate(sorted(gates), start=1):
            rc = ids[f"split1-rc{i}"]
            self.assertIn(g, rc["deps"])            # 中間集約は自分の群の gate 通過後
            self.assertLessEqual(len(ids[g]["deps"]), 8)
        # 最終 reduce の入力は gate 済みの中間集約のみ（gate の二重掛けをしない）
        self.assertTrue(all(d.startswith("split1-rc") for d in ids["split1-reduce"]["deps"]))

    def test_deep_tree_when_intermediates_still_exceed_width(self):
        nodes, results = _split_nodes(200)
        tasks = kf._expand_splits(nodes, results, 200, False, "req", False, 8)
        ids = _by_id(tasks)
        self.assertLessEqual(len(ids["split1-reduce"]["deps"]), 8)
        self.assertTrue(any(i.startswith("split1-rc2-") for i in ids),
                        "2 段目の中間集約が生成されていない")

    def test_generated_ids_never_look_like_retries(self):
        """`-r<数字>` を含む id は _retry_depth が作り直し回数と誤読する。"""
        nodes, results = _split_nodes(200)
        tasks = kf._expand_splits(nodes, results, 200, True, "req", False, 8)
        for t in tasks:
            self.assertEqual(kf._retry_depth(t["id"], {}), 0, t["id"])

    def test_intent_reaches_intermediate_reduces(self):
        nodes, results = _split_nodes(20)
        tasks = kf._expand_splits(nodes, results, 50, False, "API のドキュメント化", False, 8)
        ids = _by_id(tasks)
        self.assertIn("API のドキュメント化", ids["split1-rc1"]["goal"])
        self.assertIn("中間集約", ids["split1-rc1"]["goal"])
        # 最終 reduce は「中間集約の結果を集約する」と書く（map を直接受けないため）
        self.assertIn("各中間集約の結果", ids["split1-reduce"]["goal"])

    def test_expansion_is_idempotent(self):
        """展開済み（-reduce がある）split は二度展開しない。"""
        nodes, results = _split_nodes(20)
        tasks = kf._expand_splits(nodes, results, 50, False, "req", False, 8)
        nodes.update({t["id"]: {"goal": t["goal"], "deps": t["deps"], "kind": t["kind"]}
                      for t in tasks})
        self.assertEqual(kf._expand_splits(nodes, results, 50, False, "req", False, 8), [])


class ExemplarFirstTests(unittest.TestCase):
    """見本先行（pilot→残り）でも階層化が効く。"""

    def test_pilot_stage_is_unchanged(self):
        nodes, results = _split_nodes(20)
        tasks = kf._expand_splits(nodes, results, 50, False, "req", True, 8)
        ids = _by_id(tasks)
        self.assertEqual(sorted(ids), ["split1-m1", "split1-pilot"])

    def test_rest_stage_chunks_all_maps_including_pilot(self):
        nodes, results = _split_nodes(20)
        for t in kf._expand_splits(nodes, results, 50, False, "req", True, 8):
            nodes[t["id"]] = {"goal": t["goal"], "deps": t["deps"], "kind": t["kind"]}
        results["split1-pilot"] = {"status": "done", "output": "ok"}
        tasks = kf._expand_splits(nodes, results, 50, False, "req", True, 8)
        ids = _by_id(tasks)
        covered = [x for d in ids["split1-reduce"]["deps"] for x in ids[d]["deps"]]
        self.assertEqual(sorted(covered), sorted(f"split1-m{i}" for i in range(1, 21)))


class ChunkEvenlyTests(unittest.TestCase):
    def test_sizes_are_within_one_of_each_other(self):
        for n in range(1, 60):
            chunks = kf._chunk_evenly(list(range(n)), 8)
            sizes = [len(c) for c in chunks]
            self.assertLessEqual(max(sizes), 8, n)
            self.assertLessEqual(max(sizes) - min(sizes), 1, n)
            self.assertEqual(sum(sizes), n)

    def test_preserves_order_and_covers_all(self):
        chunks = kf._chunk_evenly(list(range(20)), 8)
        self.assertEqual([x for c in chunks for x in c], list(range(20)))

    def test_degenerate_width_does_not_hang(self):
        self.assertEqual([len(c) for c in kf._chunk_evenly([1, 2, 3], 0)], [1, 1, 1])


class ConfigDefaultTests(unittest.TestCase):
    def test_reduce_width_default_is_eight(self):
        self.assertEqual(kf.CONFIG_DEFAULTS["reduce_width"], 8)
        self.assertEqual(kf._DEFAULT_REDUCE_WIDTH, 8)


if __name__ == "__main__":
    unittest.main()
