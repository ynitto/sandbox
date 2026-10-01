#!/usr/bin/env python3
"""スキルカタログ (skill-catalog.json) を自動生成する。

全スキルの SKILL.md フロントマターと本文から主要メタデータを抽出し、
機械可読な JSON カタログを生成する。

使い方:
    python generate_skill_catalog.py                          # 標準出力に出力
    python generate_skill_catalog.py -o skill-catalog.json    # ファイルに出力
    python generate_skill_catalog.py --path /path/to/skills   # パス指定

リポジトリにコミットしてある generated/skill-catalog.json の更新と鮮度検査は
`tools/ci/check_generated_skill_catalog.py`（`--write` / 引数なしで検査）が行う。
そちらはスキルの列挙を install.py の `_discover_all_skills` に任せ、その名前の並びを
`generate_catalog(skills_dir, skill_names)` に渡す。この単体の既定の列挙
（`discover_skills`）は、install.py の無い配布先でも動くための写しで、規則は
install.py と同じ「SKILL.md を持つディレクトリ、名前順」に揃えてある。

出力は入力（SKILL.md とディレクトリ構成）だけで決まる。時刻・環境・ファイルシステムの
列挙順に依存する値は入れない。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """YAML フロントマターを簡易パースする（PyYAML 不要）。"""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    fm_text = parts[1].strip()
    body = parts[2]
    result: dict = {}
    current_parent = ""
    current_list_key = ""

    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # ブロックリストアイテム: "    - value"
        if stripped.startswith("- ") and current_list_key and current_parent:
            val = stripped[2:].strip().strip("\"'")
            target = result.get(current_parent, {})
            if isinstance(target, dict) and isinstance(target.get(current_list_key), list):
                target[current_list_key].append(val)
            continue

        # リストアイテムの連続が終わったらリセット
        current_list_key = ""

        if indent == 0 and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val:
                result[key] = val
            else:
                result[key] = {}
                current_parent = key
        elif indent > 0 and ":" in stripped and current_parent:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if isinstance(result.get(current_parent), dict):
                if val:
                    # フロー形式リスト: [item1, item2]
                    if val.startswith("[") and val.endswith("]"):
                        items = [
                            item.strip().strip("\"'")
                            for item in val[1:-1].split(",")
                            if item.strip()
                        ]
                        result[current_parent][key] = items
                    else:
                        result[current_parent][key] = val
                else:
                    # 値なし → ブロックリストの開始の可能性
                    result[current_parent][key] = []
                    current_list_key = key

    return result, body


def extract_triggers(description: str) -> list[str]:
    """description フィールドからトリガーフレーズを抽出する。"""
    triggers = []
    # 「〜」で囲まれたフレーズを抽出
    for match in re.finditer(r"「([^」]+)」", description):
        triggers.append(match.group(1))
    return triggers


def _has_tracked_files(path: str) -> bool:
    """ディレクトリに中身があるか。実行の副産物（__pycache__ や隠しファイル）は数えない。

    手元で一度テストを回しただけの scripts/ が「中身あり」に化けると、
    同じコミットから生成した結果が環境によって変わってしまうため。
    """
    if not os.path.isdir(path):
        return False
    return any(not e.startswith(".") and e != "__pycache__" for e in os.listdir(path))


def discover_skills(skills_dir: str) -> list[str]:
    """SKILL.md を持つスキルを名前順に返す（install.py の `_discover_all_skills` と同じ規則）。"""
    if not os.path.isdir(skills_dir):
        return []
    return [name for name in sorted(os.listdir(skills_dir))
            if os.path.isfile(os.path.join(skills_dir, name, "SKILL.md"))]


def build_skill_entry(skill_dir: str, skill_name: str) -> dict | None:
    """1 スキルのカタログエントリを構築する。"""
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None

    with open(skill_md, "r", encoding="utf-8") as f:
        content = f.read()

    fm, body = parse_frontmatter(content)
    metadata = fm.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    description = fm.get("description", "")
    triggers = extract_triggers(description)

    has_scripts = _has_tracked_files(os.path.join(skill_dir, "scripts"))
    has_references = _has_tracked_files(os.path.join(skill_dir, "references"))

    entry: dict = {
        "name": fm.get("name", skill_name),
        "description": description,
        "version": metadata.get("version", "unknown"),
        "tier": metadata.get("tier", "unknown"),
        "category": metadata.get("category", "unknown"),
        "tags": metadata.get("tags", []),
        "triggers": triggers,
        "has_scripts": has_scripts,
        "has_references": has_references,
    }

    return entry


def generate_catalog(skills_dir: str, skill_names: list[str] | None = None) -> dict:
    """全スキルのカタログを生成する。

    skill_names を渡すとその名前だけをその順で載せる（CI は install.py の列挙を渡す）。
    省略時は discover_skills() で列挙する。
    """
    if skill_names is None:
        skill_names = discover_skills(skills_dir)
    skills = []

    for name in skill_names:
        skill_entry = build_skill_entry(os.path.join(skills_dir, name), name)
        if skill_entry:
            skills.append(skill_entry)

    # カテゴリ別統計
    categories: dict[str, int] = {}
    tiers: dict[str, int] = {}
    for s in skills:
        cat = s.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1
        tier = s.get("tier", "unknown")
        tiers[tier] = tiers.get(tier, 0) + 1

    return {
        "generated_by": "generate_skill_catalog.py",
        "total_skills": len(skills),
        "categories": categories,
        "tiers": tiers,
        "skills": skills,
    }


def render_catalog(catalog: dict) -> str:
    """カタログをファイルに書く形の文字列にする（末尾改行つき）。"""
    return json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="スキルカタログ生成")
    parser.add_argument(
        "--path",
        default=os.path.join(os.path.dirname(__file__), "..", ".."),
        help="スキルディレクトリの親パス (デフォルト: .github/skills/)",
    )
    parser.add_argument("-o", "--output", help="出力ファイルパス")
    args = parser.parse_args()

    skills_dir = os.path.realpath(args.path)
    catalog = generate_catalog(skills_dir)

    output = render_catalog(catalog)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(output)
        print(f"✅ カタログ生成完了: {args.output} ({catalog['total_skills']} スキル)")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
