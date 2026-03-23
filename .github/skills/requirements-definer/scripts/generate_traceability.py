#!/usr/bin/env python3
"""
generate_traceability.py - 受け入れ条件と実装コード/テストの双方向トレーサビリティマトリクス生成

requirements.json を読み込み、プロジェクトのソースコード・テストファイルを走査して
各受け入れ条件がどのファイル/テストでカバーされているかを追跡する。

使い方:
  # デフォルト（カレントディレクトリを走査）
  python generate_traceability.py

  # 対象ディレクトリを指定
  python generate_traceability.py --root path/to/project

  # requirements.json のパスを指定
  python generate_traceability.py --requirements path/to/requirements.json

  # requirements.json に traceability_matrix フィールドを埋め込む
  python generate_traceability.py --embed

  # Markdown のみ出力（requirements.json は更新しない）
  python generate_traceability.py --markdown-only

出力:
  traceability-matrix.md  — 人間が読みやすいマトリクス
  requirements.json       — traceability_matrix フィールドを追記（--embed 時のみ）

終了コード:
  0 = 正常完了
  1 = エラーあり
  2 = requirements.json が見つからない / パースエラー
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple


# ─── 定数 ────────────────────────────────────────────────────

# 走査対象の拡張子
SOURCE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".rb", ".go", ".java", ".kt", ".swift",
    ".cs", ".rs", ".php", ".vue", ".svelte",
}

# テストファイルと判定するパターン
TEST_FILE_PATTERNS = [
    re.compile(r"\.test\.[^.]+$"),
    re.compile(r"\.spec\.[^.]+$"),
    re.compile(r"_test\.[^.]+$"),
    re.compile(r"test_[^/\\]+\.[^.]+$"),
    re.compile(r"[/\\]tests?[/\\]"),
    re.compile(r"[/\\]__tests__[/\\]"),
    re.compile(r"[/\\]spec[/\\]"),
]

# 無視するディレクトリ
IGNORE_DIRS = {
    ".git", "node_modules", ".next", ".nuxt", "dist", "build",
    "__pycache__", ".venv", "venv", "env", ".env",
    "vendor", "coverage", ".coverage", ".pytest_cache",
    ".mypy_cache", ".tox", "target",
}

# 要件IDにマッチするパターン（コード内のコメントや文字列を探す）
REQ_ID_PATTERN = re.compile(
    r"\b(F-\d{2,}|N-\d{2,})\b",
    re.IGNORECASE,
)

# テスト関数/メソッド名を抽出するパターン
TEST_FUNC_PATTERNS = [
    # JavaScript/TypeScript: it("...") / test("...") / describe("...")
    re.compile(
        r"""(?:it|test|describe)\s*\(\s*['"`]([^'"`]+)['"`]""",
        re.MULTILINE,
    ),
    # Python: def test_xxx
    re.compile(
        r"""def\s+(test_\w+)\s*\(""",
        re.MULTILINE,
    ),
    # pytest / unittest: class TestXxx
    re.compile(
        r"""class\s+(Test\w+)\s*[:(]""",
        re.MULTILINE,
    ),
    # Go: func TestXxx(t *testing.T)
    re.compile(
        r"""func\s+(Test\w+)\s*\(""",
        re.MULTILINE,
    ),
    # Java/Kotlin: @Test メソッド
    re.compile(
        r"""@Test\s+(?:fun|public\s+void)\s+(\w+)\s*\(""",
        re.MULTILINE,
    ),
]


# ─── データクラス ─────────────────────────────────────────────

class FileRef(NamedTuple):
    file: str
    note: str = ""


class TestRef(NamedTuple):
    file: str
    test_name: str


class ACTrace(NamedTuple):
    index: int
    summary: str
    implementation: list[FileRef]
    tests: list[TestRef]
    status: str  # "covered" | "partial" | "not_covered"


class RequirementTrace(NamedTuple):
    requirement_id: str
    requirement_name: str
    acceptance_criteria: list[ACTrace]


# ─── ファイル走査 ─────────────────────────────────────────────

def is_test_file(path: Path) -> bool:
    path_str = str(path)
    return any(p.search(path_str) for p in TEST_FILE_PATTERNS)


def collect_files(root: Path) -> tuple[list[Path], list[Path]]:
    """ソースファイルとテストファイルを分けて収集する。"""
    source_files: list[Path] = []
    test_files: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # 無視するディレクトリをスキップ
        parts = set(path.parts)
        if parts & IGNORE_DIRS:
            continue
        if path.suffix not in SOURCE_EXTENSIONS:
            continue
        if is_test_file(path):
            test_files.append(path)
        else:
            source_files.append(path)

    return sorted(source_files), sorted(test_files)


def extract_req_ids(text: str) -> set[str]:
    """テキストから要件IDを抽出する（大文字に正規化）。"""
    return {m.upper() for m in REQ_ID_PATTERN.findall(text)}


def extract_test_names(text: str) -> list[str]:
    """テストファイルからテスト関数名/説明を抽出する。"""
    names: list[str] = []
    for pattern in TEST_FUNC_PATTERNS:
        names.extend(pattern.findall(text))
    return names


def read_file_safe(path: Path) -> str:
    """ファイルを安全に読み込む。読み込めない場合は空文字を返す。"""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, PermissionError):
            continue
    return ""


# ─── マッチング ───────────────────────────────────────────────

def build_req_to_files_map(
    files: list[Path],
    root: Path,
) -> dict[str, list[Path]]:
    """要件ID → ファイル一覧のマップを構築する。"""
    mapping: dict[str, list[Path]] = {}
    for path in files:
        text = read_file_safe(path)
        if not text:
            continue
        for req_id in extract_req_ids(text):
            mapping.setdefault(req_id, []).append(path)
    return mapping


def build_req_to_tests_map(
    test_files: list[Path],
    root: Path,
) -> dict[str, list[tuple[Path, str]]]:
    """要件ID → (テストファイル, テスト名) 一覧のマップを構築する。"""
    mapping: dict[str, list[tuple[Path, str]]] = {}
    for path in test_files:
        text = read_file_safe(path)
        if not text:
            continue
        req_ids = extract_req_ids(text)
        test_names = extract_test_names(text)
        for req_id in req_ids:
            for name in test_names:
                mapping.setdefault(req_id, []).append((path, name))
            if not test_names:
                # テスト名が取れなくてもファイル名だけ記録
                mapping.setdefault(req_id, []).append((path, ""))
    return mapping


def keyword_search(
    text: str, keywords: list[str], threshold: int = 2
) -> bool:
    """キーワードが threshold 個以上含まれていれば True を返す。"""
    if not keywords:
        return False
    text_lower = text.lower()
    matched = sum(1 for kw in keywords if kw.lower() in text_lower)
    return matched >= threshold


def search_by_keywords(
    files: list[Path],
    keywords: list[str],
) -> list[Path]:
    """キーワードマッチでファイルを絞り込む。"""
    results: list[Path] = []
    for path in files:
        text = read_file_safe(path)
        if keyword_search(text, keywords):
            results.append(path)
    return results


def search_tests_by_keywords(
    test_files: list[Path],
    keywords: list[str],
) -> list[tuple[Path, str]]:
    """キーワードマッチでテストファイルとテスト名を取得する。"""
    results: list[tuple[Path, str]] = []
    for path in test_files:
        text = read_file_safe(path)
        if not keyword_search(text, keywords):
            continue
        test_names = extract_test_names(text)
        # キーワードを含むテスト名だけに絞り込む
        matched_names = [
            n for n in test_names
            if any(kw.lower() in n.lower() for kw in keywords)
        ]
        if matched_names:
            for name in matched_names:
                results.append((path, name))
        else:
            results.append((path, ""))
    return results


# ─── トレーサビリティ構築 ─────────────────────────────────────

def make_ac_summary(ac: dict) -> str:
    """受け入れ条件から短いサマリーを生成する。"""
    given = ac.get("given", "")
    when = ac.get("when", "")
    then = ac.get("then", "")
    # then を主体にした簡潔なサマリー
    summary = then if then else f"{when} → {given}"
    return summary[:100] + ("..." if len(summary) > 100 else "")


def extract_keywords_from_ac(req: dict, ac: dict) -> list[str]:
    """要件名と受け入れ条件からキーワードを抽出する。"""
    keywords: list[str] = []
    # 要件IDと名前
    req_id = req.get("id", "")
    req_name = req.get("name", "")
    # AC のテキスト
    given = ac.get("given", "")
    when = ac.get("when", "")
    then = ac.get("then", "")

    # 単語に分割（日本語は分割困難なのでそのまま）
    for text in (req_id, req_name):
        if text:
            keywords.append(text)
    # AC 全体を1キーワードとして追加（部分一致）
    for text in (given, when, then):
        words = text.split()
        keywords.extend(w for w in words if len(w) >= 3)

    return list(dict.fromkeys(keywords))  # 重複除去・順序保持


def build_traceability(
    requirements: dict,
    source_files: list[Path],
    test_files: list[Path],
    root: Path,
) -> list[RequirementTrace]:
    """requirements.json からトレーサビリティマトリクスを構築する。"""
    # 要件ID によるインデックス構築
    req_id_src_map = build_req_to_files_map(source_files, root)
    req_id_test_map = build_req_to_tests_map(test_files, root)

    traces: list[RequirementTrace] = []

    for req in requirements.get("functional_requirements", []):
        req_id = req.get("id", "")
        req_name = req.get("name", "")
        ac_traces: list[ACTrace] = []

        criteria = req.get("acceptance_criteria", [])
        for i, ac in enumerate(criteria):
            summary = make_ac_summary(ac)
            keywords = extract_keywords_from_ac(req, ac)

            # 1. 要件IDでの直接マッチ（コードに F-01 等が書かれている場合）
            impl_by_id = req_id_src_map.get(req_id, [])
            test_by_id = req_id_test_map.get(req_id, [])

            # 2. キーワードマッチ（IDが書かれていない場合のフォールバック）
            impl_by_kw: list[Path] = []
            test_by_kw: list[tuple[Path, str]] = []
            if not impl_by_id:
                impl_by_kw = search_by_keywords(source_files, keywords)
            if not test_by_id:
                test_by_kw = search_tests_by_keywords(test_files, keywords)

            # 結合（IDマッチ優先）
            impl_paths = impl_by_id or impl_by_kw
            test_pairs = test_by_id or test_by_kw

            impl_refs = [
                FileRef(
                    file=str(p.relative_to(root)),
                    note="ID参照" if p in impl_by_id else "キーワードマッチ",
                )
                for p in impl_paths
            ]
            test_refs = [
                TestRef(
                    file=str(p.relative_to(root)),
                    test_name=name,
                )
                for p, name in test_pairs
            ]

            # カバレッジステータス判定
            if test_refs and impl_refs:
                status = "covered"
            elif test_refs or impl_refs:
                status = "partial"
            else:
                status = "not_covered"

            ac_traces.append(ACTrace(
                index=i,
                summary=summary,
                implementation=impl_refs,
                tests=test_refs,
                status=status,
            ))

        traces.append(RequirementTrace(
            requirement_id=req_id,
            requirement_name=req_name,
            acceptance_criteria=ac_traces,
        ))

    return traces


# ─── 出力フォーマット ─────────────────────────────────────────

STATUS_EMOJI = {
    "covered": "✅",
    "partial": "⚠️",
    "not_covered": "❌",
}

STATUS_LABEL = {
    "covered": "カバー済み",
    "partial": "一部カバー",
    "not_covered": "未カバー",
}


def render_markdown(
    traces: list[RequirementTrace],
    requirements: dict,
) -> str:
    """トレーサビリティマトリクスを Markdown で出力する。"""
    lines: list[str] = []
    goal = requirements.get("goal", "")

    lines.append("# トレーサビリティマトリクス")
    lines.append("")
    if goal:
        lines.append(f"> **プロジェクト目標**: {goal}")
        lines.append("")

    # サマリーテーブル
    lines.append("## カバレッジサマリー")
    lines.append("")
    lines.append("| 要件ID | 要件名 | AC数 | ✅ 済 | ⚠️ 一部 | ❌ 未 |")
    lines.append("|--------|--------|------|-------|---------|-------|")
    for req_trace in traces:
        total = len(req_trace.acceptance_criteria)
        covered = sum(1 for ac in req_trace.acceptance_criteria if ac.status == "covered")
        partial = sum(1 for ac in req_trace.acceptance_criteria if ac.status == "partial")
        not_covered = sum(1 for ac in req_trace.acceptance_criteria if ac.status == "not_covered")
        lines.append(
            f"| {req_trace.requirement_id} | {req_trace.requirement_name} "
            f"| {total} | {covered} | {partial} | {not_covered} |"
        )
    lines.append("")

    # 詳細テーブル（要件ごと）
    lines.append("## 詳細マトリクス")
    lines.append("")

    for req_trace in traces:
        lines.append(f"### {req_trace.requirement_id}: {req_trace.requirement_name}")
        lines.append("")
        lines.append("| # | 受け入れ条件 | 実装ファイル | テスト | ステータス |")
        lines.append("|---|------------|-------------|--------|-----------|")

        for ac in req_trace.acceptance_criteria:
            ac_num = ac.index + 1
            summary_escaped = ac.summary.replace("|", "\\|")

            # 実装ファイル
            if ac.implementation:
                impl_text = "<br>".join(
                    f"`{ref.file}`" + (f" ({ref.note})" if ref.note else "")
                    for ref in ac.implementation[:3]  # 最大3件
                )
                if len(ac.implementation) > 3:
                    impl_text += f"<br>...他 {len(ac.implementation) - 3} 件"
            else:
                impl_text = "—"

            # テスト
            if ac.tests:
                test_parts: list[str] = []
                for ref in ac.tests[:3]:  # 最大3件
                    if ref.test_name:
                        test_parts.append(f"`{ref.file}`<br>└ {ref.test_name}")
                    else:
                        test_parts.append(f"`{ref.file}`")
                test_text = "<br>".join(test_parts)
                if len(ac.tests) > 3:
                    test_text += f"<br>...他 {len(ac.tests) - 3} 件"
            else:
                test_text = "—"

            status_icon = STATUS_EMOJI.get(ac.status, "?")
            status_label = STATUS_LABEL.get(ac.status, ac.status)

            lines.append(
                f"| {ac_num} | {summary_escaped} | {impl_text} | {test_text} "
                f"| {status_icon} {status_label} |"
            )

        lines.append("")

    # 逆引き: コード → 要件
    lines.append("## 逆引きマトリクス（コード → 要件）")
    lines.append("")
    lines.append("実装ファイルとテストファイルがどの要件をカバーしているかを示す。")
    lines.append("")

    # ファイルごとに要件IDを収集
    file_to_reqs: dict[str, list[str]] = {}
    for req_trace in traces:
        for ac in req_trace.acceptance_criteria:
            req_label = f"{req_trace.requirement_id} AC-{ac.index + 1}"
            for ref in ac.implementation:
                file_to_reqs.setdefault(ref.file, []).append(req_label)
            for ref in ac.tests:
                file_to_reqs.setdefault(ref.file, []).append(req_label)

    if file_to_reqs:
        lines.append("| ファイル | カバーする要件・AC |")
        lines.append("|---------|------------------|")
        for file_path, req_labels in sorted(file_to_reqs.items()):
            unique_labels = list(dict.fromkeys(req_labels))
            labels_text = ", ".join(unique_labels[:5])
            if len(unique_labels) > 5:
                labels_text += f", ...他 {len(unique_labels) - 5} 件"
            lines.append(f"| `{file_path}` | {labels_text} |")
    else:
        lines.append("*コード内に要件IDの参照が見つかりませんでした。*")
        lines.append("")
        lines.append("コードに要件IDを記載することで自動追跡が可能になります。例:")
        lines.append("")
        lines.append("```typescript")
        lines.append("// F-01: TODO作成")
        lines.append("export function createTodo(title: string, deadline: Date) {")
        lines.append("  ...")
        lines.append("}")
        lines.append("```")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*このファイルは `generate_traceability.py` によって自動生成されました。*"
    )

    return "\n".join(lines)


def traces_to_json(traces: list[RequirementTrace]) -> list[dict]:
    """トレーサビリティデータを JSON シリアライズ可能な形式に変換する。"""
    result = []
    for req_trace in traces:
        ac_list = []
        for ac in req_trace.acceptance_criteria:
            ac_list.append({
                "index": ac.index,
                "summary": ac.summary,
                "implementation": [
                    {"file": ref.file, "note": ref.note}
                    for ref in ac.implementation
                ],
                "tests": [
                    {"file": ref.file, "test_name": ref.test_name}
                    for ref in ac.tests
                ],
                "status": ac.status,
            })
        result.append({
            "requirement_id": req_trace.requirement_id,
            "requirement_name": req_trace.requirement_name,
            "acceptance_criteria": ac_list,
        })
    return result


# ─── エントリポイント ──────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="受け入れ条件と実装コード/テストの双方向トレーサビリティマトリクス生成"
    )
    parser.add_argument(
        "--root",
        default=".",
        help="走査するプロジェクトルートディレクトリ（デフォルト: カレントディレクトリ）",
    )
    parser.add_argument(
        "--requirements",
        default="requirements.json",
        help="requirements.json のパス（デフォルト: requirements.json）",
    )
    parser.add_argument(
        "--output",
        default="traceability-matrix.md",
        help="出力する Markdown ファイルのパス（デフォルト: traceability-matrix.md）",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="requirements.json に traceability_matrix フィールドを埋め込む",
    )
    parser.add_argument(
        "--markdown-only",
        action="store_true",
        help="Markdown のみ出力（requirements.json は更新しない）",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    req_path = Path(args.requirements)
    if not req_path.is_absolute():
        req_path = root / req_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = root / output_path

    # requirements.json を読み込む
    if not req_path.exists():
        print(f"❌ requirements.json が見つかりません: {req_path}", file=sys.stderr)
        return 2
    try:
        with req_path.open(encoding="utf-8") as f:
            requirements = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ JSON パースエラー: {e}", file=sys.stderr)
        return 2

    func_reqs = requirements.get("functional_requirements", [])
    if not func_reqs:
        print("⚠️  functional_requirements が空です。トレーサビリティを生成するには要件が必要です。")
        return 1

    # ソース/テストファイルを収集
    print(f"📂 プロジェクトを走査中: {root}")
    source_files, test_files = collect_files(root)
    print(f"   ソースファイル: {len(source_files)} 件")
    print(f"   テストファイル: {len(test_files)} 件")

    if not source_files and not test_files:
        print("⚠️  ソースファイルが見つかりませんでした。")
        print("   --root でプロジェクトルートを指定してください。")

    # トレーサビリティを構築
    print("🔍 トレーサビリティを分析中...")
    traces = build_traceability(requirements, source_files, test_files, root)

    # サマリー表示
    total_ac = sum(len(t.acceptance_criteria) for t in traces)
    covered = sum(
        1 for t in traces
        for ac in t.acceptance_criteria
        if ac.status == "covered"
    )
    partial = sum(
        1 for t in traces
        for ac in t.acceptance_criteria
        if ac.status == "partial"
    )
    not_covered = total_ac - covered - partial
    print(f"\n📊 カバレッジサマリー:")
    print(f"   総AC数: {total_ac}")
    print(f"   ✅ カバー済み: {covered}")
    print(f"   ⚠️  一部カバー: {partial}")
    print(f"   ❌ 未カバー:   {not_covered}")

    # Markdown 出力
    markdown = render_markdown(traces, requirements)
    output_path.write_text(markdown, encoding="utf-8")
    print(f"\n✅ {output_path} を出力しました")

    # requirements.json への埋め込み
    if args.embed and not args.markdown_only:
        requirements["traceability_matrix"] = traces_to_json(traces)
        with req_path.open("w", encoding="utf-8") as f:
            json.dump(requirements, f, ensure_ascii=False, indent=2)
        print(f"✅ {req_path} に traceability_matrix を埋め込みました")

    return 0


if __name__ == "__main__":
    sys.exit(main())
