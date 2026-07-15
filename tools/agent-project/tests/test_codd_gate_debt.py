"""codd_gate_debt の単体テスト（標準ライブラリ unittest）。

`codd-gate tasks --debt` の stdout パースを、object 1件・array 複数件・空・非 JSON・
レコード単位の不備（非 object・title 欠落）の各ケースで検証する。1件の不備で全体を
捨てず、残りのレコードは処理を続けることが本モジュールの核となる不変条件。

    python -m unittest discover -s tools/agent-project/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codd_gate_debt as debt


class TestParseDebtOutput(unittest.TestCase):
    def test_empty_text_is_zero_items(self):
        result = debt.parse_debt_output("")
        self.assertEqual(result.items, [])
        self.assertEqual(result.errors, [])

    def test_whitespace_only_is_zero_items(self):
        result = debt.parse_debt_output("   \n  ")
        self.assertEqual(result.items, [])
        self.assertEqual(result.errors, [])

    def test_single_object_top_level(self):
        result = debt.parse_debt_output('{"id": "D1", "title": "drift 1"}')
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].id, "D1")
        self.assertEqual(result.items[0].title, "drift 1")
        self.assertEqual(result.errors, [])

    def test_array_of_objects(self):
        result = debt.parse_debt_output(
            '[{"id": "D1", "title": "drift 1"}, {"id": "D2", "title": "drift 2"}]')
        self.assertEqual([i.id for i in result.items], ["D1", "D2"])
        self.assertEqual(result.errors, [])

    def test_invalid_json_reports_single_error_and_no_items(self):
        result = debt.parse_debt_output("not-json")
        self.assertEqual(result.items, [])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("JSON として解釈できない", result.errors[0])

    def test_one_bad_record_does_not_discard_the_rest(self):
        # 1件目は title 欠落、2件目は非 object、3件目は正常。3件目だけが items に残ること。
        result = debt.parse_debt_output(
            '[{"id": "D1"}, "not-an-object", {"id": "D3", "title": "ok"}]')
        self.assertEqual([i.id for i in result.items], ["D3"])
        self.assertEqual(len(result.errors), 2)
        self.assertIn("title が空/欠落", result.errors[0])
        self.assertIn("object ではない", result.errors[1])

    def test_missing_id_is_allowed(self):
        result = debt.parse_debt_output('{"title": "no id here"}')
        self.assertEqual(len(result.items), 1)
        self.assertIsNone(result.items[0].id)

    def test_unknown_fields_are_preserved(self):
        result = debt.parse_debt_output(
            '{"id": "D1", "title": "t", "verify": "true", "note": "custom"}')
        item = result.items[0]
        self.assertEqual(item.fields.get("verify"), "true")
        self.assertEqual(item.fields.get("note"), "custom")


class TestDriftItemToSpec(unittest.TestCase):
    def test_to_spec_round_trips_known_and_unknown_fields(self):
        result = debt.parse_debt_output(
            '{"id": "D1", "title": "t", "verify": "true", "note": "custom"}')
        spec = result.items[0].to_spec()
        self.assertEqual(spec, {"id": "D1", "title": "t", "verify": "true", "note": "custom"})

    def test_to_spec_omits_id_when_absent(self):
        result = debt.parse_debt_output('{"title": "t"}')
        spec = result.items[0].to_spec()
        self.assertEqual(spec, {"title": "t"})


if __name__ == "__main__":
    unittest.main()
