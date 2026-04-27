#!/usr/bin/env python3
"""
init_persona.py — ペルソナフォルダを初期化する

使い方:
  python scripts/init_persona.py                              # 対話モード
  python scripts/init_persona.py --non-interactive \
      --persona-home ~/.claude/persona                        # 非対話モード
  python scripts/init_persona.py --reset                      # 既存ファイルを上書きリセット
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from batch_update_persona import run_batch_update
from persona_utils import (
    get_registry_path,
    load_config,
    save_config,
    resolve_persona_home,
    DEFAULT_PERSONA_HOME,
)

TODAY = date.today().isoformat()

PROFILE_TEMPLATE = f"""\
---
updated: {TODAY}
---

# ユーザープロファイル

## コミュニケーションスタイル

- **使用言語**: （例: 日本語優先）
- **口調**: （例: 丁寧語）
- **応答の長さの好み**: （例: 簡潔）
- **説明の詳しさ**: （例: 結論を先に）

## 作業パターン

- **主なタスク種別**: （例: バックエンド開発、スクリプト作成）
- **作業環境**: （例: Linux / VS Code）
- **繰り返し出てくる指示**: （例: 必ず日本語で回答）

## 備考

"""

PREFERENCES_TEMPLATE = f"""\
---
updated: {TODAY}
---

# 嗜好・フォーマット好み

## コーディングスタイル

- **インデント**: （例: スペース4つ）
- **コメント**: （例: 最小限）
- **命名規約**: （例: snake_case）

## 出力フォーマット好み

- **コード量**: （例: 動くコード全体を出す）
- **説明スタイル**: （例: 箇条書きより文章）
- **図表**: （例: Mermaid歓迎）

## ツール・環境

- **シェル**: （例: bash）
- **パッケージマネージャ**: （例: pip / npm）
- **好みのライブラリ**: （例: requests, pytest）

"""

EXPERTISE_TEMPLATE = f"""\
---
updated: {TODAY}
---

# 専門領域・技術スタック

## プログラミング言語

| 言語 | レベル | 備考 |
|------|--------|------|
| （例: Python） | 上級 | 主力言語 |

## フレームワーク・ライブラリ

| 名称 | カテゴリ | レベル |
|------|----------|--------|

## インフラ・ツール

| 名称 | 用途 | レベル |
|------|------|--------|

## ドメイン知識

- （例: Webアプリ開発）
- （例: データ処理・自動化）

"""

def init_files(persona_home: Path, reset: bool = False) -> None:
    persona_home.mkdir(parents=True, exist_ok=True)

    files = {
        "profile.md": PROFILE_TEMPLATE,
        "preferences.md": PREFERENCES_TEMPLATE,
        "expertise.md": EXPERTISE_TEMPLATE,
    }
    for name, content in files.items():
        path = persona_home / name
        if path.exists() and not reset:
            print(f"   (スキップ) {path}")
        else:
            path.write_text(content, encoding="utf-8")
            print(f"   {'(上書き)' if reset and path.exists() else '(作成)  '} {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ペルソナフォルダを初期化する")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--persona-home", default=None)
    parser.add_argument("--reset", action="store_true", help="既存ファイルを上書きリセット")
    args = parser.parse_args()

    # 設定の決定
    if args.persona_home:
        persona_home_str = args.persona_home
    elif args.non_interactive:
        try:
            config = load_config()
            persona_home_str = config["persona_home"]
        except RuntimeError:
            persona_home_str = DEFAULT_PERSONA_HOME
    else:
        try:
            existing = load_config()
            current = existing.get("persona_home", DEFAULT_PERSONA_HOME)
        except RuntimeError:
            current = DEFAULT_PERSONA_HOME
        print("=== persona-use 初期化 ===")
        print(f"設定保存先: {get_registry_path()} (skill_configs.persona-use)")
        print()
        value = input(f"persona_home [{current}]: ").strip() or current
        persona_home_str = value

    persona_home = Path(os.path.expanduser(persona_home_str))

    # レジストリに保存
    save_config({"persona_home": str(persona_home_str)})

    print(f"\nペルソナフォルダ: {persona_home}")
    print()
    run_batch_update(persona_home)
    init_files(persona_home, reset=args.reset)

    print()
    if args.reset:
        print("[OK] ペルソナをリセットしました")
    else:
        print("[OK] 初期化完了")
    print(f"     ペルソナを編集: {persona_home}/profile.md")


if __name__ == "__main__":
    main()
