#!/usr/bin/env python3
"""ノードアイデンティティ管理。

各ノード（開発環境）に一意のIDを付与し、
スキル改善の出所を追跡可能にする。

使い方:
    python node_identity.py init              # ノードIDを生成（未生成の場合のみ）
    python node_identity.py init --name "tokyo-team"  # 名前付きで生成
    python node_identity.py show              # 現在のノードIDを表示
    python node_identity.py reset             # ノードIDを再生成
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from registry import load_registry, save_registry


def _generate_node_id() -> str:
    """ランダムなノードIDを生成する。

    プレフィックス "node-" + UUIDv4 の先頭8文字。
    例: node-a1b2c3d4
    """
    return "node-" + uuid.uuid4().hex[:8]


def _default_node_name() -> str:
    """デフォルトのノード名としてホスト名を使用する。"""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown-host"


def init_node(name: str | None = None, force: bool = False) -> dict:
    """ノードIDを初期化する。既存IDがある場合はスキップ（force=True で強制上書き）。"""
    reg = load_registry()
    node = reg.get("node", {})

    if node.get("id") and not force:
        print(f"ℹ️  ノードIDは既に設定済みです: {node['id']}")
        print("   強制再生成するには --reset オプションを使用してください")
        return node

    node_id = _generate_node_id()
    node_name = name or _default_node_name()
    node_info = {
        "id": node_id,
        "name": node_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    reg["node"] = node_info
    save_registry(reg)

    print(f"✅ ノードIDを生成しました")
    print(f"   ID:   {node_id}")
    print(f"   名前: {node_name}")

    return node_info


def get_node_id() -> str | None:
    """現在のノードIDを返す。未設定の場合は None。"""
    reg = load_registry()
    return reg.get("node", {}).get("id")


def show_node() -> None:
    """ノード情報を表示する。"""
    reg = load_registry()
    node = reg.get("node", {})

    if not node.get("id"):
        print("⚠️  ノードIDが設定されていません")
        print("   'python node_identity.py init' で初期化してください")
        return

    print(f"📍 ノード情報:")
    print(f"   ID:          {node['id']}")
    print(f"   名前:        {node.get('name', '(未設定)')}")
    print(f"   作成日時:    {node.get('created_at', '(不明)')}")


def main():
    parser = argparse.ArgumentParser(description="ノードアイデンティティ管理")
    sub = parser.add_subparsers(dest="command")

    init_p = sub.add_parser("init", help="ノードIDを初期化する")
    init_p.add_argument("--name", help="ノードの識別名（省略時はホスト名）")

    sub.add_parser("show", help="現在のノード情報を表示する")

    reset_p = sub.add_parser("reset", help="ノードIDを再生成する")
    reset_p.add_argument("--name", help="ノードの識別名")

    args = parser.parse_args()

    if args.command == "init":
        init_node(name=getattr(args, "name", None))
    elif args.command == "show":
        show_node()
    elif args.command == "reset":
        init_node(name=getattr(args, "name", None), force=True)
        print("⚠️  ノードIDを再生成しました。既存の貢献記録との関連付けが切れます")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
