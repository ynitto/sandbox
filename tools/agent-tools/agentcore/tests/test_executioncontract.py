"""段A（候補ベース実行の契約固定）の契約テスト。

schema の examples を共通 fixture として executioncontract の検証に通し、
schema 側 enum とモジュール定数の一致を縛る（語彙の 2 実装を作らない——C7）。
E1（Resolver）と U2（Compiler）はここと同じ fixture から契約テストを書き始める。
"""
import copy
import json
import pathlib
import unittest

from agentcore import executioncontract as ec

SCHEMAS = pathlib.Path(__file__).parents[4] / "schemas"


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


class QualificationsContractTests(unittest.TestCase):
    def setUp(self):
        self.schema = _schema("agent-candidate-qualifications.schema.json")

    def test_examples_validate_clean(self):
        for example in self.schema["examples"]:
            self.assertEqual(ec.qualifications_errors(example), [])

    def test_enums_match_module_constants(self):
        defs = self.schema["$defs"]
        self.assertEqual(tuple(defs["qualification"]["properties"]["status"]["enum"]),
                         ec.QUALIFICATION_STATUSES)
        self.assertEqual(tuple(defs["qualification"]["properties"]["source"]["enum"]),
                         ec.QUALIFICATION_SOURCES)
        self.assertEqual(tuple(defs["candidate"]["properties"]["execution_site"]["enum"]),
                         ec.EXECUTION_SITES)
        self.assertEqual(self.schema["properties"]["version"]["const"],
                         ec.QUALIFICATIONS_VERSION)

    def test_auto_selectable_is_qualified_only(self):
        # trial は Envelope 明示承認 run 限定、blocked / unknown は自動選択不可（設計 §5.2 / §6.3）。
        self.assertEqual(ec.AUTO_SELECTABLE_STATUSES, {"qualified"})
        self.assertLess(ec.AUTO_SELECTABLE_STATUSES, set(ec.QUALIFICATION_STATUSES))

    def test_rejects_broken_shapes(self):
        base = copy.deepcopy(self.schema["examples"][0])
        qual = base["candidates"][0]["qualifications"]["existing-test-repair"]

        bad = copy.deepcopy(base)
        bad["candidates"][0]["qualifications"]["existing-test-repair"]["status"] = "great"
        self.assertTrue(any("status" in e for e in ec.qualifications_errors(bad)))

        bad = copy.deepcopy(base)
        bad["candidates"][0]["qualifications"]["existing-test-repair"]["passed"] = qual["samples"] + 1
        self.assertTrue(any("passed" in e for e in ec.qualifications_errors(bad)))

        bad = copy.deepcopy(base)
        bad["candidates"][0]["qualifications"]["existing-test-repair"]["evaluation_profile_id"] = "no-such-profile"
        self.assertTrue(any("evaluation_profile_id" in e for e in ec.qualifications_errors(bad)))

        bad = copy.deepcopy(base)
        bad["version"] = 2
        self.assertTrue(any("version" in e for e in ec.qualifications_errors(bad)))


class SelectionPolicyContractTests(unittest.TestCase):
    def setUp(self):
        self.schema = _schema("agent-control.schema.json")
        self.example = self.schema["examples"][0]
        self.policy = self.example["workloads"]["flow"]["selection_policy"]

    def test_control_example_policy_validates_clean(self):
        self.assertEqual(ec.selection_policy_errors(self.policy), [])

    def test_control_example_dual_writes_legacy_fallback(self):
        # 移行契約: v2 の workload は旧 reader 向け単一 fallback を併記する（設計 §6.6）。
        flow = self.example["workloads"]["flow"]
        self.assertEqual(self.example["version"], ec.CONTROL_VERSION_SELECTION)
        self.assertTrue(flow.get("agent_cli") and flow.get("model"))

    def test_enums_match_module_constants(self):
        defs = self.schema["$defs"]
        policy_props = defs["selection_policy"]["properties"]
        self.assertEqual(tuple(policy_props["strategy"]["enum"]), ec.STRATEGIES)
        self.assertEqual(tuple(policy_props["no_candidate"]["enum"]), ec.NO_CANDIDATE_ACTIONS)
        self.assertEqual(defs["control"]["properties"]["version"]["enum"],
                         [1, ec.CONTROL_VERSION_SELECTION])
        self.assertIn("selection_policy", defs["workload_control"]["properties"])

    def test_rejects_broken_shapes(self):
        bad = copy.deepcopy(self.policy)
        bad["strategy"] = "cheapest"
        self.assertTrue(any("strategy" in e for e in ec.selection_policy_errors(bad)))

        bad = copy.deepcopy(self.policy)
        bad["candidates"] = []
        self.assertTrue(any("candidates" in e for e in ec.selection_policy_errors(bad)))

        bad = copy.deepcopy(self.policy)
        del bad["candidates"][0]["rank"]
        self.assertTrue(any("rank" in e for e in ec.selection_policy_errors(bad)))

        bad = copy.deepcopy(self.policy)
        bad["no_candidate"] = "degrade"
        self.assertTrue(any("no_candidate" in e for e in ec.selection_policy_errors(bad)))


class ExecutionReceiptContractTests(unittest.TestCase):
    def setUp(self):
        self.schema = _schema("execution-receipt.schema.json")

    def test_examples_validate_clean(self):
        for example in self.schema["examples"]:
            self.assertEqual(ec.execution_receipt_errors(example), [])

    def test_enums_match_module_constants(self):
        defs = self.schema["$defs"]
        self.assertEqual(
            tuple(defs["execution_decision"]["properties"]["selection_source"]["enum"]),
            ec.SELECTION_SOURCES)
        # 検査 verdict の語彙は verification-receipt と同一（verifycontract が正典）。
        self.assertEqual(tuple(defs["verification"]["properties"]["verdict"]["enum"]),
                         ec.VERIFICATION_VERDICTS)

    def test_candidate_id_matches_example_notation(self):
        example = self.schema["examples"][0]
        decision = example["execution_decision"]
        self.assertIn(ec.candidate_id(decision["agent_cli"], decision["model"]),
                      decision["eligible_candidate_ids"])

    def test_rejects_broken_shapes(self):
        base = self.schema["examples"][0]

        bad = copy.deepcopy(base)
        del bad["attempt_id"]
        self.assertTrue(any("attempt_id" in e for e in ec.execution_receipt_errors(bad)))

        bad = copy.deepcopy(base)
        del bad["execution_decision"]
        self.assertTrue(any("execution_decision" in e for e in ec.execution_receipt_errors(bad)))

        bad = copy.deepcopy(base)
        bad["execution_decision"]["selection_source"] = "local"
        self.assertTrue(any("selection_source" in e for e in ec.execution_receipt_errors(bad)))

        bad = copy.deepcopy(base)
        bad["verification"]["verdict"] = "ok"
        self.assertTrue(any("verdict" in e for e in ec.execution_receipt_errors(bad)))

        bad = copy.deepcopy(base)
        bad["resource_snapshot"]["budget_remaining"] = 1.5
        self.assertTrue(any("budget_remaining" in e for e in ec.execution_receipt_errors(bad)))


class ResolutionOrderTests(unittest.TestCase):
    def test_resolution_order_is_five_rungs(self):
        # 設計 §6.6: envelope pin → selection_policy → purpose override → workload 単一 → profiles 既定。
        self.assertEqual(ec.RESOLUTION_ORDER,
                         ("envelope-pin", "selection-policy", "purpose-override",
                          "workload-single", "profiles-default"))


if __name__ == "__main__":
    unittest.main()
