"""
auto_tagger.py - 自動タグ抽出

保存テキストから関連するタグを自動的に推薦する。
TF-IDF スコアを使用して重要語を抽出する。
"""

import math
import re

import similarity


def suggest_tags(title: str, summary: str, content: str,
                 existing_tags: list[str], corpus: dict,
                 max_tags: int = 5) -> list[str]:
    """保存テキストから自動タグを推薦する。

    戦略:
    1. TF-IDF ベクトルの上位 N 語を候補とする
    2. 既存コーパスで df が高すぎる一般語は除外（IDF が低い）
    3. カテゴリ名に近い語は優先
    4. 既存タグとの重複は除外
    5. 英語・日本語混合に対応（漢字2文字以上の語は優先）

    Args:
        title: タイトル
        summary: 要約
        content: 本文
        existing_tags: 手動で指定された既存タグ
        corpus: コーパス（df を含む）
        max_tags: 最大推薦タグ数

    Returns:
        推薦タグのリスト
    """
    # コーパスが空の場合は簡易的な抽出
    if not corpus.get("df"):
        return _simple_tag_extraction(title, summary, content, existing_tags, max_tags)

    # テキスト全体をトークナイズ
    text = f"{title} {title} {summary} {content}"  # title を2回含めて重み付け
    tokens = similarity.tokenize(text)

    # TF を計算
    tf = similarity.compute_tf(tokens)

    # IDF を計算
    idf = similarity.compute_idf(corpus.get("df", {}), corpus.get("total_docs", 1))

    # TF-IDF スコアを計算して候補をランク付け
    scored = []
    for term, tf_val in tf.items():
        idf_val = idf.get(term, math.log(1000) + 1)
        tfidf = tf_val * idf_val

        # 2文字の日本語バイグラムよりも、漢字語やASCIIワードを優先
        if len(term) >= 3:
            tfidf *= 1.3
        elif re.match(r'[\u4E00-\u9FFF]{2,}', term):
            # 漢字2文字以上の単語
            tfidf *= 1.4
        elif len(term) == 2 and re.match(r'[a-z]{2}', term):
            # 英語2文字（少し減点）
            tfidf *= 0.8

        # 既存タグに類似した語は優先
        for existing in existing_tags:
            if existing.lower() in term or term in existing.lower():
                tfidf *= 1.5
                break

        scored.append((tfidf, term))

    # スコア降順ソート
    scored.sort(reverse=True)

    # 既存タグと重複しない候補を選択
    suggestions = []
    existing_lower = {t.lower() for t in existing_tags}
    for _, term in scored:
        if term.lower() not in existing_lower and term not in suggestions:
            suggestions.append(term)
        if len(suggestions) >= max_tags:
            break

    return suggestions


def _simple_tag_extraction(title: str, summary: str, content: str,
                           existing_tags: list[str], max_tags: int) -> list[str]:
    """コーパスなしの簡易タグ抽出（フォールバック）

    単純に出現回数が多い語を抽出する。
    """
    text = f"{title} {title} {summary} {content}"
    tokens = similarity.tokenize(text)

    # 出現回数をカウント
    from collections import Counter
    counts = Counter(tokens)

    # 既存タグと重複しない上位語を選択
    suggestions = []
    existing_lower = {t.lower() for t in existing_tags}
    for term, _ in counts.most_common(max_tags * 2):
        # 3文字以上または漢字2文字以上を優先
        if len(term) >= 3 or re.match(r'[\u4E00-\u9FFF]{2,}', term):
            if term.lower() not in existing_lower and term not in suggestions:
                suggestions.append(term)
            if len(suggestions) >= max_tags:
                break

    return suggestions


def merge_tags(manual_tags: list[str], auto_tags: list[str], max_total: int = 10) -> tuple[list[str], list[str]]:
    """手動タグと自動タグをマージする

    Args:
        manual_tags: 手動で指定されたタグ
        auto_tags: 自動推薦タグ
        max_total: 最大総タグ数

    Returns:
        (merged_tags, auto_tags_used) のタプル
        - merged_tags: 手動 + 自動の統合リスト
        - auto_tags_used: 実際に追加された自動タグのリスト
    """
    merged = list(manual_tags)
    auto_tags_used = []
    manual_lower = {t.lower() for t in manual_tags}

    for tag in auto_tags:
        if tag.lower() not in manual_lower and len(merged) < max_total:
            merged.append(tag)
            auto_tags_used.append(tag)
            if len(merged) >= max_total:
                break

    return merged, auto_tags_used
