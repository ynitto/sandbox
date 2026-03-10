#!/usr/bin/env python3
"""
scan_vulnerabilities.py - OWASP Top 10 ベースの脆弱性パターン静的スキャン

コードファイルを grep/正規表現でスキャンし、OWASP Top 10 に対応する
脆弱性パターンを検出して重要度付きで報告する。外部依存ゼロ。

使い方:
  # カレントディレクトリを再帰スキャン
  python scan_vulnerabilities.py

  # 対象ディレクトリを指定
  python scan_vulnerabilities.py --path src/

  # 特定ファイルのみ
  python scan_vulnerabilities.py --file app.py

  # JSON 形式で出力（他ツールとの連携用）
  python scan_vulnerabilities.py --json

  # 重要度でフィルタ（critical のみ表示）
  python scan_vulnerabilities.py --severity critical

終了コード:
  0 = Critical / High 検出なし（Medium 以下のみ）
  1 = Critical または High が 1 件以上検出
  2 = ファイル読み取りエラー
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


# ─── 対象拡張子 ──────────────────────────────────────────────

TARGET_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".go", ".rb", ".php", ".cs",
}

EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", "coverage",
}

# ─── 脆弱性パターン定義 ──────────────────────────────────────

@dataclass
class VulnPattern:
    id: str               # OWASP ID + 連番
    owasp: str            # 例: "A02:2021"
    severity: str         # critical / high / medium
    title: str
    pattern: re.Pattern
    message: str
    false_positive_note: str = ""


def _p(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern:
    return re.compile(pattern, flags)


PATTERNS: list[VulnPattern] = [
    # ── A02: Cryptographic Failures ─────────────────────────
    VulnPattern(
        id="A02-01", owasp="A02:2021",
        severity="critical",
        title="ハードコードされたシークレット（APIキー）",
        pattern=_p(
            r"""(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token|private[_-]?key)\s*[=:]\s*['"][A-Za-z0-9\-_./+]{16,}['"]"""
        ),
        message="APIキー・シークレットキーがコードにハードコードされています。環境変数または Secret Manager を使用してください。",
        false_positive_note="テストデータや ``example`` を含む場合は誤検知の可能性があります",
    ),
    VulnPattern(
        id="A02-02", owasp="A02:2021",
        severity="critical",
        title="ハードコードされたパスワード",
        pattern=_p(
            r"""(?:password|passwd|pwd)\s*[=:]\s*['"][^'"]{6,}['"]"""
        ),
        message="パスワードがコードにハードコードされています。環境変数または Secret Manager を使用してください。",
        false_positive_note="'password' という変数名だけで値が空・プレースホルダーの場合は誤検知です",
    ),
    VulnPattern(
        id="A02-03", owasp="A02:2021",
        severity="high",
        title="弱いハッシュアルゴリズム（MD5/SHA1）の使用",
        pattern=_p(r"""\b(?:md5|sha1|sha-1)\b"""),
        message="MD5・SHA1 はパスワードハッシュ・整合性検証には安全ではありません。SHA-256 以上を使用してください。",
        false_positive_note="チェックサムや非セキュリティ用途での使用は許容される場合があります",
    ),
    VulnPattern(
        id="A02-04", owasp="A02:2021",
        severity="high",
        title="HTTP（非TLS）による外部通信",
        pattern=_p(r"""fetch\s*\(\s*['"]http://|axios\.[a-z]+\s*\(\s*['"]http://|requests\.[a-z]+\s*\(\s*['"]http://"""),
        message="HTTP（非TLS）で外部APIを呼び出しています。本番環境では HTTPS を使用してください。",
    ),
    # ── A03: Injection ──────────────────────────────────────
    VulnPattern(
        id="A03-01", owasp="A03:2021",
        severity="critical",
        title="SQL インジェクション（文字列結合）",
        pattern=_p(
            r"""(?:SELECT|INSERT|UPDATE|DELETE|FROM|WHERE)\b.{0,60}?\+\s*(?:req\.|request\.|params\.|query\.|body\.|user\.|input)"""
        ),
        message="ユーザー入力を文字列結合してSQLを構築しています。パラメータ化クエリ（プリペアドステートメント）を使用してください。",
    ),
    VulnPattern(
        id="A03-02", owasp="A03:2021",
        severity="critical",
        title="コマンドインジェクション（shell=True）",
        pattern=_p(r"""subprocess\.[a-z_]+\s*\([^)]*shell\s*=\s*True"""),
        message="shell=True でサブプロセスを実行しています。ユーザー入力が含まれる場合、OS コマンドインジェクションが発生します。",
    ),
    VulnPattern(
        id="A03-03", owasp="A03:2021",
        severity="critical",
        title="eval/exec の使用（コードインジェクション）",
        pattern=_p(r"""\beval\s*\(|\bexec\s*\("""),
        message="eval/exec はコードインジェクションのリスクがあります。使用を避け、代替手段を検討してください。",
        false_positive_note="eval がコメントや文字列リテラル内の場合は誤検知です",
    ),
    VulnPattern(
        id="A03-04", owasp="A03:2021",
        severity="high",
        title="XSS: innerHTML への直接代入",
        pattern=_p(r"""\.innerHTML\s*=(?!\s*['"`]\s*['"`])"""),
        message="innerHTML への直接代入は XSS の原因になります。textContent または DOMPurify を使用してください。",
    ),
    VulnPattern(
        id="A03-05", owasp="A03:2021",
        severity="high",
        title="XSS: document.write の使用",
        pattern=_p(r"""document\.write\s*\("""),
        message="document.write は XSS リスクがあります。DOM 操作メソッドを使用してください。",
    ),
    # ── A07: Authentication Failures ────────────────────────
    VulnPattern(
        id="A07-01", owasp="A07:2021",
        severity="critical",
        title="JWT alg:none の脆弱性",
        pattern=_p(r"""alg[\"']?\s*[=:]\s*[\"']none[\"']"""),
        message="JWT の alg:none は署名検証をバイパスする重大な脆弱性です。有効な署名アルゴリズム（RS256等）を指定してください。",
    ),
    VulnPattern(
        id="A07-02", owasp="A07:2021",
        severity="high",
        title="トークンの localStorage 保存",
        pattern=_p(r"""localStorage\.setItem\s*\([^)]*(?:token|jwt|auth|session)[^)]*\)"""),
        message="認証トークンを localStorage に保存するとXSS攻撃で盗まれます。httpOnly クッキーを使用してください。",
    ),
    # ── A05: Security Misconfiguration ──────────────────────
    VulnPattern(
        id="A05-01", owasp="A05:2021",
        severity="high",
        title="CORS ワイルドカード（全オリジン許可）",
        pattern=_p(r"""Access-Control-Allow-Origin['":\s]+\*|cors\([^)]*origin\s*:\s*['"]\*['"]"""),
        message="CORS でワイルドカード（*）を設定すると全ドメインからのアクセスを許可します。信頼するオリジンを明示してください。",
    ),
    VulnPattern(
        id="A05-02", owasp="A05:2021",
        severity="medium",
        title="デバッグモードの有効化",
        pattern=_p(r"""debug\s*=\s*True|DEBUG\s*=\s*True|app\.run\s*\([^)]*debug\s*=\s*True"""),
        message="debug=True が設定されています。本番環境では必ず False にしてください。",
        false_positive_note="テストコードや設定ファイルのコメントは誤検知です",
    ),
    # ── A09: Logging Failures ───────────────────────────────
    VulnPattern(
        id="A09-01", owasp="A09:2021",
        severity="medium",
        title="ログへの機密情報出力",
        pattern=_p(
            r"""(?:console\.log|print|logger\.[a-z]+|logging\.[a-z]+)\s*\([^)]*(?:password|token|secret|api[_-]?key|credit_card)[^)]*\)"""
        ),
        message="機密情報（パスワード・トークン等）がログに出力される可能性があります。ログ出力前にマスキングしてください。",
        false_positive_note="エラーメッセージのみでフィールド値を含まない場合は誤検知です",
    ),
    # ── A10: SSRF ───────────────────────────────────────────
    VulnPattern(
        id="A10-01", owasp="A10:2021",
        severity="high",
        title="SSRF: ユーザー入力URLへの直接リクエスト",
        pattern=_p(
            r"""(?:fetch|axios\.[a-z]+|requests\.[a-z]+|urllib\.request)\s*\([^)]*(?:req\.|request\.|params\.|query\.|body\.|user_input)"""
        ),
        message="ユーザー提供のURLに直接リクエストを送信しています。URLのホスト先を許可リストで検証してSSRFを防いでください。",
    ),
    # ── A01: Broken Access Control ──────────────────────────
    VulnPattern(
        id="A01-01", owasp="A01:2021",
        severity="medium",
        title="コメントアウトされた認証チェック",
        pattern=_p(r"""#.*(?:auth|authorize|permission|is_admin|require_login)|//.*(?:auth|authorize|permission|is_admin|requireLogin)"""),
        message="認証・認可のチェックがコメントアウトされている可能性があります。意図的な無効化かを確認してください。",
        false_positive_note="説明コメントの場合は誤検知です",
    ),
    # ── 汎用 ────────────────────────────────────────────────
    VulnPattern(
        id="GEN-01", owasp="General",
        severity="medium",
        title="TODO / FIXME セキュリティマーカー",
        pattern=_p(r"""(?:TODO|FIXME|HACK|XXX)\s*:?\s*(?:security|auth|vuln|sql|xss|injection|unsafe)"""),
        message="セキュリティ関連の TODO/FIXME が残っています。対処済みかを確認してください。",
    ),
    VulnPattern(
        id="GEN-02", owasp="General",
        severity="medium",
        title="pickle/marshal のデシリアライズ（Python）",
        pattern=_p(r"""\bpickle\.loads?\s*\(|\bmarshal\.loads?\s*\("""),
        message="pickle/marshal は任意コード実行につながるデシリアライズ脆弱性を持ちます。信頼できないデータには使用しないでください。",
    ),
]


# ─── スキャン実行 ─────────────────────────────────────────────

@dataclass
class Finding:
    file: str
    line: int
    col: int
    vuln_id: str
    owasp: str
    severity: str
    title: str
    message: str
    matched_text: str
    false_positive_note: str


def should_skip_file(path: Path) -> bool:
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True
    return path.suffix.lower() not in TARGET_EXTENSIONS


def scan_content(content: str, filepath: str) -> list[Finding]:
    findings: list[Finding] = []
    lines = content.splitlines()
    for lineno, line in enumerate(lines, start=1):
        for pattern in PATTERNS:
            match = pattern.pattern.search(line)
            if match:
                findings.append(Finding(
                    file=filepath,
                    line=lineno,
                    col=match.start() + 1,
                    vuln_id=pattern.id,
                    owasp=pattern.owasp,
                    severity=pattern.severity,
                    title=pattern.title,
                    message=pattern.message,
                    matched_text=line.strip()[:120],
                    false_positive_note=pattern.false_positive_note,
                ))
    return findings


def collect_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if not should_skip_file(root) else []
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not should_skip_file(path):
            result.append(path)
    return result


def run_scan(root: Path) -> tuple[list[Finding], list[str]]:
    """スキャンを実行し (findings, errors) を返す。"""
    findings: list[Finding] = []
    errors: list[str] = []
    files = collect_files(root)
    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            findings.extend(scan_content(content, str(fpath)))
        except OSError as e:
            errors.append(f"{fpath}: {e}")
    return findings, errors


# ─── 出力 ─────────────────────────────────────────────────────

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}
SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡"}


def filter_by_severity(findings: list[Finding], min_severity: str) -> list[Finding]:
    threshold = SEVERITY_ORDER.get(min_severity.lower(), 2)
    return [f for f in findings if SEVERITY_ORDER.get(f.severity, 2) <= threshold]


def print_text_report(findings: list[Finding], errors: list[str]) -> None:
    if not findings and not errors:
        print("✅ 脆弱性パターンは検出されませんでした")
        return

    by_severity: dict[str, list[Finding]] = {"critical": [], "high": [], "medium": []}
    for f in findings:
        by_severity.setdefault(f.severity, []).append(f)

    total = len(findings)
    c = len(by_severity["critical"])
    h = len(by_severity["high"])
    m = len(by_severity["medium"])
    print(f"\n📊 スキャン結果: Critical {c} / High {h} / Medium {m}  (計 {total} 件)\n")

    for sev in ("critical", "high", "medium"):
        group = by_severity[sev]
        if not group:
            continue
        icon = SEVERITY_ICON[sev]
        print(f"{'=' * 60}")
        print(f"{icon}  {sev.upper()} ({len(group)} 件)")
        print(f"{'=' * 60}")
        for f in group:
            print(f"  [{f.vuln_id}] {f.title}")
            print(f"  ファイル : {f.file}:{f.line}:{f.col}")
            print(f"  OWASP   : {f.owasp}")
            print(f"  内容    : {f.message}")
            print(f"  該当行  : {f.matched_text}")
            if f.false_positive_note:
                print(f"  ⚠️  誤検知注意: {f.false_positive_note}")
            print()

    if errors:
        print(f"⚠️  読み取りエラー ({len(errors)} 件):")
        for e in errors:
            print(f"  {e}")


def print_json_report(findings: list[Finding], errors: list[str]) -> None:
    output = {
        "summary": {
            "total": len(findings),
            "critical": sum(1 for f in findings if f.severity == "critical"),
            "high": sum(1 for f in findings if f.severity == "high"),
            "medium": sum(1 for f in findings if f.severity == "medium"),
        },
        "findings": [asdict(f) for f in findings],
        "errors": errors,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


# ─── エントリポイント ──────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="OWASP Top 10 ベースの脆弱性パターン静的スキャン"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--path", default=".", help="スキャン対象ディレクトリ（デフォルト: カレント）")
    group.add_argument("--file", help="スキャン対象ファイルを直接指定")
    parser.add_argument(
        "--severity",
        choices=["critical", "high", "medium"],
        default="medium",
        help="報告する最低重要度（デフォルト: medium）",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON形式で出力")
    args = parser.parse_args()

    root = Path(args.file if args.file else args.path)
    if not root.exists():
        print(f"❌ パスが見つかりません: {root}", file=sys.stderr)
        return 2

    findings, errors = run_scan(root)
    findings = filter_by_severity(findings, args.severity)
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.file, f.line))

    if args.as_json:
        print_json_report(findings, errors)
    else:
        print_text_report(findings, errors)

    critical_or_high = any(f.severity in ("critical", "high") for f in findings)
    return 1 if critical_or_high else 0


if __name__ == "__main__":
    sys.exit(main())
