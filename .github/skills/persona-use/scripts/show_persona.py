#!/usr/bin/env python3
"""
show_persona.py — ペルソナを表示する

使い方:
  python scripts/show_persona.py                    # 全セクション表示
  python scripts/show_persona.py --section profile
  python scripts/show_persona.py --section preferences
  python scripts/show_persona.py --section expertise
  python scripts/show_persona.py --summary           # 1行サマリーのみ
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from persona_utils import load_config, resolve_persona_home

SECTIONS = ["profile", "preferences", "expertise"]
FILE_MAP = {
    "profile": "profile.md",
    "preferences": "preferences.md",
    "expertise": "expertise.md",
}


def read_section(persona_home: Path, section: str) -> str | None:
    path = persona_home / FILE_MAP[section]
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def print_summary(persona_home: Path) -> None:
    """エージェントがコンテキストに素早く取り込むための1行サマリー。"""
    lines = []
    for section in SECTIONS:
        content = read_section(persona_home, section)
        if content:
            # フロントマターとコメント行を除いたテキストを取得
            body = "\n".join(
                l for l in content.splitlines()
                if l.strip() and not l.startswith("---") and not l.startswith("<!--")
            )
            lines.append(f"[{section}]\n{body[:400]}")
    if lines:
        print("\n\n".join(lines))
    else:
        print("(ペルソナ未設定)")


def main() -> None:
    parser = argparse.ArgumentParser(description="ペルソナを表示する")
    parser.add_argument("--section", choices=SECTIONS, default=None)
    parser.add_argument("--summary", action="store_true", help="コンパクトなサマリー表示")
    args = parser.parse_args()

    try:
        config = load_config()
    except RuntimeError as e:
        print(f"[INFO] {e}", file=sys.stderr)
        print("(ペルソナ未設定 — スキップ)")
        sys.exit(0)

    persona_home = resolve_persona_home(config)

    if not persona_home.exists():
        print(f"(persona_home が存在しません: {persona_home} — init_persona.py を実行してください)")
        sys.exit(0)

    if args.summary:
        print_summary(persona_home)
        return

    if args.section:
        content = read_section(persona_home, args.section)
        if content is None:
            print(f"(ファイルが見つかりません: {persona_home / FILE_MAP[args.section]})")
        else:
            print(content)
        return

    # 全セクション表示
    print(f"=== ユーザーペルソナ ({persona_home}) ===\n")
    for section in SECTIONS:
        content = read_section(persona_home, section)
        if content:
            print(f"--- {FILE_MAP[section]} ---")
            print(content)
            print()


if __name__ == "__main__":
    main()
