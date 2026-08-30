import json
import pathlib
import unittest

from agentcore import nodecontract


class NodeContractTests(unittest.TestCase):
    def test_human_requires_engine_resolution_shape(self):
        data = {"interaction_id": "ix-1234567890abcdef", "outcome": "approved",
                "actor": "dashboard-user", "answer": {"decision": "approved"}}
        self.assertEqual(nodecontract.validate_node_data("human", data), data)
        with self.assertRaisesRegex(nodecontract.NodeDataError, "outcome"):
            nodecontract.validate_node_data("human", {**data, "outcome": "unknown"})

    def test_public_kinds_match_schema(self):
        schema = pathlib.Path(__file__).parents[4] / "schemas" / "agent-node-data.schema.json"
        raw = json.loads(schema.read_text(encoding="utf-8"))
        self.assertEqual(set(raw["properties"]["kind"]["enum"]), nodecontract.VALID_KINDS)
        self.assertNotIn("human", nodecontract.PLANNER_KINDS)
        self.assertEqual(nodecontract.VALID_KINDS - {"human"}, nodecontract.PLANNER_KINDS)

    def test_extract_requires_evidence_for_every_record(self):
        data = {
            "records": [{
                "fields": {"name": "example"},
                "evidence": [{"source_id": "input", "locator": "line:1", "excerpt": "example"}],
            }],
            "warnings": [],
        }
        self.assertEqual(nodecontract.validate_node_data("extract", data), data)
        with self.assertRaisesRegex(nodecontract.NodeDataError, "evidence"):
            nodecontract.validate_node_data("extract", {
                "records": [{"fields": {"name": "example"}, "evidence": []}], "warnings": [],
            })

    def test_retrieve_requires_traceable_sources(self):
        data = {
            "sources": [{
                "id": "source-1", "uri": "repo://README.md", "title": "README",
                "locator": "line:1", "excerpt": "example", "digest": "sha256:abc",
            }],
            "warnings": [],
        }
        self.assertEqual(nodecontract.validate_node_data("retrieve", data), data)
        with self.assertRaisesRegex(nodecontract.NodeDataError, "digest"):
            nodecontract.validate_node_data("retrieve", {
                "sources": [{
                    "id": "source-1", "uri": "repo://README.md", "title": "README",
                    "locator": "line:1", "excerpt": "example", "digest": "",
                }],
                "warnings": [],
            })


class OperationContractTest(unittest.TestCase):
    CONTRACT = {
        "operation_class": "existing-test-repair",
        "scope": {"read": ["src/format.py", "tests/test_format.py"],
                  "write": ["src/format.py"],
                  "protected": ["schemas/", "docs/architecture/"]},
        "deliverables": ["src/format.py"],
        "acceptance": ["pytest tests/test_format.py が成功する"],
        "verification": {"commands": [["pytest", "tests/test_format.py"]]},
    }

    def test_design_example_passes(self):
        self.assertEqual(nodecontract.operation_contract_errors(self.CONTRACT), [])

    def test_shape_errors(self):
        self.assertTrue(nodecontract.operation_contract_errors("x"))
        self.assertTrue(nodecontract.operation_contract_errors({}))
        broken = dict(self.CONTRACT, verification={"commands": ["pytest tests"]})
        self.assertTrue(any("argv" in e for e in
                            nodecontract.operation_contract_errors(broken)))

    def test_local_patch_eligible(self):
        self.assertEqual(nodecontract.local_patch_blockers(self.CONTRACT), [])
        self.assertEqual(nodecontract.local_patch_blockers(
            self.CONTRACT, existing_paths=["src/format.py"]), [])

    def test_local_patch_blockers(self):
        multi = dict(self.CONTRACT,
                     scope=dict(self.CONTRACT["scope"], write=["src/a.py", "src/b.py"]))
        self.assertTrue(any("1 ファイル" in b for b in
                            nodecontract.local_patch_blockers(multi)))
        no_check = dict(self.CONTRACT, verification=None)
        self.assertTrue(any("verification" in b for b in
                            nodecontract.local_patch_blockers(no_check)))
        new_test = dict(self.CONTRACT,
                        scope=dict(self.CONTRACT["scope"], write=["tests/test_new.py"]),
                        deliverables=["tests/test_new.py"])
        self.assertTrue(any("対象外" in b for b in
                            nodecontract.local_patch_blockers(new_test)))
        protected = dict(self.CONTRACT,
                         scope=dict(self.CONTRACT["scope"], write=["schemas/x.json"]),
                         deliverables=["schemas/x.json"])
        self.assertTrue(any("protected" in b for b in
                            nodecontract.local_patch_blockers(protected)))
        new_file = nodecontract.local_patch_blockers(
            self.CONTRACT, existing_paths=["src/other.py"])
        self.assertTrue(any("新規ファイル" in b for b in new_file))
        mismatch = dict(self.CONTRACT, deliverables=["src/other.py"])
        self.assertTrue(any("書込 scope" in b for b in
                            nodecontract.local_patch_blockers(mismatch)))


class DecideCandidatesTest(unittest.TestCase):
    FACTS = [
        {"id": "c1", "tests": "pass", "extra_deps": True, "lines": 30},
        {"id": "c2", "tests": "fail", "extra_deps": False, "lines": 48},
        {"id": "c3", "tests": "pass", "extra_deps": False, "lines": 41},
        {"id": "c4", "tests": "pass", "extra_deps": True, "lines": 27},
        {"id": "c5", "tests": "fail", "extra_deps": False, "lines": 35},
        {"id": "c6", "tests": "none", "extra_deps": False, "lines": 52},
    ]

    def test_filter_single_criterion(self):
        decision = nodecontract.decide_candidates(
            [{"fact": "extra_deps", "op": "eq", "value": False}], self.FACTS)
        self.assertEqual(decision["kept"], ["c2", "c3", "c5", "c6"])
        self.assertEqual(decision["undecided"], [])
        self.assertIsNone(decision["winner"])

    def test_judge_multi_criteria_with_tie_break(self):
        decision = nodecontract.decide_candidates(
            [{"fact": "tests", "op": "eq", "value": "pass"},
             {"fact": "extra_deps", "op": "eq", "value": False}],
            self.FACTS, tie_break={"fact": "lines", "op": "min"})
        self.assertEqual(decision["kept"], ["c3"])
        self.assertEqual(decision["winner"], "c3")

    def test_missing_fact_goes_undecided_and_blocks_winner(self):
        facts = [dict(f) for f in self.FACTS]
        del facts[2]["extra_deps"]      # c3 の事実が欠測
        decision = nodecontract.decide_candidates(
            [{"fact": "tests", "op": "eq", "value": "pass"},
             {"fact": "extra_deps", "op": "eq", "value": False}],
            facts, tie_break={"fact": "lines", "op": "min"})
        self.assertIn("c3", decision["undecided"])
        self.assertIsNone(decision["winner"], "欠測があるのに確定しない")

    def test_tie_break_equal_uses_id_order_and_max(self):
        facts = [{"id": "b", "score": 5}, {"id": "a", "score": 5}, {"id": "c", "score": 3}]
        decision = nodecontract.decide_candidates(
            [], facts, tie_break={"fact": "score", "op": "max"})
        self.assertEqual(decision["winner"], "a")
    def test_single_survivor_wins_without_tie_break(self):
        decision = nodecontract.decide_candidates(
            [{"fact": "tests", "op": "eq", "value": "pass"},
             {"fact": "extra_deps", "op": "eq", "value": False}], self.FACTS)
        self.assertEqual(decision["winner"], "c3", "条件だけで 1 つに絞れたら順位基準は要らない")


class DecisionContractTest(unittest.TestCase):
    DECISION = {
        "facts": [{"name": "extra_deps", "type": "bool", "description": "追加依存が要るか"},
                  {"name": "tests", "type": "string", "values": ["pass", "fail", "none"]},
                  {"name": "lines", "type": "int"}],
        "criteria": [{"fact": "extra_deps", "op": "eq", "value": False}],
        "tie_break": {"fact": "lines", "op": "min"},
    }

    def test_valid_contract_has_no_errors(self):
        self.assertEqual(nodecontract.decision_contract_errors(self.DECISION), [])

    def test_unknown_fact_names_are_errors(self):
        bad = dict(self.DECISION, criteria=[{"fact": "speed", "op": "eq", "value": 1}])
        self.assertTrue(any("criteria の fact" in e
                            for e in nodecontract.decision_contract_errors(bad)))
        bad = dict(self.DECISION, tie_break={"fact": "speed", "op": "min"})
        self.assertTrue(any("tie_break の fact" in e
                            for e in nodecontract.decision_contract_errors(bad)))

    def test_shape_errors(self):
        for bad, hint in (
                ({"facts": [], "criteria": []}, "facts"),
                ({"facts": [{"name": "x", "type": "float"}], "criteria": []}, "type"),
                ({"facts": [{"name": "id", "type": "bool"}], "criteria": []}, "id"),
                ({"facts": [{"name": "x", "type": "bool"}],
                  "criteria": [{"fact": "x", "op": "gt", "value": 1}]}, "op"),
                ({"facts": [{"name": "x", "type": "bool"}], "criteria": "no"}, "criteria"),
        ):
            with self.subTest(hint=hint):
                self.assertTrue(any(hint in e for e in nodecontract.decision_contract_errors(bad)))

    def test_normalize_facts_keeps_missing_values_as_none(self):
        facts = nodecontract.normalize_facts(self.DECISION, {"facts": [
            {"id": "c1", "extra_deps": False, "tests": "PASS", "lines": 30},
            {"id": "c2", "extra_deps": "no", "tests": "unknown", "lines": "48"},
            {"id": "", "extra_deps": False},          # id 無しは候補にならない
        ]})
        self.assertEqual(facts[0], {"id": "c1", "extra_deps": False, "tests": "pass", "lines": 30})
        self.assertEqual(facts[1], {"id": "c2", "extra_deps": None, "tests": None, "lines": None})
        self.assertEqual(len(facts), 2)

    def test_normalize_facts_accepts_a_bare_list(self):
        facts = nodecontract.normalize_facts(self.DECISION, [{"id": "c1", "extra_deps": True}])
        self.assertEqual(facts[0]["extra_deps"], True)

    def test_directive_asks_for_facts_not_for_a_verdict(self):
        text = nodecontract.fact_extraction_directive(self.DECISION)
        self.assertIn('"facts"', text)
        self.assertIn("判定・最良案の選択はしない", text)
        self.assertIn("- tests: \"pass\" / \"fail\" / \"none\"", text)
        self.assertNotIn("extra_deps が false", text)   # 条件そのものは載せない


class DeliverableSlotTest(unittest.TestCase):
    NODE = {
        "id": "t1", "kind": "work", "deps": ["t0"], "goal": "スキーマを足し、契約テストも足す",
        "operation": {
            "operation_class": "feature",
            "scope": {"read": ["tools/agent-project"],
                      "write": ["schemas/s.json", "tests/test_s.py"]},
            "deliverables": ["schemas/s.json", "tests/test_s.py"],
            "verification": {"commands": [["python", "-m", "pytest", "-q", "tests"]]},
        },
    }

    def test_two_deliverables_become_a_chain_of_one_slot_each(self):
        slots = nodecontract.split_by_deliverables(self.NODE)
        self.assertEqual([s["id"] for s in slots], ["t1-d1", "t1-d2"])
        self.assertEqual(slots[0]["deps"], ["t0"])          # 先頭は元の依存を継ぐ
        self.assertEqual(slots[1]["deps"], ["t1-d1"])       # 直列（同時に 2 つ渡さない）
        for slot, want in zip(slots, self.NODE["operation"]["deliverables"]):
            self.assertEqual(slot["operation"]["deliverables"], [want])
            self.assertEqual(slot["operation"]["scope"]["write"], [want])
            self.assertEqual(slot["operation"]["scope"]["read"], ["tools/agent-project"])
            self.assertIn(want, slot["goal"])
            self.assertIn("1 つだけ", slot["goal"])
        self.assertIn("tests/test_s.py", slots[0]["goal"])  # 他スロットは触らないと明示する

    def test_each_slot_passes_the_local_patch_gate(self):
        """スロットは書込 1 ファイル・成果物 1 つになるので、局所修正の適格判定を通る
        （テスト / schema / 文書は元々対象外なので、ここでは実装ファイルで測る）。"""
        node = dict(self.NODE, operation=dict(
            self.NODE["operation"], deliverables=["src/a.py", "src/b.py"],
            scope={"read": ["src"], "write": ["src/a.py", "src/b.py"]}))
        for slot in nodecontract.split_by_deliverables(node):
            self.assertEqual(nodecontract.local_patch_blockers(
                slot["operation"], existing_paths=["src/a.py", "src/b.py"]), [], slot["id"])

    def test_not_split(self):
        cases = {
            "成果物 1 つ": dict(self.NODE, operation=dict(
                self.NODE["operation"], deliverables=["schemas/s.json"])),
            "処理契約なし": {k: v for k, v in self.NODE.items() if k != "operation"},
            "壊れた契約": dict(self.NODE, operation={"scope": {"write": "x"}}),
            "対象外の kind": dict(self.NODE, kind="verify"),
            "上限超え": dict(self.NODE, operation=dict(
                self.NODE["operation"], deliverables=[f"a{i}.py" for i in range(5)])),
        }
        for hint, node in cases.items():
            with self.subTest(hint=hint):
                self.assertIsNone(nodecontract.split_by_deliverables(node))

    def test_write_scope_is_not_invented_when_the_slot_was_not_declared(self):
        node = dict(self.NODE, operation=dict(
            self.NODE["operation"], scope={"write": ["schemas/s.json"]}))
        slots = nodecontract.split_by_deliverables(node)
        self.assertEqual(slots[0]["operation"]["scope"]["write"], ["schemas/s.json"])
        self.assertNotIn("scope", slots[1]["operation"])   # 宣言に無い書込先を作らない


class DependencyGapTests(unittest.TestCase):
    """依存が申告した欠落を、集約系の成果へ機械が運ぶ（統合役は落とす: SY2 0/5）。"""

    DEPS = {
        "t1": {"output": "12 件中 10 件を索引にまとめた",
               "data": {"warnings": ["ITEM-11.md と ITEM-12.md は読み取りに失敗"]}},
        "t2": {"output": "report.md へ書き出した", "data": {"issues": ["行数は 10 行"]}},
        "t3": {"output": "自由記述だけの依存（本文に欠落を書いている）", "data": None},
    }

    def test_collects_only_structured_declarations(self):
        gaps = nodecontract.collect_dependency_gaps(self.DEPS)
        self.assertEqual([g[0] for g in gaps], ["t1", "t2"])   # 散文の t3 は拾わない
        self.assertIn("ITEM-11.md", gaps[0][1])

    def test_carries_what_the_model_dropped(self):
        text, data = nodecontract.carry_dependency_gaps(self.DEPS, "索引を作成しました。", None)
        self.assertIn(nodecontract.GAP_HEADING, text)
        self.assertIn("ITEM-11.md と ITEM-12.md は読み取りに失敗", text)
        self.assertEqual([g["dep"] for g in data["gaps"]], ["t1", "t2"])

    def test_does_not_repeat_what_the_model_already_carried(self):
        body = ("ITEM-11.md と ITEM-12.md は読み取りに失敗したため未収録。"
                "行数は 10 行。")
        text, data = nodecontract.carry_dependency_gaps(self.DEPS, body, None)
        self.assertEqual(text, body)
        self.assertEqual(len(data["gaps"]), 2)   # 運搬済みでも記録は残す

    def test_no_declaration_leaves_the_result_untouched(self):
        """申告が無ければ data を dict へ変えない——下流の形を無意味に動かさない。"""
        self.assertEqual(
            nodecontract.carry_dependency_gaps({"t1": {"output": "x", "data": None}}, "text", None),
            ("text", None))


if __name__ == "__main__":
    unittest.main()
