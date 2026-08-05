"""flow-planner の粒度導出・決定的ゲート（LLM 無し）の単体テスト。"""
from __future__ import annotations

import importlib.util
import json
import os
import unittest
from unittest import mock


def _load_plan():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    path = os.path.join(root, ".github", "skills", "flow-planner", "scripts", "plan.py")
    spec = importlib.util.spec_from_file_location("flow_planner_plan", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


plan = _load_plan()


class ResolveGranularityTests(unittest.TestCase):
    def test_auto_from_complexity(self):
        self.assertEqual(plan.resolve_granularity("auto", "simple"), "coarse")
        self.assertEqual(plan.resolve_granularity("auto", "moderate"), "fine")
        self.assertEqual(plan.resolve_granularity("auto", "complex"), "finest")
        self.assertEqual(plan.resolve_granularity(None, "simple"), "coarse")

    def test_explicit_overrides_complexity(self):
        self.assertEqual(plan.resolve_granularity("finest", "simple"), "finest")
        self.assertEqual(plan.resolve_granularity("coarse", "complex"), "coarse")

    def test_unknown_complexity_defaults_fine(self):
        self.assertEqual(plan.resolve_granularity("auto", "weird"), "fine")
        self.assertEqual(plan.resolve_granularity("auto", None), "fine")


class WorkNodeRangeTests(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(plan.work_node_range("coarse"), (1, 3))
        self.assertEqual(plan.work_node_range("fine"), (3, 8))
        self.assertEqual(plan.work_node_range("finest"), (6, 12))


class ScopeAndGateTests(unittest.TestCase):
    def test_has_scope_marker_and_path(self):
        self.assertTrue(plan.has_scope("[scope] src/foo.py\n[out_of_scope] bar\n実装"))
        self.assertTrue(plan.has_scope("update `src/auth/login.ts` only"))
        self.assertTrue(plan.has_scope("touch /Users/x/proj/mod.py"))
        self.assertFalse(plan.has_scope("認証を実装する"))

    def test_gate_skips_deferred_only_graphs(self):
        tasks = [{"id": "split1", "goal": "分解", "deps": [], "kind": "split"}]
        self.assertEqual(plan.gate_tasks(tasks, "fine"), [])

    def test_gate_count_out_of_range(self):
        tasks = [
            {"id": "t1", "goal": "[scope] a.py\n[out_of_scope] x\none", "deps": [], "kind": "work"},
        ]
        issues = plan.gate_tasks(tasks, "fine")  # fine wants 3–8
        self.assertTrue(any("レンジ" in i for i in issues))

    def test_gate_missing_scope(self):
        tasks = [
            {"id": f"t{i}", "goal": f"抽象目標{i}", "deps": [], "kind": "work"}
            for i in range(1, 5)
        ]
        issues = plan.gate_tasks(tasks, "fine")
        self.assertTrue(any("scope" in i for i in issues))

    def test_gate_duplicate_goals(self):
        g = "[scope] a.py\n[out_of_scope] x\nsame body"
        tasks = [
            {"id": "t1", "goal": g, "deps": [], "kind": "work"},
            {"id": "t2", "goal": g, "deps": [], "kind": "work"},
            {"id": "t3", "goal": "[scope] b.py\n[out_of_scope] y\nother", "deps": [], "kind": "work"},
        ]
        issues = plan.gate_tasks(tasks, "fine")
        self.assertTrue(any("重複" in i for i in issues))

    def test_gate_pass(self):
        tasks = [
            {"id": f"t{i}",
             "goal": f"[scope] mod{i}.py\n[out_of_scope] other\n実装{i}",
             "deps": [], "kind": "work"}
            for i in range(1, 5)
        ] + [{"id": "synth", "goal": "統合", "deps": ["t1", "t2", "t3", "t4"], "kind": "synthesize"}]
        self.assertEqual(plan.gate_tasks(tasks, "fine"), [])


class Phase3RetryTests(unittest.TestCase):
    def test_retries_once_when_gate_fails(self):
        bad = [{"id": "t1", "goal": "抽象", "deps": [], "kind": "work"}]
        good = [
            {"id": f"t{i}",
             "goal": f"[scope] m{i}.py\n[out_of_scope] x\njob{i}",
             "deps": [], "kind": "work"}
            for i in range(1, 5)
        ]
        calls = {"n": 0}

        def fake_agent(prompt, model):
            calls["n"] += 1
            return json.dumps(bad if calls["n"] == 1 else good)

        analysis = {"subtasks": ["a", "b", "c"], "complexity": "moderate"}
        strategy = {"patterns": ["fan-out-and-synthesize"], "parallelism": 3,
                    "reason": "r", "composite_template": None, "review": False}
        with mock.patch.object(plan, "run_agent", side_effect=fake_agent):
            tasks = plan.phase3_build("req", analysis, strategy, None, "fine")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len([t for t in tasks if t["kind"] == "work"]), 4)


if __name__ == "__main__":
    unittest.main()


class FallbackGranularityTests(unittest.TestCase):
    """flow-planner を経ない planner へ渡す粒度（agent_flow.patterns.fallback_granularity）。

    auto は「flow-planner が complexity から導出する」意味なので、スキル未導入・スキル失敗の
    フォールバック経路には導出者が居ない。そのまま渡すと粒度指示も並列倍率も効かず、
    設定を変えていない利用者の計画だけが黙って粗くなる（auto 導入前の既定は finest）。"""

    def setUp(self):
        import sys
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        import agent_flow
        self.kf = agent_flow

    def test_auto_resolves_to_previous_default(self):
        self.assertEqual(self.kf.fallback_granularity("auto"), "finest")
        self.assertEqual(self.kf.fallback_granularity(None), "finest")
        self.assertEqual(self.kf.fallback_granularity(""), "finest")

    def test_explicit_levels_pass_through(self):
        for lv in ("coarse", "fine", "finest"):
            self.assertEqual(self.kf.fallback_granularity(lv), lv)

    def test_fallback_keeps_directive_and_scaling(self):
        # auto のままだと指示が空・倍率 1 になる（＝粗くなる）ことと、
        # 解決後は指示が出て倍率も戻ることを対比で固定する。
        self.assertEqual(self.kf.granularity_directive("auto"), "")
        self.assertEqual(self.kf.granularity_factor("auto"), 1)
        resolved = self.kf.fallback_granularity("auto")
        self.assertNotEqual(self.kf.granularity_directive(resolved), "")
        self.assertEqual(self.kf.granularity_factor(resolved), 3)


class EstimatedStepsTests(unittest.TestCase):
    """Phase 1 の `estimated_steps`（設計「Phase 1 拡張」）。"""

    def test_normalizes_the_shapes_an_llm_actually_returns(self):
        self.assertEqual(plan.normalize_estimated_steps(6), 6)
        self.assertEqual(plan.normalize_estimated_steps("6"), 6)
        self.assertEqual(plan.normalize_estimated_steps(6.9), 6)
        self.assertEqual(plan.normalize_estimated_steps("約6ステップ"), 6)

    def test_unreadable_values_become_none(self):
        for bad in (None, "", "たぶん", 0, -3, True, False, [], {}):
            self.assertIsNone(plan.normalize_estimated_steps(bad), bad)

    def test_hint_never_overrides_the_granularity_range(self):
        """見積りが何であれ、プロンプトのレンジは granularity_target が決める。"""
        seen = {}

        def fake_agent(prompt, model=None):
            seen["prompt"] = prompt
            return json.dumps([
                {"id": f"t{i}", "goal": f"[scope] src/m{i}.py\n実装", "deps": [], "kind": "work"}
                for i in range(1, 4)])

        analysis = {"subtasks": ["a"], "estimated_steps": 99}
        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase3_build("req", analysis, {"patterns": []}, None, "fine")
        self.assertIn("3–8 個", seen["prompt"])                  # fine のレンジのまま
        self.assertIn("最小ステップ見積り: 99", seen["prompt"])   # 目安としては渡る

    def test_no_hint_line_when_estimate_is_missing(self):
        seen = {}

        def fake_agent(prompt, model=None):
            seen["prompt"] = prompt
            return json.dumps([
                {"id": "t1", "goal": "[scope] src/a.py\n実装", "deps": [], "kind": "work"}])

        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase3_build("req", {"subtasks": []}, {"patterns": []}, None, "coarse")
        self.assertNotIn("最小ステップ見積り", seen["prompt"])


class ContextPrefixTests(unittest.TestCase):
    """プロジェクト文脈（案 H・オプトイン --context）の Phase 1/3 への前置。
    Phase 2（phase2_select）は request を埋め込まないため対象外——ここでは検証しない。"""

    def test_phase1_prepends_context_when_given(self):
        seen = {}

        def fake_agent(prompt, model=None):
            seen["prompt"] = prompt
            return json.dumps({"complexity": "moderate", "subtasks": ["a"]})

        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase1_analyze("要求本文", None, context="CTX-STABLE-TEXT")
        self.assertTrue(seen["prompt"].startswith("CTX-STABLE-TEXT"))
        self.assertIn("要求本文", seen["prompt"])
        self.assertLess(seen["prompt"].index("CTX-STABLE-TEXT"), seen["prompt"].index("要求本文"))

    def test_phase1_without_context_is_byte_identical_to_before(self):
        seen = {}

        def fake_agent(prompt, model=None):
            seen["prompt"] = prompt
            return json.dumps({"complexity": "moderate", "subtasks": ["a"]})

        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase1_analyze("要求本文", None)
        without_context = seen["prompt"]
        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase1_analyze("要求本文", None, context="")
        self.assertEqual(without_context, seen["prompt"])
        self.assertNotIn("CTX-STABLE-TEXT", without_context)

    def test_phase3_prepends_context_ahead_of_retry_instruction(self):
        """context（安定）→ extra（再生成時の指示・可変）→ 本体、の順（安定部を最優先で先頭に）。"""
        seen = {}

        def fake_agent(prompt, model=None):
            seen["prompt"] = prompt
            return json.dumps([{"id": "t1", "goal": "[scope] a.py\n実装", "deps": [], "kind": "work"}])

        analysis = {"subtasks": ["a"], "complexity": "moderate"}
        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase3_build("要求本文", analysis, {"patterns": []}, None, "coarse",
                              context="CTX-STABLE-TEXT")
        self.assertTrue(seen["prompt"].startswith("CTX-STABLE-TEXT"))

    def test_phase3_without_context_is_byte_identical_to_before(self):
        seen = {}

        def fake_agent(prompt, model=None):
            seen["prompt"] = prompt
            return json.dumps([{"id": "t1", "goal": "[scope] a.py\n実装", "deps": [], "kind": "work"}])

        analysis = {"subtasks": ["a"], "complexity": "moderate"}
        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase3_build("要求本文", analysis, {"patterns": []}, None, "coarse")
        without_context = seen["prompt"]
        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase3_build("要求本文", analysis, {"patterns": []}, None, "coarse", context="")
        self.assertEqual(without_context, seen["prompt"])


if __name__ == "__main__":
    unittest.main()
