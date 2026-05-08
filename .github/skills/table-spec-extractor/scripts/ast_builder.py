"""Build Document AST from raw ingestion + table extraction results."""
from __future__ import annotations
from pathlib import Path

from models import Document, Section, Table, Row, Cell, Paragraph
from ingest import ExcelSheet, iter_excel_sheets, iter_pdf_images, pdf_text_by_page
from table_extractor import TableTransformerExtractor


# ---------------------------------------------------------------------------
# Breadcrumb path inference
# ---------------------------------------------------------------------------

def _infer_paths(table: Table) -> None:
    """Fill Cell.path with breadcrumb labels inferred from table structure.

    For tables with carry-forward (merged) columns, propagates parent labels
    downward and builds a path like [context, parent_val, col_header].
    """
    if not table.rows:
        return

    header_row = next((r for r in table.rows if r.is_header), table.rows[0])
    col_headers: dict[int, str] = {c.col_idx: c.text for c in header_row.cells}

    data_rows = [r for r in table.rows if not r.is_header]
    if not data_rows:
        return

    # Detect hierarchy columns: >30% of data cells are empty
    all_cols = sorted({c.col_idx for r in data_rows for c in r.cells})
    n = len(data_rows)
    hierarchy_cols: set[int] = set()
    for ci in all_cols:
        empty = sum(
            1 for r in data_rows
            if any(c.col_idx == ci and not c.text.strip() for c in r.cells)
        )
        if n > 1 and empty / n > 0.3:
            hierarchy_cols.add(ci)

    ctx = table.sheet or f"page_{table.page + 1}"
    carry: dict[int, str] = {}

    for row in data_rows:
        for cell in row.cells:
            col = cell.col_idx
            val = cell.text.strip()
            if col in hierarchy_cols:
                if val:
                    carry[col] = val
                elif col in carry:
                    cell.text = carry[col]  # flatten carry-forward value
            path = [ctx]
            for hcol in sorted(hierarchy_cols):
                if hcol != col:
                    path.append(carry.get(hcol, ""))
            col_name = col_headers.get(col, "")
            if col_name:
                path.append(col_name)
            cell.path = [p for p in path if p]

    for cell in header_row.cells:
        cell.path = [ctx, cell.text]


# ---------------------------------------------------------------------------
# Excel path
# ---------------------------------------------------------------------------

def _excel_sheet_to_table(sheet: ExcelSheet, page: int = 0) -> Table:
    rows: list[Row] = []
    for r_idx, raw_row in enumerate(sheet.rows):
        is_header = r_idx == 0
        cells = [
            Cell(text=val, row_idx=r_idx, col_idx=c_idx, is_header=is_header)
            for c_idx, val in enumerate(raw_row)
        ]
        rows.append(Row(cells=cells, index=r_idx, is_header=is_header))
    table = Table(rows=rows, page=page, sheet=sheet.name)
    _infer_paths(table)
    return table


def build_from_excel(path: Path) -> Document:
    sections: list[Section] = []
    for sheet in iter_excel_sheets(path):
        table = _excel_sheet_to_table(sheet)
        sections.append(Section(title=sheet.name, content=[table]))
    return Document(
        source=str(path),
        sections=sections,
        metadata={"format": "excel", "sheets": len(sections)},
    )


# ---------------------------------------------------------------------------
# PDF path
# ---------------------------------------------------------------------------

def _grid_to_table(
    grid: list[list[str]],
    page: int,
    bbox: tuple,
    header_row_indices: list[int],
) -> Table:
    rows: list[Row] = []
    for r_idx, raw_row in enumerate(grid):
        is_header = r_idx in header_row_indices or r_idx == 0
        cells = [
            Cell(text=val, row_idx=r_idx, col_idx=c_idx, is_header=is_header)
            for c_idx, val in enumerate(raw_row)
        ]
        rows.append(Row(cells=cells, index=r_idx, is_header=is_header))
    table = Table(rows=rows, page=page, bbox=bbox)
    _infer_paths(table)
    return table


def build_from_pdf(
    path: Path,
    extractor: TableTransformerExtractor | None = None,
    ocr_fn=None,
    dpi: int = 150,
) -> Document:
    if extractor is None:
        extractor = TableTransformerExtractor()

    page_texts = pdf_text_by_page(path)
    sections: list[Section] = []

    for page_img in iter_pdf_images(path, dpi=dpi):
        p = page_img.page
        content: list[Table | Paragraph] = []

        # Add page text as paragraph if available
        text = page_texts.get(p, "").strip()
        if text:
            content.append(Paragraph(text=text, page=p))

        # Detect + parse tables
        detected = extractor.detect_tables(page_img.image)
        for det in detected:
            structure = extractor.recognize_structure(det.cropped)
            grid = extractor.extract_cell_text(det.cropped, structure, ocr_fn=ocr_fn)
            if not grid:
                continue
            table = _grid_to_table(
                grid=grid,
                page=p,
                bbox=det.bbox.as_tuple(),
                header_row_indices=structure.header_rows,
            )
            content.append(table)

        if content:
            sections.append(Section(title=f"Page {p + 1}", content=content, page=p))

    return Document(
        source=str(path),
        sections=sections,
        metadata={"format": "pdf", "pages": len(sections)},
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def build_document(
    path: Path | str,
    extractor: TableTransformerExtractor | None = None,
    ocr_fn=None,
    dpi: int = 150,
) -> Document:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls", ".xlsm"):
        return build_from_excel(path)
    elif suffix == ".pdf":
        return build_from_pdf(path, extractor=extractor, ocr_fn=ocr_fn, dpi=dpi)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")
