#!/usr/bin/env python3
"""
wiki_query.py — Wiki を検索・閲覧するスクリプト

使い方:
  python scripts/wiki_query.py search "<キーワード>"
      キーワードで Wiki ページを検索する（トークン化＋フィールド重み付け）
      - 日本語/英語/表記ゆれをまたいでヒットする（title・aliases を最重視）
      - 完全一致を優先し、部分一致は被覆率順に提示する

  python scripts/wiki_query.py list-pages [--category atoms|topics]
      ページ一覧を表示する

  python scripts/wiki_query.py show <ページパス>
      ページ内容を表示する

  python scripts/wiki_query.py hot
      hot.md（最近のコンテキスト）を表示する

  python scripts/wiki_query.py queries
      queries.md（価値あるクエリの記録）を表示する

  python scripts/wiki_query.py save-query --query "<クエリ文>" [--answer <ページパス>] [--keywords kw1 kw2]
      価値あるクエリを queries.md に保存する
"""

import argparse
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from wiki_utils import load_config, resolve_wiki_root


# --- トークン化・frontmatter 解析（builtin 検索エンジン） ---

# CJK（ひらがな・カタカナ・漢字・半角カナ）の連続を 1 ランとして扱う
_CJK_RUN_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]+")
# ASCII 英数字の連続を 1 トークンとして扱う
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")

# 検索フィールドの重み（title/aliases を最重視し、本文を最小にする）
_FIELD_WEIGHTS = {"title": 5, "aliases": 5, "tags": 3, "summary": 2, "body": 1}

# 部分一致として採用する最小被覆率（これ未満は bigram の偶発一致とみなして捨てる）
PARTIAL_COVERAGE_MIN = 0.5


def tokenize(text: str) -> list:
    """テキストをトークン列に分解する。

    - ASCII 英数字: 連続を 1 トークン（小文字化）
    - CJK: 文字 2-gram（1 文字のランは 1-gram）。日本語の分かち書き無し環境向け。
    依存ライブラリを増やさず、表記ゆれにある程度耐えるための軽量実装。
    """
    text = unicodedata.normalize("NFKC", text).lower()
    tokens = []
    tokens.extend(_ASCII_TOKEN_RE.findall(text))
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _strip_scalar(val: str) -> str:
    """前後のクォートを除去する。"""
    val = val.strip()
    if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
        return val[1:-1]
    return val


def parse_frontmatter(text: str) -> tuple:
    """YAML frontmatter を簡易解析して (dict, body) を返す。

    PyYAML に依存せず、wiki-use の規約で使うフィールド（scalar / インラインリスト /
    ブロックリスト）だけを解釈する軽量パーサ。
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end]
    body = text[end + 4:]

    data = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        if val == "":
            # ブロックリスト（  - item）を収集する
            items = []
            j = i + 1
            while j < len(lines) and re.match(r"^\s+-\s+", lines[j]):
                items.append(_strip_scalar(re.sub(r"^\s+-\s+", "", lines[j])))
                j += 1
            data[key] = items if items else ""
            i = j if items else i + 1
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            data[key] = [_strip_scalar(x) for x in inner.split(",") if x.strip()] if inner else []
        else:
            data[key] = _strip_scalar(val)
        i += 1
    return data, body


def _as_list(val) -> list:
    if isinstance(val, list):
        return val
    if val:
        return [val]
    return []


def get_page_fields(page_path: Path) -> dict:
    """ページから検索用フィールド（title/aliases/tags/summary/body）を抽出する。"""
    text = page_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    title = fm.get("title") or ""
    if not title:
        m = re.search(r"^#\s+(.+)", body, re.MULTILINE)
        title = m.group(1).strip() if m else page_path.stem

    return {
        "title": title,
        "aliases": _as_list(fm.get("aliases")),
        "tags": _as_list(fm.get("tags")),
        "summary": fm.get("summary") or "",
        "body": body,
        "raw": text,
    }


def get_page_title(page_path: Path) -> str:
    """ページの frontmatter から title を返す。なければ stem を返す。"""
    if not page_path.exists():
        return page_path.stem
    return get_page_fields(page_path)["title"]


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


def _matched_lines(page_path: Path, query_tokens: set, max_lines: int = 3) -> list:
    """クエリトークンを最も多く含む本文行を抽出する。"""
    scored = []
    for line in page_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("---"):
            continue
        line_tokens = set(tokenize(s))
        overlap = len(query_tokens & line_tokens)
        if overlap:
            scored.append((overlap, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:max_lines]]


def score_page(fields: dict, query_tokens: set) -> tuple:
    """ページのフィールド別重み付きスコアと、ヒットしたクエリトークン集合を返す。"""
    score = 0
    hit_tokens = set()
    for field, weight in _FIELD_WEIGHTS.items():
        value = fields[field]
        if isinstance(value, list):
            value = " ".join(value)
        field_tokens = set(tokenize(value))
        matched = query_tokens & field_tokens
        if matched:
            score += weight * len(matched)
            hit_tokens |= matched
    return score, hit_tokens


def cmd_search(args, wiki_root: Path) -> None:
    """キーワードで Wiki ページを検索する（トークン化＋フィールド重み付け）。"""
    query_tokens = set(tokenize(args.keyword))
    pages = collect_wiki_pages(wiki_root)

    if not query_tokens:
        print(f"[INFO] 検索可能なトークンがありません: '{args.keyword}'")
        return

    scored = []
    for cat, page_path in pages:
        fields = get_page_fields(page_path)
        score, hit_tokens = score_page(fields, query_tokens)
        if score > 0:
            coverage = len(hit_tokens) / len(query_tokens)
            scored.append((score, coverage, cat, page_path, hit_tokens))

    # 全クエリトークンを含むものを優先し、次にスコア順
    scored.sort(key=lambda x: (x[1] >= 1.0, x[0], x[1]), reverse=True)

    # 完全一致（全トークンを被覆）と部分一致を分ける。
    # 部分一致は被覆率がしきい値以上のものだけ採用し、bigram の偶発一致ノイズを落とす。
    full = [r for r in scored if r[1] >= 1.0]
    partial = [r for r in scored if PARTIAL_COVERAGE_MIN <= r[1] < 1.0]
    primary = full if full else partial

    if not primary:
        # 近傍提示: 何もヒットしなければ全ページ一覧へ誘導する
        print(f"[INFO] '{args.keyword}' にマッチするページはありません")
        print("       list-pages で全体を確認してください:")
        print("       python scripts/wiki_query.py list-pages")
        return

    label = "完全一致" if full else "部分一致（全キーワードは揃っていません）"
    print(f"検索結果: '{args.keyword}' — {len(primary)} 件（{label}）")
    print()
    for score, coverage, cat, page_path, hit_tokens in primary:
        title = get_page_title(page_path)
        rel = page_path.relative_to(wiki_root)
        print(f"  [{cat}] {title}  (score={score}, 一致={int(coverage * 100)}%)")
        print(f"    パス: {rel}")
        for line in _matched_lines(page_path, query_tokens):
            print(f"    …{line}…")
        print()

    # 完全一致を出したが部分一致も残っている場合、近傍候補として件数を示す
    if full and partial:
        print(f"  （ほか部分一致 {len(partial)} 件。list-pages で全体を確認できます）")


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


QUERIES_MAX = 50


def cmd_queries(args, wiki_root: Path) -> None:
    """queries.md（価値あるクエリの記録）を表示する。"""
    queries_path = wiki_root / "wiki" / "meta" / "queries.md"
    if not queries_path.exists():
        print("[INFO] queries.md が見つかりません")
        return
    print(queries_path.read_text(encoding="utf-8"))


def cmd_save_query(args, wiki_root: Path) -> None:
    """価値あるクエリを queries.md に保存する。"""
    queries_path = wiki_root / "wiki" / "meta" / "queries.md"
    if not queries_path.exists():
        print(f"[ERROR] queries.md が見つかりません: {queries_path}", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    query_text = args.query
    answer = getattr(args, "answer", None) or ""
    keywords = getattr(args, "keywords", None) or []

    if answer:
        stem = Path(answer).stem
        entry = f"- **{query_text}** → [[{stem}]] ({today})"
    else:
        entry = f"- **{query_text}** ({today})"

    if keywords:
        tags = " ".join(f"#{kw}" for kw in keywords)
        entry += f" {tags}"

    entry += "\n"

    existing = queries_path.read_text(encoding="utf-8")

    # 重複チェック
    if f"**{query_text}**" in existing:
        print(f"[INFO] 同じクエリが既に存在します（スキップ）: {query_text[:60]}")
        return

    # コメント行の直後に挿入
    comment_end = existing.find("-->")
    if comment_end != -1:
        insert_pos = existing.find("\n", comment_end) + 1
    else:
        insert_pos = len(existing)

    new_text = existing[:insert_pos] + "\n" + entry + existing[insert_pos:]

    # 最大 QUERIES_MAX 件に制限
    entry_pattern = re.compile(r"^- \*\*.+$", re.MULTILINE)
    all_entries = entry_pattern.findall(new_text)
    if len(all_entries) > QUERIES_MAX:
        # 古いエントリを末尾から削除
        excess = len(all_entries) - QUERIES_MAX
        for old_entry in all_entries[-excess:]:
            new_text = new_text.replace(old_entry + "\n", "", 1)

    # 最終更新日を更新
    new_text = re.sub(
        r"最終更新: \d{4}-\d{2}-\d{2}",
        f"最終更新: {today}",
        new_text,
    )

    queries_path.write_text(new_text, encoding="utf-8")
    print(f"[OK] クエリを保存しました: {query_text[:60]}")

    # log.md にも記録する（Karpathy: log.md は ingests・queries・lint passes の記録）
    log_path = wiki_root / "log.md"
    if log_path.exists():
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        log_entry = f"\n## {now} — query\n\n- クエリ: \"{query_text}\"\n"
        if answer:
            log_entry += f"- 保存先: {answer}\n"
        if keywords:
            log_entry += f"- キーワード: {', '.join(keywords)}\n"
        log_entry += "\n---\n"

        log_text = log_path.read_text(encoding="utf-8")
        header_end = log_text.find("\n", log_text.find("# Wiki 操作ログ")) + 1
        log_path.write_text(log_text[:header_end] + log_entry + log_text[header_end:], encoding="utf-8")
        print(f"[OK] log.md に記録しました: {now}")


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

    # queries
    subparsers.add_parser("queries", help="queries.md（価値あるクエリの記録）を表示する")

    # save-query
    p_save_query = subparsers.add_parser("save-query", help="価値あるクエリを queries.md に保存する")
    p_save_query.add_argument("--query", required=True, help="クエリ文（質問・検索テキスト）")
    p_save_query.add_argument(
        "--answer",
        default="",
        help="回答ページのパス（wiki_root からの相対パスまたは stem）",
    )
    p_save_query.add_argument(
        "--keywords",
        nargs="+",
        default=[],
        help="タグ用キーワード（スペース区切り）",
    )

    args = parser.parse_args()

    dispatch = {
        "search": cmd_search,
        "list-pages": cmd_list_pages,
        "show": cmd_show,
        "hot": cmd_hot,
        "queries": cmd_queries,
        "save-query": cmd_save_query,
    }
    dispatch[args.command](args, wiki_root)


if __name__ == "__main__":
    main()
