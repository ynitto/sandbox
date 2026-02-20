#!/usr/bin/env python3
"""スキルリポジトリを検証する: ライセンス・SKILL.md 構造・セキュリティ簡易チェック。

使い方:
    python verify_skill.py <repo-url> [--skill-root <path>]

引数:
    repo-url     - Git リポジトリURL
    --skill-root - リポジトリ内のスキルルートパス (デフォルト: skills)

出力 (エージェントが解析する機械可読行):
    VERIFY_CLONE: ok|fail
    VERIFY_LICENSE: ok|warn|fail  <ライセンス名>
    VERIFY_SKILL: ok|fail  <name>  <description>
    VERIFY_SECURITY: ok|warn
    VERIFY_RESULT: ok|warn|fail  [メッセージ]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

# ライセンス判定リスト
APPROVED_LICENSES = frozenset({
    "MIT", "Apache-2.0", "ISC",
    "BSD-2-Clause", "BSD-3-Clause", "BSD",
    "0BSD", "Unlicense", "CC0-1.0",
})

WARN_LICENSES = frozenset({
    "GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "AGPL-3.0", "MPL-2.0",
})

# 簡易セキュリティパターン (Python / Shell / JS)
SUSPICIOUS_PATTERNS = [
    r"rm\s+-[rf]+\s+/",
    r"curl\s+[^\|]*\|\s*(?:bash|sh)\b",
    r"wget\s+[^\|]*\|\s*(?:bash|sh)\b",
    r"eval\s+\$",
    r"subprocess\.[a-z_]+\(.*shell\s*=\s*True",
    r"os\.system\s*\(",
    r"__import__\s*\(\s*['\"]os['\"]",
    r"exec\s*\(",
]

SCRIPT_EXTENSIONS = (".py", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".js", ".ts")


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------

def clone_repo(url: str, target_dir: str) -> bool:
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--", url, target_dir],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  git clone エラー: {result.stderr.strip()[:200]}")
    return result.returncode == 0


# ---------------------------------------------------------------------------
# ライセンス検出
# ---------------------------------------------------------------------------

_LICENSE_SIGNATURES: list[tuple[str, str]] = [
    ("MIT License", "MIT"),
    ("MIT license", "MIT"),
    ("Apache License", "Apache-2.0"),
    ("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL-3.0"),
    ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL"),
    ("GNU GENERAL PUBLIC LICENSE", "GPL"),
    ("GNU Lesser General Public License", "LGPL"),
    ("Mozilla Public License", "MPL-2.0"),
    ("The Unlicense", "Unlicense"),
    ("unlicense.org", "Unlicense"),
    ("CC0 1.0 Universal", "CC0-1.0"),
    ("BSD 3-Clause", "BSD-3-Clause"),
    ("BSD 2-Clause", "BSD-2-Clause"),
    ("ISC License", "ISC"),
    ("ISC license", "ISC"),
]

_LICENSE_FILENAMES = (
    "LICENSE", "LICENSE.md", "LICENSE.txt",
    "LICENCE", "LICENCE.md", "COPYING",
)


def detect_license(repo_dir: str) -> tuple[str, str]:
    """(status, license_name) を返す。status: ok / warn / fail"""
    for fname in _LICENSE_FILENAMES:
        path = os.path.join(repo_dir, fname)
        if not os.path.isfile(path):
            continue

        with open(path, encoding="utf-8", errors="ignore") as f:
            content = f.read(8192)

        name = _identify_license(content)

        if name in APPROVED_LICENSES:
            return "ok", name
        elif name in WARN_LICENSES or name.startswith("GPL") or name.startswith("LGPL"):
            return "warn", name
        else:
            return "warn", f"{name} (要確認)"

    return "fail", "LICENSE ファイルなし"


def _identify_license(content: str) -> str:
    for signature, name in _LICENSE_SIGNATURES:
        if signature in content:
            # GPL/LGPL はバージョンを抽出する
            if name in ("GPL", "LGPL"):
                version_m = re.search(r"Version\s+(\d+)", content)
                if version_m:
                    return f"{name.split('-')[0]}-{version_m.group(1)}.0"
            return name
    return "Unknown"


# ---------------------------------------------------------------------------
# SKILL.md 検証
# ---------------------------------------------------------------------------

def check_skill_md(repo_dir: str, skill_root: str) -> tuple[str, str, str]:
    """(status, name, description) を返す。status: ok / fail"""
    candidates: list[str] = []

    # skill_root 以下のサブディレクトリにある SKILL.md を探す
    root_path = os.path.join(repo_dir, skill_root)
    if os.path.isdir(root_path):
        for entry in os.listdir(root_path):
            md = os.path.join(root_path, entry, "SKILL.md")
            if os.path.isfile(md):
                candidates.append(md)

    # リポジトリ直下の SKILL.md もチェック（単体スキルリポジトリ対応）
    root_md = os.path.join(repo_dir, "SKILL.md")
    if os.path.isfile(root_md):
        candidates.append(root_md)

    if not candidates:
        return "fail", "", "SKILL.md が見つかりません"

    skills: list[tuple[str, str]] = []
    for md_path in candidates:
        name, desc = _parse_frontmatter(md_path)
        if name and desc:
            skills.append((name, desc))

    if not skills:
        return "fail", "", "SKILL.md に name/description フロントマターがありません"

    name, desc = skills[0]
    suffix = f" (+{len(skills) - 1} スキル)" if len(skills) > 1 else ""
    return "ok", name, f"{desc[:60]}{suffix}"


def _parse_frontmatter(md_path: str) -> tuple[str, str]:
    """(name, description) を返す。なければ空文字。"""
    try:
        with open(md_path, encoding="utf-8", errors="ignore") as f:
            content = f.read(4096)
    except OSError:
        return "", ""

    fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not fm_match:
        return "", ""

    fm = fm_match.group(1)
    name_m = re.search(r'^name:\s*(.+)$', fm, re.MULTILINE)
    desc_m = re.search(r'^description:\s*(.+)$', fm, re.MULTILINE)
    return (
        name_m.group(1).strip() if name_m else "",
        desc_m.group(1).strip() if desc_m else "",
    )


# ---------------------------------------------------------------------------
# セキュリティ簡易チェック
# ---------------------------------------------------------------------------

def check_security(repo_dir: str) -> tuple[str, list[str]]:
    """(status, warnings) を返す。status: ok / warn"""
    compiled = [(re.compile(p), p) for p in SUSPICIOUS_PATTERNS]
    warnings: list[str] = []

    for dirpath, dirnames, filenames in os.walk(repo_dir):
        # .git ディレクトリは除外
        dirnames[:] = [d for d in dirnames if d != ".git"]

        for fname in filenames:
            if not fname.endswith(SCRIPT_EXTENSIONS):
                continue

            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue

            for pattern, pattern_str in compiled:
                if pattern.search(content):
                    rel = os.path.relpath(fpath, repo_dir)
                    warnings.append(f"{rel}: {pattern_str}")
                    break  # ファイルごとに最初の1件のみ報告

    return ("warn", warnings) if warnings else ("ok", [])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="スキルリポジトリを検証する",
    )
    parser.add_argument("url", help="Git リポジトリURL")
    parser.add_argument(
        "--skill-root",
        default="skills",
        help="リポジトリ内のスキルルートパス (デフォルト: skills)",
    )
    args = parser.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="skill-recruit-")
    try:
        print(f"🔄 クローン中: {args.url}")
        if not clone_repo(args.url, tmpdir):
            print("VERIFY_CLONE: fail")
            print("VERIFY_RESULT: fail  クローンに失敗しました")
            sys.exit(1)
        print("VERIFY_CLONE: ok")

        lic_status, lic_name = detect_license(tmpdir)
        print(f"VERIFY_LICENSE: {lic_status}  {lic_name}")

        skill_status, skill_name, skill_desc = check_skill_md(tmpdir, args.skill_root)
        print(f"VERIFY_SKILL: {skill_status}  {skill_name}  {skill_desc}")

        sec_status, sec_warnings = check_security(tmpdir)
        print(f"VERIFY_SECURITY: {sec_status}")
        for w in sec_warnings:
            print(f"  ⚠️  {w}")

        # 総合判定
        if lic_status == "fail" or skill_status == "fail":
            reasons = []
            if lic_status == "fail":
                reasons.append(lic_name)
            if skill_status == "fail":
                reasons.append(skill_desc)
            print(f"VERIFY_RESULT: fail  {' / '.join(reasons)}")
        elif lic_status == "warn" or sec_status == "warn":
            print("VERIFY_RESULT: warn  要確認事項があります")
        else:
            print("VERIFY_RESULT: ok")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
