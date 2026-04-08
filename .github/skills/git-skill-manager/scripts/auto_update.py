#!/usr/bin/env python3
"""auto_update 操作: セッション開始時にスキルの更新を自動チェックする。"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from registry import load_registry, save_registry, _cache_dir, _skill_home, _version_tuple, _read_frontmatter_version
from repo import clone_or_fetch


def _bump_type(old_ver: str | None, new_ver: str | None) -> str:
    """バージョンアップの種別を返す: 'patch', 'minor', 'major', 'unknown'。"""
    old = _version_tuple(old_ver)
    new = _version_tuple(new_ver)
    if new == old:
        return "unknown"
    if new[0] > old[0]:
        return "major"
    if new[1] > old[1]:
        return "minor"
    if new[2] > old[2]:
        return "patch"
    return "unknown"


def _default_auto_update() -> dict:
    """auto_update 設定のデフォルト値を返す。"""
    return {
        "enabled": False,
        "interval_hours": 24,
        "notify_only": True,
        "last_checked_at": None,
    }


def _should_check(reg: dict) -> bool:
    """前回チェックから interval_hours 以上経過しているかを判定する。"""
    au = reg.get("auto_update", {})
    last_checked = au.get("last_checked_at")
    if not last_checked:
        return True

    interval = au.get("interval_hours", 24)
    try:
        last_dt = datetime.fromisoformat(last_checked)
    except (ValueError, TypeError):
        return True

    now = datetime.now(timezone.utc)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return now - last_dt >= timedelta(hours=interval)


def check_updates(force: bool = False) -> list[dict]:
    """
    リポジトリをフェッチして更新可能なスキルを検出する。

    force=True の場合、interval に関わらず即座にチェックする。
    戻り値: 更新情報のリスト [{name, current_hash, latest_hash, repo_name}]
    """
    reg = load_registry()

    if not force and not _should_check(reg):
        return []

    cache_dir = _cache_dir()
    repos = reg.get("repositories", [])
    installed = {s["name"]: s for s in reg.get("installed_skills", [])}
    updates: list[dict] = []

    for repo in repos:
        try:
            repo_cache = clone_or_fetch(repo)
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"⚠️ {repo['name']}: フェッチ失敗（{e}）— スキップ")
            continue

        root = os.path.join(repo_cache, repo["skill_root"])
        if not os.path.isdir(root):
            continue

        for entry in os.listdir(root):
            skill_md = os.path.join(root, entry, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue

            current = installed.get(entry)
            if not current:
                continue
            if current.get("source_repo") != repo["name"]:
                continue
            if current.get("pinned_commit"):
                continue

            latest_hash = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_cache, capture_output=True, text=True, encoding="utf-8",
            ).stdout.strip()

            if latest_hash and latest_hash != current.get("commit_hash"):
                with open(skill_md, encoding="utf-8") as f:
                    content = f.read()
                desc = ""
                fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
                if fm_match:
                    for line in fm_match.group(1).splitlines():
                        if line.startswith("description:"):
                            desc = line[len("description:"):].strip()
                            break

                latest_ver = _read_frontmatter_version(os.path.join(root, entry))
                updates.append({
                    "name": entry,
                    "current_hash": current["commit_hash"],
                    "latest_hash": latest_hash,
                    "current_version": current.get("version"),
                    "latest_version": latest_ver,
                    "repo_name": repo["name"],
                    "description": desc[:80],
                })

    # チェック日時を更新
    au = reg.setdefault("auto_update", _default_auto_update())
    au["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    save_registry(reg)

    return updates


def run_auto_update(force: bool = False, explicit: bool = False) -> None:
    """
    自動更新のメインエントリーポイント。

    explicit=True の場合: enabled フラグを無視し、インターバルチェックのみで判定する。
    notify_only=True の場合: 更新可能なスキルを表示するのみ。
    notify_only=False の場合: 自動で pull を実行する。
    """
    reg = load_registry()
    au = reg.get("auto_update", {})

    if not force and not explicit and not au.get("enabled", False):
        return

    if not force and not _should_check(reg):
        if explicit:
            interval = au.get("interval_hours", 24)
            last = au.get("last_checked_at", "未チェック")
            print(f"⏭️  インターバル未達のためスキップ (間隔: {interval}h, 最終チェック: {last})")
        return

    updates = check_updates(force=force)
    if not updates:
        if force:
            print("✅ すべてのスキルは最新です")
        return

    sync_policy = reg.get("sync_policy", {})
    auto_patch = sync_policy.get("auto_accept_patch", True)
    auto_minor = sync_policy.get("auto_accept_minor", False)
    notify_only = au.get("notify_only", True)

    # 更新をバージョン種別で分類
    auto_pull_updates = []
    review_updates = []
    for u in updates:
        bump = _bump_type(u.get("current_version"), u.get("latest_version"))
        cur_label = u.get("current_version") or u["current_hash"]
        new_label = u.get("latest_version") or u["latest_hash"]
        u["_bump"] = bump
        u["_cur_label"] = cur_label
        u["_new_label"] = new_label
        if not notify_only:
            if bump == "patch" and auto_patch:
                auto_pull_updates.append(u)
            elif bump == "minor" and auto_minor:
                auto_pull_updates.append(u)
            elif bump in ("major",):
                review_updates.append(u)
            else:
                # unknown（バージョン情報なし）または上記以外は自動 pull
                auto_pull_updates.append(u)
        else:
            review_updates.append(u)

    if auto_pull_updates or review_updates:
        print(f"\n🔔 {len(updates)} 件のスキル更新があります:")
        for u in updates:
            desc = f"  {u['description']}" if u["description"] else ""
            bump_label = f" [{u['_bump']}]" if u.get("latest_version") else ""
            print(f"   📦 {u['name']} ({u['_cur_label']} → {u['_new_label']}){bump_label}{desc}")

    if auto_pull_updates:
        print(f"\n⬇️  自動更新を実行します ({len(auto_pull_updates)} 件)...")
        from pull import pull_skills
        for u in auto_pull_updates:
            pull_skills(repo_name=u["repo_name"], skill_name=u["name"], interactive=False)
        print("✅ 自動更新が完了しました")

    if review_updates:
        if notify_only:
            print('\n💡 更新するには「スキルをpullして」と指示してください')
        else:
            print(f"\n⚠️  手動確認が必要な更新 ({len(review_updates)} 件):")
            for u in review_updates:
                print(f"   📦 {u['name']} ({u['_cur_label']} → {u['_new_label']}, {u['_bump']}アップデート)")
            print("   💡 更新するには「スキルをpullして」と指示してください")

    # instructions を自動同期
    if not notify_only:
        try:
            from sync_instructions import sync_instructions
            changed = sync_instructions(force=force)
            if changed:
                print(f"\n📄 instructions: {changed} ファイルを更新しました")
        except Exception as e:
            print(f"\n⚠️  instructions 同期をスキップしました: {e}")


def configure_auto_update(
    enabled: bool | None = None,
    interval_hours: int | None = None,
    notify_only: bool | None = None,
) -> None:
    """auto_update 設定を変更する。"""
    reg = load_registry()
    au = reg.setdefault("auto_update", _default_auto_update())

    if enabled is not None:
        au["enabled"] = enabled
    if interval_hours is not None:
        if interval_hours < 1:
            print("❌ interval_hours は 1 以上で指定してください")
            return
        au["interval_hours"] = interval_hours
    if notify_only is not None:
        au["notify_only"] = notify_only

    save_registry(reg)

    status = "有効" if au["enabled"] else "無効"
    mode = "通知のみ" if au["notify_only"] else "自動pull"
    print(f"✅ 自動更新設定を保存しました")
    print(f"   状態: {status}")
    print(f"   チェック間隔: {au['interval_hours']} 時間")
    print(f"   モード: {mode}")
    if au.get("last_checked_at"):
        print(f"   最終チェック: {au['last_checked_at']}")


# --- CLI ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="スキルの自動更新チェック")
    sub = parser.add_subparsers(dest="command")

    check_cmd = sub.add_parser("check", help="更新をチェックする")
    check_cmd.add_argument("--force", action="store_true", help="間隔を無視して即座にチェック")

    config_cmd = sub.add_parser("configure", help="自動更新設定を変更する")
    config_cmd.add_argument("--enable", action="store_const", const=True, default=None, help="自動更新を有効化")
    config_cmd.add_argument("--disable", action="store_const", const=True, default=None, help="自動更新を無効化")
    config_cmd.add_argument("--interval", type=int, default=None, help="チェック間隔（時間）")
    config_cmd.add_argument("--notify-only", action="store_const", const=True, default=None, help="通知のみ（自動pullしない）")
    config_cmd.add_argument("--auto-pull", action="store_const", const=True, default=None, help="自動pullを有効化")

    status_cmd = sub.add_parser("status", help="現在の自動更新設定を表示する")

    args = parser.parse_args()

    if args.command == "check":
        run_auto_update(force=args.force, explicit=True)
    elif args.command == "configure":
        enabled = None
        if args.enable:
            enabled = True
        elif args.disable:
            enabled = False

        n_only = None
        if args.notify_only:
            n_only = True
        elif args.auto_pull:
            n_only = False

        configure_auto_update(
            enabled=enabled,
            interval_hours=args.interval,
            notify_only=n_only,
        )
    elif args.command == "status":
        reg = load_registry()
        au = reg.get("auto_update", _default_auto_update())
        status = "有効" if au.get("enabled") else "無効"
        mode = "通知のみ" if au.get("notify_only", True) else "自動pull"
        print(f"自動更新: {status}")
        print(f"チェック間隔: {au.get('interval_hours', 24)} 時間")
        print(f"モード: {mode}")
        last = au.get("last_checked_at", "未チェック")
        print(f"最終チェック: {last}")
    else:
        parser.print_help()
