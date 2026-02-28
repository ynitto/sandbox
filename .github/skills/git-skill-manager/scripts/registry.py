#!/usr/bin/env python3
"""レジストリの読み書き・マイグレーション・有効判定。

他のスクリプトから共通で使う基盤モジュール。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys


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
    "requirements-definer", "skill-recruiter", "skill-evaluator",
    "generating-skills-from-copilot-logs", "sprint-reviewer", "codebase-to-skill",
]


def migrate_registry(reg: dict) -> dict:
    """最新 version へのマイグレーション。"""
    version = reg.get("version", 1)

    # v1 → v2
    if version < 2:
        for repo in reg.get("repositories", []):
            repo.setdefault("priority", 100)
        for skill in reg.get("installed_skills", []):
            skill.setdefault("enabled", True)
            skill.setdefault("pinned_commit", None)
        reg.setdefault("core_skills", list(CORE_SKILLS_DEFAULT))
        reg.setdefault("profiles", {"default": ["*"]})
        reg.setdefault("active_profile", None)
        reg.setdefault("remote_index", {})

    # v2 → v3: feedback_history / pending_refinement を追加
    if version < 3:
        for skill in reg.get("installed_skills", []):
            skill.setdefault("feedback_history", [])
            skill.setdefault("pending_refinement", False)

    # v3 → v4: auto_update 設定を追加
    if version < 4:
        reg.setdefault("auto_update", {
            "enabled": False,
            "interval_hours": 24,
            "notify_only": True,
            "last_checked_at": None,
        })

    # v4 → v5: ノードフェデレーション機能を追加
    if version < 5:
        # ノードアイデンティティ
        reg.setdefault("node", {
            "id": None,       # node_identity.py で生成
            "name": None,
            "created_at": None,
        })
        # 昇格ポリシー（何を中央にあげるかの基準）
        # require_local_modified はデフォルト False: push は気軽に行える
        reg.setdefault("promotion_policy", {
            "min_ok_count": 3,
            "max_problem_rate": 0.1,
            "require_local_modified": False,
            "auto_pr": False,
            "notify_on_eligible": True,
        })
        # 選択的同期ポリシー（中央→ノード方向の制御）
        reg.setdefault("sync_policy", {
            "auto_accept_patch": True,
            "auto_accept_minor": False,
            "protect_local_modified": True,
        })
        # 貢献キュー（昇格候補のステージング）
        reg.setdefault("contribution_queue", [])
        # 各スキルに系譜・バージョン・メトリクスを追加
        for skill in reg.get("installed_skills", []):
            skill.setdefault("version", None)
            skill.setdefault("central_version", None)
            skill.setdefault("version_ahead", False)
            skill.setdefault("lineage", {
                "origin_repo": skill.get("source_repo"),
                "origin_commit": skill.get("commit_hash"),
                "origin_version": None,
                "local_modified": False,
                "diverged_at": None,
                "local_changes_summary": "",
            })
            skill.setdefault("metrics", {
                "total_executions": 0,
                "ok_rate": None,
                "last_executed_at": None,
                "central_ok_rate": None,
            })

    # usage_stats と skill_discovery を全バージョンから除去（使用記録機能削除）
    for skill in reg.get("installed_skills", []):
        skill.pop("usage_stats", None)
    reg.pop("skill_discovery", None)

    reg["version"] = 5
    return reg


def load_registry() -> dict:
    path = _registry_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
        return migrate_registry(reg)
    return {
        "version": 5,
        "node": {
            "id": None,
            "name": None,
            "created_at": None,
        },
        "repositories": [],
        "installed_skills": [],
        "core_skills": list(CORE_SKILLS_DEFAULT),
        "remote_index": {},
        "profiles": {"default": ["*"]},
        "active_profile": None,
        "auto_update": {
            "enabled": False,
            "interval_hours": 24,
            "notify_only": True,
            "last_checked_at": None,
        },
        "promotion_policy": {
            "min_ok_count": 3,
            "max_problem_rate": 0.1,
            "require_local_modified": False,
            "auto_pr": False,
            "notify_on_eligible": True,
        },
        "sync_policy": {
            "auto_accept_patch": True,
            "auto_accept_minor": False,
            "protect_local_modified": True,
        },
        "contribution_queue": [],
    }


def save_registry(reg: dict) -> None:
    path = _registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)


def _vscode_mcp_path() -> str | None:
    """VS Code ユーザーレベルの mcp.json パスを返す。"""
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Code", "User", "mcp.json")
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        base = appdata if appdata else os.path.join(home, "AppData", "Roaming")
        return os.path.join(base, "Code", "User", "mcp.json")
    else:  # Linux
        return os.path.join(home, ".config", "Code", "User", "mcp.json")


def merge_mcp_config(src_cfg: dict, project_path: str) -> str | None:
    """mcp.json をユーザーレベルの VS Code 設定にマージする。

    src_cfg: .vscode/mcp.json の内容 (dict)
    project_path: $(pwd) を置換するプロジェクトの絶対パス
    戻り値: 書き込み先パス、またはスキップ時 None
    """
    dest = _vscode_mcp_path()
    if not dest:
        return None

    # $(pwd) をプロジェクトパスに置換
    src_str = json.dumps(src_cfg)
    src_str = src_str.replace("$(pwd)", project_path.replace("\\", "/"))
    merged_servers = json.loads(src_str).get("servers", {})

    # 既存 mcp.json と統合
    if os.path.isfile(dest):
        with open(dest, encoding="utf-8") as f:
            try:
                dest_cfg = json.load(f)
            except json.JSONDecodeError:
                dest_cfg = {}
    else:
        dest_cfg = {}

    dest_cfg.setdefault("servers", {})
    dest_cfg["servers"].update(merged_servers)

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(dest_cfg, f, indent=4, ensure_ascii=False)

    return dest


def _vscode_settings_path() -> str | None:
    """VS Code ユーザーレベルの settings.json パスを返す。"""
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Code", "User", "settings.json")
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        base = appdata if appdata else os.path.join(home, "AppData", "Roaming")
        return os.path.join(base, "Code", "User", "settings.json")
    else:  # Linux
        return os.path.join(home, ".config", "Code", "User", "settings.json")


def _parse_jsonc(text: str) -> dict:
    """JSONC（コメント付き JSON）をパースする。パース失敗時は空 dict を返す。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # ブロックコメントと行コメントを除去して再試行
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    stripped = re.sub(r"//[^\n]*", "", stripped)
    # 末尾カンマを除去 (}, ])
    stripped = re.sub(r",\s*([}\]])", r"\1", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return {}


def set_vscode_autostart_mcp() -> str | None:
    """VS Code の settings.json に chat.mcp.autostart: true を設定する。

    既に設定済みの場合はスキップする。
    戻り値: 書き込み先パス、またはスキップ時 None
    """
    dest = _vscode_settings_path()
    if not dest:
        return None

    if os.path.isfile(dest):
        with open(dest, encoding="utf-8") as f:
            raw = f.read()
        settings = _parse_jsonc(raw)
    else:
        settings = {}

    if settings.get("chat.mcp.autostart") is True:
        return None  # 既に設定済み

    settings["chat.mcp.autostart"] = True

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

    return dest


def _is_uv_required(servers: dict) -> bool:
    """サーバー設定に uvx/uv コマンドが含まれるか確認する。"""
    return any(
        v.get("command") in ("uv", "uvx")
        for v in servers.values()
        if isinstance(v, dict)
    )


def _check_uv_installed() -> bool:
    """uv/uvx がインストール済みかどうかを確認する。"""
    return shutil.which("uvx") is not None or shutil.which("uv") is not None


def _get_new_mcp_servers(src_servers: dict, dest_path: str) -> dict:
    """新規追加される MCP サーバーを返す（既存 mcp.json に存在しないもの）。"""
    if dest_path and os.path.isfile(dest_path):
        try:
            with open(dest_path, encoding="utf-8") as f:
                existing = json.load(f)
            existing_servers = existing.get("servers", {})
        except (json.JSONDecodeError, OSError):
            existing_servers = {}
    else:
        existing_servers = {}
    return {k: v for k, v in src_servers.items() if k not in existing_servers}


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
