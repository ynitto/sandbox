#!/usr/bin/env python3
from __future__ import annotations
"""
hints.py - agentic search の「次の一手」ヒントエンジン（バックエンド非依存）

検索スキル（ltm-use / wiki-use / moltbook-use など）は、自前のコーパスを検索した結果を
本モジュールが定める **正規化済み結果リスト** に変換して渡す。本モジュールはその結果と
クエリから、エージェント（Claude）が反復ループを駆動するための手がかり（hints）を計算する。

設計思想:
  反復ループ（plan → search → evaluate → reformulate → expand → synthesize）の駆動役は
  エージェントが担う。本モジュールは反復を内蔵せず、1 ステップ分の結果に対して
  「次に何をすべきか（next_action）」と再検索・展開の材料だけを返すプリミティブに徹する。

正規化済み結果アイテムの契約（呼び出し側がこの形に変換する）:
  {
    "id":      "string",          # バックエンド固有の ID / 参照（必須）
    "title":   "string",          # タイトル（必須）
    "summary": "string",          # 要約（任意）
    "tags":    ["string", ...],   # タグ / ラベル（任意。フォローアップ候補の生成に使う）
    "score":   0.0,               # 0..1 に正規化した関連度（必須）
    "related": ["string", ...],   # 他アイテムへの参照（任意。マルチホップの種）
    "text":    "string"           # 任意。gap 判定に使う全文。無ければ title+summary+tags で代用
  }

ライブラリとして使う:
  from hints import compute_hints
  hints = compute_hints(results, keywords)

CLI として使う（stdin に JSON、stdout に hints JSON）:
  echo '{"keywords": ["JWT","認証"], "results": [...]}' | python hints.py
  python hints.py --input results.json
"""

import argparse
import json
import sys

# 既定パラメータ（呼び出し側から上書き可能）
SUFFICIENT_SCORE = 0.5      # max_score がこの値以上かつ 1 件以上なら「十分な手がかりあり」
MIN_SUGGEST_SCORE = 0.15    # フォローアップ候補の抽出に使う結果の足切りスコア
MAX_SUGGESTED_QUERIES = 5   # 提示するフォローアップクエリの最大数


def _as_list(value) -> list[str]:
    """tags / related を文字列リストに正規化する。"""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]
    return []


def _result_text(r: dict) -> str:
    """gap 判定に使う検索対象テキストを取り出す（text 優先、無ければ複合）。"""
    if r.get("text"):
        return str(r["text"])
    return " ".join([
        str(r.get("title", "")),
        str(r.get("summary", "")),
        " ".join(_as_list(r.get("tags"))),
    ])


def collect_related_ids(results: list[dict]) -> list[str]:
    """結果群から「まだ見ていない関連参照」を集める（マルチホップの種）。

    各アイテムの related のうち、結果集合の id に未出のものを順序保持で返す。
    参照が ID として辿れるか（fetch 可能か）はバックエンド固有なので呼び出し側が判断する。
    """
    result_ids = {str(r.get("id", "")) for r in results}
    related_ids: list[str] = []
    for r in results:
        for ref in _as_list(r.get("related")):
            if ref and ref not in result_ids and ref not in related_ids:
                related_ids.append(ref)
    return related_ids


def suggest_followup_queries(results: list[dict], keywords: list[str],
                             min_score: float = MIN_SUGGEST_SCORE,
                             max_queries: int = MAX_SUGGESTED_QUERIES) -> list[str]:
    """上位結果のタグから「次に検索すべきクエリ」を提案する（クエリ再構成の手がかり）。

    元クエリに含まれないタグを頻度順に並べ、元クエリを絞り込む refine 候補を生成する。
    """
    query_lower = {k.lower() for k in keywords}
    tag_freq: dict[str, int] = {}
    for r in results:
        if float(r.get("score", 0.0)) < min_score:
            continue
        for tag in _as_list(r.get("tags")):
            tl = tag.lower()
            if tl in query_lower:
                continue
            tag_freq[tag] = tag_freq.get(tag, 0) + 1

    ranked = sorted(tag_freq.items(), key=lambda kv: kv[1], reverse=True)
    base = " ".join(keywords).strip()
    suggestions: list[str] = []
    for tag, _freq in ranked:
        suggestion = f"{base} {tag}".strip() if base else tag
        if suggestion not in suggestions:
            suggestions.append(suggestion)
        if len(suggestions) >= max_queries:
            break
    return suggestions


def gap_keywords(keywords: list[str], results: list[dict]) -> list[str]:
    """どの結果にもヒットしなかったクエリ語を返す（再構成が必要なシグナル）。"""
    texts = [_result_text(r).lower() for r in results]
    gaps: list[str] = []
    for kw in keywords:
        kl = kw.lower()
        if not any(kl in t for t in texts):
            gaps.append(kw)
    return gaps


def compute_hints(results: list[dict], keywords: list[str],
                  sufficient_score: float = SUFFICIENT_SCORE) -> dict:
    """agentic search の「次の一手」ヒントを構築する。

    Args:
        results:  正規化済み結果リスト（score 降順を推奨。順序は max_score 計算に非依存）
        keywords: 検索に使ったキーワード（スペース区切りを分割したもの）
        sufficient_score: 十分性判定のスコア閾値

    Returns:
        {sufficient, max_score, result_count, next_action,
         suggested_queries, related_ids, gap_keywords}
    """
    count = len(results)
    max_score = max((float(r.get("score", 0.0)) for r in results), default=0.0)
    related_ids = collect_related_ids(results)
    gaps = gap_keywords(keywords, results)
    suggested = suggest_followup_queries(results, keywords)
    sufficient = count > 0 and max_score >= sufficient_score

    # 推奨される次アクションを決定する
    if count == 0:
        next_action = "broaden"      # 0件 → 語を減らす / 同義語で広げる
    elif max_score < sufficient_score:
        next_action = "refine"       # 弱一致のみ → suggested_queries で再構成
    elif related_ids:
        next_action = "expand"       # 十分だが関連あり → 関連参照を辿りマルチホップ
    else:
        next_action = "synthesize"   # 十分 → 反復終了して結果を統合

    return {
        "sufficient": sufficient,
        "max_score": round(max_score, 3),
        "result_count": count,
        "next_action": next_action,
        "suggested_queries": suggested,
        "related_ids": related_ids,
        "gap_keywords": gaps,
    }


def format_hints(hints: dict) -> str:
    """ヒントを人間可読に整形する（各検索スキルの --suggest 表示で再利用）。"""
    action_label = {
        "broaden": "broaden（語を減らす／同義語で広げる）",
        "refine": "refine（suggested_queries でクエリを再構成して再検索）",
        "expand": "expand（related_ids を辿りマルチホップ展開）",
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
        lines.append(f"  related_ids (辿って再検索可): {', '.join(hints['related_ids'])}")
    if hints["gap_keywords"]:
        lines.append(f"  gap_keywords (未ヒット): {', '.join(hints['gap_keywords'])}")
    return "\n".join(lines)


def _parse_keywords(payload: dict) -> list[str]:
    if isinstance(payload.get("keywords"), list):
        return [str(k) for k in payload["keywords"]]
    return str(payload.get("query", "")).split()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="正規化済み検索結果から agentic search ヒントを計算する")
    parser.add_argument("--input", help="入力 JSON ファイル（省略時は stdin）")
    parser.add_argument("--sufficient-score", type=float, default=SUFFICIENT_SCORE,
                        help=f"十分性判定の閾値（default: {SUFFICIENT_SCORE}）")
    parser.add_argument("--text", action="store_true",
                        help="JSON ではなく人間可読な形式で出力する")
    args = parser.parse_args()

    raw = open(args.input, encoding="utf-8").read() if args.input else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"入力 JSON の解析に失敗しました: {e}", file=sys.stderr)
        return 1

    results = payload.get("results", [])
    keywords = _parse_keywords(payload)
    hints = compute_hints(results, keywords, sufficient_score=args.sufficient_score)

    if args.text:
        print(format_hints(hints))
    else:
        print(json.dumps(hints, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
