#!/usr/bin/env python3
"""
discover_skills.py — 利用可能なスキルを走査して一覧を出力する。

対応探索ディレクトリ:
  1. ~/.copilot/skills/       (ユーザーホーム)
  2. <workspace>/.github/skills/  (ワークスペース優先)

Windows/macOS 両対応。

オプション:
  --group-by-category  カテゴリ別にグループ化して出力する
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


def find_skills_dirs() -> list[Path]:
    """探索対象のスキルディレクトリを返す（存在するものだけ）。"""
    candidates = []

    # ユーザーホーム
    if "AGENT_SKILLS_HOME" in os.environ:
        home_skills = Path(os.environ["AGENT_SKILLS_HOME"]) / "skills"
    else:
        legacy = Path.home() / ".copilot" / "skills"
        home_skills = legacy if legacy.is_dir() else Path.home() / ".agent-skills" / "skills"
    if home_skills.is_dir():
        candidates.append(home_skills)

    # このスクリプトの場所から .github/skills/ を特定
    # discover_skills.py -> scripts/ -> skill-selector/ -> skills/
    script_dir = Path(__file__).resolve().parent
    workspace_skills = script_dir.parent.parent
    if workspace_skills.is_dir() and workspace_skills.name == "skills":
        candidates.append(workspace_skills)

    return candidates


def parse_frontmatter(skill_md_path: Path) -> dict:
    """SKILL.md から YAML フロントマターの各フィールドを抽出する。

    抽出対象: name, description, metadata.category, metadata.tags, metadata.tier
    """
    try:
        content = skill_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    # --- で囲まれた YAML フロントマターを取得
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}

    yaml_text = match.group(1)

    result = {}

    # name
    name_match = re.search(r"^name:\s*(.+)$", yaml_text, re.MULTILINE)
    if name_match:
        result["name"] = name_match.group(1).strip().strip('"\'')

    # description は複数行になることがある（YAML 折り畳み）
    desc_match = re.search(
        r"^description:\s*(.+?)(?=\n\w|\nmetadata:|\Z)",
        yaml_text,
        re.DOTALL | re.MULTILINE,
    )
    if desc_match:
        raw_desc = desc_match.group(1).strip()
        # 複数行を 1 行に正規化（インデントと改行を除去）
        desc = re.sub(r"\s+", " ", raw_desc).strip().strip('"\'')
        result["description"] = desc

    # metadata ブロック（インデントされた行）を抽出
    metadata_match = re.search(
        r"^metadata:\s*\n((?:[ \t]+.+\n?)*)",
        yaml_text,
        re.MULTILINE,
    )
    if metadata_match:
        meta_text = metadata_match.group(1)

        # category
        cat_match = re.search(r"^\s+category:\s*(.+)$", meta_text, re.MULTILINE)
        if cat_match:
            result["category"] = cat_match.group(1).strip()

        # tier
        tier_match = re.search(r"^\s+tier:\s*(.+)$", meta_text, re.MULTILINE)
        if tier_match:
            result["tier"] = tier_match.group(1).strip()

        # tags（リスト形式: "    - tag-name"）
        tags_section = re.search(
            r"^\s+tags:\s*\n((?:\s+- .+\n?)*)",
            meta_text,
            re.MULTILINE,
        )
        if tags_section:
            tags = re.findall(r"^\s+- (.+)$", tags_section.group(1), re.MULTILINE)
            result["tags"] = [t.strip() for t in tags]

    return result


def discover_skills(skills_dirs: list[Path]) -> list[dict]:
    """スキルディレクトリから全スキルを探索する。重複は後勝ち（ワークスペース優先）。"""
    seen: dict[str, dict] = {}  # name -> skill_info

    for skills_dir in skills_dirs:
        for item in sorted(skills_dir.iterdir()):
            if not item.is_dir():
                continue
            skill_md = item / "SKILL.md"
            if not skill_md.is_file():
                continue

            info = parse_frontmatter(skill_md)
            name = info.get("name") or item.name
            seen[name] = {
                "name": name,
                "description": info.get("description", "(説明なし)"),
                "category": info.get("category", ""),
                "tags": info.get("tags", []),
                "tier": info.get("tier", ""),
                "path": str(skill_md),
                "source": str(skills_dir),
            }

    return sorted(seen.values(), key=lambda s: s["name"])


def print_skills_flat(skills: list[dict]) -> None:
    """スキル一覧をフラット形式で出力する。"""
    print(f"# 利用可能なスキル一覧 ({len(skills)} 件)\n")
    for skill in skills:
        print(f"## {skill['name']}")
        print(f"  {skill['description']}")
        if skill["category"]:
            print(f"  🏷️  category: {skill['category']}")
        if skill["tags"]:
            print(f"  🔖  tags: {', '.join(skill['tags'])}")
        if skill["tier"]:
            print(f"  ⭐  tier: {skill['tier']}")
        print(f"  📁 {skill['path']}")
        print()


def print_skills_by_category(skills: list[dict]) -> None:
    """スキル一覧をカテゴリ別グループ化で出力する。"""
    by_category: dict[str, list[dict]] = defaultdict(list)
    for skill in skills:
        category = skill["category"] or "uncategorized"
        by_category[category].append(skill)

    total = len(skills)
    print(f"# 利用可能なスキル一覧 ({total} 件、カテゴリ別)\n")
    for category in sorted(by_category.keys()):
        cat_skills = by_category[category]
        print(f"## カテゴリ: {category} ({len(cat_skills)} 件)\n")
        for skill in cat_skills:
            tags_str = f"  [tags: {', '.join(skill['tags'])}]" if skill["tags"] else ""
            tier_str = f"  [tier: {skill['tier']}]" if skill["tier"] else ""
            print(f"  - **{skill['name']}**{tags_str}{tier_str}")
            print(f"    {skill['description']}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="利用可能なスキルを走査して一覧を出力する。"
    )
    parser.add_argument(
        "--group-by-category",
        action="store_true",
        help="カテゴリ別にグループ化して出力する",
    )
    args = parser.parse_args()

    skills_dirs = find_skills_dirs()

    if not skills_dirs:
        print("スキルディレクトリが見つかりませんでした。", file=sys.stderr)
        print("探索対象: ~/.copilot/skills/  および  .github/skills/", file=sys.stderr)
        sys.exit(1)

    skills = discover_skills(skills_dirs)

    if not skills:
        print("スキルが見つかりませんでした。", file=sys.stderr)
        sys.exit(1)

    if args.group_by_category:
        print_skills_by_category(skills)
    else:
        print_skills_flat(skills)


if __name__ == "__main__":
    main()
