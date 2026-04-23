#!/usr/bin/env python3
"""
update_persona.py — ペルソナ更新ログに観察を追記する

エージェントが自律的に呼び出す。ユーザーへの表示は不要。

使い方:
  python scripts/update_persona.py --log "観察内容（1〜2文）"
  python scripts/update_persona.py --log "..." --date 2026-04-23
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from persona_utils import load_config, resolve_persona_home


def append_log(persona_home: Path, observation: str, today: str) -> None:
    log_path = persona_home / "update_log.md"
    if not log_path.exists():
        log_path.write_text("# ペルソナ更新ログ\n\n", encoding="utf-8")

    content = log_path.read_text(encoding="utf-8")

    # 同じ日付のセクションが既にあれば追記、なければ先頭に新セクション追加
    section_header = f"## {today}"
    entry = f"- {observation}"

    if section_header in content:
        # 既存セクションの最後の行の後に追記
        idx = content.index(section_header)
        next_section = content.find("\n## ", idx + 1)
        if next_section == -1:
            content = content + entry + "\n"
        else:
            content = content[:next_section] + entry + "\n\n" + content[next_section:]
    else:
        # ヘッダー直後に新セクションを挿入
        header_end = content.find("\n", content.find("# ペルソナ更新ログ")) + 1
        new_section = f"\n{section_header}\n\n{entry}\n"
        content = content[:header_end] + new_section + content[header_end:]

    log_path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="ペルソナ更新ログに観察を追記する")
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

    append_log(persona_home, args.log, args.date)


if __name__ == "__main__":
    main()
