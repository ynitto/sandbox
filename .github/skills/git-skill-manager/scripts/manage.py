#!/usr/bin/env python3
"""スキル管理操作: list / search / enable / disable / pin / unpin / lock / unlock / promote / profile / diff / sync / changelog / deps。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime

from registry import (
    load_registry, save_registry, is_skill_enabled, _skill_home, _cache_dir,
    _read_frontmatter_version, _update_frontmatter_version, _version_tuple,
)
from repo import clone_or_fetch, update_remote_index
from push import push_skill
from changelog import generate_changelog


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

        version = info.get("version")
        central_ver = info.get("central_version")
        version_ahead = info.get("version_ahead", False)
        if version:
            ver_label = f" v{version}"
            if version_ahead:
                ver_label += " ⬆️"
            elif central_ver and central_ver != version:
                ver_label += f" (central: v{central_ver})"
        else:
            ver_label = ""

        print(f"   {status} {entry:30s}  repo: {repo:20s}  commit: {hash_}{pin_mark}{ver_label}")


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
    raw = input("> ").strip()
    try:
        selected_indices = [
            int(x.strip()) - 1
            for x in raw.split(",")
            if x.strip().isdigit() and 1 <= int(x.strip()) <= len(candidates)
        ]
    except ValueError:
        selected_indices = []

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
            "source_path": os.path.abspath(c["path"]),
            "commit_hash": "-",
            "installed_at": datetime.now().isoformat(),
            "enabled": True,
            "pinned_commit": None,
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
    raw = input("> ").strip()
    repo_choice = int(raw) if raw.isdigit() and 0 <= int(raw) <= len(writable_repos) else 0

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
    """discover_skills のソートキーを生成する。

    優先順:
      1. コアスキル（常に先頭）
      2. pending_refinement=False かつ直近フィードバックが ok → 信頼済み
      3. pending_refinement=True → 改良待ち（後ろ）
      4. フィードバックなし → アルファベット順
    """
    name = skill["name"]
    is_core = 0 if name in core_skills else 1
    reg_skill = next(
        (s for s in registry.get("installed_skills", []) if s["name"] == name),
        None,
    )
    if reg_skill:
        pending = 1 if reg_skill.get("pending_refinement") else 0
        history = reg_skill.get("feedback_history") or []
        last_verdict = history[-1]["verdict"] if history else ""
        # ok が最後なら信頼スコア高（0）、それ以外は中（1）
        trust = 0 if last_verdict == "ok" else 1
    else:
        pending = 0
        trust = 2  # 情報なし → 最後

    return (is_core, pending, trust, name)


# ---------------------------------------------------------------------------
# refine
# ---------------------------------------------------------------------------

def refine_skill(skill_name):
    """pending_refinement のあるスキルの改良フローを開始する。

    このスクリプトはフィードバックを収集・整形して出力する。
    実際の skill-creator 起動は Claude（エージェント）が行う。
    ワークスペーススキル / インストール済みスキルの両方に対応する。
    """
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return

    history = skill.get("feedback_history") or []
    pending = [e for e in history if not e.get("refined") and e["verdict"] != "ok"]

    if not pending:
        print(f"ℹ️ '{skill_name}' に未処理の改善フィードバックはありません")
        return

    # スキルの実体パスを特定する
    source = skill.get("source_repo", "")
    if source == "workspace":
        skill_path = os.path.join(".github", "skills", skill_name)
        location_label = "ワークスペーススキル"
    else:
        skill_path = os.path.join(_skill_home(), skill_name)
        location_label = "インストール済みスキル"

    print(f"📋 '{skill_name}' の未処理フィードバック ({len(pending)} 件):\n")
    for i, entry in enumerate(pending, 1):
        ts = entry.get("timestamp", "")[:10]
        verdict = entry.get("verdict", "")
        note = entry.get("note", "(コメントなし)")
        mark = "⚠️" if verdict == "needs-improvement" else "❌"
        print(f"  {i}. [{ts}] {mark} {note}")

    print()
    print(f"スキルパス: {skill_path}  ({location_label})")
    print("これらのフィードバックを skill-creator に渡してスキルを改良してください。")
    print(f"REFINE_COMPLETE_CMD: python manage.py mark-refined {skill_name}")


def mark_refined(skill_name):
    """pending_refinement を解除し、feedback_history の refined フラグを立てる。"""
    reg = load_registry()
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        print(f"❌ スキル '{skill_name}' がインストールされていません")
        return

    history = skill.get("feedback_history") or []
    updated = 0
    for entry in history:
        if not entry.get("refined") and entry["verdict"] != "ok":
            entry["refined"] = True
            updated += 1

    skill["pending_refinement"] = False
    save_registry(reg)
    print(f"✅ '{skill_name}': {updated} 件のフィードバックを改良済みにしました")


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

def diff_skill(skill_name: str, repo_names: list[str] | None = None) -> None:
    """複数リポジトリ間の同名スキルの実装差分を表示する。

    repo_names=None → 全登録リポジトリのキャッシュを対象にする。
    """
    reg = load_registry()
    cache = _cache_dir()

    repos = reg["repositories"]
    if repo_names:
        repos = [r for r in repos if r["name"] in repo_names]

    # 各リポジトリのキャッシュからスキルを検索
    found: list[dict] = []
    for repo in repos:
        skill_path = os.path.join(cache, repo["name"], repo["skill_root"], skill_name)
        if not (os.path.isdir(skill_path) and os.path.isfile(os.path.join(skill_path, "SKILL.md"))):
            continue

        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI %h", "--",
             os.path.join(repo["skill_root"], skill_name).replace("\\", "/")],
            cwd=os.path.join(cache, repo["name"]),
            capture_output=True, text=True,
        )
        log_out = result.stdout.strip()
        if log_out:
            parts = log_out.split(" ", 1)
            date_str, hash_str = parts[0][:10], parts[1] if len(parts) > 1 else "?"
        else:
            date_str, hash_str = "不明", "?"

        found.append({
            "repo_name": repo["name"],
            "path": skill_path,
            "date": date_str,
            "hash": hash_str,
        })

    if not found:
        print(f"❌ スキル '{skill_name}' がキャッシュ内のどのリポジトリにも見つかりません")
        print("  先に pull または search --refresh を実行してキャッシュを更新してください")
        return

    if len(found) == 1:
        print(f"ℹ️ スキル '{skill_name}' は {found[0]['repo_name']} にのみ存在します（差分なし）")
        return

    print(f"🔍 スキル '{skill_name}' の差分 ({len(found)} リポジトリ)\n")
    for f in found:
        print(f"  [{f['repo_name']}]  commit: {f['hash']}  ({f['date']})")

    # ペアワイズ差分
    for i in range(len(found)):
        for j in range(i + 1, len(found)):
            a, b = found[i], found[j]
            print(f"\n{'─' * 60}")
            print(f"  {a['repo_name']} ({a['hash']})  vs  {b['repo_name']} ({b['hash']})")
            print(f"{'─' * 60}")

            stat = subprocess.run(
                ["git", "diff", "--no-index", "--stat", a["path"], b["path"]],
                capture_output=True, text=True,
            )
            stat_out = stat.stdout.strip()

            if not stat_out:
                print("  (差分なし: 内容は同一です)")
                continue

            print(stat_out)
            print()

            detail = subprocess.run(
                ["git", "diff", "--no-index", a["path"], b["path"]],
                capture_output=True, text=True,
            )
            lines = detail.stdout.splitlines()
            if len(lines) > 120:
                print("\n".join(lines[:120]))
                print(f"\n  ... (+{len(lines) - 120} 行省略。全差分: git diff --no-index \"{a['path']}\" \"{b['path']}\")")
            else:
                print(detail.stdout)


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def sync_skill(skill_name: str, repo_names: list[str] | None = None) -> None:
    """マージ済みスキルをインストール済みの実体から複数リポジトリへ一括 push する。

    repo_names=None → 書き込み可能な全リポジトリが対象。
    事前にマージ済み実装を skill_home/<skill_name>/ に配置しておく必要がある。
    """
    reg = load_registry()
    skill_home = _skill_home()
    skill_path = os.path.join(skill_home, skill_name)

    if not os.path.isdir(skill_path):
        print(f"❌ スキル '{skill_name}' が {skill_home} にありません")
        print("  マージ済みの実装をそのパスに配置してから実行してください")
        return

    repos = [r for r in reg["repositories"] if not r.get("readonly", False)]
    if repo_names:
        repos = [r for r in repos if r["name"] in repo_names]

    if not repos:
        print("❌ push 可能なリポジトリが見つかりません（全リポジトリが readonly、または指定名が不正）")
        return

    print(f"🔄 '{skill_name}' を {len(repos)} リポジトリへ同期します\n")
    for repo in repos:
        print(f"  → {repo['name']}  ({repo['url']})")
    print()

    results: list[dict] = []
    for repo in repos:
        print(f"⬆️  push 中: {repo['name']} ...")
        try:
            push_skill(
                skill_path,
                repo["name"],
                branch_strategy="new_branch",
                commit_msg=f"Sync skill: {skill_name} (cross-repo merge)",
            )
            results.append({"repo": repo["name"], "ok": True})
        except Exception as e:
            print(f"  ❌ {repo['name']}: push 失敗 — {e}")
            results.append({"repo": repo["name"], "ok": False, "error": str(e)})

    print(f"\n📋 sync 結果: {skill_name}")
    for r in results:
        mark = "✅" if r["ok"] else "❌"
        detail = f"  ({r.get('error', '')})" if not r["ok"] else ""
        print(f"  {mark} {r['repo']}{detail}")

    succeeded = [r for r in results if r["ok"]]
    if succeeded:
        print("\n💡 各リポジトリで PR/MR を作成してマージしてください")


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def merge_skill(skill_name: str, repo_names: list[str] | None = None) -> None:
    """クロスリポジトリマージフローの入口。

    diff を表示してエージェントへのガイダンスを出力する。
    エージェントはその後 skill-creator を起動してマージ実装を生成し、
    最後に sync_skill() を呼んで全リポジトリへ配信する。
    """
    reg = load_registry()
    skill_home = _skill_home()

    # ステップ1: diff を表示
    print(f"🔀 クロスリポジトリマージ: '{skill_name}'\n")
    print("【ステップ 1/3】差分を確認します\n")
    diff_skill(skill_name, repo_names)

    # ステップ2: skill-creator へのガイダンスを出力
    repos = reg["repositories"]
    if repo_names:
        repos = [r for r in repos if r["name"] in repo_names]

    repo_list = ", ".join(r["name"] for r in repos)
    merge_target = os.path.join(skill_home, skill_name)
    sync_cmd = f"python manage.py sync {skill_name}" + (
        f" --repos {','.join(repo_names)}" if repo_names else ""
    )

    print(f"\n{'─' * 60}")
    print("【ステップ 2/3】skill-creator でマージ実装を生成する")
    print(f"  対象リポジトリ: {repo_list}")
    print(f"  編集先: {merge_target}")
    print()
    print("MERGE_GUIDANCE:")
    print(f"  skill-creator サブエージェントを起動し、上記の差分を踏まえて")
    print(f"  '{skill_name}' の統合実装を次のパスに作成してください:")
    print(f"  {merge_target}")
    print()
    print("  skill-creator への指示例:")
    print(f"  「上の差分を踏まえて {skill_name} の統合実装を")
    print(f"   {merge_target} に作成して。どの変更を取り込むか確認しながら進めてください。」")
    print(f"\n{'─' * 60}")
    print("【ステップ 3/3】マージ完了後に次のコマンドを実行する:")
    print(f"  {sync_cmd}")


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


# ---------------------------------------------------------------------------
# changelog
# ---------------------------------------------------------------------------

def changelog_skill(skill_name: str, dry_run: bool = False) -> None:
    """スキルの CHANGELOG.md を git ログとフロントマターのバージョン変更から生成する。

    スキルのファイルを変更した後に呼び出す。フロントマターの version が変わった
    タイミングでセクションを区切り、コミットメッセージを箇条書きにまとめる。
    dry_run=True の場合はファイルに書かずに内容を表示する。
    """
    content = generate_changelog(skill_name)

    if dry_run:
        print(content)
        return

    from changelog import _skill_path
    path = _skill_path(skill_name)
    if not path:
        print(f"❌ スキル '{skill_name}' が見つかりません")
        return

    out = os.path.join(path, "CHANGELOG.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ {out} を生成しました")


# ---------------------------------------------------------------------------
# bump
# ---------------------------------------------------------------------------

def _find_skill_path(skill_name: str) -> str | None:
    """ワークスペース → インストール済みの順でスキルパスを返す。"""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        root = result.stdout.strip()
        ws_path = os.path.join(root, ".github", "skills", skill_name)
        if os.path.isdir(ws_path) and os.path.isfile(os.path.join(ws_path, "SKILL.md")):
            return ws_path

    installed = os.path.join(_skill_home(), skill_name)
    if os.path.isdir(installed) and os.path.isfile(os.path.join(installed, "SKILL.md")):
        return installed

    return None


def bump_version(skill_name: str, bump_type: str = "patch") -> None:
    """SKILL.md の metadata.version をセマンティックバージョニングに従ってインクリメントする。

    bump_type:
        "patch" (デフォルト) — バグ修正・軽微な改善: 1.2.3 → 1.2.4
        "minor"             — 後方互換の機能追加:     1.2.3 → 1.3.0
        "major"             — 破壊的変更:             1.2.3 → 2.0.0
    """
    if bump_type not in ("major", "minor", "patch"):
        print(f"❌ bump_type は 'major' / 'minor' / 'patch' のいずれかを指定してください")
        return

    skill_path = _find_skill_path(skill_name)
    if not skill_path:
        print(f"❌ スキル '{skill_name}' が見つかりません")
        return

    current_ver = _read_frontmatter_version(skill_path)
    tup = _version_tuple(current_ver)

    if bump_type == "major":
        new_tup = (tup[0] + 1, 0, 0)
    elif bump_type == "minor":
        new_tup = (tup[0], tup[1] + 1, 0)
    else:
        new_tup = (tup[0], tup[1], tup[2] + 1)

    new_ver = f"{new_tup[0]}.{new_tup[1]}.{new_tup[2]}"

    if not _update_frontmatter_version(skill_path, new_ver):
        print(f"❌ SKILL.md の version フィールドが見つかりません: {skill_path}")
        return

    # レジストリのバージョンも更新
    reg = load_registry()
    skill_entry = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if skill_entry:
        central_ver = skill_entry.get("central_version")
        skill_entry["version"] = new_ver
        skill_entry["version_ahead"] = _version_tuple(new_ver) > _version_tuple(central_ver)
        save_registry(reg)

    cur_label = current_ver or "0.0.0"
    print(f"✅ {skill_name}: v{cur_label} → v{new_ver} ({bump_type})")
    print(f"   パス: {skill_path}")
    print()
    print(f"   次のステップ:")
    print(f"     1. スキルを修正する")
    print(f"     2. python changelog.py {skill_name}  # CHANGELOG.md を更新")
    print(f"     3. push または promote でリポジトリに反映")


# ---------------------------------------------------------------------------
# deps （依存関係の検証・グラフ出力）
# ---------------------------------------------------------------------------

def deps_check(skill_name: str | None = None) -> int:
    """depends_on / recommends の充足状況を検証する。不足があれば終了コード 1。"""
    from deps import check_deps
    return check_deps(skill_name)


def deps_graph(skill_name: str | None = None) -> None:
    """スキル依存グラフを Mermaid 形式で出力する。"""
    from deps import show_graph
    show_graph(skill_name)
