"""列挙駆動の分解（案1 enumerability 軸 ＋ 案2 軽量 probe）の単体テスト。

守りたい性質は 2 つ:
  1. 「同一手順×独立多対象」が map-reduce に届く（従来は fan-out へ吸われていた）
  2. **他パターンを侵食しない**（3 条件のどれかが偽・件数 ≤3 なら従来経路と完全に同一）
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock


def _load_plan():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    path = os.path.join(root, ".github", "skills", "flow-planner", "scripts", "plan.py")
    spec = importlib.util.spec_from_file_location("flow_planner_plan_enum", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


plan = _load_plan()


def _enum(**over):
    base = {"same_procedure": True, "independent": True, "per_target_deliverable": True,
            "target_kind": "API エンドポイント", "how_to_enumerate": "src/routes/ を走査",
            "estimated_count": None}
    base.update(over)
    return base


class NormalizeEnumerableTests(unittest.TestCase):
    def test_is_enumerable_is_the_and_of_three_conditions(self):
        self.assertTrue(plan.normalize_enumerable(_enum())["is_enumerable"])
        for k in plan.ENUMERABLE_CONDITIONS:
            got = plan.normalize_enumerable(_enum(**{k: False}))
            self.assertFalse(got["is_enumerable"], k)

    def test_missing_or_garbage_input_is_not_enumerable(self):
        for bad in (None, {}, [], "yes", 3):
            self.assertFalse(plan.normalize_enumerable(bad)["is_enumerable"], bad)

    def test_accepts_the_bool_shapes_an_llm_returns(self):
        got = plan.normalize_enumerable(_enum(same_procedure="true", independent="yes",
                                              per_target_deliverable=1))
        self.assertTrue(got["is_enumerable"])

    def test_count_is_normalized_and_unreadable_becomes_none(self):
        self.assertEqual(plan.normalize_enumerable(_enum(estimated_count="12件"))["estimated_count"], 12)
        for bad in (None, "たぶん", 0, -1, True):
            self.assertIsNone(plan.normalize_enumerable(_enum(estimated_count=bad))["estimated_count"])


class EnumerationDecisionTests(unittest.TestCase):
    """ハイブリッド発動: force（件数確定>3）/ boost（件数不明）/ off（条件未充足・少数）。"""

    def test_force_when_conditions_met_and_count_known(self):
        d = plan.enumeration_decision(plan.normalize_enumerable(_enum(estimated_count=12)))
        self.assertEqual(d["mode"], "force")
        self.assertEqual(d["count"], 12)

    def test_boost_when_count_unknown(self):
        d = plan.enumeration_decision(plan.normalize_enumerable(_enum()))
        self.assertEqual(d["mode"], "boost")
        self.assertIsNone(d["count"])

    def test_off_when_few_targets(self):
        for n in (1, 2, 3):
            d = plan.enumeration_decision(plan.normalize_enumerable(_enum(estimated_count=n)))
            self.assertEqual(d["mode"], "off", n)

    def test_off_when_any_condition_fails(self):
        """単一成果物の実装（多数ファイルに触るが独立でない）は列挙駆動にしない。"""
        d = plan.enumeration_decision(
            plan.normalize_enumerable(_enum(independent=False, estimated_count=30)))
        self.assertEqual(d["mode"], "off")
        self.assertIn("対象間に依存が無い", d["reason"])

    def test_probe_overrides_the_llm_estimate(self):
        enum = plan.normalize_enumerable(_enum(estimated_count=2))
        self.assertEqual(plan.enumeration_decision(enum, probed=40)["mode"], "force")
        enum = plan.normalize_enumerable(_enum(estimated_count=40))
        self.assertEqual(plan.enumeration_decision(enum, probed=2)["mode"], "off")

    def test_reason_is_always_present_for_observability(self):
        for enum, probed in ((_enum(), None), (_enum(estimated_count=9), 9),
                             (_enum(same_procedure=False), None)):
            d = plan.enumeration_decision(plan.normalize_enumerable(enum), probed)
            self.assertTrue(d["reason"].strip())


class ProbeTests(unittest.TestCase):
    """決定的走査（LLM 無し）。数えられなければ None＝不明（従来動作へ倒す）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "src", "routes"))
        for i in range(7):
            with open(os.path.join(self.root, "src", "routes", f"r{i}.ts"), "w") as f:
                f.write("x")
        os.makedirs(os.path.join(self.root, "src", "routes", "node_modules"))
        with open(os.path.join(self.root, "src", "routes", "node_modules", "dep.ts"), "w") as f:
            f.write("x")
        self.addCleanup(self.tmp.cleanup)

    def test_counts_via_glob(self):
        self.assertEqual(plan.probe_target_count("src/routes/**/*.ts を走査", self.root), 7)

    def test_counts_via_directory_when_no_glob(self):
        self.assertEqual(plan.probe_target_count("src/routes/ 配下を走査", self.root), 7)

    def test_skips_dependency_dirs(self):
        """node_modules 等は成果物の対象ではない（数えると件数が水増しされる）。"""
        self.assertEqual(plan.probe_target_count("src/ 配下", self.root), 7)

    def test_unknown_paths_are_none_not_zero(self):
        """0 を『対象なし』と読むと、ワークスペース未取得の計画で列挙駆動を誤って止める。"""
        self.assertIsNone(plan.probe_target_count("nope/**/*.ts", self.root))
        self.assertIsNone(plan.probe_target_count("どこかを調べる", self.root))
        self.assertIsNone(plan.probe_target_count("", self.root))

    def test_probe_runs_only_when_conditions_are_met(self):
        analysis = {"enumerable": _enum(same_procedure=False)}
        with mock.patch.object(plan, "probe_target_count") as p:
            plan.resolve_enumeration(analysis, self.root)
        p.assert_not_called()

    def test_resolve_enumeration_records_probe_result(self):
        analysis = {"enumerable": _enum(how_to_enumerate="src/routes/**/*.ts")}
        d = plan.resolve_enumeration(analysis, self.root)
        self.assertEqual(d["mode"], "force")
        self.assertEqual(analysis["enumerable"]["probed_count"], 7)


class ScoringTests(unittest.TestCase):
    def _catalog(self):
        return {"patterns": {"fan-out-and-synthesize": {}, "map-reduce": {}},
                "decision_matrix": {"data_flow": {"static": {"fan-out-and-synthesize": 2}}},
                "use_case_mapping": []}

    def test_boost_lifts_map_reduce_over_static_fanout(self):
        """静的な一覧（リポジトリ内の API 群）が fan-out へ吸われるのを覆せること。"""
        analysis = {"data_flow": "static", "enumeration_decision": {"mode": "boost"}}
        ranked = dict(plan.score_patterns(self._catalog(), analysis))
        self.assertGreater(ranked["map-reduce"], ranked["fan-out-and-synthesize"])

    def test_no_boost_when_off(self):
        analysis = {"data_flow": "static", "enumeration_decision": {"mode": "off"}}
        ranked = dict(plan.score_patterns(self._catalog(), analysis))
        self.assertEqual(ranked["map-reduce"], 0)
        self.assertGreater(ranked["fan-out-and-synthesize"], ranked["map-reduce"])

    def test_force_does_not_double_dip_the_score(self):
        """force は Phase 2 の後で決定的に強制する。スコアは触らない（二重の効き目を作らない）。"""
        analysis = {"data_flow": "static", "enumeration_decision": {"mode": "force"}}
        ranked = dict(plan.score_patterns(self._catalog(), analysis))
        self.assertEqual(ranked["map-reduce"], 0)


class Phase2ForceTests(unittest.TestCase):
    def _select(self, analysis, llm_patterns):
        catalog = {"patterns": {"fan-out-and-synthesize": {"description": "d",
                                                           "when_to_use": [], "typical_parallelism": [2, 4]},
                                "map-reduce": {"description": "d", "when_to_use": [],
                                               "typical_parallelism": [5, 50]}},
                   "composites": {}, "use_case_mapping": [], "decision_matrix": {}}
        out = json.dumps({"patterns": llm_patterns, "parallelism": 3, "reason": "LLM の理由"})
        with mock.patch.object(plan, "run_agent", return_value=out):
            return plan.phase2_select("req", analysis, catalog, None, "auto")

    def test_force_injects_map_reduce_even_if_llm_picked_another(self):
        analysis = {"enumeration_decision": {"mode": "force", "reason": "対象 12 件"},
                    "enumerable": _enum(estimated_count=12)}
        s = self._select(analysis, ["fan-out-and-synthesize"])
        self.assertEqual(s["patterns"][0], "map-reduce")
        self.assertIn("fan-out-and-synthesize", s["patterns"])   # 複合は潰さない
        self.assertIn("列挙駆動", s["reason"])
        self.assertIn("LLM の理由", s["reason"])

    def test_force_does_not_duplicate_when_llm_already_chose_it(self):
        analysis = {"enumeration_decision": {"mode": "force", "reason": "r"},
                    "enumerable": _enum()}
        s = self._select(analysis, ["map-reduce", "adversarial-verification"])
        self.assertEqual(s["patterns"].count("map-reduce"), 1)

    def test_off_leaves_the_llm_choice_untouched(self):
        analysis = {"enumeration_decision": {"mode": "off", "reason": "r"},
                    "enumerable": _enum(independent=False)}
        s = self._select(analysis, ["fan-out-and-synthesize"])
        self.assertEqual(s["patterns"], ["fan-out-and-synthesize"])
        self.assertNotIn("列挙駆動", s["reason"])


class EnumerationNoteTests(unittest.TestCase):
    def test_note_is_empty_when_off(self):
        analysis = {"enumeration_decision": {"mode": "off"}, "enumerable": _enum()}
        self.assertEqual(plan.enumeration_note(analysis, "select"), "")
        self.assertEqual(plan.enumeration_note(analysis, "build"), "")

    def test_build_note_carries_the_enumeration_procedure(self):
        analysis = {"enumeration_decision": {"mode": "force", "count": 12, "reason": "r"},
                    "enumerable": _enum(how_to_enumerate="src/routes/**/*.ts を走査")}
        note = plan.enumeration_note(analysis, "build")
        self.assertIn("src/routes/**/*.ts を走査", note)
        self.assertIn("split ノードを 1 つだけ", note)
        self.assertIn("推測で列挙しない", note)

    def test_select_note_distinguishes_force_from_boost(self):
        enum = {"enumerable": _enum()}
        forced = plan.enumeration_note({**enum, "enumeration_decision": {"mode": "force", "reason": "r"}}, "select")
        boosted = plan.enumeration_note({**enum, "enumeration_decision": {"mode": "boost", "reason": "r"}}, "select")
        self.assertIn("強制", forced)
        self.assertIn("第一候補", boosted)


class Phase3SplitGateTests(unittest.TestCase):
    def test_gate_requires_split_when_forced(self):
        tasks = [{"id": "t1", "goal": "[scope] a.py\n作業", "deps": [], "kind": "work"}]
        issues = plan.gate_tasks(tasks, "coarse", require_split=True)
        self.assertTrue(any("split" in i for i in issues))

    def test_gate_passes_with_split_present(self):
        tasks = [{"id": "s1", "goal": "[scope] src/\n列挙", "deps": [], "kind": "split"}]
        self.assertEqual(plan.gate_tasks(tasks, "fine", require_split=True), [])

    def test_gate_is_unchanged_without_the_flag(self):
        tasks = [{"id": "t1", "goal": "[scope] a.py\n作業", "deps": [], "kind": "work"}]
        self.assertEqual(plan.gate_tasks(tasks, "coarse"), [])

    def test_phase3_retries_once_when_split_is_missing(self):
        no_split = [{"id": "t1", "goal": "[scope] a.py\n作業", "deps": [], "kind": "work"}]
        with_split = [{"id": "s1", "goal": "[scope] src/\n実際に走査して一覧化", "deps": [], "kind": "split"}]
        calls = {"n": 0}

        def fake_agent(prompt, model=None):
            calls["n"] += 1
            return json.dumps(no_split if calls["n"] == 1 else with_split)

        analysis = {"subtasks": [], "complexity": "simple",
                    "enumeration_decision": {"mode": "force", "count": 12, "reason": "r"},
                    "enumerable": _enum(estimated_count=12)}
        with mock.patch.object(plan, "run_agent", side_effect=fake_agent):
            tasks = plan.phase3_build("req", analysis, {"patterns": ["map-reduce"]}, None, "coarse")
        self.assertEqual(calls["n"], 2)
        self.assertEqual([t["kind"] for t in tasks], ["split"])


class EndToEndPipelineTests(unittest.TestCase):
    """API ドキュメント化（今回の症状）が split を持つグラフになること。"""

    def _run_plan(self, enumerable, tasks, llm_patterns=("fan-out-and-synthesize",)):
        analysis = {"intent": "API をドキュメント化する", "subtasks": ["調査", "執筆"],
                    "data_flow": "static", "quality_focus": "coverage",
                    "complexity": "moderate", "estimated_steps": 5,
                    "domain_hints": ["ドキュメント"], "enumerable": enumerable}
        replies = [json.dumps(analysis),
                   json.dumps({"patterns": list(llm_patterns), "parallelism": 4, "reason": "r"}),
                   json.dumps(tasks)]
        it = iter(replies)
        catalog = {"patterns": {"fan-out-and-synthesize": {"description": "d", "when_to_use": [],
                                                           "typical_parallelism": [3, 6]},
                                "map-reduce": {"description": "d", "when_to_use": [],
                                               "typical_parallelism": [5, 50]}},
                   "composites": {}, "use_case_mapping": [], "decision_matrix": {}}
        with mock.patch.object(plan, "load_catalog", return_value=catalog), \
             mock.patch.object(plan, "run_agent", side_effect=lambda *a, **k: next(it)):
            return plan.plan("API のドキュメント化", None, "auto", "auto", ".")

    def test_forced_run_produces_a_split_graph(self):
        split_graph = [{"id": "s1", "goal": "[scope] src/routes/\n実際に走査して API を列挙する",
                        "deps": [], "kind": "split"}]
        strategy, tasks = self._run_plan(_enum(estimated_count=12), split_graph)
        self.assertIn("map-reduce", strategy["patterns"])
        self.assertEqual(strategy["enumeration"]["mode"], "force")
        self.assertEqual([t["kind"] for t in tasks], ["split"])

    def test_non_enumerable_run_is_untouched(self):
        """単一成果物の実装は従来どおり（パターンも grafも変わらない）。"""
        fanout = [{"id": f"t{i}", "goal": f"[scope] src/m{i}.py\n実装", "deps": [], "kind": "work"}
                  for i in range(1, 5)]
        strategy, tasks = self._run_plan(_enum(independent=False), fanout)
        self.assertEqual(strategy["patterns"], ["fan-out-and-synthesize"])
        self.assertEqual(strategy["enumeration"]["mode"], "off")
        self.assertEqual(len(tasks), 4)


if __name__ == "__main__":
    unittest.main()
