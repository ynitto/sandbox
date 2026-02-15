#!/usr/bin/env python3
"""スキル使用回数を記録する。

使い方: python record_usage.py <skill-name>

レジストリの installed_skills[].usage_stats を更新する。
レジストリが存在しない場合は何もしない（エラーにしない）。
"""
import json
import os
import sys
from datetime import datetime


def main():
    if len(sys.argv) < 2:
        print("Usage: python record_usage.py <skill-name>")
        sys.exit(1)

    skill_name = sys.argv[1]
    registry_path = os.path.join(
        os.environ.get("USERPROFILE", os.path.expanduser("~")),
        ".copilot",
        "skill-registry.json",
    )

    if not os.path.isfile(registry_path):
        return

    with open(registry_path, encoding="utf-8") as f:
        reg = json.load(f)

    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        return

    stats = skill.get("usage_stats") or {"total_count": 0, "last_used_at": None}
    stats["total_count"] = stats.get("total_count", 0) + 1
    stats["last_used_at"] = datetime.now().isoformat()
    skill["usage_stats"] = stats

    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)

    print(f"📊 {skill_name}: 使用回数 {stats['total_count']}")


if __name__ == "__main__":
    main()
