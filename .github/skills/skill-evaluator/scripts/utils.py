"""スキル作成ユーティリティ（共通関数）。"""

from __future__ import annotations

from pathlib import Path


def parse_skill_md(skill_path: Path) -> tuple[str, str, str]:
    """SKILL.md をパースして (name, description, full_content) を返す。

    YAML フロントマターのブロックスカラー（>、|、>-、|-）にも対応。
    """
    content = (skill_path / "SKILL.md").read_text(encoding="utf-8")
    lines = content.split("\n")

    if lines[0].strip() != "---":
        raise ValueError("SKILL.md にフロントマターがありません（--- で開始してください）")

    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        raise ValueError("SKILL.md のフロントマターが閉じられていません（--- が必要）")

    name = ""
    description = ""
    frontmatter_lines = lines[1:end_idx]
    i = 0
    while i < len(frontmatter_lines):
        line = frontmatter_lines[i]
        if line.startswith("name:"):
            name = line[len("name:"):].strip().strip('"').strip("'")
        elif line.startswith("description:"):
            value = line[len("description:"):].strip()
            # YAML ブロックスカラー (>, |, >-, |-) への対応
            if value in (">", "|", ">-", "|-"):
                continuation_lines: list[str] = []
                i += 1
                while i < len(frontmatter_lines) and (
                    frontmatter_lines[i].startswith("  ") or frontmatter_lines[i].startswith("\t")
                ):
                    continuation_lines.append(frontmatter_lines[i].strip())
                    i += 1
                description = " ".join(continuation_lines)
                continue
            else:
                description = value.strip('"').strip("'")
        i += 1

    return name, description, content
