#!/usr/bin/env python3
"""
discover_skills.py — 利用可能なスキルを走査して一覧を出力する。

対応探索ディレクトリ:
  1. ~/.copilot/skills/       (ユーザーホーム)
  2. <workspace>/.github/skills/  (ワークスペース優先)

Windows/macOS 両対応。
"""

import os
import sys
import re
from pathlib import Path


def find_skills_dirs() -> list[Path]:
    """探索対象のスキルディレクトリを返す（存在するものだけ）。"""
    candidates = []

    # ユーザーホーム
    home_skills = Path.home() / ".copilot" / "skills"
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
    """SKILL.md から YAML フロントマターの name と description を抽出する。"""
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
    # シンプルな key: value 抽出（ネストや複数行 description に対応）
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
                "path": str(skill_md),
                "source": str(skills_dir),
            }

    return sorted(seen.values(), key=lambda s: s["name"])


def main():
    skills_dirs = find_skills_dirs()

    if not skills_dirs:
        print("スキルディレクトリが見つかりませんでした。", file=sys.stderr)
        print("探索対象: ~/.copilot/skills/  および  .github/skills/", file=sys.stderr)
        sys.exit(1)

    skills = discover_skills(skills_dirs)

    if not skills:
        print("スキルが見つかりませんでした。", file=sys.stderr)
        sys.exit(1)

    print(f"# 利用可能なスキル一覧 ({len(skills)} 件)\n")
    for skill in skills:
        print(f"## {skill['name']}")
        print(f"  {skill['description']}")
        print(f"  📁 {skill['path']}")
        print()


if __name__ == "__main__":
    main()
