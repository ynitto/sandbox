#!/usr/bin/env python3
"""
check.py - self-checking スキルの自動チェックスクリプト

成果物の種別を自動検出し、定量チェックを実施して JSON 形式で結果を返す。

使い方:
  # 種別を自動検出する
  python check.py --detect path/to/file.py

  # コードをチェックする
  python check.py --type code --files src/foo.py src/bar.py

  # 調査レポートをチェックする
  python check.py --type research --files report.md

  # ドキュメントをチェックする（完了基準付き）
  python check.py --type document --files README.md --criteria "APIの使い方が説明されていること"

終了コード:
  0 = 全チェック合格
  1 = 一部チェック失敗
  2 = 実行エラー（ファイル不在・パースエラー等）
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 種別検出
# ---------------------------------------------------------------------------

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb", ".rs",
    ".c", ".cpp", ".cs", ".swift", ".kt", ".php", ".sh", ".bash",
}
DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}
RESEARCH_KEYWORDS = {
    "調査", "分析", "比較", "検討", "調べた", "まとめ",
    "research", "analysis", "comparison", "investigation", "survey",
}


def detect_artifact_type(path: str) -> dict:
    """ファイルの拡張子・内容から artifact_type を推定する。"""
    p = Path(path)
    ext = p.suffix.lower()

    if ext in CODE_EXTENSIONS:
        return {"artifact_type": "code", "confidence": 0.95}

    if ext in DOC_EXTENSIONS:
        try:
            content = p.read_text(encoding="utf-8", errors="ignore").lower()
            keyword_hits = sum(1 for kw in RESEARCH_KEYWORDS if kw in content)
            if keyword_hits >= 2:
                return {"artifact_type": "research", "confidence": 0.75}
        except OSError:
            pass
        return {"artifact_type": "document", "confidence": 0.80}

    return {"artifact_type": "document", "confidence": 0.40}


# ---------------------------------------------------------------------------
# チェック関数
# ---------------------------------------------------------------------------

def check_code(files: list[str], criteria: str) -> dict:
    """コード成果物の自動チェック。"""
    checks: dict[str, dict] = {}
    total_lines = 0
    has_test_file = False
    syntax_errors = []

    for f in files:
        p = Path(f)
        if not p.exists():
            return _error_result(f"ファイルが見当たらない: {f}")

        content = p.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        total_lines += len(lines)

        # テストファイル判定
        name = p.name.lower()
        if (
            "test" in name
            or "spec" in name
            or name.startswith("test_")
            or name.endswith("_test.py")
        ):
            has_test_file = True

        # Python 構文チェック
        if p.suffix == ".py":
            try:
                ast.parse(content)
            except SyntaxError as e:
                syntax_errors.append(f"{f}: {e}")

    # 構文チェック結果
    checks["syntax"] = {
        "passed": len(syntax_errors) == 0,
        "details": "; ".join(syntax_errors) if syntax_errors else "",
    }

    # 空ファイルチェック
    checks["non_empty"] = {
        "passed": total_lines > 0,
        "details": "" if total_lines > 0 else "全ファイルが空",
    }

    # テストファイル存在チェック（コードファイルが1件以上の場合のみ）
    non_test_files = [
        f for f in files
        if not any(k in Path(f).name.lower() for k in ("test", "spec"))
    ]
    if non_test_files:
        checks["test_presence"] = {
            "passed": has_test_file,
            "details": "" if has_test_file else "テストファイルが見当たらない",
        }

    # TODO / FIXME 残存チェック
    todo_count = 0
    for f in files:
        p = Path(f)
        if p.exists():
            content = p.read_text(encoding="utf-8", errors="ignore")
            todo_count += len(re.findall(r"\b(TODO|FIXME|HACK|XXX)\b", content))
    checks["no_todo"] = {
        "passed": todo_count == 0,
        "details": f"TODO/FIXME が {todo_count} 件残っている" if todo_count else "",
    }

    # 完了基準のキーワード一致チェック（簡易）
    if criteria:
        checks["criteria_hint"] = {
            "passed": True,  # LLM 評価に委ねるためデフォルトは PASS
            "details": f"完了基準はルーブリック評価で確認すること: {criteria}",
        }

    passed = [k for k, v in checks.items() if v["passed"]]
    failed = [k for k, v in checks.items() if not v["passed"]]
    auto_score = len(passed) / len(checks) if checks else 1.0

    return {
        "artifact_type": "code",
        "checks": checks,
        "auto_score": round(auto_score, 2),
        "failed_checks": failed,
    }


def check_research(files: list[str], criteria: str) -> dict:
    """調査レポートの自動チェック。"""
    checks: dict[str, dict] = {}

    all_content = ""
    for f in files:
        p = Path(f)
        if not p.exists():
            return _error_result(f"ファイルが見当たらない: {f}")
        all_content += p.read_text(encoding="utf-8", errors="ignore")

    word_count = len(all_content.split())

    # 最低文字数チェック（調査レポートとして意味のある量）
    checks["min_length"] = {
        "passed": word_count >= 200,
        "details": "" if word_count >= 200 else f"本文が短すぎる（{word_count} 語）",
    }

    # 見出し構造チェック
    headings = re.findall(r"^#{1,3}\s+.+", all_content, re.MULTILINE)
    checks["has_structure"] = {
        "passed": len(headings) >= 2,
        "details": "" if len(headings) >= 2 else "見出し（#）が 2 つ以上ない",
    }

    # 結論・まとめセクションの存在チェック
    conclusion_pattern = re.compile(
        r"(まとめ|結論|conclusion|summary|所感|考察|recommendations?)",
        re.IGNORECASE,
    )
    checks["has_conclusion"] = {
        "passed": bool(conclusion_pattern.search(all_content)),
        "details": "" if conclusion_pattern.search(all_content)
                   else "まとめ・結論セクションが見当たらない",
    }

    # 根拠・参照の存在チェック（URL、引用、出典）
    evidence_pattern = re.compile(r"(https?://|参考|出典|引用|参照|ref\.|source)", re.IGNORECASE)
    checks["has_evidence"] = {
        "passed": bool(evidence_pattern.search(all_content)),
        "details": "" if evidence_pattern.search(all_content)
                   else "根拠・参照リンクが見当たらない",
    }

    if criteria:
        checks["criteria_hint"] = {
            "passed": True,
            "details": f"完了基準はルーブリック評価で確認すること: {criteria}",
        }

    passed = [k for k, v in checks.items() if v["passed"]]
    failed = [k for k, v in checks.items() if not v["passed"]]
    auto_score = len(passed) / len(checks) if checks else 1.0

    return {
        "artifact_type": "research",
        "checks": checks,
        "auto_score": round(auto_score, 2),
        "failed_checks": failed,
    }


def check_document(files: list[str], criteria: str) -> dict:
    """ドキュメント成果物の自動チェック。"""
    checks: dict[str, dict] = {}

    all_content = ""
    for f in files:
        p = Path(f)
        if not p.exists():
            return _error_result(f"ファイルが見当たらない: {f}")
        all_content += p.read_text(encoding="utf-8", errors="ignore")

    word_count = len(all_content.split())

    # 最低文字数チェック
    checks["min_length"] = {
        "passed": word_count >= 50,
        "details": "" if word_count >= 50 else f"ドキュメントが短すぎる（{word_count} 語）",
    }

    # 見出し構造チェック
    headings = re.findall(r"^#{1,4}\s+.+", all_content, re.MULTILINE)
    checks["has_structure"] = {
        "passed": len(headings) >= 1,
        "details": "" if headings else "見出し（#）が見当たらない",
    }

    # プレースホルダ残存チェック
    placeholder_pattern = re.compile(r"\[TODO\]|\[WIP\]|<TBD>|PLACEHOLDER", re.IGNORECASE)
    placeholder_count = len(placeholder_pattern.findall(all_content))
    checks["no_placeholder"] = {
        "passed": placeholder_count == 0,
        "details": f"プレースホルダが {placeholder_count} 件残っている" if placeholder_count else "",
    }

    # コードブロックの閉じチェック（Markdown）
    backtick_blocks = all_content.count("```")
    checks["code_blocks_closed"] = {
        "passed": backtick_blocks % 2 == 0,
        "details": "" if backtick_blocks % 2 == 0 else "コードブロック（```）が閉じられていない",
    }

    if criteria:
        checks["criteria_hint"] = {
            "passed": True,
            "details": f"完了基準はルーブリック評価で確認すること: {criteria}",
        }

    passed = [k for k, v in checks.items() if v["passed"]]
    failed = [k for k, v in checks.items() if not v["passed"]]
    auto_score = len(passed) / len(checks) if checks else 1.0

    return {
        "artifact_type": "document",
        "checks": checks,
        "auto_score": round(auto_score, 2),
        "failed_checks": failed,
    }


def _error_result(message: str) -> dict:
    return {
        "error": message,
        "artifact_type": "unknown",
        "checks": {},
        "auto_score": 0.0,
        "failed_checks": [],
    }


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="self-checking スキルの自動チェックスクリプト"
    )
    subparsers = parser.add_subparsers(dest="command")

    # --detect モード
    parser.add_argument("--detect", metavar="FILE", help="artifact_type を自動検出する")

    # チェックモード
    parser.add_argument(
        "--type",
        choices=["code", "research", "document"],
        help="成果物の種別",
    )
    parser.add_argument("--files", nargs="+", metavar="FILE", help="チェック対象ファイル")
    parser.add_argument("--criteria", default="", help="完了基準（任意）")

    args = parser.parse_args()

    # --detect モード
    if args.detect:
        result = detect_artifact_type(args.detect)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # チェックモード
    if not args.type or not args.files:
        parser.print_help()
        return 2

    artifact_type = args.type
    files = args.files
    criteria = args.criteria or ""

    try:
        if artifact_type == "code":
            result = check_code(files, criteria)
        elif artifact_type == "research":
            result = check_research(files, criteria)
        else:
            result = check_document(files, criteria)
    except Exception as e:
        result = _error_result(str(e))

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if "error" in result:
        return 2
    return 0 if not result.get("failed_checks") else 1


if __name__ == "__main__":
    sys.exit(main())
