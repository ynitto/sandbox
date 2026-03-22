#!/usr/bin/env python3
"""
detect_antipatterns.py - 静的解析による性能アンチパターン検出

使い方:
  python detect_antipatterns.py [--path DIR] [--lang LANG] [--json] [--severity LEVEL]

検出カテゴリ:
  - N+1 クエリ（ループ内 DB アクセス）
  - ループ内不変処理
  - 文字列 + 連結
  - 同期 I/O（async コンテキスト内）
  - 大量データの一括展開
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ==================== データモデル ====================

@dataclass
class Finding:
    severity: str          # Critical / Warning / Suggestion
    category: str
    summary: str
    location: str          # file:line
    detail: str = ""
    suggestion: str = ""

# ==================== 検出パターン ====================

# 言語 → 拡張子マッピング
LANG_EXTENSIONS = {
    "python": [".py"],
    "javascript": [".js", ".mjs"],
    "typescript": [".ts", ".tsx"],
    "go": [".go"],
    "ruby": [".rb"],
    "java": [".java"],
    "kotlin": [".kt"],
}

ALL_EXTENSIONS = {ext for exts in LANG_EXTENSIONS.values() for ext in exts}

# パターン定義: (regex, severity, category, summary, suggestion)
PATTERNS = [
    # ---- N+1 / DB アクセス ----
    (
        r"for .+:\s*\n(?:.*\n)*?.*\.(find|get|filter|query|execute|fetch|select)\(",
        "Critical", "N+1クエリ",
        "ループ内でDB/ORM クエリを実行している（N+1 の可能性）",
        "eager_load / prefetch_related / JOIN / バッチ取得に置き換える",
    ),
    (
        r"for .+:\s*\n(?:.*\n)*?.*\bawait\b.*(find|get|query|fetch)\(",
        "Critical", "N+1クエリ(async)",
        "非同期ループ内でDBクエリを直列実行している",
        "Promise.all / asyncio.gather でバッチ実行に変更する",
    ),
    (
        r"SELECT\s+\*\s+FROM",
        "Warning", "SELECT *",
        "SELECT * で全カラムを取得している",
        "必要なカラムのみ指定して転送量を削減する",
    ),
    # ---- ループ内不変処理 ----
    (
        r"for .+:\s*\n(?:.*\n)*?.*re\.compile\(",
        "Warning", "ループ内不変処理",
        "ループ内で正規表現をコンパイルしている（毎回コンパイルは無駄）",
        "ループ外で re.compile() してキャッシュする",
    ),
    (
        r"for .+:\s*\n(?:.*\n)*?.*open\(.+['\"]r['\"]",
        "Warning", "ループ内ファイルI/O",
        "ループ内でファイルを繰り返し open している",
        "ループ外で読み込みキャッシュするか、ストリーミング処理に変更する",
    ),
    # ---- 文字列連結 ----
    (
        r"for .+:\s*\n(?:.*\n)*?\s+\w+\s*\+=\s*['\"]",
        "Warning", "ループ内文字列連結",
        "ループ内で += による文字列連結をしている（O(n²) になる）",
        "リストに追記して最後に ''.join() でまとめる",
    ),
    # ---- async 内 sync I/O ----
    (
        r"async def .+:\s*\n(?:.*\n)*?(?<!await )(?:requests\.(get|post|put)|urllib\.request|open\()",
        "Critical", "async内同期I/O",
        "async 関数内で同期 I/O を呼んでいる（イベントループをブロックする）",
        "httpx / aiohttp / aiofiles 等の非同期ライブラリに切り替える",
    ),
    # ---- ネストループ ----
    (
        r"for .+:\s*\n(?:.*\n)*?\s+for .+:\s*\n(?:.*\n)*?\s+for ",
        "Warning", "3重ネストループ",
        "3重以上のネストループ（O(n³)の可能性）",
        "アルゴリズムを見直す。辞書/集合を使ったインデックス化でループ削減を検討する",
    ),
    # ---- 大量データ一括展開 ----
    (
        r"\.read\(\)\s*$",
        "Suggestion", "ファイル全体読み込み",
        "ファイル全体を一度にメモリへ読み込んでいる",
        "大きなファイルはイテレータ/チャンク読み込みに変更する",
    ),
    (
        r"list\(.*\.all\(\)\)",
        "Suggestion", "全件list展開",
        "QuerySet/カーソルを list() で全件展開している",
        "iterator() / チャンク処理 / ページネーションを使う",
    ),
    # ---- JavaScript/TypeScript 固有 ----
    (
        r"for\s*\(.+\)\s*\{[\s\S]*?await\s+.*(find|get|fetch|query)",
        "Critical", "N+1クエリ(JS)",
        "for ループ内で await を使い DB/API を直列呼び出しをしている",
        "Promise.all() でバッチ化する",
    ),
    (
        r"Array\.from\(.*\.keys\(\)\)",
        "Suggestion", "不要なArray変換",
        "イテレータを Array.from で展開している（forOf で直接反復可能）",
        "for...of で直接反復するか、必要な場合のみ Array.from を使う",
    ),
]


# ==================== スキャン処理 ====================

def get_files(path: Path, lang: str | None) -> list[Path]:
    """対象ファイルを列挙する。"""
    if lang:
        exts = LANG_EXTENSIONS.get(lang, [])
    else:
        exts = list(ALL_EXTENSIONS)

    result = []
    if path.is_file():
        if path.suffix in exts:
            result.append(path)
    else:
        for ext in exts:
            result.extend(path.rglob(f"*{ext}"))

    # node_modules / .venv / __pycache__ 等を除外
    ignore_dirs = {"node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build", "target"}
    return [f for f in result if not any(d in f.parts for d in ignore_dirs)]


def scan_file(filepath: Path) -> list[Finding]:
    """単一ファイルをスキャンして Finding を返す。"""
    findings = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
    except OSError:
        return findings

    for pattern, severity, category, summary, suggestion in PATTERNS:
        for match in re.finditer(pattern, content, re.MULTILINE):
            # マッチ開始位置の行番号を計算
            lineno = content[: match.start()].count("\n") + 1
            findings.append(Finding(
                severity=severity,
                category=category,
                summary=summary,
                location=f"{filepath}:{lineno}",
                detail=lines[lineno - 1].strip() if lineno <= len(lines) else "",
                suggestion=suggestion,
            ))

    return findings


def filter_severity(findings: list[Finding], min_severity: str) -> list[Finding]:
    order = {"Critical": 3, "Warning": 2, "Suggestion": 1}
    threshold = order.get(min_severity, 1)
    return [f for f in findings if order.get(f.severity, 0) >= threshold]


# ==================== 出力 ====================

SEVERITY_ICON = {"Critical": "🔴", "Warning": "🟡", "Suggestion": "🔵"}


def print_text(findings: list[Finding]) -> None:
    if not findings:
        print("✅ 性能アンチパターンは検出されませんでした。")
        return

    by_severity: dict[str, list[Finding]] = {}
    for f in findings:
        by_severity.setdefault(f.severity, []).append(f)

    counts = {s: len(v) for s, v in by_severity.items()}
    print(f"## 性能アンチパターン検出結果")
    print(f"Critical: {counts.get('Critical', 0)}件 / Warning: {counts.get('Warning', 0)}件 / Suggestion: {counts.get('Suggestion', 0)}件\n")

    for severity in ["Critical", "Warning", "Suggestion"]:
        items = by_severity.get(severity, [])
        if not items:
            continue
        icon = SEVERITY_ICON[severity]
        for f in items:
            print(f"{icon} [{severity}] {f.summary}")
            print(f"   場所: {f.location}")
            if f.detail:
                print(f"   コード: {f.detail}")
            print(f"   改善案: {f.suggestion}")
            print()


def print_json(findings: list[Finding]) -> None:
    data = {
        "total": len(findings),
        "summary": {
            "critical": sum(1 for f in findings if f.severity == "Critical"),
            "warning": sum(1 for f in findings if f.severity == "Warning"),
            "suggestion": sum(1 for f in findings if f.severity == "Suggestion"),
        },
        "findings": [asdict(f) for f in findings],
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ==================== エントリポイント ====================

def main() -> int:
    parser = argparse.ArgumentParser(description="性能アンチパターン静的解析")
    parser.add_argument("--path", default=".", help="スキャン対象のパス（デフォルト: カレントディレクトリ）")
    parser.add_argument("--lang", choices=list(LANG_EXTENSIONS.keys()), help="対象言語を絞る")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 形式で出力")
    parser.add_argument("--severity", default="Suggestion", choices=["Critical", "Warning", "Suggestion"],
                        help="報告する最低重要度（デフォルト: Suggestion）")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"エラー: {target} が存在しません", file=sys.stderr)
        return 2

    files = get_files(target, args.lang)
    all_findings: list[Finding] = []
    for f in files:
        all_findings.extend(scan_file(f))

    all_findings = filter_severity(all_findings, args.severity)

    if args.as_json:
        print_json(all_findings)
    else:
        print_text(all_findings)

    # 終了コード: 0=Critical/High なし / 1=Critical 検出
    has_critical = any(f.severity == "Critical" for f in all_findings)
    return 1 if has_critical else 0


if __name__ == "__main__":
    sys.exit(main())
