"""doc-drift-detector の Markdown レポートを集約用 JSON に変換する。

JSON 様式（version 1）
- version: 1、source: "doc-drift-detector"、generated_at: UTC の ISO 8601 日時
- summary: documents、claims、verified、drifted、unverifiable の件数と、
  by_class（DOC-STALE、CODE-DRIFT、UNKNOWN）、by_severity（critical、major、
  minor）の指摘件数。該当がない分類・重大度も 0 を出す。
- findings: id、class、severity、title を持つ指摘の配列（レポートの掲載順）
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile


SUMMARY_HEADER = ["ドキュメント", "claim数", "verified", "drifted", "unverifiable", "鮮度スコア"]
COUNT_KEYS = ("claims", "verified", "drifted", "unverifiable")
CLASSES = ("DOC-STALE", "CODE-DRIFT", "UNKNOWN")
SEVERITIES = ("critical", "major", "minor")
FINDING = re.compile(
    r"###\s+(D-[0-9]+)\s+\[(DOC-STALE|CODE-DRIFT|UNKNOWN)\s*/\s*"
    r"(critical|major|minor)\]\s+(.+)"
)


def section(lines, title):
    active = False
    for line in lines:
        heading = re.fullmatch(r"##\s+(.+)", line)
        if heading:
            if active:
                break
            active = heading[1] == title
        elif active:
            yield line


def table_cells(line):
    return [cell.strip() for cell in line.strip("|").split("|")]


def summary_counts(lines):
    rows = iter(section(lines, "サマリ"))
    for line in rows:
        if table_cells(line) == SUMMARY_HEADER:
            break
    else:
        raise ValueError("サマリ表の見出しがありません")

    separator = table_cells(next(rows, ""))
    if len(separator) != len(SUMMARY_HEADER) or not all(
        re.fullmatch(r":?-+:?", cell) for cell in separator
    ):
        raise ValueError("サマリ表の区切り行を読み取れません")

    counts = dict.fromkeys(("documents", *COUNT_KEYS), 0)
    for line in rows:
        if not line or "|" not in line:
            break
        cells = table_cells(line)
        if len(cells) != len(SUMMARY_HEADER) or not cells[0] or not all(
            re.fullmatch(r"[0-9]+", cell) for cell in cells[1:5]
        ):
            raise ValueError(f"サマリ表の行を読み取れません: {line}")
        counts["documents"] += 1
        for key, value in zip(COUNT_KEYS, cells[1:5]):
            counts[key] += int(value)
    return counts


def convert(markdown, generated_at):
    lines = [line.strip() for line in markdown.splitlines()]
    summary = summary_counts(lines)
    summary["by_class"] = dict.fromkeys(CLASSES, 0)
    summary["by_severity"] = dict.fromkeys(SEVERITIES, 0)
    findings = []
    seen = set()
    for line in section(lines, "指摘一覧"):
        match = FINDING.fullmatch(line)
        if not match:
            if re.match(r"###(?:\s|$)", line):
                raise ValueError(f"指摘一覧の見出しを読み取れません: {line}")
            continue
        if match[1] in seen:
            continue
        finding_id, category, severity, title = match.groups()
        seen.add(finding_id)
        findings.append({"id": finding_id, "class": category, "severity": severity, "title": title})
        summary["by_class"][category] += 1
        summary["by_severity"][severity] += 1

    if summary["drifted"] > 0 and not findings:
        raise ValueError("サマリ表の drifted が 0 より大きいのに指摘一覧が空です")

    return {
        "version": 1,
        "source": "doc-drift-detector",
        "generated_at": generated_at,
        "summary": summary,
        "findings": findings,
    }


def write_report(path, report):
    # 集約が書き込み途中の JSON を読まないよう、同じディレクトリ内で置き換える。
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True, help="入力の Markdown レポート")
    parser.add_argument("--output", type=Path, required=True, help="出力先の JSON レポート")
    parser.add_argument("--now", help="generated_at に使う UTC の ISO 8601 日時")
    args = parser.parse_args()
    generated_at = args.now or datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        args.output.unlink(missing_ok=True)
        report = convert(args.input.read_text(encoding="utf-8-sig"), generated_at)
        write_report(args.output, report)
    except (OSError, ValueError) as error:
        print(f"レポートの変換に失敗しました: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
