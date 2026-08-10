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


if __name__ == "__main__":
    unittest.main()
