#!/usr/bin/env python3
"""
recall_memory.py - キーワードで記憶を検索するスクリプト

インデックスを使った高速検索（大量記憶でも O(index) で動作）。
recall すると access_count が加算され share_score が自動更新される。
ワークスペース記憶で見つからない場合、home/shared を自動フォールバック検索する。

Usage:
  python recall_memory.py "JWT 認証"
  python recall_memory.py "バグ" --category bug-investigation
  python recall_memory.py "API" --scope all --limit 5
  python recall_memory.py "デプロイ" --full        # 全文表示
  python recall_memory.py "設計" --no-track        # access_count 更新しない
  python recall_memory.py "JWT" --rate-after       # 結果表示後に評価入力
"""

import argparse
import os
import sys

import memory_utils


# ─── インデックス検索 ────────────────────────────────────────

def _score_index_entry(entry: dict, keywords: list[str]) -> int:
    """インデックスエントリのスコアを計算する（title/summary/tags のみ、高速）"""
    title = entry.get("title", "").lower()
    summary = entry.get("summary", "").lower()
    tags = " ".join(entry.get("tags", [])).lower()
    score = 0
    for kw in keywords:
        kw = kw.lower()
        if kw in title:    score += 10
        if kw in summary:  score += 6
        if kw in tags:     score += 4
    return score


def search_with_index(memory_dir: str, keywords: list[str],
                      status_filter: str | None, limit: int,
                      category: str | None = None) -> list[dict]:
    """インデックス優先の2段階検索:
    1. インデックスで title/summary/tags をスコアリング（ファイル読み込みなし）
    2. 上位候補のみ実ファイルを読み込み body を追加スコアリング
    """
    index = memory_utils.load_index(memory_dir)
    if not index.get("entries"):
        index = memory_utils.refresh_index(memory_dir)
    entries = index.get("entries", [])

    # ── ステップ1: インデックスでフィルタ＆スコアリング ──
    candidates = []
    for entry in entries:
        if status_filter and entry.get("status", "active") != status_filter:
            continue
        if category:
            filepath_rel = entry.get("filepath", "")
            entry_cat = os.path.dirname(filepath_rel).replace("\\", "/")
            if entry_cat != category:
                continue
        score = _score_index_entry(entry, keywords)
        if score > 0:
            candidates.append((score, entry))

    if not candidates:
        return []

    candidates.sort(key=lambda x: x[0], reverse=True)
    # 上位30件（またはlimit*3件）までフルファイル読み込み対象
    top = candidates[:max(limit * 3, 30)]

    # ── ステップ2: フルファイル読み込みで body 追加スコア ──
    results = []
    for base_score, entry in top:
        fpath = os.path.join(memory_dir, entry["filepath"])
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                text = f.read()
            meta, body = memory_utils.parse_frontmatter(text)
            body_score = sum(
                min(body.lower().count(kw.lower()), 5) for kw in keywords
            )
            results.append({
                "filepath": fpath,
                "memory_dir": memory_dir,
                "score": base_score + body_score,
                "meta": meta,
                "body": body,
                "entry": entry,
            })
        except (OSError, IOError):
            pass

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def search_all_scopes(scope: str, keywords: list[str], status_filter: str | None,
                      limit: int, category: str | None) -> list[dict]:
    """スコープ横断検索（all の場合は全スコープの結果をマージ）"""
    all_results = []
    for memory_dir in memory_utils.get_memory_dirs(scope):
        if not os.path.isdir(memory_dir):
            continue
        results = search_with_index(memory_dir, keywords, status_filter, limit, category)
        all_results.extend(results)
    all_results.sort(key=lambda r: r["score"], reverse=True)
    return all_results[:limit]


def fallback_search(keywords: list[str], limit: int) -> tuple[list[dict], bool]:
    """workspace で0件の場合の自動フォールバック（home → shared → git pull）"""
    # まず home を検索
    results = search_all_scopes("home", keywords, "active", limit, None)
    if results:
        return results, False

    # shared ローカルを検索
    results = search_all_scopes("shared", keywords, "active", limit, None)
    if results:
        return results, False

    # git pull して再検索
    repos = memory_utils.get_shared_repos()
    if repos:
        print("  → shared を git pull して再検索します...")
        synced = False
        for repo in repos:
            ok, _ = memory_utils.git_pull_repo(repo)
            if ok:
                synced = True
        if synced:
            results = search_all_scopes("shared", keywords, "active", limit, None)
            if results:
                return results, True  # True = git sync した
    return [], False


# ─── access_count 追跡 ────────────────────────────────────────

def track_access(result: dict) -> None:
    """access_count をインクリメントし share_score を再計算してインデックスも更新する"""
    meta = result["meta"]
    body = result["body"]
    filepath = result["filepath"]
    memory_dir = result["memory_dir"]

    new_count = int(meta.get("access_count", 0)) + 1
    today = memory_utils.today_str()
    pseudo_meta = dict(meta, access_count=new_count)
    new_score = memory_utils.compute_share_score(pseudo_meta, body)

    memory_utils.update_frontmatter_fields(filepath, {
        "access_count": new_count,
        "last_accessed": today,
        "share_score": new_score,
    })
    # インデックスも更新
    if memory_dir:
        memory_utils.update_index_entry(memory_dir, filepath)


# ─── 表示 ────────────────────────────────────────────────────

def format_result(result: dict, index: int, full: bool = False) -> str:
    meta = result["meta"]
    memory_dir = result["memory_dir"]
    rel_path = os.path.relpath(result["filepath"], memory_dir)
    scope = meta.get("scope", "workspace")
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    lines = [
        f"[{index}] [{scope}] {meta.get('title', '')} "
        f"(match={result['score']}, share={meta.get('share_score', 0)}, "
        f"rating={meta.get('user_rating', 0)}, corrections={meta.get('correction_count', 0)})",
        f"    Path: {rel_path}",
        f"    Created: {meta.get('created', '')} | Updated: {meta.get('updated', '')} "
        f"| Status: {meta.get('status', '')}",
        f"    Tags: {', '.join(tags) if tags else 'なし'} | access_count: {meta.get('access_count', 0)}",
        f"    Summary: {meta.get('summary', '')}",
    ]
    if full:
        lines += ["", "    --- 全文 ---"] + [f"    {line}" for line in result["body"].splitlines()]
    return "\n".join(lines)


# ─── 評価ループ ──────────────────────────────────────────────

def interactive_rate(results: list[dict]) -> None:
    """recall 結果を表示した後にユーザーが評価を入力できるループ"""
    # rate_memory は recall_memory と相互依存しないが、起動コストを避けるため遅延インポート
    from rate_memory import apply_rating

    print("\n参照した記憶を評価しますか？ (Enter でスキップ)")
    for i, r in enumerate(results, 1):
        mem_id = r["meta"].get("id", "?")
        title = r["meta"].get("title", "?")
        ans = input(
            f"[{i}] {title} ({mem_id})\n"
            f"     評価: [g=良い / b=悪い / c=修正が必要 / Enter=スキップ] > "
        ).strip().lower()

        if ans in ("g", "good"):
            apply_rating(r["filepath"], good=True)
            if r["memory_dir"]:
                memory_utils.update_index_entry(r["memory_dir"], r["filepath"])
            print("  → 良い評価を記録しました ✓")
        elif ans in ("b", "bad"):
            apply_rating(r["filepath"], bad=True)
            if r["memory_dir"]:
                memory_utils.update_index_entry(r["memory_dir"], r["filepath"])
            print("  → 悪い評価を記録しました ✗")
        elif ans in ("c", "correction"):
            note = input("     修正内容を入力してください: ").strip()
            apply_rating(r["filepath"], correction=True, note=note)
            if r["memory_dir"]:
                memory_utils.update_index_entry(r["memory_dir"], r["filepath"])
            print("  → 修正フィードバックを記録しました ⚠")


# ─── メイン ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="記憶をキーワード検索する")
    parser.add_argument("query", help="検索クエリ（スペース区切りで複数キーワード）")
    parser.add_argument("--category", help="カテゴリを絞り込む")
    parser.add_argument("--scope", default="workspace",
                        choices=["workspace", "home", "shared", "all"],
                        help="検索スコープ (default: workspace)")
    parser.add_argument("--status", default="active",
                        choices=["active", "archived", "deprecated", "all"],
                        help="ステータスフィルタ (default: active)")
    parser.add_argument("--limit", type=int, default=10, help="最大表示件数 (default: 10)")
    parser.add_argument("--full", action="store_true", help="全文を表示する")
    parser.add_argument("--no-track", action="store_true",
                        help="access_count を更新しない")
    parser.add_argument("--rate-after", action="store_true",
                        help="結果表示後にインタラクティブ評価ループを実行する")
    args = parser.parse_args()

    keywords = args.query.split()
    status = None if args.status == "all" else args.status

    results = search_all_scopes(args.scope, keywords, status, args.limit, args.category)

    # workspace で0件ならフォールバック
    synced = False
    if not results and args.scope == "workspace":
        print(f"「{args.query}」: ワークスペースに記憶なし → home/shared を検索します...\n")
        results, synced = fallback_search(keywords, args.limit)

    if not results:
        print(f"「{args.query}」に関連する記憶が見つかりませんでした。")
        print("保存するには: python save_memory.py --title '...' --summary '...'")
        sys.exit(0)

    note = "（git pull して取得）" if synced else ""
    print(f"「{args.query}」の検索結果: {len(results)}件{note}\n")
    for i, r in enumerate(results, 1):
        print(format_result(r, i, full=args.full))
        print()

    # access_count を追跡
    if not args.no_track:
        for r in results:
            try:
                track_access(r)
            except Exception:
                pass

    # 結果に対してインタラクティブ評価
    if args.rate_after:
        interactive_rate(results)


if __name__ == "__main__":
    main()
