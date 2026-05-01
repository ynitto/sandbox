#!/usr/bin/env python3
"""
wiki_ingest.py — ソース取り込み支援スクリプト

使い方:
  python scripts/wiki_ingest.py update-index --pages <page1.md> [<page2.md> ...]
      index.md に新規ページを登録する

  python scripts/wiki_ingest.py log \\
      --source <ソースパス> --pages-created <N> --pages-updated <N> [--published YYYY-MM-DD]
      log.md に操作を記録する

  python scripts/wiki_ingest.py update-hot --pages <page1.md> [<page2.md> ...]
      hot.md を更新する（直近20件を維持）

  python scripts/wiki_ingest.py init-batches --source <フォルダパス> [--batch-size 5]
      フォルダ内ファイルのバッチ処理状態を初期化する

  python scripts/wiki_ingest.py next-batch
      次の未処理バッチのファイルリストを出力する

  python scripts/wiki_ingest.py complete-batch --pages-created <N> --pages-updated <M>
      処理中バッチを完了としてマークする

  python scripts/wiki_ingest.py verify-completion --source <フォルダパス>
      全バッチの完了状況を確認する
"""

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from wiki_utils import load_config, resolve_wiki_root

HOT_MAX = 20
BATCH_STATE_FILE = ".wiki-batch-state.json"

INGESTABLE_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".html", ".htm", ".pdf", ".docx"}


def _batch_state_path(wiki_root: Path) -> Path:
    return wiki_root / BATCH_STATE_FILE


def _load_batch_state(wiki_root: Path) -> dict:
    path = _batch_state_path(wiki_root)
    if not path.exists():
        print("[ERROR] バッチ状態ファイルが見つかりません。先に init-batches を実行してください。", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def _save_batch_state(wiki_root: Path, state: dict) -> None:
    _batch_state_path(wiki_root).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cmd_init_batches(args, wiki_root: Path, _config: dict) -> None:
    """バッチ処理状態ファイルを生成する。AIには next-batch で1バッチずつ提供される。"""
    source_path = Path(args.source).expanduser()
    if not source_path.exists():
        print(f"[ERROR] パスが見つかりません: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not source_path.is_dir():
        print(f"[ERROR] フォルダを指定してください: {source_path}", file=sys.stderr)
        sys.exit(1)

    batch_size = args.batch_size if args.batch_size > 0 else 5
    files = [
        str(f) for f in sorted(source_path.rglob("*"))
        if f.is_file() and f.suffix.lower() in INGESTABLE_EXTENSIONS
    ]
    if not files:
        print(f"[WARN] 解析可能なファイルが見つかりません: {source_path}")
        return

    total = len(files)
    num_batches = (total + batch_size - 1) // batch_size
    batches = [
        {
            "index": i + 1,
            "files": files[i * batch_size:(i + 1) * batch_size],
            "status": "pending",
            "pages_created": 0,
            "pages_updated": 0,
        }
        for i in range(num_batches)
    ]
    state = {
        "source": str(source_path),
        "batch_size": batch_size,
        "total_files": total,
        "total_batches": num_batches,
        "batches": batches,
        "created_at": datetime.now().isoformat(),
    }
    _save_batch_state(wiki_root, state)

    print(f"[OK] バッチ状態を初期化しました: {_batch_state_path(wiki_root)}")
    print(f"     合計 {total} ファイル・{num_batches} バッチ（バッチサイズ: {batch_size}）")
    print(f"\nnext-batch を実行して最初のバッチを取得してください。")


def cmd_next_batch(args, wiki_root: Path, _config: dict) -> None:
    """次の未処理バッチのファイルリストだけを出力する。先読み・全件表示は行わない。"""
    state = _load_batch_state(wiki_root)

    # 処理中バッチがあれば再表示（中断リカバリ）
    in_progress = [b for b in state["batches"] if b["status"] == "in_progress"]
    if in_progress:
        batch = in_progress[0]
        remaining = sum(1 for b in state["batches"] if b["status"] in ("in_progress", "pending"))
        print(f"[INFO] バッチ {batch['index']}/{state['total_batches']} は処理中です（complete-batch 未実行）。")
        print()
        _print_batch(batch, state["total_batches"], remaining)
        return

    # 次の pending バッチ
    pending = [b for b in state["batches"] if b["status"] == "pending"]
    if not pending:
        done = sum(1 for b in state["batches"] if b["status"] == "done")
        print(f"[OK] 全バッチ完了済み（{done}/{state['total_batches']}）")
        print(f"verify-completion --source {state['source']} で最終確認してください。")
        return

    batch = pending[0]
    batch["status"] = "in_progress"
    _save_batch_state(wiki_root, state)

    remaining = len(pending)  # includes this batch
    _print_batch(batch, state["total_batches"], remaining)


def _print_batch(batch: dict, total_batches: int, remaining: int) -> None:
    done = total_batches - remaining
    print(f"=== BATCH {batch['index']}/{total_batches} ===")
    print(f"完了済み: {done} バッチ / 残り: {remaining} バッチ（このバッチを含む）")
    print()
    for f in batch["files"]:
        print(f)
    print()
    print(f"処理完了後: python scripts/wiki_ingest.py complete-batch --pages-created <N> --pages-updated <M>")


def cmd_complete_batch(args, wiki_root: Path, _config: dict) -> None:
    """処理中バッチを完了としてマークし、残バッチ数を表示する。"""
    state = _load_batch_state(wiki_root)

    in_progress = [b for b in state["batches"] if b["status"] == "in_progress"]
    if not in_progress:
        print("[ERROR] 処理中のバッチがありません。next-batch を先に実行してください。", file=sys.stderr)
        sys.exit(1)

    batch = in_progress[0]
    pages_created = args.pages_created
    pages_updated = args.pages_updated

    if pages_created + pages_updated == 0:
        print(f"[WARN] pages-created と pages-updated がどちらも 0 です。ソースの精読・ページ生成が実施されましたか？")

    batch["status"] = "done"
    batch["pages_created"] = pages_created
    batch["pages_updated"] = pages_updated
    _save_batch_state(wiki_root, state)

    remaining = sum(1 for b in state["batches"] if b["status"] == "pending")
    print(f"[OK] バッチ {batch['index']}/{state['total_batches']} 完了（作成: {pages_created} ページ・更新: {pages_updated} ページ）")

    if remaining > 0:
        print(f"     残り {remaining} バッチ。")
        print(f"\nnext-batch を実行して次のバッチを取得してください。")
    else:
        print(f"     全 {state['total_batches']} バッチ完了！")
        print(f"\npython scripts/wiki_ingest.py verify-completion --source {state['source']} で最終確認してください。")


def cmd_verify_completion(args, wiki_root: Path, _config: dict) -> None:
    """バッチ状態ファイルを参照し、全バッチの完了状況を報告する。"""
    state = _load_batch_state(wiki_root)

    total = state["total_batches"]
    done_batches = [b for b in state["batches"] if b["status"] == "done"]
    incomplete = [b for b in state["batches"] if b["status"] != "done"]

    pages_created = sum(b["pages_created"] for b in done_batches)
    pages_updated = sum(b["pages_updated"] for b in done_batches)

    print(f"VERIFY: {len(done_batches)}/{total} バッチ完了（作成: {pages_created} ページ・更新: {pages_updated} ページ）")

    if incomplete:
        print()
        print(f"[WARN] 未完了: {len(incomplete)} バッチ")
        for b in incomplete:
            print(f"  バッチ {b['index']}: {b['status']}")
        sys.exit(2)
    else:
        print("[OK] 全ファイルの取り込みが完了しています")


def _read_page_meta(page_path: Path) -> dict:
    """ページのフロントマターから summary・type・source_count を返す。"""
    result = {"summary": "", "type": "", "source_count": 0}
    if not page_path.exists():
        return result
    text = page_path.read_text(encoding="utf-8")
    fm = ""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            fm = text[3:end]
    if fm:
        m = re.search(r'^summary:\s*["\']?(.+?)["\']?\s*$', fm, re.MULTILINE)
        if m:
            result["summary"] = m.group(1).strip()
        m = re.search(r'^type:\s*(\S+)', fm, re.MULTILINE)
        if m:
            result["type"] = m.group(1).strip()
        result["source_count"] = len(re.findall(r'^\s+- ', fm, re.MULTILINE))
    if not result["summary"]:
        body = text[text.find("---", 3) + 3:] if fm else text
        lines = [l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")]
        if lines:
            snippet = lines[0]
            result["summary"] = snippet[:80] + "…" if len(snippet) > 80 else snippet
    return result


def _insert_into_section(index_text: str, section_name: str, new_line: str) -> str | None:
    """セクション末尾に1行追加した新テキストを返す。セクションが見つからなければ None。"""
    header_pat = re.compile(rf"^## {re.escape(section_name)}\n", re.MULTILINE)
    m = header_pat.search(index_text)
    if not m:
        return None
    section_start = m.end()
    next_header = re.search(r"^## ", index_text[section_start:], re.MULTILINE)
    insert_pos = section_start + next_header.start() if next_header else len(index_text)
    prefix = index_text[:insert_pos].rstrip("\n") + "\n"
    suffix = index_text[insert_pos:]
    return prefix + new_line + suffix


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
        category = None
        for part in page_path.parts:
            if part in ("atoms", "topics"):
                category = part
                break

        if category is None:
            print(f"[WARN] カテゴリ不明（スキップ）: {page_path}")
            continue

        stem = page_path.stem
        link = f"[[{stem}]]"

        if link in index_text:
            print(f"  スキップ（既登録）: {link}")
            continue

        meta = _read_page_meta(wiki_root / page_path_str)
        summary = meta["summary"]
        type_str = meta["type"]
        n = meta["source_count"]
        src_label = f"{n} source{'s' if n != 1 else ''}"
        annotation = f"（{type_str}, {src_label}）" if type_str else f"（{src_label}）"
        new_line = f"- {link} — {summary}{annotation}\n" if summary else f"- {link}{annotation}\n"

        updated = _insert_into_section(index_text, category, new_line)
        if updated is not None:
            index_text = updated
            print(f"  追加: {link} → {category}")
        else:
            print(f"[WARN] セクション '{category}' が見つかりません: {page_path}")

    index_text = re.sub(
        r"最終更新: \d{4}-\d{2}-\d{2}",
        f"最終更新: {today}",
        index_text,
    )
    index_path.write_text(index_text, encoding="utf-8")
    print(f"[OK] index.md を更新しました: {index_path}")


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

    # update-index
    p_idx = subparsers.add_parser("update-index", help="index.md に新規ページを登録する")
    p_idx.add_argument("--pages", nargs="+", required=True, help="登録するページのパス（wiki_root からの相対パス）")

    # log
    p_log = subparsers.add_parser("log", help="log.md に操作を記録する")
    p_log.add_argument("--source", required=True, help="ソースパス（元ファイルのパス）")
    p_log.add_argument("--pages-created", type=int, default=0, help="作成したページ数")
    p_log.add_argument("--pages-updated", type=int, default=0, help="更新したページ数")
    p_log.add_argument("--published", default="", help="情報の発行日 (YYYY-MM-DD)")
    p_log.add_argument("--notes", default="", help="メモ（任意）")

    # update-hot
    p_hot = subparsers.add_parser("update-hot", help="hot.md を更新する")
    p_hot.add_argument("--pages", nargs="+", required=True, help="作成・更新したページのパス")

    # verify-completion
    p_verify = subparsers.add_parser(
        "verify-completion",
        help="ソースフォルダと log.md を突合し、未処理ファイルを報告する（終了コード 2 = 未完了あり）",
    )
    p_verify.add_argument("--source", required=True, help="フォルダパス")

    # init-batches
    p_init_b = subparsers.add_parser(
        "init-batches",
        help="バッチ処理状態ファイルを生成する。AIには next-batch で1バッチずつ提供される",
    )
    p_init_b.add_argument("--source", required=True, help="フォルダパス")
    p_init_b.add_argument("--batch-size", type=int, default=5, metavar="N",
                          help="1バッチあたりのファイル数（デフォルト: 5）")

    # next-batch
    subparsers.add_parser(
        "next-batch",
        help="次の未処理バッチのファイルリストだけを出力する",
    )

    # complete-batch
    p_cb = subparsers.add_parser(
        "complete-batch",
        help="処理中バッチを完了としてマークし、残バッチ数を表示する",
    )
    p_cb.add_argument("--pages-created", type=int, default=0, required=True, help="作成したページ数")
    p_cb.add_argument("--pages-updated", type=int, default=0, required=True, help="更新したページ数")

    args = parser.parse_args()

    dispatch = {
        "update-index": cmd_update_index,
        "log": cmd_log,
        "update-hot": cmd_update_hot,
        "verify-completion": cmd_verify_completion,
        "init-batches": cmd_init_batches,
        "next-batch": cmd_next_batch,
        "complete-batch": cmd_complete_batch,
    }
    dispatch[args.command](args, wiki_root, config)


if __name__ == "__main__":
    main()
