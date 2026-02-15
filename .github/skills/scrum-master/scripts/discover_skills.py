#!/usr/bin/env python3
"""スキル探索スクリプト。

.github/skills/ 配下の全スキルを走査し、メタデータ一覧をJSON出力する。
scrum-master自身は一覧から除外する。
レジストリの enabled / プロファイル設定に基づき、無効なスキルを除外できる。

使い方:
    python discover_skills.py [skills-directory] [--registry path/to/skill-registry.json]

デフォルト: .github/skills/
"""

import json
import os
import sys

try:
    import yaml
except ImportError:
    yaml = None


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
        data = {}
        current_key = None
        current_value_lines = []
        for line in raw.splitlines():
            if line and not line[0].isspace() and ":" in line:
                if current_key is not None:
                    data[current_key] = " ".join(current_value_lines).strip()
                key, _, value = line.partition(":")
                current_key = key.strip()
                current_value_lines = [value.strip()] if value.strip() else []
            elif current_key is not None:
                current_value_lines.append(line.strip())
        if current_key is not None:
            data[current_key] = " ".join(current_value_lines).strip()
    return data if isinstance(data, dict) else None


def list_dir_files(path: str) -> list[str]:
    """ディレクトリ内のファイル名一覧を返す。存在しなければ空リスト。"""
    if not os.path.isdir(path):
        return []
    return sorted(
        f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))
    )


def load_registry(registry_path: str) -> dict | None:
    """レジストリJSONを読み込む。存在しなければ None を返す。"""
    if not os.path.isfile(registry_path):
        return None
    with open(registry_path, encoding="utf-8") as f:
        return json.load(f)


def is_skill_enabled(skill_name: str, registry: dict | None) -> bool:
    """レジストリの enabled フラグとアクティブプロファイルに基づき有効判定する。"""
    if registry is None:
        return True

    # プロファイルによるフィルタ
    active_profile = registry.get("active_profile")
    profiles = registry.get("profiles", {})
    if active_profile and active_profile in profiles:
        profile_skills = profiles[active_profile]
        if "*" not in profile_skills and skill_name not in profile_skills:
            return False

    # 個別の enabled フラグ
    for skill in registry.get("installed_skills", []):
        if skill.get("name") == skill_name:
            return skill.get("enabled", True)

    return True


def get_usage_stats(skill_name: str, registry: dict | None) -> dict:
    """レジストリからスキルの usage_stats を取得する。"""
    if registry is None:
        return {}
    for skill in registry.get("installed_skills", []):
        if skill.get("name") == skill_name:
            return skill.get("usage_stats") or {}
    return {}


def skill_sort_key(
    skill: dict, core_skills: list[str], registry: dict | None
) -> tuple:
    """スキルのソートキーを生成する。

    優先度:
    1. コアスキル (core_skills に含まれる) → 常に先頭
    2. usage_stats.total_count 降順 → よく使うスキルほど上位
    3. usage_stats.last_used_at 降順 → 最近使ったものが上位
    4. 名前順
    """
    name = skill["name"]
    is_core = 0 if name in core_skills else 1
    stats = get_usage_stats(name, registry)
    total = -(stats.get("total_count", 0))
    last_used = stats.get("last_used_at") or ""
    # last_used を降順にするため反転（空文字は最後尾）
    last_used_inv = "" if not last_used else last_used
    return (is_core, total, last_used_inv, name)


def discover_skills(
    skills_dir: str, registry: dict | None = None
) -> list[dict]:
    """スキルディレクトリを走査してメタデータ一覧を返す。

    registry が指定された場合、enabled=false のスキルや
    アクティブプロファイル外のスキルを除外する。
    結果はコアスキル優先・使用頻度順にソートされる。
    """
    skills = []

    if not os.path.isdir(skills_dir):
        return skills

    for entry in sorted(os.listdir(skills_dir)):
        skill_path = os.path.join(skills_dir, entry)
        if not os.path.isdir(skill_path):
            continue

        # scrum-master自身を除外
        if entry == "scrum-master":
            continue

        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue

        with open(skill_md, encoding="utf-8") as f:
            content = f.read()

        fm = parse_frontmatter(content)
        if fm is None:
            continue

        name = fm.get("name", entry)

        # レジストリの enabled / プロファイル判定
        if not is_skill_enabled(name, registry):
            continue

        description = fm.get("description", "")

        skills.append(
            {
                "name": name,
                "description": description,
                "path": skill_path,
                "skill_md": skill_md,
                "resources": {
                    "scripts": list_dir_files(os.path.join(skill_path, "scripts")),
                    "references": list_dir_files(
                        os.path.join(skill_path, "references")
                    ),
                    "assets": list_dir_files(os.path.join(skill_path, "assets")),
                },
            }
        )

    # コアスキル優先 + 使用頻度順にソート
    core_skills = (registry or {}).get("core_skills", [])
    skills.sort(key=lambda s: skill_sort_key(s, core_skills, registry))

    return skills


def main() -> None:
    skills_dir = sys.argv[1] if len(sys.argv) > 1 else ".github/skills"

    # --registry オプション対応
    registry = None
    for i, arg in enumerate(sys.argv):
        if arg == "--registry" and i + 1 < len(sys.argv):
            registry = load_registry(sys.argv[i + 1])
            break

    skills = discover_skills(skills_dir, registry=registry)
    print(json.dumps(skills, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
