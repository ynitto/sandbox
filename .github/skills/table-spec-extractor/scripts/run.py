"""Document graph pipeline — entry point.

Subcommands:
    init    依存ライブラリのインストールとNeo4j疎通確認
    save    Excel/PDF → Table Transformer → AST → Neo4j（＋ローカルスナップショット）
    search  Neo4j グラフの全文＋トラバーサル検索
    config  プロファイル管理（add / list / show / remove / set-default）

Usage:
    python run.py init [--profile local]
    python run.py save report.pdf [--profile local] [--dry-run]
    python run.py search "売上 Q3" [--profile local]
    python run.py config add local --neo4j bolt://localhost:7687 --data-path ~/graph-data/local
    python run.py config list
"""
from __future__ import annotations
import sys
from pathlib import Path

# make sibling modules importable when run as a script
sys.path.insert(0, str(Path(__file__).parent))

import argparse
import json

from ast_builder import build_document
from config import Config, Profile, load_config, cmd_config, add_config_subparser
from graph_loader import Neo4jLoader
from markdown_serializer import document_to_markdown
from search import GraphSearcher, format_results


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _resolve_neo4j(args, profile: Profile | None) -> tuple[str, str, str, str]:
    """Return (uri, user, password, database) — CLI flags override profile."""
    uri = getattr(args, "neo4j", "") or (profile.neo4j_uri if profile else "")
    user = getattr(args, "user", "") or (profile.neo4j_user if profile else "neo4j")
    pwd = getattr(args, "password", "") or (profile.neo4j_password if profile else "")
    db = getattr(args, "database", "") or (profile.neo4j_database if profile else "neo4j")
    return uri, user, pwd, db


def _data_path(args, profile: Profile | None) -> Path | None:
    raw = getattr(args, "data_path", "") or (profile.data_path if profile else "")
    return Path(raw).expanduser() if raw else None


def _save_snapshot(doc, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(doc.source).stem + ".json"
    out = dest_dir / name
    from run import _ast_to_dict  # self-reference OK after sys.path is set
    out.write_text(json.dumps(_ast_to_dict(doc), ensure_ascii=False, indent=2))
    return out


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    import subprocess

    cfg = load_config()
    profile = cfg.get(getattr(args, "profile", ""))

    scripts_dir = Path(__file__).parent
    reqs = [scripts_dir / "requirements.txt"]
    if args.enable_pdf:
        reqs.append(scripts_dir / "requirements-pdf.txt")

    for req in reqs:
        label = req.name
        print(f"[{'1' if req == reqs[0] else '2'}/{len(reqs)+1}] {label} をインストール中 …")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"[✗] pip install 失敗 ({label}):\n{result.stderr}", file=sys.stderr)
            sys.exit(1)
    print("[✓] 依存ライブラリ: OK")

    uri, user, pwd, _ = _resolve_neo4j(args, profile)
    if not uri:
        print("[–] Neo4j URI が未指定のため接続確認をスキップ")
        return

    print(f"[2/2] Neo4j 接続確認: {uri} …")
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(uri, auth=(user, pwd))
        driver.verify_connectivity()
        driver.close()
        print(f"[✓] Neo4j 接続: OK ({user}@{uri})")
    except Exception as e:
        print(f"[✗] Neo4j 接続失敗: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

def _ast_to_dict(doc) -> dict:
    from models import Table, Paragraph

    def cell_d(c):
        return {"text": c.text, "row": c.row_idx, "col": c.col_idx,
                "header": c.is_header, "path": c.path}

    def row_d(r):
        return {"index": r.index, "header": r.is_header, "cells": [cell_d(c) for c in r.cells]}

    def table_d(t):
        return {"type": "table", "page": t.page, "sheet": t.sheet,
                "rows": [row_d(r) for r in t.rows]}

    def para_d(p):
        return {"type": "paragraph", "page": p.page,
                "text": p.text[:120] + ("…" if len(p.text) > 120 else "")}

    def section_d(s):
        return {"title": s.title, "page": s.page,
                "content": [table_d(i) if isinstance(i, Table) else para_d(i)
                            for i in s.content]}

    return {"source": doc.source, "metadata": doc.metadata,
            "sections": [section_d(s) for s in doc.sections]}


def cmd_save(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.exists():
        print(f"Error: ファイルが見つかりません: {path}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config()
    profile = cfg.get(getattr(args, "profile", ""))

    extractor = None
    if path.suffix.lower() == ".pdf":
        from table_extractor import TableTransformerExtractor
        extractor = TableTransformerExtractor(device=args.device, threshold=args.threshold)

    print(f"[1/3] 解析中: {path.name} …")
    doc = build_document(path, extractor=extractor, dpi=args.dpi)
    tables = doc.all_tables()
    print(f"      → {len(doc.sections)} sections, {len(tables)} tables, "
          f"{len(doc.all_paragraphs())} paragraphs")

    if args.dry_run:
        print(json.dumps(_ast_to_dict(doc), ensure_ascii=False, indent=2))
        return

    # ローカルスナップショット保存
    dp = _data_path(args, profile)
    if dp:
        snap = _save_snapshot(doc, dp)
        print(f"      → スナップショット保存: {snap}")

    uri, user, pwd, db = _resolve_neo4j(args, profile)
    if not uri:
        print("[!] Neo4j URI が未指定です。--dry-run でAST確認、または --profile / --neo4j を指定してください。")
        return

    print(f"[2/3] Neo4j 接続中: {uri} …")
    with Neo4jLoader(uri, user, pwd, db) as loader:
        loader.create_indexes()
        print("[3/3] グラフへロード中 …")
        loader.load(doc)
    print("完了。")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace) -> None:
    cfg = load_config()
    profile = cfg.get(getattr(args, "profile", ""))
    uri, user, pwd, db = _resolve_neo4j(args, profile)

    if not uri:
        print("Error: Neo4j URI が必要です (--neo4j または --profile).", file=sys.stderr)
        sys.exit(1)

    with GraphSearcher(uri, user, pwd, db) as searcher:
        result = searcher.search(args.query, limit=args.limit)

    if args.json:
        import dataclasses
        print(json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2))
    else:
        print(format_results(result))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_neo4j_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profile", default="", help="使用するプロファイル名")
    p.add_argument("--neo4j", default="", help="Neo4j bolt URI（プロファイルを上書き）")
    p.add_argument("--user", default="")
    p.add_argument("--password", default="")
    p.add_argument("--database", default="")


def main() -> None:
    root = argparse.ArgumentParser(description="Document graph pipeline")
    sub = root.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sub.add_parser("init", help="依存インストール＋Neo4j疎通確認")
    _add_neo4j_args(p_init)
    p_init.add_argument("--enable-pdf", action="store_true",
                        help="PDF処理用ライブラリも追加インストール（torch, transformers等）")

    # save
    p_save = sub.add_parser("save", help="ドキュメントを解析してNeo4jへロード")
    p_save.add_argument("file", help="PDFまたはExcelファイルのパス")
    _add_neo4j_args(p_save)
    p_save.add_argument("--data-path", default="", dest="data_path",
                        help="ローカルスナップショット保存先（プロファイルを上書き）")
    p_save.add_argument("--device", default="cpu")
    p_save.add_argument("--threshold", type=float, default=0.9)
    p_save.add_argument("--dpi", type=int, default=150)
    p_save.add_argument("--dry-run", action="store_true",
                        help="Neo4jへロードせずASTをJSON表示")

    # search
    p_search = sub.add_parser("search", help="グラフ全文＋トラバーサル検索")
    p_search.add_argument("query", help="検索クエリ文字列")
    _add_neo4j_args(p_search)
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--json", action="store_true", help="JSON形式で出力")

    # config
    add_config_subparser(sub)

    args = root.parse_args()
    dispatch = {
        "init": cmd_init,
        "save": cmd_save,
        "search": cmd_search,
        "config": cmd_config,
    }
    dispatch[args.cmd](args)


# backward compat for tests
def run(args: argparse.Namespace) -> None:
    cmd_save(args)


if __name__ == "__main__":
    main()
