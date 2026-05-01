#!/usr/bin/env python3
"""
wiki_query.py — Wiki を検索・閲覧するスクリプト

使い方:
  python scripts/wiki_query.py search "<キーワード>"
      キーワードで Wiki ページを全文検索する

  python scripts/wiki_query.py list-pages [--category atoms|topics]
      ページ一覧を表示する

  python scripts/wiki_query.py show <ページパス>
      ページ内容を表示する

  python scripts/wiki_query.py hot
      hot.md（最近のコンテキスト）を表示する
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from wiki_utils import load_config, resolve_wiki_root


def get_page_title(page_path: Path) -> str:
    """ページの frontmatter から title を返す。なければ stem を返す。"""
    if not page_path.exists():
        return page_path.stem
    text = page_path.read_text(encoding="utf-8")
    m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r'^#\s+(.+)', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return page_path.stem


def get_page_snippet(page_path: Path, max_chars: int = 100) -> str:
    """ページ本文の先頭スニペットを返す（frontmatter を除く）。"""
    text = page_path.read_text(encoding="utf-8")
    # frontmatter を除去
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:]
    # 見出し行を除去して最初のテキスト
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    snippet = " ".join(lines)
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "…"
    return snippet


def collect_wiki_pages(wiki_root: Path, category: str = None) -> list:
    """wiki/ 以下の .md ページを収集する。"""
    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.exists():
        return []

    categories = ["atoms", "topics"]
    if category:
        categories = [c for c in categories if c == category]

    pages = []
    for cat in categories:
        cat_dir = wiki_dir / cat
        if cat_dir.exists():
            for p in sorted(cat_dir.glob("*.md")):
                if p.name != ".gitkeep":
                    pages.append((cat, p))
    return pages


def cmd_search(args, wiki_root: Path) -> None:
    """キーワードで Wiki ページを全文検索する。"""
    keyword = args.keyword.lower()
    pages = collect_wiki_pages(wiki_root)

    results = []
    for cat, page_path in pages:
        text = page_path.read_text(encoding="utf-8").lower()
        if keyword in text:
            # マッチした行を抽出（最大3行）
            matched_lines = []
            for line in page_path.read_text(encoding="utf-8").splitlines():
                if keyword.lower() in line.lower() and line.strip():
                    matched_lines.append(line.strip())
                    if len(matched_lines) >= 3:
                        break
            results.append((cat, page_path, matched_lines))

    if not results:
        print(f"[INFO] '{args.keyword}' にマッチするページはありません")
        return

    print(f"検索結果: '{args.keyword}' — {len(results)} 件")
    print()
    for cat, page_path, matched_lines in results:
        title = get_page_title(page_path)
        rel = page_path.relative_to(wiki_root)
        print(f"  [{cat}] {title}")
        print(f"    パス: {rel}")
        for line in matched_lines:
            # マッチ箇所を強調
            print(f"    …{line}…")
        print()


def cmd_list_pages(args, wiki_root: Path) -> None:
    """ページ一覧を表示する。"""
    category = getattr(args, "category", None)
    pages = collect_wiki_pages(wiki_root, category)

    if not pages:
        print("[INFO] ページが見つかりません")
        return

    current_cat = None
    for cat, page_path in pages:
        if cat != current_cat:
            print(f"\n## {cat}")
            current_cat = cat
        title = get_page_title(page_path)
        stem = page_path.stem
        snippet = get_page_snippet(page_path, max_chars=60)
        print(f"  [[{stem}]] {title}")
        if snippet:
            print(f"    {snippet}")

    print(f"\n合計: {len(pages)} ページ")


def cmd_show(args, wiki_root: Path) -> None:
    """ページ内容を表示する。"""
    page_path = wiki_root / args.path
    if not page_path.exists():
        stem = Path(args.path).stem
        for cat in ["atoms", "topics"]:
            candidate = wiki_root / "wiki" / cat / f"{stem}.md"
            if candidate.exists():
                page_path = candidate
                break
        else:
            print(f"[ERROR] ページが見つかりません: {args.path}", file=sys.stderr)
            sys.exit(1)

    print(page_path.read_text(encoding="utf-8"))


def cmd_hot(args, wiki_root: Path) -> None:
    """hot.md を表示する。"""
    hot_path = wiki_root / "wiki" / "meta" / "hot.md"
    if not hot_path.exists():
        print("[INFO] hot.md が見つかりません")
        return
    print(hot_path.read_text(encoding="utf-8"))


def main() -> None:
    config = load_config()
    wiki_root = resolve_wiki_root(config)

    parser = argparse.ArgumentParser(
        description="Wiki を検索・閲覧するスクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = subparsers.add_parser("search", help="キーワードで全文検索する")
    p_search.add_argument("keyword", help="検索キーワード")

    # list-pages
    p_list = subparsers.add_parser("list-pages", help="ページ一覧を表示する")
    p_list.add_argument(
        "--category",
        choices=["atoms", "topics"],
        help="カテゴリを絞り込む",
    )

    # show
    p_show = subparsers.add_parser("show", help="ページ内容を表示する")
    p_show.add_argument("path", help="ページパス（wiki_root からの相対パスまたはstem）")

    # hot
    subparsers.add_parser("hot", help="hot.md を表示する")

    args = parser.parse_args()

    dispatch = {
        "search": cmd_search,
        "list-pages": cmd_list_pages,
        "show": cmd_show,
        "hot": cmd_hot,
    }
    dispatch[args.command](args, wiki_root)


if __name__ == "__main__":
    main()
