#!/usr/bin/env python3
"""
update_persona.py — ペルソナ観察を YYYY-MM-DD-update.md に記録する

エージェントが自律的に呼び出す。ユーザーへの表示は不要。
観察は当日の YYYY-MM-DD-update.md に追記され、batch-update で管理ファイルに反映される。

使い方:
  python scripts/update_persona.py --log "観察内容（1〜2文）"
  python scripts/update_persona.py --log "..." --date 2026-04-27
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from batch_update_persona import run_batch_update
from persona_utils import load_config, resolve_persona_home


def append_daily_log(persona_home: Path, observation: str, today: str) -> None:
    log_path = persona_home / f"{today}-update.md"
    if not log_path.exists():
        log_path.write_text(f"# ペルソナ更新 {today}\n\n", encoding="utf-8")

    content = log_path.read_text(encoding="utf-8")
    content = content.rstrip("\n") + f"\n- {observation}\n"
    log_path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="ペルソナ観察を日次ファイルに記録する")
    parser.add_argument("--log", required=True, help="観察内容（1〜2文）")
    parser.add_argument("--date", default=date.today().isoformat(), help="日付（YYYY-MM-DD）")
    args = parser.parse_args()

    try:
        config = load_config()
    except RuntimeError as e:
        print(f"[SKIP] {e}", file=sys.stderr)
        sys.exit(0)

    persona_home = resolve_persona_home(config)
    if not persona_home.exists():
        print(f"[SKIP] persona_home が存在しません: {persona_home}", file=sys.stderr)
        sys.exit(0)

    run_batch_update(persona_home)
    append_daily_log(persona_home, args.log, args.date)


if __name__ == "__main__":
    main()
