#!/usr/bin/env python3
"""
discover.py — フィード自動発見

articles.json の記事説明文・リンクから外部ドメインを抽出し、
RSS/Atom フィードが存在するか試行する。
候補フィードを skill-registry.json の candidate_feeds セクションに追記する。

Usage:
  python discover.py --articles articles.json
  python discover.py --articles articles.json --top-domains 20 --timeout 8
  python discover.py show
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / "skill-registry.json"

_RSS_PATHS = [
    "/feed",
    "/feed.xml",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/rss/",
    "/atom.xml",
    "/index.xml",
    "/feeds/posts/default",
    "/blog/feed",
    "/blog/rss.xml",
    "/blog/feed.xml",
]

_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Domains to skip (already common, search engines, social media, CDN, etc.)
_SKIP_DOMAINS = {
    "github.com", "github.io", "twitter.com", "x.com", "youtube.com",
    "linkedin.com", "facebook.com", "instagram.com", "reddit.com",
    "google.com", "googleapis.com", "gstatic.com", "stackoverflow.com",
    "wikipedia.org", "wikimedia.org", "medium.com", "substack.com",
    "amazon.com", "amazonaws.com", "cloudfront.net", "cdn.net",
    "t.co", "bit.ly", "tinyurl.com", "ow.ly",
    "zenn.dev", "qiita.com", "dev.to", "note.com", "hatena.ne.jp",
    "b.hatena.ne.jp", "hatenablog.com",
}


def load_registry(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _get_config(registry: dict) -> dict:
    return registry.setdefault("skill_configs", {}).setdefault("tech-harvester", {})


def _extract_urls(text: str) -> list[str]:
    return re.findall(r'https?://[^\s\'"<>)]+', text)


def _normalize_domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.removeprefix("www.")
    except Exception:
        return ""


def _get_registered_domains(config: dict) -> set[str]:
    domains = set()
    for feed in config.get("feeds", []):
        d = _normalize_domain(feed.get("url", ""))
        if d:
            domains.add(d)
    for c in config.get("candidate_feeds", []):
        d = _normalize_domain(c.get("url", ""))
        if d:
            domains.add(d)
    return domains


def _is_rss(raw: bytes) -> bool:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False
    tag = root.tag.lower()
    if "rss" in tag:
        return True
    if _ATOM_NS.lower() in tag or "feed" in tag:
        return True
    if root.find("channel") is not None:
        return True
    if root.find(f"{_ATOM_NS}entry") is not None:
        return True
    return False


def _feed_title(raw: bytes) -> str:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return ""
    # RSS 2.0
    ch = root.find("channel")
    if ch is not None:
        t = ch.find("title")
        if t is not None and t.text:
            return t.text.strip()
    # Atom
    t = root.find(f"{_ATOM_NS}title")
    if t is not None and t.text:
        return t.text.strip()
    return ""


def _guess_lang(domain: str, sample_url: str) -> str:
    jp_tlds = {".jp", ".co.jp", ".ne.jp", ".or.jp"}
    if any(domain.endswith(tld) for tld in jp_tlds):
        return "ja"
    # rough heuristic: Japanese tech blog patterns
    jp_keywords = ["japan", "japanese", "hatenablog", "livedoor"]
    if any(kw in domain for kw in jp_keywords):
        return "ja"
    return "en"


def _suggest_tags(domain: str, title: str) -> list[str]:
    tags = []
    title_lower = title.lower()
    domain_lower = domain.lower()
    if any(kw in domain_lower for kw in [".jp", "japan", "japanese", "hatenablog"]):
        tags.append("japanese")
    if any(kw in title_lower + domain_lower for kw in ["tech", "engineering", "developer", "dev", "code"]):
        tags.append("tech")
    if any(kw in title_lower + domain_lower for kw in ["blog", "diary", "journal"]):
        tags.append("blog")
    if any(kw in title_lower + domain_lower for kw in ["aws", "cloud", "azure", "gcp"]):
        tags.append("cloud")
    if any(kw in title_lower + domain_lower for kw in ["ai", "ml", "machine learning", "llm"]):
        tags.append("ai")
    if not tags:
        tags = ["tech"]
    return tags


def try_discover_feed(domain: str, source_url: str, timeout: int) -> dict | None:
    for path in _RSS_PATHS:
        url = f"https://{domain}{path}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tech-harvester-discover/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(65536)  # Read up to 64KB
        except (urllib.error.URLError, OSError):
            continue

        if _is_rss(raw):
            title = _feed_title(raw) or domain
            lang = _guess_lang(domain, title)
            return {
                "name": title,
                "url": url,
                "lang": lang,
                "suggested_tags": _suggest_tags(domain, title),
            }
    return None


def discover(
    articles_path: Path,
    registry_path: Path,
    top_domains: int,
    timeout: int,
) -> list[dict]:
    data = json.loads(articles_path.read_text(encoding="utf-8"))
    articles = data.get("articles", [])

    # Collect all URLs from descriptions and article links
    domain_to_sources: dict[str, list[str]] = {}
    for article in articles:
        sources = []
        for url in _extract_urls(article.get("description", "")):
            sources.append(url)
        sources.append(article.get("link", ""))

        for url in sources:
            domain = _normalize_domain(url)
            if domain:
                domain_to_sources.setdefault(domain, []).append(article.get("link", url))

    # Count domain frequency
    domain_counts = Counter({d: len(srcs) for d, srcs in domain_to_sources.items()})

    registry = load_registry(registry_path)
    config = _get_config(registry)
    registered = _get_registered_domains(config)
    skipped = _SKIP_DOMAINS | registered

    # Try top N unregistered domains
    candidates_map: dict[str, dict] = {
        c["url"]: c for c in config.get("candidate_feeds", [])
    }
    newly_found: list[dict] = []

    checked = 0
    for domain, count in domain_counts.most_common(top_domains * 3):
        if checked >= top_domains:
            break
        if domain in skipped or not domain:
            continue
        checked += 1
        print(f"  探索中: {domain} (参照数: {count}) …", file=sys.stderr)
        result = try_discover_feed(domain, domain_to_sources[domain][0], timeout)
        if result:
            feed_url = result["url"]
            sources = list(set(domain_to_sources[domain]))[:5]
            if feed_url in candidates_map:
                # Update existing candidate
                candidates_map[feed_url]["discovery_count"] = (
                    candidates_map[feed_url].get("discovery_count", 0) + count
                )
                for s in sources:
                    if s not in candidates_map[feed_url].get("discovered_from", []):
                        candidates_map[feed_url].setdefault("discovered_from", []).append(s)
            else:
                new_candidate = {
                    **result,
                    "discovered_from": sources,
                    "discovery_count": count,
                    "relevance_score": 0.0,
                    "status": "pending",
                }
                candidates_map[feed_url] = new_candidate
                newly_found.append(new_candidate)
            print(f"    ✓ フィード発見: {result['name']} ({feed_url})", file=sys.stderr)

    config["candidate_feeds"] = list(candidates_map.values())
    save_registry(registry_path, registry)
    return newly_found


def show(registry_path: Path) -> None:
    registry = load_registry(registry_path)
    config = _get_config(registry)
    candidates = config.get("candidate_feeds", [])
    if not candidates:
        print("candidate_feeds はまだありません。'discover.py --articles FILE' で探索してください。")
        return

    pending = [c for c in candidates if c.get("status") == "pending"]
    promoted = [c for c in candidates if c.get("status") == "promoted"]

    print(f"候補フィード: {len(pending)} 件（保留中）, {len(promoted)} 件（昇格済み）\n")
    print(f"{'フィード名':<35} {'発見数':>5} {'関連性':>6} {'ステータス':<10} URL")
    print("-" * 90)
    for c in sorted(candidates, key=lambda x: x.get("discovery_count", 0), reverse=True):
        print(
            f"{c['name']:<35} {c.get('discovery_count', 0):>5}"
            f" {c.get('relevance_score', 0.0):>6.1f}"
            f" {c.get('status', 'pending'):<10} {c['url']}"
        )


def main():
    parser = argparse.ArgumentParser(description="フィード自動発見")
    parser.add_argument("--registry", default=str(REGISTRY_PATH), metavar="FILE")
    sub = parser.add_subparsers(dest="cmd")

    disc = sub.add_parser("discover", help="articles.json からフィードを発見（デフォルト動作）")
    disc.add_argument("--articles", required=True, metavar="FILE")
    disc.add_argument("--top-domains", type=int, default=15, metavar="N", help="調査するドメイン上位数 (デフォルト: 15)")
    disc.add_argument("--timeout", type=int, default=8, metavar="SEC", help="フィード確認タイムアウト秒数 (デフォルト: 8)")

    sub.add_parser("show", help="候補フィード一覧を表示")

    # 短縮形: --articles を直接受け取る
    parser.add_argument("--articles", default="", metavar="FILE")
    parser.add_argument("--top-domains", type=int, default=15, metavar="N")
    parser.add_argument("--timeout", type=int, default=8, metavar="SEC")

    args = parser.parse_args()
    registry_path = Path(args.registry)

    if args.cmd == "show" or (not args.cmd and not args.articles):
        show(registry_path)
        return

    articles_file = args.articles if not args.cmd else args.articles
    top_domains = args.top_domains
    timeout = args.timeout

    if not articles_file:
        parser.print_help()
        sys.exit(1)

    print(f"フィード発見を開始します …", file=sys.stderr)
    found = discover(Path(articles_file), registry_path, top_domains, timeout)

    if found:
        print(f"\n## 新たに発見したフィード候補 ({len(found)} 件)")
        for c in found:
            print(f"- **{c['name']}** ({c['url']})")
            print(f"  発見数: {c['discovery_count']}, タグ候補: {', '.join(c.get('suggested_tags', []))}")
        print("\n候補は skill-registry.json の candidate_feeds に追記されました。")
        print("関連性スコアを確認後、'evolve_feeds.py' で昇格させてください。")
    else:
        print("\n新しいフィード候補は見つかりませんでした。")


if __name__ == "__main__":
    main()
