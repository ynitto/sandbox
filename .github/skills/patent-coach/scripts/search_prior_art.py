#!/usr/bin/env python3
"""
先行技術検索スクリプト

Google Patents の検索 URL を生成し、検索結果ページの取得を試みる。
取得に失敗した場合は URL のみを出力する（エージェントが fetch_webpage で取得可能）。

使用例:
  python search_prior_art.py --keywords "機械学習" "異常検知" --lang ja
  python search_prior_art.py --keywords "machine learning" "anomaly detection" --lang en
  python search_prior_art.py --keywords "強化学習" "ロボット制御" --lang ja --ipc G06N
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
import re
from html import unescape
from typing import Optional


def build_google_patents_url(
    keywords: list[str],
    lang: str = "ja",
    ipc: Optional[str] = None,
    num_results: int = 10,
) -> str:
    """Google Patents の検索 URL を構築する。"""
    query_parts = keywords[:]
    if ipc:
        query_parts.append(f"({ipc})")

    params = {
        "q": " ".join(query_parts),
        "num": str(num_results),
        "oq": " ".join(keywords),
    }

    if lang == "ja":
        params["country"] = "JP"
        params["language"] = "JAPANESE"
    elif lang == "en":
        params["language"] = "ENGLISH"

    return "https://patents.google.com/?" + urllib.parse.urlencode(params)


def build_jplatpat_url(keywords: list[str]) -> str:
    """J-PlatPat の全文検索 URL を構築する（ブラウザでの手動検索用）。"""
    # J-PlatPat は JS ベースのため直接的な検索 URL は限定的
    # 特許・実用新案テキスト検索の入口 URL を提供する
    return "https://www.j-platpat.inpit.go.jp/p0200"


def try_fetch_google_patents(url: str) -> Optional[str]:
    """Google Patents の検索結果ページを取得する。失敗時は None を返す。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[INFO] 直接取得に失敗しました: {e}", file=sys.stderr)
        print("[INFO] 出力された URL を fetch_webpage ツールで取得してください。", file=sys.stderr)
        return None


def extract_patents_from_html(html: str) -> list[dict]:
    """Google Patents の検索結果 HTML から特許情報を抽出する。"""
    results = []

    # Google Patents の検索結果は <search-result-item> タグ内にある
    # または state.results JSON 内にある
    # 簡易的にタイトルと特許番号を正規表現で抽出する

    # パターン1: <article> 内の特許情報
    patent_pattern = re.compile(
        r'<a[^>]*href="[^"]*patent/([A-Z]{2}\d+[A-Z]?\d*)"[^>]*>.*?'
        r'<span[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</span>',
        re.DOTALL,
    )
    for match in patent_pattern.finditer(html):
        patent_id = match.group(1)
        title = unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
        if title and patent_id:
            results.append({"patent_id": patent_id, "title": title})

    # パターン2: data-result 属性から抽出
    if not results:
        id_pattern = re.compile(r'data-result="([A-Z]{2}[\d]+[A-Z]?\d*)"')
        title_pattern = re.compile(
            r'<h3[^>]*>(.*?)</h3>|<span[^>]*id="htmlContent"[^>]*>(.*?)</span>',
            re.DOTALL,
        )
        ids = id_pattern.findall(html)
        titles = title_pattern.findall(html)
        for i, pid in enumerate(ids):
            t = ""
            if i < len(titles):
                t = titles[i][0] or titles[i][1]
                t = unescape(re.sub(r"<[^>]+>", "", t)).strip()
            results.append({"patent_id": pid, "title": t})

    return results[:10]


def main():
    parser = argparse.ArgumentParser(
        description="先行技術検索: Google Patents / J-PlatPat の検索 URL 生成と結果取得"
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        required=True,
        help="検索キーワード（スペース区切りで複数指定可）",
    )
    parser.add_argument(
        "--lang",
        choices=["ja", "en"],
        default="ja",
        help="検索言語 (ja: 日本語特許, en: 英語特許)",
    )
    parser.add_argument(
        "--ipc",
        default=None,
        help="IPC分類コード（例: G06F, G06N）で絞り込む",
    )
    parser.add_argument(
        "--num",
        type=int,
        default=10,
        help="取得件数（デフォルト: 10）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON 形式で出力する",
    )
    args = parser.parse_args()

    # --- URL 生成 ---
    google_url = build_google_patents_url(
        args.keywords, lang=args.lang, ipc=args.ipc, num_results=args.num
    )
    jplatpat_url = build_jplatpat_url(args.keywords)

    output = {
        "keywords": args.keywords,
        "lang": args.lang,
        "ipc": args.ipc,
        "urls": {
            "google_patents": google_url,
            "jplatpat": jplatpat_url,
        },
        "jplatpat_search_terms": " AND ".join(args.keywords),
        "patents": [],
        "fetch_status": "not_attempted",
    }

    # --- Google Patents から直接取得を試みる ---
    html = try_fetch_google_patents(google_url)
    if html:
        patents = extract_patents_from_html(html)
        output["patents"] = patents
        output["fetch_status"] = "success" if patents else "parsed_but_empty"

        if not patents:
            print(
                "[INFO] HTML は取得できましたがパース結果が空です。"
                "fetch_webpage ツールで URL を取得し直してください。",
                file=sys.stderr,
            )
    else:
        output["fetch_status"] = "failed"

    # --- 出力 ---
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print("先行技術検索結果")
        print("=" * 60)
        print(f"キーワード: {', '.join(args.keywords)}")
        print(f"言語: {'日本語' if args.lang == 'ja' else '英語'}")
        if args.ipc:
            print(f"IPC: {args.ipc}")
        print()
        print(f"[Google Patents]  {google_url}")
        print(f"[J-PlatPat]       {jplatpat_url}")
        print(f"  検索語: {output['jplatpat_search_terms']}")
        print()

        if output["patents"]:
            print(f"--- 検索結果 ({len(output['patents'])}件) ---")
            for i, p in enumerate(output["patents"], 1):
                print(f"  {i}. {p['patent_id']}")
                if p["title"]:
                    print(f"     {p['title']}")
                detail_url = f"https://patents.google.com/patent/{p['patent_id']}"
                print(f"     {detail_url}")
                print()
        else:
            print("--- 検索結果の自動取得に失敗しました ---")
            print("上記 URL を fetch_webpage ツールで開いて結果を確認してください。")
            print()

        print("=" * 60)
        print("※ この調査は予備的なものです。正式な先行技術調査は弁理士にご依頼ください。")


if __name__ == "__main__":
    main()
