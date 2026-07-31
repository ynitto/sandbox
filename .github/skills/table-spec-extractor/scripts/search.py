"""GraphRAG-style search against the Neo4j document graph."""
from __future__ import annotations
from dataclasses import dataclass

from neo4j import GraphDatabase


@dataclass
class CellHit:
    text: str
    row_idx: int
    col_idx: int
    is_header: bool
    table_page: int
    table_sheet: str
    section_title: str
    document_source: str
    column_header: str  # header cell text for the same column


@dataclass
class ParagraphHit:
    text: str
    page: int
    section_title: str
    document_source: str


@dataclass
class SearchResult:
    query: str
    cell_hits: list[CellHit]
    paragraph_hits: list[ParagraphHit]


_CELL_QUERY = """
CALL db.index.fulltext.queryNodes('cell_fulltext', $query)
YIELD node AS cell, score
WHERE score > 0
MATCH (row:Row)-[:HAS_CELL]->(cell)
MATCH (table:Table)-[:HAS_ROW]->(row)
MATCH (section:Section)-[:CONTAINS]->(table)
MATCH (doc:Document)-[:HAS_SECTION]->(section)
OPTIONAL MATCH (header_row:Row {is_header: true})-[:HAS_CELL]->(hcell:Cell)
  WHERE (table)-[:HAS_ROW]->(header_row)
    AND hcell.col_idx = cell.col_idx
WITH cell, row, table, section, doc,
     collect(hcell.text)[0] AS column_header,
     score
ORDER BY score DESC
LIMIT $limit
RETURN
  cell.text        AS text,
  cell.row_idx     AS row_idx,
  cell.col_idx     AS col_idx,
  cell.is_header   AS is_header,
  table.page       AS table_page,
  table.sheet      AS table_sheet,
  section.title    AS section_title,
  doc.source       AS document_source,
  coalesce(column_header, '') AS column_header
"""

_PARA_QUERY = """
CALL db.index.fulltext.queryNodes('para_fulltext', $query)
YIELD node AS para, score
WHERE score > 0
MATCH (section:Section)-[:CONTAINS]->(para)
MATCH (doc:Document)-[:HAS_SECTION]->(section)
ORDER BY score DESC
LIMIT $limit
RETURN
  para.text      AS text,
  para.page      AS page,
  section.title  AS section_title,
  doc.source     AS document_source
"""


class GraphSearcher:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def close(self):
        self._driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def search(self, query: str, limit: int = 10) -> SearchResult:
        with self._driver.session(database=self._database) as session:
            cell_hits = self._search_cells(session, query, limit)
            para_hits = self._search_paragraphs(session, query, limit)
        return SearchResult(query=query, cell_hits=cell_hits, paragraph_hits=para_hits)

    def _search_cells(self, session, query: str, limit: int) -> list[CellHit]:
        result = session.run(_CELL_QUERY, query=query, limit=limit)
        hits = []
        for rec in result:
            hits.append(CellHit(
                text=rec["text"],
                row_idx=rec["row_idx"],
                col_idx=rec["col_idx"],
                is_header=rec["is_header"],
                table_page=rec["table_page"],
                table_sheet=rec["table_sheet"] or "",
                section_title=rec["section_title"],
                document_source=rec["document_source"],
                column_header=rec["column_header"],
            ))
        return hits

    def _search_paragraphs(self, session, query: str, limit: int) -> list[ParagraphHit]:
        result = session.run(_PARA_QUERY, query=query, limit=limit)
        return [
            ParagraphHit(
                text=rec["text"],
                page=rec["page"],
                section_title=rec["section_title"],
                document_source=rec["document_source"],
            )
            for rec in result
        ]


def format_results(result: SearchResult) -> str:
    lines = [f'Search: "{result.query}"', ""]

    if result.cell_hits:
        lines.append(f"=== Table Cells ({len(result.cell_hits)}) ===")
        for h in result.cell_hits:
            col_info = f" [{h.column_header}]" if h.column_header else ""
            location = h.table_sheet or f"page {h.table_page + 1}"
            lines.append(f"  {h.document_source} › {h.section_title} › {location}"
                         f"{col_info} (row {h.row_idx}, col {h.col_idx})")
            lines.append(f"    {h.text}")
    else:
        lines.append("No table cell hits.")

    lines.append("")

    if result.paragraph_hits:
        lines.append(f"=== Paragraphs ({len(result.paragraph_hits)}) ===")
        for h in result.paragraph_hits:
            lines.append(f"  {h.document_source} › {h.section_title} (page {h.page + 1})")
            snippet = h.text[:200] + ("…" if len(h.text) > 200 else "")
            lines.append(f"    {snippet}")
    else:
        lines.append("No paragraph hits.")

    return "\n".join(lines)
