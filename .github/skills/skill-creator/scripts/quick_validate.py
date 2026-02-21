#!/usr/bin/env python3
"""スキルのバリデーションスクリプト。

スキルディレクトリが要件を満たしているか検査する。

使い方:
    python quick_validate.py <path/to/skill-folder>
"""
from __future__ import annotations

import re
import sys
import os

try:
    import yaml
except ImportError:
    yaml = None


ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
}


def parse_frontmatter(content: str) -> dict | None:
    """YAML フロントマターをパースする。"""
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    raw = parts[1].strip()
    if not raw:
        return None
    if yaml:
        data = yaml.safe_load(raw)
    else:
        # yaml未インストール時の簡易パース
        data = {}
        for line in raw.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                data[key.strip()] = value.strip()
    return data if isinstance(data, dict) else None


SKILL_MD_MAX_LINES = 500
DESCRIPTION_MIN_CHARS = 20
DESCRIPTION_MAX_CHARS = 200


def validate_skill(skill_path: str) -> tuple[list[str], list[str]]:
    """スキルを検証してエラーリストと警告リストを返す。"""
    errors: list[str] = []
    warnings: list[str] = []

    skill_md = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md):
        errors.append("SKILL.md が見つかりません")
        return errors, warnings

    with open(skill_md, encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()

    # フロントマター検証
    fm = parse_frontmatter(content)
    if fm is None:
        errors.append("YAMLフロントマターが正しくフォーマットされていません（--- で囲む）")
        return errors, warnings

    # 許可されたキーの検査
    unknown_keys = set(fm.keys()) - ALLOWED_FRONTMATTER_KEYS
    if unknown_keys:
        errors.append(f"不明なフロントマターキー: {', '.join(sorted(unknown_keys))}")

    # name 検証
    name = fm.get("name")
    if not name:
        errors.append("'name' フィールドが必須です")
    elif not isinstance(name, str):
        errors.append("'name' は文字列である必要があります")
    else:
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
            errors.append(
                "'name' はkebab-case（小文字・数字・ハイフン、先頭末尾ハイフン不可、連続ハイフン不可）"
            )
        if len(name) > 64:
            errors.append("'name' は64文字以内にしてください")

    # description 検証
    desc = fm.get("description")
    if not desc:
        errors.append("'description' フィールドが必須です")
    elif not isinstance(desc, str):
        errors.append("'description' は文字列である必要があります")
    else:
        if "<" in desc or ">" in desc:
            errors.append("'description' に山括弧（< >）は使用できません")
        if len(desc) < DESCRIPTION_MIN_CHARS:
            errors.append(
                f"'description' は{DESCRIPTION_MIN_CHARS}文字以上にしてください（現在: {len(desc)}文字）"
            )
        if len(desc) > DESCRIPTION_MAX_CHARS:
            warnings.append(
                f"'description' が長すぎます（{len(desc)}文字 > 推奨{DESCRIPTION_MAX_CHARS}文字）。discover_skills のトークンを節約するため短くしてください"
            )
        if len(desc) > 1024:
            errors.append("'description' は1024文字以内にしてください")

    # compatibility 検証（任意）
    compat = fm.get("compatibility")
    if compat is not None:
        if not isinstance(compat, str):
            errors.append("'compatibility' は文字列である必要があります")
        elif len(compat) > 500:
            errors.append("'compatibility' は500文字以内にしてください")

    # SKILL.md 行数チェック
    line_count = len(lines)
    if line_count > SKILL_MD_MAX_LINES:
        warnings.append(
            f"SKILL.md が長すぎます（{line_count}行 > 推奨{SKILL_MD_MAX_LINES}行）。references/ への分割を検討してください"
        )
    elif line_count > int(SKILL_MD_MAX_LINES * 0.9):
        warnings.append(
            f"SKILL.md が制限の90%に達しています（{line_count}行 / {SKILL_MD_MAX_LINES}行）。references/ への分割を準備してください"
        )

    # references/ リンク整合性チェック
    refs_dir = os.path.join(skill_path, "references")
    if os.path.isdir(refs_dir):
        ref_files = {
            f for f in os.listdir(refs_dir)
            if os.path.isfile(os.path.join(refs_dir, f))
        }
        for ref_file in sorted(ref_files):
            if ref_file not in content:
                warnings.append(
                    f"references/{ref_file} が SKILL.md から参照されていません"
                )

    return errors, warnings


def main() -> None:
    if len(sys.argv) < 2:
        print("使い方: python quick_validate.py <path/to/skill-folder>")
        sys.exit(1)

    skill_path = sys.argv[1]

    if not os.path.isdir(skill_path):
        print(f"エラー: '{skill_path}' はディレクトリではありません")
        sys.exit(1)

    errors, warnings = validate_skill(skill_path)

    if warnings:
        print("警告:")
        for w in warnings:
            print(f"  ⚠ {w}")

    if errors:
        print("バリデーション失敗:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("バリデーション成功" + (" (警告あり)" if warnings else ""))
        sys.exit(0)


if __name__ == "__main__":
    main()
