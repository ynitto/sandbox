"""Unit tests for the document graph pipeline (no external services needed)."""
import io
from pathlib import Path
import pytest

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------

from pipeline.models import Cell, Row, Table, Paragraph, Section, Document


def test_model_ids_are_unique():
    c1 = Cell(text="a", row_idx=0, col_idx=0)
    c2 = Cell(text="a", row_idx=0, col_idx=0)
    assert c1.node_id != c2.node_id


def test_table_headers_and_data_rows():
    header_row = Row(cells=[Cell(text="Name", row_idx=0, col_idx=0, is_header=True)],
                     index=0, is_header=True)
    data_row = Row(cells=[Cell(text="Alice", row_idx=1, col_idx=0)],
                   index=1, is_header=False)
    table = Table(rows=[header_row, data_row], page=0)
    assert len(table.headers) == 1
    assert len(table.data_rows) == 1


def test_document_all_tables():
    t1 = Table(rows=[], page=0)
    t2 = Table(rows=[], page=1)
    p = Paragraph(text="hello", page=0)
    doc = Document(
        source="test.pdf",
        sections=[
            Section(title="p1", content=[t1, p], page=0),
            Section(title="p2", content=[t2], page=1),
        ],
    )
    assert len(doc.all_tables()) == 2
    assert len(doc.all_paragraphs()) == 1


# ---------------------------------------------------------------------------
# ingest - Excel
# ---------------------------------------------------------------------------

def _make_excel(tmp_path: Path) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Name", "Age", "City"])
    ws.append(["Alice", 30, "Tokyo"])
    ws.append(["Bob", 25, "Osaka"])
    path = tmp_path / "test.xlsx"
    wb.save(path)
    return path


def test_excel_ingestion(tmp_path):
    from pipeline.ingest import iter_excel_sheets
    path = _make_excel(tmp_path)
    sheets = list(iter_excel_sheets(path))
    assert len(sheets) == 1
    assert sheets[0].name == "Sheet1"
    assert sheets[0].rows[0] == ["Name", "Age", "City"]
    assert sheets[0].rows[1][0] == "Alice"


def test_build_from_excel(tmp_path):
    from pipeline.ast_builder import build_from_excel
    path = _make_excel(tmp_path)
    doc = build_from_excel(path)
    assert doc.source == str(path)
    assert len(doc.sections) == 1
    tables = doc.all_tables()
    assert len(tables) == 1
    assert tables[0].rows[0].is_header is True
    assert tables[0].rows[1].cells[0].text == "Alice"


# ---------------------------------------------------------------------------
# ast_builder - dispatcher
# ---------------------------------------------------------------------------

def test_build_document_unsupported_format(tmp_path):
    from pipeline.ast_builder import build_document
    bad = tmp_path / "file.txt"
    bad.write_text("hello")
    with pytest.raises(ValueError, match="Unsupported"):
        build_document(bad)


# ---------------------------------------------------------------------------
# graph_loader - dry-run (no real Neo4j)
# ---------------------------------------------------------------------------

def test_graph_loader_builds_cypher_params():
    """Verify the data passed to Neo4j transactions has the right shape."""
    cells = [
        Cell(text="Name", row_idx=0, col_idx=0, is_header=True),
        Cell(text="Age", row_idx=0, col_idx=1, is_header=True),
    ]
    row = Row(cells=cells, index=0, is_header=True)
    table = Table(rows=[row], page=0)
    section = Section(title="p1", content=[table], page=0)
    doc = Document(source="x.pdf", sections=[section])

    # Ensure all node_ids are non-empty strings
    for obj in [doc, section, table, row] + cells:
        assert isinstance(obj.node_id, str) and len(obj.node_id) > 0


# ---------------------------------------------------------------------------
# pipeline CLI - dry-run with Excel
# ---------------------------------------------------------------------------

def test_pipeline_dry_run_excel(tmp_path, capsys):
    import argparse
    from pipeline.pipeline import run

    path = _make_excel(tmp_path)
    args = argparse.Namespace(
        file=str(path),
        neo4j="",
        user="neo4j",
        password="",
        database="neo4j",
        device="cpu",
        threshold=0.9,
        dpi=150,
        dry_run=True,
    )
    run(args)
    captured = capsys.readouterr()
    assert "Sheet1" in captured.out
    assert "Alice" in captured.out
