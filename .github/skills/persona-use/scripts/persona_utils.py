#!/usr/bin/env python3
"""
persona_utils.py — persona-use スキル共通ユーティリティ

使い方:
  python scripts/persona_utils.py config       # 現在の設定を表示
  python scripts/persona_utils.py set-config   # 設定を対話的に作成・更新
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SKILL_NAME = "persona-use"
REGISTRY_FILENAME = "skill-registry.json"
DEFAULT_PERSONA_HOME = "~/.claude/persona"

_AGENT_DIR_NAMES = {".copilot", ".claude", ".codex", ".kiro"}


def _get_agent_home() -> Path:
    """scripts/ から3階層上がエージェントホームかを確認し、なければ標準パスへフォールバック。"""
    scripts_dir = Path(__file__).resolve().parent
    candidate = scripts_dir.parent.parent.parent
    if candidate.name in _AGENT_DIR_NAMES:
        return candidate
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


def _load_registry() -> dict:
    path = get_registry_path()
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _save_registry(reg: dict) -> None:
    path = get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_config() -> dict:
    """skill-registry.json の skill_configs["persona-use"] を返す。未設定なら RuntimeError。"""
    reg = _load_registry()
    config = reg.get("skill_configs", {}).get(SKILL_NAME)
    if config is None:
        raise RuntimeError(
            f"persona-use の設定が skill-registry.json に見つかりません: {get_registry_path()}\n"
            "  python scripts/init_persona.py を実行して初期化してください。"
        )
    return config


def save_config(config: dict) -> None:
    reg = _load_registry()
    reg.setdefault("skill_configs", {})[SKILL_NAME] = config
    _save_registry(reg)


def resolve_persona_home(config: dict) -> Path:
    return Path(os.path.expanduser(config["persona_home"]))


def cmd_config(_args) -> None:
    try:
        config = load_config()
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
    persona_home = resolve_persona_home(config)
    print(f"registry_path : {get_registry_path()}")
    print(f"persona_home  : {persona_home}")
    print(f"exists        : {persona_home.exists()}")


def cmd_set_config(_args) -> None:
    existing = {}
    try:
        existing = load_config()
    except RuntimeError:
        pass
    print("=== persona-use 設定 ===")
    print(f"保存先: {get_registry_path()} (skill_configs.persona-use)")
    current = existing.get("persona_home", DEFAULT_PERSONA_HOME)
    value = input(f"persona_home [{current}]: ").strip() or current
    config = {"persona_home": value}
    save_config(config)
    print(f"\n[OK] 設定を保存しました")
    print(json.dumps({"skill_configs": {SKILL_NAME: config}}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="persona-use 共通ユーティリティ")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="現在の設定を表示")
    sub.add_parser("set-config", help="設定を対話的に作成・更新")
    args = parser.parse_args()
    {"config": cmd_config, "set-config": cmd_set_config}[args.command](args)


if __name__ == "__main__":
    main()
