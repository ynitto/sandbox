"""agent-flow の単体テスト — ユーザー定義フロー（plan）。

投入契約（inbox 要求の `plan` / `--plan-file`）から planner を通さずグラフを固定する
第 3 の計画経路（`plan_strategy_user`）の検証。ビルダー（agent-dashboard）が使う。

    python -m unittest tests.test_user_plan
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・kf ロード・共通ヘルパ）


def _plan(nodes, **extra):
    return {"nodes": nodes, **extra}


class PlanStrategyUserTests(unittest.TestCase):
    def test_patterns_cli_lists_canonical_catalog(self):
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "patterns", "--json"],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = json.loads(p.stdout)
        self.assertEqual({row["id"] for row in rows}, set(kf.PATTERNS))
        self.assertTrue(all(row["label"] and row["description"] for row in rows))
        self.assertTrue(all(row["template"]["nodes"] for row in rows))
        self.assertTrue(all(row["template"]["name"] == row["label"] for row in rows))

    def test_valid_plan_fixed_verbatim(self):
        plan = _plan([
            {"id": "a", "goal": "調査: {{request}}", "kind": "work"},
            {"id": "b", "goal": "candidate", "kind": "generate",
             "agent": {"agent_cli": "ollama", "model": "qwen3.5:9b"}},
            {"id": "v", "goal": "検証", "deps": ["a", "b"], "kind": "verify",
             "dependency_input": "full", "retries": 2},
        ], name="調査フロー")
        strategy, tasks = kf.plan_strategy_user(plan, "X を調べる")
        self.assertEqual(strategy["patterns"], ["user-defined"])
        self.assertTrue(strategy["user_plan"])
        self.assertEqual(strategy["plan_name"], "調査フロー")
        self.assertFalse(strategy["review"])          # gate の自動挿入はしない
        self.assertEqual(strategy["parallelism"], 2)  # 根ノード数
        self.assertNotIn("user_plan_evaluate", strategy)
        by_id = {t["id"]: t for t in tasks}
        self.assertEqual(set(by_id), {"a", "b", "v"})
        self.assertEqual(by_id["a"]["goal"], "調査: X を調べる")  # {{request}} 置換
        # per-node agent（人の明示設定）は planner 経路と違い剥がさない
        self.assertEqual(by_id["b"]["agent"], {"agent_cli": "ollama", "model": "qwen3.5:9b"})
        self.assertEqual(by_id["v"]["deps"], ["a", "b"])
        self.assertEqual(by_id["v"]["dependency_input"], "full")
        self.assertEqual(by_id["v"]["retries"], 2)

    def test_evaluate_flag_enables_evaluator(self):
        strategy, _ = kf.plan_strategy_user(
            _plan([{"id": "a", "goal": "g"}], evaluate=True), "r")
        self.assertTrue(strategy["user_plan_evaluate"])

    def test_agent_model_optional(self):
        _, tasks = kf.plan_strategy_user(
            _plan([{"id": "a", "goal": "g", "agent": {"agent_cli": "codex"}}]), "r")
        self.assertEqual(tasks[0]["agent"], {"agent_cli": "codex"})

    def test_tier_kept_on_nodes_and_graph_entries(self):
        # 固定実行レベル（tier）は plan から剥がさない——pinned-tier の記録と
        # 手法判定（when.tiers のノード tier 優先）が読む。graph のノード entry へも運ぶ。
        _, tasks = kf.plan_strategy_user(_plan([
            {"id": "a", "goal": "g", "tier": "large",
             "agent": {"agent_cli": "codex", "model": "gpt-5"}},
            {"id": "b", "goal": "h", "tier": "basic", "kind": "extract", "deps": ["a"]},
            {"id": "c", "goal": "i", "deps": ["a"]},
        ]), "r")
        by_id = {t["id"]: t for t in tasks}
        self.assertEqual(by_id["a"]["tier"], "large")
        self.assertEqual(by_id["b"]["tier"], "basic")
        self.assertNotIn("tier", by_id["c"])  # auto（継承）は tier を持たない
        entry = kf._node_entry(by_id["a"])
        self.assertEqual(entry["tier"], "large")
        self.assertNotIn("tier", kf._node_entry(by_id["c"]))

    def test_human_rejects_tier(self):
        with self.assertRaises(kf.UserPlanError):
            kf.plan_strategy_user(_plan([{
                "id": "review", "goal": "確認", "kind": "human", "tier": "small",
                "interaction": {"mode": "approval", "prompt": "進めますか",
                                "audience": ["reviewer"]},
            }]), "r")

    def test_human_plan_preserves_interaction_without_agent(self):
        _, tasks = kf.plan_strategy_user(_plan([{
            "id": "review", "goal": "人の確認を待つ", "kind": "human",
            "interaction": {"mode": "approval", "prompt": "次へ進めますか", "audience": ["reviewer"]},
        }]), "r")
        self.assertEqual(tasks[0]["interaction"], {
            "mode": "approval", "prompt": "次へ進めますか", "audience": ["reviewer"],
            "timeout_seconds": 604800,
        })
        self.assertNotIn("agent", tasks[0])

    def test_rejects_invalid_plans(self):
        # 丸めず失敗させる（planner の _coerce_tasks と逆の方針）ことを網羅的に固定する
        cases = {
            "plan が dict でない": ([], "r"),
            "nodes 無し": ({}, "r"),
            "nodes 空": (_plan([]), "r"),
            "id 無し": (_plan([{"goal": "g"}]), "r"),
            "id 重複": (_plan([{"id": "a", "goal": "g"}, {"id": "a", "goal": "h"}]), "r"),
            "goal 空": (_plan([{"id": "a", "goal": "  "}]), "r"),
            "kind 不正": (_plan([{"id": "a", "goal": "g", "kind": "nope"}]), "r"),
            "未知依存": (_plan([{"id": "a", "goal": "g", "deps": ["zz"]}]), "r"),
            "自己依存": (_plan([{"id": "a", "goal": "g", "deps": ["a"]}]), "r"),
            "循環": (_plan([{"id": "a", "goal": "g", "deps": ["b"]},
                            {"id": "b", "goal": "h", "deps": ["a"]}]), "r"),
            "agent に agent_cli 無し": (_plan([{"id": "a", "goal": "g", "agent": {"model": "m"}}]), "r"),
            "retries 非数値": (_plan([{"id": "a", "goal": "g", "retries": "abc"}]), "r"),
            "split への静的依存": (_plan([{"id": "s", "goal": "g", "kind": "split"},
                                          {"id": "b", "goal": "h", "deps": ["s"]}]), "r"),
        }
        for label, (plan, req) in cases.items():
            with self.assertRaises(kf.UserPlanError, msg=label):
                kf.plan_strategy_user(plan, req)

    def test_rejects_oversized_plan(self):
        nodes = [{"id": f"n{i}", "goal": "g"} for i in range(65)]
        with self.assertRaises(kf.UserPlanError):
            kf.plan_strategy_user(_plan(nodes), "r")


class UserPlanBusTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kf-userplan-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_submit_request_carries_plan(self):
        bus = kf.Bus(self.root, "req-1")
        os.makedirs(bus.inbox_dir, exist_ok=True)
        plan = _plan([{"id": "a", "goal": "g"}])
        bus.submit_request("req-1", "r", "tester", plan=plan)
        rec = bus.read_inbox("req-1")
        self.assertEqual(rec["plan"], plan)

    def test_submit_request_carries_pattern(self):
        bus = kf.Bus(self.root, "req-pattern")
        os.makedirs(bus.inbox_dir, exist_ok=True)
        bus.submit_request("req-pattern", "r", "tester", pattern="map-reduce")
        self.assertEqual(bus.read_inbox("req-pattern")["pattern"], "map-reduce")

    def test_submit_request_ignores_empty_plan(self):
        bus = kf.Bus(self.root, "req-2")
        os.makedirs(bus.inbox_dir, exist_ok=True)
        bus.submit_request("req-2", "r", "tester", plan={"nodes": []})
        self.assertNotIn("plan", bus.read_inbox("req-2"))

    def test_read_user_plan_prefers_plan_file_over_inbox(self):
        bus = kf.Bus(self.root, "req-3")
        os.makedirs(bus.inbox_dir, exist_ok=True)
        bus.submit_request("req-3", "r", "tester",
                           plan=_plan([{"id": "inbox", "goal": "g"}]))
        pf = os.path.join(self.root, "plan.json")
        pathlib.Path(pf).write_text(json.dumps(_plan([{"id": "file", "goal": "g"}])),
                                    encoding="utf-8")
        args = argparse.Namespace(run_id="req-3", plan_file=pf)
        self.assertEqual(kf._read_user_plan(bus, args)["nodes"][0]["id"], "file")
        args.plan_file = None
        self.assertEqual(kf._read_user_plan(bus, args)["nodes"][0]["id"], "inbox")

    def test_read_user_plan_broken_file_raises(self):
        bus = kf.Bus(self.root, "req-4")
        pf = os.path.join(self.root, "broken.json")
        pathlib.Path(pf).write_text("{not json", encoding="utf-8")
        with self.assertRaises(kf.UserPlanError):
            kf._read_user_plan(bus, argparse.Namespace(run_id="req-4", plan_file=pf))

    def test_read_user_plan_absent_returns_none(self):
        bus = kf.Bus(self.root, "req-5")
        self.assertIsNone(
            kf._read_user_plan(bus, argparse.Namespace(run_id="req-5", plan_file=None)))


class UserPlanContinueTests(unittest.TestCase):
    """評価役の継続判断: ユーザー定義フローは既定で再計画しない。"""

    def _args(self):
        return argparse.Namespace(executor="stub", max_fanout=50, review="auto",
                                  max_retries=3, exemplar_first=False)

    def test_done_when_quiesced_without_failures(self):
        decision, tasks, _ = kf._continue(
            self._args(), None, "r", {"a": {"goal": "g", "deps": [], "kind": "work"}},
            {"a": {"status": "done"}}, 0, {"user_plan": True})
        self.assertEqual((decision, tasks), ("done", []))

    def test_failed_nodes_fail_the_run_honestly(self):
        decision, tasks, reason = kf._continue(
            self._args(), None, "r", {"a": {"goal": "g", "deps": [], "kind": "work"}},
            {"a": {"status": "failed", "output": "x"}}, 0, {"user_plan": True})
        self.assertEqual((decision, tasks), ("failed", []))
        self.assertIn("a", reason)

    def test_evaluate_flag_falls_through_to_normal_continuation(self):
        with mock.patch.object(kf, "continue_stub",
                               return_value=("done", [], "stub")) as cs:
            decision, _, _ = kf._continue(
                self._args(), None, "r", {"a": {"goal": "g", "deps": [], "kind": "work"}},
                {"a": {"status": "done"}}, 0,
                {"user_plan": True, "user_plan_evaluate": True})
        self.assertTrue(cs.called)
        self.assertEqual(decision, "done")


class UserPlanEndToEndTests(unittest.TestCase):
    """黒箱 e2e: --plan-file / inbox 経由の plan が planner を通さず実行されること。"""

    def _bus(self):
        bus = tempfile.mkdtemp(prefix="kf-userplan-e2e-")
        self.addCleanup(shutil.rmtree, bus, ignore_errors=True)
        return bus

    def _graph(self, bus):
        run_id = sorted(os.listdir(os.path.join(bus, "runs")))[0]
        run_dir = os.path.join(bus, "runs", run_id)
        return (run_id, kf.read_json(os.path.join(run_dir, "graph.json")),
                kf.read_json(os.path.join(run_dir, "final.json")),
                kf.read_json(os.path.join(run_dir, "meta.json")))

    def test_plan_file_run_executes_user_graph(self):
        bus = self._bus()
        plan = _plan([
            {"id": "left", "goal": "左: {{request}}", "kind": "work"},
            {"id": "right", "goal": "右: {{request}}", "kind": "work"},
            {"id": "merge", "goal": "統合", "deps": ["left", "right"], "kind": "synthesize"},
        ], name="二股統合")
        pf = os.path.join(bus, "plan.json")
        pathlib.Path(pf).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "run", "対象を調べる",
             "--plan-file", pf, "--workers", "2", "--planner", "stub",
             "--executor", "stub", "--poll", "0.2"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        _, graph, final, _meta = self._graph(bus)
        self.assertEqual(set(graph["nodes"]), {"left", "right", "merge"})
        self.assertEqual(graph["strategy"]["patterns"], ["user-defined"])
        self.assertEqual(graph["strategy"]["plan_name"], "二股統合")
        self.assertEqual(graph["nodes"]["left"]["goal"], "左: 対象を調べる")
        for nid, r in final["results"].items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")

    def test_explicit_pattern_run_uses_selected_pattern(self):
        bus = self._bus()
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "run", "対象を実装する",
             "--pattern", "adversarial-verification", "--workers", "2",
             "--planner", "stub", "--executor", "stub", "--poll", "0.2"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        _, graph, final, _meta = self._graph(bus)
        self.assertEqual(graph["strategy"]["patterns"], ["adversarial-verification"])
        self.assertEqual(set(graph["nodes"]), {"gen1", "verify1"})
        self.assertTrue(all(r["status"] == "done" for r in final["results"].values()))

    def test_inbox_plan_reaches_orchestrator_without_argv(self):
        bus = self._bus()
        req_id = "req-userplan-1"
        b = kf.Bus(bus, req_id)
        os.makedirs(b.inbox_dir, exist_ok=True)
        b.submit_request(req_id, "inbox 経由", "tester", plan=_plan(
            [{"id": "solo", "goal": "単独: {{request}}", "kind": "work"}]))
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "--run-id", req_id, "run",
             "--from-inbox", "--workers", "1", "--planner", "stub",
             "--executor", "stub", "--poll", "0.2"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        _, graph, final, _meta = self._graph(bus)
        self.assertEqual(set(graph["nodes"]), {"solo"})
        self.assertEqual(graph["nodes"]["solo"]["goal"], "単独: inbox 経由")
        self.assertEqual(final["results"]["solo"]["status"], "done")

    def test_inbox_pattern_reaches_orchestrator(self):
        bus = self._bus()
        req_id = "req-pattern-1"
        b = kf.Bus(bus, req_id)
        os.makedirs(b.inbox_dir, exist_ok=True)
        b.submit_request(req_id, "inbox 経由", "tester", pattern="adversarial-verification")
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "--run-id", req_id, "run",
             "--from-inbox", "--workers", "2", "--planner", "stub",
             "--executor", "stub", "--poll", "0.2"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        _, graph, final, _meta = self._graph(bus)
        self.assertEqual(graph["strategy"]["patterns"], ["adversarial-verification"])
        self.assertTrue(all(r["status"] == "done" for r in final["results"].values()))

    def test_invalid_plan_fails_run_without_fallback(self):
        bus = self._bus()
        pf = os.path.join(bus, "plan.json")
        pathlib.Path(pf).write_text(json.dumps(_plan(
            [{"id": "a", "goal": "g", "deps": ["missing"]}])), encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "run", "r",
             "--plan-file", pf, "--workers", "1", "--planner", "stub",
             "--executor", "stub", "--poll", "0.2"],
            capture_output=True, text=True, timeout=90)
        self.assertNotEqual(p.returncode, 0)
        _, graph, final, meta = self._graph(bus)
        self.assertIn(meta.get("status"), ("failed",))
        self.assertIn("[user-plan]", str(meta.get("failure_reason", "")))
        # planner へフォールバックしていない（グラフが作られていない）
        self.assertFalse((graph or {}).get("nodes"))
        self.assertIn("[user-plan]", str((final or {}).get("failure_reason", "")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
