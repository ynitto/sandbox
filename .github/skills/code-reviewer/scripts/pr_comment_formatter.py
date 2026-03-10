#!/usr/bin/env python3
"""
pr_comment_formatter.py - レビュー結果を GitHub PR コメント形式にフォーマット

code-reviewer によるレビュー結果（JSON）を受け取り、GitHub PR コメントとして
貼り付けられる Markdown を生成する。外部依存ゼロ。

入力 JSON スキーマ:
  {
    "verdict": "LGTM" | "REQUEST_CHANGES",
    "coding_rules": [                       // オプション
      {"rule": "...", "status": "ok"|"violation", "location": "...", "detail": "...", "suggestion": "..."}
    ],
    "findings": [
      {
        "severity": "Critical" | "Warning" | "Suggestion",
        "confidence": "High" | "Medium",
        "category": "...",
        "summary": "...",
        "location": "...",       // "src/app.ts:42"
        "problem": "...",
        "suggestion": "...",
        "code_example": "..."    // オプション: 修正後コード例
      }
    ],
    "summary": {
      "critical": 0,
      "warning": 0,
      "suggestion": 0,
      "rationale": "...",
      "overall": "..."
    }
  }

使い方:
  # JSON ファイルを指定
  python pr_comment_formatter.py --file review_result.json

  # stdin から JSON を読み込み
  echo '{"verdict": "LGTM", ...}' | python pr_comment_formatter.py

  # クリップボードにコピー（macOS）
  python pr_comment_formatter.py --file review_result.json | pbcopy

  # Markdown ファイルに出力
  python pr_comment_formatter.py --file review_result.json --out pr_review.md

終了コード:
  0 = 正常終了
  1 = 入力 JSON のパースエラー / 必須フィールド不足
  2 = ファイル読み取りエラー
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


# ─── フォーマット定数 ─────────────────────────────────────────

_VERDICT_BADGE = {
    "LGTM": "## ✅ LGTM",
    "REQUEST_CHANGES": "## ❌ Request Changes",
}

_SEVERITY_ICON = {
    "critical": "🔴",
    "warning": "🟡",
    "suggestion": "🔵",
}

_CONFIDENCE_LABEL = {
    "high": "High",
    "medium": "Medium",
}


# ─── バリデーション ───────────────────────────────────────────

def validate_input(data: dict) -> list[str]:
    errors = []
    if "verdict" not in data:
        errors.append("'verdict' フィールドが必須です（LGTM | REQUEST_CHANGES）")
    elif data["verdict"] not in ("LGTM", "REQUEST_CHANGES"):
        errors.append(f"'verdict' は LGTM または REQUEST_CHANGES でなければなりません: {data['verdict']}")
    if "findings" not in data:
        errors.append("'findings' フィールドが必須です（空配列でも可）")
    if "summary" not in data:
        errors.append("'summary' フィールドが必須です")
    return errors


# ─── Markdown 生成 ────────────────────────────────────────────

def _code_block(code: str, lang: str = "") -> str:
    """コードブロックを生成する。"""
    return f"\n```{lang}\n{code.strip()}\n```\n"


def _escape_md(text: str) -> str:
    """Markdown の特殊文字をエスケープする（テーブルセル用）。"""
    return text.replace("|", "&#124;").replace("\n", " ")


def _format_coding_rules(rules: list[dict]) -> str:
    if not rules:
        return ""
    lines = ["### コーディングルールへの準拠\n"]
    for rule in rules:
        status = rule.get("status", "")
        icon = "✅" if status == "ok" else "❌"
        rule_name = rule.get("rule", "(無名)")
        lines.append(f"- {icon} **{rule_name}**")
        if status == "violation":
            location = rule.get("location", "")
            detail = rule.get("detail", "")
            suggestion = rule.get("suggestion", "")
            if location:
                lines.append(f"  - 違反箇所: `{location}`")
            if detail:
                lines.append(f"  - 問題: {detail}")
            if suggestion:
                lines.append(f"  - 改善案: {suggestion}")
    lines.append("")
    return "\n".join(lines)


def _format_findings_group(severity: str, findings: list[dict]) -> str:
    if not findings:
        return ""
    icon = _SEVERITY_ICON.get(severity.lower(), "●")
    label = severity.capitalize()
    lines = [f"#### {icon} {label} ({len(findings)} 件)\n"]

    for item in findings:
        confidence = item.get("confidence", "High")
        conf_label = _CONFIDENCE_LABEL.get(confidence.lower(), confidence)
        category = item.get("category", "")
        summary = item.get("summary", "")
        location = item.get("location", "")
        problem = item.get("problem", "")
        suggestion = item.get("suggestion", "")
        code_example = item.get("code_example", "")

        title_parts = []
        if category:
            title_parts.append(f"[{category}]")
        title_parts.append(summary)
        title = " ".join(title_parts)

        lines.append(f"**{title}** `[信頼度: {conf_label}]`")
        if location:
            lines.append(f"- 場所: `{location}`")
        if problem:
            lines.append(f"- 問題: {problem}")
        if suggestion:
            lines.append(f"- 改善案: {suggestion}")
        if code_example:
            lines.append(_code_block(code_example))
        lines.append("")

    return "\n".join(lines)


def format_pr_comment(data: dict) -> str:
    """レビュー結果 dict を GitHub PR コメント Markdown に変換する。"""
    verdict = data.get("verdict", "LGTM")
    verdict_header = _VERDICT_BADGE.get(verdict, f"## {verdict}")

    findings_raw = data.get("findings", [])
    summary_info = data.get("summary", {})
    coding_rules = data.get("coding_rules", [])

    # severity でグループ分け（大文字小文字を正規化）
    grouped: dict[str, list[dict]] = {"critical": [], "warning": [], "suggestion": []}
    for item in findings_raw:
        sev = item.get("severity", "suggestion").lower()
        grouped.setdefault(sev, []).append(item)

    parts: list[str] = []

    # ── ヘッダー ──
    parts.append(verdict_header)
    parts.append("")

    # ── コーディングルール ──
    rules_section = _format_coding_rules(coding_rules)
    if rules_section:
        parts.append(rules_section)

    # ── 指摘事項 ──
    has_findings = any(grouped.values())
    if has_findings:
        parts.append("### 指摘事項\n")
        for sev in ("critical", "warning", "suggestion"):
            section = _format_findings_group(sev, grouped[sev])
            if section:
                parts.append(section)

    # ── サマリー ──
    c = summary_info.get("critical", len(grouped["critical"]))
    w = summary_info.get("warning", len(grouped["warning"]))
    s = summary_info.get("suggestion", len(grouped["suggestion"]))
    rationale = summary_info.get("rationale", "")
    overall = summary_info.get("overall", "")

    parts.append("### サマリー\n")
    parts.append(
        f"| 重要度 | 件数 |\n"
        f"|--------|------|\n"
        f"| 🔴 Critical | {c} |\n"
        f"| 🟡 Warning | {w} |\n"
        f"| 🔵 Suggestion | {s} |\n"
    )
    if rationale:
        parts.append(f"**判定根拠**: {rationale}\n")
    if overall:
        parts.append(f"> {overall}\n")

    # ── フッター ──
    parts.append(
        f"<sub>Generated by code-reviewer skill — {datetime.now().strftime('%Y-%m-%d %H:%M')}</sub>"
    )

    return "\n".join(parts)


# ─── エントリポイント ──────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="レビュー結果 JSON を GitHub PR コメント Markdown に変換する"
    )
    parser.add_argument("--file", help="入力 JSON ファイルパス（省略時は stdin）")
    parser.add_argument("--out", help="出力 Markdown ファイルパス（省略時は stdout）")
    args = parser.parse_args()

    # 入力読み込み
    try:
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        else:
            if sys.stdin.isatty():
                print("使い方: python pr_comment_formatter.py --file review_result.json", file=sys.stderr)
                print("または: echo '{...}' | python pr_comment_formatter.py", file=sys.stderr)
                return 1
            text = sys.stdin.read()
    except OSError as e:
        print(f"❌ ファイル読み取りエラー: {e}", file=sys.stderr)
        return 2

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"❌ JSON パースエラー: {e}", file=sys.stderr)
        return 1

    errors = validate_input(data)
    if errors:
        print("❌ 入力 JSON のバリデーションエラー:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    markdown = format_pr_comment(data)

    if args.out:
        try:
            Path(args.out).write_text(markdown, encoding="utf-8")
            print(f"✅ {args.out} に出力しました")
        except OSError as e:
            print(f"❌ 書き込みエラー: {e}", file=sys.stderr)
            return 2
    else:
        print(markdown)

    return 0


if __name__ == "__main__":
    sys.exit(main())
