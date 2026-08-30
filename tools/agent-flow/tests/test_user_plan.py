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

    def test_readonly_kept_on_nodes_and_graph_entries(self):
        _, tasks = kf.plan_strategy_user(_plan([
            {"id": "design", "goal": "設計する", "kind": "work", "readonly": True},
        ]), "r")
        self.assertIs(tasks[0]["readonly"], True)
        self.assertIs(kf._node_entry(tasks[0])["readonly"], True)

    def test_human_rejects_readonly_even_when_false(self):
        with self.assertRaises(kf.UserPlanError):
            kf.plan_strategy_user(_plan([{
                "id": "approve", "goal": "承認", "kind": "human", "readonly": False,
                "interaction": {"mode": "approval", "prompt": "進めますか"},
            }]), "r")

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


class UserPlanTierReviewTests(unittest.TestCase):
    """review の三値解決 × 実行 tier — カスタムフローへの tier 補償（G2）。
    "user-defined" は AGGREGATING_PATTERNS に含まれないため、basic 以外の auto は
    従来どおり False（後方互換）。basic のときだけ verify gate が動的 fan-out に入る。"""

    def _split_plan(self):
        return _plan([{"id": "s", "goal": "分解: {{request}}", "kind": "split"}])

    def test_default_non_basic_stays_false(self):
        # 既定（review 未指定）× 非 basic → 今日と同じ False
        strategy, _ = kf.plan_strategy_user(self._split_plan(), "r")
        self.assertIs(strategy["review"], False)
        self.assertNotIn("tier", strategy)
        strategy2, _ = kf.plan_strategy_user(self._split_plan(), "r", tier="large")
        self.assertIs(strategy2["review"], False)

    def test_default_basic_turns_review_on(self):
        strategy, _ = kf.plan_strategy_user(self._split_plan(), "r", tier="basic")
        self.assertIs(strategy["review"], True)
        self.assertEqual(strategy["tier"], "basic")

    def test_explicit_true_respected_regardless_of_tier(self):
        strategy, _ = kf.plan_strategy_user(
            _plan([{"id": "a", "goal": "g"}], review=True), "r")
        self.assertIs(strategy["review"], True)

    def test_explicit_false_not_overridden_by_basic(self):
        # tier_review_decision は明示 bool を尊重する既存仕様の確認
        strategy, _ = kf.plan_strategy_user(
            _plan([{"id": "a", "goal": "g"}], review=False), "r", tier="basic")
        self.assertIs(strategy["review"], False)

    def test_invalid_review_rejected(self):
        # 厳格検証の方針どおり、三値（true/false/"auto"）以外は丸めず失敗させる
        with self.assertRaises(kf.UserPlanError):
            kf.plan_strategy_user(_plan([{"id": "a", "goal": "g"}], review="yes"), "r")

    def test_review_true_inserts_gate_into_fanout(self):
        # gate が入るのは動的 fan-out 領域（map→reduce 間）だけ——静的な形は変わらない
        strategy, tasks = kf.plan_strategy_user(self._split_plan(), "r", tier="basic")
        nodes = {t["id"]: kf._node_entry(t) for t in tasks}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        _, new, _ = kf.continue_stub("r", nodes, results, 0, review=strategy["review"])
        gate = next(t for t in new if t["id"] == "s-gate")
        self.assertEqual(gate["kind"], "verify")
        self.assertIn("s-gate", next(t for t in new if t["id"] == "s-reduce")["deps"])
        # 非 basic（review False）では従来どおり gate 無し
        _, new2, _ = kf.continue_stub("r", nodes, results, 0, review=False)
        self.assertNotIn("s-gate", [t["id"] for t in new2])


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

    def test_split_fanout_expands_without_evaluator(self):
        # データ駆動 fan-out（split → map/reduce）は機械展開（LLM 無し）なので、評価役が
        # 無効な既定でも走る——これが無いと split を含むカスタムフローは「後段は実行時に
        # 自動生成される」契約（plan_strategy_user が静的依存を弾く理由）が果たされず空振りする。
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        decision, new, reason = kf._continue(
            self._args(), None, "r", nodes, results, 0, {"user_plan": True})
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new], ["s-m1", "s-m2", "s-reduce"])
        self.assertIn("data-driven fan-out", reason)

    def test_split_fanout_gate_follows_strategy_review(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        _, new, _ = kf._continue(self._args(), None, "r", nodes, results, 0,
                                 {"user_plan": True, "review": True})
        self.assertIn("s-gate", [t["id"] for t in new])
        _, new2, _ = kf._continue(self._args(), None, "r", nodes, results, 0,
                                  {"user_plan": True, "review": False})
        self.assertNotIn("s-gate", [t["id"] for t in new2])

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
            [{"id": "solo", "goal": "単独: {{request}}", "kind": "work"}]),
            readonly=True)
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "--run-id", req_id, "run",
             "--from-inbox", "--workers", "1", "--planner", "stub",
             "--executor", "stub", "--poll", "0.2"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        _, graph, final, meta = self._graph(bus)
        self.assertEqual(set(graph["nodes"]), {"solo"})
        self.assertEqual(graph["nodes"]["solo"]["goal"], "単独: inbox 経由")
        self.assertIs(meta["readonly"], True)
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

    def test_basic_tier_run_gates_dynamic_fanout(self):
        # 配線の確認: orchestrate が flow_tier()（agent-control 宣言）を plan_strategy_user へ
        # 渡し、basic では split の動的 fan-out に verify gate が入って run が完走する。
        bus = self._bus()
        ctl = tempfile.mkdtemp(prefix="kf-userplan-ctl-")
        self.addCleanup(shutil.rmtree, ctl, ignore_errors=True)
        pathlib.Path(ctl, "control.json").write_text(json.dumps(
            {"workloads": {"flow": {"tier": "basic"}}}), encoding="utf-8")
        pf = os.path.join(bus, "plan.json")
        pathlib.Path(pf).write_text(json.dumps(_plan(
            [{"id": "s", "goal": "分解: {{request}}", "kind": "split"}])), encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "run", "各件を処理",
             "--plan-file", pf, "--workers", "2", "--planner", "stub",
             "--executor", "stub", "--poll", "0.2"],
            capture_output=True, text=True, timeout=90,
            env=dict(os.environ, AGENT_CONTROL_DIR=ctl))
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        _, graph, final, _meta = self._graph(bus)
        self.assertIs(graph["strategy"]["review"], True)
        self.assertEqual(graph["strategy"]["tier"], "basic")
        self.assertIn("s-gate", graph["nodes"])           # map→reduce 間の verify gate
        self.assertIn("s-gate", graph["nodes"]["s-reduce"]["deps"])
        for nid, r in final["results"].items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")

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


class PlanDecisionContractTests(unittest.TestCase):
    """判定契約（node.decision）: filter / judge だけ・壊れた宣言は投入時に弾く。"""

    DECISION = {"facts": [{"name": "extra_deps", "type": "bool"}],
                "criteria": [{"fact": "extra_deps", "op": "eq", "value": False}]}

    def test_valid_decision_is_carried_verbatim(self):
        _strategy, tasks = kf.plan_strategy_user(_plan([
            {"id": "g", "goal": "候補を作る", "kind": "generate"},
            {"id": "f", "goal": "選別", "kind": "filter", "deps": ["g"],
             "decision": self.DECISION},
        ]), "X")
        self.assertEqual(tasks[1]["decision"], self.DECISION)

    def test_decision_on_other_kinds_is_rejected(self):
        with self.assertRaises(kf.UserPlanError):
            kf.plan_strategy_user(_plan([
                {"id": "a", "goal": "g", "kind": "work", "decision": self.DECISION}]), "X")

    def test_broken_decision_is_rejected_not_ignored(self):
        with self.assertRaises(kf.UserPlanError):
            kf.plan_strategy_user(_plan([
                {"id": "f", "goal": "選別", "kind": "filter",
                 "decision": {"facts": [{"name": "x", "type": "bool"}],
                              "criteria": [{"fact": "unknown", "op": "eq", "value": 1}]}}]), "X")

    def test_planner_path_drops_broken_decision_and_keeps_valid_one(self):
        tasks = kf._coerce_tasks([
            {"id": "f1", "goal": "選別", "kind": "filter", "decision": self.DECISION},
            {"id": "f2", "goal": "選別", "kind": "filter", "decision": {"criteria": []}},
            {"id": "w", "goal": "実装", "kind": "work", "decision": self.DECISION},
        ])
        self.assertEqual(tasks[0]["decision"], self.DECISION)
        self.assertNotIn("decision", tasks[1])
        self.assertNotIn("decision", tasks[2])   # filter / judge 以外へは運ばない

    def test_dropped_contracts_are_recorded_on_the_node(self):
        """剥がした事実は log だけでなくノードにも残す（log は run ディレクトリの外へ
        出ないので、audit / dashboard から「宣言したのに効かなかった」を数えられない）。"""
        tasks = kf._coerce_tasks([
            {"id": "f1", "goal": "選別", "kind": "filter", "decision": self.DECISION},
            {"id": "f2", "goal": "選別", "kind": "filter", "decision": {"criteria": []}},
            {"id": "w", "goal": "実装", "kind": "work", "decision": self.DECISION,
             "operation": {"operation_class": "feature", "scope": "オブジェクトでない"}},
        ])
        self.assertNotIn("contract_dropped", tasks[0])
        self.assertEqual([d["contract"] for d in tasks[1]["contract_dropped"]], ["decision"])
        self.assertTrue(tasks[1]["contract_dropped"][0]["reason"])
        self.assertEqual(sorted(d["contract"] for d in tasks[2]["contract_dropped"]),
                         ["decision", "operation"])


class DeliverableSlotExpansionTests(unittest.TestCase):
    """成果物スロットの機械分割: planner 経路では割り、ユーザー定義フローでは割らない。"""

    OPERATION = {
        "operation_class": "feature",
        "scope": {"read": ["src"], "write": ["src/a.py", "tests/test_a.py"]},
        "deliverables": ["src/a.py", "tests/test_a.py"],
        "verification": {"commands": [["python", "-m", "pytest", "-q", "tests"]]},
    }

    def test_planner_task_with_two_deliverables_is_chained(self):
        tasks = kf._coerce_tasks([
            {"id": "t1", "goal": "実装してテストも足す", "kind": "work",
             "operation": self.OPERATION},
            {"id": "t2", "goal": "検証", "kind": "verify", "deps": ["t1"]},
        ])
        self.assertEqual([t["id"] for t in tasks], ["t1-d1", "t1-d2", "t2"])
        self.assertEqual(tasks[1]["deps"], ["t1-d1"])
        # 後続は最後のスロットに依存させる（片方だけ出来た状態で検証を走らせない）
        self.assertEqual(tasks[2]["deps"], ["t1-d2"])
        self.assertEqual(tasks[0]["operation"]["deliverables"], ["src/a.py"])

    def test_single_deliverable_is_left_alone(self):
        tasks = kf._coerce_tasks([
            {"id": "t1", "goal": "実装", "kind": "work",
             "operation": dict(self.OPERATION, deliverables=["src/a.py"],
                               scope={"read": ["src"], "write": ["src/a.py"]})}])
        self.assertEqual([t["id"] for t in tasks], ["t1"])

    def test_replaces_stays_on_the_last_slot_only(self):
        """差し替え宣言が全スロットに付くと、旧ノードの後続が最初のスロットへ繋がる
        （成果物が 1 つ出来ただけで走り出す）。最後のスロットにだけ残す。"""
        tasks = kf._coerce_tasks([
            {"id": "r1", "goal": "作り直す", "kind": "work", "replaces": "t9",
             "operation": self.OPERATION}])
        self.assertEqual([t.get("replaces") for t in tasks], [None, "t9"])

    def test_user_plan_is_not_split(self):
        """人が描いた形は意図そのもの——投入した plan のノードを勝手に増やさない。"""
        _strategy, tasks = kf.plan_strategy_user(_plan([
            {"id": "a", "goal": "実装してテストも足す", "kind": "work",
             "operation": self.OPERATION}]), "X")
        self.assertEqual([t["id"] for t in tasks], ["a"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
