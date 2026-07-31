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
from persona_utils import load_config, resolve_persona_home

_UPDATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-update\.md$")


def find_update_files(persona_home: Path) -> list[Path]:
    if not persona_home.exists():
        return []
    return sorted(f for f in persona_home.iterdir() if _UPDATE_FILE_RE.match(f.name))


def run_batch_update(persona_home: Path, dry_run: bool = False) -> bool:
    """YYYY-MM-DD-update.md を処理して削除する。処理対象があれば True を返す。
    他スクリプトから呼び出して自動的に一括更新を行う。古い日付のファイルも含めてすべて処理する。"""
    update_files = find_update_files(persona_home)
    if not update_files:
        return False

    print("=== ペルソナ一括更新 ===")
    print(
        "以下の観察ログを読んで、profile.md / preferences.md / expertise.md の"
        "該当セクションを更新してください。\n"
        "【注意】既存ファイルの内容と重複する記述は追加しないこと。"
        "既存記述と矛盾する場合は上書き、新規情報のみ追記してください。"
    )
    print()

    for path in update_files:
        print(f"--- {path.name} ---")
        print(path.read_text(encoding="utf-8"))
        print()

    if dry_run:
        print("[DRY-RUN] ファイルは削除されませんでした。")
        return True

    for path in update_files:
        path.unlink()
        print(f"[削除] {path.name}")

    print("[OK] 一括更新完了\n")
    return True


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

    if not run_batch_update(persona_home, dry_run=args.dry_run):
        print("(処理対象の更新ファイルがありません)")


if __name__ == "__main__":
    main()
