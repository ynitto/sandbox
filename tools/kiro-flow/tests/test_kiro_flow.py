#!/usr/bin/env python3
"""kiro-flow のプロトコル単体テスト＋障害注入テスト。

kiro-cli 不要（stub のみ）。標準ライブラリの unittest で完結する。
実行: python3 -m unittest discover -s tools/kiro-flow/tests
      または python3 tools/kiro-flow/tests/test_kiro_flow.py
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "kiro-flow.py"

# stub の擬似実行スリープを無効化してテストを高速化（子プロセスにも継承される）
os.environ["KIRO_FLOW_STUB_SLEEP_MAX"] = "0"


def _load_module():
    spec = importlib.util.spec_from_file_location("kiroflow", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kf = _load_module()


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-test-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("test request")

    def _add_task(self, tid, goal="g", deps=None):
        self.bus.write_task({"id": tid, "goal": goal, "deps": deps or []})
        graph = self.bus.read_graph() or {"nodes": {}, "iteration": 0}
        graph["nodes"][tid] = {"goal": goal, "deps": deps or []}
        self.bus.write_graph(graph)

    def test_pending_then_claimed_then_done(self):
        self._add_task("t1")
        self.assertEqual(self.bus.node_state("t1"), "pending")
        self.assertTrue(self.bus.try_claim("t1", "w1", lease_sec=60))
        self.assertEqual(self.bus.node_state("t1"), "claimed")
        self.bus.write_result("t1", "w1", "done", "out")
        self.assertEqual(self.bus.node_state("t1"), "done")

    def test_deterministic_winner_earliest_ts(self):
        self._add_task("t1")
        # 名前空間付き claim を 2 つ手で書く（ts が古い方が勝つ）
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "wB.json"),
                             {"who": "wB", "ts": 200.0, "lease_until": time.time() + 999})
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "wA.json"),
                             {"who": "wA", "ts": 100.0, "lease_until": time.time() + 999})
        self.assertEqual(self.bus._winner("t1"), "wA")

    def test_expired_lease_is_reclaimable(self):
        """障害注入: ワーカーが死んで claim を残したケース。lease 切れなら再 claim できる。"""
        self._add_task("t1")
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "dead.json"),
                             {"who": "dead", "ts": 1.0, "lease_until": time.time() - 10})
        self.assertIsNone(self.bus._winner("t1"))          # 期限切れは無視
        self.assertEqual(self.bus.node_state("t1"), "pending")
        self.assertTrue(self.bus.try_claim("t1", "w2", lease_sec=60))  # 別ノードが回収
        self.assertEqual(self.bus._winner("t1"), "w2")

    def test_concurrent_claim_single_winner(self):
        """障害注入相当: 複数ワーカーが同時に同じタスクを取りに行っても勝者は 1 人。"""
        self._add_task("t1")
        results = {}
        barrier = threading.Barrier(5)

        def worker(name):
            barrier.wait()
            results[name] = self.bus.try_claim("t1", name, lease_sec=60)

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [n for n, ok in results.items() if ok]
        self.assertEqual(len(winners), 1, f"勝者は 1 人のはず: {results}")

    def test_claim_lock_is_off_bus(self):
        # 排他ロックはバス（git に乗る領域）の外＝一時領域に置く
        lp = kf._claim_lock_path(self.bus._claim_dir("t1"))
        self.assertNotIn(self.tmp, lp)
        self.assertIn("kiro-flow-locks", lp)

    def test_all_terminal(self):
        self._add_task("t1")
        self._add_task("t2")
        self.assertFalse(self.bus.all_terminal())
        self.bus.write_result("t1", "w", "done", "o")
        self.bus.write_result("t2", "w", "failed", "o")
        self.assertTrue(self.bus.all_terminal())  # done/failed はどちらも terminal


class PlannerTests(unittest.TestCase):
    def test_parallel_split(self):
        tasks = kf.plan_stub("a; b; c")
        self.assertEqual([t["id"] for t in tasks], ["t1", "t2", "t3"])
        self.assertTrue(all(t["deps"] == [] for t in tasks))

    def test_sequential_chain_deps(self):
        tasks = kf.plan_stub("setup -> build -> test; docs")
        by_id = {t["id"]: t for t in tasks}
        self.assertEqual(by_id["t1"]["deps"], [])         # setup
        self.assertEqual(by_id["t2"]["deps"], ["t1"])     # build after setup
        self.assertEqual(by_id["t3"]["deps"], ["t2"])     # test after build
        self.assertEqual(by_id["t4"]["deps"], [])         # docs independent


class StructuredResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-data-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")

    def test_result_data_roundtrip(self):
        self.bus.write_result("t1", "w", "done", "txt", data={"items": [1, 2, 3]})
        r = self.bus.read_result("t1")
        self.assertEqual(r["output"], "txt")
        self.assertEqual(r["data"], {"items": [1, 2, 3]})

    def test_result_without_data_has_no_key(self):
        self.bus.write_result("t1", "w", "done", "txt")
        self.assertNotIn("data", self.bus.read_result("t1"))

    def test_collect_dep_results_sees_through_gate(self):
        # planner が work→gate→synth と直列にしても、集約役は gate が検証した
        # 上流（t2,t3）の成果を受け取れる（gate 経由でも入力が空にならない）
        self.bus.write_graph({"nodes": {
            "t2": {"deps": [], "kind": "work"},
            "t3": {"deps": [], "kind": "work"},
            "gate": {"deps": ["t2", "t3"], "kind": "verify"},
            "synth": {"deps": ["gate"], "kind": "synthesize"},
        }})
        self.bus.write_result("t2", "w", "done", "out2")
        self.bus.write_result("t3", "w", "done", "out3")
        self.bus.write_result("gate", "w", "done", "verify=pass", data={"ok": True})
        node = {"deps": ["gate"], "kind": "synthesize"}
        dep = kf._collect_dep_results(self.bus, node, "synthesize")
        self.assertEqual(set(dep), {"gate", "t2", "t3"})  # 上流が透過された
        self.assertEqual(dep["t2"]["output"], "out2")

    def test_collect_dep_results_no_passthrough_for_work(self):
        # 非集約ノードは透過しない（gate をそのまま受ける）
        self.bus.write_graph({"nodes": {
            "a": {"deps": [], "kind": "work"},
            "gate": {"deps": ["a"], "kind": "verify"},
        }})
        self.bus.write_result("a", "w", "done", "oa")
        self.bus.write_result("gate", "w", "done", "verify=pass", data={"ok": True})
        dep = kf._collect_dep_results(self.bus, {"deps": ["gate"], "kind": "work"}, "work")
        self.assertEqual(set(dep), {"gate"})

    def test_executor_returns_text_and_data(self):
        text, data = kf.execute_stub("classify", "backend のバグ", {}, None)
        self.assertEqual(text, "class=backend")
        self.assertEqual(data, {"label": "backend"})
        text, data = kf.execute_stub("work", "ふつうの仕事", {}, None)
        self.assertIsNone(data)

    def test_reduce_aggregates_dependency_data(self):
        deps = {
            "a": {"output": "oa", "data": ["x", "y"]},
            "b": {"output": "ob", "data": ["z"]},
            "c": {"output": "oc"},  # data 無し → output を要素化
        }
        text, data = kf.execute_stub("reduce", "集約", deps, None)
        self.assertEqual(data["count"], 4)
        self.assertEqual(sorted(str(i) for i in data["items"]), ["oc", "x", "y", "z"])


class OutputSanitizeTests(unittest.TestCase):
    def test_strip_ansi(self):
        raw = "\x1b[38;5;141m> \x1b[0mhello\x1b[1mX\x1b[22m"
        self.assertEqual(kf.strip_ansi(raw), "> helloX")
        self.assertEqual(kf.strip_ansi(""), "")

    def test_reconcile_count_fixes_mismatch(self):
        d = kf._reconcile_count({"primes": [2, 3, 5], "count": 99, "range": {"min": 2}})
        self.assertEqual(d["count"], 3)

    def test_reconcile_count_skips_when_ambiguous(self):
        # count 無し / 複数リスト / 非 dict は変更しない
        self.assertEqual(kf._reconcile_count({"primes": [2, 3]}), {"primes": [2, 3]})
        self.assertEqual(kf._reconcile_count({"a": [1], "b": [1, 2], "count": 5})["count"], 5)
        self.assertEqual(kf._reconcile_count([1, 2, 3]), [1, 2, 3])


class VerifyGateTests(unittest.TestCase):
    def test_normalize_verify_from_json(self):
        d = kf._normalize_verify("verify=fail", {"ok": False, "issues": ["x"]})
        self.assertFalse(d["ok"])
        self.assertEqual(d["issues"], ["x"])

    def test_normalize_verify_from_text(self):
        self.assertFalse(kf._normalize_verify("verify=fail: 件数不一致", None)["ok"])
        self.assertTrue(kf._normalize_verify("verify=pass 問題なし", None)["ok"])

    def test_is_gate_result(self):
        self.assertTrue(kf._is_gate_result({"data": {"ok": True}}))
        self.assertFalse(kf._is_gate_result({"data": [1, 2]}))
        self.assertFalse(kf._is_gate_result({"output": "x"}))


class DataDrivenFanoutTests(unittest.TestCase):
    def test_split_executor_returns_list(self):
        text, data = kf.execute_stub("split", "5 件に分解", {}, None)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 5)

    def test_split_expands_to_map_and_reduce(self):
        nodes = {"split1": {"goal": "分解", "deps": [], "kind": "split"}}
        results = {"split1": {"status": "done", "data": ["x", "y", "z"]}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0, max_fanout=50)
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new],
                         ["split1-m1", "split1-m2", "split1-m3", "split1-reduce"])
        red = next(t for t in new if t["kind"] == "reduce")
        self.assertEqual(red["deps"], ["split1-m1", "split1-m2", "split1-m3"])

    def test_fanout_respects_max(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": list(range(100))}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, max_fanout=5)
        self.assertEqual(len([t for t in new if t["kind"] == "map"]), 5)

    def test_map_goal_carries_request_intent(self):
        # map ゴールに元の要求（intent）が埋め込まれ、各要素に本来のタスクが適用される
        nodes = {"t1": {"id": "t1", "goal": "分解", "deps": [], "kind": "split"}}
        results = {"t1": {"status": "done", "data": ["1-100", "101-200"]}}
        _, new, _ = kf.continue_stub("1-1000まで素数を出して", nodes, results, 0)
        m1 = next(t for t in new if t["id"] == "t1-m1")
        self.assertIn("素数", m1["goal"])
        self.assertIn("1-100", m1["goal"])
        # reduce ゴールも intent を保持（並べ替え・集約条件を失わない）
        red = next(t for t in new if t["id"] == "t1-reduce")
        self.assertIn("素数", red["goal"])

    def test_collapse_static_split_successors(self):
        # planner が split→work→reduce を静的に焼き込んでも fan-out 前に後段を除去
        g = {"t1": {"id": "t1", "goal": "分割", "deps": [], "kind": "split"},
             "t2": {"id": "t2", "goal": "work", "deps": ["t1"], "kind": "work"},
             "t3": {"id": "t3", "goal": "reduce", "deps": ["t2"], "kind": "reduce"}}
        kf._sanitize_graph(g)
        self.assertEqual(sorted(g), ["t1"])

    def test_collapse_skipped_after_fanout(self):
        # 既に fan-out 済み（<split>-reduce 生成済み）なら除去しない
        g = {"t1": {"id": "t1", "goal": "s", "deps": [], "kind": "split"},
             "t1-m1": {"id": "t1-m1", "goal": "m", "deps": [], "kind": "map"},
             "t1-reduce": {"id": "t1-reduce", "goal": "r", "deps": ["t1-m1"], "kind": "reduce"}}
        kf._sanitize_graph(g)
        self.assertEqual(sorted(g), ["t1", "t1-m1", "t1-reduce"])

    def test_split_not_reexpanded(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"},
                 "s-reduce": {"goal": "集約", "deps": ["s-m1"], "kind": "reduce"}}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        # 既に展開済み（s-reduce あり）→ 追加しない
        self.assertFalse(any(t["id"].startswith("s-m") for t in new))

    def test_strategy_map_reduce_starts_with_split(self):
        strat, tasks = kf.plan_strategy_stub("ファイルをそれぞれ処理して集約")
        self.assertIn("map-reduce", strat["patterns"])
        self.assertEqual([t["kind"] for t in tasks], ["split"])
        # 集約パターンは既定（auto）で検証 gate が有効
        self.assertTrue(strat["review"])
        self.assertIn("adversarial-verification", strat["patterns"])


class CoerceTasksTests(unittest.TestCase):
    def test_unknown_kind_coerced_to_work(self):
        out = kf._coerce_tasks([{"id": "a", "goal": "g", "kind": "bogus"}])
        self.assertEqual(out[0]["kind"], "work")

    def test_valid_kinds_preserved(self):
        out = kf._coerce_tasks([{"id": "a", "kind": "split"}, {"id": "b", "kind": "reduce"}])
        self.assertEqual([t["kind"] for t in out], ["split", "reduce"])

    def test_duplicate_and_existing_ids_dropped(self):
        out = kf._coerce_tasks(
            [{"id": "x"}, {"id": "x"}, {"id": "y"}], existing={"y"})
        self.assertEqual([t["id"] for t in out], ["x"])  # 重複 x は 1 つ、既存 y は除外

    def test_deps_stringified(self):
        out = kf._coerce_tasks([{"id": "a", "deps": [1, "b"]}])
        self.assertEqual(out[0]["deps"], ["1", "b"])


class PlannerRobustnessTests(unittest.TestCase):
    """planner（kiro）がオブジェクトでなくベア配列を返しても落ちないこと。"""

    def test_continue_kiro_handles_bare_list(self):
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(
                kf, "run_kiro",
                return_value='[{"id":"n1","goal":"次","deps":[],"kind":"work"}]'):
            decision, new, _ = kf.continue_kiro("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new], ["n1"])

    def test_continue_kiro_handles_scalar(self):
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(kf, "run_kiro", return_value="42"):
            decision, new, _ = kf.continue_kiro("req", nodes, results, 0)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])

    def test_plan_strategy_kiro_handles_bare_list(self):
        with mock.patch.object(
                kf, "run_kiro",
                return_value='[{"id":"t1","goal":"分解","deps":[],"kind":"split"}]'):
            strat, tasks = kf.plan_strategy_kiro("req", None)
        self.assertEqual([t["id"] for t in tasks], ["t1"])


class KiroTimeoutTests(unittest.TestCase):
    """kiro-cli のハングがタイムアウトで失敗化され、run が無限停止しないこと。"""

    def test_run_kiro_timeout_raises_runtimeerror(self):
        import subprocess
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="kiro-cli", timeout=k.get("timeout"))
        with mock.patch.object(kf.subprocess, "run", side_effect=boom):
            with self.assertRaises(RuntimeError) as ctx:
                kf.run_kiro("素数を列挙", None)
        self.assertIn("タイムアウト", str(ctx.exception))

    def test_kiro_timeout_env_override(self):
        with mock.patch.dict(os.environ, {"KIRO_FLOW_KIRO_TIMEOUT": "0"}):
            self.assertIsNone(kf._kiro_timeout())   # 0/負で無効化
        with mock.patch.dict(os.environ, {"KIRO_FLOW_KIRO_TIMEOUT": "120"}):
            self.assertEqual(kf._kiro_timeout(), 120.0)


class StructuredExtractionTests(unittest.TestCase):
    """自由記述 kind の本文に紛れた JSON 風断片を data に誤昇格させないこと。"""

    def test_work_does_not_extract_incidental_json(self):
        # 本文に "issues": [] を含む work 出力でも data は None（誤抽出の事故防止）
        txt = 'verify=pass（修正不要）。t2の検査で問題なし（{"ok": true, "issues": []}）。通過。'
        with mock.patch.object(kf, "run_kiro", return_value=txt):
            _, data = kf.execute_kiro("work", "修正し通過", {}, None)
        self.assertIsNone(data)

    def test_generate_does_not_extract_incidental_json(self):
        with mock.patch.object(kf, "run_kiro", return_value="例: [1, 2] のような配列を返す関数"):
            _, data = kf.execute_kiro("generate", "関数を書く", {}, None)
        self.assertIsNone(data)

    def test_split_still_extracts_list(self):
        with mock.patch.object(kf, "run_kiro", return_value='["1-100", "101-200"]'):
            _, data = kf.execute_kiro("split", "分割", {}, None)
        self.assertEqual(data, ["1-100", "101-200"])

    def test_reduce_still_extracts_and_reconciles(self):
        with mock.patch.object(kf, "run_kiro",
                               return_value='{"primes": [2, 3, 5], "count": 99}'):
            _, data = kf.execute_kiro("reduce", "集約", {}, None)
        self.assertEqual(data["count"], 3)  # 実リスト長へ補正


class GraphHealthTests(unittest.TestCase):
    def test_unknown_deps_dropped(self):
        nodes = {"a": {"id": "a", "goal": "", "deps": ["ghost"], "kind": "work"},
                 "b": {"id": "b", "goal": "", "deps": ["a"], "kind": "work"}}
        kf._sanitize_graph(nodes)
        self.assertEqual(nodes["a"]["deps"], [])      # 未知 ghost を除去
        self.assertEqual(nodes["b"]["deps"], ["a"])   # 正当な依存は保持

    def test_cycle_broken(self):
        nodes = {"a": {"id": "a", "goal": "", "deps": ["b"], "kind": "work"},
                 "b": {"id": "b", "goal": "", "deps": ["a"], "kind": "work"}}
        kf._sanitize_graph(nodes)
        # 循環が断ち切られ、トポロジカル順が成立する（少なくとも片方の deps が空）
        self.assertTrue(nodes["a"]["deps"] == [] or nodes["b"]["deps"] == [])

    def test_self_loop_dropped(self):
        nodes = {"a": {"id": "a", "goal": "", "deps": ["a"], "kind": "work"}}
        kf._sanitize_graph(nodes)
        self.assertEqual(nodes["a"]["deps"], [])


class ReviewGateTests(unittest.TestCase):
    def test_fanout_inserts_gate_before_synthesize(self):
        strat, tasks = kf.plan_strategy_stub("A; B; C", review=True)
        by = {t["id"]: t for t in tasks}
        self.assertIn("gate", by)
        self.assertEqual(by["gate"]["kind"], "verify")
        self.assertIn("gate", by["synth"]["deps"])        # 統合は gate を待つ
        self.assertIn("t1", by["synth"]["deps"])          # 統合は成果も集約する
        self.assertIn("adversarial-verification", strat["patterns"])

    def test_map_reduce_gate_between_map_and_reduce(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["x", "y"]}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, max_fanout=50, review=True)
        by = {t["id"]: t for t in new}
        self.assertIn("s-gate", by)
        self.assertEqual(by["s-gate"]["kind"], "verify")
        self.assertIn("s-gate", by["s-reduce"]["deps"])    # reduce は gate を待つ
        self.assertIn("s-m1", by["s-reduce"]["deps"])      # reduce は map 成果を集約
        self.assertEqual(by["s-gate"]["deps"], ["s-m1", "s-m2"])


class ContinuationTests(unittest.TestCase):
    def test_replan_retries_failed_once(self):
        nodes = {"t1": {"goal": "ok", "deps": [], "kind": "work"},
                 "t2": {"goal": "FAIL bad", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done"}, "t2": {"status": "failed"}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new], ["t2r"])
        self.assertNotIn("FAIL", new[0]["goal"])  # retry のゴールは修正済み

    def test_no_replan_when_all_done(self):
        nodes = {"t1": {"goal": "ok", "deps": [], "kind": "work"}}
        decision, new, _ = kf.continue_stub("req", nodes, {"t1": {"status": "done"}}, 0)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])

    def test_classify_routes_to_specialist(self):
        nodes = {"classify": {"goal": "分類: backend のバグ", "deps": [], "kind": "classify"}}
        results = {"classify": {"status": "done", "output": "class=backend"}}
        decision, new, _ = kf.continue_stub("backend のバグ", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual(new[0]["id"], "classify-act")
        self.assertIn("backend", new[0]["goal"])
        self.assertEqual(new[0]["deps"], ["classify"])

    def test_verify_fail_triggers_regen_and_recheck(self):
        nodes = {"gen1": {"goal": "FLAKY work", "deps": [], "kind": "generate"},
                 "verify1": {"goal": "検証", "deps": ["gen1"], "kind": "verify"}}
        results = {"gen1": {"status": "done", "output": "[stub] 未完(issue)"},
                   "verify1": {"status": "done", "output": "verify=fail"}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        ids = [t["id"] for t in new]
        self.assertIn("gen1-r1", ids)     # 作り直し
        self.assertIn("verify1-r1", ids)  # 再検証
        self.assertNotIn("FLAKY", next(t for t in new if t["id"] == "gen1-r1")["goal"])


class PatternStrategyTests(unittest.TestCase):
    def test_pattern_detection(self):
        cases = {
            "バグを分類して振り分けて": "classify-and-act",
            "3案を比較して最良を選ぶ tournament": "tournament",
            "候補を出してフィルタ": "generate-and-filter",
            "成果をレビューして検証": "adversarial-verification",
            "テストが通るまで繰り返す": "loop-until-done",
            "資料を3観点でまとめる": "fan-out-and-synthesize",
        }
        for req, want in cases.items():
            self.assertEqual(kf._detect_pattern(req), want, req)

    def test_parallelism_extraction(self):
        self.assertEqual(kf._parallelism("候補を x4 出す", 2), 4)
        self.assertEqual(kf._parallelism("並列5で", 2), 5)
        self.assertEqual(kf._parallelism("ふつうの要求", 3), 3)

    def test_fanout_graph_has_synthesize_over_parallel(self):
        # 既定（auto）では集約パターンに検証 gate が入るため、純粋な構造は --no-review で確認
        strat, tasks = kf.plan_strategy_stub("A; B; C", review=False)
        self.assertEqual(strat["patterns"], ["fan-out-and-synthesize"])
        self.assertFalse(strat["review"])
        synth = [t for t in tasks if t["kind"] == "synthesize"]
        self.assertEqual(len(synth), 1)
        # 統合ノードは全並列ノードに依存
        gens = [t["id"] for t in tasks if t["kind"] != "synthesize"]
        self.assertEqual(sorted(synth[0]["deps"]), sorted(gens))

    def test_aggregating_pattern_auto_enables_review(self):
        # 公式準拠: 集約パターンは既定で検証 gate を自動挿入する
        strat, tasks = kf.plan_strategy_stub("A; B; C")  # fan-out-and-synthesize
        self.assertTrue(strat["review"])
        self.assertIn("verify", [t["kind"] for t in tasks])

    def test_non_aggregating_pattern_no_auto_review(self):
        # 集約点を持たない（または内包する）パターンは auto では gate を足さない
        strat, _ = kf.plan_strategy_stub("バグを分類して振り分けて")  # classify-and-act
        self.assertFalse(strat["review"])

    def test_explicit_no_review_overrides_auto(self):
        strat, _ = kf.plan_strategy_stub("ファイルをそれぞれ処理して集約", review=False)
        self.assertFalse(strat["review"])

    def test_tournament_graph_has_judge(self):
        strat, tasks = kf.plan_strategy_stub("最良案を選ぶ tournament x3")
        self.assertEqual(strat["patterns"], ["tournament"])
        self.assertEqual(strat["parallelism"], 3)
        self.assertEqual(len([t for t in tasks if t["kind"] == "generate"]), 3)
        self.assertEqual(len([t for t in tasks if t["kind"] == "judge"]), 1)


class DaemonPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-daemon-")
        self.bus = kf.Bus(self.tmp, "_")

    def test_submit_and_inbox(self):
        self.bus.submit_request("req1", "do things", "tester")
        self.assertIn("req1", self.bus.list_inbox())
        self.assertEqual(self.bus.read_inbox("req1")["request"], "do things")

    def test_claim_request_single_winner(self):
        self.bus.submit_request("req1", "x", "t")
        a = self.bus.claim_request("req1", "daemonA", 60)
        b = self.bus.claim_request("req1", "daemonB", 60)
        self.assertNotEqual(a, b)            # ちょうど 1 台が勝つ
        self.assertTrue(a and not b)         # 先に claim した A が勝者

    def test_claim_request_false_if_run_exists(self):
        self.bus.submit_request("req1", "x", "t")
        kf.Bus(self.tmp, "req1").ensure_run("x")  # 既に run が作られている
        self.assertFalse(self.bus.claim_request("req1", "daemonC", 60))

    def test_remove_run_also_purges_inbox(self):
        # gc（remove_run）は対応する inbox 要求と claim も消す。残すと run_exists が
        # 再び False になり、デーモンが完了済み要求を再実行してしまう（resurrection 防止）。
        self.bus.submit_request("req1", "x", "t")
        self.bus.claim_request("req1", "daemonA", 60)  # inbox/claims/req1 を作る
        kf.Bus(self.tmp, "req1").ensure_run("x")
        self.assertIn("req1", self.bus.list_inbox())
        self.bus.remove_run("req1")
        self.assertNotIn("req1", self.bus.list_inbox())          # inbox 要求が消えた
        self.assertFalse(self.bus.run_exists("req1"))            # run も消えた
        import os
        self.assertFalse(os.path.exists(
            os.path.join(self.bus.inbox_claims_dir, "req1")))   # claim も消えた
        # 消えた後は再 claim 可能にならない（要求自体が無い）
        self.assertEqual(self.bus.list_inbox(), [])

    def test_active_runs_and_claimable_count(self):
        v = kf.Bus(self.tmp, "runA")
        v.ensure_run("req")
        v.write_graph({"nodes": {"t1": {"goal": "a", "deps": []},
                                 "t2": {"goal": "b", "deps": ["t1"]}}, "iteration": 0})
        v.write_task({"id": "t1", "goal": "a", "deps": []})
        v.write_task({"id": "t2", "goal": "b", "deps": ["t1"]})
        v.set_status("running")
        # t1 は claim 可能、t2 は依存未充足なので不可 → count == 1
        self.assertIn("runA", self.bus.active_runs())
        self.assertEqual(self.bus.run_claimable_count("runA"), 1)
        # t1 完了で t2 が解放される
        v.write_result("t1", "w", "done", "o")
        self.assertEqual(self.bus.run_claimable_count("runA"), 1)
        # 終端した run は active_runs から外れる
        v.write_result("t2", "w", "done", "o")
        v.set_status("done")
        self.assertNotIn("runA", self.bus.active_runs())


class EndToEndTests(unittest.TestCase):
    def _run_up(self, bus, request, extra=None, timeout=90):
        cmd = [sys.executable, str(SCRIPT), "--bus", bus, "run", request,
               "--workers", "3", "--planner", "stub", "--executor", "stub", "--poll", "0.2"]
        cmd += extra or []
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p

    def _final(self, bus):
        run_id = sorted(os.listdir(os.path.join(bus, "runs")))[0]
        return kf.read_json(os.path.join(bus, "runs", run_id, "final.json"))

    def test_up_completes_all_tasks_once(self):
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "x; y; z")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        results = final["results"]
        # fan-out-and-synthesize: 並列ノード + 統合ノード（合計 >= 4）がすべて done
        self.assertGreaterEqual(len(results), 4)
        self.assertIn("synth", results)
        for nid, r in results.items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")
            self.assertTrue(r["who"])  # 誰かが実行した
        self.assertIn("fan-out-and-synthesize", final["strategy"]["patterns"])
        # 集約パターンは既定で検証 gate が自動挿入される
        self.assertTrue(final["strategy"]["review"])
        self.assertIn("gate", results)

    def test_up_replan_recovers_failure(self):
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "good; FAIL bad")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        self.assertGreaterEqual(final["iterations"], 1)        # 再計画が回った
        self.assertEqual(final["results"]["t2r"]["status"], "done")  # retry 成功

    def test_up_map_reduce_with_review(self):
        # データ駆動 fan-out（split→map）＋統合前 gate（--review）の複合を end-to-end で
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "ファイルをそれぞれ処理して集約 3件", extra=["--review"])
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        res = final["results"]
        # split → 3 map → gate(verify) → reduce がすべて done
        self.assertEqual(sum(1 for k in res if k.startswith("split1-m")), 3)
        self.assertIn("split1-gate", res)
        self.assertEqual(res["split1-reduce"]["status"], "done")
        self.assertEqual(res["split1-reduce"]["data"]["count"], 3)


class GitDistributedTests(unittest.TestCase):
    """複数 PC 分散の模擬: ローカルのベアリポジトリを共有バスにし、ノードごとに
    独立クローン（= 別 PC 相当）から push/pull させて検証する。git 必須。"""

    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git が無い環境ではスキップ")
        self.root = tempfile.mkdtemp(prefix="kf-git-")
        self.bare = os.path.join(self.root, "bus.git")
        r = subprocess.run(["git", "init", "--bare", "-b", "main", self.bare],
                           capture_output=True, text=True)
        if r.returncode != 0:  # 古い git 向けフォールバック
            subprocess.run(["git", "init", "--bare", self.bare], check=True,
                           capture_output=True)
        self.clones = os.path.join(self.root, "clones")

    def _final_from_bare(self):
        tmp = tempfile.mkdtemp(prefix="kf-read-")
        subprocess.run(["git", "clone", "-q", self.bare, tmp], check=True,
                       capture_output=True)
        runs = os.path.join(tmp, "runs")
        rid = sorted(os.listdir(runs))[0]
        return kf.read_json(os.path.join(runs, rid, "final.json"))

    def test_claim_across_separate_clones_single_winner(self):
        # 別クローン（別 PC 相当）から同じタスクを claim → 勝者は 1 人
        a = kf.GitBus(os.path.join(self.clones, "A"), "run1", remote=self.bare, branch="main")
        b = kf.GitBus(os.path.join(self.clones, "B"), "run1", remote=self.bare, branch="main")
        won_a = a.try_claim("t1", "nodeA", 60)
        won_b = b.try_claim("t1", "nodeB", 60)
        self.assertTrue(won_a)
        self.assertFalse(won_b)
        # 先着の claim が両クローンから見て勝者
        b.sync_pull()
        self.assertEqual(b._winner("t1"), "nodeA")

    def test_request_claim_elects_single_daemon(self):
        # 別クローンの 2 デーモンが同じ要求を claim → orchestrate 担当は 1 台
        a = kf.GitBus(os.path.join(self.clones, "dA"), "_", remote=self.bare, branch="main")
        b = kf.GitBus(os.path.join(self.clones, "dB"), "_", remote=self.bare, branch="main")
        a.submit_request("req1", "do it", "submitter")
        a.sync_push("submit req1")
        b.sync_pull()
        ca = a.claim_request("req1", "daemonA", 60)
        cb = b.claim_request("req1", "daemonB", 60)
        self.assertTrue(ca)
        self.assertFalse(cb)

    def test_run_over_git_bus_completes(self):
        # orchestrator + worker が各自の独立クローンから git バスへ push/pull して完走
        cmd = [sys.executable, str(SCRIPT), "--bus", self.clones, "--git", self.bare,
               "run", "x; y; z", "--planner", "stub", "--executor", "stub",
               "--workers", "3", "--poll", "0.2"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stderr[-1000:])
        final = self._final_from_bare()
        self.assertIsNotNone(final)
        res = final["results"]
        self.assertGreaterEqual(len(res), 4)  # fan-out 並列 + synth
        for nid, r in res.items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")
            self.assertTrue(r["who"])  # 誰か（どこかのクローン）が実行した

    def test_sparse_checkout_limits_worktree(self):
        # 既存リポジトリにバスを間借り（--git-subdir）し、sparse で他を展開しない
        seed = tempfile.mkdtemp(prefix="kf-seed-")
        subprocess.run(["git", "clone", "-q", self.bare, seed], check=True, capture_output=True)
        os.makedirs(os.path.join(seed, "unrelated"))
        with open(os.path.join(seed, "unrelated", "x.txt"), "w") as f:
            f.write("hi")
        for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed"],
                  ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", seed] + c, check=True, capture_output=True)
        clone = os.path.join(self.clones, "sub")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main", subdir="flow")
        entries = set(os.listdir(clone))
        self.assertNotIn("unrelated", entries)  # sparse で無関係ディレクトリは未展開
        self.assertIn(".git", entries)


if __name__ == "__main__":
    unittest.main(verbosity=2)