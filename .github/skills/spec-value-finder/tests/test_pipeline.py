"""spec-value-finder のパイプライン検証。

Excel/Word を実際に生成し、extract → find → fill が一気通貫で動くことを確認する。
Neo4j も外部サーバも不要なため、CI でそのまま実行できる。
"""
import json

import openpyxl
import pytest

import docx

from extract import extract_file, find_files, to_markdown
from filler import fill
from finder import find_candidates
from mapping import validate_mapping, write_draft
import yaml


# ---------------------------------------------------------------------------
# フィクスチャ生成
# ---------------------------------------------------------------------------

def _make_source_xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Network"
    ws.append(["分類", "項目", "最大値", "単位"])
    ws.append(["MTU設定", "MTU上限", 1500, "bytes"])
    ws.append(["MTU設定", "ジャンボフレーム", 9000, "bytes"])
    wb.save(path)


def _make_source_docx(path):
    d = docx.Document()
    d.add_heading("ネットワーク仕様", level=1)
    d.add_paragraph("本機器の最大転送単位（ＭＴＵ）は 1500 バイトである。")
    d.add_heading("電源", level=1)
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "項目"
    t.cell(0, 1).text = "値"
    t.cell(1, 0).text = "定格電圧"
    t.cell(1, 1).text = "100V"
    d.save(path)


def _make_template_xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["項目", "値", "出典", "確信度", "要確認"])
    ws.append(["MTU上限", "", "", "", ""])
    ws.append(["定格電圧", "", "", "", ""])
    wb.save(path)


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------

def test_extract_excel_keeps_breadcrumb(tmp_path):
    src = tmp_path / "HW仕様書.xlsx"
    _make_source_xlsx(src)
    doc = extract_file(src)
    assert doc.fmt == "excel"
    cells = [c for b in doc.blocks for row in b.rows for c in row]
    mtu = next(c for c in cells if c.text == "1500")
    # breadcrumb にシート名と列見出しが含まれる
    assert "Network" in mtu.path
    assert "最大値" in mtu.path


def test_extract_word_picks_up_nontabular(tmp_path):
    src = tmp_path / "仕様書.docx"
    _make_source_docx(src)
    doc = extract_file(src)
    assert doc.fmt == "word"
    texts = [b for b in doc.blocks if getattr(b, "kind", "") == "text"]
    para = next(b for b in texts if "最大転送単位" in b.text)
    # 見出し階層が breadcrumb に入る
    assert "ネットワーク仕様" in para.path
    tables = [b for b in doc.blocks if getattr(b, "kind", "") == "table"]
    assert tables and any(c.text == "100V" for r in tables[0].rows for c in r)


def test_to_markdown_renders(tmp_path):
    src = tmp_path / "HW仕様書.xlsx"
    _make_source_xlsx(src)
    md = to_markdown(extract_file(src))
    assert "| MTU上限 |" in md.replace(" ", " ")
    assert "Network" in md


# ---------------------------------------------------------------------------
# find_files（部分一致走査）
# ---------------------------------------------------------------------------

def test_find_files_partial_match(tmp_path):
    _make_source_xlsx(tmp_path / "HW仕様書_v2.xlsx")
    _make_source_xlsx(tmp_path / "別資料.xlsx")
    hits = find_files(tmp_path, "HW仕様")
    assert [p.name for p in hits] == ["HW仕様書_v2.xlsx"]
    assert len(find_files(tmp_path, "")) == 2


# ---------------------------------------------------------------------------
# find（候補抽出）
# ---------------------------------------------------------------------------

def test_find_candidates_excel(tmp_path):
    src = tmp_path / "HW仕様書.xlsx"
    _make_source_xlsx(src)
    docs = [extract_file(src)]
    items = [{"target": "MTU上限", "keywords": ["MTU上限", "MTU"],
              "section_hint": "Network"}]
    result = find_candidates(docs, items)
    cands = result["items"][0]["candidates"]
    assert cands, "候補が見つかること"
    top = cands[0]
    # 行文脈に値 1500 が含まれる
    assert "1500" in top["row_values"]
    assert "MTU上限" in top["text"]


def test_find_candidates_word_nontabular(tmp_path):
    src = tmp_path / "仕様書.docx"
    _make_source_docx(src)
    docs = [extract_file(src)]
    # 全角ＭＴＵ の本文を半角キーワードで拾えること（NFKC 正規化）
    items = [{"target": "MTU", "keywords": ["MTU"]}]
    result = find_candidates(docs, items)
    cands = result["items"][0]["candidates"]
    assert any("最大転送単位" in c["text"] for c in cands)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def test_validate_detects_missing_keywords():
    data = {"version": 1, "source": {"folder": "./s"},
            "items": [{"target": "X", "keywords": []}]}
    errors, _ = validate_mapping(data)
    assert any("keywords" in e for e in errors)


def test_validate_passes_valid_mapping():
    data = {"version": 1, "source": {"folder": "./s"},
            "items": [{"target": "MTU上限", "keywords": ["MTU"],
                       "section_hint": "Network"}]}
    errors, _ = validate_mapping(data)
    assert errors == []


def test_write_draft_is_parseable(tmp_path):
    out = tmp_path / "mapping.yaml"
    write_draft(out, source_text="MTU は 1500")
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["items"] == []


# ---------------------------------------------------------------------------
# fill
# ---------------------------------------------------------------------------

def test_fill_excel_writes_and_appends(tmp_path):
    template = tmp_path / "記入例.xlsx"
    _make_template_xlsx(template)
    findings = [
        {"target": "MTU上限", "value": "1500", "source": "HW.xlsx :: C2",
         "confidence": "high", "needs_review": False},
        {"target": "電源電圧", "value": "100V", "source": "HW.xlsx :: B2",
         "confidence": "low", "needs_review": True},
    ]
    out = tmp_path / "結果.xlsx"
    report = fill(template, findings, out)
    assert report["matched"] == 1
    assert report["appended"] == 1

    wb = openpyxl.load_workbook(out)
    ws = wb.active
    rows = {r[0].value: r for r in ws.iter_rows()}
    assert rows["MTU上限"][1].value == "1500"
    assert rows["MTU上限"][3].value == "高"
    # 末尾追記された行も値と要確認フラグを持つ
    assert rows["電源電圧"][1].value == "100V"
    assert rows["電源電圧"][4].value == "要確認"


def test_fill_word_replaces_placeholder(tmp_path):
    template = tmp_path / "記入例.docx"
    d = docx.Document()
    d.add_paragraph("MTU上限: {{MTU上限}}")
    d.save(template)
    findings = [{"target": "MTU上限", "value": "1500"}]
    out = tmp_path / "結果.docx"
    report = fill(template, findings, out)
    assert report["replaced"] == 1
    assert "1500" in docx.Document(str(out)).paragraphs[0].text
    assert "{{" not in docx.Document(str(out)).paragraphs[0].text


# ---------------------------------------------------------------------------
# 一気通貫
# ---------------------------------------------------------------------------

def test_end_to_end(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    _make_source_xlsx(specs / "HW仕様書.xlsx")

    files = find_files(specs, "HW仕様")
    docs = [extract_file(p) for p in files]
    items = [{"target": "MTU上限", "keywords": ["MTU上限"], "section_hint": "Network"}]
    result = find_candidates(docs, items)
    assert result["items"][0]["candidates"]

    # Claude が候補を吟味して findings.json を作る想定 → ここでは値を直接確定
    findings = [{"target": "MTU上限", "value": "1500",
                 "source": result["items"][0]["candidates"][0]["location"],
                 "confidence": "high", "needs_review": False}]
    template = tmp_path / "記入例.xlsx"
    _make_template_xlsx(template)
    out = tmp_path / "結果.xlsx"
    fill(template, findings, out)
    wb = openpyxl.load_workbook(out)
    assert wb.active["B2"].value == "1500"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
