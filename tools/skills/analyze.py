#!/usr/bin/env python3
"""
analyze.py — ltm-use の procedural 記憶を分析して進化候補を特定する。

使い方:
  python analyze.py                    # 全 procedural 記憶を分析
  python analyze.py --category testing # カテゴリ絞り込み
  python analyze.py --json             # JSON 出力

進化候補の判定基準:
  - correction_count >= 1  (修正・再指示を受けた)
  - user_rating < 0        (ユーザー評価がマイナス)
  - access_count >= 3 かつ share_score < 50  (よく使われるが評価が低い)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MEMORY_HOME = Path.home() / ".kiro/memory/home"
_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_fm(text: str) -> dict:
    m = _FM_RE.match(text)
    if not m:
        return {}
    result: dict = {}
    current_key = None
    for line in m.group(1).splitlines():
        if line.startswith("  - ") or line.startswith("- "):
            if current_key:
                result.setdefault(current_key, [])
                if isinstance(result[current_key], list):
                    result[current_key].append(line.lstrip("- ").strip())
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        current_key = k.strip()
        v = v.strip().strip('"')
        if v.startswith("[") and v.endswith("]"):
            result[current_key] = [x.strip().strip('"') for x in v[1:-1].split(",") if x.strip()]
        else:
            result[current_key] = v
    return result


def _load_procedural_memories(category: str | None = None) -> list[dict]:
    memories = []
    search_root = MEMORY_HOME / category if category else MEMORY_HOME
    if not search_root.exists():
        return []
    for md in search_root.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_fm(text)
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        if "procedural" not in tags:
            continue
        if fm.get("status") == "archived":
            continue
        memories.append({
            "path": str(md),
            "id": fm.get("id", ""),
            "title": fm.get("title", md.stem),
            "category": md.parent.name,
            "access_count": int(fm.get("access_count", 0)),
            "user_rating": int(fm.get("user_rating", 0)),
            "correction_count": int(fm.get("correction_count", 0)),
            "share_score": float(fm.get("share_score", 0)),
            "summary": fm.get("summary", ""),
            "body": text,
        })
    return memories


def _score_evolution_need(m: dict) -> tuple[float, list[str]]:
    """進化の必要性スコアと理由を返す。スコアが高いほど優先度が高い。"""
    score = 0.0
    reasons = []

    if m["correction_count"] >= 1:
        score += m["correction_count"] * 30
        reasons.append(f"修正・再指示を {m['correction_count']} 回受けた")

    if m["user_rating"] < 0:
        score += abs(m["user_rating"]) * 20
        reasons.append(f"ユーザー評価がマイナス ({m['user_rating']})")

    if m["access_count"] >= 3 and m["share_score"] < 50:
        score += 15
        reasons.append(f"よく参照される（{m['access_count']}回）が share_score が低い ({m['share_score']:.0f})")

    return score, reasons


def analyze(category: str | None = None, min_score: float = 10.0) -> list[dict]:
    memories = _load_procedural_memories(category)
    candidates = []
    for m in memories:
        score, reasons = _score_evolution_need(m)
        if score >= min_score:
            candidates.append({**m, "evolution_score": score, "reasons": reasons})
    candidates.sort(key=lambda x: x["evolution_score"], reverse=True)
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="procedural 記憶の進化候補を特定する")
    parser.add_argument("--category", help="カテゴリ絞り込み")
    parser.add_argument("--min-score", type=float, default=10.0, help="最低進化スコア（デフォルト: 10）")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    candidates = analyze(args.category, args.min_score)

    if args.as_json:
        print(json.dumps(
            [{k: v for k, v in c.items() if k != "body"} for c in candidates],
            ensure_ascii=False, indent=2,
        ))
        return

    if not candidates:
        print("進化候補はありません。")
        return

    print(f"進化候補: {len(candidates)} 件\n")
    for c in candidates:
        print(f"  [{c['category']}] {c['title']}")
        print(f"    スコア: {c['evolution_score']:.0f}  id: {c['id']}")
        for r in c["reasons"]:
            print(f"    - {r}")
        print()


if __name__ == "__main__":
    main()
