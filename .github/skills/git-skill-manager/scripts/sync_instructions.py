#!/usr/bin/env python3
"""sync_instructions 操作: リモートリポジトリから instructions/ を同期する。

instructions/ はリポジトリルートに配置された言語・用途別コーディング指示 MD の集合。
このスクリプトはレジストリに登録された全リポジトリを対象に:
  1. リポジトリのキャッシュを更新（clone_or_fetch）
  2. `instructions/` ディレクトリを検出
  3. 変更ファイルのみ <AGENT_HOME>/instructions/ にコピー
  4. レジストリに最終同期日時を記録する

使い方:
    python sync_instructions.py [--force] [--repo REPO_NAME] [--dry-run]

オプション:
    --force        更新チェック間隔（interval_hours）を無視して強制実行
    --repo NAME    指定リポジトリのみを対象にする
    --dry-run      ファイルをコピーせずに変更内容を表示する
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
from datetime import datetime, timezone

from registry import load_registry, save_registry, _instructions_home, _transform_frontmatter_for_kiro
from repo import clone_or_fetch


# リポジトリ内の instructions フォルダ名（規約）
INSTRUCTIONS_DIR_NAME = "instructions"


def _file_hash(path: str) -> str:
    """ファイルの MD5 ハッシュを返す。存在しない場合は空文字列。"""
    if not os.path.isfile(path):
        return ""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_instructions_root(repo_cache: str, repo: dict) -> str | None:
    """リポジトリキャッシュ内の instructions/ ディレクトリを探す。

    1. `instructions_root` フィールドが repo に定義されていればそれを使う
    2. なければリポジトリルートの `instructions/` を探す
    """
    custom = repo.get("instructions_root")
    if custom:
        candidate = os.path.join(repo_cache, custom)
        return candidate if os.path.isdir(candidate) else None

    candidate = os.path.join(repo_cache, INSTRUCTIONS_DIR_NAME)
    return candidate if os.path.isdir(candidate) else None


def sync_from_repo(
    repo: dict,
    dest_dir: str,
    *,
    dry_run: bool = False,
) -> list[dict]:
    """1 つのリポジトリから instructions を同期する。

    戻り値: 変更のあったファイルのリスト
        [{"file": "python.md", "action": "added"|"updated"|"unchanged"}, ...]
    """
    try:
        repo_cache = clone_or_fetch(repo)
    except Exception as e:
        print(f"   ⚠️  {repo['name']}: フェッチ失敗（{e}）— スキップ")
        return []

    src_dir = _find_instructions_root(repo_cache, repo)
    if not src_dir:
        return []

    os.makedirs(dest_dir, exist_ok=True)
    results = []
    is_kiro = load_registry().get("agent_type") == "kiro"

    for fname in sorted(os.listdir(src_dir)):
        if not fname.endswith(".md"):
            continue
        src_path = os.path.join(src_dir, fname)
        dest_path = os.path.join(dest_dir, fname)

        if not os.path.isfile(src_path):
            continue

        src_hash = _file_hash(src_path)
        dest_hash = _file_hash(dest_path)

        if src_hash == dest_hash:
            results.append({"file": fname, "action": "unchanged"})
            continue

        action = "added" if not os.path.exists(dest_path) else "updated"
        if not dry_run:
            if is_kiro:
                with open(src_path, encoding="utf-8") as f:
                    content = f.read()
                with open(dest_path, "w", encoding="utf-8") as f:
                    f.write(_transform_frontmatter_for_kiro(content))
            else:
                shutil.copy2(src_path, dest_path)

        results.append({"file": fname, "action": action})

    return results


def sync_instructions(
    *,
    force: bool = False,
    repo_name: str | None = None,
    dry_run: bool = False,
) -> int:
    """全登録リポジトリから instructions を同期する。

    戻り値: 変更されたファイル数
    """
    reg = load_registry()
    dest_dir = _instructions_home()
    repos = reg.get("repositories", [])

    if repo_name:
        repos = [r for r in repos if r["name"] == repo_name]
        if not repos:
            print(f"エラー: リポジトリ '{repo_name}' が見つかりません")
            return 0

    total_changed = 0
    found_any = False

    for repo in repos:
        results = sync_from_repo(repo, dest_dir, dry_run=dry_run)
        if results is None:
            continue

        changed = [r for r in results if r["action"] != "unchanged"]
        if not results:
            continue

        found_any = True
        print(f"   [{repo['name']}]")
        for r in results:
            icon = {"added": "+", "updated": "~", "unchanged": " "}[r["action"]]
            print(f"     {icon} {r['file']}")
        total_changed += len(changed)

    if not found_any:
        print("   （instructions/ フォルダが見つかりません、スキップ）")
        return 0

    # レジストリに同期日時を記録
    if not dry_run:
        reg.setdefault("instructions_sync", {})
        reg["instructions_sync"]["last_synced_at"] = datetime.now(timezone.utc).isoformat()
        save_registry(reg)

    return total_changed


def main() -> None:
    parser = argparse.ArgumentParser(description="instructions/ をリモートから同期する")
    parser.add_argument("--force", action="store_true", help="間隔を無視して強制実行")
    parser.add_argument("--repo", metavar="NAME", help="対象リポジトリを限定する")
    parser.add_argument("--dry-run", action="store_true", help="変更内容を表示するだけ（コピーしない）")
    args = parser.parse_args()

    if args.dry_run:
        print("（dry-run モード: ファイルはコピーされません）")

    print(f"同期先: {_instructions_home()}")
    changed = sync_instructions(
        force=args.force,
        repo_name=args.repo,
        dry_run=args.dry_run,
    )

    if changed:
        print(f"\n{changed} ファイルを更新しました")
    else:
        print("\nすべて最新です")


if __name__ == "__main__":
    main()
