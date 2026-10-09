"""docx_builder（build / example）のテスト。

実行: cd .github/skills/docx-document-builder && uv run python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from docx.oxml.ns import qn

import docx_builder as db
import docx_template as dt

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "docx_builder.py")


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "report.docx")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **over) -> dt.DocView:
        spec = {**db.EXAMPLE_SPEC, "filename": self.out, **over}
        db.build(spec)
        return dt.DocView(dt.open_doc(self.out))

    def test_example(self):
        view = self.build()
        kinds = [dt.block_kind(view, e) for e in view.blocks]
        self.assertEqual(kinds[:4], ["段落", "段落", "段落", "見出し 1"])
        self.assertEqual(view.style_name(view.style_of(view.blocks[0])), "Title")
        self.assertIn("リスト（番号・段 1）", kinds)
        self.assertIn("リスト（記号・段 0）", kinds)
        self.assertIn("表", kinds)
        self.assertEqual(view.font_name(view.blocks[4]), "游明朝")
        self.assertEqual(view.font_name(view.blocks[3]), "游ゴシック")

    def test_heading_numbers_and_paragraphs(self):
        view = self.build()
        heads = [e for e in view.blocks if view.heading_level(e)]
        self.assertTrue(all(view.numpr(e) for e in heads))         # 章の番号はスタイルの番号で振る（文字に混ぜない）
        self.assertEqual(dt.literal_text(heads[0]), "概要")
        body = [dt.literal_text(e) for e in view.blocks if dt.literal_text(e).startswith(("申請の確認に 1", "今期"))]
        self.assertEqual(len(body), 2)                               # 空行で段落が分かれる
        view = self.build(numbered_headings=False)
        self.assertFalse(any(view.numpr(e) for e in view.blocks if view.heading_level(e)))

    def test_line_break_and_table(self):
        view = self.build(blocks=[{"type": "paragraph", "text": "1 行め\n2 行め"},
                                  {"type": "table", "columns": ["項目", "金額"], "rows": [{"項目": "設計", "金額": "120"}]}])
        p = view.blocks[0]
        self.assertEqual(len(list(p.iter(qn("w:br")))), 1)
        tbl = view.blocks[1]
        self.assertEqual(dt.table_grid(tbl), [["項目", "金額"], ["設計", "120"]])
        self.assertIsNotNone(tbl.find(f"{qn('w:tr')}/{qn('w:trPr')}/{qn('w:tblHeader')}"))

    def test_properties_and_package(self):
        self.build()
        with zipfile.ZipFile(self.out) as z:
            names = z.namelist()
            core = z.read("docProps/core.xml").decode()
        self.assertNotIn("docProps/thumbnail.jpeg", names)
        self.assertIn("業務改善の報告", core)
        self.assertEqual([p for p in dt.provenance(dt.read_bytes(self.out)) if "題" in p], [])

    def test_errors(self):
        with self.assertRaises(dt.TemplateError):
            self.build(blocks=[])
        with self.assertRaises(dt.TemplateError):
            self.build(blocks=[{"type": "chart"}])
        with self.assertRaises(dt.TemplateError):
            self.build(blocks=[{"type": "heading", "text": "x", "level": 4}])

    def test_built_document_is_a_template(self):
        self.build()
        d = dt.analyze(self.out)
        keys = [p.get("key") for p in d["parts"] if p.get("key")]
        self.assertEqual(keys[:3], ["表紙", "概要", "進め方"])
        fees = next(p for p in d["parts"] if p.get("key") == "費用")
        self.assertEqual(fees["tables"][0]["footer_rows"], 1)

    def test_cli(self):
        r = subprocess.run([sys.executable, SCRIPT, "example"], capture_output=True, text=True)
        spec = json.loads(r.stdout)
        spec["filename"] = self.out
        path = os.path.join(self.tmp.name, "spec.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        r = subprocess.run([sys.executable, SCRIPT, "build", "--spec", path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, SCRIPT, "inspect", self.out], capture_output=True, text=True)
        self.assertIn("章の構成", r.stdout)


if __name__ == "__main__":
    unittest.main()
