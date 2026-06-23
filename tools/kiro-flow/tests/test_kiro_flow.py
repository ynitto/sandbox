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

# テストの git コミットを環境のコミット署名設定（commit.gpgsign）から切り離す。
# 署名が有効な環境では署名が間欠的に失敗して `git commit` がコミットを作らず、git バス系の
# テストが偶発的に落ちる。GIT_CONFIG_* で commit.gpgsign=false を上乗せして決定的にする。
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = "commit.gpgsign"
os.environ["GIT_CONFIG_VALUE_0"] = "false"


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

    def test_exemplar_first_stages_pilot_then_rest(self):
        # Stage 1: split 完了直後は pilot map 1件＋検証ゲートだけ（残りは出さない）
        nodes = {"s": {"goal": "各件を移行", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["a", "b", "c"]}}
        _, new, _ = kf.continue_stub("各件を移行", nodes, results, 0, exemplar_first=True)
        ids = [t["id"] for t in new]
        self.assertEqual(ids, ["s-m1", "s-pilot"])               # 先行1件＋ゲートのみ
        self.assertEqual(next(t for t in new if t["id"] == "s-pilot")["kind"], "verify")
        self.assertNotIn("s-reduce", ids)

        # pilot ゲート未了の間は残りを展開しない
        nodes.update({"s-m1": {"goal": "", "deps": [], "kind": "map"},
                      "s-pilot": {"goal": "", "deps": ["s-m1"], "kind": "verify"}})
        results.update({"s-m1": {"status": "done"}, "s-pilot": {"status": "running"}})
        _, new2, _ = kf.continue_stub("各件を移行", nodes, results, 1, exemplar_first=True)
        self.assertEqual([t for t in new2 if t["id"].startswith("s-")], [])

        # Stage 2: pilot ゲート done → 残り map（pilot＋ゲートに依存）＋ reduce を展開
        results["s-pilot"] = {"status": "done"}
        _, new3, _ = kf.continue_stub("各件を移行", nodes, results, 2, exemplar_first=True)
        ids3 = [t["id"] for t in new3]
        self.assertEqual(ids3, ["s-m2", "s-m3", "s-reduce"])
        self.assertEqual(next(t for t in new3 if t["id"] == "s-m2")["deps"], ["s-m1", "s-pilot"])
        self.assertEqual(set(next(t for t in new3 if t["id"] == "s-reduce")["deps"]),
                         {"s-m1", "s-m2", "s-m3"})

    def test_default_fanout_unchanged_without_exemplar_first(self):
        # exemplar_first 無し（既定）は従来どおり一括 fan-out
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        _, new, _ = kf.continue_stub("g", nodes, results, 0)
        self.assertEqual([t["id"] for t in new], ["s-m1", "s-m2", "s-reduce"])

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

    def test_kiro_timeout_config_beats_env(self):
        # 設定ファイル（_configure_thresholds 経由）が環境変数より優先される
        with mock.patch.object(kf, "_KIRO_TIMEOUT", 300.0), \
             mock.patch.dict(os.environ, {"KIRO_FLOW_KIRO_TIMEOUT": "120"}):
            self.assertEqual(kf._kiro_timeout(), 300.0)
        with mock.patch.object(kf, "_KIRO_TIMEOUT", 0.0):
            self.assertIsNone(kf._kiro_timeout())   # 設定の 0/負も無効化として尊重

    def test_stub_sleep_max_config_beats_env(self):
        # stub_sleep_max も設定が環境変数より優先される（0 で即時）
        calls = []
        with mock.patch.object(kf, "_STUB_SLEEP_MAX", 0.0), \
             mock.patch.dict(os.environ, {"KIRO_FLOW_STUB_SLEEP_MAX": "5"}), \
             mock.patch.object(kf.time, "sleep", side_effect=lambda s: calls.append(s)):
            kf._stub_sleep()
        self.assertEqual(calls, [])   # 設定 0 → sleep されない

    def test_configure_thresholds_pins_config_values(self):
        # resolve_config 済みの args から kiro_timeout / stub_sleep_max が確定すること
        import argparse
        args = argparse.Namespace(argv_limit=None, executor_dir=None,
                                  kiro_timeout=45.0, stub_sleep_max=0.0)
        with mock.patch.object(kf, "_KIRO_TIMEOUT", None), \
             mock.patch.object(kf, "_STUB_SLEEP_MAX", None):
            kf._configure_thresholds(args)
            self.assertEqual(kf._KIRO_TIMEOUT, 45.0)
            self.assertEqual(kf._STUB_SLEEP_MAX, 0.0)


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


def _load_executor_plugin(name):
    """executors/<name>.py をテスト用にロードする。"""
    path = HERE.parent / "executors" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"kf_exec_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gl_plugin = _load_executor_plugin("gitlab")


class GitlabExecutorPluginTests(unittest.TestCase):
    """gitlab executor プラグイン（opt-in）: イシュー起票 → approved ポーリング → 完了。"""

    def setUp(self):
        # ポーリング待ちを無くす設定を環境変数（KIRO_FLOW_EXECUTOR_CONFIG）で渡す
        self._cfg = {"conn_label": "default", "labels": "status:open,assignee:any",
                     "priority": "priority:normal", "poll_interval": 0.0,
                     "timeout": 0.0, "approved_label": "status:approved",
                     "done_label": "status:done"}
        self._prev_env = os.environ.get("KIRO_FLOW_EXECUTOR_CONFIG")
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(self._cfg)

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None)
        else:
            os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = self._prev_env

    def _run_with(self, gl_side_effect):
        with mock.patch.object(gl_plugin, "_find_gl_script", return_value="/fake/gl.py"), \
             mock.patch.object(gl_plugin, "run_gl", side_effect=gl_side_effect) as m:
            text, data = gl_plugin.execute("work", "ログイン画面を追加", {})
        return text, data, m

    def test_creates_issue_and_waits_for_approved(self):
        # get-issue を 2 回呼ばせ、2 回目で approved にする
        states = [["status:open"], ["status:approved"]]

        def side(subargs, *a, **k):
            cmd = subargs[0]
            if cmd == "create-issue":
                return {"iid": 42, "web_url": "https://gl/x/42"}
            if cmd == "get-issue":
                return {"labels": states.pop(0), "state": "opened"}
            if cmd == "get-comments":
                return [{"body": "実装しました。MR を用意済みです。"}]
            return {}

        text, data, m = self._run_with(side)
        self.assertIn("#42 approved", text)
        self.assertIn("MR を用意済み", text)
        self.assertEqual(data["issue_iid"], 42)
        self.assertTrue(data["approved"])
        # create-issue に priority ラベルが連結されていること
        create_call = next(c for c in m.call_args_list if c.args[0][0] == "create-issue")
        labels = create_call.args[0][create_call.args[0].index("--labels") + 1]
        self.assertIn("priority:normal", labels)
        self.assertIn("assignee:any", labels)

    def test_closed_issue_counts_as_done(self):
        def side(subargs, *a, **k):
            cmd = subargs[0]
            if cmd == "create-issue":
                return {"iid": 7, "web_url": "https://gl/x/7"}
            if cmd == "get-issue":
                return {"labels": ["status:open"], "state": "closed"}
            if cmd == "get-comments":
                return []
            return {}

        text, data, _ = self._run_with(side)
        self.assertEqual(data["issue_iid"], 7)
        self.assertTrue(data["approved"])

    def test_timeout_raises(self):
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(
            dict(self._cfg, timeout=0.01, poll_interval=0.0))

        def side(subargs, *a, **k):
            cmd = subargs[0]
            if cmd == "create-issue":
                return {"iid": 1, "web_url": "https://gl/x/1"}
            if cmd == "get-issue":
                return {"labels": ["status:open"], "state": "opened"}
            return {}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(side)
        self.assertIn("approved", str(ctx.exception))

    def test_missing_gl_script_raises(self):
        with mock.patch.object(gl_plugin, "_find_gl_script", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                gl_plugin.execute("work", "なにか", {})
        self.assertIn("gl.py", str(ctx.exception))

    def test_config_zero_poll_interval_respected(self):
        # 0.0 が `x or default` で 30 に潰れないこと（プラグイン側 _as_float の確認）
        self.assertEqual(gl_plugin._as_float(0.0, 30.0), 0.0)
        self.assertEqual(gl_plugin._as_float(None, 30.0), 30.0)
        self.assertEqual(gl_plugin._as_float("bad", 30.0), 30.0)

    def test_repo_url_passed_to_run_gl(self):
        # repo_url を設定すると各 run_gl 呼び出しに repo_url が渡る（gl.py へ GL_PROJECT_URL）
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(
            dict(self._cfg, repo_url="https://gitlab.com/group/repo"))

        def side(subargs, *a, **k):
            cmd = subargs[0]
            if cmd == "create-issue":
                return {"iid": 9, "web_url": "https://gl/x/9"}
            if cmd == "get-issue":
                return {"labels": ["status:approved"], "state": "opened"}
            if cmd == "get-comments":
                return []
            return {}

        _, _, m = self._run_with(side)
        for call in m.call_args_list:
            self.assertEqual(call.kwargs.get("repo_url"), "https://gitlab.com/group/repo")

    def test_repo_url_default_empty(self):
        self.assertEqual(gl_plugin._config()["repo_url"], "")

    def test_run_gl_sets_gl_project_url_env(self):
        # repo_url 指定時、gl.py 起動の env に GL_PROJECT_URL が入ること
        captured = {}

        class _Proc:
            returncode = 0
            stdout = "{}"
            stderr = ""

        def fake_run(cmd, *a, **k):
            captured["env"] = k.get("env", {})
            return _Proc()

        with mock.patch.object(gl_plugin, "_find_gl_script", return_value="/fake/gl.py"), \
             mock.patch.object(gl_plugin.subprocess, "run", side_effect=fake_run):
            gl_plugin.run_gl(["project-info"], "default",
                             repo_url="https://gitlab.com/group/repo")
        self.assertEqual(captured["env"].get("GL_PROJECT_URL"),
                         "https://gitlab.com/group/repo")

    def test_env_override_beats_config_block(self):
        # 個別環境変数 KIRO_FLOW_GITLAB_* が KIRO_FLOW_EXECUTOR_CONFIG より優先される
        os.environ["KIRO_FLOW_GITLAB_CONN_LABEL"] = "work"
        try:
            self.assertEqual(gl_plugin._config()["conn_label"], "work")
        finally:
            os.environ.pop("KIRO_FLOW_GITLAB_CONN_LABEL", None)

    def test_issue_body_has_acceptance_criteria_and_deps(self):
        deps = {"t1": {"output": "上流の成果", "data": {"n": 3}}}
        body = gl_plugin._issue_body("synthesize", "統合する", deps)
        self.assertIn("## 受け入れ条件", body)
        self.assertIn("統合する", body)
        self.assertIn("t1", body)
        self.assertIn("kiro-flow", body)


class ExecutorResolutionTests(unittest.TestCase):
    """executor のプラグイン解決（kiro-loop の event_hook 流のローダ）。"""

    def _args(self, **kw):
        import types
        return types.SimpleNamespace(**kw)

    def test_builtin_kiro_and_stub(self):
        self.assertIs(kf.make_executor(self._args(executor="kiro")), kf.execute_kiro)
        self.assertIs(kf.make_executor(self._args(executor="stub")), kf.execute_stub)

    def test_default_is_kiro(self):
        self.assertIs(kf.make_executor(self._args(executor=None)), kf.execute_kiro)

    def test_resolves_bundled_gitlab_plugin(self):
        fn = kf.make_executor(self._args(executor="gitlab", gitlab={"poll_interval": 1}))
        self.assertTrue(callable(fn))
        # プラグイン設定が環境変数で渡されていること
        self.assertEqual(json.loads(os.environ["KIRO_FLOW_EXECUTOR_CONFIG"]),
                         {"poll_interval": 1})

    def test_explicit_path_plugin(self):
        path = str(HERE.parent / "executors" / "gitlab.py")
        fn = kf.make_executor(self._args(executor=path))
        self.assertTrue(callable(fn))

    def test_unresolvable_executor_exits(self):
        with self.assertRaises(SystemExit):
            kf.make_executor(self._args(executor="does-not-exist-xyz"))

    def test_plugin_missing_execute_exits(self):
        # execute() を持たないダミープラグインを一時生成して読み込ませる
        d = tempfile.mkdtemp(prefix="kf-exec-")
        try:
            p = pathlib.Path(d) / "noexec.py"
            p.write_text("X = 1\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                kf.make_executor(self._args(executor=str(p)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_module_cache_reloads_on_mtime_change(self):
        d = tempfile.mkdtemp(prefix="kf-exec-")
        try:
            p = pathlib.Path(d) / "p.py"
            p.write_text("VALUE = 1\ndef execute(*a, **k):\n    return ('', None)\n",
                         encoding="utf-8")
            m1 = kf._load_executor_module(str(p))
            self.assertEqual(m1.VALUE, 1)
            # mtime を進めて内容を変える → 再ロードされること
            time.sleep(0.01)
            p.write_text("VALUE = 2\ndef execute(*a, **k):\n    return ('', None)\n",
                         encoding="utf-8")
            os.utime(p, (time.time() + 5, time.time() + 5))
            m2 = kf._load_executor_module(str(p))
            self.assertEqual(m2.VALUE, 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


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


class GranularityTests(unittest.TestCase):
    def test_factor_levels(self):
        self.assertEqual(kf.granularity_factor("coarse"), 1)
        self.assertEqual(kf.granularity_factor("fine"), 2)
        self.assertEqual(kf.granularity_factor("finest"), 3)
        self.assertEqual(kf.granularity_factor(None), 3)        # 既定は最も細かい
        self.assertEqual(kf.granularity_factor("unknown"), 3)

    def test_directive_empty_for_coarse(self):
        self.assertEqual(kf.granularity_directive("coarse"), "")
        self.assertIn("細か", kf.granularity_directive("fine"))
        self.assertIn("細か", kf.granularity_directive("finest"))

    def test_stub_scales_node_count_by_granularity(self):
        # 同じ要求でも粒度が細かいほど並列ノードが増える（明示並列が無い場合）。
        # plan_stub は単一セグメントで乱数を使うため、同一 base になるよう seed を固定する
        import random
        req = "最良案を選ぶ tournament"            # generate ノード数 = parallelism

        def plan(g):
            random.seed(0)
            return kf.plan_strategy_stub(req, granularity=g)

        coarse, ctasks = plan("coarse")
        fine, _ = plan("fine")
        finest, ftasks = plan("finest")
        self.assertLess(coarse["parallelism"], fine["parallelism"])
        self.assertLess(fine["parallelism"], finest["parallelism"])
        self.assertEqual(fine["parallelism"], coarse["parallelism"] * 2)
        self.assertEqual(finest["parallelism"], coarse["parallelism"] * 3)
        gens = lambda ts: len([t for t in ts if t["kind"] == "generate"])
        self.assertGreater(gens(ftasks), gens(ctasks))           # 細かいほどノードが多い

    def test_explicit_parallelism_not_scaled(self):
        # 要求に "x3" 等の明示があれば粒度倍率は効かせない（ユーザ指定を尊重）
        strat, _ = kf.plan_strategy_stub("案を出して選ぶ tournament x3", granularity="finest")
        self.assertEqual(strat["parallelism"], 3)

    def test_scale_parallelism_caps_at_16(self):
        self.assertEqual(kf.scale_parallelism(6, "finest"), 16)   # 6*3=18 → 16 にクランプ


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

    def test_run_repos_roundtrip_via_meta(self):
        # 成果物リポジトリは run meta に載り、submit→inbox でも伝搬する
        b = kf.Bus(self.tmp, "runR")
        b.ensure_run("goal", repos=["https://x/a.git", "https://x/b.git"])
        self.assertEqual(b.run_repos(), ["https://x/a.git", "https://x/b.git"])
        self.bus.submit_request("reqR", "do", "t", repos=["https://x/c.git"])
        self.assertEqual(self.bus.read_inbox("reqR")["repos"], ["https://x/c.git"])

    def test_ensure_work_repos_clones_and_cleans(self):
        # ローカルの「リモート」を git init で用意し、clone→作業後 cleanup を検証
        remote = os.path.join(self.tmp, "remote_repo")
        os.makedirs(remote)
        subprocess.run(["git", "init", "-q", remote], check=True)
        subprocess.run(["git", "-C", remote, "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", remote, "config", "user.name", "t"], check=True)
        open(os.path.join(remote, "f.txt"), "w").close()
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "init"], check=True)
        try:
            clones = kf.ensure_work_repos([remote], "worker-1")
            self.assertEqual(len(clones), 1)
            c = clones[0]
            path = c["clone"]
            self.assertEqual(c["url"], remote)
            self.assertTrue(path and os.path.isdir(os.path.join(path, ".git")))
            self.assertIn(path, kf.repo_instruction(clones))   # エージェントへ渡す指示にパスが入る
        finally:
            kf.cleanup_work_repos()
        self.assertFalse(path and os.path.exists(path))        # 作業後に消える（クリーン必須）

    def test_ensure_work_repos_marks_failed_clone(self):
        clones = kf.ensure_work_repos([os.path.join(self.tmp, "does_not_exist")], "w")
        self.addCleanup(kf.cleanup_work_repos)
        self.assertEqual(clones[0]["clone"], "")               # clone 失敗は path 空
        self.assertIn("clone 失敗", kf.repo_instruction(clones))

    def test_parse_repo_token_url_or_json(self):
        # 素の URL は url だけの spec。JSON はメタを構造化して受ける（後方互換）
        u = kf.parse_repo_token("https://git/app.git")
        self.assertEqual((u["url"], u["readonly"], u["path"]), ("https://git/app.git", False, ""))
        j = kf.parse_repo_token(
            '{"url":"https://git/shop.git","name":"api","path":"apps/api",'
            '"base":"main","target":"develop","readonly":true,"desc":"API"}')
        self.assertEqual((j["name"], j["path"], j["base"], j["target"], j["readonly"], j["desc"]),
                         ("api", "apps/api", "main", "develop", True, "API"))

    def test_ensure_work_repos_checks_out_base_branch(self):
        # base 指定があればそのブランチを checkout して clone する
        remote = os.path.join(self.tmp, "branched_repo")
        os.makedirs(remote)
        for cmd in (["git", "init", "-q", "-b", "main", remote],
                    ["git", "-C", remote, "config", "user.email", "t@t"],
                    ["git", "-C", remote, "config", "user.name", "t"]):
            subprocess.run(cmd, check=True)
        open(os.path.join(remote, "f.txt"), "w").close()
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "init"], check=True)
        subprocess.run(["git", "-C", remote, "checkout", "-q", "-b", "develop"], check=True)
        open(os.path.join(remote, "dev.txt"), "w").close()
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "dev"], check=True)
        token = json.dumps({"url": remote, "name": "svc", "base": "develop",
                            "path": "apps/api", "desc": "API", "readonly": False})
        try:
            clones = kf.ensure_work_repos([token], "w")
            path = clones[0]["clone"]
            self.assertTrue(path)
            head = subprocess.run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            self.assertEqual(head, "develop")                  # base ブランチが checkout される
            instr = kf.repo_instruction(clones)
            self.assertIn("apps/api", instr)
            self.assertIn("develop", instr)
        finally:
            kf.cleanup_work_repos()

    def test_resolve_node_repos_selects_subset(self):
        api = json.dumps({"url": "https://git/shop.git", "name": "api", "path": "apps/api"})
        web = json.dumps({"url": "https://git/shop.git", "name": "web", "path": "apps/web"})
        run = [api, web, "https://git/lib.git"]
        # 未注釈ノード → 全 repo（後方互換）
        self.assertEqual(kf.resolve_node_repos({"goal": "x"}, run), run)
        # subset 指定 → 一致する token だけ
        self.assertEqual(kf.resolve_node_repos({"goal": "x", "repos": ["api"]}, run), [api])
        self.assertEqual(kf.resolve_node_repos({"goal": "x", "repos": ["web", "lib"]}, run),
                         [web, "https://git/lib.git"])
        # 空配列 → clone しない
        self.assertEqual(kf.resolve_node_repos({"goal": "x", "repos": []}, run), [])
        # run に repo が無ければ常に空
        self.assertEqual(kf.resolve_node_repos({"goal": "x", "repos": ["api"]}, []), [])

    def test_assign_node_repos_stub_assigns_all_llm_keeps(self):
        import types
        run = [json.dumps({"url": "https://git/a.git", "name": "a"}), "https://git/b.git"]
        # stub プランナー: 全ノードへ全 repo を割り当てる
        stub_args = types.SimpleNamespace(planner="stub", repos=run)
        tasks = [{"id": "t1", "goal": "x"}, {"id": "t2", "goal": "y", "repos": ["a"]}]
        kf._assign_node_repos(tasks, stub_args)
        self.assertEqual(tasks[0]["repos"], ["a", "b"])     # 未注釈 → 全 repo（id 化）
        self.assertEqual(tasks[1]["repos"], ["a"])          # 既に割当済みは尊重
        # LLM プランナー: プランナー出力に委ね、本体は触らない
        llm_args = types.SimpleNamespace(planner="kiro", repos=run)
        t2 = [{"id": "t1", "goal": "x"}]
        kf._assign_node_repos(t2, llm_args)
        self.assertNotIn("repos", t2[0])                    # 未注釈のまま（worker で全 repo にフォールバック）

    def test_node_entry_and_coerce_preserve_repos(self):
        e = kf._node_entry({"goal": "g", "deps": [], "kind": "work", "repos": ["a", "b"]})
        self.assertEqual(e["repos"], ["a", "b"])
        self.assertNotIn("repos", kf._node_entry({"goal": "g", "deps": [], "kind": "work"}))
        coerced = kf._coerce_tasks([{"id": "t1", "goal": "g", "repos": ["a"]},
                                    {"id": "t2", "goal": "h"}])
        self.assertEqual(coerced[0]["repos"], ["a"])
        self.assertNotIn("repos", coerced[1])               # 未指定はキーを作らない（フォールバック用）

    def test_repo_instruction_marks_readonly(self):
        ro = [{"url": "https://git/lib.git", "name": "lib", "path": "", "base": "main",
               "target": "main", "readonly": True, "desc": "参照元", "clone": "/tmp/lib"}]
        instr = kf.repo_instruction(ro)
        self.assertIn("参照のみ", instr)
        self.assertNotIn("commit して push すること", instr)   # 参照のみだけなら push 指示を出さない
        rw = [dict(ro[0], readonly=False)]
        self.assertIn("commit して push すること", kf.repo_instruction(rw))

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

    def test_lock_path_canonical_and_config_dir(self):
        import argparse
        # local キーは realpath で canonical 化 → symlink 経由でも同一ロックパス
        real = os.path.join(self.tmp, "real_bus")
        os.makedirs(real)
        link = os.path.join(self.tmp, "link_bus")
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink 不可")
        a_real = argparse.Namespace(bus=real, git=None, git_branch="main", git_subdir=None, lock_dir=None)
        a_link = argparse.Namespace(bus=link, git=None, git_branch="main", git_subdir=None, lock_dir=None)
        self.assertEqual(kf._daemon_lock_path(a_real), kf._daemon_lock_path(a_link))
        # 設定 lock_dir でロック置き場を共有できる（TMPDIR 差の吸収）
        lockdir = os.path.join(self.tmp, "locks")
        a_cfg = argparse.Namespace(bus=real, git=None, git_branch="main", git_subdir=None, lock_dir=lockdir)
        self.assertEqual(os.path.dirname(kf._daemon_lock_path(a_cfg)), lockdir)

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

    def test_result_command_presents_final_output(self):
        # 完了した run に対し result が最終成果（集約ノード synth）を返す
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "x; y; z")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        run_id = sorted(os.listdir(os.path.join(bus, "runs")))[0]
        rp = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "--run-id", run_id,
             "result", "--json"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(rp.returncode, 0, rp.stderr[-800:])
        out = json.loads(rp.stdout)
        self.assertTrue(out["done"])
        self.assertEqual(out["status"], "done")
        self.assertEqual([n["id"] for n in out["final_nodes"]], ["synth"])
        self.assertTrue(out["final_nodes"][0]["output"])


class DaemonE2ETests(unittest.TestCase):
    """実 daemon プロセスが submit を拾い、orchestrator/worker をオンデマンド起動して run を完走させる黒箱 e2e。

    DaemonPrimitiveTests が bus プリミティブ（submit/claim/inbox）を in-process で検証するのに対し、
    こちらは `daemon` を実プロセスとして常駐させ、`submit` 投入 → final.json 生成まで通す。"""

    def setUp(self):
        self.bus = tempfile.mkdtemp(prefix="kf-daemon-e2e-")
        self.daemon = None

    def tearDown(self):
        if self.daemon and self.daemon.poll() is None:
            self.daemon.terminate()
            try:
                self.daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.daemon.kill()
                self.daemon.wait(timeout=5)
        if self.daemon:
            for s in (self.daemon.stdout, self.daemon.stderr):
                if s:
                    s.close()
        shutil.rmtree(self.bus, ignore_errors=True)

    def _start_daemon(self):
        self.daemon = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--bus", self.bus, "daemon",
             "--max-workers", "3", "--planner", "stub", "--executor", "stub",
             "--poll", "0.2", "--no-cleanup"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _submit(self, request):
        p = subprocess.run([sys.executable, str(SCRIPT), "--bus", self.bus, "submit", request],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return p.stdout.strip().splitlines()[0]   # submit は run-id を標準出力の先頭に出す

    def _wait_final(self, run_id, timeout=90):
        final = os.path.join(self.bus, "runs", run_id, "final.json")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.daemon.poll() is not None:    # daemon が落ちたら即座に失敗（無駄に待たない）
                _, err = self.daemon.communicate()
                self.fail(f"daemon が早期終了 rc={self.daemon.returncode}\n{(err or b'').decode()[-800:]}")
            data = kf.read_json(final) if os.path.exists(final) else None
            if data:                              # final.json は atomic write なので存在＝完成
                return data
            time.sleep(0.3)
        self.fail(f"final.json がタイムアウト({timeout}s)内に現れず: {run_id}")

    def test_daemon_picks_up_submit_and_completes(self):
        self._start_daemon()
        run_id = self._submit("x; y; z")
        final = self._wait_final(run_id)
        results = final["results"]
        # daemon → orchestrator → worker で fan-out-and-synthesize が完走（並列ノード + 統合）
        self.assertGreaterEqual(len(results), 4)
        self.assertIn("synth", results)
        for nid, r in results.items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")
            self.assertTrue(r["who"])             # worker が実行した

    def test_daemon_completes_multiple_submits(self):
        # 1 デーモンが複数要求を並行に受理し、それぞれ独立 run として完走させる
        self._start_daemon()
        r1 = self._submit("a; b")
        r2 = self._submit("c; d")
        for run_id in (r1, r2):
            final = self._wait_final(run_id)
            self.assertIn("synth", final["results"])
            for nid, r in final["results"].items():
                self.assertEqual(r["status"], "done", f"{run_id}/{nid}: {r}")


class FinalResultNodeTests(unittest.TestCase):
    def test_prefers_aggregation_sink(self):
        nodes = {
            "t1": {"kind": "work", "deps": []},
            "t2": {"kind": "work", "deps": []},
            "synth": {"kind": "synthesize", "deps": ["t1", "t2"]},
        }
        results = {k: {"status": "done"} for k in nodes}
        self.assertEqual(kf._final_result_nodes(nodes, results), ["synth"])

    def test_falls_back_to_sinks_without_agg_kind(self):
        # 末端が work のみ → 集約 kind が無いので末端ノードを返す
        nodes = {
            "a": {"kind": "work", "deps": []},
            "b": {"kind": "work", "deps": ["a"]},
        }
        results = {k: {"status": "done"} for k in nodes}
        self.assertEqual(kf._final_result_nodes(nodes, results), ["b"])

    def test_falls_back_when_agg_node_not_done(self):
        # 集約ノードが未完了なら done の末端へフォールバック
        nodes = {
            "t1": {"kind": "work", "deps": []},
            "synth": {"kind": "synthesize", "deps": ["t1"]},
        }
        results = {"t1": {"status": "done"}, "synth": {"status": "pending"}}
        self.assertEqual(kf._final_result_nodes(nodes, results), ["t1"])

    def test_empty_when_nothing_done(self):
        self.assertEqual(
            kf._final_result_nodes({"t1": {"kind": "work", "deps": []}},
                                   {"t1": {"status": "pending"}}), [])
        self.assertEqual(kf._final_result_nodes({}, {}), [])


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

    def test_cleanup_clone_removes_worktree(self):
        # 作業後にクローン（.git を含む作業ツリー）を丸ごと削除できる。
        clone = os.path.join(self.clones, "to-remove")
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
        bus.cleanup_clone()
        self.assertFalse(os.path.exists(clone))  # クローンごと消える
        bus.cleanup_clone()  # 既に無くても安全（冪等）

    def test_clone_inside_parent_repo_does_not_touch_parent(self):
        # クローン先が親リポジトリの作業ツリー配下にあっても、sparse-checkout が親へ波及しない。
        # 親リポジトリを用意し、その配下にバス用クローンを作る。
        parent = tempfile.mkdtemp(prefix="kf-parent-")
        subprocess.run(["git", "-C", parent, "init", "-q"], check=True, capture_output=True)
        for name in ("keepA", "keepB"):
            with open(os.path.join(parent, name), "w") as f:
                f.write("x")
        for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "p"]):
            subprocess.run(["git", "-C", parent] + c, check=True, capture_output=True)
        clone = os.path.join(parent, "bus", "node")     # 親リポジトリの作業ツリー配下
        kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        # クローンは自分自身の .git を持ち、親の作業ツリーは無傷（sparse で隠されない）
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
        self.assertTrue(os.path.exists(os.path.join(parent, "keepA")))
        self.assertTrue(os.path.exists(os.path.join(parent, "keepB")))
        # 親リポジトリに sparse-checkout が設定されていない（cone 化していない）
        cfg = subprocess.run(["git", "-C", parent, "config", "--get", "core.sparseCheckout"],
                             capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(cfg, "true")

    def test_reuse_full_checkout_of_same_remote_is_refused(self):
        # 同一 remote の既存フルチェックアウト（ユーザーの作業リポジトリ等）を --bus に指定しても、
        # sparse-checkout で subdir 以外の追跡ファイルを隠さず、上書きせず中断する。
        seed = tempfile.mkdtemp(prefix="kf-seed-")
        subprocess.run(["git", "clone", "-q", self.bare, seed], check=True, capture_output=True)
        for d in ("flow", "src", "docs"):
            os.makedirs(os.path.join(seed, d))
            with open(os.path.join(seed, d, "f.txt"), "w") as f:
                f.write("x")
        for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "s"],
                  ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", seed] + c, check=True, capture_output=True)
        userwork = tempfile.mkdtemp(prefix="kf-userwork-")
        subprocess.run(["git", "clone", "-q", self.bare, userwork], check=True, capture_output=True)
        before = set(os.listdir(userwork))
        self.assertEqual(before, {".git", "flow", "src", "docs"})
        with self.assertRaises(RuntimeError):
            kf.GitBus(userwork, "run1", remote=self.bare, branch="main", subdir="flow")
        # 追跡ファイルは隠されず、作業ツリーは無傷
        self.assertEqual(set(os.listdir(userwork)), before)
        sparse = subprocess.run(["git", "-C", userwork, "config", "--get", "core.sparseCheckout"],
                                capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(sparse.lower(), "true")

    def test_managed_bus_clone_is_reused(self):
        # 自前で作ったバスクローン（目印つき）は二度目以降そのまま再利用される（中断しない）。
        clone = os.path.join(self.clones, "managed")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main", subdir="flow")
        marker = subprocess.run(["git", "-C", clone, "config", "--get", "kiro-flow.busclone"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(marker, "1")
        kf.GitBus(clone, "run2", remote=self.bare, branch="main", subdir="flow")  # 再利用で例外なし

    def test_clone_into_foreign_nonempty_dir_is_refused(self):
        # 別リポジトリの非空ディレクトリを誤ってバスのクローン先に指定したら、上書きせず中断する。
        foreign = tempfile.mkdtemp(prefix="kf-foreign-")
        subprocess.run(["git", "-C", foreign, "init", "-q"], check=True, capture_output=True)
        with open(os.path.join(foreign, "important.txt"), "w") as f:
            f.write("do not touch")
        with self.assertRaises(RuntimeError):
            kf.GitBus(foreign, "run1", remote=self.bare, branch="main")
        # 既存ファイルは無傷
        self.assertTrue(os.path.exists(os.path.join(foreign, "important.txt")))

    def test_make_bus_cleanup_removes_active_clones(self):
        # make_bus で作ったクローンは cleanup_active_clones でまとめて削除される。
        kf._active_clones.clear()
        self.addCleanup(kf._active_clones.clear)
        args = mock.Mock(bus=self.clones, run_id="run1", git=self.bare,
                         git_branch="main", git_subdir="")
        bus = kf.make_bus(args, "node-x")
        self.assertIn(bus, kf._active_clones)
        self.assertTrue(os.path.isdir(bus.workdir))
        kf.cleanup_active_clones()
        self.assertFalse(os.path.exists(bus.workdir))
        self.assertEqual(kf._active_clones, [])


class CleanupTests(unittest.TestCase):
    """一時ファイルの自動クリーンアップ（A: ロック / B: 中間 .tmp / C: 孤立クローン）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-cleanup-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _old(self, path, age_sec):
        t = time.time() - age_sec
        os.utime(path, (t, t))

    def test_sweep_tmp_dead_pid(self):
        # 死んだ pid の <path>.tmp.<pid> は消す。生存 pid の新しいものは残す。
        d = os.path.join(self.tmp, "runs", "r1")
        os.makedirs(d)
        dead = os.path.join(d, "meta.json.tmp.999999")
        alive = os.path.join(d, "meta.json.tmp.%d" % os.getpid())
        normal = os.path.join(d, "meta.json")
        for p in (dead, alive, normal):
            with open(p, "w") as f:
                f.write("{}")
        removed = kf.sweep_tmp_files(self.tmp, min_age_sec=300.0)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(dead))
        self.assertTrue(os.path.exists(alive))   # 生存 pid かつ新しい → 残す
        self.assertTrue(os.path.exists(normal))  # 中間でない通常ファイルは対象外

    def test_sweep_tmp_old_alive_pid(self):
        # 生存 pid でも min_age を超えて古ければクラッシュ残骸とみなし消す。
        d = os.path.join(self.tmp, "runs")
        os.makedirs(d)
        old = os.path.join(d, "graph.json.tmp.%d" % os.getpid())
        with open(old, "w") as f:
            f.write("{}")
        self._old(old, 600)
        self.assertEqual(kf.sweep_tmp_files(self.tmp, min_age_sec=300.0), 1)
        self.assertFalse(os.path.exists(old))

    def test_sweep_tmp_skips_git_internals(self):
        # .git 配下は走査しない（git の内部一時ファイルに触れない）。
        g = os.path.join(self.tmp, ".git", "objects")
        os.makedirs(g)
        inside = os.path.join(g, "x.tmp.999999")
        with open(inside, "w") as f:
            f.write("x")
        self.assertEqual(kf.sweep_tmp_files(self.tmp), 0)
        self.assertTrue(os.path.exists(inside))

    def test_sweep_lock_unused_old(self):
        # 古くて誰も保持していないロックは消す。新しいロックは残す。
        d = kf._locks_root()
        os.makedirs(d, exist_ok=True)
        old = os.path.join(d, "kf-test-old.lock")
        fresh = os.path.join(d, "kf-test-fresh.lock")
        for p in (old, fresh):
            with open(p, "w") as f:
                f.write("")
        self.addCleanup(lambda: [os.path.exists(p) and os.remove(p) for p in (old, fresh)])
        self._old(old, 7200)
        removed = kf.sweep_lock_files(min_age_sec=3600.0)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))  # 新しい → 残す
        self.assertGreaterEqual(removed, 1)

    @unittest.skipIf(kf.fcntl is None, "flock 非対応環境")
    def test_sweep_lock_held_is_kept(self):
        # 保持中（flock 中）の古いロックは消さない。
        d = kf._locks_root()
        os.makedirs(d, exist_ok=True)
        held = os.path.join(d, "kf-test-held.lock")
        f = open(held, "w")
        self.addCleanup(lambda: (f.close(), os.path.exists(held) and os.remove(held)))
        kf.fcntl.flock(f, kf.fcntl.LOCK_EX)
        self._old(held, 7200)
        kf.sweep_lock_files(min_age_sec=3600.0)
        self.assertTrue(os.path.exists(held))  # 保持中 → 残す

    def test_sweep_clone_dirs(self):
        # .git を持つ古い孤立クローンは消す。新しいクローンと keep 対象・非クローンは残す。
        parent = self.tmp
        for name in ("orchestrator-r1", "daemon-self", "worker-w1"):
            os.makedirs(os.path.join(parent, name, ".git"))
        plain = os.path.join(parent, "runs")  # .git を持たない → 触らない
        os.makedirs(plain)
        self._old(os.path.join(parent, "orchestrator-r1"), 100000)
        self._old(os.path.join(parent, "orchestrator-r1", ".git"), 100000)
        removed = kf.sweep_clone_dirs(parent, keep_basename="daemon-self",
                                      min_age_sec=3600.0)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(os.path.join(parent, "orchestrator-r1")))
        self.assertTrue(os.path.exists(os.path.join(parent, "daemon-self")))  # keep
        self.assertTrue(os.path.exists(os.path.join(parent, "worker-w1")))    # 新しい
        self.assertTrue(os.path.exists(plain))  # 非クローン

    def test_run_cleanup_local_skips_clones(self):
        # ローカルバス（--git なし）では孤立クローン掃除は走らない（クローンが無い）。
        args = mock.Mock(bus=self.tmp, lease=1800.0, git=None, cleanup_age=24.0)
        bus = kf.Bus(self.tmp, "_")
        res = kf.run_cleanup(args, bus)
        self.assertEqual(res["clones"], 0)
        self.assertIn("locks", res)
        self.assertIn("tmp", res)


class ArtifactProtocolTests(unittest.TestCase):
    """中間成果物のファイル参照プロトコル（依存タスクの成果物を決定的パスで受け渡す）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-art-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")

    def test_node_artifact_dir_is_deterministic(self):
        # 同じ node-id なら別 Bus ビューでも同じ run 相対パスを指す（後続が発見できる）
        d1 = self.bus.node_artifact_dir("t1")
        d2 = kf.Bus(self.tmp, "run1").node_artifact_dir("t1")
        self.assertEqual(d1, d2)
        self.assertEqual(os.path.relpath(d1, self.bus.run_dir),
                         os.path.join("artifacts", "t1"))

    def test_ensure_and_list_artifacts(self):
        d = self.bus.ensure_artifact_dir("t1")
        self.assertTrue(os.path.isdir(d))
        with open(os.path.join(d, "out.bin"), "w") as f:
            f.write("payload")
        self.assertEqual(self.bus.list_artifacts("t1"), [os.path.join(d, "out.bin")])
        self.assertEqual(self.bus.list_artifacts("missing"), [])  # 無ければ空

    def test_write_result_records_artifacts(self):
        self.bus.write_result("t1", "w", "done", "out", artifacts=["artifacts/t1/out.bin"])
        self.assertEqual(self.bus.read_result("t1")["artifacts"], ["artifacts/t1/out.bin"])
        # artifacts 無しなら後方互換でキーを足さない
        self.bus.write_result("t2", "w", "done", "out")
        self.assertNotIn("artifacts", self.bus.read_result("t2"))

    def test_artifact_instruction_lists_self_and_deps(self):
        dep_dir = self.bus.ensure_artifact_dir("dep1")
        with open(os.path.join(dep_dir, "data.json"), "w") as f:
            f.write("{}")
        self_dir = self.bus.node_artifact_dir("t2")
        note = kf.artifact_instruction(self_dir, {"dep1": dep_dir})
        self.assertIn(self_dir, note)            # 出力先を案内
        self.assertIn(dep_dir, note)             # 依存の成果物パスを案内
        self.assertIn("data.json", note)         # 依存ディレクトリ内のファイル名も
        # 依存ディレクトリが空（成果物なし）なら依存欄は出さない
        empty = kf.artifact_instruction(self_dir, {"dep1": self.bus.node_artifact_dir("nope")})
        self.assertNotIn("依存タスクの成果物", empty)

    def test_artifact_instruction_empty_when_nothing(self):
        self.assertEqual(kf.artifact_instruction(None, None), "")

    def test_execute_kiro_prompt_references_dep_artifacts_by_path(self):
        # execute_kiro は依存成果物の中身を本文に貼らず、パスを示してファイル参照させる
        dep_dir = self.bus.ensure_artifact_dir("dep1")
        with open(os.path.join(dep_dir, "big.txt"), "w") as f:
            f.write("X" * 100)
        captured = {}

        def fake(prompt, model):
            captured["prompt"] = prompt
            return "ok"

        with mock.patch.object(kf, "run_kiro", side_effect=fake):
            kf.execute_kiro("work", "後続処理", {}, None,
                            self.bus.node_artifact_dir("t2"), {"dep1": dep_dir})
        self.assertIn("中間成果物プロトコル", captured["prompt"])
        self.assertIn(dep_dir, captured["prompt"])
        self.assertIn("big.txt", captured["prompt"])
        self.assertNotIn("X" * 100, captured["prompt"])  # 中身は貼らない（参照のみ）

    def test_worker_records_artifacts_in_result(self):
        # ワーカーが実行中に書いた成果物を result に記録し、後続が発見できる
        bus = self.bus
        bus.write_graph({"nodes": {"t1": {"goal": "g", "deps": [], "kind": "work"}},
                         "iteration": 0})
        bus.write_task({"id": "t1", "goal": "g", "deps": [], "kind": "work"})
        bus.set_status("running")

        def fake_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None):
            with open(os.path.join(art_dir, "result.bin"), "w") as f:
                f.write("done")
            return "ok", None

        args = mock.Mock(bus=self.tmp, run_id="run1", git=None, node_id="w1",
                         executor="stub", model=None, lease=60, poll=0,
                         keep_alive=False, idle_exit=True)
        with mock.patch.object(kf, "execute_stub", side_effect=fake_exec), \
             mock.patch.object(kf, "make_bus", return_value=bus):
            kf.cmd_work(args)
        r = bus.read_result("t1")
        self.assertEqual(r["status"], "done")
        self.assertIn(os.path.join("artifacts", "t1", "result.bin"), r["artifacts"])


class ArgvLimitTests(unittest.TestCase):
    """大きなプロンプトをコマンドライン長制限で落とさず、一時ファイル参照に切り替える。"""

    def test_argv_limit_from_config(self):
        import argparse
        # 解決済み設定値（argv_limit）はモジュール変数へ確定し、free 関数が参照する
        orig = kf._ARGV_LIMIT
        self.addCleanup(setattr, kf, "_ARGV_LIMIT", orig)
        kf._configure_thresholds(argparse.Namespace(argv_limit=123))
        self.assertEqual(kf._kiro_argv_limit(), 123)
        kf._configure_thresholds(argparse.Namespace(argv_limit=None))  # 未指定は据え置き
        self.assertEqual(kf._kiro_argv_limit(), 123)
        kf._ARGV_LIMIT = 0  # 0/不正は組み込み既定へフォールバック
        self.assertEqual(kf._kiro_argv_limit(), kf.CONFIG_DEFAULTS["argv_limit"])

    def test_argv_limit_resolved_from_config_file(self):
        # 設定ファイルの argv_limit が resolve_config 経由で args に載る（env 非依存）
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "kiro-flow.json")
        with open(cfg, "w") as f:
            json.dump({"argv_limit": 4096}, f)
        args = argparse.Namespace(config=cfg, argv_limit=None)
        kf.resolve_config(args)
        self.assertEqual(args.argv_limit, 4096)

    def test_gitlab_block_resolved_from_config_file(self):
        # 設定ファイルの gitlab: ブロック（repo_url 含む）が args.gitlab に載り、_config_path も確定する。
        # これで --config を渡された worker が repo_url を gl.py へ伝えられる（GL_PROJECT_URL）。
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "kiro-flow.json")
        with open(cfg, "w") as f:
            json.dump({"gitlab": {"repo_url": "https://gitlab.com/grp/repo"}}, f)
        args = argparse.Namespace(config=cfg, gitlab=None)
        kf.resolve_config(args)
        self.assertEqual(args.gitlab.get("repo_url"), "https://gitlab.com/grp/repo")
        self.assertEqual(args._config_path, cfg)
        # make_executor がこの gitlab ブロックを KIRO_FLOW_EXECUTOR_CONFIG へ載せる
        prev = os.environ.get("KIRO_FLOW_EXECUTOR_CONFIG")
        self.addCleanup(lambda: os.environ.__setitem__("KIRO_FLOW_EXECUTOR_CONFIG", prev)
                        if prev is not None else os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None))
        kf.make_executor(argparse.Namespace(executor="gitlab", gitlab=args.gitlab))
        self.assertEqual(json.loads(os.environ["KIRO_FLOW_EXECUTOR_CONFIG"]).get("repo_url"),
                         "https://gitlab.com/grp/repo")

    def test_child_spawn_propagates_config(self):
        # run/daemon が子（orchestrator/worker）へ --config を引き継ぐ。これが無いと worker は設定を
        # 再解決できず gitlab.repo_url が既定（空）になり、起票先が git origin にフォールバックする。
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "kiro-flow.json")
        with open(cfg, "w") as f:
            json.dump({"executor": "gitlab", "gitlab": {"repo_url": "https://gitlab.com/grp/repo"}}, f)
        bus = tempfile.mkdtemp(prefix="kf-bus-")
        self.addCleanup(shutil.rmtree, bus, ignore_errors=True)
        spawned = []

        class _FakePopen:
            def __init__(self, cmd, *a, **k):
                spawned.append(cmd)
            def poll(self): return 0
            def wait(self, *a, **k): return 0
            def terminate(self): pass

        base_args = dict(config=cfg, bus=bus, git=None, git_branch="main", git_subdir=None,
                         lease=30.0, run_id="run-x", workers=1, request="x", planner="stub",
                         executor=None, model=None, poll=0.01, max_iterations=1, max_fanout=4,
                         max_retries=1, review=None, granularity="finest", exemplar_first=False,
                         cleanup_clone=True, repos=None, keep_clone=False)
        args = argparse.Namespace(**base_args)
        kf.resolve_config(args)   # executor=gitlab / gitlab block / _config_path を確定
        with mock.patch.object(kf.subprocess, "Popen", _FakePopen), \
             mock.patch.object(kf, "make_bus", lambda *a, **k: mock.Mock()):
            try:
                kf.cmd_run(args)
            except Exception:
                pass  # bus/poll をモックしているので途中で抜けてよい（spawn コマンドだけ検証）
        self.assertTrue(spawned, "子プロセスが起動されていない")
        for cmd in spawned:
            self.assertIn("--config", cmd)
            self.assertEqual(cmd[cmd.index("--config") + 1], os.path.abspath(cfg))

    def test_small_prompt_passed_inline(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_kiro("短いプロンプト", None)
        self.assertIn("短いプロンプト", seen["cmd"])  # そのまま argv に乗る

    def test_large_prompt_spilled_to_tempfile(self):
        big = "依存成果物" + "X" * 200000  # argv 長制限を超える巨大プロンプト
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            # 退避ファイルへのパスが argv 末尾に入り、実行中はその中身が読めること
            path = cmd[-1].split(": ")[-1]
            seen["spill_path"] = path
            with open(path, encoding="utf-8") as f:
                seen["spill_body"] = f.read()
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_kiro(big, None)
        # 巨大プロンプト本体は argv に乗らない（コマンドライン長制限を回避）
        self.assertNotIn(big, seen["cmd"])
        self.assertLess(len(seen["cmd"][-1]), 500)
        self.assertEqual(seen["spill_body"], big)          # ファイルには全文がある
        self.assertFalse(os.path.exists(seen["spill_path"]))  # 実行後に掃除される


class CircuitBreakerTests(unittest.TestCase):
    """judge/評価役のサーキットブレーカー: 達成不可能な完了条件で無限に再タスクを積まない。"""

    def test_node_entry_preserves_retries(self):
        e = kf._node_entry({"id": "x", "goal": "g", "deps": [], "kind": "verify", "retries": 2})
        self.assertEqual(e["retries"], 2)
        e0 = kf._node_entry({"id": "y", "goal": "g", "deps": [], "kind": "work"})
        self.assertNotIn("retries", e0)  # 0/未指定は持たない（ノイズを足さない）

    def test_verify_fail_increments_retries(self):
        nodes = {"gen1": {"goal": "FLAKY", "deps": [], "kind": "generate"},
                 "v1": {"goal": "検証", "deps": ["gen1"], "kind": "verify"}}
        results = {"gen1": {"status": "done", "output": "issue"},
                   "v1": {"status": "done", "output": "verify=fail"}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, max_retries=3)
        by = {t["id"]: t for t in new}
        self.assertEqual(by["v1-r1"]["retries"], 1)
        self.assertEqual(by["gen1-r1"]["retries"], 1)

    def test_circuit_breaker_stops_verify_retries_at_cap(self):
        # retries が上限に達した verify-fail は作り直しを生成せず done で打ち切る
        nodes = {"gen1": {"goal": "g", "deps": [], "kind": "generate", "retries": 3},
                 "v1": {"goal": "検証", "deps": ["gen1"], "kind": "verify", "retries": 3}}
        results = {"gen1": {"status": "done", "output": "issue"},
                   "v1": {"status": "done", "output": "verify=fail"}}
        decision, new, reason = kf.continue_stub("req", nodes, results, 5, max_retries=3)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])
        self.assertIn("サーキットブレーカー", reason)

    def test_circuit_breaker_stops_failed_task_retries_at_cap(self):
        nodes = {"t2": {"goal": "FAIL", "deps": [], "kind": "work", "retries": 3}}
        results = {"t2": {"status": "failed"}}
        decision, new, reason = kf.continue_stub("req", nodes, results, 5, max_retries=3)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])
        self.assertIn("サーキットブレーカー", reason)

    def test_failed_retry_below_cap_still_retries(self):
        nodes = {"t2": {"goal": "FAIL", "deps": [], "kind": "work", "retries": 1}}
        results = {"t2": {"status": "failed"}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0, max_retries=3)
        self.assertEqual(decision, "replan")
        self.assertEqual(new[0]["id"], "t2r")
        self.assertEqual(new[0]["retries"], 2)

    def test_retry_depth_from_id_chain(self):
        self.assertEqual(kf._retry_depth("gen1", {}), 0)
        self.assertEqual(kf._retry_depth("gen1-r1", {}), 1)
        self.assertEqual(kf._retry_depth("gen1-r1-r2", {}), 2)
        self.assertEqual(kf._retry_depth("x", {"retries": 4}), 4)  # 明示カウンタ優先

    def test_continue_kiro_circuit_breaker_short_circuits(self):
        # 評価役 LLM を呼ぶ前に、上限到達の系統を検知して done で打ち切る（LLM 不要）
        nodes = {"v1-r1-r2-r3": {"goal": "検証", "deps": [], "kind": "verify"}}
        results = {"v1-r1-r2-r3": {"status": "done", "output": "verify=fail"}}
        with mock.patch.object(kf, "run_kiro",
                               side_effect=AssertionError("LLM を呼んではいけない")):
            decision, new, reason = kf.continue_kiro("req", nodes, results, 9, max_retries=3)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])
        self.assertIn("サーキットブレーカー", reason)


class _Args:
    """doctor の決定的チェック/シグナル/cmd が読む args の最小スタブ。"""
    def __init__(self, bus, **kw):
        self.bus = bus
        self.run_id = None
        self.git = None
        self.git_branch = "main"
        self.git_subdir = ""
        self.model = None
        self.executor = "stub"     # 既定は stub（kiro-cli を要求しない）
        self.planner = "stub"
        self.max_iterations = 3
        self.max_retries = 3
        self.lease = 1800.0
        self.argv_limit = 100000
        self.fix = False
        self.json = False
        self.cleanup_clone = True
        for k, v in kw.items():
            setattr(self, k, v)


class DoctorTests(unittest.TestCase):
    """kiro-flow の稼働診断（doctor）: env/config チェック・シグナル・修正・分類・連携用 JSON。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-doc-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_findings_kiro_cli_and_finite_stop(self):
        args = _Args(os.path.join(self.tmp, "bus"), executor="kiro",
                     max_iterations=0, lease=0)
        fs = kf.doctor_env_findings(args, which=lambda _n: None)
        ids = {f["title"]: f for f in fs}
        self.assertTrue(any("kiro-cli" in t for t in ids))          # executor=kiro → env critical
        self.assertTrue(any("max_iterations" in t for t in ids))    # ≤0 → config critical
        self.assertTrue(any("リース" in t for t in ids))             # lease≤0 → config warn
        # バス未作成は ensure-bus アクション付き
        bus = next(f for f in fs if f.get("fix_action") == "ensure-bus")
        self.assertEqual(bus["category"], "config")

    def test_env_findings_clean_when_ready(self):
        bus = os.path.join(self.tmp, "bus")
        os.makedirs(bus)
        args = _Args(bus)                                            # executor=stub・正の閾値
        fs = kf.doctor_env_findings(args, which=lambda n: "/usr/bin/" + n)
        self.assertEqual(fs, [])                                     # 所見なし

    def test_apply_fix_ensure_bus(self):
        bus = os.path.join(self.tmp, "bus")
        args = _Args(bus)
        msg = kf.apply_doctor_fix(args, {"fix_action": "ensure-bus"})
        self.assertTrue(os.path.isdir(bus))
        self.assertIn("作成", msg)

    def test_signals_flag_stuck_and_failed_runs(self):
        bus_root = os.path.join(self.tmp, "bus")
        b = kf.Bus(bus_root, "runOld")
        b.ensure_run("古い要求")
        b.write_graph({"nodes": {"t1": {"goal": "g", "deps": []}}, "iteration": 4})
        b.write_task({"id": "t1", "goal": "g", "deps": []})
        b.write_result("t1", "w1", "failed", "例外: Traceback ...")
        # 滞留判定のため updated_at を十分過去にする
        meta = kf.read_json(b.meta_path)
        meta["status"] = "running"
        meta["updated_at"] = "2000-01-01T00:00:00Z"
        meta["created_at"] = "2000-01-01T00:00:00Z"
        kf.write_json_atomic(b.meta_path, meta)

        sig = kf.collect_doctor_signals(_Args(bus_root))
        self.assertEqual(sig["runs_total"], 1)
        self.assertTrue(any(s["run"] == "runOld" for s in sig["stuck"]))   # 非終端＋高齢→滞留
        self.assertTrue(any(fl["run"] == "runOld" for fl in sig["failed"]))
        self.assertTrue(any("runOld" == e.get("run") for e in sig["errors"]))

    def test_cmd_doctor_json_and_program_routing(self):
        bus_root = os.path.join(self.tmp, "bus")
        os.makedirs(bus_root)
        args = _Args(bus_root, json=True, fix=True)
        filed = []

        def agent(prompt, model):
            if "稼働診断医" in prompt:
                return ('[{"category":"program","severity":"critical",'
                        '"title":"グラフ生成バグ","evidence":"run","fix":"例外"}]')
            filed.append("file")
            return "起票"

        import io
        import contextlib as _ctx
        buf = io.StringIO()
        with _ctx.redirect_stdout(buf):
            rc = kf.cmd_doctor(args, kiro_run=agent, skill_finder=lambda _n: None)
        # スキルが見つからない → 出力のみ（起票しない）
        self.assertEqual(filed, [])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["tool"], "kiro-flow")
        self.assertTrue(any(f["category"] == "program" for f in out["findings"]))
        self.assertEqual(rc, 2)                                     # 未解決 critical program

    def test_cmd_doctor_files_via_gitlab_idd_when_present(self):
        bus_root = os.path.join(self.tmp, "bus")
        os.makedirs(bus_root)
        args = _Args(bus_root, json=True, fix=True)
        skill = os.path.join(self.tmp, "skills", "gitlab-idd")
        os.makedirs(skill)
        filed = []

        def agent(prompt, model):
            if "稼働診断医" in prompt:
                return ('[{"category":"program","severity":"critical",'
                        '"title":"バグ","evidence":"e","fix":"f"}]')
            filed.append("file")
            return "起票しました"

        import io
        import contextlib as _ctx
        with _ctx.redirect_stdout(io.StringIO()):
            rc = kf.cmd_doctor(args, kiro_run=agent, skill_finder=lambda _n: skill)
        self.assertEqual(filed, ["file"])                          # gitlab-idd へ委譲
        self.assertEqual(rc, 0)                                    # 唯一の所見が起票で解消 → healthy


if __name__ == "__main__":
    unittest.main(verbosity=2)