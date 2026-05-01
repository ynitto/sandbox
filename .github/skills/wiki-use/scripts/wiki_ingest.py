#!/usr/bin/env python3
"""
wiki_ingest.py — ソース取り込み支援スクリプト

使い方:
  python scripts/wiki_ingest.py copy --source <ファイルパス|フォルダパス> [--published YYYY-MM-DD]
      ソースを sources/ にコピーする。Markdown の場合はフロントマターの published を自動検出する。
      フォルダを指定した場合、テキストとして解析可能なファイルを再帰的に全て列挙してコピーする。

  python scripts/wiki_ingest.py update-index --pages <page1.md> [<page2.md> ...]
      index.md に新規ページを登録する

  python scripts/wiki_ingest.py log \\
      --source <ソースパス> --pages-created <N> --pages-updated <N> [--published YYYY-MM-DD]
      log.md に操作を記録する

  python scripts/wiki_ingest.py update-hot --pages <page1.md> [<page2.md> ...]
      hot.md を更新する（直近20件を維持）
"""

import argparse
import hashlib
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from wiki_utils import load_config, resolve_wiki_root

HOT_MAX = 20

# copy 対象とするテキスト解析可能な拡張子
INGESTABLE_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".html", ".htm", ".pdf", ".docx"}


def slugify(name: str) -> str:
    """ファイル名を slug 化する。"""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "source"


def is_ingested(sources_dir: Path, log_path: Path) -> set:
    """log.md に記録済みのソースファイル名セットを返す。"""
    if not log_path.exists():
        return set()
    text = log_path.read_text(encoding="utf-8")
    pattern = re.compile(r"sources/([^\s`]+)")
    return {m.group(1) for m in pattern.finditer(text)}


def _file_hash(path: Path) -> str:
    """ファイル内容の SHA-256 ハッシュを返す。"""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _find_duplicate(source_path: Path, sources_dir: Path) -> Path | None:
    """同じ内容のファイルが sources/ に存在すれば、そのパスを返す。"""
    if not sources_dir.exists():
        return None
    source_hash = _file_hash(source_path)
    for existing in sorted(sources_dir.iterdir()):
        if existing.is_file() and _file_hash(existing) == source_hash:
            return existing
    return None


def _extract_published_from_frontmatter(path: Path) -> str | None:
    """Markdown ファイルのフロントマターから published 日付を取得する。"""
    if path.suffix.lower() not in (".md", ".markdown"):
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    frontmatter = text[3:end]
    m = re.search(r"^published:\s*(\S+)", frontmatter, re.MULTILINE)
    return m.group(1).strip() if m else None


def _build_unique_dest_path(sources_dir: Path, today: str, slug: str, suffix: str) -> Path:
    """同名ファイルがある場合は連番サフィックスで重複を回避する。"""
    candidate = sources_dir / f"{today}-{slug}{suffix}"
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        candidate = sources_dir / f"{today}-{slug}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _copy_single_file(source_path: Path, wiki_root: Path, published_override: str) -> bool:
    """1 ファイルを sources/ にコピーし、発行日を出力する。重複の場合は False を返す。"""
    sources_dir = wiki_root / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    # 重複チェック（内容ハッシュで比較）
    duplicate = _find_duplicate(source_path, sources_dir)
    if duplicate is not None:
        print(f"[SKIP] 同一内容が既に存在します: {duplicate} ← {source_path}")
        return False

    today = date.today().isoformat()
    slug = slugify(source_path.stem)
    dest_path = _build_unique_dest_path(sources_dir, today, slug, source_path.suffix)

    shutil.copy2(source_path, dest_path)
    print(f"[OK] コピーしました: {dest_path}")

    published = published_override or _extract_published_from_frontmatter(source_path)
    published_str = published if published else "(unknown)"
    print(f"published: {published_str}")
    print(f"source_path: {dest_path}")
    return True


def cmd_copy(args, wiki_root: Path, _config: dict) -> None:
    """ソース（ファイルまたはフォルダ）を sources/ にコピーする。"""
    source_path = Path(args.source).expanduser()
    if not source_path.exists():
        print(f"[ERROR] パスが見つかりません: {source_path}", file=sys.stderr)
        sys.exit(1)

    published_override = getattr(args, "published", "") or ""
    batch_size = getattr(args, "batch_size", None) or 0

    if source_path.is_dir():
        files = [
            f for f in sorted(source_path.rglob("*"))
            if f.is_file() and f.suffix.lower() in INGESTABLE_EXTENSIONS
        ]
        if not files:
            print(f"[WARN] 解析可能なファイルが見つかりません: {source_path}")
            return

        # 重複を除外した実コピー対象を先に確認（情報表示用）
        total = len(files)
        if batch_size > 0:
            num_batches = (total + batch_size - 1) // batch_size
            print(f"[INFO] {total} 件のファイルを検出しました（{batch_size} 件ずつ {num_batches} バッチ）")
            for batch_idx in range(num_batches):
                batch_files = files[batch_idx * batch_size:(batch_idx + 1) * batch_size]
                print(f"\n[BATCH {batch_idx + 1}/{num_batches}]")
                for f in batch_files:
                    print(f"--- {f} ---")
                    _copy_single_file(f, wiki_root, published_override)
        else:
            print(f"[INFO] {total} 件のファイルを検出しました")
            for f in files:
                print(f"--- {f} ---")
                _copy_single_file(f, wiki_root, published_override)
    else:
        _copy_single_file(source_path, wiki_root, published_override)


def cmd_list_batches(args, wiki_root: Path, _config: dict) -> None:
    """フォルダ内の取り込み可能ファイルをバッチに分けて一覧表示する。"""
    source_path = Path(args.source).expanduser()
    if not source_path.exists():
        print(f"[ERROR] パスが見つかりません: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not source_path.is_dir():
        print(f"[ERROR] フォルダを指定してください: {source_path}", file=sys.stderr)
        sys.exit(1)

    batch_size = args.batch_size if args.batch_size > 0 else 5

    files = [
        f for f in sorted(source_path.rglob("*"))
        if f.is_file() and f.suffix.lower() in INGESTABLE_EXTENSIONS
    ]
    if not files:
        print(f"[WARN] 解析可能なファイルが見つかりません: {source_path}")
        return

    total = len(files)
    num_batches = (total + batch_size - 1) // batch_size
    print(f"TOTAL: {total} files, {num_batches} batches (batch-size: {batch_size})")
    print()
    for batch_idx in range(num_batches):
        batch_files = files[batch_idx * batch_size:(batch_idx + 1) * batch_size]
        print(f"=== BATCH {batch_idx + 1}/{num_batches} ===")
        for f in batch_files:
            print(str(f))
        print()


def cmd_update_index(args, wiki_root: Path, _config: dict) -> None:
    """index.md に新規ページを追加する。"""
    index_path = wiki_root / "index.md"
    if not index_path.exists():
        print(f"[ERROR] index.md が見つかりません: {index_path}", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    index_text = index_path.read_text(encoding="utf-8")

    for page_path_str in args.pages:
        page_path = Path(page_path_str)
        # カテゴリを path から判定
        parts = page_path.parts
        category = None
        for part in parts:
            if part in ("concepts", "entities", "topics"):
                category = part
                break

        if category is None:
            print(f"[WARN] カテゴリ不明（スキップ）: {page_path}")
            continue

        stem = page_path.stem
        link = f"[[{stem}]]"

        # 既に登録済みかチェック
        if link in index_text:
            print(f"  スキップ（既登録）: {link}")
            continue

        # カテゴリセクションの末尾テーブル行の後に追記
        # パターン: "## <category>" 以降の最後のテーブル行の後
        section_pattern = re.compile(
            rf"(## {category}\n\s*\|[^\n]+\n\|[-| ]+\n)((?:\|[^\n]+\n)*)",
            re.MULTILINE,
        )
        m = section_pattern.search(index_text)
        if m:
            # 概要を frontmatter から読む
            summary = _read_page_summary(wiki_root / page_path_str)
            new_row = f"| {link} | {summary} | {today} |\n"
            replacement = m.group(1) + m.group(2) + new_row
            index_text = index_text[: m.start()] + replacement + index_text[m.end():]
            print(f"  追加: {link} → {category}")
        else:
            print(f"[WARN] セクション '{category}' のテーブルが見つかりません: {page_path}")

    # 最終更新日を更新
    index_text = re.sub(
        r"最終更新: \d{4}-\d{2}-\d{2}",
        f"最終更新: {today}",
        index_text,
    )
    index_path.write_text(index_text, encoding="utf-8")
    print(f"[OK] index.md を更新しました: {index_path}")


def _read_page_summary(page_path: Path) -> str:
    """ページの frontmatter から title を読むか、最初の行を返す。"""
    if not page_path.exists():
        return ""
    text = page_path.read_text(encoding="utf-8")
    # frontmatter の title を探す
    m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # 最初の # 見出し
    m = re.search(r'^#\s+(.+)', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


def cmd_log(args, wiki_root: Path, _config: dict) -> None:
    """log.md に操作を記録する。"""
    log_path = wiki_root / "log.md"
    if not log_path.exists():
        print(f"[ERROR] log.md が見つかりません: {log_path}", file=sys.stderr)
        sys.exit(1)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    source_rel = args.source
    created = args.pages_created
    updated = args.pages_updated
    notes = args.notes or ""
    published = getattr(args, "published", None) or ""

    entry = (
        f"\n## {now} — ingest\n\n"
        f"- ソース: `{source_rel}`\n"
    )
    if published:
        entry += f"- 発行日: {published}\n"
    entry += (
        f"- 作成: {created}ページ\n"
        f"- 更新: {updated}ページ\n"
    )
    if notes:
        entry += f"- メモ: {notes}\n"
    entry += "\n---\n"

    # ヘッダーの直後（最初の ## の前）に挿入
    existing = log_path.read_text(encoding="utf-8")
    # "# Wiki 操作ログ\n" の直後に挿入
    header_end = existing.find("\n", existing.find("# Wiki 操作ログ")) + 1
    new_text = existing[:header_end] + entry + existing[header_end:]
    log_path.write_text(new_text, encoding="utf-8")
    print(f"[OK] log.md に記録しました: {now}")


def cmd_update_hot(args, wiki_root: Path, _config: dict) -> None:
    """hot.md を更新する（直近 HOT_MAX 件を維持）。"""
    hot_path = wiki_root / "wiki" / "meta" / "hot.md"
    if not hot_path.exists():
        print(f"[ERROR] hot.md が見つかりません: {hot_path}", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    existing = hot_path.read_text(encoding="utf-8")

    # 既存エントリを抽出
    entry_pattern = re.compile(r"^- \[\[(.+?)\]\] — (.+)$", re.MULTILINE)
    existing_entries = entry_pattern.findall(existing)

    # 新規エントリを先頭に追加
    new_stems = []
    for page_path_str in args.pages:
        stem = Path(page_path_str).stem
        action = "更新" if page_path_str in getattr(args, "updated_pages", []) else "作成"
        new_stems.append((stem, f"{today} {action}"))

    # 重複を除いてマージ（新しいものが先頭）
    seen = {stem for stem, _ in new_stems}
    merged = list(new_stems)
    for stem, ts in existing_entries:
        if stem not in seen:
            seen.add(stem)
            merged.append((stem, ts))

    # 最大 HOT_MAX 件に制限
    merged = merged[:HOT_MAX]

    # hot.md を再生成
    lines = [
        "# Hot Pages（最近のコンテキスト）",
        "",
        f"最終更新: {today}",
        "",
        f"<!-- 新しい取り込みで更新される。最大{HOT_MAX}件 -->",
        "",
    ]
    for stem, ts in merged:
        lines.append(f"- [[{stem}]] — {ts}")

    hot_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[OK] hot.md を更新しました（{len(merged)} 件）")


def main() -> None:
    config = load_config()
    wiki_root = resolve_wiki_root(config)

    parser = argparse.ArgumentParser(
        description="ソース取り込み支援スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list-batches
    p_list = subparsers.add_parser("list-batches", help="フォルダ内ファイルをバッチに分けて一覧表示する")
    p_list.add_argument("--source", required=True, help="フォルダパス")
    p_list.add_argument("--batch-size", type=int, default=5, metavar="N",
                        help="1バッチあたりのファイル数（デフォルト: 5）")

    # copy
    p_copy = subparsers.add_parser("copy", help="ソースを sources/ にコピーする")
    p_copy.add_argument("--source", required=True, help="ソースファイルのパス")
    p_copy.add_argument("--published", default="", help="情報の発行日 (YYYY-MM-DD)。未指定時は Markdown フロントマターから自動検出")
    p_copy.add_argument("--batch-size", type=int, default=0, metavar="N",
                        help="フォルダ指定時に N 件ずつバッチ区切りを出力する（0=区切りなし）")

    # update-index
    p_idx = subparsers.add_parser("update-index", help="index.md に新規ページを登録する")
    p_idx.add_argument("--pages", nargs="+", required=True, help="登録するページのパス（wiki_root からの相対パス）")

    # log
    p_log = subparsers.add_parser("log", help="log.md に操作を記録する")
    p_log.add_argument("--source", required=True, help="ソースパス（sources/ からの相対表記推奨）")
    p_log.add_argument("--pages-created", type=int, default=0, help="作成したページ数")
    p_log.add_argument("--pages-updated", type=int, default=0, help="更新したページ数")
    p_log.add_argument("--published", default="", help="情報の発行日 (YYYY-MM-DD)")
    p_log.add_argument("--notes", default="", help="メモ（任意）")

    # update-hot
    p_hot = subparsers.add_parser("update-hot", help="hot.md を更新する")
    p_hot.add_argument("--pages", nargs="+", required=True, help="作成・更新したページのパス")

    args = parser.parse_args()

    dispatch = {
        "list-batches": cmd_list_batches,
        "copy": cmd_copy,
        "update-index": cmd_update_index,
        "log": cmd_log,
        "update-hot": cmd_update_hot,
    }
    dispatch[args.command](args, wiki_root, config)


if __name__ == "__main__":
    main()
