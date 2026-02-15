#!/usr/bin/env python3
"""スキル管理操作: list / search / enable / disable / pin / unpin / lock / unlock / promote / profile。"""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime

from registry import load_registry, save_registry, is_skill_enabled, _skill_home
from repo import clone_or_fetch, update_remote_index
from push import push_skill


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def list_skills():
    reg = load_registry()
    skill_home = _skill_home()
    registry_map = {s["name"]: s for s in reg.get("installed_skills", [])}
    active_profile = reg.get("active_profile")

    print(f"📂 スキル一覧 ({skill_home})")
    if active_profile:
        print(f"   アクティブプロファイル: {active_profile}")
    print()

    if not os.path.isdir(skill_home):
        print("   (スキルなし)")
        return

    for entry in sorted(os.listdir(skill_home)):
        if not os.path.isfile(os.path.join(skill_home, entry, "SKILL.md")):
            continue
        info = registry_map.get(entry, {})
        repo = info.get("source_repo", "local")
        hash_ = info.get("commit_hash", "-")
        enabled = is_skill_enabled(entry, reg)
        pinned = info.get("pinned_commit")
        status = "✅" if enabled else "⏸️"
        pin_mark = f" 📌{pinned[:7]}" if pinned else ""
        print(f"   {status} {entry:30s}  repo: {repo:20s}  commit: {hash_}{pin_mark}")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def search_skills(repo_name=None, keyword=None, refresh=False):
    reg = load_registry()
    repos = reg["repositories"]
    if repo_name:
        repos = [r for r in repos if r["name"] == repo_name]

    remote_index = reg.get("remote_index", {})

    # インデックスが空 or --refresh → リモートからインデックスを更新
    needs_refresh = refresh or not any(
        repo["name"] in remote_index for repo in repos
    )

    if needs_refresh:
        print("🔄 リモートからインデックスを更新中...")
        for repo in repos:
            repo_cache = clone_or_fetch(repo)
            update_remote_index(reg, repo["name"], repo_cache, repo["skill_root"])
        save_registry(reg)
        remote_index = reg.get("remote_index", {})

    # インデックスから検索
    for repo in repos:
        index_entry = remote_index.get(repo["name"])
        if not index_entry:
            continue

        print(f"\n🔍 {repo['name']} ({repo['url']})")
        updated = index_entry.get("updated_at", "不明")[:10]
        print(f"   (インデックス更新日: {updated})")

        found = False
        for skill in index_entry.get("skills", []):
            name = skill["name"]
            desc = skill.get("description", "")

            if keyword and keyword.lower() not in name.lower() and keyword.lower() not in desc.lower():
                continue

            found = True
            short_desc = desc[:80] + "..." if len(desc) > 80 else desc
            print(f"   {name:30s}  {short_desc}")

        if not found:
            print("   (該当なし)")


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------

def enable_skill(skill_name):
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return
    if skill.get("enabled", True):
        print(f"ℹ️ スキル '{skill_name}' は既に有効です")
        return
    skill["enabled"] = True
    save_registry(reg)
    print(f"✅ スキル '{skill_name}' を有効化しました")


def disable_skill(skill_name):
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return
    if not skill.get("enabled", True):
        print(f"ℹ️ スキル '{skill_name}' は既に無効です")
        return
    skill["enabled"] = False
    save_registry(reg)
    print(f"⏸️ スキル '{skill_name}' を無効化しました")


# ---------------------------------------------------------------------------
# pin / unpin
# ---------------------------------------------------------------------------

def pin_skill(skill_name, commit=None):
    """commit=None → 現在の commit_hash に固定。commit 指定 → 指定コミットに固定。"""
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return

    target = commit or skill.get("commit_hash")
    if not target:
        print(f"❌ コミットハッシュが不明です。先に pull してください")
        return

    skill["pinned_commit"] = target
    save_registry(reg)
    print(f"📌 スキル '{skill_name}' を {target[:7]} に固定しました")


def unpin_skill(skill_name):
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return
    if not skill.get("pinned_commit"):
        print(f"ℹ️ スキル '{skill_name}' は固定されていません")
        return

    skill["pinned_commit"] = None
    save_registry(reg)
    print(f"🔓 スキル '{skill_name}' の固定を解除しました（次回 pull で最新版を取得します）")


# ---------------------------------------------------------------------------
# lock / unlock
# ---------------------------------------------------------------------------

def lock_all():
    """全スキルを現在の commit_hash に一括固定する。"""
    reg = load_registry()
    skills = reg.get("installed_skills", [])
    locked = 0

    for skill in skills:
        hash_ = skill.get("commit_hash")
        if hash_ and not skill.get("pinned_commit"):
            skill["pinned_commit"] = hash_
            locked += 1

    save_registry(reg)
    print(f"🔒 lock 完了: {locked} 件のスキルを固定しました")
    for skill in skills:
        pin = skill.get("pinned_commit")
        if pin:
            print(f"   📌 {skill['name']:30s}  {pin[:7]}")


def unlock_all():
    """全スキルの固定を一括解除する。"""
    reg = load_registry()
    skills = reg.get("installed_skills", [])
    unlocked = 0

    for skill in skills:
        if skill.get("pinned_commit"):
            skill["pinned_commit"] = None
            unlocked += 1

    save_registry(reg)
    print(f"🔓 unlock 完了: {unlocked} 件のスキルの固定を解除しました")


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def promote_skills(workspace_skills_dir, interactive=True):
    """ワークスペース内スキルをユーザー領域にコピーし、リポジトリにも push する。"""
    reg = load_registry()
    skill_home = _skill_home()

    # ワークスペース内スキルをスキャン
    candidates = []
    for entry in sorted(os.listdir(workspace_skills_dir)):
        skill_md = os.path.join(workspace_skills_dir, entry, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue

        with open(skill_md, encoding="utf-8") as f:
            content = f.read()
        desc = ""
        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).splitlines():
                if line.startswith("description:"):
                    desc = line[len("description:"):].strip()
                    break

        already_installed = os.path.isdir(os.path.join(skill_home, entry))
        candidates.append({
            "name": entry,
            "path": os.path.join(workspace_skills_dir, entry),
            "description": desc[:80],
            "already_installed": already_installed,
        })

    if not candidates:
        print("ℹ️ ワークスペースにスキルが見つかりません")
        return

    # ---- ユーザーに候補を提示して選択させる ----
    print(f"\n📂 ワークスペースのスキル ({workspace_skills_dir})\n")
    for i, c in enumerate(candidates, 1):
        installed_mark = " (インストール済み)" if c["already_installed"] else ""
        short_desc = c["description"] or "(説明なし)"
        print(f"   {i}. {c['name']:30s}  {short_desc}{installed_mark}")

    print(f"\nユーザー領域にコピーするスキルを選んでください（カンマ区切り、例: 1,3）")

    # ※ Claude がユーザーの選択を対話的に受け取り、
    #   selected_indices に反映する
    selected_indices = []  # プレースホルダー

    # ---- コピー実行 ----
    promoted = []
    for idx in selected_indices:
        c = candidates[idx]
        dest = os.path.join(skill_home, c["name"])
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(c["path"], dest)

        existing_skill = next(
            (s for s in reg.get("installed_skills", []) if s["name"] == c["name"]),
            None,
        )
        skill_entry = {
            "name": c["name"],
            "source_repo": "local",
            "source_path": os.path.relpath(c["path"]),
            "commit_hash": "-",
            "installed_at": datetime.now().isoformat(),
            "enabled": True,
            "pinned_commit": None,
            "usage_stats": existing_skill.get("usage_stats") if existing_skill else None,
        }
        if existing_skill:
            existing_skill.update(skill_entry)
        else:
            reg["installed_skills"].append(skill_entry)

        promoted.append(c["name"])

    save_registry(reg)

    print(f"\n✅ {len(promoted)} 件のスキルをユーザー領域にコピーしました")
    for name in promoted:
        print(f"   → {name}")

    # ---- リポジトリへの push ----
    writable_repos = [r for r in reg["repositories"] if not r.get("readonly", False)]
    if not writable_repos:
        print("\nℹ️ 書き込み可能なリポジトリが登録されていません。push をスキップします")
        return

    print(f"\nリポジトリに push しますか？")
    for i, repo in enumerate(writable_repos, 1):
        print(f"   {i}. {repo['name']:20s}  ({repo['url']})")
    print(f"   0. push しない")

    # ※ Claude がユーザーの選択を対話的に受け取る
    repo_choice = 0  # プレースホルダー

    if repo_choice > 0:
        target_repo = writable_repos[repo_choice - 1]
        for name in promoted:
            skill_path = os.path.join(skill_home, name)
            push_skill(skill_path, target_repo["name"],
                       branch_strategy="new_branch",
                       commit_msg=f"Promote skill: {name}")

    print(f"\n🎉 promote 完了")


# ---------------------------------------------------------------------------
# sort_key (discover_skills 用)
# ---------------------------------------------------------------------------

def sort_key(skill, core_skills, registry):
    """discover_skills のソートキーを生成する。"""
    name = skill["name"]
    is_core = 0 if name in core_skills else 1
    reg_skill = next(
        (s for s in registry.get("installed_skills", []) if s["name"] == name),
        None,
    )
    stats = (reg_skill or {}).get("usage_stats") or {}
    total = -(stats.get("total_count", 0))
    last_used = stats.get("last_used_at", "")
    last_used_neg = "" if not last_used else last_used
    return (is_core, total, last_used_neg, name)


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

def profile_create(profile_name, skill_names):
    reg = load_registry()
    profiles = reg.setdefault("profiles", {})

    if profile_name in profiles:
        print(f"⚠️ プロファイル '{profile_name}' を上書きします")

    profiles[profile_name] = skill_names
    save_registry(reg)
    print(f"✅ プロファイル '{profile_name}' を作成しました: {', '.join(skill_names)}")


def profile_use(profile_name):
    """プロファイルをアクティブにする。None で個別 enabled に戻る。"""
    reg = load_registry()
    profiles = reg.get("profiles", {})

    if profile_name is not None and profile_name not in profiles:
        print(f"❌ プロファイル '{profile_name}' が見つかりません")
        print(f"   利用可能: {', '.join(profiles.keys())}")
        return

    reg["active_profile"] = profile_name
    save_registry(reg)

    if profile_name is None:
        print("✅ プロファイルを解除しました（個別の enabled 設定に従います）")
    else:
        skills = profiles[profile_name]
        label = "全スキル" if "*" in skills else ", ".join(skills)
        print(f"✅ プロファイル '{profile_name}' をアクティブにしました: {label}")


def profile_list():
    reg = load_registry()
    profiles = reg.get("profiles", {})
    active = reg.get("active_profile")

    if not profiles:
        print("   (プロファイルなし)")
        return

    print("📋 プロファイル一覧\n")
    for name, skills in profiles.items():
        marker = " ◀ active" if name == active else ""
        label = "全スキル" if "*" in skills else ", ".join(skills)
        print(f"   {name:20s}  [{label}]{marker}")


def profile_delete(profile_name):
    reg = load_registry()
    profiles = reg.get("profiles", {})

    if profile_name not in profiles:
        print(f"❌ プロファイル '{profile_name}' が見つかりません")
        return

    if profile_name == "default":
        print(f"❌ 'default' プロファイルは削除できません")
        return

    if reg.get("active_profile") == profile_name:
        reg["active_profile"] = None

    del profiles[profile_name]
    save_registry(reg)
    print(f"✅ プロファイル '{profile_name}' を削除しました")
