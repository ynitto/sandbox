#!/usr/bin/env python3
"""スキルスナップショット管理。

pull 前に現在のスキル状態を保存し、問題が発生した場合に元に戻せる仕組み。

使い方:
    python snapshot.py save              # 現在の状態を保存
    python snapshot.py save --label "1.3.0対応前"  # ラベル付きで保存
    python snapshot.py list              # スナップショット一覧を表示
    python snapshot.py restore --latest  # 直近スナップショットに戻す
    python snapshot.py restore <snap-id> # 指定スナップショットに戻す
    python snapshot.py clean             # 古いスナップショットを削除（最新5件を保持）

スナップショットの保存先:
    ~/.copilot/snapshots/snapshot-{timestamp}/
        ├── meta.json          (作成日時・ラベル・スキル一覧)
        ├── skill-registry.json
        └── skills/            (~/.copilot/skills/ のコピー)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from registry import load_registry, save_registry, _skill_home


def _snapshots_dir() -> str:
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".copilot", "snapshots")


def _registry_path() -> str:
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".copilot", "skill-registry.json")


def save_snapshot(label: str = "") -> str:
    """現在のスキル状態をスナップショットとして保存する。

    Returns:
        保存したスナップショットのID（ディレクトリ名）
    """
    now = datetime.now(timezone.utc)
    snap_id = "snapshot-" + now.strftime("%Y%m%dT%H%M%S")
    snap_dir = os.path.join(_snapshots_dir(), snap_id)
    os.makedirs(snap_dir, exist_ok=True)

    # レジストリを保存
    reg_src = _registry_path()
    if os.path.isfile(reg_src):
        shutil.copy2(reg_src, os.path.join(snap_dir, "skill-registry.json"))

    # スキルファイルを保存
    skill_home = _skill_home()
    skills_dest = os.path.join(snap_dir, "skills")
    if os.path.isdir(skill_home):
        shutil.copytree(skill_home, skills_dest)
    else:
        os.makedirs(skills_dest, exist_ok=True)

    # スキル一覧を記録
    reg = load_registry()
    installed = [
        {
            "name": s["name"],
            "commit_hash": s.get("commit_hash", "-"),
            "source_repo": s.get("source_repo", "-"),
            "local_modified": s.get("lineage", {}).get("local_modified", False),
        }
        for s in reg.get("installed_skills", [])
    ]

    meta = {
        "snap_id": snap_id,
        "created_at": now.isoformat(),
        "label": label,
        "skill_count": len(installed),
        "skills": installed,
    }
    with open(os.path.join(snap_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"📸 スナップショットを保存しました: {snap_id}")
    if label:
        print(f"   ラベル: {label}")
    print(f"   スキル数: {len(installed)} 件")
    print(f"   保存先: {snap_dir}")
    print(f"   復元: python snapshot.py restore --latest")

    return snap_id


def list_snapshots() -> list:
    """スナップショット一覧を返す（新しい順）。"""
    snap_dir = _snapshots_dir()
    if not os.path.isdir(snap_dir):
        print("ℹ️  スナップショットがありません")
        return []

    snaps = []
    for entry in sorted(os.listdir(snap_dir), reverse=True):
        meta_path = os.path.join(snap_dir, entry, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        snaps.append(meta)

    if not snaps:
        print("ℹ️  スナップショットがありません")
        return []

    print(f"📋 スナップショット一覧 ({len(snaps)} 件):\n")
    for i, s in enumerate(snaps):
        label_str = f"  [{s['label']}]" if s.get("label") else ""
        created = s["created_at"][:19].replace("T", " ")
        marker = " ← 最新" if i == 0 else ""
        print(f"  {s['snap_id']}  {created}{label_str}  スキル:{s['skill_count']}件{marker}")

    return snaps


def restore_snapshot(snap_id: str | None = None, latest: bool = False) -> bool:
    """スナップショットから状態を復元する。

    Args:
        snap_id: 復元するスナップショットID（ディレクトリ名）
        latest: True の場合は最新のスナップショットを使用

    Returns:
        成功した場合 True
    """
    snap_base = _snapshots_dir()

    if latest:
        if not os.path.isdir(snap_base):
            print("❌ スナップショットが見つかりません")
            return False
        entries = sorted(
            [e for e in os.listdir(snap_base)
             if os.path.isfile(os.path.join(snap_base, e, "meta.json"))],
            reverse=True,
        )
        if not entries:
            print("❌ スナップショットが見つかりません")
            return False
        snap_id = entries[0]

    if not snap_id:
        print("❌ スナップショットIDを指定するか --latest を使用してください")
        return False

    snap_dir = os.path.join(snap_base, snap_id)
    if not os.path.isdir(snap_dir):
        print(f"❌ スナップショット '{snap_id}' が見つかりません")
        return False

    meta_path = os.path.join(snap_dir, "meta.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    created = meta["created_at"][:19].replace("T", " ")
    label_str = f" [{meta['label']}]" if meta.get("label") else ""
    print(f"🔄 スナップショット '{snap_id}' から復元します")
    print(f"   保存日時: {created}{label_str}")
    print(f"   スキル数: {meta['skill_count']} 件")

    # スキルファイルを復元
    skill_home = _skill_home()
    skills_src = os.path.join(snap_dir, "skills")

    if os.path.isdir(skill_home):
        shutil.rmtree(skill_home)
    if os.path.isdir(skills_src):
        shutil.copytree(skills_src, skill_home)
    else:
        os.makedirs(skill_home, exist_ok=True)

    # レジストリを復元
    reg_src = os.path.join(snap_dir, "skill-registry.json")
    reg_dest = _registry_path()
    if os.path.isfile(reg_src):
        os.makedirs(os.path.dirname(reg_dest), exist_ok=True)
        shutil.copy2(reg_src, reg_dest)

    print(f"\n✅ 復元完了")
    print(f"   復元したスキル:")
    for s in meta.get("skills", []):
        mod = " (ローカル改善版)" if s.get("local_modified") else ""
        print(f"     {s['name']:30s} ← {s['source_repo']} ({s['commit_hash']}){mod}")

    return True


def clean_snapshots(keep: int = 5) -> None:
    """古いスナップショットを削除する（最新 keep 件を保持）。"""
    snap_base = _snapshots_dir()
    if not os.path.isdir(snap_base):
        return

    entries = sorted(
        [e for e in os.listdir(snap_base)
         if os.path.isfile(os.path.join(snap_base, e, "meta.json"))],
        reverse=True,
    )

    to_delete = entries[keep:]
    if not to_delete:
        print(f"ℹ️  削除対象なし（{len(entries)}/{keep} 件）")
        return

    for entry in to_delete:
        shutil.rmtree(os.path.join(snap_base, entry))
        print(f"🗑️  削除: {entry}")

    print(f"✅ {len(to_delete)} 件削除（残: {keep} 件）")


def main():
    parser = argparse.ArgumentParser(description="スキルスナップショット管理")
    sub = parser.add_subparsers(dest="command")

    save_p = sub.add_parser("save", help="現在の状態をスナップショット保存する")
    save_p.add_argument("--label", default="", help="スナップショットのラベル（任意）")

    sub.add_parser("list", help="スナップショット一覧を表示する")

    restore_p = sub.add_parser("restore", help="スナップショットから復元する")
    restore_p.add_argument("snap_id", nargs="?", help="スナップショットID")
    restore_p.add_argument("--latest", action="store_true", help="最新のスナップショットを使用する")

    clean_p = sub.add_parser("clean", help="古いスナップショットを削除する")
    clean_p.add_argument("--keep", type=int, default=5, help="保持するスナップショット数（デフォルト: 5）")

    args = parser.parse_args()

    if args.command == "save":
        save_snapshot(label=getattr(args, "label", ""))
    elif args.command == "list":
        list_snapshots()
    elif args.command == "restore":
        snap_id = getattr(args, "snap_id", None)
        latest = getattr(args, "latest", False)
        if not snap_id and not latest:
            print("❌ スナップショットIDを指定するか --latest を使用してください")
            sys.exit(1)
        success = restore_snapshot(snap_id=snap_id, latest=latest)
        if not success:
            sys.exit(1)
    elif args.command == "clean":
        clean_snapshots(keep=args.keep)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
