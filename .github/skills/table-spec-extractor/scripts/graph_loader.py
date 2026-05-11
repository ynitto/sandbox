"""Load Document AST into Neo4j with a GraphRAG-ready schema.

Node labels:
  (:Document)  (:Section)  (:Table)  (:Row)  (:Cell)  (:Paragraph)

Relationships:
  (Document)-[:HAS_SECTION]->(Section)
  (Section)-[:CONTAINS]->(Table|Paragraph)
  (Table)-[:HAS_ROW]->(Row)
  (Row)-[:HAS_CELL]->(Cell)
  (Row)-[:NEXT_ROW]->(Row)           # sequential navigation
  (Cell)-[:NEXT_CELL]->(Cell)        # same row, left→right
  (Cell)-[:SAME_COLUMN]->(Cell)      # same column index across rows
"""
from __future__ import annotations
from contextlib import contextmanager

import json as _json

from neo4j import GraphDatabase

from models import Document, Section, Table, Row, Cell, Paragraph
from markdown_serializer import table_to_markdown


# ---------------------------------------------------------------------------
# Cypher helpers
# ---------------------------------------------------------------------------

_MERGE_DOCUMENT = """
MERGE (d:Document {node_id: $node_id})
SET d.source = $source, d.metadata = $metadata
RETURN d
"""

_MERGE_SECTION = """
MERGE (s:Section {node_id: $node_id})
SET s.title = $title, s.page = $page
RETURN s
"""

_MERGE_TABLE = """
MERGE (t:Table {node_id: $node_id})
SET t.page = $page, t.sheet = $sheet, t.bbox = $bbox, t.markdown_text = $markdown_text
RETURN t
"""

_MERGE_ROW = """
MERGE (r:Row {node_id: $node_id})
SET r.index = $index, r.is_header = $is_header
RETURN r
"""

_MERGE_CELL = """
MERGE (c:Cell {node_id: $node_id})
SET c.text = $text, c.row_idx = $row_idx, c.col_idx = $col_idx,
    c.is_header = $is_header, c.path = $path
RETURN c
"""

_MERGE_PARAGRAPH = """
MERGE (p:Paragraph {node_id: $node_id})
SET p.text = $text, p.page = $page
RETURN p
"""

_REL = "MATCH (a {{node_id: $a}}) MATCH (b {{node_id: $b}}) MERGE (a)-[:{rel}]->(b)"


def _rel_q(rel: str) -> str:
    return _REL.format(rel=rel)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class Neo4jLoader:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def close(self):
        self._driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------

    def load(self, doc: Document) -> None:
        with self._driver.session(database=self._database) as session:
            session.execute_write(self._write_document, doc)

    @staticmethod
    def _write_document(tx, doc: Document) -> None:
        tx.run(_MERGE_DOCUMENT, node_id=doc.node_id, source=doc.source,
               metadata=str(doc.metadata))

        for section in doc.sections:
            tx.run(_MERGE_SECTION, node_id=section.node_id,
                   title=section.title, page=section.page)
            tx.run(_rel_q("HAS_SECTION"), a=doc.node_id, b=section.node_id)

            for item in section.content:
                if isinstance(item, Table):
                    Neo4jLoader._write_table(tx, item, section.node_id)
                elif isinstance(item, Paragraph):
                    tx.run(_MERGE_PARAGRAPH, node_id=item.node_id,
                           text=item.text, page=item.page)
                    tx.run(_rel_q("CONTAINS"), a=section.node_id, b=item.node_id)

    @staticmethod
    def _write_table(tx, table: Table, section_id: str) -> None:
        tx.run(_MERGE_TABLE, node_id=table.node_id, page=table.page,
               sheet=table.sheet or "",
               bbox=str(table.bbox) if table.bbox else "",
               markdown_text=table_to_markdown(table))
        tx.run(_rel_q("CONTAINS"), a=section_id, b=table.node_id)

        if not table.rows:
            return

        # Bulk-merge all rows in one query
        row_params = [
            {"node_id": row.node_id, "index": row.index, "is_header": row.is_header}
            for row in table.rows
        ]
        tx.run(
            "UNWIND $rows AS r MERGE (n:Row {node_id: r.node_id}) "
            "SET n.index = r.index, n.is_header = r.is_header",
            rows=row_params,
        )

        # Bulk-create HAS_ROW relationships
        tx.run(
            "MATCH (t:Table {node_id: $tid}) "
            "UNWIND $row_ids AS rid MATCH (r:Row {node_id: rid}) "
            "MERGE (t)-[:HAS_ROW]->(r)",
            tid=table.node_id,
            row_ids=[row.node_id for row in table.rows],
        )

        # Bulk-create NEXT_ROW relationships
        row_pairs = [
            {"a": table.rows[i].node_id, "b": table.rows[i + 1].node_id}
            for i in range(len(table.rows) - 1)
        ]
        if row_pairs:
            tx.run(
                "UNWIND $pairs AS p "
                "MATCH (a:Row {node_id: p.a}) MATCH (b:Row {node_id: p.b}) "
                "MERGE (a)-[:NEXT_ROW]->(b)",
                pairs=row_pairs,
            )

        # Collect all cells and build NEXT_CELL / SAME_COLUMN data
        cell_params: list[dict] = []
        has_cell_pairs: list[dict] = []
        next_cell_pairs: list[dict] = []
        col_cells: dict[int, list[str]] = {}

        for row in table.rows:
            prev_cell_id: str | None = None
            for cell in row.cells:
                cell_params.append({
                    "node_id": cell.node_id,
                    "text": cell.text,
                    "row_idx": cell.row_idx,
                    "col_idx": cell.col_idx,
                    "is_header": cell.is_header,
                    "path": _json.dumps(cell.path, ensure_ascii=False),
                })
                has_cell_pairs.append({"row_id": row.node_id, "cell_id": cell.node_id})
                if prev_cell_id:
                    next_cell_pairs.append({"a": prev_cell_id, "b": cell.node_id})
                prev_cell_id = cell.node_id
                col_cells.setdefault(cell.col_idx, []).append(cell.node_id)

        # Bulk-merge all cells
        tx.run(
            "UNWIND $cells AS c MERGE (n:Cell {node_id: c.node_id}) "
            "SET n.text = c.text, n.row_idx = c.row_idx, n.col_idx = c.col_idx, "
            "n.is_header = c.is_header, n.path = c.path",
            cells=cell_params,
        )

        # Bulk-create HAS_CELL relationships
        tx.run(
            "UNWIND $pairs AS p "
            "MATCH (r:Row {node_id: p.row_id}) MATCH (c:Cell {node_id: p.cell_id}) "
            "MERGE (r)-[:HAS_CELL]->(c)",
            pairs=has_cell_pairs,
        )

        # Bulk-create NEXT_CELL relationships
        if next_cell_pairs:
            tx.run(
                "UNWIND $pairs AS p "
                "MATCH (a:Cell {node_id: p.a}) MATCH (b:Cell {node_id: p.b}) "
                "MERGE (a)-[:NEXT_CELL]->(b)",
                pairs=next_cell_pairs,
            )

        # Bulk-create SAME_COLUMN edges
        same_col_pairs = [
            {"a": ids[i], "b": ids[i + 1]}
            for ids in col_cells.values()
            for i in range(len(ids) - 1)
        ]
        if same_col_pairs:
            tx.run(
                "UNWIND $pairs AS p "
                "MATCH (a:Cell {node_id: p.a}) MATCH (b:Cell {node_id: p.b}) "
                "MERGE (a)-[:SAME_COLUMN]->(b)",
                pairs=same_col_pairs,
            )

    # ------------------------------------------------------------------
    # Index setup (run once)
    # ------------------------------------------------------------------

    def create_indexes(self) -> None:
        queries = [
            "CREATE INDEX doc_id IF NOT EXISTS FOR (n:Document) ON (n.node_id)",
            "CREATE INDEX section_id IF NOT EXISTS FOR (n:Section) ON (n.node_id)",
            "CREATE INDEX table_id IF NOT EXISTS FOR (n:Table) ON (n.node_id)",
            "CREATE INDEX row_id IF NOT EXISTS FOR (n:Row) ON (n.node_id)",
            "CREATE INDEX cell_id IF NOT EXISTS FOR (n:Cell) ON (n.node_id)",
            "CREATE INDEX cell_text IF NOT EXISTS FOR (n:Cell) ON (n.text)",
            "CREATE INDEX para_id IF NOT EXISTS FOR (n:Paragraph) ON (n.node_id)",
            "CREATE FULLTEXT INDEX cell_fulltext IF NOT EXISTS FOR (n:Cell) ON EACH [n.text]",
            "CREATE FULLTEXT INDEX para_fulltext IF NOT EXISTS FOR (n:Paragraph) ON EACH [n.text]",
        ]
        with self._driver.session(database=self._database) as session:
            for q in queries:
                session.run(q)
