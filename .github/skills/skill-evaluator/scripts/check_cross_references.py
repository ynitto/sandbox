#!/usr/bin/env python3
"""スキル間のクロスリファレンス整合性を検証する。

SKILL.md 内で言及されている他スキル名が実在するかを検証し、
壊れた参照や非推奨スキルへの参照を検出する。

検出戦略:
  1. バッククォートで囲まれた kebab-case 名を抽出（`skill-name`）
  2. 委譲パターン（「→ skill-name」「skill-name に委譲」等）を抽出
  3. 抽出した名前を既知スキル一覧と照合

使い方:
    python check_cross_references.py                     # 全スキルをチェック
    python check_cross_references.py --skill <name>      # 特定スキルのみ
    python check_cross_references.py --json              # JSON 形式で出力
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# バッククォート内の kebab-case 名: `skill-name`
BACKTICK_REF_RE = re.compile(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)+)`")

# 委譲パターン: "→ skill-name", "skill-name に委譲", "skill-name スキル"
DELEGATION_PATTERNS = [
    re.compile(r"→\s*([a-z][a-z0-9]*(?:-[a-z0-9]+)+)"),
    re.compile(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s*に委譲"),
    re.compile(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s*スキル"),
    re.compile(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s*を使う"),
    re.compile(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s*を参照"),
]


def discover_skills(skills_dir: str) -> dict[str, dict]:
    """スキルディレクトリをスキャンし、名前→メタデータのマッピングを返す。"""
    skills = {}
    if not os.path.isdir(skills_dir):
        return skills

    for entry in sorted(os.listdir(skills_dir)):
        if entry.startswith(("_", ".")):
            continue
        skill_path = os.path.join(skills_dir, entry)
        if not os.path.isdir(skill_path):
            continue
        skill_md = os.path.join(skill_path, "SKILL.md")
        tier = None
        if os.path.isfile(skill_md):
            with open(skill_md, "r", encoding="utf-8") as f:
                content = f.read()
            # tier を抽出
            m = re.search(r"tier:\s*(\S+)", content)
            if m:
                tier = m.group(1)
        skills[entry] = {"path": skill_path, "tier": tier}

    return skills


def extract_references(content: str, skill_name: str, all_skill_names: set[str]) -> list[str]:
    """SKILL.md の本文からスキル名の参照を抽出する。

    検出方法:
      1. バッククォート内の kebab-case 名で既知スキル名と一致するもの
      2. 委譲パターンで既知スキル名と一致するもの
      3. 上記いずれかで既知スキル名と一致しないが、パターン的にスキル名の可能性が高いもの
    """
    # フロントマターを除外
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
        else:
            body = content
    else:
        body = content

    found = set()

    # バッククォート内の参照を抽出
    for match in BACKTICK_REF_RE.finditer(body):
        ref = match.group(1)
        if ref != skill_name:
            found.add(ref)

    # 委譲パターンの参照を抽出
    for pattern in DELEGATION_PATTERNS:
        for match in pattern.finditer(body):
            ref = match.group(1)
            if ref != skill_name:
                found.add(ref)

    # 既知スキル名と一致するもの、または潜在的な参照のみ返す
    # （ツール名やパラメータ名等の誤検出を減らすため、既知スキル名一覧でフィルタ）
    relevant = set()
    for ref in found:
        if ref in all_skill_names:
            relevant.add(ref)
        # 委譲パターンで見つかった場合は未知でも含める（壊れた参照の可能性）
        elif any(pattern.search(body) and ref in [m.group(1) for m in pattern.finditer(body)] for pattern in DELEGATION_PATTERNS):
            relevant.add(ref)

    return sorted(relevant)


def check_skill(
    skill_name: str,
    skills_dir: str,
    all_skills: dict[str, dict],
) -> list[dict]:
    """1 スキルのクロスリファレンスをチェックする。"""
    skill_md = os.path.join(skills_dir, skill_name, "SKILL.md")
    if not os.path.isfile(skill_md):
        return []

    with open(skill_md, "r", encoding="utf-8") as f:
        content = f.read()

    refs = extract_references(content, skill_name, set(all_skills.keys()))
    issues = []

    for ref in refs:
        if ref not in all_skills:
            issues.append({
                "skill": skill_name,
                "reference": ref,
                "level": "WARN",
                "code": "XREF_BROKEN",
                "message": f"参照先スキル '{ref}' が存在しません",
            })
        elif all_skills[ref].get("tier") == "deprecated":
            issues.append({
                "skill": skill_name,
                "reference": ref,
                "level": "INFO",
                "code": "XREF_DEPRECATED",
                "message": f"参照先スキル '{ref}' は非推奨です",
            })

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="スキル間クロスリファレンス検証")
    parser.add_argument("--skill", help="特定スキルのみチェック")
    parser.add_argument(
        "--path",
        default=os.path.join(os.path.dirname(__file__), "..", ".."),
        help="スキルディレクトリの親パス (デフォルト: .github/skills/)",
    )
    parser.add_argument("--json", action="store_true", help="JSON 形式で出力")
    args = parser.parse_args()

    skills_dir = os.path.realpath(args.path)
    all_skills = discover_skills(skills_dir)

    if not all_skills:
        print(f"スキルが見つかりません: {skills_dir}", file=sys.stderr)
        sys.exit(1)

    all_issues: list[dict] = []

    if args.skill:
        if args.skill not in all_skills:
            print(f"スキル '{args.skill}' が見つかりません", file=sys.stderr)
            sys.exit(1)
        all_issues = check_skill(args.skill, skills_dir, all_skills)
    else:
        for name in all_skills:
            all_issues.extend(check_skill(name, skills_dir, all_skills))

    # 出力
    if args.json:
        print(json.dumps(all_issues, ensure_ascii=False, indent=2))
    else:
        if not all_issues:
            print(f"✅ クロスリファレンス検証完了: 問題なし ({len(all_skills)} スキル)")
        else:
            warn_count = sum(1 for i in all_issues if i["level"] == "WARN")
            info_count = sum(1 for i in all_issues if i["level"] == "INFO")
            print(f"クロスリファレンス検証: WARN={warn_count}, INFO={info_count}\n")
            for issue in all_issues:
                icon = "⚠️" if issue["level"] == "WARN" else "ℹ️"
                print(f"  {icon} [{issue['code']}] {issue['skill']} → {issue['reference']}")
                print(f"     {issue['message']}")

    sys.exit(1 if any(i["level"] == "WARN" for i in all_issues) else 0)


if __name__ == "__main__":
    main()
