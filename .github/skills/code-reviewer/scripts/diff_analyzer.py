#!/usr/bin/env python3
"""
diff_analyzer.py - git diff の解析・サマリー生成

unified diff 形式（git diff 出力）を解析してレビュー対象のファイルと変更行を
特定し、code-reviewer による LLM レビューのインプットを構造化する。

使い方:
  # stdin から diff を読み込み
  git diff HEAD~1 | python diff_analyzer.py

  # diff ファイルを指定
  python diff_analyzer.py --file changes.diff

  # JSON 形式で出力（他ツールとの連携用）
  git diff HEAD~1 | python diff_analyzer.py --json

  # レビュー用サマリーのみ表示
  git diff | python diff_analyzer.py --summary

終了コード:
  0 = 正常終了
  1 = diff の読み取りエラー
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ─── データ構造 ──────────────────────────────────────────────

@dataclass
class HunkChange:
    line_no: int       # 変更後ファイルでの行番号
    kind: str          # "added" | "removed" | "context"
    content: str       # 行の内容（先頭の +/-/ を除く）


@dataclass
class FileDiff:
    old_path: str
    new_path: str
    status: str                          # "modified" | "added" | "deleted" | "renamed"
    extension: str
    added_lines: int = 0
    removed_lines: int = 0
    hunks: list[list[HunkChange]] = field(default_factory=list)


@dataclass
class DiffSummary:
    total_files: int
    added_files: int
    deleted_files: int
    modified_files: int
    renamed_files: int
    total_added_lines: int
    total_removed_lines: int
    file_diffs: list[FileDiff]


# ─── パーサー ─────────────────────────────────────────────────

_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_OLD_FILE = re.compile(r"^--- (?:a/)?(.+)$")
_NEW_FILE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_RENAME_FROM = re.compile(r"^rename from (.+)$")
_RENAME_TO = re.compile(r"^rename to (.+)$")
_NEW_FILE_MODE = re.compile(r"^new file mode")
_DELETED_FILE_MODE = re.compile(r"^deleted file mode")


def _get_extension(path: str) -> str:
    return Path(path).suffix.lower() or "(no ext)"


def parse_diff(text: str) -> DiffSummary:
    """unified diff テキストをパースして DiffSummary を返す。"""
    file_diffs: list[FileDiff] = []
    current: FileDiff | None = None
    current_hunk: list[HunkChange] | None = None
    new_line_no = 0

    rename_from: str | None = None

    for raw_line in text.splitlines():
        # 新しいファイルの diff 開始
        m = _DIFF_HEADER.match(raw_line)
        if m:
            if current is not None:
                if current_hunk is not None:
                    current.hunks.append(current_hunk)
                file_diffs.append(current)
            old_p, new_p = m.group(1), m.group(2)
            current = FileDiff(
                old_path=old_p,
                new_path=new_p,
                status="modified",
                extension=_get_extension(new_p),
            )
            current_hunk = None
            rename_from = None
            continue

        if current is None:
            continue

        if _NEW_FILE_MODE.match(raw_line):
            current.status = "added"
            continue
        if _DELETED_FILE_MODE.match(raw_line):
            current.status = "deleted"
            continue
        m = _RENAME_FROM.match(raw_line)
        if m:
            rename_from = m.group(1)
            current.status = "renamed"
            continue
        m = _RENAME_TO.match(raw_line)
        if m:
            current.old_path = rename_from or current.old_path
            current.new_path = m.group(1)
            current.extension = _get_extension(current.new_path)
            continue

        # ハンクヘッダー
        m = _HUNK_HEADER.match(raw_line)
        if m:
            if current_hunk is not None:
                current.hunks.append(current_hunk)
            new_line_no = int(m.group(2))
            current_hunk = []
            continue

        if current_hunk is None:
            continue

        # 変更行の解析
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            current.added_lines += 1
            current_hunk.append(HunkChange(
                line_no=new_line_no,
                kind="added",
                content=raw_line[1:],
            ))
            new_line_no += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            current.removed_lines += 1
            current_hunk.append(HunkChange(
                line_no=new_line_no,
                kind="removed",
                content=raw_line[1:],
            ))
            # removed 行は new_line_no を進めない
        elif raw_line.startswith(" "):
            current_hunk.append(HunkChange(
                line_no=new_line_no,
                kind="context",
                content=raw_line[1:],
            ))
            new_line_no += 1

    # 最後のファイルを追加
    if current is not None:
        if current_hunk is not None:
            current.hunks.append(current_hunk)
        file_diffs.append(current)

    total_added = sum(f.added_lines for f in file_diffs)
    total_removed = sum(f.removed_lines for f in file_diffs)

    return DiffSummary(
        total_files=len(file_diffs),
        added_files=sum(1 for f in file_diffs if f.status == "added"),
        deleted_files=sum(1 for f in file_diffs if f.status == "deleted"),
        modified_files=sum(1 for f in file_diffs if f.status == "modified"),
        renamed_files=sum(1 for f in file_diffs if f.status == "renamed"),
        total_added_lines=total_added,
        total_removed_lines=total_removed,
        file_diffs=file_diffs,
    )


# ─── 出力 ─────────────────────────────────────────────────────

_STATUS_ICON = {"added": "🟢", "deleted": "🔴", "modified": "🔵", "renamed": "🟡"}


def print_summary(summary: DiffSummary) -> None:
    print("─" * 60)
    print("📋 Diff アナライザー サマリー")
    print("─" * 60)
    print(f"  変更ファイル数  : {summary.total_files}")
    print(f"  追加ファイル   : {summary.added_files}")
    print(f"  削除ファイル   : {summary.deleted_files}")
    print(f"  編集ファイル   : {summary.modified_files}")
    print(f"  リネーム       : {summary.renamed_files}")
    print(f"  追加行 (+)     : {summary.total_added_lines}")
    print(f"  削除行 (-)     : {summary.total_removed_lines}")
    print()

    if not summary.file_diffs:
        print("  ⚠️  変更ファイルがありません")
        return

    print("  ファイル一覧:")
    for fd in summary.file_diffs:
        icon = _STATUS_ICON.get(fd.status, "●")
        path = fd.new_path if fd.status != "deleted" else fd.old_path
        if fd.status == "renamed":
            path = f"{fd.old_path} → {fd.new_path}"
        print(f"    {icon} {path}  +{fd.added_lines}/-{fd.removed_lines}")

    print()
    # レビュー推奨事項
    large_files = [f for f in summary.file_diffs if (f.added_lines + f.removed_lines) > 200]
    if large_files:
        print("  ⚠️  レビュー注意: 変更規模が大きいファイル:")
        for f in large_files:
            print(f"    - {f.new_path}  ({f.added_lines + f.removed_lines} 行変更)")

    ext_counts: dict[str, int] = {}
    for f in summary.file_diffs:
        ext_counts[f.extension] = ext_counts.get(f.extension, 0) + 1
    if ext_counts:
        exts = ", ".join(f"{ext}({n})" for ext, n in sorted(ext_counts.items()))
        print(f"  ファイル種別   : {exts}")
    print("─" * 60)


def print_review_context(summary: DiffSummary) -> None:
    """LLM レビュー用の変更コンテキストを整形して表示する。"""
    print_summary(summary)
    print()
    for fd in summary.file_diffs:
        if fd.status == "deleted":
            print(f"### [削除] {fd.old_path}")
            print()
            continue
        path = fd.new_path
        print(f"### [{fd.status.upper()}] {path}  +{fd.added_lines}/-{fd.removed_lines}")
        for i, hunk in enumerate(fd.hunks):
            added = [c for c in hunk if c.kind == "added"]
            removed = [c for c in hunk if c.kind == "removed"]
            print(f"  Hunk {i + 1}: +{len(added)}/-{len(removed)}")
            for change in hunk:
                prefix = "+" if change.kind == "added" else ("-" if change.kind == "removed" else " ")
                print(f"  {prefix} {change.content}")
        print()


def print_json_output(summary: DiffSummary) -> None:
    def _fd_to_dict(fd: FileDiff) -> dict:
        d = asdict(fd)
        # hunks を行ベースの簡潔な形式に変換
        d["hunks"] = [
            [
                {"line": c["line_no"], "kind": c["kind"], "content": c["content"]}
                for c in hunk
            ]
            for hunk in d["hunks"]
        ]
        return d

    output = {
        "summary": {
            "total_files": summary.total_files,
            "added_files": summary.added_files,
            "deleted_files": summary.deleted_files,
            "modified_files": summary.modified_files,
            "renamed_files": summary.renamed_files,
            "total_added_lines": summary.total_added_lines,
            "total_removed_lines": summary.total_removed_lines,
        },
        "files": [_fd_to_dict(fd) for fd in summary.file_diffs],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


# ─── エントリポイント ──────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="git diff の解析・サマリー生成")
    parser.add_argument("--file", help="diff ファイルのパス（省略時は stdin）")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON形式で出力")
    parser.add_argument("--summary", action="store_true", help="サマリーのみ表示（詳細な diff 行を省略）")
    args = parser.parse_args()

    try:
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8", errors="replace")
        else:
            if sys.stdin.isatty():
                print("使い方: git diff | python diff_analyzer.py", file=sys.stderr)
                print("または: python diff_analyzer.py --file changes.diff", file=sys.stderr)
                return 1
            text = sys.stdin.read()
    except OSError as e:
        print(f"❌ 読み取りエラー: {e}", file=sys.stderr)
        return 1

    if not text.strip():
        print("⚠️  diff が空です（変更なし）")
        return 0

    summary = parse_diff(text)

    if args.as_json:
        print_json_output(summary)
    elif args.summary:
        print_summary(summary)
    else:
        print_review_context(summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
