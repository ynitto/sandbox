#!/usr/bin/env python3
"""レジストリの読み書き・マイグレーション・有効判定。

他のスクリプトから共通で使う基盤モジュール。
"""
from __future__ import annotations

import json
import os
import re

# エージェント種別とインストール先ディレクトリ名のマッピング
AGENT_DIRS: dict[str, str] = {
    "copilot": ".copilot",
    "claude": ".claude",
    "codex": ".codex",
    "kiro": ".kiro",
}


def _user_home() -> str:
    """ユーザーホームディレクトリを返す。"""
    return os.environ.get("USERPROFILE", os.path.expanduser("~"))


def _agent_home() -> str:
    """エージェントホームディレクトリを返す。

    通常は __file__ の位置から導出する:
        {agent_home}/skills/git-skill-manager/scripts/registry.py

    スクリプトが正規のエージェントディレクトリ外に置かれている場合
    （ワークスペース・テスト環境など）は USERPROFILE + "/.copilot" へフォールバックする。
    """
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.dirname(      # agent_home/
        os.path.dirname(         # skills/
            os.path.dirname(     # git-skill-manager/
                scripts_dir      # scripts/
            )
        )
    )
    # 正規エージェントディレクトリ名かどうかを確認（.copilot, .claude, .codex, .kiro）
    if os.path.basename(candidate) in AGENT_DIRS.values():
        return candidate
    # ワークスペース・テスト実行時は USERPROFILE から導出する
    return os.path.join(_user_home(), ".copilot")


def _registry_path() -> str:
    """skill-registry.json のパスを __file__ の位置から導出する。"""
    return os.path.join(_agent_home(), "skill-registry.json")


def _skill_home() -> str:
    """スキルインストール先ディレクトリを返す。"""
    return os.path.join(_agent_home(), "skills")


def _cache_dir() -> str:
    """キャッシュディレクトリを __file__ の位置から導出する。"""
    return os.path.join(_agent_home(), "cache")


def _instructions_home() -> str:
    try:
        reg = load_registry()
        if reg.get("agent_type") == "kiro":
            return os.path.join(_agent_home(), "steering")
    except Exception:
        pass
    return os.path.join(_agent_home(), "instructions")


def _transform_frontmatter_for_kiro(content: str) -> str:
    """Kiro steering 向けにフロントマターの applyTo を inclusion に変換する。

    - applyTo: "**"  → inclusion: always
    - applyTo: "<pattern>"  → inclusion: fileMatch
                               fileMatchPattern: "<pattern>"
    """
    fm_match = re.match(r'^(---[ \t]*\n)(.*?)(\n---)', content, re.DOTALL)
    if not fm_match:
        return content

    fm_body = fm_match.group(2)

    # applyTo の値を抽出（ダブルクォート → シングルクォート → クォートなし の順に試行）
    apply_to: str | None = None
    m = re.search(r'^applyTo:\s*"([^"]*)"', fm_body, re.MULTILINE)
    if m:
        apply_to = m.group(1)
    else:
        m = re.search(r"^applyTo:\s*'([^']*)'", fm_body, re.MULTILINE)
        if m:
            apply_to = m.group(1)
        else:
            m = re.search(r'^applyTo:\s*(\S.*?)\s*$', fm_body, re.MULTILINE)
            if m:
                apply_to = m.group(1).strip()

    if apply_to is None:
        return content

    if apply_to == "**":
        new_fm_body = re.sub(
            r'^applyTo:.*$', 'inclusion: always',
            fm_body, count=1, flags=re.MULTILINE,
        )
    else:
        replacement = f'inclusion: fileMatch\nfileMatchPattern: "{apply_to}"'
        new_fm_body = re.sub(
            r'^applyTo:.*$', replacement,
            fm_body, count=1, flags=re.MULTILINE,
        )

    return fm_match.group(1) + new_fm_body + fm_match.group(3) + content[fm_match.end():]


def _version_tuple(v: str | None) -> tuple:
    """バージョン文字列を比較可能な 3 要素タプルに変換する。

    'X.Y.Z' → (X, Y, Z)。要素が不足する場合はゼロ埋め。
    例: '1.2' → (1, 2, 0)、'1' → (1, 0, 0)
    プレリリース識別子（'-' を含む部分）はそこで打ち切り無視する。
    """
    if not v:
        return (0, 0, 0)
    try:
        parts = []
        for x in v.split("."):
            if x.isdigit():
                parts.append(int(x))
            else:
                break  # プレリリース識別子に到達したら打ち切る
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except Exception:
        return (0, 0, 0)


def _read_frontmatter_version(skill_path: str) -> str | None:
    """SKILL.md のフロントマターから metadata.version を読み取る。未記載なら None。"""
    skill_md = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None
    with open(skill_md, encoding="utf-8") as f:
        content = f.read()
    fm = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not fm:
        return None
    in_metadata = False
    for line in fm.group(1).splitlines():
        if line.startswith("metadata:"):
            in_metadata = True
            continue
        if in_metadata:
            if line and not line[0].isspace():
                in_metadata = False
            elif line.lstrip().startswith("version:"):
                ver = line.split(":", 1)[1].strip().strip("\"'")
                return ver or None
    return None


def _update_frontmatter_version(skill_path: str, new_ver: str) -> bool:
    """SKILL.md のフロントマター内 metadata.version を new_ver に書き換える。

    書き換えに成功した場合は True、フロントマターや version フィールドが
    見つからない場合は False を返す。
    """
    skill_md = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md):
        return False
    with open(skill_md, encoding="utf-8") as f:
        content = f.read()

    fm = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not fm:
        return False

    fm_text = fm.group(1)
    lines = fm_text.splitlines()
    in_metadata = False
    new_lines = []
    updated = False

    for line in lines:
        if line.startswith("metadata:"):
            in_metadata = True
            new_lines.append(line)
        elif in_metadata:
            if line and not line[0].isspace():
                in_metadata = False
                new_lines.append(line)
            elif re.match(r'^[ \t]+version:', line):
                indent_match = re.match(r'^([ \t]+)', line)
                indent = indent_match.group(1) if indent_match else "  "
                new_lines.append(f'{indent}version: "{new_ver}"')
                updated = True
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if not updated:
        return False

    new_fm = "\n".join(new_lines)
    new_content = content[:fm.start(1)] + new_fm + content[fm.end(1):]
    with open(skill_md, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True


def _discover_core_skills() -> list[str]:
    """skill_home の SKILL.md をスキャンして tier: core のスキルを動的収集する。"""
    skill_home = _skill_home()
    result = []
    if not os.path.isdir(skill_home):
        return result
    for name in sorted(os.listdir(skill_home)):
        skill_md = os.path.join(skill_home, name, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        with open(skill_md, encoding="utf-8") as f:
            content = f.read()
        fm = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if fm and re.search(r'^\s+tier:\s*core\s*$', fm.group(1), re.MULTILINE):
            result.append(name)
    return result


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
        reg.setdefault("core_skills", _discover_core_skills())
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

    # v5 → v6: メトリクス拡張（実行時間・サブエージェント回数・トレンド・共起）
    if version < 6:
        for skill in reg.get("installed_skills", []):
            metrics = skill.setdefault("metrics", {})
            metrics.setdefault("total_executions", 0)
            metrics.setdefault("ok_rate", None)
            metrics.setdefault("last_executed_at", None)
            metrics.setdefault("central_ok_rate", None)
            metrics.setdefault("avg_duration_sec", None)
            metrics.setdefault("p90_duration_sec", None)
            metrics.setdefault("avg_subagent_calls", None)
            metrics.setdefault("trend_7d", {"executions": 0, "ok_rate": 0.0})
            metrics.setdefault("top_co_skills", [])

    # v6 → v7: マルチエージェント対応フィールドを追加
    #   agent_type:  インストール対象エージェント種別
    #   user_home:   ユーザーホームディレクトリ
    #   install_dir: スキルリポジトリのルートディレクトリ（自動更新の参照元）
    #   skill_home:  スキルインストール先ディレクトリ
    if version < 7:
        home = _user_home()
        reg.setdefault("agent_type", "copilot")
        reg.setdefault("user_home", home)
        reg.setdefault("install_dir", None)
        # skill_home は agent_type から導出（デフォルト copilot）
        agent_dir = AGENT_DIRS.get(reg["agent_type"], ".copilot")
        reg.setdefault("skill_home", os.path.join(home, agent_dir, "skills"))

    # usage_stats と skill_discovery を全バージョンから除去（使用記録機能削除）
    for skill in reg.get("installed_skills", []):
        skill.pop("usage_stats", None)
    reg.pop("skill_discovery", None)

    reg["version"] = 7
    # tier: core の SKILL.md から常に最新のコアスキル一覧を再計算する
    reg["core_skills"] = _discover_core_skills()
    return reg


def load_registry() -> dict:
    path = _registry_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
        return migrate_registry(reg)
    home = _user_home()
    return {
        "version": 7,
        "agent_type": "copilot",
        "user_home": home,
        "install_dir": None,
        "skill_home": os.path.join(home, ".copilot", "skills"),
        "node": {
            "id": None,
            "name": None,
            "created_at": None,
        },
        "repositories": [],
        "installed_skills": [],
        "core_skills": _discover_core_skills(),
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
        json.dump(reg, f, indent=2, ensure_ascii=True)


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
