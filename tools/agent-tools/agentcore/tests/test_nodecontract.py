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


if __name__ == "__main__":
    unittest.main()
