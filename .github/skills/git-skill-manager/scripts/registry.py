#!/usr/bin/env python3
"""レジストリの読み書き・マイグレーション・有効判定。

他のスクリプトから共通で使う基盤モジュール。
"""
from __future__ import annotations

import json
import os


def _registry_path() -> str:
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".copilot", "skill-registry.json")


def _skill_home() -> str:
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".copilot", "skills")


def _cache_dir() -> str:
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".copilot", "cache")


CORE_SKILLS_DEFAULT = [
    "scrum-master", "git-skill-manager", "skill-creator",
    "sprint-reviewer", "codebase-to-skill",
]


def migrate_registry(reg: dict) -> dict:
    """version 1-2 → 3 へのマイグレーション。"""
    version = reg.get("version", 1)

    # v1 → v2
    if version < 2:
        for repo in reg.get("repositories", []):
            repo.setdefault("priority", 100)
        for skill in reg.get("installed_skills", []):
            skill.setdefault("enabled", True)
            skill.setdefault("pinned_commit", None)
            skill.setdefault("usage_stats", None)
        reg.setdefault("core_skills", list(CORE_SKILLS_DEFAULT))
        reg.setdefault("profiles", {"default": ["*"]})
        reg.setdefault("active_profile", None)
        reg.setdefault("remote_index", {})

    # v2 → v3: usage_stats を feedback_history に移行、skill_discovery を追加
    if version < 3:
        for skill in reg.get("installed_skills", []):
            # usage_stats を削除し feedback_history を初期化
            skill.pop("usage_stats", None)
            skill.setdefault("feedback_history", [])
            skill.setdefault("pending_refinement", False)
        reg.setdefault("skill_discovery", {
            "last_run_at": None,
            "suggest_interval_days": 7,
        })

    reg["version"] = 3
    return reg


def load_registry() -> dict:
    path = _registry_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
        return migrate_registry(reg)
    return {
        "version": 3,
        "repositories": [],
        "installed_skills": [],
        "core_skills": list(CORE_SKILLS_DEFAULT),
        "remote_index": {},
        "profiles": {"default": ["*"]},
        "active_profile": None,
        "skill_discovery": {
            "last_run_at": None,
            "suggest_interval_days": 7,
        },
    }


def save_registry(reg: dict) -> None:
    path = _registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)


def is_skill_enabled(skill_name: str, reg: dict) -> bool:
    """スキルが有効かどうかを判定する。プロファイルと enabled フラグの両方を考慮。"""
    active_profile = reg.get("active_profile")
    profiles = reg.get("profiles", {})

    if active_profile and active_profile in profiles:
        profile_skills = profiles[active_profile]
        if "*" not in profile_skills and skill_name not in profile_skills:
            return False

    skill_info = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if skill_info and not skill_info.get("enabled", True):
        return False

    return True
