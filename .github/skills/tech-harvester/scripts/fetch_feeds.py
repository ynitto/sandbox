#!/usr/bin/env python3
"""
Fetch IT RSS feeds listed in skill-registry.json and output a Markdown digest.

Usage:
    python fetch_feeds.py [--max-items N] [--tags TAG1,TAG2] [--lang ja|en]

Options:
    --max-items N      Max articles per feed (default: 5)
    --tags TAG1,TAG2   Comma-separated tags to filter feeds (optional)
    --lang ja|en       Filter feeds by language (optional)
    --output FILE      Write Markdown to file instead of stdout
"""

import argparse
import json
import sys
import textwrap
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

REGISTRY_PATH = Path(__file__).parent / "skill-registry.json"

# XML namespace map used by Atom feeds
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"


def load_registry(tags: list[str] | None, lang: str | None) -> list[dict]:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    feeds = data["feeds"]
    if lang:
        feeds = [f for f in feeds if f.get("lang") == lang]
    if tags:
        feeds = [f for f in feeds if set(tags) & set(f.get("tags", []))]
    return feeds


def _text(element) -> str:
    """Return stripped text content of an XML element, or empty string."""
    if element is None:
        return ""
    return (element.text or "").strip()


def _truncate(text: str, max_chars: int = 200) -> str:
    text = " ".join(text.split())  # collapse whitespace
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


def fetch_feed(url: str, max_items: int) -> list[dict]:
    """Fetch an RSS/Atom feed URL and return a list of article dicts."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tech-harvester/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        print(f"  [WARN] Failed to fetch {url}: {exc}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        print(f"  [WARN] Failed to parse {url}: {exc}", file=sys.stderr)
        return []

    articles = []

    # RSS 2.0
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item")[:max_items]:
            title = _text(item.find("title"))
            link = _text(item.find("link"))
            desc = _text(item.find("description"))
            pub = _text(item.find("pubDate"))
            if not title or not link:
                continue
            articles.append({"title": title, "link": link, "summary": _truncate(desc), "date": pub})
        return articles

    # Atom
    for entry in root.findall(f"{_ATOM_NS}entry")[:max_items]:
        title = _text(entry.find(f"{_ATOM_NS}title"))
        link_el = entry.find(f"{_ATOM_NS}link")
        link = link_el.get("href", "") if link_el is not None else ""
        summary_el = entry.find(f"{_ATOM_NS}summary") or entry.find(f"{_ATOM_NS}content")
        summary = _truncate(_text(summary_el))
        pub = _text(entry.find(f"{_ATOM_NS}updated") or entry.find(f"{_ATOM_NS}published"))
        if not title or not link:
            continue
        articles.append({"title": title, "link": link, "summary": summary, "date": pub})

    return articles


def build_markdown(feeds_with_articles: list[tuple[dict, list[dict]]], generated_at: str) -> str:
    lines = [
        "# IT Tech Digest",
        "",
        f"_Generated: {generated_at}_",
        "",
    ]

    for feed_meta, articles in feeds_with_articles:
        if not articles:
            continue
        lines += [f"## {feed_meta['name']}", ""]
        for art in articles:
            lines.append(f"### [{art['title']}]({art['link']})")
            if art.get("date"):
                lines.append(f"_{art['date']}_")
            if art.get("summary"):
                lines.append("")
                lines.append(art["summary"])
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Fetch IT RSS feeds and output a Markdown digest.")
    parser.add_argument("--max-items", type=int, default=5, metavar="N", help="Max articles per feed (default: 5)")
    parser.add_argument("--tags", default="", help="Comma-separated tags to filter feeds")
    parser.add_argument("--lang", default="", help="Filter feeds by language (ja/en)")
    parser.add_argument("--output", default="", help="Write Markdown to this file (default: stdout)")
    args = parser.parse_args()

    tag_list = [t.strip() for t in args.tags.split(",") if t.strip()] or None
    lang = args.lang.strip() or None

    feeds = load_registry(tag_list, lang)
    if not feeds:
        print("No feeds matched the given filters.", file=sys.stderr)
        sys.exit(1)

    results = []
    for feed_meta in feeds:
        print(f"Fetching {feed_meta['name']} …", file=sys.stderr)
        articles = fetch_feed(feed_meta["url"], args.max_items)
        results.append((feed_meta, articles))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = build_markdown(results, generated_at)

    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"Wrote digest to {args.output}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
