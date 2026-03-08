"""
similarity.py - 類似度エンジン（TF-IDF + コサイン類似度）

Python標準ライブラリのみで実装。
外部依存なし（numpy, scikit-learn 不使用）。
"""

import datetime
import json
import math
import os
import re
import unicodedata
from collections import Counter

import memory_utils


# ─── ストップワード ──────────────────────────────────────────

STOP_WORDS_JA = {
    "の", "は", "が", "で", "に", "を", "と", "た", "する", "ある", "いる",
    "この", "その", "から", "まで", "れる", "れた", "して", "した", "こと",
    "など", "ため", "よう", "さん", "これ", "それ", "あり", "なる", "もの",
    "ない", "なし", "られ", "です", "ます", "でき", "できる", "として",
}

STOP_WORDS_EN = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "can", "could", "may", "might", "shall", "should", "must",
    "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "and", "or", "but", "not", "this", "that", "it", "as", "if",
}

STOP_WORDS = STOP_WORDS_JA | STOP_WORDS_EN


# ─── トークナイゼーション ────────────────────────────────────

def tokenize(text: str) -> list[str]:
    """日英混合テキストをトークンに分割する。

    戦略:
    1. ASCII 部分: 空白・記号で分割 → 小文字化
    2. 日本語部分: 2-gram（文字バイグラム）で分割
       - 形態素解析なしでも実用的な精度を得るため
       - 例: "認証トークン" → ["認証", "証ト", "トー", "ーク", "クン"]
       - 漢字連続は単語境界として扱い、2文字以上の漢字列も1トークンにする
    3. ストップワードを除去
    4. 1文字トークンを除去

    Args:
        text: 入力テキスト（日英混合可）

    Returns:
        トークンのリスト（重複を許す）
    """
    text = text.lower()
    tokens = []

    # ASCII ワード抽出（英数字・ハイフン・アンダースコアを許可）
    ascii_words = re.findall(r'[a-z][a-z0-9_-]*[a-z0-9]|[a-z]', text)
    tokens.extend(w for w in ascii_words if len(w) >= 2)

    # 日本語部分の抽出（CJK Unified Ideographs + Katakana + Hiragana）
    ja_segments = re.findall(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBF]+', text)
    for seg in ja_segments:
        # 漢字の連続をまず単語として扱う（2文字以上）
        kanji_words = re.findall(r'[\u4E00-\u9FFF\u3400-\u4DBF]{2,}', seg)
        tokens.extend(kanji_words)
        # 全体で文字バイグラムも生成（カタカナ語やひらがな混じりのカバー）
        if len(seg) >= 2:
            for i in range(len(seg) - 1):
                bigram = seg[i:i+2]
                tokens.append(bigram)

    # ストップワード除去
    tokens = [t for t in tokens if t not in STOP_WORDS and len(t) >= 2]
    return tokens


# ─── TF-IDF ──────────────────────────────────────────────────

def compute_tf(tokens: list[str]) -> dict[str, float]:
    """Term Frequency（対数スケーリング）

    Args:
        tokens: トークンのリスト

    Returns:
        {term: tf_value} の辞書
    """
    counts = Counter(tokens)
    if not counts:
        return {}
    return {term: 1 + math.log(count) for term, count in counts.items()}


def compute_idf(df: dict[str, int], total_docs: int) -> dict[str, float]:
    """Inverse Document Frequency

    Args:
        df: {term: document_frequency} の辞書
        total_docs: 総ドキュメント数

    Returns:
        {term: idf_value} の辞書
    """
    return {
        term: math.log((total_docs + 1) / (freq + 1)) + 1
        for term, freq in df.items()
    }


def compute_tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """TF-IDF ベクトル（スパース辞書表現）を計算する

    Args:
        tokens: トークンのリスト
        idf: {term: idf_value} の辞書

    Returns:
        L2正規化済みの {term: tfidf_value} の辞書
    """
    tf = compute_tf(tokens)
    vector = {}
    for term, tf_val in tf.items():
        idf_val = idf.get(term, math.log(1000) + 1)  # 未知語は高IDF
        vector[term] = tf_val * idf_val

    # L2 正規化
    norm = math.sqrt(sum(v * v for v in vector.values()))
    if norm > 0:
        vector = {k: v / norm for k, v in vector.items()}
    return vector


def cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """スパースベクトル同士のコサイン類似度（0.0〜1.0）

    Args:
        vec_a: L2正規化済みのTF-IDFベクトル
        vec_b: L2正規化済みのTF-IDFベクトル

    Returns:
        コサイン類似度（0.0〜1.0）
    """
    # 両ベクトルが L2 正規化済みなら内積 = コサイン類似度
    common_keys = set(vec_a.keys()) & set(vec_b.keys())
    if not common_keys:
        return 0.0
    return sum(vec_a[k] * vec_b[k] for k in common_keys)


# ─── コーパス管理 ────────────────────────────────────────────

def get_corpus_path(memory_dir: str) -> str:
    """コーパスファイルのパスを返す"""
    return os.path.join(memory_dir, memory_utils.CORPUS_FILENAME)


def load_corpus(memory_dir: str) -> dict:
    """コーパスファイルを読み込む（存在しなければ空を返す）

    Returns:
        {
            "version": 1,
            "built_at": "2026-03-08T12:00:00",
            "total_docs": 42,
            "df": {term: doc_freq, ...},
            "doc_vectors": {mem_id: {term: tfidf, ...}, ...}
        }
    """
    path = get_corpus_path(memory_dir)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "version": 1,
        "built_at": "",
        "total_docs": 0,
        "df": {},
        "doc_vectors": {},
    }


def save_corpus(memory_dir: str, corpus: dict) -> None:
    """コーパスをファイルに書き込む"""
    os.makedirs(memory_dir, exist_ok=True)
    path = get_corpus_path(memory_dir)
    corpus["built_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    corpus["total_docs"] = len(corpus.get("doc_vectors", {}))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)


def build_corpus(memory_dir: str) -> dict:
    """全記憶からコーパス（df + doc_vectors）を構築する

    インデックスを使用するため高速（body 読み込みなし）。

    Args:
        memory_dir: メモリーディレクトリのパス

    Returns:
        構築済みコーパス
    """
    index = memory_utils.load_index(memory_dir)
    if not index.get("entries"):
        index = memory_utils.refresh_index(memory_dir)
    entries = index.get("entries", [])

    df: dict[str, int] = {}          # document frequency
    doc_tokens: dict[str, list] = {} # mem_id → tokens

    for entry in entries:
        if entry.get("status") == "deprecated":
            continue
        mem_id = entry.get("id", "")
        if not mem_id:
            continue

        # title + summary + tags からトークンを生成（body は読まない → 高速）
        text = " ".join([
            entry.get("title", ""),
            entry.get("summary", ""),
            " ".join(entry.get("tags", [])),
        ])
        tokens = tokenize(text)
        doc_tokens[mem_id] = tokens

        # document frequency を更新
        seen = set()
        for t in tokens:
            if t not in seen:
                df[t] = df.get(t, 0) + 1
                seen.add(t)

    total_docs = len(doc_tokens)
    idf = compute_idf(df, total_docs)

    # 各ドキュメントの TF-IDF ベクトルを計算
    doc_vectors = {}
    for mem_id, tokens in doc_tokens.items():
        doc_vectors[mem_id] = compute_tfidf_vector(tokens, idf)

    corpus = {
        "version": 1,
        "built_at": "",
        "total_docs": total_docs,
        "df": df,
        "doc_vectors": doc_vectors,
    }
    save_corpus(memory_dir, corpus)
    return corpus


def update_corpus_entry(memory_dir: str, mem_id: str, title: str,
                        summary: str, tags: list[str]) -> None:
    """単一記憶のコーパスエントリを更新する（save/rate 後に呼ぶ）

    既存コーパスがない場合は何もしない（build_corpus を先に実行すること）。

    Args:
        memory_dir: メモリーディレクトリのパス
        mem_id: 記憶ID
        title: タイトル
        summary: 要約
        tags: タグリスト
    """
    corpus = load_corpus(memory_dir)
    if not corpus.get("doc_vectors"):
        # コーパス未構築の場合はスキップ（次回 build_corpus で再構築）
        return

    # 新規トークンを生成
    text = f"{title} {summary} {' '.join(tags)}"
    tokens = tokenize(text)

    # 既存の df から IDF を再計算（簡易版: 既存 IDF を流用）
    idf = compute_idf(corpus.get("df", {}), corpus.get("total_docs", 1))

    # ベクトル更新
    corpus["doc_vectors"][mem_id] = compute_tfidf_vector(tokens, idf)

    # df の更新（厳密には全体再計算が必要だが、近似として既存 df を維持）
    # → 正確性が必要な場合は build_corpus を実行すること

    save_corpus(memory_dir, corpus)


def remove_corpus_entry(memory_dir: str, mem_id: str) -> None:
    """コーパスから記憶を削除する（cleanup/delete 後に呼ぶ）

    Args:
        memory_dir: メモリーディレクトリのパス
        mem_id: 削除する記憶ID
    """
    corpus = load_corpus(memory_dir)
    if mem_id in corpus.get("doc_vectors", {}):
        del corpus["doc_vectors"][mem_id]
        # df は再計算しない（次回 build_corpus で正確化）
        save_corpus(memory_dir, corpus)


def find_similar_memories(memory_dir: str, title: str, summary: str,
                          tags: list[str], threshold: float = 0.65,
                          limit: int = 5) -> list[dict]:
    """新規記憶と類似する既存記憶を検索する（save 時の重複チェック用）

    Args:
        memory_dir: メモリーディレクトリのパス
        title: 新規記憶のタイトル
        summary: 新規記憶の要約
        tags: 新規記憶のタグ
        threshold: 類似度閾値（この値以上を返す）
        limit: 最大返却数

    Returns:
        [{"mem_id": "mem-XXX", "title": "...", "similarity": 0.xx}, ...]
    """
    corpus = load_corpus(memory_dir)
    if not corpus.get("doc_vectors"):
        return []

    # 新規記憶をベクトル化
    text = f"{title} {summary} {' '.join(tags)}"
    tokens = tokenize(text)
    idf = compute_idf(corpus.get("df", {}), corpus.get("total_docs", 1))
    query_vec = compute_tfidf_vector(tokens, idf)

    # 全記憶との類似度を計算
    results = []
    for mem_id, doc_vec in corpus.get("doc_vectors", {}).items():
        sim = cosine_similarity(query_vec, doc_vec)
        if sim >= threshold:
            results.append({"mem_id": mem_id, "similarity": sim})

    # 類似度降順ソート
    results.sort(key=lambda x: x["similarity"], reverse=True)

    # インデックスから title/summary を取得
    index = memory_utils.load_index(memory_dir)
    entries_by_id = {e["id"]: e for e in index.get("entries", [])}
    for r in results[:limit]:
        entry = entries_by_id.get(r["mem_id"], {})
        r["title"] = entry.get("title", "")
        r["summary"] = entry.get("summary", "")
        r["filepath"] = entry.get("filepath", "")

    return results[:limit]
