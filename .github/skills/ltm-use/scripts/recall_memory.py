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
import subprocess
import sys

import memory_utils
import similarity


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


def _compute_meta_boost(entry: dict) -> float:
    """メタデータに基づく boost スコア（0.0〜1.0）。

    v5: retention_score があれば忘却曲線値を使用（なければ鮮度線形補間にフォールバック）。
    """
    access_count = entry.get("access_count", 0)
    user_rating = entry.get("user_rating", 0)
    status = entry.get("status", "active")

    # 参照回数（最大 0.25）
    access_boost = min(access_count / 20, 0.25)

    # ユーザー評価（最大 0.25）
    rating_boost = min(max(user_rating / 3, 0.0), 0.25)

    # v5: retention_score → 0.3 成分 / v4 互換: freshness → 0.2 成分
    retention_val = entry.get("retention_score", None)
    if retention_val is not None:
        retention_component = 0.3 * float(retention_val)
    else:
        updated = entry.get("updated", "")
        days_old = memory_utils.days_since(updated) if updated else 999
        if days_old < 30:
            freshness = 0.2
        elif days_old < 90:
            freshness = 0.1
        else:
            freshness = 0.0
        retention_component = freshness

    # ステータス（0.2）
    status_boost = 0.2 if status == "active" else 0.0

    return access_boost + rating_boost + retention_component + status_boost


def _collect_auto_context() -> str:
    """git diff --stat と cwd からコンテキストを自動収集する（v5 auto-context）。"""
    parts = [os.path.basename(os.getcwd())]
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "|" in line:
                    fname = line.split("|")[0].strip()
                    if fname:
                        parts.append(os.path.splitext(os.path.basename(fname))[0])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return " ".join(parts)


def search_with_index(memory_dir: str, keywords: list[str],
                      status_filter: str | None, limit: int,
                      category: str | None = None,
                      use_hybrid: bool = True,
                      context_text: str = "",
                      memory_type_filter: str = "") -> list[dict]:
    """インデックス優先の2段階検索（v5: 4軸ハイブリッドランキング対応）。

    1. インデックスで title/summary/tags をスコアリング（ファイル読み込みなし）
    2. TF-IDF コサイン類似度を計算（コーパスがある場合）
    3. v4（3軸）: 0.5*keyword + 0.35*tfidf + 0.15*meta_boost
       v5（4軸）: 0.4*keyword + 0.3*tfidf + 0.15*meta_boost + 0.15*context_boost
    4. 上位候補のみ実ファイルを読み込み body キーワードスコアを加算
    """
    index = memory_utils.load_index(memory_dir)
    if not index.get("entries"):
        index = memory_utils.refresh_index(memory_dir)
    entries = index.get("entries", [])

    corpus = None
    query_vector = None
    context_vector = None
    idf: dict = {}

    if use_hybrid:
        corpus = similarity.load_corpus(memory_dir)
        if corpus.get("doc_vectors"):
            idf = similarity.compute_idf(corpus.get("df", {}), corpus.get("total_docs", 1))
            query_text = " ".join(keywords)
            query_tokens = similarity.tokenize(query_text)
            query_vector = similarity.compute_tfidf_vector(query_tokens, idf)
            # v5 コンテキストベクトル
            if context_text:
                ctx_tokens = similarity.tokenize(context_text)
                context_vector = similarity.compute_tfidf_vector(ctx_tokens, idf)

    # ── ステップ1: インデックスでフィルタ＆スコアリング ──
    candidates = []
    for entry in entries:
        if status_filter and entry.get("status", "active") != status_filter:
            continue
        if memory_type_filter and entry.get("memory_type", "semantic") != memory_type_filter:
            continue
        if category:
            filepath_rel = entry.get("filepath", "")
            entry_cat = os.path.dirname(filepath_rel).replace("\\", "/")
            if entry_cat != category:
                continue

        kw_score = _score_index_entry(entry, keywords)

        if query_vector and corpus:
            mem_id = entry.get("id", "")
            doc_vec = corpus.get("doc_vectors", {}).get(mem_id, {})
            tfidf_sim = similarity.cosine_similarity(query_vector, doc_vec) if doc_vec else 0.0
            meta_boost = _compute_meta_boost(entry)
            kw_max = len(keywords) * 20
            kw_norm = min(kw_score / kw_max, 1.0) if kw_max > 0 else 0.0

            if context_vector and doc_vec:
                # v5 4軸スコア
                ctx_boost = similarity.cosine_similarity(context_vector, doc_vec)
                hybrid = (0.4 * kw_norm + 0.3 * tfidf_sim
                          + 0.15 * meta_boost + 0.15 * ctx_boost)
            else:
                # v4 3軸スコア（context 未指定時）
                hybrid = 0.5 * kw_norm + 0.35 * tfidf_sim + 0.15 * meta_boost

            if hybrid > 0.05:
                candidates.append((hybrid, entry, kw_score))
        else:
            # v3 互換モード（コーパスなし）
            if kw_score > 0:
                candidates.append((kw_score, entry, kw_score))

    if not candidates:
        return []

    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[:max(limit * 3, 30)]

    # ── ステップ2: フルファイル読み込みで body キーワードスコアを加算 ──
    results = []
    for base_score, entry, kw_score in top:
        fpath = os.path.join(memory_dir, entry["filepath"])
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                text = f.read()
            meta, body = memory_utils.parse_frontmatter(text)
            body_score = sum(min(body.lower().count(kw.lower()), 5) for kw in keywords)
            # body のキーワード一致は常に加算（Fix 5: 論理バグ修正）
            final_score = base_score + body_score
            results.append({
                "filepath": fpath,
                "memory_dir": memory_dir,
                "score": final_score,
                "keyword_score": kw_score,
                "meta": meta,
                "body": body,
                "entry": entry,
            })
        except (OSError, IOError):
            pass

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def search_all_scopes(scope: str, keywords: list[str], status_filter: str | None,
                      limit: int, category: str | None,
                      context_text: str = "",
                      memory_type_filter: str = "") -> list[dict]:
    """スコープ横断検索（all の場合は全スコープの結果をマージ）"""
    all_results = []
    for memory_dir in memory_utils.get_memory_dirs(scope):
        if not os.path.isdir(memory_dir):
            continue
        results = search_with_index(memory_dir, keywords, status_filter, limit,
                                    category, context_text=context_text,
                                    memory_type_filter=memory_type_filter)
        all_results.extend(results)
    all_results.sort(key=lambda r: r["score"], reverse=True)
    return all_results[:limit]


def fallback_search(keywords: list[str], limit: int,
                    memory_type_filter: str = "") -> tuple[list[dict], bool]:
    """workspace で0件の場合の自動フォールバック（home → shared → git pull）"""
    # まず home を検索
    results = search_all_scopes("home", keywords, "active", limit, None,
                                memory_type_filter=memory_type_filter)
    if results:
        return results, False

    # shared ローカルを検索
    results = search_all_scopes("shared", keywords, "active", limit, None,
                                memory_type_filter=memory_type_filter)
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
            results = search_all_scopes("shared", keywords, "active", limit, None,
                                        memory_type_filter=memory_type_filter)
            if results:
                return results, True  # True = git sync した
    return [], False


# ─── access_count 追跡 ────────────────────────────────────────

def track_access(result: dict) -> None:
    """access_count をインクリメントし share_score と retention_score を再計算する。

    v5: recall 時に retention_score もリセット（間隔反復効果）。
    """
    meta = result["meta"]
    body = result["body"]
    filepath = result["filepath"]
    memory_dir = result["memory_dir"]

    new_count = int(meta.get("access_count", 0)) + 1
    today = memory_utils.today_str()
    pseudo_meta = dict(meta, access_count=new_count, last_accessed=today)
    new_score = memory_utils.compute_share_score(pseudo_meta, body)
    # v5: アクセス直後の retention は最高値（忘却曲線リセット）
    new_retention = memory_utils.compute_retention_score(pseudo_meta)

    memory_utils.update_frontmatter_fields(filepath, {
        "access_count": new_count,
        "last_accessed": today,
        "share_score": new_score,
        "retention_score": round(new_retention, 3),
    })
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
    parser.add_argument("query", nargs="?", default="",
                        help="検索クエリ（スペース区切りで複数キーワード）")
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
    # v5.0.0 文脈依存想起
    parser.add_argument("--context", default="",
                        help="作業コンテキストを指定して関連性ブースト（v5 4軸ランキング）")
    parser.add_argument("--auto-context", action="store_true",
                        help="git diff / cwd からコンテキストを自動収集して使用")
    parser.add_argument("--memory-type",
                        choices=["episodic", "semantic", "procedural"],
                        help="記憶タイプでフィルタリング")
    args = parser.parse_args()

    if not args.query and not args.auto_context:
        parser.error("query または --auto-context が必要です")

    # v5 コンテキスト収集
    context_text = args.context
    if args.auto_context and not context_text:
        context_text = _collect_auto_context()
        if context_text:
            print(f"[auto-context] {context_text}\n")

    keywords = args.query.split() if args.query else []
    status = None if args.status == "all" else args.status

    results = search_all_scopes(args.scope, keywords, status, args.limit, args.category,
                                context_text=context_text,
                                memory_type_filter=args.memory_type or "")

    # workspace で0件ならフォールバック
    synced = False
    if not results and args.scope == "workspace":
        query_label = args.query or "(auto-context)"
        print(f"「{query_label}」: ワークスペースに記憶なし → home/shared を検索します...\n")
        results, synced = fallback_search(keywords, args.limit,
                                          memory_type_filter=args.memory_type or "")

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
