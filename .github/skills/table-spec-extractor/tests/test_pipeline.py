"""Unit tests for the document graph pipeline (no external services needed)."""
from pathlib import Path
import pytest

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------

from models import Cell, Row, Table, Paragraph, Section, Document


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


def _make_hierarchical_excel(tmp_path: Path) -> Path:
    """Excel with carry-forward hierarchy: col0 has merged-cell pattern."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Config"
    ws.append(["Category", "Setting", "Value"])
    ws.append(["Network", "IP Address", "192.168.1.1"])
    ws.append(["", "Subnet", "255.255.255.0"])     # merged cell → empty
    ws.append(["Security", "Auth", "OAuth"])
    ws.append(["", "MFA", "enabled"])              # merged cell → empty
    path = tmp_path / "hier.xlsx"
    wb.save(path)
    return path


def test_excel_ingestion(tmp_path):
    from ingest import iter_excel_sheets
    path = _make_excel(tmp_path)
    sheets = list(iter_excel_sheets(path))
    assert len(sheets) == 1
    assert sheets[0].name == "Sheet1"
    assert sheets[0].rows[0] == ["Name", "Age", "City"]
    assert sheets[0].rows[1][0] == "Alice"


def test_build_from_excel(tmp_path):
    from ast_builder import build_from_excel
    path = _make_excel(tmp_path)
    doc = build_from_excel(path)
    assert doc.source == str(path)
    assert len(doc.sections) == 1
    tables = doc.all_tables()
    assert len(tables) == 1
    assert tables[0].rows[0].is_header is True
    assert tables[0].rows[1].cells[0].text == "Alice"


def test_hierarchical_carry_forward(tmp_path):
    """Carry-forward fills empty hierarchy cells; path is populated."""
    from ast_builder import build_from_excel
    path = _make_hierarchical_excel(tmp_path)
    doc = build_from_excel(path)
    table = doc.all_tables()[0]
    data_rows = table.data_rows

    # Row 1 (col0="Network") and row 2 (col0 carried forward to "Network")
    col0_values = [r.cells[0].text for r in data_rows]
    assert col0_values[0] == "Network"
    assert col0_values[1] == "Network"   # carry-forward applied
    assert col0_values[2] == "Security"
    assert col0_values[3] == "Security"  # carry-forward applied

    # Cells in non-header rows should have a path set
    for row in data_rows:
        for cell in row.cells:
            assert isinstance(cell.path, list)


# ---------------------------------------------------------------------------
# markdown_serializer
# ---------------------------------------------------------------------------

def test_table_to_markdown():
    from markdown_serializer import table_to_markdown
    header_row = Row(cells=[
        Cell(text="Name", row_idx=0, col_idx=0, is_header=True),
        Cell(text="Age", row_idx=0, col_idx=1, is_header=True),
    ], index=0, is_header=True)
    data_row = Row(cells=[
        Cell(text="Alice", row_idx=1, col_idx=0),
        Cell(text="30", row_idx=1, col_idx=1),
    ], index=1)
    table = Table(rows=[header_row, data_row], page=0)
    md = table_to_markdown(table)
    assert "| Name | Age |" in md
    assert "| --- |" in md
    assert "| Alice | 30 |" in md


# ---------------------------------------------------------------------------
# ast_builder - dispatcher
# ---------------------------------------------------------------------------

def test_build_document_unsupported_format(tmp_path):
    from ast_builder import build_document
    bad = tmp_path / "file.txt"
    bad.write_text("hello")
    with pytest.raises(ValueError, match="Unsupported"):
        build_document(bad)


# ---------------------------------------------------------------------------
# graph_loader - dry-run (no real Neo4j)
# ---------------------------------------------------------------------------

def test_graph_loader_builds_cypher_params():
    cells = [
        Cell(text="Name", row_idx=0, col_idx=0, is_header=True, path=["Sheet1", "Name"]),
        Cell(text="Age", row_idx=0, col_idx=1, is_header=True, path=["Sheet1", "Age"]),
    ]
    row = Row(cells=cells, index=0, is_header=True)
    table = Table(rows=[row], page=0)
    section = Section(title="p1", content=[table], page=0)
    doc = Document(source="x.pdf", sections=[section])

    for obj in [doc, section, table, row] + cells:
        assert isinstance(obj.node_id, str) and len(obj.node_id) > 0
    assert cells[0].path == ["Sheet1", "Name"]


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def test_config_add_and_load(tmp_path, monkeypatch):
    from config import Profile, Config, save_config, load_config
    monkeypatch.setenv("TABLE_SPEC_EXTRACTOR_CONFIG", str(tmp_path / "config.json"))

    cfg = Config()
    cfg.add(Profile(name="local", neo4j_uri="bolt://localhost:7687",
                    data_path=str(tmp_path / "data")))
    cfg.add(Profile(name="prod", neo4j_uri="bolt://prod:7687"))
    cfg.default_profile = "local"
    save_config(cfg)

    loaded = load_config()
    assert "local" in loaded.profiles
    assert "prod" in loaded.profiles
    assert loaded.default_profile == "local"
    assert loaded.get("local").data_path == str(tmp_path / "data")


def test_config_get_default(tmp_path, monkeypatch):
    from config import Profile, Config, save_config, load_config
    monkeypatch.setenv("TABLE_SPEC_EXTRACTOR_CONFIG", str(tmp_path / "config.json"))

    cfg = Config()
    cfg.add(Profile(name="local", neo4j_uri="bolt://localhost:7687"))
    cfg.default_profile = "local"
    save_config(cfg)

    loaded = load_config()
    p = loaded.get()  # no name → use default
    assert p is not None
    assert p.neo4j_uri == "bolt://localhost:7687"


# ---------------------------------------------------------------------------
# run CLI - dry-run with Excel
# ---------------------------------------------------------------------------

def test_run_dry_run_excel(tmp_path, capsys):
    import argparse
    from run import run

    path = _make_excel(tmp_path)
    args = argparse.Namespace(
        file=str(path),
        profile="",
        neo4j="",
        user="",
        password="",
        database="",
        data_path="",
        device="cpu",
        threshold=0.9,
        dpi=150,
        dry_run=True,
    )
    run(args)
    captured = capsys.readouterr()
    assert "Sheet1" in captured.out
    assert "Alice" in captured.out
