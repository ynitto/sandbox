#!/usr/bin/env python3
"""
wiki_init.py — Wiki を初期化する

使い方:
  python scripts/wiki_init.py              # 対話的に設定して初期化
  python scripts/wiki_init.py --non-interactive \
      --wiki-root ~/Documents/wiki         # 非対話モード
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# wiki_utils を同ディレクトリから import
sys.path.insert(0, str(Path(__file__).parent))
from wiki_utils import (
    get_registry_path,
    get_agent_home,
    load_config,
    save_config,
    resolve_wiki_root,
)


SCHEMA_TEMPLATE = """\
# Wiki スキーマ

このファイルは Wiki の構造・規約を定義します。LLM はこのファイルを参照して
一貫したページを作成・更新してください。このファイル自体を LLM とユーザーが
協力して育てることで、ドメインに最適化された Wiki 運用ルールを確立していく。

## ディレクトリ構造

- `wiki/atoms/`  — 個別トピックのページ（概念・用語・人物・製品・組織など）
- `wiki/topics/` — 複数 atom を横断するまとめ・比較・分析ページ
- `wiki/meta/`   — hot.md（最近のコンテキスト）
- `sources/`     — 取り込み元の原文（変更しない）

## ページ規約

- フロントマター必須（title, type, tags, created, updated, sources, summary）
- `type` フィールド: concept | term | person | organization | product | topic
- ウィキリンク形式: [[ファイル名]]（拡張子なし）
- ファイル名: 英小文字 + ハイフン（例: attention-mechanism.md）
- 各ページに `## 関連` セクションを設け、関連ページをリンクする
- `summary` は index.md 用の1文説明（80文字以内）

## このWikiのドメイン

（このWikiが扱うテーマ・領域をここに記入してください）

## カスタムルール

（このWikiに特有の運用ルール・強調したい観点があれば追記してください）
"""

INDEX_TEMPLATE = """\
# Wiki インデックス

最終更新: {today}

## atoms

## topics
"""

LOG_TEMPLATE = """\
# Wiki 操作ログ

## {today} — init

- Wiki を初期化しました
- wiki_root: {wiki_root}
"""

HOT_TEMPLATE = """\
# Hot Pages（最近のコンテキスト）

最終更新: {today}

<!-- 新しい取り込みで更新される。最大20件 -->
"""


def create_structure(wiki_root: Path) -> None:
    """Wiki のディレクトリ構造を作成する。"""
    dirs = [
        wiki_root / "sources",
        wiki_root / "wiki" / "atoms",
        wiki_root / "wiki" / "topics",
        wiki_root / "wiki" / "meta",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        gitkeep = d / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()

    today = date.today().isoformat()

    # SCHEMA.md
    schema_path = wiki_root / "SCHEMA.md"
    if not schema_path.exists():
        schema_path.write_text(SCHEMA_TEMPLATE, encoding="utf-8")
        print(f"  作成: {schema_path}")
    else:
        print(f"  スキップ（既存）: {schema_path}")

    # index.md
    index_path = wiki_root / "index.md"
    if not index_path.exists():
        index_path.write_text(INDEX_TEMPLATE.format(today=today), encoding="utf-8")
        print(f"  作成: {index_path}")
    else:
        print(f"  スキップ（既存）: {index_path}")

    # log.md
    log_path = wiki_root / "log.md"
    if not log_path.exists():
        log_path.write_text(
            LOG_TEMPLATE.format(today=today, wiki_root=wiki_root), encoding="utf-8"
        )
        print(f"  作成: {log_path}")
    else:
        print(f"  スキップ（既存）: {log_path}")

    # wiki/meta/hot.md
    hot_path = wiki_root / "wiki" / "meta" / "hot.md"
    if not hot_path.exists():
        hot_path.write_text(HOT_TEMPLATE.format(today=today), encoding="utf-8")
        print(f"  作成: {hot_path}")
    else:
        print(f"  スキップ（既存）: {hot_path}")


def cmd_init_interactive() -> None:
    """対話的に設定を作成して Wiki を初期化する。"""
    registry_path = get_registry_path()
    existing = {}
    try:
        existing = load_config()
        print(f"既存の設定が見つかりました: {registry_path} (skill_configs.wiki-use)")
        print(f"  wiki_root : {existing.get('wiki_root', '(未設定)')}")
        print()
    except RuntimeError:
        pass

    print("=== wiki-use 初期化 ===")
    current_root = existing.get("wiki_root", "~/Documents/wiki")
    wiki_root_str = input(f"wiki_root [{current_root}]: ").strip() or current_root

    config = {
        "wiki_root": wiki_root_str,
    }
    save_config(config)
    print(f"\n[OK] 設定を保存しました: {registry_path} (skill_configs.wiki-use)")

    wiki_root = Path(os.path.expanduser(wiki_root_str))
    print(f"\nディレクトリ構造を作成します: {wiki_root}")
    create_structure(wiki_root)

    print("\n=== 初期化完了 ===")
    print(f"  wiki_root   : {wiki_root}")
    print(f"  sources     : {wiki_root / 'sources'}")
    print(f"  wiki/atoms  : {wiki_root / 'wiki' / 'atoms'}")
    print(f"  wiki/topics : {wiki_root / 'wiki' / 'topics'}")
    print(f"  SCHEMA.md   : {wiki_root / 'SCHEMA.md'}")
    print(f"  index.md    : {wiki_root / 'index.md'}")
    print(f"  log.md      : {wiki_root / 'log.md'}")
    print()
    print("次のステップ:")
    print("  1. SCHEMA.md の「このWikiのドメイン」欄にテーマ・領域を記入する")
    print("  2. ソースファイルを取り込む: python scripts/wiki_ingest.py copy --source <path>")


def cmd_init_non_interactive(wiki_root_str: str) -> None:
    """非対話モードで Wiki を初期化する。"""
    config = {
        "wiki_root": wiki_root_str,
    }
    save_config(config)
    registry_path = get_registry_path()
    print(f"[OK] 設定を保存しました: {registry_path} (skill_configs.wiki-use)")

    wiki_root = Path(os.path.expanduser(wiki_root_str))
    print(f"ディレクトリ構造を作成します: {wiki_root}")
    create_structure(wiki_root)
    print("[OK] 初期化完了")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wiki を初期化する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="非対話モード（--wiki-root が必須）",
    )
    parser.add_argument("--wiki-root", help="wiki のルートディレクトリ")

    args = parser.parse_args()

    if args.non_interactive:
        if not args.wiki_root:
            parser.error("--non-interactive モードでは --wiki-root が必須です")
        cmd_init_non_interactive(args.wiki_root)
    else:
        cmd_init_interactive()


if __name__ == "__main__":
    main()
