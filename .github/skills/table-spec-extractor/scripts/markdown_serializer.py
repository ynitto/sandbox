"""Convert Document AST nodes to Markdown for LLM-friendly representation."""
from __future__ import annotations


def table_to_markdown(table) -> str:
    """Serialize a Table AST node to a Markdown table string."""
    if not table.rows:
        return ""
    max_cols = max((len(r.cells) for r in table.rows), default=0)
    if max_cols == 0:
        return ""

    lines = []
    separator_added = False
    for row in table.rows:
        cells = [c.text for c in row.cells]
        while len(cells) < max_cols:
            cells.append("")
        escaped = [c.replace("|", "\\|").replace("\n", " ") for c in cells]
        lines.append("| " + " | ".join(escaped) + " |")
        if not separator_added and (row.is_header or len(lines) == 1):
            lines.append("|" + "|".join([" --- " for _ in range(max_cols)]) + "|")
            separator_added = True

    return "\n".join(lines)


def document_to_markdown(doc) -> str:
    """Serialize a full Document AST to Markdown (sections → tables/paragraphs)."""
    from models import Table, Paragraph

    parts: list[str] = []
    for section in doc.sections:
        parts.append(f"## {section.title}\n")
        for item in section.content:
            if isinstance(item, Table):
                parts.append(table_to_markdown(item))
            elif isinstance(item, Paragraph):
                parts.append(item.text)
        parts.append("")
    return "\n".join(parts)
