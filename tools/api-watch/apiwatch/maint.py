"""運用: 状況の確認・保持期間の整理・Dagu の DAG の生成。"""
from __future__ import annotations

import json
import sys
from typing import Any

from .config import Config
from .store import DEAD, FAILED, FINAL, SEND_FAILED, Store
from .util import iso, now


def status(cfg: Config, store: Store) -> dict:
    out: dict[str, Any] = {"state_dir": str(store.dir), "storage": store.storage(), "sources": [], "rules": [],
                           "problems": []}
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
    for name, rule in cfg.rules.items():
        r = store.one("SELECT * FROM rules WHERE name=?", (name,))
        srow = store.source_row(rule.source)
        pending_fetches = 0
        if r is not None and r["last_fetch_id"] is not None:
            pending_fetches = store.one("SELECT COUNT(*) c FROM fetches WHERE source=? AND epoch=? AND status='complete' "
                                        "AND id>?", (rule.source, srow["epoch"], r["last_fetch_id"]))["c"]
        counts = {row["status"]: row["c"] for row in
                  store.q("SELECT status, COUNT(*) c FROM deliveries WHERE rule=? GROUP BY status", (name,))}
        events = store.one("SELECT COUNT(*) c FROM events WHERE rule=?", (name,))["c"]
        out["rules"].append({
            "name": name, "source": rule.source,
            "evaluated_through_fetch": None if r is None else r["last_fetch_id"],
            "unevaluated_fetches": pending_fetches,
            "last_eval_at": iso(r["last_eval_at"]) if r is not None else "-",
            "last_error": None if r is None else r["last_error"],
            "rebaseline_requested": bool(r is not None and r["needs_rebaseline"]),
            "events": events,
            "deliveries": counts,
        })
    for d in store.q("SELECT * FROM deliveries WHERE status IN (?,?,?) ORDER BY seq LIMIT 50",
                     (SEND_FAILED, DEAD, FAILED)):
        out["problems"].append({"delivery": d["id"], "event": d["event_id"], "rule": d["rule"], "target": d["target"],
                                "record": d["rkey"], "status": d["status"], "attempts": d["attempts"],
                                "error": d["last_error"], "updated_at": iso(d["updated_at"])})
    known_rules = set(cfg.rules)
    out["orphan_rules"] = [r["name"] for r in store.q("SELECT name FROM rules") if r["name"] not in known_rules]
    return out


def print_status(st: dict, file=None) -> None:
    file = file or sys.stdout
    p = lambda s="": print(s, file=file)  # noqa: E731
    p(f"状態の保存先: {st['state_dir']}")
    sg = st["storage"]
    mb = lambda n: f"{n / 1048576:.1f} MB"  # noqa: E731
    p(f"  DB {mb(sg['db_bytes'])}（うち空き {mb(sg['free_bytes'])}）・WAL {mb(sg['wal_bytes'])}・"
      f"イベントのファイル {sg['event_files']} 個 {mb(sg['event_files_bytes'])}")
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
    p("ルール")
    for r in st["rules"]:
        d = r["deliveries"]
        dl = "・".join(f"{k} {v}" for k, v in sorted(d.items())) or "なし"
        p(f"  {r['name']}（{r['source']}）: 判定済み #{r['evaluated_through_fetch'] or '-'}・未判定 {r['unevaluated_fetches']} 回"
          f"・イベント {r['events']} 件・受け渡し {dl}")
        if r["last_error"]:
            p(f"    判定のエラー: {r['last_error']}")
        if r["rebaseline_requested"]:
            p("    次の判定で基準値を取り直します")
    if st["problems"]:
        p()
        p("失敗している受け渡し（retry で再実行・skip で飛ばす）")
        for x in st["problems"]:
            p(f"  {x['delivery']}  {x['rule']} → {x['target']}  レコード {x['record']}  {x['status']}"
              f"（{x['attempts']} 回）: {x['error'] or ''}")
    if st["orphan_rules"]:
        p()
        p("設定から消えたルール（状態が残っています。要らなければ forget-rule で消せます）: " + ", ".join(st["orphan_rules"]))


def gc(cfg: Config, store: Store, dry_run: bool = False, vacuum: bool = False) -> dict:
    """保持期間を過ぎたものを消し、空いた領域をファイルから返す。未判定の取得結果・未完了の受け渡しのイベントは消さない。

    vacuum=True なら VACUUM で DB を作り直す（古い DB を自動で縮む形に切り替える。時間がかかる）。
    """
    t = now()
    rt = cfg.retention
    counts = {"fetches": 0, "pages_of_abandoned": 0, "events": 0, "event_files": 0, "http_cache": 0,
              "removed_records": 0, "stale_records": 0, "freed_bytes": 0}
    fetch_cut = t - rt.fetch_days * 86400
    ev_cut = t - rt.events_days * 86400
    with store.tx() as db:
        for srow in db.execute("SELECT * FROM sources").fetchall():
            name = srow["name"]
            src = cfg.sources.get(name)
            epoch = srow["epoch"]
            keep = {r["id"] for r in db.execute(
                "SELECT id FROM fetches WHERE source=? AND epoch=? AND status='complete' ORDER BY id DESC LIMIT ?",
                (name, epoch, rt.fetches_keep))}
            # 設定にあるルールのうち、まだ判定していない取得は消さない
            floor = None
            for rule in cfg.rules_for(name):
                rr = db.execute("SELECT last_fetch_id, epoch, needs_rebaseline FROM rules WHERE name=?",
                                (rule.name,)).fetchone()
                if rr is None or rr["last_fetch_id"] is None or rr["epoch"] != epoch or rr["needs_rebaseline"]:
                    continue   # 次の判定は最新の取得で基準値を取る
                floor = rr["last_fetch_id"] if floor is None else min(floor, rr["last_fetch_id"])
            resume_cut = t - (src.resume_max_age_sec if src else 0)
            for f in db.execute("SELECT id, status, epoch, started_at, finished_at, updated_at FROM fetches "
                                "WHERE source=?", (name,)).fetchall():
                fid = f["id"]
                if f["status"] == "complete" and f["epoch"] == epoch:
                    if fid in keep or (floor is not None and fid > floor):
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
        final = ",".join(f"'{s}'" for s in FINAL)
        old = db.execute(f"SELECT id FROM events e WHERE created_at<? AND NOT EXISTS (SELECT 1 FROM deliveries d "
                         f"WHERE d.event_id=e.id AND d.status NOT IN ({final}))", (ev_cut,)).fetchall()
        for e in old:
            counts["events"] += 1
            if not dry_run:
                db.execute("DELETE FROM deliveries WHERE event_id=?", (e["id"],))
                db.execute("DELETE FROM events WHERE id=?", (e["id"],))
        # 消えた・対象外になったレコードの前回の観測値（また現れたら appeared になる）
        removed_cut = t - rt.removed_days * 86400
        counts["removed_records"] = db.execute("SELECT COUNT(*) c FROM rule_records WHERE present=0 AND updated_at<?",
                                               (removed_cut,)).fetchone()["c"]
        if not dry_run:
            db.execute("DELETE FROM rule_records WHERE present=0 AND updated_at<?", (removed_cut,))
            # 処理し終えた状態は、ルールの観測値が消えたレコードのものだけ消す（また現れたら appeared で流れる）
            db.execute("DELETE FROM handled_records WHERE NOT EXISTS (SELECT 1 FROM rule_records r "
                       "WHERE r.rule=handled_records.rule AND r.rkey=handled_records.rkey)")
        # 増分取得で組み立てた全体の姿のうち、長く取得されていないもの（設定したときだけ）
        if rt.record_days is not None:
            stale_cut = t - rt.record_days * 86400
            counts["stale_records"] = db.execute(
                "SELECT COUNT(*) c FROM source_records WHERE COALESCE(seen_at, 0)<?", (stale_cut,)).fetchone()["c"]
            if not dry_run:
                db.execute("DELETE FROM source_records WHERE COALESCE(seen_at, 0)<?", (stale_cut,))
    # イベントのファイル（DB から消えたもの・最終状態になってから保持期間を過ぎたもの）
    live = {r["id"] for r in store.q("SELECT id FROM events")}
    for f in (store.dir / "events").glob("*.json"):
        eid = f.stem.split("--")[0]          # 取り直したレコードを入れたもの（<ID>--<受け渡し先>）も同じイベントのもの
        if eid in live:
            continue
        try:
            if f.stat().st_mtime < t - 86400:
                counts["event_files"] += 1
                if not dry_run:
                    f.unlink()
        except OSError:
            pass
    if not dry_run:
        counts["freed_bytes"] = store.shrink(full=vacuum)
    return counts


def forget_rule(store: Store, name: str) -> str:
    open_d = store.one("SELECT COUNT(*) c FROM deliveries WHERE rule=? AND status NOT IN ('done','skipped')",
                       (name,))["c"]
    with store.tx() as db:
        db.execute("DELETE FROM rule_records WHERE rule=?", (name,))
        db.execute("DELETE FROM handled_records WHERE rule=?", (name,))
        db.execute("DELETE FROM rules WHERE name=?", (name,))
    msg = f"ルール {name} の判定状態を消しました"
    if open_d:
        msg += f"（未完了の受け渡し {open_d} 件は残しています。要らなければ skip してください）"
    return msg
