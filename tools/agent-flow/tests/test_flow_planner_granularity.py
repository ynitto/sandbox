"""flow-planner の粒度導出・決定的ゲート（LLM 無し）の単体テスト。"""
from __future__ import annotations

import importlib.util
import io
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

    def test_gate_rejects_static_map_reduce_behind_split(self):
        # engine は split 完了後に map / reduce を動的生成する。静的に書くと map が全件を
        # 1 ノードで受ける（planner_eval 2026-08-23 で e4b が 2/3 この形を書いた）。
        tasks = [{"id": "s", "goal": "[scope] notes/ 列挙", "deps": [], "kind": "split"},
                 {"id": "m", "goal": "[scope] notes/ 各ファイル", "deps": ["s"], "kind": "map"},
                 {"id": "r", "goal": "集約", "deps": ["m"], "kind": "reduce"}]
        issues = plan.gate_tasks(tasks, "coarse")
        self.assertTrue(any("静的 map" in x for x in issues), issues)
        direct_reduce = [{"id": "s", "goal": "[scope] 列挙", "deps": [], "kind": "split"},
                         {"id": "r", "goal": "集約", "deps": ["s"], "kind": "reduce"}]
        self.assertTrue(any("静的 reduce" in x for x in plan.gate_tasks(direct_reduce, "coarse")))

    def test_gate_rejects_any_static_successor_of_split(self):
        """kind を問わず落とす。map / reduce だけ見ていたとき、e4b は同じ形を work で書いて
        素通りしていた（planner_eval PL3 0/3・2026-08-29）。engine 側（plan_strategy_user）は
        kind に関係なく split への静的依存を拒む。"""
        tasks = [{"id": "s", "goal": "[scope] notes/ 列挙", "deps": [], "kind": "split"},
                 {"id": "w", "goal": "[scope] notes/ 各ファイルの見出し抽出", "deps": ["s"],
                  "kind": "work"}]
        self.assertTrue(any("静的 work" in x for x in plan.gate_tasks(tasks, "coarse")))

    def test_gate_rejects_declarations_that_the_engine_would_strip(self):
        """宣言の器が壊れていると engine が剥がす＝宣言したのに効かない。ここで作り直させる。"""
        broken_decision = [{"id": "f", "goal": "[scope] 候補\n残す", "deps": [], "kind": "filter"}]
        self.assertTrue(any("decision が無い" in x
                            for x in plan.gate_tasks(broken_decision, "coarse")))
        tie = [{"id": "f", "goal": "[scope] 候補\n残す", "deps": [], "kind": "judge",
                "decision": {"facts": [{"name": "lines", "type": "int"}],
                             "criteria": [{"fact": "lines", "op": "ne", "value": 0}],
                             "tie_break": "lines が最小"}}]
        self.assertTrue(any("tie_break" in x for x in plan.gate_tasks(tie, "coarse")))
        empty_deliverables = [{"id": "t", "goal": "[scope] src/a.py\n実装", "deps": [],
                               "kind": "work", "operation": {"operation_class": "feature"}}]
        self.assertTrue(any("deliverables が空" in x
                            for x in plan.gate_tasks(empty_deliverables, "coarse")))
        # 成果物を作らないノードに operation が無いのは正常（宣言は必須にしない）
        no_contract = [{"id": "t", "goal": "[scope] src/a.py\n調べる", "deps": [], "kind": "work"}]
        self.assertEqual(plan.gate_tasks(no_contract, "coarse"), [])

    def test_gate_rejects_facts_that_were_never_declared(self):
        """実測 2026-08-29: e4b は facts に無い fact を tie_break / criteria に書く。
        engine はその decision を丸ごと剥がす（＝モデル判定へ戻る）ので、ここで作り直させる。"""
        undeclared_tie = [{"id": "j", "goal": "[scope] 候補\n最良を選ぶ", "deps": [], "kind": "judge",
                           "decision": {"facts": [{"name": "extra_deps", "type": "bool"}],
                                        "criteria": [{"fact": "extra_deps", "op": "eq",
                                                      "value": False}],
                                        "tie_break": {"fact": "lines", "op": "min"}}}]
        self.assertTrue(any("tie_break の fact" in x
                            for x in plan.gate_tasks(undeclared_tie, "coarse")))
        undeclared_criteria = [{"id": "f", "goal": "[scope] 候補\n残す", "deps": [], "kind": "filter",
                                "decision": {"facts": [{"name": "extra_deps", "type": "bool"}],
                                             "criteria": [{"fact": "tests", "op": "eq",
                                                           "value": "pass"}]}}]
        self.assertTrue(any("criteria の fact" in x
                            for x in plan.gate_tasks(undeclared_criteria, "coarse")))

    def test_gate_rejects_tie_break_on_filter(self):
        """順位基準が要るのは judge。filter に付けても使われず、器が崩れると宣言ごと消える。"""
        tasks = [{"id": "f", "goal": "[scope] 候補\n残す", "deps": [], "kind": "filter",
                  "decision": {"facts": [{"name": "lines", "type": "int"}],
                               "criteria": [{"fact": "lines", "op": "ne", "value": 0}],
                               "tie_break": {"fact": "lines", "op": "min"}}}]
        self.assertTrue(any("filter に tie_break" in x for x in plan.gate_tasks(tasks, "coarse")))

    def test_filter_tie_break_is_dropped_not_carried(self):
        """使われない 1 語（filter の tie_break）のために decision ごと剥がされるのを避ける。
        judge の tie_break は使われるので落とさない。"""
        out = plan.normalize_tasks([
            {"id": "f", "goal": "g", "kind": "filter",
             "decision": {"facts": [{"name": "x", "type": "bool"}],
                          "criteria": [{"fact": "x", "op": "eq", "value": True}],
                          "tie_break": {"fact": "y", "op": "min"}}},
            {"id": "j", "goal": "g", "kind": "judge",
             "decision": {"facts": [{"name": "x", "type": "int"}],
                          "criteria": [{"fact": "x", "op": "ne", "value": 0}],
                          "tie_break": {"fact": "x", "op": "min"}}}])
        by_id = {t["id"]: t for t in out}
        self.assertNotIn("tie_break", by_id["f"]["decision"])
        self.assertEqual(by_id["f"]["decision"]["criteria"][0]["fact"], "x")  # 本体は残す
        self.assertIn("tie_break", by_id["j"]["decision"])

    def test_gate_rejects_two_nodes_making_the_same_deliverable(self):
        """実測 2026-08-29: 同じテストファイルを 2 ノードが宣言した（どちらが作るのか決まらない）。"""
        tasks = [{"id": "t1", "goal": "[scope] a.py\n実装", "deps": [], "kind": "work",
                  "operation": {"operation_class": "feature",
                                "deliverables": ["eval/test_x.py"]}},
                 {"id": "t2", "goal": "[scope] test_x.py\nテスト", "deps": ["t1"], "kind": "work",
                  "operation": {"operation_class": "feature",
                                "deliverables": ["eval/test_x.py"]}}]
        self.assertTrue(any("2 ノードが作る" in x for x in plan.gate_tasks(tasks, "coarse")))

    def test_gate_rejects_a_deliverable_the_request_never_named(self):
        """実測 2026-08-30: PL5 の失敗は、要求に無い docs/... を deliverables に足した回。"""
        tasks = [{"id": "t1", "goal": "[scope] eval/humansize.py\n実装", "deps": [], "kind": "work",
                  "operation": {"operation_class": "feature",
                                "deliverables": ["eval/humansize.py", "docs/spec.md"]}}]
        issues = plan.gate_tasks(tasks, "coarse",
                                 context_text="eval/humansize.py に human_bytes を実装する")
        self.assertTrue(any("要求にも Phase 1" in x for x in issues))

    def test_gate_accepts_deliverables_the_request_named(self):
        """basename で照合する（要求が `humansize.py` とだけ書く形を落とさない）。"""
        tasks = [{"id": "t1", "goal": "[scope] eval/humansize.py\n実装", "deps": [], "kind": "work",
                  "operation": {"operation_class": "feature",
                                "deliverables": ["eval/humansize.py", "eval/test_humansize.py"]}}]
        issues = plan.gate_tasks(tasks, "coarse",
                                 context_text="humansize.py と test_humansize.py を作る")
        self.assertFalse([x for x in issues if "要求にも Phase 1" in x])

    def test_gate_is_silent_when_the_request_names_no_path(self):
        """パスを名指ししない要求では推測が planner の仕事——ここで叱らない。"""
        tasks = [{"id": "t1", "goal": "[scope] src\n実装", "deps": [], "kind": "work",
                  "operation": {"operation_class": "feature", "deliverables": ["src/a.py"]}}]
        issues = plan.gate_tasks(tasks, "coarse", context_text="集計スクリプトを作る")
        self.assertFalse([x for x in issues if "要求にも Phase 1" in x])

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


class TierPreparationTests(unittest.TestCase):
    """実行 tier（basic）のお膳立て: auto 粒度の finest 化・Phase 3 の分解指示・review 強制。"""

    def test_basic_tier_resolves_auto_to_finest(self):
        self.assertEqual(plan.resolve_granularity("auto", "simple", "basic"), "finest")
        self.assertEqual(plan.resolve_granularity(None, "moderate", "basic"), "finest")
        # 明示指定は人の意思なので tier では覆さない
        self.assertEqual(plan.resolve_granularity("coarse", "complex", "basic"), "coarse")
        # basic 以外は従来どおり complexity 導出
        self.assertEqual(plan.resolve_granularity("auto", "simple", "large"), "coarse")

    def test_phase3_injects_tier_note_only_for_basic(self):
        seen = {}

        def fake_agent(prompt, model=None):
            seen["prompt"] = prompt
            return json.dumps([
                {"id": "t1", "goal": "[scope] src/a.py\n実装", "deps": [], "kind": "work"}])

        analysis = {"subtasks": ["a"], "complexity": "moderate"}
        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase3_build("req", analysis, {"patterns": []}, None, "finest", tier="basic")
        self.assertIn("実行ティア（厳守）", seen["prompt"])
        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase3_build("req", analysis, {"patterns": []}, None, "finest")
        self.assertNotIn("実行ティア", seen["prompt"])

    def test_phase2_forces_review_for_basic_auto(self):
        catalog = {"patterns": {}, "composites": {}, "use_case_mapping": [],
                   "decision_matrix": {}}

        def fake_agent(prompt, model=None):
            return json.dumps({"patterns": ["classify-and-act"], "parallelism": 2,
                               "reason": "r"})

        analysis = {"data_flow": "static", "quality_focus": "speed", "complexity": "simple"}
        with mock.patch.object(plan, "run_agent", fake_agent):
            basic = plan.phase2_select("req", analysis, catalog, None, "auto", tier="basic")
            plain = plan.phase2_select("req", analysis, catalog, None, "auto")
            pinned = plan.phase2_select("req", analysis, catalog, None, False, tier="basic")
        self.assertTrue(basic["review"])       # basic + auto → 常時有効
        self.assertFalse(plain["review"])      # 集約なしの auto は従来 off
        self.assertFalse(pinned["review"])     # 明示 false は尊重


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


class Phase3SplitDirectiveTests(unittest.TestCase):
    """分割の単位（split_policy）の指示文。

    tier の指示文（TIER_BUILD_NOTES）と違い、スキルは文面を持たず**呼び出し側が解決済みの
    テキスト**を渡す——文面の正典は手法カタログ（split-policy-<policy>）にあり、対象
    リポジトリの `.agents/methods/` による差し替えをこの経路にも届けるため。
    """

    def _build(self, **kw):
        seen = {}

        def fake_agent(prompt, model=None):
            seen["prompt"] = prompt
            return json.dumps([
                {"id": "t1", "goal": "[scope] a.py\n実装", "deps": [], "kind": "work"}])

        analysis = {"subtasks": ["a"], "complexity": "moderate"}
        with mock.patch.object(plan, "run_agent", fake_agent):
            plan.phase3_build("req", analysis, {"patterns": []}, None, "finest", **kw)
        return seen["prompt"]

    def test_directive_text_reaches_the_build_prompt(self):
        prompt = self._build(split_directive="分割の単位: TEST-SPLIT-DIRECTIVE")
        self.assertIn("分割の単位: TEST-SPLIT-DIRECTIVE", prompt)

    def test_empty_directive_is_byte_identical_to_before(self):
        # 既定挙動不変: 渡さない場合と空文字を渡した場合でプロンプトが 1 バイトも変わらない
        self.assertEqual(self._build(), self._build(split_directive=""))

    def test_cli_accepts_the_flag_and_forwards_it(self):
        # agent-flow はスキルのソースに `--split-directive` があるかで版ずれを判定するため、
        # フラグ名そのものと plan() への配線を固定する。
        seen = {}

        def fake_plan(request, model, review, granularity, probe_root, context, tier,
                      split_directive=""):
            seen["split_directive"] = split_directive
            return {"patterns": []}, []

        argv = ["plan.py", "req", "--split-directive", "SD"]
        with mock.patch.object(plan, "plan", fake_plan), \
             mock.patch.object(plan.sys, "argv", argv), \
             mock.patch.object(plan.sys, "stdout", io.StringIO()):
            plan.main()
        self.assertEqual(seen["split_directive"], "SD")


if __name__ == "__main__":
    unittest.main()
