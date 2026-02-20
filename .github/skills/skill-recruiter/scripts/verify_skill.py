#!/usr/bin/env python3
"""スキルリポジトリを検証する: ライセンス・SKILL.md 構造・セキュリティ・ネットワーク通信チェック。

使い方:
    python verify_skill.py <source> [--skill-root <path>]

引数:
    source       - Git リポジトリURL またはローカルディレクトリパス
    --skill-root - リポジトリ/ディレクトリ内のスキルルートパス (デフォルト: skills)

出力 (エージェントが解析する機械可読行):
    VERIFY_CLONE: ok|skip|fail          skip=ローカルパス（クローン不要）
    VERIFY_LICENSE: ok|warn|fail  <ライセンス名>
    VERIFY_SKILL: ok|warn|fail  <name>  <description>
    VERIFY_SECURITY: ok|warn
    VERIFY_NETWORK: ok|warn  [検出パターン...]
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

# ネットワーク通信パターン (HTTP リクエスト・外部サービス接続)
NETWORK_PATTERNS = [
    # Python
    r"requests\.(get|post|put|patch|delete|head|request)\s*\(",
    r"urllib\.request\.",
    r"urllib2\.",
    r"http\.client\.",
    r"aiohttp\.",
    r"httpx\.",
    # JavaScript / TypeScript
    r"\bfetch\s*\(",
    r"\bXMLHttpRequest\b",
    r"\baxios\s*\.",
    r"\bhttp(s)?\.request\s*\(",
    r"\bgot\s*\(",
    r"\bnode-fetch\b",
    # Shell
    r"\bcurl\s+",
    r"\bwget\s+",
    r"\bnc\s+",          # netcat
    r"\btelnet\s+",
    r"\bssh\s+",
]

SCRIPT_EXTENSIONS = (".py", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".js", ".ts")


# ---------------------------------------------------------------------------
# ソース判定
# ---------------------------------------------------------------------------

def is_local_path(source: str) -> bool:
    """source がローカルパスなら True、URL なら False を返す。"""
    if source.startswith(("./", "../", "/", "~", "\\")):
        return True
    # Windows ドライブレター (例: C:\, D:/)
    if len(source) >= 2 and source[1] == ":" and source[0].isalpha():
        return True
    # URL スキームがある場合は URL
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+\-.]*://', source):
        return False
    # その他は URL として扱う（例: github.com/... 形式）
    return False


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

    return "warn", "LICENSE ファイルなし（ライセンス不明）"


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
# ネットワーク通信チェック
# ---------------------------------------------------------------------------

def check_network(repo_dir: str) -> tuple[str, list[str]]:
    """(status, detections) を返す。status: ok / warn

    スクリプトファイルおよび SKILL.md 内で外部通信の可能性があるパターンを検出する。
    """
    compiled = [(re.compile(p), p) for p in NETWORK_PATTERNS]
    detections: list[str] = []
    target_extensions = SCRIPT_EXTENSIONS + (".md",)

    for dirpath, dirnames, filenames in os.walk(repo_dir):
        dirnames[:] = [d for d in dirnames if d != ".git"]

        for fname in filenames:
            if not any(fname.endswith(ext) for ext in target_extensions):
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
                    detections.append(f"{rel}: {pattern_str}")
                    break  # ファイルごとに最初の1件のみ報告

    return ("warn", detections) if detections else ("ok", [])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="スキルリポジトリを検証する",
    )
    parser.add_argument(
        "source",
        help="Git リポジトリURL またはローカルディレクトリパス",
    )
    parser.add_argument(
        "--skill-root",
        default="skills",
        help="リポジトリ/ディレクトリ内のスキルルートパス (デフォルト: skills)",
    )
    args = parser.parse_args()

    local = is_local_path(args.source)
    tmpdir: str | None = None

    if local:
        work_dir = os.path.expandvars(os.path.expanduser(args.source))
        if not os.path.isdir(work_dir):
            print("VERIFY_CLONE: fail")
            print(f"VERIFY_RESULT: fail  ローカルパスが見つかりません: {work_dir}")
            sys.exit(1)
        print(f"VERIFY_CLONE: skip  (ローカルパス: {work_dir})")
    else:
        tmpdir = tempfile.mkdtemp(prefix="skill-recruit-")
        print(f"🔄 クローン中: {args.source}")
        if not clone_repo(args.source, tmpdir):
            print("VERIFY_CLONE: fail")
            print("VERIFY_RESULT: fail  クローンに失敗しました")
            shutil.rmtree(tmpdir, ignore_errors=True)
            sys.exit(1)
        print("VERIFY_CLONE: ok")
        work_dir = tmpdir

    try:
        lic_status, lic_name = detect_license(work_dir)
        print(f"VERIFY_LICENSE: {lic_status}  {lic_name}")

        skill_status, skill_name, skill_desc = check_skill_md(work_dir, args.skill_root)
        print(f"VERIFY_SKILL: {skill_status}  {skill_name}  {skill_desc}")

        sec_status, sec_warnings = check_security(work_dir)
        print(f"VERIFY_SECURITY: {sec_status}")
        for w in sec_warnings:
            print(f"  ⚠️  {w}")

        net_status, net_detections = check_network(work_dir)
        print(f"VERIFY_NETWORK: {net_status}")
        for d in net_detections:
            print(f"  🌐  {d}")

        # 総合判定 — SKILL.md 不正のみ fail（ライセンス・ネットワークは warn 止まりでユーザー選択）
        if skill_status == "fail":
            print(f"VERIFY_RESULT: fail  {skill_desc}")
        elif lic_status == "warn" or sec_status == "warn" or net_status == "warn":
            print("VERIFY_RESULT: warn  要確認事項があります")
        else:
            print("VERIFY_RESULT: ok")

    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
