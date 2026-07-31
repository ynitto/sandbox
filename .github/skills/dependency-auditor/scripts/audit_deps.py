#!/usr/bin/env python3
"""
audit_deps.py - 依存関係の自動検出・監査コマンド生成・結果集約

使い方:
  python audit_deps.py [--path DIR] [--check security|license|outdated] [--json] [--severity LEVEL]

機能:
  1. パッケージマネージャーを自動検出する
  2. 各マネージャーの監査コマンドを提示する（実行可能な場合は実行する）
  3. 結果を統合して報告する
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path


# ==================== データモデル ====================

@dataclass
class PackageManager:
    name: str
    marker_files: list[str]
    lock_files: list[str]
    security_cmd: list[str]
    outdated_cmd: list[str]
    license_cmd: list[str]


@dataclass
class AuditFinding:
    type: str          # vulnerability / license / outdated / abandoned
    severity: str      # Critical / High / Moderate / Low / Info
    package: str
    summary: str
    detail: str = ""
    fix: str = ""
    cve: str = ""
    cvss: float = 0.0
    license: str = ""


# ==================== パッケージマネージャー定義 ====================

PACKAGE_MANAGERS = [
    PackageManager(
        name="npm",
        marker_files=["package.json"],
        lock_files=["package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
        security_cmd=["npm", "audit", "--json"],
        outdated_cmd=["npm", "outdated", "--json"],
        license_cmd=["npx", "license-checker", "--json"],
    ),
    PackageManager(
        name="pip",
        marker_files=["requirements.txt", "setup.py", "setup.cfg", "pyproject.toml"],
        lock_files=["Pipfile.lock", "poetry.lock", "uv.lock"],
        security_cmd=["pip-audit", "--format", "json"],
        outdated_cmd=["pip", "list", "--outdated", "--format", "json"],
        license_cmd=["pip-licenses", "--format", "json"],
    ),
    PackageManager(
        name="cargo",
        marker_files=["Cargo.toml"],
        lock_files=["Cargo.lock"],
        security_cmd=["cargo", "audit", "--json"],
        outdated_cmd=["cargo", "outdated", "--format", "json"],
        license_cmd=["cargo", "license", "--json"],
    ),
    PackageManager(
        name="go",
        marker_files=["go.mod"],
        lock_files=["go.sum"],
        security_cmd=["govulncheck", "-json", "./..."],
        outdated_cmd=["go", "list", "-u", "-m", "-json", "all"],
        license_cmd=["go-licenses", "report", "./..."],
    ),
    PackageManager(
        name="bundler",
        marker_files=["Gemfile"],
        lock_files=["Gemfile.lock"],
        security_cmd=["bundle", "audit", "check"],
        outdated_cmd=["bundle", "outdated"],
        license_cmd=["license_finder", "report", "--format", "json"],
    ),
    PackageManager(
        name="maven",
        marker_files=["pom.xml"],
        lock_files=[],
        security_cmd=["mvn", "dependency-check:check", "-Dformat=JSON"],
        outdated_cmd=["mvn", "versions:display-dependency-updates"],
        license_cmd=["mvn", "license:aggregate-add-third-party"],
    ),
    PackageManager(
        name="gradle",
        marker_files=["build.gradle", "build.gradle.kts"],
        lock_files=["gradle.lockfile"],
        security_cmd=["./gradlew", "dependencyCheckAnalyze"],
        outdated_cmd=["./gradlew", "dependencyUpdates"],
        license_cmd=["./gradlew", "generateLicenseReport"],
    ),
]


# ==================== 検出 ====================

def detect_package_managers(path: Path) -> list[tuple[PackageManager, Path]]:
    """指定ディレクトリ内のパッケージマネージャーと該当ファイルを検出する。"""
    found = []
    for pm in PACKAGE_MANAGERS:
        for marker in pm.marker_files:
            marker_path = path / marker
            if marker_path.exists():
                found.append((pm, marker_path))
                break
    return found


def check_lock_file(pm: PackageManager, path: Path) -> bool:
    """ロックファイルが存在するか確認する。"""
    return any((path / lf).exists() for lf in pm.lock_files) if pm.lock_files else True


def run_command(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    """コマンドを実行して (成功フラグ, 出力テキスト) を返す。"""
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=120
        )
        return result.returncode in (0, 1), result.stdout or result.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, str(e)


# ==================== npm 結果パース ====================

def parse_npm_audit(output: str) -> list[AuditFinding]:
    findings = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return findings

    # npm audit v7+ 形式
    vulns = data.get("vulnerabilities", {})
    for pkg_name, info in vulns.items():
        severity = info.get("severity", "").capitalize()
        if severity not in ("Critical", "High", "Moderate", "Low"):
            continue
        via = info.get("via", [])
        cve = ""
        cvss = 0.0
        for v in via:
            if isinstance(v, dict):
                cve = v.get("cve", "")
                cvss = v.get("cvss", {}).get("score", 0.0) if isinstance(v.get("cvss"), dict) else 0.0
                break
        fix_data = info.get("fixAvailable", {})
        fix = f"npm update {fix_data.get('name', pkg_name)}@{fix_data.get('version', 'latest')}" if isinstance(fix_data, dict) else "npm audit fix"

        findings.append(AuditFinding(
            type="vulnerability",
            severity=severity,
            package=f"{pkg_name}@{info.get('range', 'unknown')}",
            summary=f"{cve or '脆弱性'}: {severity} severity",
            cve=cve,
            cvss=cvss,
            fix=fix,
        ))
    return findings


def parse_pip_audit(output: str) -> list[AuditFinding]:
    findings = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return findings

    # pip-audit JSON 形式
    for dep in data.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            sev = vuln.get("fix_versions", [])
            findings.append(AuditFinding(
                type="vulnerability",
                severity="High",  # pip-audit は severity を返さないため High とする
                package=f"{dep.get('name')}=={dep.get('version')}",
                summary=vuln.get("description", "脆弱性"),
                cve=vuln.get("id", ""),
                fix=f"pip install {dep.get('name')}=={sev[0]}" if sev else "バージョンアップを確認",
            ))
    return findings


# ==================== ライセンス評価 ====================

LICENSE_RISK = {
    # 🔴 要確認（コピーレフト）
    "GPL-2.0": "critical", "GPL-3.0": "critical",
    "AGPL-3.0": "critical", "AGPL-1.0": "critical",
    "GPL-2.0-only": "critical", "GPL-3.0-only": "critical",
    # 🟡 注意（弱コピーレフト）
    "LGPL-2.0": "warning", "LGPL-2.1": "warning", "LGPL-3.0": "warning",
    "MPL-2.0": "warning", "EPL-1.0": "warning", "EPL-2.0": "warning",
    "CDDL-1.0": "warning",
    # 🟢 許容（ペルミッシブ）
    "MIT": "ok", "Apache-2.0": "ok", "BSD-2-Clause": "ok", "BSD-3-Clause": "ok",
    "ISC": "ok", "0BSD": "ok", "CC0-1.0": "ok", "Unlicense": "ok",
}

def classify_license(license_str: str) -> tuple[str, str]:
    """ライセンス文字列からリスク分類と説明を返す。"""
    normalized = license_str.strip().replace(" ", "-")
    risk = LICENSE_RISK.get(normalized)
    if risk == "critical":
        return "critical", "コピーレフトライセンス。ソース開示義務の可能性あり（法務確認推奨）"
    elif risk == "warning":
        return "warning", "弱コピーレフト。利用条件を確認する"
    elif risk == "ok":
        return "ok", "ペルミッシブライセンス。商用・プロプライエタリ利用可"
    elif not license_str or license_str in ("UNKNOWN", ""):
        return "unknown", "ライセンス不明。個別確認が必要"
    else:
        return "unknown", f"分類不明なライセンス: {license_str}"


# ==================== レポート生成 ====================

SEVERITY_ORDER = {"Critical": 4, "High": 3, "Moderate": 2, "Low": 1, "Info": 0}
SEVERITY_ICON = {"Critical": "🔴", "High": "🟠", "Moderate": "🟡", "Low": "🔵", "Info": "ℹ️"}


def print_text_report(pm_list: list[tuple[PackageManager, Path]], findings: list[AuditFinding],
                       lock_warnings: list[str]) -> None:
    print("## 依存関係監査結果\n")

    # パッケージマネージャー一覧
    print("### 検出されたパッケージマネージャー")
    for pm, marker_path in pm_list:
        print(f"- {pm.name}: {marker_path}")
    if lock_warnings:
        for w in lock_warnings:
            print(f"  ⚠️  {w}")
    print()

    # セキュリティ
    vuln_findings = [f for f in findings if f.type == "vulnerability"]
    print("### 🔒 セキュリティ脆弱性")
    if not vuln_findings:
        print("✅ 既知の脆弱性は検出されませんでした。\n")
    else:
        for f in sorted(vuln_findings, key=lambda x: SEVERITY_ORDER.get(x.severity, 0), reverse=True):
            icon = SEVERITY_ICON.get(f.severity, "•")
            cvss_str = f" / CVSS: {f.cvss}" if f.cvss else ""
            cve_str = f" [{f.cve}{cvss_str}]" if f.cve else ""
            print(f"{icon} {f.severity}{cve_str}: {f.package}")
            print(f"   内容: {f.summary}")
            if f.fix:
                print(f"   対処: {f.fix}")
            print()

    # ライセンス
    license_findings = [f for f in findings if f.type == "license"]
    print("### 📄 ライセンス問題")
    if not license_findings:
        print("✅ 問題のあるライセンスは検出されませんでした。\n")
    else:
        for f in license_findings:
            icon = "🔴" if f.severity == "Critical" else "🟡"
            print(f"{icon} {f.package} — {f.license}")
            print(f"   リスク: {f.summary}")
            if f.fix:
                print(f"   対処: {f.fix}")
            print()

    # 最新性
    outdated_findings = [f for f in findings if f.type in ("outdated", "abandoned")]
    print("### 🕐 最新性・メンテナンス")
    if not outdated_findings:
        print("✅ 大きな遅延のある依存関係は検出されませんでした。\n")
    else:
        for f in outdated_findings:
            print(f"• {f.package}: {f.summary}")
            if f.fix:
                print(f"  対処: {f.fix}")
        print()

    # サマリー
    critical = sum(1 for f in vuln_findings if f.severity == "Critical")
    high = sum(1 for f in vuln_findings if f.severity == "High")
    moderate = sum(1 for f in vuln_findings if f.severity == "Moderate")
    lic_issues = len(license_findings)
    print("### サマリー")
    print(f"- セキュリティ: Critical {critical}件 / High {high}件 / Moderate {moderate}件")
    print(f"- ライセンス: 要確認 {lic_issues}件")
    print(f"- 最新性: 遅延・放棄リスク {len(outdated_findings)}件")


def print_json_report(pm_list: list[tuple[PackageManager, Path]], findings: list[AuditFinding]) -> None:
    critical = sum(1 for f in findings if f.severity == "Critical")
    high = sum(1 for f in findings if f.severity == "High")
    verdict = "CLEAN" if critical == 0 and high == 0 else "CRITICAL_RISK" if critical > 0 else "NEEDS_ATTENTION"
    data = {
        "skill": "dependency-auditor",
        "verdict": verdict,
        "severity_summary": {
            "critical": critical,
            "high": high,
            "moderate": sum(1 for f in findings if f.severity == "Moderate"),
            "license_issues": sum(1 for f in findings if f.type == "license"),
            "abandoned": sum(1 for f in findings if f.type == "abandoned"),
        },
        "blocking": critical > 0 or any(f.type == "license" and f.severity == "Critical" for f in findings),
        "package_managers": [{"name": pm.name, "marker": str(marker)} for pm, marker in pm_list],
        "blocking_issues": [
            {
                "type": f.type,
                "severity": f.severity,
                "package": f.package,
                "cve": f.cve,
                "cvss_score": f.cvss,
                "summary": f.summary,
                "fix": f.fix,
            }
            for f in findings if f.severity in ("Critical", "High")
        ],
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ==================== エントリポイント ====================

def main() -> int:
    parser = argparse.ArgumentParser(description="依存関係監査ツール")
    parser.add_argument("--path", default=".", help="プロジェクトルート（デフォルト: カレントディレクトリ）")
    parser.add_argument("--check", choices=["security", "license", "outdated", "all"], default="all",
                        help="監査軸を絞る（デフォルト: all）")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 形式で出力")
    parser.add_argument("--severity", choices=["critical", "high", "moderate", "low"], default="moderate",
                        help="報告する最低重要度（デフォルト: moderate）")
    parser.add_argument("--parse-npm-json", action="store_true", help="stdin の npm audit JSON を解析する")
    args = parser.parse_args()

    # stdin からの npm audit JSON パース
    if args.parse_npm_json:
        raw = sys.stdin.read()
        findings = parse_npm_audit(raw)
        if args.as_json:
            print_json_report([], findings)
        else:
            print_text_report([], findings, [])
        return 1 if any(f.severity == "Critical" for f in findings) else 0

    target = Path(args.path)
    if not target.exists():
        print(f"エラー: {target} が存在しません", file=sys.stderr)
        return 2

    # パッケージマネージャー検出
    pm_list = detect_package_managers(target)
    if not pm_list:
        print("⚠️  依存関係ファイルが見つかりませんでした。", file=sys.stderr)
        print("確認対象: package.json, requirements.txt, Cargo.toml, go.mod, Gemfile, pom.xml, build.gradle")
        return 2

    # ロックファイル確認
    lock_warnings = []
    for pm, _ in pm_list:
        if pm.lock_files and not check_lock_file(pm, target):
            lock_warnings.append(f"{pm.name}: ロックファイルがありません。監査精度が低下します")

    # 監査コマンドを提示・実行
    all_findings: list[AuditFinding] = []

    for pm, marker_path in pm_list:
        print(f"[{pm.name}] 監査中...", file=sys.stderr)

        # セキュリティ
        if args.check in ("security", "all"):
            success, output = run_command(pm.security_cmd, target)
            if success:
                if pm.name == "npm":
                    all_findings.extend(parse_npm_audit(output))
                elif pm.name == "pip":
                    all_findings.extend(parse_pip_audit(output))
                # 他のパッケージマネージャーは出力をテキスト形式で表示（パーサー未実装）
            else:
                print(f"  ⚠️  {pm.name} セキュリティ監査コマンドが実行できませんでした。", file=sys.stderr)
                print(f"  手動で実行してください: {' '.join(pm.security_cmd)}", file=sys.stderr)

    # 重要度フィルタ
    severity_threshold = {"critical": 4, "high": 3, "moderate": 2, "low": 1}
    threshold = severity_threshold.get(args.severity, 2)
    filtered = [f for f in all_findings if SEVERITY_ORDER.get(f.severity, 0) >= threshold]

    if args.as_json:
        print_json_report(pm_list, filtered)
    else:
        print_text_report(pm_list, filtered, lock_warnings)

    has_critical = any(f.severity == "Critical" for f in filtered)
    return 1 if has_critical else 0


if __name__ == "__main__":
    sys.exit(main())
