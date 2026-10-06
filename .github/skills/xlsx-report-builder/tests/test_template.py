"""xlsx_template（analyze / render）のテスト。テンプレートは openpyxl で組み立てる。

実行: cd .github/skills/xlsx-report-builder && uv run python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

import xlsx_template as xt

THIN = Side(style="thin", color="888888")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
BAND_FILL = PatternFill("solid", fgColor="DDEBF7")
TOTAL_FILL = PatternFill("solid", fgColor="FFF2CC")


def make_template(path: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "請求書"
    ws.merge_cells("A1:E1")
    ws["A1"] = "請求書"
    ws["A1"].font = Font(name="MS Gothic", size=20, bold=True)
    ws["A3"], ws["B3"] = "請求先", "サンプル株式会社"
    ws["A4"], ws["B4"] = "請求日", "2020-01-01"
    ws["B4"].number_format = "yyyy/mm/dd"
    ws["A5"], ws["B5"] = "税率", 0.1
    ws["B5"].number_format = "0%"
    for c, h in zip("ABCDE", ["No", "品名", "数量", "単価", "金額"]):
        cell = ws[f"{c}7"]
        cell.value, cell.font, cell.fill, cell.border = h, Font(bold=True, color="FFFFFF"), HEAD_FILL, BOX
    for r, fill in ((8, None), (9, BAND_FILL)):  # 縞模様のサンプル 2 行
        ws[f"A{r}"], ws[f"B{r}"], ws[f"C{r}"], ws[f"D{r}"] = r - 7, f"サンプル{r}", 1, 100
        ws[f"E{r}"] = f"=C{r}*D{r}"
        for c in "ABCDE":
            ws[f"{c}{r}"].border = BOX
            ws[f"{c}{r}"].font = Font(name="MS Gothic", size=10)
            if fill:
                ws[f"{c}{r}"].fill = fill
        ws[f"D{r}"].number_format = ws[f"E{r}"].number_format = "#,##0"
    ws["D10"], ws["E10"] = "合計", "=SUM(E8:E9)"
    ws["D11"], ws["E11"] = "消費税", "=E10*$B$5"
    ws["D12"], ws["E12"] = "請求額", "=E10+E11"
    for r in (10, 11, 12):
        for c in "DE":
            ws[f"{c}{r}"].fill, ws[f"{c}{r}"].border = TOTAL_FILL, BOX
        ws[f"E{r}"].number_format = "#,##0"
    ws["A14"] = "お支払状況"
    for c, h in zip("ABC", ["日付", "方法", "入金額"]):
        cell = ws[f"{c}15"]
        cell.value, cell.font, cell.fill, cell.border = h, Font(bold=True, color="FFFFFF"), HEAD_FILL, BOX
    ws["A16"], ws["B16"], ws["C16"] = "2020-02-01", "振込", 500
    ws["A16"].number_format = "yyyy/mm/dd"
    for c in "ABC":
        ws[f"{c}16"].border = BOX
    ws["A18"] = "※ 振込手数料はご負担ください"
    ws["A18"].font = Font(italic=True, color="C00000")
    ws.conditional_formatting.add("E8:E9", CellIsRule(operator="greaterThan", formula=["1000"], fill=PatternFill("solid", bgColor="FFC7CE")))
    dv = DataValidation(type="whole", operator="greaterThanOrEqual", formula1="0")
    dv.add("C8:C9")
    ws.add_data_validation(dv)
    ws.column_dimensions["B"].width = 30
    ws.row_dimensions[8].height = 22
    wb.defined_names["請求額"] = DefinedName("請求額", attr_text="'請求書'!$E$12")
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=5, min_row=7, max_row=9), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=2, min_row=8, max_row=9))
    ws.add_chart(chart, "G3")
    s2 = wb.create_sheet("集計")
    s2["A1"] = "請求額"
    s2["B1"] = "='請求書'!E12"
    s2["A2"] = "合計"
    s2["B2"] = "=請求書!E10"
    wb.save(path)


DATA = {
    "customer": "株式会社テスト",
    "date": "2026-10-05",
    "rate": 0.08,
    "items": [{"name": f"商品{i}", "qty": i, "price": 1000 * i} for i in range(1, 6)],
    "payments": [{"date": "2026-10-10", "method": "振込", "amount": 1000}, {"date": "2026-10-20", "method": "現金", "amount": 2000}],
}

DEF = {
    "version": 1,
    "template": "t.xlsx",
    "sheets": [{
        "name": "請求書",
        "cells": {"B3": "customer", "B4": "date", "B5": "rate"},
        "tables": [
            {"id": "items", "header_row": 7, "first_row": 8, "sample_rows": 2, "pattern": [8, 9], "key": "items",
             "columns": {"A": {"key": "$index"}, "B": {"key": "name"}, "C": {"key": "qty"}, "D": {"key": "price"}, "E": {"formula": True}}},
            {"id": "payments", "header_row": 15, "first_row": 16, "sample_rows": 1, "pattern": [16], "key": "payments",
             "columns": {"A": {"key": "date"}, "B": {"key": "method"}, "C": {"key": "amount"}}},
        ],
    }],
}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.tpl = os.path.join(self.dir, "t.xlsx")
        make_template(self.tpl)

    def render(self, data=DATA, definition=DEF, name="out.xlsx"):
        out = os.path.join(self.dir, name)
        self.warnings = xt.render(self.tpl, definition, data, out)
        return out


class RenderTests(Base):
    def test_rows_expand_and_following_content_moves(self):
        ws = load_workbook(self.render())["請求書"]
        # 明細 5 行 → 8..12、合計は 13..15（+3）、支払表の見出しは 15+3+... と連動してずれる
        self.assertEqual([ws[f"B{r}"].value for r in range(8, 13)], [f"商品{i}" for i in range(1, 6)])
        self.assertEqual([ws[f"A{r}"].value for r in range(8, 13)], [1, 2, 3, 4, 5])
        self.assertEqual(ws["D13"].value, "合計")
        self.assertEqual(ws["D14"].value, "消費税")
        self.assertEqual(ws["D15"].value, "請求額")
        self.assertEqual(ws["A17"].value, "お支払状況")
        self.assertEqual(ws["A18"].value, "日付")  # 見出し行: 15 + 3 = 18
        self.assertEqual([ws[f"B{r}"].value for r in (19, 20)], ["振込", "現金"])  # 表 2 は 1 → 2 行
        self.assertEqual(ws["A22"].value, "※ 振込手数料はご負担ください")  # 18 + 3 + 1

    def test_formulas_are_translated_and_totals_extended(self):
        ws = load_workbook(self.render())["請求書"]
        self.assertEqual([ws[f"E{r}"].value for r in (8, 9, 10, 11, 12)], [f"=C{r}*D{r}" for r in (8, 9, 10, 11, 12)])
        self.assertEqual(ws["E13"].value, "=SUM(E8:E12)")
        self.assertEqual(ws["E14"].value, "=E13*$B$5")
        self.assertEqual(ws["E15"].value, "=E13+E14")

    def test_template_formatting_is_kept_and_banding_repeats(self):
        tpl = load_workbook(self.tpl)["請求書"]
        ws = load_workbook(self.render())["請求書"]
        self.assertEqual(ws["A7"].fill.fgColor.rgb, tpl["A7"].fill.fgColor.rgb)  # 見出し
        for r in range(8, 13):
            for col in "ABCDE":
                c = ws[f"{col}{r}"]
                self.assertEqual(c.border.left.style, "thin")
                self.assertEqual(c.font.name, "MS Gothic")
        self.assertEqual(ws["B9"].fill.fgColor.rgb, tpl["B9"].fill.fgColor.rgb)
        self.assertEqual(ws["B9"].fill.fgColor.rgb, ws["B11"].fill.fgColor.rgb)  # 縞は 2 行周期
        self.assertNotEqual(ws["B8"].fill.fgColor.rgb, ws["B9"].fill.fgColor.rgb)
        self.assertEqual(ws["D8"].number_format, "#,##0")
        self.assertEqual(ws["D13"].fill.fgColor.rgb, tpl["D10"].fill.fgColor.rgb)  # 合計行の色
        self.assertEqual(ws["A22"].font.italic, True)
        self.assertEqual(ws.column_dimensions["B"].width, 30)

    def test_row_height_is_copied_with_the_row(self):
        ws = load_workbook(self.render())["請求書"]
        self.assertEqual(ws.row_dimensions[8].height, 22)
        self.assertIsNone(ws.row_dimensions[9].height)

    def test_fixed_cells_and_dates(self):
        ws = load_workbook(self.render())["請求書"]
        self.assertEqual(ws["B3"].value, "株式会社テスト")
        self.assertEqual(ws["B4"].value.date().isoformat(), "2026-10-05")  # 日付書式のセルは日付型
        self.assertEqual(ws["B4"].number_format, "yyyy/mm/dd")
        self.assertEqual(ws["B5"].value, 0.08)
        self.assertEqual(ws["A19"].value.date().isoformat(), "2026-10-10")

    def test_sample_values_do_not_leak(self):
        ws = load_workbook(self.render(data={**DATA, "items": [{"name": "だけ"}]}))["請求書"]
        self.assertEqual(ws["B8"].value, "だけ")
        self.assertIsNone(ws["C8"].value)  # サンプルの 1 が残らない
        self.assertIsNone(ws["D8"].value)
        self.assertEqual(ws["E8"].value, "=C8*D8")

    def test_shrink_and_empty(self):
        ws = load_workbook(self.render(data={**DATA, "items": [{"name": "a", "qty": 1, "price": 5}] * 1}))["請求書"]
        self.assertEqual(ws["D9"].value, "合計")  # 2 行 → 1 行で -1
        self.assertEqual(ws["E9"].value, "=SUM(E8:E8)")
        ws = load_workbook(self.render(data={**DATA, "items": []}, name="e.xlsx"))["請求書"]
        self.assertIsNone(ws["B8"].value)  # 空でも 1 行（空欄）は残す
        self.assertEqual(ws["D9"].value, "合計")

    def test_merged_cf_dv_names_and_cross_sheet_refs_follow(self):
        out = self.render()
        wb = load_workbook(out)
        ws = wb["請求書"]
        self.assertIn("A1:E1", [str(m) for m in ws.merged_cells.ranges])
        cf = [str(r.sqref) for r in ws.conditional_formatting]
        self.assertEqual(cf, ["E8:E12"])
        self.assertEqual([str(d.sqref) for d in ws.data_validations.dataValidation], ["C8:C12"])
        self.assertEqual(wb.defined_names["請求額"].attr_text, "'請求書'!$E$15")
        self.assertEqual(wb["集計"]["B1"].value, "='請求書'!E15")
        self.assertEqual(wb["集計"]["B2"].value, "=請求書!E13")

    def test_chart_range_and_untouched_parts_are_byte_identical(self):
        out = self.render()
        with zipfile.ZipFile(out) as new, zipfile.ZipFile(self.tpl) as old:
            chart = [n for n in new.namelist() if n.startswith("xl/charts/chart")][0]
            self.assertIn("$E$8:$E$12", new.read(chart).decode())
            for name in ("xl/styles.xml", "xl/theme/theme1.xml", "docProps/core.xml"):
                if name in old.namelist():
                    self.assertEqual(new.read(name), old.read(name), name)
            wbxml = new.read("xl/workbook.xml").decode()
            self.assertIn('fullCalcOnLoad="1"', wbxml)

    def test_merge_in_sample_row_is_replicated(self):
        wb = load_workbook(self.tpl)
        ws = wb["請求書"]
        ws.merge_cells("B16:C16")
        wb.save(self.tpl)
        out = self.render()
        merged = [str(m) for m in load_workbook(out)["請求書"].merged_cells.ranges]
        self.assertIn("B19:C19", merged)
        self.assertIn("B20:C20", merged)

    def test_errors(self):
        with self.assertRaises(xt.TemplateError):
            self.render(data={"items": []})  # cells のキー欠落
        bad = json.loads(json.dumps(DEF))
        bad["sheets"][0]["tables"][0]["pattern"] = [20]
        with self.assertRaises(xt.TemplateError):
            self.render(definition=bad)

    @unittest.skipUnless(shutil.which("soffice"), "LibreOffice がない")
    def test_opens_and_calculates_in_libreoffice(self):
        out = self.render()
        subprocess.run(["soffice", "--headless", f"-env:UserInstallation=file://{self.dir}/lo", "--convert-to", "csv", "--outdir", self.dir, out],
                       check=True, capture_output=True, timeout=120)
        with open(os.path.join(self.dir, "out.csv"), encoding="utf-8") as fh:
            text = fh.read()
        # 商品 i: 数量 i × 単価 1000i の合計 = 1000 × (1+4+9+16+25) = 55000
        self.assertIn("合計,55000", text.replace('"', ""))
        self.assertIn("請求額,59400", text.replace('"', ""))  # 55000 × 1.08


class AnalyzeTests(Base):
    def test_detects_tables_pattern_and_columns(self):
        d = xt.analyze(self.tpl)
        sheet = d["sheets"][0]
        self.assertEqual(sheet["name"], "請求書")
        t1, t2 = sheet["tables"]
        self.assertEqual((t1["header_row"], t1["first_row"], t1["sample_rows"], t1["pattern"]), (7, 8, 2, [8, 9]))
        self.assertTrue(t1["columns"]["E"]["formula"])
        self.assertEqual(t1["columns"]["B"]["key"], "品名")
        self.assertEqual(t1["_total_row"], 10)
        self.assertEqual((t2["header_row"], t2["first_row"], t2["sample_rows"]), (15, 16, 1))
        self.assertEqual(list(t2["columns"]), ["A", "B", "C"])

    def test_candidates_exclude_tables_and_list_labels(self):
        sheet = xt.analyze(self.tpl)["sheets"][0]
        refs = {c["ref"]: c for c in sheet["_candidates"]}
        self.assertEqual(refs["B3"]["label"], "請求先")
        self.assertNotIn("B8", refs)
        self.assertNotIn("E10", refs)  # 数式は候補にしない

    def test_cli_roundtrip_with_analyzed_definition(self):
        import xlsx_builder  # noqa: F401  (サブコマンド統合の確認)
        defn = os.path.join(self.dir, "d.json")
        data = os.path.join(self.dir, "d.data.json")
        out = os.path.join(self.dir, "o.xlsx")
        script = os.path.join(os.path.dirname(__file__), "..", "scripts", "xlsx_builder.py")
        subprocess.run([sys.executable, script, "analyze", self.tpl, "-o", defn], check=True, capture_output=True)
        with open(defn, encoding="utf-8") as fh:
            d = json.load(fh)
        d["sheets"][0]["tables"][0]["key"] = "items"
        for letter, key in zip("BCD", ["name", "qty", "price"]):
            d["sheets"][0]["tables"][0]["columns"][letter]["key"] = key
        d["sheets"][0]["tables"][1]["key"] = "payments"
        for letter, key in zip("ABC", ["date", "method", "amount"]):
            d["sheets"][0]["tables"][1]["columns"][letter]["key"] = key
        d["sheets"][0]["tables"][0]["columns"]["A"]["key"] = "$index"
        d["sheets"][0]["cells"] = {"B3": "customer"}
        with open(defn, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        with open(data, "w", encoding="utf-8") as fh:
            json.dump(DATA, fh, ensure_ascii=False)
        subprocess.run([sys.executable, script, "render", "--template", self.tpl, "--def", defn, "--data", data, "-o", out],
                       check=True, capture_output=True)
        ws = load_workbook(out)["請求書"]
        self.assertEqual(ws["B3"].value, "株式会社テスト")
        self.assertEqual(ws["E13"].value, "=SUM(E8:E12)")


def rewrite_zip(src: str, dst: str, edits: dict) -> None:
    """zip 内の part を {名前: 関数(bytes → bytes)} で書き換えて保存する。"""
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in zi.infolist():
            raw = zi.read(info.filename)
            if info.filename in edits:
                raw = edits[info.filename](raw)
            zo.writestr(info.filename, raw)


class RealWorldShapeTests(Base):
    """openpyxl が作らない形（共有数式・共有文字列・calcChain）でも壊れない。"""

    def test_shared_formulas_are_expanded_and_translated(self):
        def to_shared(raw: bytes) -> bytes:
            text = raw.decode()
            self.assertIn("<f>C8*D8</f>", text)
            text = text.replace("<f>C8*D8</f>", '<f t="shared" ref="E8:E9" si="0">C8*D8</f>')
            return text.replace("<f>C9*D9</f>", '<f t="shared" si="0"/>').encode()
        shared = os.path.join(self.dir, "shared.xlsx")
        rewrite_zip(self.tpl, shared, {"xl/worksheets/sheet1.xml": to_shared})
        self.tpl = shared
        ws = load_workbook(self.render())["請求書"]
        self.assertEqual([ws[f"E{r}"].value for r in range(8, 13)], [f"=C{r}*D{r}" for r in range(8, 13)])

    def test_calc_chain_is_dropped_consistently(self):
        chain = os.path.join(self.dir, "chain.xlsx")
        ct = lambda raw: raw.decode().replace("</Types>", '<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/></Types>').encode()
        rel = lambda raw: raw.decode().replace("</Relationships>", '<Relationship Id="rIdC" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain" Target="calcChain.xml"/></Relationships>').encode()
        with zipfile.ZipFile(self.tpl) as zi, zipfile.ZipFile(chain, "w") as zo:
            for info in zi.infolist():
                raw = zi.read(info.filename)
                if info.filename == "[Content_Types].xml":
                    raw = ct(raw)
                if info.filename == "xl/_rels/workbook.xml.rels":
                    raw = rel(raw)
                zo.writestr(info.filename, raw)
            zo.writestr("xl/calcChain.xml", '<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><c r="E8" i="1"/></calcChain>')
        self.tpl = chain
        out = self.render()
        with zipfile.ZipFile(out) as z:
            self.assertNotIn("xl/calcChain.xml", z.namelist())
            self.assertNotIn("calcChain", z.read("[Content_Types].xml").decode())
            self.assertNotIn("calcChain", z.read("xl/_rels/workbook.xml.rels").decode())
        load_workbook(out)

    @unittest.skipUnless(shutil.which("soffice"), "LibreOffice がない")
    def test_template_saved_by_libreoffice_uses_shared_strings(self):
        lo_dir = os.path.join(self.dir, "lo_tpl")
        os.makedirs(lo_dir)
        subprocess.run(["soffice", "--headless", f"-env:UserInstallation=file://{self.dir}/lo", "--convert-to", "xlsx", "--outdir", lo_dir, self.tpl],
                       check=True, capture_output=True, timeout=120)
        self.tpl = os.path.join(lo_dir, "t.xlsx")
        with zipfile.ZipFile(self.tpl) as z:
            self.assertIn("xl/sharedStrings.xml", z.namelist())
        sheet = xt.analyze(self.tpl)["sheets"][0]
        self.assertEqual([(t["header_row"], t["first_row"], t["sample_rows"]) for t in sheet["tables"]], [(7, 8, 2), (15, 16, 1)])
        self.assertEqual(sheet["tables"][0]["columns"]["B"]["header"], "品名")
        ws = load_workbook(self.render())["請求書"]
        self.assertEqual(ws["B12"].value, "商品5")
        self.assertEqual(ws["A22"].value, "※ 振込手数料はご負担ください")
        self.assertEqual(ws["B9"].border.left.style, "thin")


if __name__ == "__main__":
    unittest.main()
