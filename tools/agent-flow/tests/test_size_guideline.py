"""規模の目安（size）— Claude Code の workflowSizeGuideline と同じ目盛りで、
1 つの担当で終わる依頼を細切れにしないための計画パラメータ。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from test_flow_planner_granularity import plan  # noqa: E402
import test_run  # noqa: E402


class EngineSizeTests(unittest.TestCase):
    def test_default_is_small_and_unknown_values_fall_back(self):
        self.assertEqual(kf.CONFIG_DEFAULTS["size"], "small")
        self.assertEqual(kf.resolve_size(None), "small")
        self.assertEqual(kf.resolve_size("huge"), "small")
        self.assertEqual(kf.resolve_size("Large"), "large")

    def test_limits_follow_the_upstream_scale(self):
        self.assertEqual(kf.size_node_limit("small"), 5)
        self.assertEqual(kf.size_node_limit("medium"), 10)
        self.assertEqual(kf.size_node_limit("large"), 50)
        self.assertIsNone(kf.size_node_limit("unrestricted"))
        self.assertEqual(kf.size_directive("unrestricted"), "")
        self.assertIn("5 個未満", kf.size_directive("small"))

    def test_fallback_granularity_stays_inside_the_size(self):
        # flow-planner を経ない縮退で finest（細かく割れ）を渡すと small の目安と食い違う。
        self.assertEqual(kf.fallback_granularity("auto", "small"), "coarse")
        self.assertEqual(kf.fallback_granularity("auto", "medium"), "fine")
        self.assertEqual(kf.fallback_granularity("auto", "unrestricted"), "finest")
        self.assertEqual(kf.fallback_granularity("fine", "small"), "fine")

    def test_agent_planner_prompt_carries_the_size(self):
        seen = {}

        def fake_agent(prompt, model, purpose=None):
            seen["prompt"] = prompt
            return json.dumps({"patterns": ["adversarial-verification"], "parallelism": 1,
                               "tasks": [{"id": "t1", "goal": "g", "deps": [], "kind": "work"}]})

        with mock.patch.object(kf, "run_agent", side_effect=fake_agent):
            strategy, _ = kf.plan_strategy_agent("req", None, size="medium")
        self.assertIn(kf.size_directive("medium"), seen["prompt"])
        self.assertEqual(strategy["size"], "medium")

    def test_flow_planner_receives_the_size_flag(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return types.SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                "strategy": {"patterns": ["adversarial-verification"]},
                "tasks": [{"id": "t1", "goal": "g", "deps": [], "kind": "work"}]}))

        with mock.patch.object(kf, "_find_flow_planner_script", return_value="/tmp/plan.py"), \
                mock.patch.object(kf, "_skill_flag_supported", return_value=True), \
                mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            strategy, _ = kf.plan_strategy_flow_planner("req", None, size="large")
        self.assertEqual(seen["cmd"][seen["cmd"].index("--size") + 1], "large")
        self.assertEqual(strategy["size"], "large")

    def test_inbox_can_name_size_and_plan_gate(self):
        args = types.SimpleNamespace(size="small", plan_gate=False, _cli_explicit=set())
        kf._apply_inbox_planning({"size": "medium", "plan_gate": True}, args)
        self.assertEqual(args.size, "medium")
        self.assertTrue(args.plan_gate)
        # CLI で打った値は要求に負けない
        args = types.SimpleNamespace(size="large", plan_gate=False,
                                     _cli_explicit={"size", "plan_gate"})
        kf._apply_inbox_planning({"size": "medium", "plan_gate": True}, args)
        self.assertEqual(args.size, "large")
        self.assertFalse(args.plan_gate)

    def test_inbox_rejects_an_unknown_size(self):
        with self.assertRaises(kf.InboxRequestError):
            kf._apply_inbox_planning({"size": "huge"}, types.SimpleNamespace(_cli_explicit=set()))


class SpawnSizeTests(unittest.TestCase):
    # 子の argv の組み立てと実パーサでの検証は SpawnArgvTests の手順を借りる（継承すると
    # あちらのテストまでここで二重に走る）。
    _args = test_run.SpawnArgvTests._args
    _capture = test_run.SpawnArgvTests._capture
    _parse_child = test_run.SpawnArgvTests._parse_child
    _base = test_run.SpawnArgvTests._base

    def test_request_size_and_gate_reach_the_orchestrator(self):
        args = self._args(size="small")
        cmd = self._capture(kf._spawn_orchestrator, self._base(), args, "run-sz",
                            {"request": "do it", "size": "medium", "plan_gate": True})
        parsed = self._parse_child(cmd)
        self.assertEqual(parsed.size, "medium")
        self.assertTrue(parsed.plan_gate)


class FlowPlannerSizeTests(unittest.TestCase):
    def test_range_is_capped_and_auto_lowers_the_floor(self):
        self.assertEqual(plan.work_node_range("finest"), (6, 12))          # 目安なしは従来どおり
        self.assertEqual(plan.work_node_range("finest", "small", explicit=False), (1, 3))
        self.assertEqual(plan.work_node_range("fine", "medium", explicit=False), (1, 8))
        # 明示の粒度は下限を残す（ただし上限の中へ収める）
        self.assertEqual(plan.work_node_range("fine", "small", explicit=True), (3, 3))

    def test_gate_counts_every_node_against_the_size(self):
        tasks = [{"id": f"t{i}", "kind": "work", "goal": f"[scope] src/m{i}.py\n直す{i}", "deps": []}
                 for i in range(3)]
        tasks += [{"id": "v", "kind": "verify", "goal": "確かめる", "deps": ["t0"]},
                  {"id": "s", "kind": "synthesize", "goal": "まとめる", "deps": ["v"]}]
        issues = plan.gate_tasks(tasks, "fine", size="small", explicit=False)
        self.assertTrue(any("規模の目安" in item for item in issues), issues)
        self.assertFalse(any("規模の目安" in item for item in plan.gate_tasks(tasks, "fine")))

    def test_one_worker_plan_passes_under_small(self):
        tasks = [{"id": "t1", "kind": "work", "goal": "[scope] src/app.py\n直す", "deps": []},
                 {"id": "v", "kind": "verify", "goal": "別の担当が確かめる", "deps": ["t1"]}]
        self.assertEqual(plan.gate_tasks(tasks, "fine", size="small", explicit=False), [])

    def test_line_limit_only_applies_without_the_size(self):
        self.assertIn("30 行", plan.scope_rule("", False))
        self.assertIn("30 行", plan.scope_rule("small", True))
        self.assertIn("30 行", plan.scope_rule("small", False, "basic"))
        self.assertNotIn("30 行", plan.scope_rule("small", False))
        self.assertEqual(plan.size_build_note("", False), "")
        self.assertIn("5 個未満", plan.size_build_note("small", False))


if __name__ == "__main__":
    unittest.main()
