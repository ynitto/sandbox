"""CLI entry point for the document → graph pipeline.

Usage:
    python -m pipeline.pipeline <file> [options]

Examples:
    python -m pipeline.pipeline report.pdf
    python -m pipeline.pipeline data.xlsx --neo4j bolt://localhost:7687
    python -m pipeline.pipeline report.pdf --neo4j bolt://localhost:7687 \\
        --user neo4j --password secret --dpi 200
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .ast_builder import build_document
from .graph_loader import Neo4jLoader
from .table_extractor import TableTransformerExtractor


def _ast_to_dict(doc) -> dict:
    """Serialize Document AST to a JSON-compatible dict (for --dry-run)."""
    from .models import Document, Section, Table, Row, Cell, Paragraph

    def cell_d(c: Cell) -> dict:
        return {"text": c.text, "row": c.row_idx, "col": c.col_idx,
                "header": c.is_header}

    def row_d(r: Row) -> dict:
        return {"index": r.index, "header": r.is_header,
                "cells": [cell_d(c) for c in r.cells]}

    def table_d(t: Table) -> dict:
        return {"type": "table", "page": t.page, "sheet": t.sheet,
                "rows": [row_d(r) for r in t.rows]}

    def para_d(p: Paragraph) -> dict:
        return {"type": "paragraph", "page": p.page,
                "text": p.text[:120] + ("…" if len(p.text) > 120 else "")}

    def section_d(s: Section) -> dict:
        return {"title": s.title, "page": s.page,
                "content": [table_d(i) if isinstance(i, Table) else para_d(i)
                            for i in s.content]}

    return {"source": doc.source, "metadata": doc.metadata,
            "sections": [section_d(s) for s in doc.sections]}


def run(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    extractor = TableTransformerExtractor(
        device=args.device, threshold=args.threshold
    ) if path.suffix.lower() == ".pdf" else None

    print(f"[1/3] Ingesting {path.name} …")
    doc = build_document(path, extractor=extractor, dpi=args.dpi)
    tables = doc.all_tables()
    print(f"      → {len(doc.sections)} sections, {len(tables)} tables, "
          f"{len(doc.all_paragraphs())} paragraphs")

    if args.dry_run:
        print(json.dumps(_ast_to_dict(doc), ensure_ascii=False, indent=2))
        return

    if not args.neo4j:
        print("[!] No --neo4j URI provided. Use --dry-run to inspect AST.")
        return

    print(f"[2/3] Connecting to Neo4j at {args.neo4j} …")
    with Neo4jLoader(args.neo4j, args.user, args.password, args.database) as loader:
        loader.create_indexes()
        print(f"[3/3] Loading document graph …")
        loader.load(doc)
    print("Done.")


def main() -> None:
    p = argparse.ArgumentParser(description="Excel/PDF → Table Transformer → AST → Neo4j")
    p.add_argument("file", help="Input PDF or Excel file")
    p.add_argument("--neo4j", default="", help="Neo4j bolt URI (e.g. bolt://localhost:7687)")
    p.add_argument("--user", default="neo4j", help="Neo4j username")
    p.add_argument("--password", default="", help="Neo4j password")
    p.add_argument("--database", default="neo4j", help="Neo4j database name")
    p.add_argument("--device", default="cpu", help="Torch device (cpu / cuda)")
    p.add_argument("--threshold", type=float, default=0.9,
                   help="Table detection confidence threshold")
    p.add_argument("--dpi", type=int, default=150, help="PDF render DPI")
    p.add_argument("--dry-run", action="store_true",
                   help="Print AST as JSON without loading to Neo4j")
    run(p.parse_args())


if __name__ == "__main__":
    main()
