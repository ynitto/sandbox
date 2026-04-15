#!/usr/bin/env python3
"""
wiki_utils.py — wiki-use スキル共通ユーティリティ

使い方:
  python scripts/wiki_utils.py config          # 現在の設定を表示
  python scripts/wiki_utils.py set-config      # 設定を対話的に作成・更新
"""

import argparse
import json
import os
import sys
from pathlib import Path


CONFIG_FILENAME = "wiki-config.json"
DEFAULT_SOURCE_DIR = "~/Downloads"


def get_agent_home() -> Path:
    return Path(os.path.expanduser("~"))


def get_config_path() -> Path:
    return get_agent_home() / CONFIG_FILENAME


def load_config() -> dict:
    """設定ファイルを読み込む。存在しない場合は RuntimeError を送出する。"""
    config_path = get_config_path()
    if not config_path.exists():
        raise RuntimeError(
            f"設定ファイルが見つかりません: {config_path}\n"
            "  python scripts/wiki_init.py を実行して初期化してください。"
        )
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    config_path = get_config_path()
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")


def resolve_wiki_root(config: dict) -> Path:
    """wiki_root を絶対パスに解決する。"""
    return Path(os.path.expanduser(config["wiki_root"]))


def resolve_source_dir(config: dict) -> Path:
    """default_source_dir を絶対パスに解決する。"""
    return Path(os.path.expanduser(config.get("default_source_dir", DEFAULT_SOURCE_DIR)))


def cmd_config(_args) -> None:
    """現在の設定を表示する。"""
    config_path = get_config_path()
    if not config_path.exists():
        print(f"[ERROR] 設定ファイルが存在しません: {config_path}", file=sys.stderr)
        print("  python scripts/wiki_init.py を実行して初期化してください。", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    wiki_root = resolve_wiki_root(config)
    source_dir = resolve_source_dir(config)

    print(f"config_path   : {config_path}")
    print(f"wiki_root     : {wiki_root}")
    print(f"default_source_dir: {source_dir}")
    print()
    print(f"wiki_root exists      : {wiki_root.exists()}")
    print(f"default_source_dir exists: {source_dir.exists()}")


def cmd_set_config(_args) -> None:
    """設定を対話的に作成・更新する。"""
    config_path = get_config_path()
    existing = {}
    if config_path.exists():
        with config_path.open(encoding="utf-8") as f:
            existing = json.load(f)

    print("=== wiki-use 設定 ===")
    print(f"設定ファイル: {config_path}")
    print()

    current_root = existing.get("wiki_root", "~/Documents/wiki")
    wiki_root = input(f"wiki_root [{current_root}]: ").strip() or current_root

    current_src = existing.get("default_source_dir", DEFAULT_SOURCE_DIR)
    source_dir = input(f"default_source_dir [{current_src}]: ").strip() or current_src

    config = {
        "wiki_root": wiki_root,
        "default_source_dir": source_dir,
    }
    save_config(config)
    print()
    print(f"[OK] 設定を保存しました: {config_path}")
    print(json.dumps(config, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="wiki-use 共通ユーティリティ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config", help="現在の設定を表示する")
    subparsers.add_parser("set-config", help="設定を対話的に作成・更新する")

    args = parser.parse_args()
    commands = {
        "config": cmd_config,
        "set-config": cmd_set_config,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
