"""マッピング × 抽出結果 → 候補抽出（キーワード前段フィルタ）。

ベクトルDBもグラフトラバーサルも使わない。マッピングの keywords で
テキストを機械的に絞り込み、各項目ごとに候補（値・出典・breadcrumb・行文脈）を
出力する。意味的にどの候補が正解かの最終判断は Claude（呼び出し側）が行う。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from extract import _table_to_markdown
from models import ExtractedDoc, Table, TextBlock, normalize


@dataclass
class Candidate:
    text: str                        # 一致したセル/段落のテキスト
    location: str                    # 出典（ファイル :: 容器 :: 座標）
    path: list[str]                  # breadcrumb
    context: str                     # 行 or 段落の文脈
    row_values: list[str] = field(default_factory=list)  # セル一致時の行全体
    matched_keywords: list[str] = field(default_factory=list)
    score: int = 0

    def to_dict(self) -> dict:
        return vars(self)


def _col_label(col: int) -> str:
    from openpyxl.utils import get_column_letter
    try:
        return get_column_letter(col)
    except Exception:
        return str(col)


def _cell_location(doc: ExtractedDoc, table: Table, cell) -> str:
    if doc.fmt == "excel":
        coord = f"{_col_label(cell.col)}{cell.row}"
        return f"{doc.filename} :: シート'{table.container}' :: {coord}"
    return f"{doc.filename} :: {table.container} :: 行{cell.row + 1}列{cell.col + 1}"


def _text_location(doc: ExtractedDoc, idx: int, block: TextBlock) -> str:
    if block.locator:
        return f"{doc.filename} :: {block.locator}"
    return f"{doc.filename} :: 段落#{idx + 1}"


def _hint_bonus(section_hint: str, path: list[str], container: str) -> int:
    if not section_hint:
        return 0
    h = normalize(section_hint)
    hay = normalize(" ".join(path) + " " + container)
    return 1 if h and h in hay else 0


def search_item(docs: list[ExtractedDoc], item: dict[str, Any],
                max_candidates: int = 8) -> list[Candidate]:
    """1 マッピング項目に対する候補リストを返す。"""
    keywords = [str(k) for k in (item.get("keywords") or []) if str(k).strip()]
    norm_kws = [(k, normalize(k)) for k in keywords]
    section_hint = str(item.get("section_hint") or "")
    by_location: dict[str, Candidate] = {}

    for doc in docs:
        for idx, block in enumerate(doc.blocks):
            if isinstance(block, Table):
                for row in block.rows:
                    row_values = [c.text for c in row]
                    row_md = "| " + " | ".join(
                        v.replace("|", "\\|").replace("\n", " ") for v in row_values
                    ) + " |"
                    for cell in row:
                        if not cell.text.strip():
                            continue
                        ncell = normalize(cell.text)
                        hit = [orig for orig, nk in norm_kws if nk and nk in ncell]
                        if not hit:
                            continue
                        loc = _cell_location(doc, block, cell)
                        score = len(set(hit)) + _hint_bonus(
                            section_hint, cell.path, block.container)
                        cand = Candidate(
                            text=cell.text, location=loc, path=cell.path,
                            context=row_md, row_values=row_values,
                            matched_keywords=sorted(set(hit)), score=score)
                        _keep_best(by_location, loc, cand)
            elif isinstance(block, TextBlock):
                ntext = normalize(block.text)
                hit = [orig for orig, nk in norm_kws if nk and nk in ntext]
                if not hit:
                    continue
                loc = _text_location(doc, idx, block)
                score = len(set(hit)) + _hint_bonus(
                    section_hint, block.path, block.style)
                snippet = block.text if len(block.text) <= 300 else block.text[:300] + "…"
                cand = Candidate(
                    text=snippet, location=loc, path=block.path,
                    context=snippet, matched_keywords=sorted(set(hit)), score=score)
                _keep_best(by_location, loc, cand)

    ranked = sorted(by_location.values(), key=lambda c: c.score, reverse=True)
    return ranked[:max_candidates]


def _keep_best(store: dict[str, Candidate], loc: str, cand: Candidate) -> None:
    cur = store.get(loc)
    if cur is None or cand.score > cur.score:
        store[loc] = cand


def find_candidates(docs: list[ExtractedDoc], items: list[dict],
                    max_candidates: int = 8) -> dict:
    """全マッピング項目の候補を集約した dict を返す（candidates.json の中身）。"""
    result_items = []
    for item in items:
        cands = search_item(docs, item, max_candidates=max_candidates)
        result_items.append({
            "target": item.get("target"),
            "keywords": item.get("keywords", []),
            "section_hint": item.get("section_hint", ""),
            "unit": item.get("unit", ""),
            "type": item.get("type", ""),
            "candidate_count": len(cands),
            "candidates": [c.to_dict() for c in cands],
        })
    return {
        "sources": [d.filename for d in docs],
        "items": result_items,
    }


def format_candidates(result: dict) -> str:
    """候補を人間可読のテキストに整形する。"""
    lines = [f"対象ファイル: {', '.join(result['sources']) or '(なし)'}", ""]
    for item in result["items"]:
        lines.append(f"■ {item['target']}  （候補 {item['candidate_count']} 件）")
        if not item["candidates"]:
            lines.append("  候補なし — keywords の見直しを検討してください。")
        for i, c in enumerate(item["candidates"], 1):
            bc = " > ".join(c["path"]) if c["path"] else "-"
            lines.append(f"  [{i}] score={c['score']} 一致={','.join(c['matched_keywords'])}")
            lines.append(f"      出典: {c['location']}")
            lines.append(f"      文脈: {bc}")
            lines.append(f"            {c['context']}")
        lines.append("")
    return "\n".join(lines)
