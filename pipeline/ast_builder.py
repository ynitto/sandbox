"""Build Document AST from raw ingestion + table extraction results."""
from __future__ import annotations
from pathlib import Path

from .models import Document, Section, Table, Row, Cell, Paragraph
from .ingest import ExcelSheet, PageImage, iter_excel_sheets, iter_pdf_images, pdf_text_by_page
from .table_extractor import TableTransformerExtractor


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
    return Table(rows=rows, page=page, sheet=sheet.name)


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
    return Table(rows=rows, page=page, bbox=bbox)


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
