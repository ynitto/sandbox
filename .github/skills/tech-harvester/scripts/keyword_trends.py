#!/usr/bin/env python3
"""
keyword_trends.py — キーワード・トレンド分析

articles.json（fetch_feeds.py の出力）からトレンドキーワードを抽出し、
skill-registry.json の keyword_trends セクションを更新する。
ltm-use が利用可能であれば、週次トレンドサマリーを episodic 記憶として保存する。

Usage:
  python keyword_trends.py --articles articles.json
  python keyword_trends.py --articles articles.json --week 2026-W19
  python keyword_trends.py --articles articles.json --keep-weeks 8 --no-ltm
  python keyword_trends.py show
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / "skill-registry.json"
LTM_SAVE = Path(__file__).parent.parent.parent / "ltm-use" / "scripts" / "save_memory.py"

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "is", "was", "are", "were", "be", "been", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "can", "that", "this", "these", "those", "it", "its",
    "from", "as", "not", "no", "how", "what", "why", "when", "where", "who",
    "which", "we", "they", "he", "she", "you", "i", "my", "your", "our",
    "their", "new", "more", "all", "also", "up", "out", "about", "into",
    "than", "then", "if", "so", "now", "just", "get", "use", "used", "using",
    "via", "per", "vs", "etc", "ie", "eg", "de", "la", "le", "et", "une",
    "one", "two", "three", "first", "last", "next", "after", "before",
    "based", "build", "built", "make", "made", "way", "time", "year",
    "open", "source", "free", "public", "release", "released", "support",
    "add", "added", "fix", "fixed", "update", "updated", "change", "changed",
}


def _current_week() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year}-W{now.isocalendar()[1]:02d}"


def extract_keywords(texts: list[str], top_n: int = 30) -> list[str]:
    counter: Counter = Counter()
    for text in texts:
        # CamelCase / PascalCase tech names (weight: 3)
        for term in re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', text):
            counter[term] += 3
        # ALL-CAPS abbreviations 2+ chars (weight: 2)
        for term in re.findall(r'\b[A-Z]{2,}\b', text):
            if term not in {"I", "A"}:
                counter[term] += 2
        # Tech terms with hyphen/dot: "GPT-4", "Node.js", "next.js" (weight: 3)
        for term in re.findall(r'\b[A-Za-z][A-Za-z0-9]*[-\.][A-Za-z0-9]+(?:[-\.][A-Za-z0-9]+)*\b', text):
            counter[term] += 3
        # Version patterns: "Python 3.12", "v2.0" (weight: 2)
        for term in re.findall(r'\bv?\d+\.\d+(?:\.\d+)?\b', text):
            counter[term] += 2
        # Regular English words (weight: 1, filter stopwords)
        for word in re.findall(r'\b[a-zA-Z]{3,}\b', text):
            if word.lower() not in _STOPWORDS and not word.isupper():
                counter[word.lower()] += 1

    return [term for term, _ in counter.most_common(top_n)]


def classify_keywords(
    current: list[str],
    prev_rising: list[str],
    prev_stable: list[str],
) -> dict:
    current_set = set(current)
    prev_top = set(prev_rising) | set(prev_stable)

    rising = [k for k in current if k not in prev_top]
    stable = [k for k in current if k in prev_top]
    declining = [k for k in (prev_rising + prev_stable) if k not in current_set]

    return {
        "rising": rising[:15],
        "stable": stable[:15],
        "declining": declining[:10],
    }


def load_registry(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _get_trends(registry: dict) -> dict:
    return (
        registry
        .setdefault("skill_configs", {})
        .setdefault("tech-harvester", {})
        .setdefault("keyword_trends", {})
    )


def _save_ltm(week: str, trends: dict, article_count: int, feed_count: int) -> None:
    if not LTM_SAVE.exists():
        print("[keyword_trends] ltm-use が見つかりません。記憶保存をスキップします。", file=sys.stderr)
        return

    rising = ", ".join(trends["rising"][:10]) or "（なし）"
    stable = ", ".join(trends["stable"][:8]) or "（なし）"
    declining = ", ".join(trends["declining"][:5]) or "（なし）"

    content = f"""\
## tech-harvester キーワードトレンド: {week}

### 上昇中キーワード
{rising}

### 安定キーワード
{stable}

### 下降キーワード
{declining}

フィード数: {feed_count}, 記事数: {article_count}
"""
    summary = (
        f"tech-harvester {week} のトレンド: 上昇={trends['rising'][:3]}, "
        f"安定={trends['stable'][:3]}"
    )

    try:
        result = subprocess.run(
            [
                sys.executable, str(LTM_SAVE),
                "--non-interactive", "--no-dedup", "--no-auto-tags",
                "--scope", "home",
                "--memory-type", "episodic",
                "--importance", "normal",
                "--category", "tech-trends",
                "--title", f"tech-harvester トレンド {week}",
                "--summary", summary,
                "--content", content,
                "--tags", "tech-harvester,keyword-trends,episodic",
                "--context", f"自動記録: keyword_trends.py by tech-harvester skill ({week})",
            ],
            capture_output=True,
            text=True,
            cwd=str(LTM_SAVE.parent),
            timeout=30,
        )
        if result.returncode == 0:
            print(f"[keyword_trends] ltm-use に記憶を保存しました。", file=sys.stderr)
        else:
            print(f"[keyword_trends] ltm-use 保存に失敗: {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[keyword_trends] ltm-use 保存をスキップ: {e}", file=sys.stderr)


def _recall_prev_trends(registry: dict, week: str) -> tuple[list[str], list[str]]:
    """直前の週のトレンドを registry から取得する。"""
    trends = _get_trends(registry)
    if not trends:
        return [], []

    # Sort weeks and find the one before the current
    weeks = sorted(trends.keys())
    if week in weeks:
        weeks = [w for w in weeks if w < week]
    if not weeks:
        return [], []

    prev = trends[weeks[-1]]
    return prev.get("rising", []), prev.get("stable", [])


def analyze(articles_path: Path, registry_path: Path, week: str, keep_weeks: int, use_ltm: bool) -> dict:
    data = json.loads(articles_path.read_text(encoding="utf-8"))
    articles = data.get("articles", [])

    texts = [f"{a.get('title', '')} {a.get('description', '')}" for a in articles]
    top_keywords = extract_keywords(texts, top_n=40)

    registry = load_registry(registry_path)
    prev_rising, prev_stable = _recall_prev_trends(registry, week)
    trends_by_week = _get_trends(registry)

    classified = classify_keywords(top_keywords, prev_rising, prev_stable)
    trends_by_week[week] = classified

    # Trim to keep_weeks most recent weeks
    all_weeks = sorted(trends_by_week.keys())
    if len(all_weeks) > keep_weeks:
        for old_week in all_weeks[: len(all_weeks) - keep_weeks]:
            del trends_by_week[old_week]

    save_registry(registry_path, registry)

    feed_names = {a["feed"] for a in articles}
    if use_ltm:
        _save_ltm(week, classified, len(articles), len(feed_names))

    return classified


def show(registry_path: Path) -> None:
    registry = load_registry(registry_path)
    trends = _get_trends(registry)
    if not trends:
        print("keyword_trends はまだありません。'keyword_trends.py --articles FILE' で分析してください。")
        return
    for week in sorted(trends.keys(), reverse=True):
        t = trends[week]
        print(f"\n## {week}")
        print(f"  上昇: {', '.join(t.get('rising', [])[:10])}")
        print(f"  安定: {', '.join(t.get('stable', [])[:8])}")
        print(f"  下降: {', '.join(t.get('declining', [])[:5])}")


def main():
    parser = argparse.ArgumentParser(description="キーワード・トレンド分析")
    parser.add_argument("--registry", default=str(REGISTRY_PATH), metavar="FILE")
    sub = parser.add_subparsers(dest="cmd")

    ana = sub.add_parser("analyze", help="articles.json からトレンドを分析（デフォルト動作）")
    ana.add_argument("--articles", required=True, metavar="FILE")
    ana.add_argument("--week", default="", help="週識別子 (例: 2026-W19)。省略時は今週")
    ana.add_argument("--keep-weeks", type=int, default=8, metavar="N", help="保持する週数 (デフォルト: 8)")
    ana.add_argument("--no-ltm", action="store_true", help="ltm-use への記憶保存をスキップ")

    sub.add_parser("show", help="保存済みキーワードトレンドを表示")

    # --articles を直接受け取る短縮形（サブコマンドなし）
    parser.add_argument("--articles", default="", metavar="FILE")
    parser.add_argument("--week", default="", help="週識別子")
    parser.add_argument("--keep-weeks", type=int, default=8, metavar="N")
    parser.add_argument("--no-ltm", action="store_true")

    args = parser.parse_args()
    registry_path = Path(args.registry)

    if args.cmd == "show" or (not args.cmd and not args.articles):
        show(registry_path)
        return

    articles_file = args.articles
    if args.cmd == "analyze":
        articles_file = args.articles
        week = args.week or _current_week()
        keep_weeks = args.keep_weeks
        use_ltm = not args.no_ltm
    else:
        week = args.week or _current_week()
        keep_weeks = args.keep_weeks
        use_ltm = not args.no_ltm

    if not articles_file:
        parser.print_help()
        sys.exit(1)

    result = analyze(Path(articles_file), registry_path, week, keep_weeks, use_ltm)

    print(f"\n## キーワードトレンド: {week}")
    print(f"上昇中: {', '.join(result['rising'][:10]) or '（なし）'}")
    print(f"安定:   {', '.join(result['stable'][:8]) or '（なし）'}")
    print(f"下降:   {', '.join(result['declining'][:5]) or '（なし）'}")


if __name__ == "__main__":
    main()
