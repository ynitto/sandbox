#!/usr/bin/env python3
"""
batch_update_persona.py — YYYY-MM-DD-update.md の内容を管理ファイルに反映する

persona_home 内の YYYY-MM-DD-update.md ファイルを読み込んでエージェントに渡す。
エージェントはその内容を profile.md / preferences.md / expertise.md に反映した後、
スクリプトが更新ファイルを削除する。

使い方:
  python scripts/batch_update_persona.py            # 更新ファイルを表示して削除
  python scripts/batch_update_persona.py --dry-run  # 削除せずに内容のみ確認
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from persona_utils import cleanup_old_update_files, load_config, resolve_persona_home

_UPDATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-update\.md$")


def find_update_files(persona_home: Path) -> list[Path]:
    if not persona_home.exists():
        return []
    return sorted(f for f in persona_home.iterdir() if _UPDATE_FILE_RE.match(f.name))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YYYY-MM-DD-update.md の内容を管理ファイルに反映する"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="削除せずに内容のみ表示する"
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    persona_home = resolve_persona_home(config)

    if not persona_home.exists():
        print(f"[ERROR] persona_home が存在しません: {persona_home}", file=sys.stderr)
        sys.exit(1)

    cleanup_old_update_files(persona_home)

    update_files = find_update_files(persona_home)
    if not update_files:
        print("(処理対象の更新ファイルがありません)")
        sys.exit(0)

    print("=== ペルソナ一括更新 ===")
    print(
        "以下の観察ログを読んで、profile.md / preferences.md / expertise.md の"
        "該当セクションを更新してください。"
        "既存記述と矛盾する場合は上書き、補完できる場合は追記してください。"
    )
    print()

    for path in update_files:
        print(f"--- {path.name} ---")
        print(path.read_text(encoding="utf-8"))
        print()

    print("=== 更新ファイル一覧 ===")
    for path in update_files:
        print(f"  {path}")

    if args.dry_run:
        print("\n[DRY-RUN] ファイルは削除されませんでした。")
        return

    print("\n上記の内容を管理ファイルに反映してください。")
    print("反映後、以下の更新ファイルは自動的に削除されます。")

    for path in update_files:
        path.unlink()
        print(f"  [削除] {path.name}")

    print("\n[OK] 一括更新完了")


if __name__ == "__main__":
    main()
