#!/usr/bin/env python3
"""ローカルスキルと中央バージョンの差分追跡。

各スキルが中央リポジトリから取得した時点から
ローカルで変更されたかどうかを検出し、
lineage フィールドを更新する。

使い方:
    python delta_tracker.py                      # 全インストール済みスキルをスキャン
    python delta_tracker.py --skill <name>       # 特定スキルのみ
    python delta_tracker.py --summary            # サマリー表示のみ
    python delta_tracker.py --note <name> "説明" # ローカル変更の説明を記録
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from registry import load_registry, save_registry, _skill_home, _cache_dir, _version_tuple, _read_frontmatter_version


def _hash_skill_content(skill_dir: str) -> str | None:
    """スキルディレクトリの SKILL.md 内容をハッシュ化する。

    SKILL.md が存在しない場合は None を返す。
    """
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None
    with open(skill_md, encoding="utf-8") as f:
        content = f.read()
    return hashlib.sha256(content.encode()).hexdigest()


def _get_central_skill_path(skill: dict) -> str | None:
    """中央リポジトリのキャッシュからスキルパスを返す。

    キャッシュが存在しない場合は None を返す。
    """
    source_repo = skill.get("source_repo")
    source_path = skill.get("source_path")
    if not source_repo or not source_path or source_repo == "workspace":
        return None

    cache_path = os.path.join(_cache_dir(), source_repo, source_path)
    if os.path.isdir(cache_path):
        return cache_path
    return None


def detect_local_modification(skill: dict) -> dict:
    """スキルのローカル変更を検出する。

    Returns:
        {
            "local_modified": bool,
            "local_hash": str | None,
            "central_hash": str | None,
            "detection_method": "hash_compare" | "no_cache" | "workspace",
        }
    """
    source_repo = skill.get("source_repo", "")

    # ワークスペーススキルは常にローカル
    if source_repo == "workspace":
        return {
            "local_modified": True,
            "local_hash": None,
            "central_hash": None,
            "detection_method": "workspace",
        }

    skill_home = _skill_home()
    local_path = os.path.join(skill_home, skill["name"])
    local_hash = _hash_skill_content(local_path)

    central_path = _get_central_skill_path(skill)
    if not central_path:
        # キャッシュがない場合はローカル変更を検出できないため保守的に False を返す
        return {
            "local_modified": False,
            "local_hash": local_hash,
            "central_hash": None,
            "detection_method": "no_cache",
        }

    central_hash = _hash_skill_content(central_path)

    return {
        "local_modified": local_hash != central_hash,
        "local_hash": local_hash,
        "central_hash": central_hash,
        "detection_method": "hash_compare",
    }


def scan_skills(target_skill: str | None = None) -> list:
    """全インストール済みスキルをスキャンし、lineage を更新する。"""
    reg = load_registry()
    skills = reg.get("installed_skills", [])

    if target_skill:
        skills = [s for s in skills if s["name"] == target_skill]

    if not skills:
        print("ℹ️  スキャン対象のスキルがありません")
        return []

    results = []
    modified_count = 0

    for skill in skills:
        detection = detect_local_modification(skill)
        lineage = skill.setdefault("lineage", {})
        prev_modified = lineage.get("local_modified", False)

        # 新たにローカル変更が検出された場合のみ diverged_at を更新
        if detection["local_modified"] and not prev_modified:
            lineage["diverged_at"] = datetime.now(timezone.utc).isoformat()

        lineage["local_modified"] = detection["local_modified"]

        # 初回スキャン時に origin 情報を記録
        if not lineage.get("origin_repo"):
            lineage["origin_repo"] = skill.get("source_repo")
        if not lineage.get("origin_commit"):
            lineage["origin_commit"] = skill.get("commit_hash")

        # version と version_ahead をローカル SKILL.md から動的に再計算
        local_path = os.path.join(_skill_home(), skill["name"])
        local_ver = _read_frontmatter_version(local_path)
        central_ver = skill.get("central_version")
        skill["version"] = local_ver
        skill["version_ahead"] = _version_tuple(local_ver) > _version_tuple(central_ver)

        if detection["local_modified"]:
            modified_count += 1

        results.append({
            "name": skill["name"],
            "local_modified": detection["local_modified"],
            "method": detection["detection_method"],
            "diverged_at": lineage.get("diverged_at"),
            "summary": lineage.get("local_changes_summary", ""),
        })

    save_registry(reg)
    return results


def show_summary(results: list, verbose: bool = False) -> None:
    """スキャン結果のサマリーを表示する。"""
    modified = [r for r in results if r["local_modified"]]
    unmodified = [r for r in results if not r["local_modified"]]

    print(f"📂 ローカル変更スキャン結果: {len(results)} 件")
    print()

    if modified:
        print(f"🔧 ローカル変更あり ({len(modified)} 件):")
        for r in modified:
            diverged = r.get("diverged_at", "")[:10] if r.get("diverged_at") else "不明"
            summary = f"  ← {r['summary']}" if r.get("summary") else ""
            print(f"   {r['name']:30s}  (変更検出: {diverged}){summary}")

    if verbose and unmodified:
        print(f"\n✅ 中央版と同一 ({len(unmodified)} 件):")
        for r in unmodified:
            print(f"   {r['name']}")

    print()
    if modified:
        print(f"💡 昇格候補: {len(modified)} 件")
        print("   'python promotion_policy.py' で昇格適性を確認できます")


def set_change_summary(skill_name: str, summary: str) -> None:
    """ローカル変更の説明テキストを記録する。"""
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' が見つかりません")
        return

    skill.setdefault("lineage", {})["local_changes_summary"] = summary
    save_registry(reg)
    print(f"✅ '{skill_name}' の変更説明を記録しました: {summary}")


def check_sync_protection(skill: dict, reg: dict) -> bool:
    """sync_policy.protect_local_modified に基づき、上書きを保護するか判定する。

    pull.py から呼び出される想定。
    True を返す場合は中央からの更新をスキップすべき。
    """
    sync_policy = reg.get("sync_policy", {})
    if not sync_policy.get("protect_local_modified", True):
        return False

    lineage = skill.get("lineage", {})
    return lineage.get("local_modified", False)


def main():
    parser = argparse.ArgumentParser(description="ローカルスキルと中央バージョンの差分追跡")
    parser.add_argument("--skill", help="特定スキルのみスキャンする")
    parser.add_argument("--summary", action="store_true", help="サマリー表示のみ（詳細を省略）")
    parser.add_argument("--verbose", action="store_true", help="変更なしのスキルも表示する")
    parser.add_argument("--note", nargs=2, metavar=("SKILL_NAME", "SUMMARY"),
                        help="ローカル変更の説明を記録する")
    args = parser.parse_args()

    if args.note:
        set_change_summary(args.note[0], args.note[1])
        return

    results = scan_skills(target_skill=args.skill)
    show_summary(results, verbose=args.verbose)


if __name__ == "__main__":
    main()
