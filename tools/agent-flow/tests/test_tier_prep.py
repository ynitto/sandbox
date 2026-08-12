"""実行 tier（tier: basic）のお膳立て — planner/evaluator が basic ワーカーを想定して
計画・評価を寄せる（予算逼迫の緊急時、普段は任せない役割へ basic を投入するための下地）。"""
from _shared import *  # noqa: F401,F403
import argparse
import shutil
import tempfile


class TierHelperTests(unittest.TestCase):
    def test_basic_resolves_auto_granularity_to_finest(self):
        self.assertEqual(kf.tier_planning_granularity("auto", "basic"), "finest")
        self.assertEqual(kf.tier_planning_granularity(None, "basic"), "finest")
        # 明示指定は人の意思なので tier では覆さない
        self.assertEqual(kf.tier_planning_granularity("coarse", "basic"), "coarse")
        # basic 以外は auto のまま（flow-planner の complexity 導出に委ねる）
        self.assertEqual(kf.tier_planning_granularity("auto", ""), "auto")
        self.assertEqual(kf.tier_planning_granularity("auto", "large"), "auto")

    def test_directives_only_for_basic(self):
        self.assertIn("basic", kf.tier_planner_directive("basic"))
        self.assertEqual(kf.tier_planner_directive(""), "")
        self.assertEqual(kf.tier_planner_directive("large"), "")
        self.assertIn("basic", kf.tier_evaluator_directive("basic"))
        self.assertEqual(kf.tier_evaluator_directive(None), "")

    def test_review_forced_on_for_basic_auto_only(self):
        # auto は basic で常時有効へ（集約パターンでなくても）
        self.assertTrue(kf.tier_review_decision("auto", ["tournament"], "basic"))
        # 明示 False/True は尊重
        self.assertFalse(kf.tier_review_decision(False, ["map-reduce"], "basic"))
        self.assertTrue(kf.tier_review_decision(True, ["classify-and-act"], ""))
        # tier 無しは従来の集約パターン判定
        self.assertFalse(kf.tier_review_decision("auto", ["tournament"], ""))
        self.assertTrue(kf.tier_review_decision("auto", ["map-reduce"], ""))

    def test_flow_tier_reads_agent_control(self):
        d = tempfile.mkdtemp(prefix="kf-tier-ctl-")
        self.addCleanup(shutil.rmtree, d, True)
        with mock.patch.dict(os.environ, {"AGENT_CONTROL_DIR": d}):
            self.assertEqual(kf.flow_tier(), "")
            pathlib.Path(d, "control.json").write_text(json.dumps(
                {"workloads": {"flow": {"tier": "basic"}}}))
            self.assertEqual(kf.flow_tier(), "basic")


class TierPlannerTests(unittest.TestCase):
    def test_stub_records_tier_and_forces_review(self):
        strategy, _tasks = kf.plan_strategy_stub("案を出して選ぶ tournament", tier="basic")
        self.assertEqual(strategy["tier"], "basic")
        self.assertTrue(strategy["review"])
        strategy2, _ = kf.plan_strategy_stub("案を出して選ぶ tournament")
        self.assertNotIn("tier", strategy2)
        self.assertFalse(strategy2["review"])

    def test_pattern_records_tier(self):
        strategy, _ = kf.plan_strategy_pattern("tournament", "req", tier="basic")
        self.assertEqual(strategy["tier"], "basic")
        self.assertTrue(strategy["review"])

    def test_agent_planner_injects_basic_directive(self):
        seen = {}

        def fake_agent(prompt, model, purpose="planner"):
            seen["prompt"] = prompt
            return json.dumps({"patterns": ["tournament"], "parallelism": 2, "reason": "r",
                               "tasks": [{"id": "t1", "goal": "g", "deps": [], "kind": "work"}]})

        with mock.patch.object(kf, "run_agent", fake_agent):
            strategy, _ = kf.plan_strategy_agent("req", None, tier="basic")
        self.assertIn(kf.tier_planner_directive("basic"), seen["prompt"])
        self.assertEqual(strategy["tier"], "basic")
        self.assertTrue(strategy["review"])   # auto + basic → 常時有効

        with mock.patch.object(kf, "run_agent", fake_agent):
            strategy2, _ = kf.plan_strategy_agent("req", None)
        self.assertNotIn("実行ティア", seen["prompt"])
        self.assertNotIn("tier", strategy2)
        self.assertFalse(strategy2["review"])

    def test_plan_strategy_resolves_tier_from_control(self):
        root = tempfile.mkdtemp(prefix="kf-tier-plan-")
        self.addCleanup(shutil.rmtree, root, True)
        ctl = tempfile.mkdtemp(prefix="kf-tier-ctl2-")
        self.addCleanup(shutil.rmtree, ctl, True)
        pathlib.Path(ctl, "control.json").write_text(json.dumps(
            {"workloads": {"flow": {"tier": "basic"}}}))
        bus = kf.Bus(root, "run-t")
        args = argparse.Namespace(request="A; B", planner="stub", model=None,
                                  review=None, granularity=None)
        kf.resolve_config(args)
        with mock.patch.dict(os.environ, {"AGENT_CONTROL_DIR": ctl}):
            strategy, _ = kf._plan_strategy(args, bus)
        self.assertEqual(strategy["tier"], "basic")
        # granularity auto → finest（stub は理由文字列に粒度を残す）
        self.assertIn("粒度 finest", strategy["reason"])

    def test_skill_flag_sniffing(self):
        d = tempfile.mkdtemp(prefix="kf-sniff-")
        self.addCleanup(shutil.rmtree, d, True)
        new = pathlib.Path(d, "new.py")
        new.write_text('parser.add_argument("--tier", default="")\n')
        old = pathlib.Path(d, "old.py")
        old.write_text('parser.add_argument("--granularity")\n')
        self.assertTrue(kf._skill_flag_supported(str(new), "--tier"))
        self.assertFalse(kf._skill_flag_supported(str(old), "--tier"))
        self.assertFalse(kf._skill_flag_supported(str(pathlib.Path(d, "none.py")), "--tier"))

    def test_flow_planner_passes_tier_only_when_supported(self):
        d = tempfile.mkdtemp(prefix="kf-fp-tier-")
        self.addCleanup(shutil.rmtree, d, True)
        script = pathlib.Path(d, "plan.py")
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            out = json.dumps({"strategy": {"patterns": ["fan-out-and-synthesize"],
                                           "parallelism": 2, "review": True,
                                           "reason": "r", "granularity": "finest"},
                              "tasks": [{"id": "t1", "goal": "g", "deps": [], "kind": "work"}]})
            return mock.Mock(returncode=0, stdout=out, stderr="")

        script.write_text("# supports --tier\n")
        with mock.patch.object(kf, "_find_flow_planner_script", return_value=str(script)), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            strategy, _ = kf.plan_strategy_flow_planner("req", None, tier="basic")
        self.assertIn("--tier", seen["cmd"])
        self.assertEqual(seen["cmd"][seen["cmd"].index("--tier") + 1], "basic")
        self.assertEqual(strategy["tier"], "basic")

        script.write_text("# old skill without the flag\n")
        with mock.patch.object(kf, "_find_flow_planner_script", return_value=str(script)), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.plan_strategy_flow_planner("req", None, tier="basic")
        self.assertNotIn("--tier", seen["cmd"])


class TierEvaluatorTests(unittest.TestCase):
    def _continue(self, tier):
        seen = {}

        def fake_agent(prompt, model, purpose="evaluator"):
            seen["prompt"] = prompt
            return json.dumps({"decision": "done", "reason": "ok", "new_tasks": []})

        nodes = {"t1": {"kind": "work", "deps": [], "goal": "g"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(kf, "run_agent", fake_agent):
            decision, _, _ = kf.continue_agent("req", nodes, results, 0, tier=tier)
        self.assertEqual(decision, "done")
        return seen["prompt"]

    def test_evaluator_gets_basic_directive_only_for_basic(self):
        self.assertIn(kf.tier_evaluator_directive("basic"), self._continue("basic"))
        self.assertNotIn("実行ティア", self._continue(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
