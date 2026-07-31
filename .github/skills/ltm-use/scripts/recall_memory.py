#!/usr/bin/env python3
from __future__ import annotations
"""
recall_memory.py - キーワードで記憶を検索するスクリプト

インデックスを使った高速検索（大量記憶でも O(index) で動作）。
recall すると access_count が加算され share_score が自動更新される。
home で見つからない場合、shared を自動フォールバック検索する。

Usage:
  python recall_memory.py "JWT 認証"
  python recall_memory.py "バグ" --category bug-investigation
  python recall_memory.py "API" --scope all --limit 5
  python recall_memory.py "デプロイ" --full        # 全文表示
  python recall_memory.py "設計" --no-track        # access_count 更新しない
  python recall_memory.py "JWT" --rate-after       # 結果表示後に評価入力
"""

import argparse
import json
import os
import re
import subprocess
import sys

import memory_utils
import similarity


# ─── Agentic Search（反復探索）パラメータ ─────────────────────
#
# recall は単発のハイブリッド検索を行うプリミティブであり、反復ループの駆動役は
# エージェント（Claude）が担う。スクリプトは「1ステップの検索 + 次の一手の手がかり」を
# 返すことで agentic search を支援する（--suggest / --json）。
SUFFICIENT_SCORE = 0.5      # max_score がこの値以上なら「十分な手がかりあり」と判定
MIN_SUGGEST_SCORE = 0.15    # フォローアップ候補の抽出に使う結果の足切りスコア
MAX_SUGGESTED_QUERIES = 5   # 提示するフォローアップクエリの最大数
MEM_ID_RE = re.compile(r"^mem-\d{8}-\d+$")


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
        if memory_type_filter and entry.get("memory_type", memory_utils.DEFAULT_MEMORY_TYPE) != memory_type_filter:
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
            # body_score を 0-1 スケールに正規化して軽い補正として加算（最大 +0.2）
            body_boost = min(body_score / 50.0, 0.2)
            final_score = base_score + body_boost
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
                      memory_type_filter: str = "",
                      use_hybrid: bool = True) -> list[dict]:
    """スコープ横断検索（all の場合は全スコープの結果をマージ）"""
    all_results = []
    for memory_dir in memory_utils.get_memory_dirs(scope):
        if not os.path.isdir(memory_dir):
            continue
        results = search_with_index(memory_dir, keywords, status_filter, limit,
                                    category, use_hybrid=use_hybrid,
                                    context_text=context_text,
                                    memory_type_filter=memory_type_filter)
        all_results.extend(results)
    all_results.sort(key=lambda r: r["score"], reverse=True)
    return all_results[:limit]


def fallback_search(keywords: list[str], limit: int,
                    memory_type_filter: str = "",
                    use_hybrid: bool = True) -> tuple[list[dict], bool]:
    """home で0件の場合の自動フォールバック（shared → git pull）"""
    # shared ローカルを検索
    results = search_all_scopes("shared", keywords, "active", limit, None,
                                memory_type_filter=memory_type_filter,
                                use_hybrid=use_hybrid)
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
                                        memory_type_filter=memory_type_filter,
                                        use_hybrid=use_hybrid)
            if results:
                return results, True  # True = git sync した
    return [], False


# ─── ID 指定取得（マルチホップ展開用） ────────────────────────

def fetch_by_ids(ids: list[str], scope: str) -> list[dict]:
    """記憶IDを指定して直接取得する（agentic search のマルチホップ展開で使用）。

    related / consolidated_from / consolidated_to で得た関連IDを次ステップで
    引き当てるための入口。スコア計算は行わず frontmatter と body を返す。
    """
    wanted = set(ids)
    results: list[dict] = []
    seen: set[str] = set()
    for memory_dir in memory_utils.get_memory_dirs(scope):
        if not wanted or not os.path.isdir(memory_dir):
            continue
        index = memory_utils.load_index(memory_dir)
        if not index.get("entries"):
            index = memory_utils.refresh_index(memory_dir)
        for entry in index.get("entries", []):
            mem_id = entry.get("id", "")
            if mem_id not in wanted or mem_id in seen:
                continue
            fpath = os.path.join(memory_dir, entry["filepath"])
            if not os.path.exists(fpath):
                continue
            try:
                with open(fpath, encoding="utf-8") as f:
                    meta, body = memory_utils.parse_frontmatter(f.read())
            except (OSError, IOError):
                continue
            seen.add(mem_id)
            results.append({
                "filepath": fpath,
                "memory_dir": memory_dir,
                "score": 1.0,
                "keyword_score": 0,
                "meta": meta,
                "body": body,
                "entry": entry,
            })
    return results


# ─── Agentic Search ヒント計算 ───────────────────────────────
#
# ヒント計算は共有スキル agentic-search（兄弟ディレクトリの hints.py）に委譲する。
# 未導入時はローカルのフォールバック実装に切り替える（オプショナル依存）。

def _as_list(value) -> list[str]:
    """frontmatter のリスト系フィールドを文字列リストに正規化する。"""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[,\n]", value) if v.strip()]
    return []


def _load_shared_hints():
    """共有スキル agentic-search の hints モジュールを読み込む（無ければ None）。"""
    as_dir = os.path.join(memory_utils.get_skill_dir(), os.pardir,
                          "agentic-search", "scripts")
    if not os.path.isdir(as_dir):
        return None
    if as_dir not in sys.path:
        sys.path.insert(0, as_dir)
    try:
        import hints as shared_hints  # type: ignore
        return shared_hints
    except ImportError:
        return None


_SHARED_HINTS = _load_shared_hints()


def _to_normalized(results: list[dict]) -> list[dict]:
    """ltm の recall 結果を agentic-search の正規化済み結果契約へ変換する。

    related は `--ids` で直接引ける mem-ID のみに絞る（related フィールドのパス参照は除外）。
    """
    norm = []
    for r in results:
        meta = r["meta"]
        refs = (_as_list(meta.get("related"))
                + _as_list(meta.get("consolidated_from"))
                + _as_list(meta.get("consolidated_to")))
        related = [ref for ref in refs if MEM_ID_RE.match(ref)]
        norm.append({
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "summary": meta.get("summary", ""),
            "tags": _as_list(meta.get("tags")),
            "score": r["score"],
            "related": related,
            "text": r.get("body", ""),
        })
    return norm


def _fallback_compute_hints(norm: list[dict], keywords: list[str]) -> dict:
    """agentic-search 未導入時のローカル・フォールバック（同等のスキーマを返す）。"""
    query_lower = {k.lower() for k in keywords}
    result_ids = {n["id"] for n in norm}

    related_ids: list[str] = []
    for n in norm:
        for ref in n["related"]:
            if ref not in result_ids and ref not in related_ids:
                related_ids.append(ref)

    tag_freq: dict[str, int] = {}
    for n in norm:
        if n["score"] < MIN_SUGGEST_SCORE:
            continue
        for tag in n["tags"]:
            if tag.lower() in query_lower:
                continue
            tag_freq[tag] = tag_freq.get(tag, 0) + 1
    base = " ".join(keywords).strip()
    suggested: list[str] = []
    for tag, _f in sorted(tag_freq.items(), key=lambda kv: kv[1], reverse=True):
        s = f"{base} {tag}".strip() if base else tag
        if s not in suggested:
            suggested.append(s)
        if len(suggested) >= MAX_SUGGESTED_QUERIES:
            break

    texts = [" ".join([n["title"], n["summary"], " ".join(n["tags"]),
                       n.get("text", "")]).lower() for n in norm]
    gaps = [kw for kw in keywords if not any(kw.lower() in t for t in texts)]

    count = len(norm)
    max_score = max((n["score"] for n in norm), default=0.0)
    sufficient = count > 0 and max_score >= SUFFICIENT_SCORE
    if count == 0:
        next_action = "broaden"
    elif max_score < SUFFICIENT_SCORE:
        next_action = "refine"
    elif related_ids:
        next_action = "expand"
    else:
        next_action = "synthesize"
    return {
        "sufficient": sufficient,
        "max_score": round(max_score, 3),
        "result_count": count,
        "next_action": next_action,
        "suggested_queries": suggested,
        "related_ids": related_ids,
        "gap_keywords": gaps,
    }


def build_hints(results: list[dict], keywords: list[str]) -> dict:
    """agentic search の「次の一手」ヒントを構築する（共有スキルへ委譲）。

    エージェントはこのヒントを読み、再検索（refine/broaden/expand）するか
    結果を統合（synthesize）するかを判断する。
    """
    norm = _to_normalized(results)
    if _SHARED_HINTS is not None:
        return _SHARED_HINTS.compute_hints(norm, keywords,
                                           sufficient_score=SUFFICIENT_SCORE)
    return _fallback_compute_hints(norm, keywords)


def build_json_output(query: str, scope: str, results: list[dict],
                      keywords: list[str], with_hints: bool) -> dict:
    """recall 結果を機械可読な JSON 構造にまとめる（agentic search 用）。"""
    items = []
    for rank, r in enumerate(results, 1):
        meta = r["meta"]
        items.append({
            "rank": rank,
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "summary": meta.get("summary", ""),
            "tags": _as_list(meta.get("tags")),
            "memory_type": meta.get("memory_type", memory_utils.DEFAULT_MEMORY_TYPE),
            "importance": meta.get("importance", "normal"),
            "status": meta.get("status", "active"),
            "score": round(r["score"], 3),
            "keyword_score": r["keyword_score"],
            "retention_score": meta.get("retention_score", ""),
            "access_count": meta.get("access_count", 0),
            "filepath": os.path.relpath(r["filepath"], r["memory_dir"]),
            "related": _as_list(meta.get("related")),
            "consolidated_from": _as_list(meta.get("consolidated_from")),
            "consolidated_to": meta.get("consolidated_to", ""),
        })
    out = {"query": query, "scope": scope, "count": len(items), "results": items}
    if with_hints:
        out["hints"] = build_hints(results, keywords)
    return out


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
    scope = meta.get("scope", "home")
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    memory_type = meta.get("memory_type", memory_utils.DEFAULT_MEMORY_TYPE)
    importance = meta.get("importance", "normal")
    retention = meta.get("retention_score", "")
    retention_str = f"{float(retention):.2f}" if retention != "" else "N/A"

    lines = [
        f"[{index}] [{scope}] {meta.get('title', '')} "
        f"(match={result['score']}, share={meta.get('share_score', 0)}, "
        f"rating={meta.get('user_rating', 0)}, corrections={meta.get('correction_count', 0)})",
        f"    Path: {rel_path}",
        f"    Created: {meta.get('created', '')} | Updated: {meta.get('updated', '')} "
        f"| Status: {meta.get('status', '')}",
        f"    Type: {memory_type} | Importance: {importance} | Retention: {retention_str} "
        f"| access_count: {meta.get('access_count', 0)}",
        f"    Tags: {', '.join(tags) if tags else 'なし'}",
        f"    Summary: {meta.get('summary', '')}",
    ]
    if full:
        lines += ["", "    --- 全文 ---"] + [f"    {line}" for line in result["body"].splitlines()]
    return "\n".join(lines)


def format_hints(hints: dict) -> str:
    """ヒントを人間可読に整形する（--suggest 時に結果末尾へ表示）。"""
    action_label = {
        "broaden": "broaden（語を減らす／同義語で広げる）",
        "refine": "refine（suggested_queries でクエリを再構成して再検索）",
        "expand": "expand（related_ids を --ids で辿りマルチホップ展開）",
        "synthesize": "synthesize（手がかり十分。反復を終了して結果を統合）",
    }
    lines = [
        "─── 🔍 agentic search hints ───",
        f"  sufficient: {hints['sufficient']} "
        f"(max_score={hints['max_score']}, count={hints['result_count']})",
        f"  next_action: {action_label.get(hints['next_action'], hints['next_action'])}",
    ]
    if hints["suggested_queries"]:
        lines.append("  suggested_queries:")
        for q in hints["suggested_queries"]:
            lines.append(f"    - {q}")
    if hints["related_ids"]:
        lines.append(f"  related_ids (--ids で取得可): {', '.join(hints['related_ids'])}")
    if hints["gap_keywords"]:
        lines.append(f"  gap_keywords (未ヒット): {', '.join(hints['gap_keywords'])}")
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
    parser.add_argument("--scope", default="home",
                        choices=["home", "shared", "all"],
                        help="検索スコープ (default: home)")
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
    parser.add_argument("--no-hybrid", action="store_true",
                        help="TF-IDF/コンテキストを無効化しキーワード一致のみで検索（v3互換モード）")
    # v5.4.0 Agentic Search（反復探索）
    parser.add_argument("--json", action="store_true",
                        help="機械可読な JSON で出力（agentic search のループ駆動用）")
    parser.add_argument("--suggest", action="store_true",
                        help="検索後に次の一手のヒント（フォローアップ候補・関連ID・充足判定）を提示する")
    parser.add_argument("--ids", default="",
                        help="記憶IDをカンマ区切りで直接取得（マルチホップ展開用。クエリ不要）")
    args = parser.parse_args()

    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if not args.query and not args.auto_context and not ids:
        parser.error("query / --auto-context / --ids のいずれかが必要です")

    # --json 時は情報メッセージを stderr に逃がして stdout を JSON 専用にする
    def info(msg: str) -> None:
        print(msg, file=sys.stderr if args.json else sys.stdout)

    keywords = args.query.split() if args.query else []
    status = None if args.status == "all" else args.status
    use_hybrid = not args.no_hybrid

    # ── マルチホップ展開: ID 指定取得（agentic search の expand ステップ） ──
    if ids:
        results = fetch_by_ids(ids, args.scope if args.scope != "home" else "all")
        if not results:
            if args.json:
                print(json.dumps(build_json_output(args.ids, args.scope, [], keywords,
                                                   args.suggest), ensure_ascii=False, indent=2))
            else:
                info(f"指定ID {args.ids} に該当する記憶が見つかりませんでした。")
            sys.exit(0)
        _emit_results(args, args.ids, results, keywords, synced=False, info=info)
        return

    # v5 コンテキスト収集
    context_text = args.context
    if args.auto_context and not context_text:
        context_text = _collect_auto_context()
        if context_text:
            info(f"[auto-context] {context_text}\n")

    results = search_all_scopes(args.scope, keywords, status, args.limit, args.category,
                                context_text=context_text,
                                memory_type_filter=args.memory_type or "",
                                use_hybrid=use_hybrid)

    # home で0件ならフォールバック
    synced = False
    if not results and args.scope == "home":
        query_label = args.query or "(auto-context)"
        info(f"「{query_label}」: home に記憶なし → shared を検索します...\n")
        results, synced = fallback_search(keywords, args.limit,
                                          memory_type_filter=args.memory_type or "",
                                          use_hybrid=use_hybrid)

    if not results:
        if args.json:
            print(json.dumps(build_json_output(args.query, args.scope, [], keywords,
                                               args.suggest), ensure_ascii=False, indent=2))
            sys.exit(0)
        print(f"「{args.query}」に関連する記憶が見つかりませんでした。")
        if args.suggest:
            print(format_hints(build_hints([], keywords)))
        print("保存するには: python save_memory.py --title '...' --summary '...'")
        sys.exit(0)

    _emit_results(args, args.query, results, keywords, synced=synced, info=info)


def _emit_results(args, query: str, results: list[dict], keywords: list[str],
                  synced: bool, info) -> None:
    """結果を出力し（人間可読 or JSON）、access_count を追跡する共通処理。"""
    if args.json:
        out = build_json_output(query, args.scope, results, keywords, args.suggest)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        note = "（git pull して取得）" if synced else ""
        print(f"「{query}」の検索結果: {len(results)}件{note}\n")
        for i, r in enumerate(results, 1):
            print(format_result(r, i, full=args.full))
            print()
        if args.suggest:
            print(format_hints(build_hints(results, keywords)))
            print()

    # access_count を追跡
    if not args.no_track:
        for r in results:
            try:
                track_access(r)
            except Exception:
                pass

    # 結果に対してインタラクティブ評価（JSON モードでは無効）
    if args.rate_after and not args.json:
        interactive_rate(results)


if __name__ == "__main__":
    main()
