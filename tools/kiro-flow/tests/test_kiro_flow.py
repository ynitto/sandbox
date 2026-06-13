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
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "kiro-flow.py"


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
        strat, tasks = kf.plan_strategy_stub("A; B; C")
        self.assertEqual(strat["patterns"], ["fan-out-and-synthesize"])
        synth = [t for t in tasks if t["kind"] == "synthesize"]
        self.assertEqual(len(synth), 1)
        # 統合ノードは全並列ノードに依存
        gens = [t["id"] for t in tasks if t["kind"] != "synthesize"]
        self.assertEqual(sorted(synth[0]["deps"]), sorted(gens))

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
        self.assertEqual(final["strategy"]["patterns"], ["fan-out-and-synthesize"])

    def test_up_replan_recovers_failure(self):
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "good; FAIL bad")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        self.assertGreaterEqual(final["iterations"], 1)        # 再計画が回った
        self.assertEqual(final["results"]["t2r"]["status"], "done")  # retry 成功


if __name__ == "__main__":
    unittest.main(verbosity=2)
