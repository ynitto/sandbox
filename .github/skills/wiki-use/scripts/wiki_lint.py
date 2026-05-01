#!/usr/bin/env python3
"""
wiki_lint.py — Wiki の整合性チェック

使い方:
  python scripts/wiki_lint.py              # 全チェックを実行する
  python scripts/wiki_lint.py --fix        # 修正可能な問題を自動修正する（孤立ページをindex.mdに追加）
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from wiki_utils import load_config, resolve_wiki_root

EMPTY_PAGE_THRESHOLD = 100  # 本文がこれ以下の文字数は「空ページ」とみなす


def collect_wiki_pages(wiki_root: Path) -> list:
    """wiki/ 以下の .md ページを収集する（meta/ を除く）。"""
    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.exists():
        return []
    pages = []
    for cat in ["atoms", "topics"]:
        cat_dir = wiki_dir / cat
        if cat_dir.exists():
            for p in sorted(cat_dir.glob("*.md")):
                if p.name != ".gitkeep":
                    pages.append((cat, p))
    return pages


def get_index_links(wiki_root: Path) -> set:
    """index.md に登録されているウィキリンクの stem セットを返す。"""
    index_path = wiki_root / "index.md"
    if not index_path.exists():
        return set()
    text = index_path.read_text(encoding="utf-8")
    return set(re.findall(r"\[\[([^\]]+)\]\]", text))


def get_page_wikilinks(page_path: Path) -> set:
    """ページ内の [[リンク]] を収集する。"""
    text = page_path.read_text(encoding="utf-8")
    return set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text))


def get_body_length(page_path: Path) -> int:
    """frontmatter を除いた本文の文字数を返す。"""
    text = page_path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:]
    return len(text.strip())


def main() -> None:
    config = load_config()
    wiki_root = resolve_wiki_root(config)

    parser = argparse.ArgumentParser(description="Wiki の整合性チェック")
    parser.add_argument("--fix", action="store_true", help="修正可能な問題を自動修正する")
    args = parser.parse_args()

    pages = collect_wiki_pages(wiki_root)
    index_links = get_index_links(wiki_root)

    # 全ページの stem セット（リンク解決用）
    all_stems = {p.stem for _, p in pages}

    warnings = []
    infos = []
    errors = []

    # ---- チェック 1: 孤立ページ（index.md に未登録） ----
    orphan_pages = []
    for cat, page_path in pages:
        stem = page_path.stem
        if stem not in index_links:
            warnings.append(f"[WARN] 孤立ページ: wiki/{cat}/{page_path.name} (index.mdに未登録)")
            orphan_pages.append((cat, page_path))

    # ---- チェック 2: リンク切れ ----
    for cat, page_path in pages:
        links = get_page_wikilinks(page_path)
        for link in links:
            if link not in all_stems:
                warnings.append(
                    f"[WARN] リンク切れ: wiki/{cat}/{page_path.name} → [[{link}]] ({link}.md が存在しない)"
                )

    # ---- チェック 3: 短小ページ ----
    for cat, page_path in pages:
        length = get_body_length(page_path)
        if length < EMPTY_PAGE_THRESHOLD:
            warnings.append(
                f"[WARN] 短小ページ: wiki/{cat}/{page_path.name} (本文 {length} 文字)"
            )

    # ---- 結果表示 ----
    all_messages = errors + warnings + infos
    if not all_messages:
        print("[OK] すべてのチェックをパスしました")
    else:
        for msg in all_messages:
            print(msg)
        print()
        print(f"エラー: {len(errors)}, 警告: {len(warnings)}, 情報: {len(infos)}")

    # ---- --fix: 孤立ページを index.md に追加 ----
    if args.fix and orphan_pages:
        print("\n--fix: 孤立ページを index.md に追加します...")
        index_path = wiki_root / "index.md"
        index_text = index_path.read_text(encoding="utf-8")
        today = date.today().isoformat()

        for cat, page_path in orphan_pages:
            stem = page_path.stem
            link = f"[[{stem}]]"
            new_line = f"- {link}\n"
            header_pat = re.compile(rf"^## {re.escape(cat)}\n", re.MULTILINE)
            m = header_pat.search(index_text)
            if m:
                section_start = m.end()
                next_h = re.search(r"^## ", index_text[section_start:], re.MULTILINE)
                insert_pos = section_start + next_h.start() if next_h else len(index_text)
                prefix = index_text[:insert_pos].rstrip("\n") + "\n"
                index_text = prefix + new_line + index_text[insert_pos:]
                print(f"  追加: {link} → {cat}")
            else:
                print(f"  [SKIP] セクション '{cat}' が見つかりません")

        index_text = re.sub(
            r"最終更新: \d{4}-\d{2}-\d{2}",
            f"最終更新: {today}",
            index_text,
        )
        index_path.write_text(index_text, encoding="utf-8")
        print(f"[OK] index.md を更新しました")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
