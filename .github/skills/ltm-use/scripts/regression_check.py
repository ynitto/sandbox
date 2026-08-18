#!/usr/bin/env python3
from __future__ import annotations
"""
regression_check.py - 整理（consolidate/cleanup）で検索できなくなった記憶を検知する

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.5-4
（回帰ゲート）。「整理したら引けなくなった」を黙って通さない。

考え方は自己想起の一貫性チェック——「整理の前に引けていた記憶が、整理の後も
（統合されていれば統合先が）引けるか」だけを見る。一度も引かれていない記憶
（access_count=0）は退役候補であってこの対象ではない（K0/K1 の既定と同じ判定軸）。

  1. snapshot: 整理の前に、access_count>=1 の記憶が自分のタイトルで自分自身を
     引けるかを記録する（=「前は引けていた」の証拠を作る）。
  2. （consolidate / cleanup を実行）
  3. compare: snapshot の記憶が今も引けるかを確認する。consolidate で
     status=archived + consolidated_to になった記憶は、統合先が同じクエリで
     引ければ回帰ではない（連鎖を辿る）。ファイルごと消えていれば cleanup による
     意図的な削除の可能性があるが、access_count>=1（=前は引けていた）だったものが
     消えた以上ここでは「要確認」として報告する（自動では判定しない）。
  4. revert: consolidate が原因の回帰（archived 側を戻せば直る）だけを機械的に
     差し戻す。削除（cleanup）が原因のものは復元できないので報告のみ（当番の
     判断に委ねる。判断に迷えば実行しない、の原則どおり自動では触らない）。

Usage:
  python regression_check.py snapshot --out /tmp/ltm-regression-baseline.json
  # ... consolidate_memory.py / cleanup_memory.py を実行 ...
  python regression_check.py compare --baseline /tmp/ltm-regression-baseline.json
  python regression_check.py revert --baseline /tmp/ltm-regression-baseline.json
"""

import argparse
import json
import os
import sys

import memory_utils
from recall_memory import search_with_index

DEFAULT_LIMIT = 5
DEFAULT_MIN_ACCESS = 1


def _search_top_ids(memory_dir: str, title: str, limit: int) -> list[str]:
    keywords = str(title or "").split()
    if not keywords:
        return []
    results = search_with_index(memory_dir, keywords, status_filter=None, limit=limit)
    return [r["entry"].get("id", "") for r in results]


def build_snapshot(memory_dir: str, min_access: int = DEFAULT_MIN_ACCESS,
                   limit: int = DEFAULT_LIMIT) -> dict:
    """access_count>=min_access の active な記憶が、自分のタイトルで自分自身を
    top-N に引けるかを記録する（『整理前に引けていた』の基準線）。"""
    # search_with_index は索引が空でない限り古い索引を使い回す。呼び出し元（consolidate/
    # cleanup）が索引を更新し忘れていても回帰ゲートが陳腐化した索引で判定しないよう、
    # ここで明示的に更新する（増分・mtime 比較なので安い）。
    memory_utils.refresh_index(memory_dir)
    baseline: dict = {}
    for fpath, rel_cat in memory_utils.iter_memory_files(memory_dir):
        try:
            with open(fpath, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        meta, _ = memory_utils.parse_frontmatter(text)
        mem_id = str(meta.get("id") or "")
        if not mem_id or meta.get("status", "active") != "active":
            continue
        access = int(meta.get("access_count", 0) or 0)
        if access < min_access:
            continue
        title = meta.get("title", "")
        top_ids = _search_top_ids(memory_dir, title, limit)
        baseline[mem_id] = {"title": title, "category": rel_cat,
                            "found": mem_id in top_ids}
    return baseline


def _load_id_map(memory_dir: str) -> dict:
    """id → {filepath, status, consolidated_to} の全件マップ（索引の鮮度に依存しない）。"""
    id_map: dict = {}
    for fpath, _rel in memory_utils.iter_memory_files(memory_dir):
        try:
            with open(fpath, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        meta, _ = memory_utils.parse_frontmatter(text)
        mem_id = str(meta.get("id") or "")
        if mem_id:
            id_map[mem_id] = {"filepath": fpath,
                              "status": meta.get("status", "active"),
                              "consolidated_to": str(meta.get("consolidated_to") or "")}
    return id_map


def _resolve_target(id_map: dict, mem_id: str, max_hops: int = 5) -> "str | None":
    """consolidated_to の連鎖を辿って現在の実体 ID を返す。ファイルが無ければ None（削除済み）。"""
    seen: set = set()
    current = mem_id
    for _ in range(max_hops):
        entry = id_map.get(current)
        if entry is None:
            return None
        nxt = entry["consolidated_to"]
        if not nxt or nxt == current or nxt in seen:
            return current
        seen.add(current)
        current = nxt
    return current


def compare(memory_dir: str, baseline: dict, limit: int = DEFAULT_LIMIT) -> dict:
    """snapshot の記憶が今も（統合先を含め）引けるかを確認する。"""
    memory_utils.refresh_index(memory_dir)   # snapshot と同じ理由（陳腐化した索引で判定しない）
    id_map = _load_id_map(memory_dir)
    regressions: list = []
    checked = 0
    for mem_id, info in baseline.items():
        if not info.get("found"):
            continue                              # 元々引けていなかったものは対象外
        checked += 1
        target = _resolve_target(id_map, mem_id)
        if target is None:
            regressions.append({"mem_id": mem_id, "title": info.get("title", ""),
                                "kind": "deleted", "resolved_to": None})
            continue
        top_ids = _search_top_ids(memory_dir, info.get("title", ""), limit)
        if target not in top_ids:
            regressions.append({"mem_id": mem_id, "title": info.get("title", ""),
                                "kind": "unfindable", "resolved_to": target})
    return {"checked": checked, "regressions": regressions}


def revert(memory_dir: str, regressions: list) -> dict:
    """consolidate が原因の回帰（archived 側を active へ戻せば直る）だけを機械的に差し戻す。

    削除（cleanup）が原因のもの・統合が絡まないもの（索引の陳腐化等）は復元できない/
    差し戻す対象が無いため報告のみに留める（当番の判断に委ねる）。
    """
    id_map = _load_id_map(memory_dir)
    reverted: list = []
    unrevertable: list = []
    for r in regressions:
        mem_id, target = r["mem_id"], r.get("resolved_to")
        if r["kind"] != "unfindable" or not target or target == mem_id:
            unrevertable.append(r)
            continue
        entry = id_map.get(mem_id)
        if not entry or entry["status"] != "archived":
            unrevertable.append(r)
            continue
        memory_utils.update_frontmatter_fields(
            entry["filepath"], {"status": "active", "consolidated_to": ""})
        memory_utils.update_index_entry(memory_dir, entry["filepath"])
        reverted.append(r)
    return {"reverted": reverted, "unrevertable": unrevertable}


def _print_regressions(result: dict) -> None:
    regressions = result["regressions"]
    print(f"確認: {result['checked']}件（access_count>=1 で整理前に引けていた記憶）")
    if not regressions:
        print("回帰なし: 整理前に引けていた記憶は、統合先も含め今も引けます。")
        return
    print(f"回帰候補: {len(regressions)}件")
    for r in regressions:
        if r["kind"] == "deleted":
            print(f"  [削除] {r['title']}（{r['mem_id']}）— ファイルが見つかりません。"
                 "cleanup による意図的な削除か要確認")
        else:
            print(f"  [検索不能] {r['title']}（{r['mem_id']} → {r['resolved_to']}）"
                 "— 統合/整理の後、同じ問いで引けなくなりました")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="整理（consolidate/cleanup）で検索できなくなった記憶を検知する")
    parser.add_argument("--scope", default="home", choices=["home"],
                       help="対象スコープ (default: home)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="整理前の基準線を記録する")
    p_snap.add_argument("--out", required=True, help="基準線の保存先（JSON）")
    p_snap.add_argument("--min-access", type=int, default=DEFAULT_MIN_ACCESS,
                        help="対象にする最小 access_count (default: 1)")
    p_snap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="top-N 判定の N (default: 5)")

    p_cmp = sub.add_parser("compare", help="基準線と現在を比較して回帰を報告する")
    p_cmp.add_argument("--baseline", required=True, help="snapshot の出力ファイル")
    p_cmp.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p_cmp.add_argument("--json", action="store_true", help="機械可読な JSON で出力")
    p_cmp.add_argument("--out", help="比較結果も JSON で保存する（当番が revert に渡す用）")

    p_rev = sub.add_parser("revert", help="consolidate が原因の回帰だけを差し戻す")
    p_rev.add_argument("--baseline", help="snapshot の出力ファイル（--regressions と排他）")
    p_rev.add_argument("--regressions", help="compare --out で保存した比較結果")
    p_rev.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p_rev.add_argument("--json", action="store_true", help="機械可読な JSON で出力")

    args = parser.parse_args()
    memory_dir = memory_utils.get_memory_dir(args.scope)

    if args.command == "snapshot":
        baseline = build_snapshot(memory_dir, args.min_access, args.limit)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(baseline, f, ensure_ascii=False, indent=2)
        found = sum(1 for v in baseline.values() if v["found"])
        print(f"[OK] 基準線を記録しました: {args.out}（{found}/{len(baseline)} 件が現在引ける）")
        return 0

    if args.command == "compare":
        with open(args.baseline, encoding="utf-8") as f:
            baseline = json.load(f)
        result = compare(memory_dir, baseline, args.limit)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_regressions(result)
        return 2 if result["regressions"] else 0

    if args.command == "revert":
        if bool(args.baseline) == bool(args.regressions):
            print("[ERROR] --baseline か --regressions のどちらか一方を指定してください",
                 file=sys.stderr)
            return 2
        if args.regressions:
            with open(args.regressions, encoding="utf-8") as f:
                result = json.load(f)
            regressions = result["regressions"]
        else:
            with open(args.baseline, encoding="utf-8") as f:
                baseline = json.load(f)
            regressions = compare(memory_dir, baseline, args.limit)["regressions"]
        outcome = revert(memory_dir, regressions)
        if args.json:
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
        else:
            print(f"差し戻し: {len(outcome['reverted'])}件"
                 f" / 差し戻せない（報告のみ）: {len(outcome['unrevertable'])}件")
            for r in outcome["unrevertable"]:
                print(f"  [要確認] {r['title']}（{r['mem_id']}・{r['kind']}）")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
