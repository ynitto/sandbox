#!/usr/bin/env python3
"""スキルの静的品質チェック。

agentskills.io のベストプラクティスガイドラインに基づいて
スキルの品質を検査する。

使い方:
    python quality_check.py                        # .github/skills/ 以下を全チェック
    python quality_check.py --skill <skill-name>   # 特定スキルのみ
    python quality_check.py --path <dir>           # 任意ディレクトリのスキルをチェック
"""
from __future__ import annotations

import argparse
import os
import re
import sys


# ──────────────────────────────────────────────
# フロントマター解析
# ──────────────────────────────────────────────

def parse_frontmatter(content: str) -> tuple[dict, str]:
    """YAML フロントマターと本文を分離してパースする。

    Returns:
        (frontmatter_dict, body_text)
        ネストされた metadata キーは dict として返す。
    """
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    raw = parts[1].strip()
    body = parts[2]

    fm: dict = {}
    current_parent: str | None = None
    nested: dict = {}

    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")

        if indent == 0:
            # 前のネストを確定
            if nested and current_parent:
                fm[current_parent] = nested
            current_parent = key
            nested = {}
            if value:
                fm[key] = value
                current_parent = None
                nested = {}
        else:
            # ネストされたキー（metadata 配下など）
            if value:
                nested[key] = value

    # 最後のネストを確定
    if nested and current_parent:
        fm[current_parent] = nested

    return fm, body


# ──────────────────────────────────────────────
# チェックルール定義
# ──────────────────────────────────────────────

# 予約語（スキル名に含めるべきでないワード）
_RESERVED_WORDS = {"anthropic"}

# 曖昧・過剰に汎用的なスキル名
_AMBIGUOUS_NAMES = {
    "helper", "utils", "tools", "documents", "data", "files",
    "misc", "common", "general", "utility", "support",
}

# 一人称を示すパターン（description に使うべきでない）
_FIRST_PERSON_PATTERNS = [
    r"お手伝いできます",
    r"お手伝いします",
    r"ご支援します",
    r"\bI can\b",
    r"\bYou can use this to\b",
    r"\bThis helps you\b",
]

# トリガーコンテキストを示すパターン（description に含むべき）
_TRIGGER_PATTERNS = [
    r"場合",
    r"とき",
    r"[Ww]hen",
    r"発動",
    r"などで",
    r"Use when",
    r"リクエストで",
    r"で使用する",
]

# スクリプト内のネットワーク呼び出しパターン
_NETWORK_PATTERNS = [
    r"\brequests\.(get|post|put|delete|patch|head|session)\b",
    r"\burllib\.request\b",
    r"\burllib\.urlopen\b",
    r"\bhttp\.client\b",
    r"\bhttpx\.",
    r"\baiohttp\.",
    r"\bfetch\s*\(",
    r"\bcurl\b",
    r"\bwget\b",
]


# ──────────────────────────────────────────────
# 個別チェック関数
# ──────────────────────────────────────────────

def check_name(name: str) -> list[dict]:
    issues = []

    # 予約語チェック
    for word in _RESERVED_WORDS:
        if word in name.lower():
            issues.append({
                "severity": "error",
                "code": "NAME_RESERVED_WORD",
                "message": f"name に予約語 '{word}' が含まれています",
            })

    # 曖昧な名前チェック
    name_parts = set(name.lower().replace("-", " ").split())
    if name.lower() in _AMBIGUOUS_NAMES or name_parts & _AMBIGUOUS_NAMES == name_parts:
        issues.append({
            "severity": "warning",
            "code": "NAME_AMBIGUOUS",
            "message": f"name '{name}' が曖昧または汎用的すぎます。より具体的な名前を推奨します",
        })

    return issues


def check_description(desc: str) -> list[dict]:
    issues = []

    # XML タグチェック
    if re.search(r"<[a-zA-Z/]", desc):
        issues.append({
            "severity": "error",
            "code": "DESC_XML_TAG",
            "message": "description に XML タグが含まれています",
        })

    # 一人称チェック
    for pattern in _FIRST_PERSON_PATTERNS:
        if re.search(pattern, desc):
            issues.append({
                "severity": "warning",
                "code": "DESC_FIRST_PERSON",
                "message": "description が一人称で書かれている可能性があります。三人称（「〜する」「〜を行う」）で記述してください",
            })
            break

    # トリガーコンテキスト不足チェック
    has_trigger = any(re.search(p, desc) for p in _TRIGGER_PATTERNS)
    if not has_trigger:
        issues.append({
            "severity": "warning",
            "code": "DESC_NO_TRIGGER",
            "message": "description にスキル発動のトリガー条件（「〜の場合」「〜とき」「〜などで発動」等）が含まれていません",
        })

    return issues


def check_metadata_version(fm: dict) -> list[dict]:
    issues = []
    metadata = fm.get("metadata")
    if not isinstance(metadata, dict):
        issues.append({
            "severity": "warning",
            "code": "META_NO_VERSION",
            "message": "metadata.version が未設定です。フロントマターに metadata: / version: \"1.0\" を追加してください",
        })
    elif "version" not in metadata:
        issues.append({
            "severity": "warning",
            "code": "META_NO_VERSION",
            "message": "metadata.version が未設定です",
        })
    return issues


def check_body(body: str, skill_dir: str) -> list[dict]:
    issues = []
    lines = body.splitlines()

    # 行数チェック（500 行超）
    if len(lines) > 500:
        issues.append({
            "severity": "warning",
            "code": "BODY_TOO_LONG",
            "message": f"SKILL.md 本文が {len(lines)} 行あります（推奨: 500 行以下）。references/ への分割を検討してください",
        })

    # Windows スタイルパスチェック
    if re.search(r'(?:scripts|references|assets)\\', body):
        issues.append({
            "severity": "warning",
            "code": "PATH_BACKSLASH",
            "message": "ファイルパスにバックスラッシュが使われています。フォワードスラッシュ（/）を使用してください",
        })

    # 参照ファイルのチェック
    ref_links = re.findall(r'\[.*?\]\(([\w./\-]+\.md)\)', body)
    checked_refs: set[str] = set()

    for ref in ref_links:
        if ref in checked_refs:
            continue
        checked_refs.add(ref)
        ref_path = os.path.join(skill_dir, ref)
        if not os.path.isfile(ref_path):
            continue

        with open(ref_path, encoding="utf-8", errors="replace") as f:
            ref_content = f.read()
        ref_lines = ref_content.splitlines()

        # 100 行以上で TOC なし
        if len(ref_lines) >= 100:
            has_toc = any(
                re.search(r'^#{1,3}\s*(目次|Contents?|Table of Contents)', line)
                for line in ref_lines[:20]
            )
            if not has_toc:
                issues.append({
                    "severity": "warning",
                    "code": "REF_NO_TOC",
                    "message": f"{ref} は {len(ref_lines)} 行ありますが先頭に目次（## 目次）がありません",
                })

        # ネスト参照チェック（参照先がさらに他の .md を参照）
        nested_refs = re.findall(r'\[.*?\]\(([\w./\-]+\.md)\)', ref_content)
        if nested_refs:
            issues.append({
                "severity": "warning",
                "code": "REF_NESTED",
                "message": f"{ref} がさらに他のファイルを参照しています（推奨: SKILL.md から 1 階層のみ）",
            })

    return issues


def check_scripts(skill_dir: str) -> list[dict]:
    issues = []
    scripts_dir = os.path.join(skill_dir, "scripts")
    if not os.path.isdir(scripts_dir):
        return issues

    for fname in sorted(os.listdir(scripts_dir)):
        if not fname.endswith((".py", ".sh")):
            continue
        fpath = os.path.join(scripts_dir, fname)
        with open(fpath, encoding="utf-8", errors="replace") as f:
            content = f.read()

        for pattern in _NETWORK_PATTERNS:
            if re.search(pattern, content):
                issues.append({
                    "severity": "warning",
                    "code": "SCRIPT_NETWORK",
                    "message": f"scripts/{fname} にネットワーク呼び出しの可能性があります（意図的な場合は無視してください）",
                })
                break

    return issues


# ──────────────────────────────────────────────
# メイン評価ロジック
# ──────────────────────────────────────────────

def check_skill(skill_dir: str) -> dict:
    """スキルディレクトリを検査して結果を返す。"""
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return {
            "name": os.path.basename(skill_dir),
            "errors": [{"severity": "error", "code": "NO_SKILL_MD", "message": "SKILL.md が見つかりません"}],
            "warnings": [],
        }

    with open(skill_md, encoding="utf-8") as f:
        content = f.read()

    fm, body = parse_frontmatter(content)
    all_issues: list[dict] = []

    name = fm.get("name", "")
    desc = fm.get("description", "")

    if name:
        all_issues.extend(check_name(name))
    if desc:
        all_issues.extend(check_description(desc))

    all_issues.extend(check_metadata_version(fm))
    all_issues.extend(check_body(body, skill_dir))
    all_issues.extend(check_scripts(skill_dir))

    errors = [i for i in all_issues if i["severity"] == "error"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]

    return {
        "name": name or os.path.basename(skill_dir),
        "errors": errors,
        "warnings": warnings,
    }


def find_skill_dirs(base_dir: str) -> list[str]:
    """ベースディレクトリ以下のスキルディレクトリを返す。"""
    if not os.path.isdir(base_dir):
        return []
    return [
        os.path.join(base_dir, entry)
        for entry in sorted(os.listdir(base_dir))
        if os.path.isdir(os.path.join(base_dir, entry))
        and os.path.isfile(os.path.join(base_dir, entry, "SKILL.md"))
    ]


def print_results(results: list[dict]) -> int:
    """結果を表示してエラー件数を返す。"""
    total_errors = 0
    total_warnings = 0

    for r in results:
        errors = r["errors"]
        warnings = r["warnings"]
        total_errors += len(errors)
        total_warnings += len(warnings)

        if not errors and not warnings:
            print(f"  ✅ {r['name']}")
            continue

        status = "❌" if errors else "⚠️ "
        print(f"  {status} {r['name']}")
        for e in errors:
            print(f"      [ERROR] {e['message']}")
        for w in warnings:
            print(f"      [WARN]  {w['message']}")

    print()
    print(f"合計: {len(results)} スキル / エラー {total_errors} 件 / 警告 {total_warnings} 件")
    return total_errors


def main() -> None:
    parser = argparse.ArgumentParser(description="スキルの静的品質チェック")
    parser.add_argument("--skill", help="特定スキルのみチェック（スキル名）")
    parser.add_argument(
        "--path",
        default=".github/skills",
        help="スキルのベースディレクトリ (default: .github/skills)",
    )
    args = parser.parse_args()

    print("🔍 スキル品質チェック\n")

    if args.skill:
        skill_dir = os.path.join(args.path, args.skill)
        if not os.path.isdir(skill_dir):
            home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
            skill_dir = os.path.join(home, ".copilot", "skills", args.skill)
            if not os.path.isdir(skill_dir):
                print(f"[ERROR] スキル '{args.skill}' が見つかりません")
                sys.exit(1)
        dirs = [skill_dir]
    else:
        dirs = find_skill_dirs(args.path)
        if not dirs:
            print(f"スキルが見つかりません: {args.path}")
            sys.exit(0)

    results = [check_skill(d) for d in dirs]
    error_count = print_results(results)
    sys.exit(1 if error_count > 0 else 0)


if __name__ == "__main__":
    main()
