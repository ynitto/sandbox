#!/usr/bin/env python3
"""
ofm_formatter.py — markitdown出力をObsidian Flavored Markdownに機械的後処理するスクリプト

【担当範囲】機械的に確定できる変換のみ実施する
  - フロントマター付加（タイトル・日付・tags雛形・ソース情報）
  - 画像リンク → Obsidian埋め込み形式 (![[filename]])
  - .mdファイルへの内部リンク → ウィキリンク ([[filename|text]])
  - H1が複数ある場合の見出しレベル正規化
  - Markdownテーブル内のNaN（Excel空セル・結合セル由来）を除去
  - 不要な空白・過剰な空行を正規化（行末空白除去・3行以上の連続空行を2行に圧縮）

【LLMに委ねる範囲】（このスクリプトでは扱わない）
  - コールアウト（> [!NOTE] 等）への意味的変換
  - タグの推定・内容に基づく付加
  - ウィキリンク候補の語句選定（Vault内の実在ノートへの参照）

使い方:
  python ofm_formatter.py input.md
  python ofm_formatter.py input.md output.md
  markitdown document.docx | python ofm_formatter.py -
"""

import re
import sys
from pathlib import Path
from datetime import date


def add_frontmatter(content: str, source_path: str) -> str:
    """フロントマターを追加する（既存の場合はスキップ）"""
    if content.startswith("---"):
        return content

    title = Path(source_path).stem if source_path != "-" else "imported"
    today = date.today().isoformat()
    source_name = Path(source_path).name if source_path != "-" else ""

    source_line = f"source: {source_name}\n" if source_name else ""
    frontmatter = (
        f"---\n"
        f"title: {title}\n"
        f"date: {today}\n"
        f"tags:\n"
        f"  - imported\n"
        f"{source_line}"
        f"---\n\n"
    )
    return frontmatter + content


def convert_image_links(content: str) -> str:
    """画像リンクをObsidian埋め込み形式に変換する

    ![alt](path/to/image.png) → ![[image.png]]
    外部URL（http/https）は変換しない
    """

    def replace_image(m: re.Match) -> str:
        path = m.group(2)
        if path.startswith("http://") or path.startswith("https://"):
            return m.group(0)
        filename = Path(path).name
        return f"![[{filename}]]"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_image, content)


def convert_internal_links(content: str) -> str:
    """内部.mdファイルへのリンクをウィキリンクに変換する（保守的）

    [text](filename.md) → [[filename|text]] または [[filename]]
    外部URL（http/https）・アンカーリンク（#）・.md以外のファイルは変換しない
    """

    def replace_link(m: re.Match) -> str:
        text = m.group(1)
        path = m.group(2)
        if (
            path.startswith("http://")
            or path.startswith("https://")
            or path.startswith("#")
        ):
            return m.group(0)
        if path.endswith(".md"):
            stem = Path(path).stem
            if text == stem or text == path:
                return f"[[{stem}]]"
            return f"[[{stem}|{text}]]"
        return m.group(0)

    return re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", replace_link, content)


def clean_nan_in_tables(content: str) -> str:
    """Markdownテーブル内のNaNを空文字に置換する

    markitdownがExcelを変換した際、空セル・結合セルが NaN として出力される。
    テーブル行（| で始まる行）のセル内の NaN / nan / NaN のみを対象とする。
    テーブル以外の本文中に NaN という語が登場する場合は変換しない。
    """

    def replace_nan_in_row(m: re.Match) -> str:
        row = m.group(0)
        # セル区切り | の間にある NaN を除去（前後の空白も含む）
        return re.sub(r"(?<=\|)\s*NaN\s*(?=\|)", "  ", row, flags=re.IGNORECASE)

    return re.sub(r"^\|.*\|\s*$", replace_nan_in_row, content, flags=re.MULTILINE)


def normalize_whitespace(content: str) -> str:
    """不要な空白・過剰な空行を除去する

    - 行末の空白・タブを除去
    - 3行以上連続する空行を2行（段落間の空行として最大1行）に圧縮
    - ファイル末尾に改行を1つ確保
    """
    # 行末の空白を除去
    lines = [line.rstrip() for line in content.split("\n")]

    # 連続空行を最大2行（空行1行）に圧縮
    result = []
    blank_count = 0
    for line in lines:
        if line == "":
            blank_count += 1
            if blank_count <= 2:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)

    # 末尾の余分な空行を1つに整理
    while result and result[-1] == "":
        result.pop()
    result.append("")

    return "\n".join(result)


def normalize_headings(content: str) -> str:
    """H1が複数ある場合、2番目以降をH2に降格する

    markitdownがドキュメントタイトルをH1として出力し、
    本文中の大見出しも同じくH1になる場合への対処。
    """
    lines = content.split("\n")
    h1_count = sum(1 for line in lines if re.match(r"^# [^#]", line))
    if h1_count <= 1:
        return content

    first_h1_seen = False
    result = []
    for line in lines:
        if re.match(r"^# [^#]", line):
            if not first_h1_seen:
                first_h1_seen = True
                result.append(line)
            else:
                result.append("#" + line)  # H1 → H2
        else:
            result.append(line)
    return "\n".join(result)


def format_ofm(content: str, source_path: str) -> str:
    """機械的OFM変換のエントリポイント"""
    content = clean_nan_in_tables(content)
    content = normalize_whitespace(content)
    content = add_frontmatter(content, source_path)
    content = convert_image_links(content)
    content = convert_internal_links(content)
    content = normalize_headings(content)
    return content


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python ofm_formatter.py <input.md|-stdin> [output.md]",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) >= 3 else None

    if input_path == "-":
        content = sys.stdin.read()
        source_label = "-"
    else:
        with open(input_path, "r", encoding="utf-8") as f:
            content = f.read()
        source_label = input_path

    result = format_ofm(content, source_label)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"Written to {output_path}", file=sys.stderr)
    else:
        print(result)
