"""spec-value-finder の軽量データ構造。

Neo4j も ML モデルも使わず、抽出結果はプレーンな dataclass で表現する。
ExtractedDoc.blocks は Table / TextBlock の順序付きリストで、元ドキュメントの
出現順を保持する（Word の見出し・本文・表の混在順序を壊さないため）。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field


def normalize(text: str) -> str:
    """照合用の正規化。NFKC で全角/半角を吸収し小文字化する。"""
    return unicodedata.normalize("NFKC", text or "").lower().strip()


@dataclass
class Cell:
    text: str
    row: int                       # Excel: 1始まりの実座標 / Word: 表内0始まり
    col: int                       # 同上
    is_header: bool = False
    path: list[str] = field(default_factory=list)  # breadcrumb [文脈, 親, 列見出し]


@dataclass
class Table:
    container: str                 # シート名(Excel) または "表#N"(Word)
    rows: list[list[Cell]] = field(default_factory=list)
    kind: str = "table"


@dataclass
class TextBlock:
    text: str
    style: str = ""                # "Heading 1" など
    path: list[str] = field(default_factory=list)  # 見出し breadcrumb
    kind: str = "text"


@dataclass
class ExtractedDoc:
    source: str                    # 絶対パス
    filename: str
    fmt: str                       # "excel" | "word"
    blocks: list = field(default_factory=list)  # list[Table | TextBlock]

    def to_dict(self) -> dict:
        def block_d(b):
            if isinstance(b, Table):
                return {
                    "kind": "table",
                    "container": b.container,
                    "rows": [[vars(c) for c in row] for row in b.rows],
                }
            return {"kind": "text", "text": b.text, "style": b.style, "path": b.path}

        return {
            "source": self.source,
            "filename": self.filename,
            "fmt": self.fmt,
            "blocks": [block_d(b) for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExtractedDoc":
        blocks: list = []
        for b in d.get("blocks", []):
            if b.get("kind") == "table":
                blocks.append(Table(
                    container=b["container"],
                    rows=[[Cell(**c) for c in row] for row in b["rows"]],
                ))
            else:
                blocks.append(TextBlock(
                    text=b["text"], style=b.get("style", ""), path=b.get("path", []),
                ))
        return cls(source=d["source"], filename=d["filename"],
                   fmt=d["fmt"], blocks=blocks)
