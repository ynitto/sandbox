"""運用: 状況の確認・保持期間の整理。"""
from __future__ import annotations

import json
import shutil
import sys
from typing import Any

from .config import Config
from .store import LEGACY_TABLES, Store
from .util import iso, now


def status(cfg: Config, store: Store) -> dict:
    out: dict[str, Any] = {"state_dir": str(store.dir), "storage": store.storage(), "sources": [], "consumers": []}
    for name, src in cfg.sources.items():
        row = store.source_row(name)
        last = store.one("SELECT * FROM fetches WHERE id=?", (row["last_fetch_id"],)) if row["last_fetch_id"] else None
        open_f = store.one("SELECT * FROM fetches WHERE source=? AND epoch=? AND status IN ('running','failed') "
                           "AND page_state IS NOT NULL ORDER BY id DESC LIMIT 1", (name, row["epoch"]))
        holder = store.lease_holder(f"fetch:{name}")
        out["sources"].append({
            "name": name,
            "last_success_at": iso(row["last_success_at"]),
            "last_attempt_at": iso(row["last_attempt_at"]),
            "last_status": row["last_status"] or "-",
            "last_error": row["last_error"],
            "consecutive_failures": row["consecutive_failures"],
            "last_fetch": None if last is None else {"id": last["id"], "mode": last["mode"], "pages": last["pages"],
                                                     "records": last["records"], "requests": last["requests"],
                                                     "not_modified_pages": last["not_modified"]},
            "position": json.loads(row["position"]) if row["position"] else None,
            "next_allowed_at": iso(row["next_allowed_at"]) if row["next_allowed_at"] and row["next_allowed_at"] > now()
            else None,
            "unfinished_fetch": None if open_f is None else {"id": open_f["id"], "pages_done": open_f["pages"],
                                                             "error": open_f["error"]},
            "fetching_now": None if holder is None else f"{holder['host']} pid {holder['pid']}",
        })
    for r in store.q("SELECT c.consumer, c.source, c.created_at, COUNT(cr.rkey) n, MAX(cr.updated_at) last "
                     "FROM consumers c LEFT JOIN consumer_records cr ON cr.consumer=c.consumer AND cr.source=c.source "
                     "GROUP BY c.consumer, c.source ORDER BY c.consumer, c.source"):
        out["consumers"].append({"consumer": r["consumer"], "source": r["source"], "records": r["n"],
                                 "last_commit_at": iso(r["last"]) if r["last"] else "-",
                                 "known_source": r["source"] in cfg.sources})
    return out


def print_status(st: dict, file=None) -> None:
    file = file or sys.stdout
    p = lambda s="": print(s, file=file)  # noqa: E731
    p(f"状態の保存先: {st['state_dir']}")
    sg = st["storage"]
    mb = lambda n: f"{n / 1048576:.1f} MB"  # noqa: E731
    p(f"  DB {mb(sg['db_bytes'])}（うち空き {mb(sg['free_bytes'])}）・WAL {mb(sg['wal_bytes'])}")
    if sg["auto_vacuum"] != "incremental":
        p("  この DB は消した領域を自動で返さない形です。gc --vacuum で一度作り直すと、以後は gc のたびに縮みます")
    p()
    p("取得元")
    for s in st["sources"]:
        lf = s["last_fetch"]
        line = f"  {s['name']}: 最後の成功 {s['last_success_at']}・結果 {s['last_status']}"
        if lf:
            line += f"・取得 #{lf['id']}（{lf['mode']}、{lf['pages']} ページ・{lf['records']} 件・要求 {lf['requests']} 回"
            line += f"・変更なし {lf['not_modified_pages']} ページ）" if lf["not_modified_pages"] else "）"
        p(line)
        if s["last_error"]:
            p(f"    エラー（連続 {s['consecutive_failures']} 回）: {s['last_error']}")
        if s["unfinished_fetch"]:
            u = s["unfinished_fetch"]
            p(f"    途中の取得 #{u['id']}（{u['pages_done']} ページ済み）。次の取得で続きから再開します")
        if s["next_allowed_at"]:
            p(f"    API に待つよう言われています: {s['next_allowed_at']} まで")
        if s["fetching_now"]:
            p(f"    取得中: {s['fetching_now']}")
        if s["position"] is not None:
            p(f"    増分の位置: {s['position']}")
    p()
    if st["consumers"]:
        p()
        p("consumer（get --consumer で変化を受け取るワークフロー）")
        for c in st["consumers"]:
            p(f"  {c['consumer']}（{c['source']}）: 処理し終えたレコード {c['records']} 件・最後の commit {c['last_commit_at']}"
              + ("" if c["known_source"] else "・設定に無い取得元"))


def gc(cfg: Config, store: Store, dry_run: bool = False, vacuum: bool = False) -> dict:
    """保持期間を過ぎたものを消し、空いた領域をファイルから返す。0.7 までのルールと受け渡しの表も消す。

    vacuum=True なら VACUUM で DB を作り直す（古い DB を自動で縮む形に切り替える。時間がかかる）。
    """
    t = now()
    rt = cfg.retention
    counts = {"fetches": 0, "pages_of_abandoned": 0, "http_cache": 0, "stale_records": 0, "freed_bytes": 0}
    fetch_cut = t - rt.fetch_days * 86400
    with store.tx() as db:
        for srow in db.execute("SELECT * FROM sources").fetchall():
            name = srow["name"]
            src = cfg.sources.get(name)
            epoch = srow["epoch"]
            keep = {r["id"] for r in db.execute(
                "SELECT id FROM fetches WHERE source=? AND epoch=? AND status='complete' ORDER BY id DESC LIMIT ?",
                (name, epoch, rt.fetches_keep))}
            resume_cut = t - (src.resume_max_age_sec if src else 0)
            for f in db.execute("SELECT id, status, epoch, started_at, finished_at, updated_at FROM fetches "
                                "WHERE source=?", (name,)).fetchall():
                fid = f["id"]
                if f["status"] == "complete" and f["epoch"] == epoch:
                    if fid in keep:
                        continue
                    if (f["finished_at"] or 0) >= fetch_cut:
                        continue
                elif f["status"] in ("running", "failed") and f["epoch"] == epoch and \
                        (f["started_at"] or 0) >= resume_cut:
                    continue
                elif f["status"] in ("abandoned", "running", "failed") or f["epoch"] != epoch:
                    if (f["updated_at"] or 0) >= fetch_cut:
                        n = db.execute("SELECT COUNT(*) c FROM fetch_pages WHERE fetch_id=?", (fid,)).fetchone()["c"]
                        if n:
                            counts["pages_of_abandoned"] += 1
                            if not dry_run:
                                db.execute("DELETE FROM fetch_pages WHERE fetch_id=?", (fid,))
                                db.execute("UPDATE fetches SET status='abandoned', page_state=NULL WHERE id=?", (fid,))
                        continue
                counts["fetches"] += 1
                if not dry_run:
                    db.execute("DELETE FROM fetch_pages WHERE fetch_id=?", (fid,))
                    db.execute("DELETE FROM fetches WHERE id=?", (fid,))
        cur = db.execute("SELECT COUNT(*) c FROM http_cache WHERE stored_at<?", (fetch_cut,)).fetchone()["c"]
        counts["http_cache"] = cur
        if not dry_run:
            db.execute("DELETE FROM http_cache WHERE stored_at<?", (fetch_cut,))
        # 増分取得で組み立てた全体の姿のうち、長く取得されていないもの（設定したときだけ）
        if rt.record_days is not None:
            stale_cut = t - rt.record_days * 86400
            counts["stale_records"] = db.execute(
                "SELECT COUNT(*) c FROM source_records WHERE COALESCE(seen_at, 0)<?", (stale_cut,)).fetchone()["c"]
            if not dry_run:
                db.execute("DELETE FROM source_records WHERE COALESCE(seen_at, 0)<?", (stale_cut,))
    if not dry_run:
        with store.tx() as db:
            for name in LEGACY_TABLES:
                db.execute(f"DROP TABLE IF EXISTS {name}")
        shutil.rmtree(store.dir / "events", ignore_errors=True)
        counts["freed_bytes"] = store.shrink(full=vacuum)
    return counts
