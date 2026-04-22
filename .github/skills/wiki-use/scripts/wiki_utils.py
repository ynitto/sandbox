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


SKILL_NAME = "wiki-use"
REGISTRY_FILENAME = "skill-registry.json"
DEFAULT_SOURCE_DIR = "~/Downloads"

_AGENT_DIR_NAMES = {".copilot", ".claude", ".codex", ".kiro"}


def _get_agent_home() -> Path:
    """エージェントホームを scripts/ から 3 階層上で探す。見つからなければ既存ファイルのある標準パスへフォールバック。"""
    scripts_dir = Path(__file__).resolve().parent
    # scripts/ -> wiki-use/ -> skills/ -> agent_home/
    candidate = scripts_dir.parent.parent.parent
    if candidate.name in _AGENT_DIR_NAMES:
        return candidate
    # 標準エージェントホームを順に確認（skill-registry.json が存在する最初のパス）
    home = Path(os.path.expanduser("~"))
    for d in (".claude", ".copilot", ".kiro", ".codex"):
        p = home / d
        if (p / REGISTRY_FILENAME).exists():
            return p
    return home / ".claude"


def get_agent_home() -> Path:
    return _get_agent_home()


def get_registry_path() -> Path:
    return _get_agent_home() / REGISTRY_FILENAME


def _load_registry_json() -> dict:
    """skill-registry.json を読み込む。存在しなければ空辞書を返す。"""
    path = get_registry_path()
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _save_registry_json(reg: dict) -> None:
    """skill-registry.json に書き戻す。"""
    path = get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_config() -> dict:
    """skill-registry.json の skill_configs["wiki-use"] を読み込む。存在しない場合は RuntimeError を送出する。"""
    reg = _load_registry_json()
    config = reg.get("skill_configs", {}).get(SKILL_NAME)
    if config is None:
        raise RuntimeError(
            f"wiki-use の設定が skill-registry.json に見つかりません: {get_registry_path()}\n"
            "  python scripts/wiki_init.py を実行して初期化してください。"
        )
    return config


def save_config(config: dict) -> None:
    """skill-registry.json の skill_configs["wiki-use"] に設定を書き込む。"""
    reg = _load_registry_json()
    reg.setdefault("skill_configs", {})[SKILL_NAME] = config
    _save_registry_json(reg)


def resolve_wiki_root(config: dict) -> Path:
    """wiki_root を絶対パスに解決する。"""
    return Path(os.path.expanduser(config["wiki_root"]))


def resolve_source_dir(config: dict) -> Path:
    """default_source_dir を絶対パスに解決する。"""
    return Path(os.path.expanduser(config.get("default_source_dir", DEFAULT_SOURCE_DIR)))


def cmd_config(_args) -> None:
    """現在の設定を表示する。"""
    registry_path = get_registry_path()
    try:
        config = load_config()
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    wiki_root = resolve_wiki_root(config)
    source_dir = resolve_source_dir(config)

    print(f"registry_path      : {registry_path}")
    print(f"wiki_root          : {wiki_root}")
    print(f"default_source_dir : {source_dir}")
    print()
    print(f"wiki_root exists           : {wiki_root.exists()}")
    print(f"default_source_dir exists  : {source_dir.exists()}")


def cmd_set_config(_args) -> None:
    """設定を対話的に作成・更新する。"""
    registry_path = get_registry_path()
    existing = {}
    try:
        existing = load_config()
    except RuntimeError:
        pass

    print("=== wiki-use 設定 ===")
    print(f"保存先: {registry_path} (skill_configs.wiki-use)")
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
    print(f"[OK] 設定を保存しました: {registry_path}")
    print(json.dumps({"skill_configs": {SKILL_NAME: config}}, ensure_ascii=False, indent=2))


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
