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


if __name__ == "__main__":
    unittest.main()
