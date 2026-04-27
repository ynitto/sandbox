#!/usr/bin/env python3
"""
load_persona.py — ペルソナをエージェントコンテキストにロードする

セッション開始時または手動でペルソナをコンテキストに取り込み、
以降の応答をペルソナに合わせてパーソナライズするよう指示する。

使い方:
  python scripts/load_persona.py                    # 全セクションをロード
  python scripts/load_persona.py --section profile
  python scripts/load_persona.py --section preferences
  python scripts/load_persona.py --section expertise
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from batch_update_persona import run_batch_update
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


def load_all(persona_home: Path, sections: list[str]) -> None:
    print("=== ペルソナロード ===")
    print(
        "以下はユーザーのペルソナ情報です。"
        "これ以降の応答はこのペルソナに基づいてパーソナライズしてください。"
    )
    print()

    any_content = False
    for section in sections:
        content = read_section(persona_home, section)
        if content:
            print(f"--- {FILE_MAP[section]} ---")
            print(content)
            print()
            any_content = True

    if not any_content:
        print("(ペルソナ情報が見つかりません — init_persona.py を実行してください)")
        return

    print("=== ロード完了 ===")
    print(
        "上記ペルソナを反映した応答を行ってください。"
        "ユーザーの好みや専門性に合わせてコミュニケーションを調整してください。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="ペルソナをエージェントコンテキストにロードする")
    parser.add_argument("--section", choices=SECTIONS, default=None)
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

    run_batch_update(persona_home)

    sections = [args.section] if args.section else SECTIONS
    load_all(persona_home, sections)


if __name__ == "__main__":
    main()
