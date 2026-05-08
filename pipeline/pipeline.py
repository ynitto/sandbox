"""CLI entry point for the document graph pipeline.

Subcommands:
    save    Excel/PDF → Table Transformer → AST → Neo4j
    search  Full-text + graph-traversal query against Neo4j

Examples:
    python -m pipeline.pipeline save report.pdf --dry-run
    python -m pipeline.pipeline save data.xlsx --neo4j bolt://localhost:7687
    python -m pipeline.pipeline search "revenue 2024" --neo4j bolt://localhost:7687
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .ast_builder import build_document
from .graph_loader import Neo4jLoader
from .search import GraphSearcher, format_results
from .table_extractor import TableTransformerExtractor


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

def _ast_to_dict(doc) -> dict:
    from .models import Table, Paragraph

    def cell_d(c):
        return {"text": c.text, "row": c.row_idx, "col": c.col_idx, "header": c.is_header}

    def row_d(r):
        return {"index": r.index, "header": r.is_header, "cells": [cell_d(c) for c in r.cells]}

    def table_d(t):
        return {"type": "table", "page": t.page, "sheet": t.sheet,
                "rows": [row_d(r) for r in t.rows]}

    def para_d(p):
        return {"type": "paragraph", "page": p.page,
                "text": p.text[:120] + ("…" if len(p.text) > 120 else "")}

    def section_d(s):
        return {"title": s.title, "page": s.page,
                "content": [table_d(i) if isinstance(i, Table) else para_d(i)
                            for i in s.content]}

    return {"source": doc.source, "metadata": doc.metadata,
            "sections": [section_d(s) for s in doc.sections]}


def cmd_save(args: argparse.Namespace) -> None:
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
        print("[3/3] Loading document graph …")
        loader.load(doc)
    print("Done.")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace) -> None:
    if not args.neo4j:
        print("Error: --neo4j URI is required for search.", file=sys.stderr)
        sys.exit(1)

    with GraphSearcher(args.neo4j, args.user, args.password, args.database) as searcher:
        result = searcher.search(args.query, limit=args.limit)

    if args.json:
        import dataclasses
        print(json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2))
    else:
        print(format_results(result))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _common_neo4j(p: argparse.ArgumentParser) -> None:
    p.add_argument("--neo4j", default="", help="Neo4j bolt URI")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", default="")
    p.add_argument("--database", default="neo4j")


def main() -> None:
    root = argparse.ArgumentParser(description="Document graph pipeline")
    sub = root.add_subparsers(dest="cmd", required=True)

    # save
    p_save = sub.add_parser("save", help="Ingest file and load into Neo4j")
    p_save.add_argument("file", help="PDF or Excel file path")
    _common_neo4j(p_save)
    p_save.add_argument("--device", default="cpu")
    p_save.add_argument("--threshold", type=float, default=0.9)
    p_save.add_argument("--dpi", type=int, default=150)
    p_save.add_argument("--dry-run", action="store_true",
                        help="Print AST as JSON without loading")

    # search
    p_search = sub.add_parser("search", help="Full-text + graph search")
    p_search.add_argument("query", help="Search query string")
    _common_neo4j(p_search)
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--json", action="store_true", help="Output as JSON")

    args = root.parse_args()
    if args.cmd == "save":
        cmd_save(args)
    else:
        cmd_search(args)


# Support legacy: `run(args)` used in tests
def run(args: argparse.Namespace) -> None:
    cmd_save(args)


if __name__ == "__main__":
    main()
