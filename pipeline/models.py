"""Document AST node definitions."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import uuid


def _uid() -> str:
    return str(uuid.uuid4())


@dataclass
class Cell:
    text: str
    row_idx: int
    col_idx: int
    is_header: bool = False
    bbox: Optional[tuple[float, float, float, float]] = None
    node_id: str = field(default_factory=_uid)


@dataclass
class Row:
    cells: list[Cell]
    index: int
    is_header: bool = False
    node_id: str = field(default_factory=_uid)


@dataclass
class Table:
    rows: list[Row]
    page: int = 0
    sheet: Optional[str] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    node_id: str = field(default_factory=_uid)

    @property
    def headers(self) -> list[Row]:
        return [r for r in self.rows if r.is_header]

    @property
    def data_rows(self) -> list[Row]:
        return [r for r in self.rows if not r.is_header]


@dataclass
class Paragraph:
    text: str
    page: int = 0
    node_id: str = field(default_factory=_uid)


@dataclass
class Section:
    title: str
    content: list[Table | Paragraph]
    page: int = 0
    node_id: str = field(default_factory=_uid)


@dataclass
class Document:
    source: str
    sections: list[Section]
    metadata: dict = field(default_factory=dict)
    node_id: str = field(default_factory=_uid)

    def all_tables(self) -> list[Table]:
        return [c for s in self.sections for c in s.content if isinstance(c, Table)]

    def all_paragraphs(self) -> list[Paragraph]:
        return [c for s in self.sections for c in s.content if isinstance(c, Paragraph)]
