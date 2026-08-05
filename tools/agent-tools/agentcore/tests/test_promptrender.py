"""agentcore.promptrender の単体テスト（案 K-1/K-2 の圧縮描画。1 実装の性質を固定する）。
設計: docs/plans/2026-08-05-json-prompt-compression-study.md

    python -m unittest discover -s tools/agent-tools/agentcore/tests
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import promptrender  # noqa: E402


def _split_fields(line: str) -> "list[str]":
    """テスト専用の行分割（JSON クォート規則を尊重してカンマで割る）。
    _fmt_scalar のクォート（json.dumps 由来）を正しく読めることを検証するためだけの、
    このテストに閉じた最小パーサ。"""
    fields, buf, in_str, i = [], "", False, 0
    while i < len(line):
        c = line[i]
        if in_str:
            buf += c
            if c == "\\" and i + 1 < len(line):
                buf += line[i + 1]
                i += 1
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
                buf += c
            elif c == ",":
                fields.append(buf)
                buf = ""
            else:
                buf += c
        i += 1
    fields.append(buf)
    return fields


def _parse_field(raw: str):
    return json.loads(raw) if raw.startswith('"') else raw


class TestDumpsPrompt(unittest.TestCase):
    def test_ensure_ascii_false_keeps_japanese_literal(self):
        s = promptrender.dumps_prompt({"title": "検証に失敗"})
        self.assertIn("検証に失敗", s)
        self.assertNotIn("\\u", s)

    def test_separators_are_minimal(self):
        s = promptrender.dumps_prompt({"a": 1, "b": [1, 2]})
        self.assertNotIn(", ", s)
        self.assertNotIn(": ", s)
        self.assertEqual(s, '{"a":1,"b":[1,2]}')

    def test_round_trips_via_json_loads(self):
        value = {"c": 1, "r": 2, "a": 3, "note": None, "ok": True, "items": ["x", "y"]}
        self.assertEqual(json.loads(promptrender.dumps_prompt(value)), value)

    def test_non_serializable_falls_back_to_str(self):
        class Foo:
            def __str__(self):
                return "foo!"
        s = promptrender.dumps_prompt({"x": Foo()})
        self.assertEqual(json.loads(s), {"x": "foo!"})


class TestRenderTableHomogeneous(unittest.TestCase):
    """均質な dict 配列（キー集合が全行で同一・値がスカラー）は表へ畳む。"""

    def test_header_and_row_count(self):
        items = [{"id": f"t{i}", "kind": "work"} for i in range(3)]
        out = promptrender.render_table({"tasks": items})
        lines = out.splitlines()
        self.assertEqual(lines[0], "tasks[3]{id,kind}:")
        self.assertEqual(len(lines), 4)  # ヘッダ + 3 行
        self.assertEqual(lines[1], "  t0,work")

    def test_reversibility_of_a_table_block(self):
        """描画→パースで元の行の値が失われないこと（案の受け入れ条件）。
        カンマ・二重引用符を含む値が行の途中でカンマ分割を壊さないかが要点。"""
        row = {"file": "a,b.py", "note": 'has "quotes"', "n": 3, "ok": True, "empty": None}
        out = promptrender.render_table({"items": [row]})
        lines = out.splitlines()
        self.assertEqual(lines[0], "items[1]{file,note,n,ok,empty}:")
        fields = _split_fields(lines[1][2:])  # 先頭のインデント2つを剥がす
        self.assertEqual(fields, ['"a,b.py"', '"has \\"quotes\\""', "3", "true", ""])
        parsed = [_parse_field(f) for f in fields]
        self.assertEqual(parsed, ["a,b.py", 'has "quotes"', "3", "true", ""])

    def test_key_order_is_preserved_from_first_element(self):
        items = [{"b": 1, "a": 2}, {"b": 3, "a": 4}]
        out = promptrender.render_table({"x": items})
        self.assertIn("x[2]{b,a}:", out)


class TestRenderTableFallback(unittest.TestCase):
    """均質でない配列・空配列・入れ子混在は compact JSON へ決定的に落ちる。"""

    def test_heterogeneous_keys_fall_back_to_compact_json(self):
        items = [{"id": "t1"}, {"id": "t2", "deps": ["t1"]}]
        out = promptrender.render_table({"tasks": items})
        self.assertEqual(out, "tasks: " + promptrender.dumps_prompt(items))
        self.assertEqual(json.loads(out.split("tasks: ", 1)[1]), items)

    def test_non_scalar_values_fall_back(self):
        items = [{"id": "t1", "deps": []}, {"id": "t2", "deps": ["t1"]}]
        out = promptrender.render_table({"tasks": items})
        self.assertEqual(out, "tasks: " + promptrender.dumps_prompt(items))

    def test_empty_list_falls_back(self):
        out = promptrender.render_table({"issues": []})
        self.assertEqual(out, "issues: []")

    def test_empty_dict_falls_back(self):
        out = promptrender.render_table({"meta": {}})
        self.assertEqual(out, "meta: {}")

    def test_mixed_list_falls_back(self):
        out = promptrender.render_table({"mixed": [1, {"a": 1}]})
        self.assertEqual(out, "mixed: " + promptrender.dumps_prompt([1, {"a": 1}]))


class TestRenderTableScalarLists(unittest.TestCase):
    def test_scalar_list_is_comma_joined_without_json_brackets(self):
        out = promptrender.render_table({"needs": ["a", "b", "c"]})
        self.assertEqual(out, "needs[3]: a,b,c")

    def test_scalar_list_with_special_chars_is_quoted_per_field(self):
        out = promptrender.render_table({"needs": ["a,b", "c"]})
        self.assertEqual(out, 'needs[2]: "a,b",c')


class TestRenderTableNesting(unittest.TestCase):
    def test_nested_dict_is_walked(self):
        out = promptrender.render_table({"stats": {"total": 3, "done": 1}})
        self.assertEqual(out, "stats:\n  total: 3\n  done: 1")

    def test_scalar_root_values(self):
        self.assertEqual(promptrender.render_table({"ok": True}), "ok: true")
        self.assertEqual(promptrender.render_table({"note": None}), "note: ")

    def test_top_level_list_uses_name_param(self):
        items = [{"id": "t1"}, {"id": "t2"}]
        out = promptrender.render_table(items, name="tasks")
        self.assertEqual(out.splitlines()[0], "tasks[2]{id}:")

    def test_full_doctor_like_payload_contains_expected_blocks(self):
        """doctor.py が渡す形（均質配列＋非均質フィールドの混在）を通しで確かめる。"""
        payload = {
            "stats": {"total": 3},
            "blocked": [{"id": "T-1", "status": "review"}, {"id": "T-2", "status": "blocked"}],
            "needs": ["承認待ち", "裁定待ち"],
            "audit": {"level": "warn", "red_flags": ["verify 無し"]},
        }
        out = promptrender.render_table(payload)
        self.assertIn("blocked[2]{id,status}:", out)
        self.assertIn("needs[2]: 承認待ち,裁定待ち", out)
        self.assertIn("audit:\n  level: warn\n  red_flags[1]: verify 無し", out)


if __name__ == "__main__":
    unittest.main()
