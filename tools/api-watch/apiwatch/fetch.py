"""取得元ごとの取得。

1 回の取得は fetches の 1 行で、ページを取るたびに「取ったページ」と「次のページの取り方」を
同じトランザクションで書く。途中で失敗・停止しても、次の取得は続きのページから再開する
（resume_max_age_sec を過ぎたら最初から取り直す）。全ページを取り終えるまで、増分の位置は進めず、
判定にも回さない。だから未取得のデータを処理済みとして飛ばすことはない。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import jpath, template
from .config import Config, SourceSpec
from .httpclient import Client, Request, build_url
from .store import Store
from .util import (REDACT, PermanentError, TransientError, canonical, is_secret_name, key_string, log, now, pack, sha,
                   unpack)


@dataclass
class FetchOutcome:
    source: str
    status: str                 # complete / skipped / deferred / busy / failed
    fetch_id: int | None = None
    records: int = 0
    pages: int = 0
    requests: int = 0
    not_modified: int = 0
    message: str = ""
    transient: bool = False


def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def _dumps(value: Any) -> str | None:
    return None if value is None else canonical(value)


def _newer(a: Any, b: Any) -> bool:
    """a が b より新しい（大きい）か。数どうし・文字列どうしだけ比べる。"""
    if b is None:
        return a is not None and a is not jpath.MISSING
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a > b
    if isinstance(a, str) and isinstance(b, str):
        return a > b
    return False


def _link_next(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        m = re.match(r'\s*<([^>]*)>\s*;(.*)', part)
        if m and re.search(r'rel\s*=\s*"?([^";]*\s)?next(\s[^";]*)?"?', m.group(2)):
            return m.group(1)
    return None


def load_records(store: Store, fetch_id: int) -> list:
    out: list = []
    for row in store.q("SELECT records FROM fetch_pages WHERE fetch_id=? ORDER BY page_no", (fetch_id,)):
        out.extend(unpack(row["records"]))
    return out


def initial_page_state(src: SourceSpec) -> dict:
    p = src.pagination
    st: dict[str, Any] = {"page_no": 0, "inc_max": None, "inc_cursor": None, "seen_cursors": []}
    if p.type == "page":
        st["page"] = p.start
    elif p.type == "offset":
        st["offset"] = p.start
    elif p.type == "cursor":
        st["cursor"] = None
    elif p.type in ("next_url", "link_header"):
        st["next_url"] = None
    return st


class Fetcher:
    def __init__(self, cfg: Config, store: Store, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.store = store
        self.sleep = sleep

    # -- 要求を組む -----------------------------------------------------------

    def _context(self, src: SourceSpec, conn: dict | None, position: Any) -> dict:
        import os
        t = now()
        return {"conn": conn or {}, "env": dict(os.environ), "position": position, "now": int(t),
                "now_iso": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": {"name": src.name}}

    def build_request(self, src: SourceSpec, ctx: dict, st: dict, position: Any) -> Request:
        p = src.pagination
        headers = {str(k): str(v) for k, v in template.render(src.headers, ctx).items() if v is not None}
        for k, v in headers.items():
            if is_secret_name(k) or k.lower() in ("authorization", "cookie"):
                REDACT.add(v)
                if " " in v:
                    REDACT.add(v.split(" ", 1)[1])
        query = dict(template.render(src.query, ctx))
        body = template.render(src.body, ctx) if src.body is not None else None
        form = template.render(src.body_form, ctx) if src.body_form is not None else None
        for k, v in query.items():
            if is_secret_name(k) and isinstance(v, str):
                REDACT.add(v)

        def place(location: str, name: str | None, value: Any) -> None:
            nonlocal body
            if not name:
                return
            if location == "body":
                body = dict(body or {})
                body[name] = value
            else:
                query[name] = value

        inc = src.incremental
        if inc is not None and position is not None and inc.param:
            place(inc.location, inc.param, position)
        url = template.render(src.url, ctx)
        if p.type in ("next_url", "link_header") and st.get("next_url"):
            url = st["next_url"]
            query = {}
        elif p.type == "page":
            place(p.location, p.param, st["page"])
            if p.size_param and p.size:
                place(p.location, p.size_param, p.size)
        elif p.type == "offset":
            place(p.location, p.param, st["offset"])
            if p.size_param and p.size:
                place(p.location, p.size_param, p.size)
        elif p.type == "cursor" and st.get("cursor") is not None:
            place(p.location, p.param, st["cursor"])
        full_url = build_url(str(url), query)
        data: bytes | None = None
        if form is not None:
            data = urllib.parse.urlencode({k: v for k, v in form.items() if v is not None}).encode("utf-8")
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            else:
                data = str(body).encode("utf-8")
        return Request(method=src.method, url=full_url, headers=headers, body=data, timeout=src.timeout_sec)

    # -- 取得 -----------------------------------------------------------------

    def run(self, src: SourceSpec, force: bool = False) -> FetchOutcome:
        conn = self.cfg.resolve_connection(src.connection)
        fp = src.fingerprint(conn)
        lease = f"fetch:{src.name}"
        if not self.store.acquire(lease, src.lease_sec):
            holder = self.store.lease_holder(lease)
            who = f"{holder['host']} pid {holder['pid']}" if holder else "別のプロセス"
            return FetchOutcome(src.name, "busy", message=f"{who} が取得中なので何もしません")
        try:
            return self._run_locked(src, conn, fp, force, lease)
        finally:
            self.store.release(lease)

    def _reset_if_changed(self, src: SourceSpec, fp: str) -> Any:
        row = self.store.source_row(src.name)
        if row["fingerprint"] == fp:
            return row
        with self.store.tx() as db:
            first = row["fingerprint"] is None
            db.execute("UPDATE sources SET fingerprint=?, epoch=epoch+1, position=NULL, last_fetch_id=NULL "
                       "WHERE name=?", (fp, src.name))
            db.execute("UPDATE fetches SET status='abandoned', page_state=NULL, error='取得条件が変わったため破棄' "
                       "WHERE source=? AND status IN ('running','failed')", (src.name,))
            db.execute("DELETE FROM http_cache WHERE source=?", (src.name,))
            db.execute("DELETE FROM source_records WHERE source=?", (src.name,))
        if not first:
            log(f"[{src.name}] 取得条件か認証が変わったので、これまでの取得結果・位置を使わず取り直します"
                "（この取得元のルールは基準値を取り直します）")
        return self.store.source_row(src.name)

    def _run_locked(self, src: SourceSpec, conn: dict | None, fp: str, force: bool, lease: str) -> FetchOutcome:
        row = self._reset_if_changed(src, fp)
        t = now()
        if not force and src.min_interval_sec and row["last_success_at"] and \
                t - row["last_success_at"] < src.min_interval_sec:
            return FetchOutcome(src.name, "skipped",
                                message=f"前回の取得から {src.min_interval_sec:.0f} 秒たっていないので取得しません")
        if row["next_allowed_at"] and row["next_allowed_at"] > t:
            wait = row["next_allowed_at"] - t
            if wait > src.max_wait_sec:
                return FetchOutcome(src.name, "deferred",
                                    message=f"API に待つよう言われているので {wait:.0f} 秒後まで取得しません")
            log(f"[{src.name}] API に待つよう言われているので {wait:.0f} 秒待ちます")
            self.sleep(wait)
        epoch = int(row["epoch"])
        fetch = self._resume_or_start(src, row, epoch)
        fid = int(fetch["id"])
        position = _loads(fetch["position_from"])
        st = json.loads(fetch["page_state"])
        ctx = self._context(src, conn, position)

        def on_request(ts: float) -> None:
            with self.store.tx() as db:
                db.execute("UPDATE sources SET last_request_at=?, last_attempt_at=? WHERE name=?",
                           (ts, ts, src.name))

        client = Client(min_interval=src.rate_min_interval_sec, per_minute=src.rate_per_minute,
                        attempts=src.retry_attempts, backoff=src.retry_backoff_sec,
                        max_backoff=src.retry_max_backoff_sec, max_wait=src.max_wait_sec,
                        last_request_at=row["last_request_at"], on_request=on_request, sleep=self.sleep)
        outcome = FetchOutcome(src.name, "failed", fetch_id=fid)
        try:
            while not st.get("done"):
                st = self._one_page(src, client, ctx, st, fid, epoch, position)
                self.store.renew(lease, src.lease_sec)
                if st is None:
                    break
            return self._complete(src, fid, epoch, position, outcome, client)
        except (TransientError, PermanentError) as e:
            msg = REDACT(str(e))
            retry_after = getattr(e, "retry_after", None)
            with self.store.tx() as db:
                db.execute("UPDATE fetches SET status='failed', error=?, updated_at=?, requests=requests+? "
                           "WHERE id=?", (msg, now(), 0, fid))
                db.execute("UPDATE sources SET last_status='error', last_error=?, "
                           "consecutive_failures=consecutive_failures+1, next_allowed_at=? WHERE name=?",
                           (msg, (now() + retry_after) if retry_after else None, src.name))
            outcome.message = msg
            outcome.transient = isinstance(e, TransientError)
            outcome.requests = client.requests
            return outcome

    def _resume_or_start(self, src: SourceSpec, row: Any, epoch: int) -> Any:
        prev = self.store.one("SELECT * FROM fetches WHERE source=? AND epoch=? AND status IN ('running','failed') "
                              "AND page_state IS NOT NULL ORDER BY id DESC LIMIT 1", (src.name, epoch))
        if prev is not None and src.resume and now() - (prev["started_at"] or 0) <= src.resume_max_age_sec:
            with self.store.tx() as db:
                db.execute("UPDATE fetches SET status='running', updated_at=? WHERE id=?", (now(), prev["id"]))
                db.execute("UPDATE fetches SET status='abandoned', page_state=NULL WHERE source=? "
                           "AND status IN ('running','failed') AND id<>?", (src.name, prev["id"]))
            log(f"[{src.name}] 前回途中で止まった取得 #{prev['id']} を {prev['pages'] + 1} ページ目から再開します")
            return self.store.one("SELECT * FROM fetches WHERE id=?", (prev["id"],))
        position = _loads(row["position"])
        inc = src.incremental
        if inc is not None and position is None and inc.initial is not None:
            position = inc.initial
        mode = "incremental" if inc is not None and position is not None else "full"
        with self.store.tx() as db:
            db.execute("UPDATE fetches SET status='abandoned', page_state=NULL WHERE source=? "
                       "AND status IN ('running','failed')", (src.name,))
            cur = db.execute("INSERT INTO fetches(source, epoch, mode, status, started_at, updated_at, page_state, "
                             "position_from) VALUES(?,?,?,?,?,?,?,?)",
                             (src.name, epoch, mode, "running", now(), now(), json.dumps(initial_page_state(src)),
                              _dumps(position)))
            fid = cur.lastrowid
        return self.store.one("SELECT * FROM fetches WHERE id=?", (fid,))

    def _one_page(self, src: SourceSpec, client: Client, ctx: dict, st: dict, fid: int, epoch: int,
                  position: Any) -> dict | None:
        p = src.pagination
        page_no = int(st["page_no"]) + 1
        if page_no > p.max_pages:
            raise PermanentError(f"ページ数が上限（max_pages={p.max_pages}）を超えました。取りこぼさないよう、"
                                 "この取得は完了にしません。上限を上げるか、取得の条件を絞ってください")
        req = self.build_request(src, ctx, st, position)
        sig = sha(req.method + " " + req.url + " " + (req.body or b"").decode("utf-8", "replace"), 40)
        cached = None
        if src.conditional:
            cached = self.store.one("SELECT * FROM http_cache WHERE source=? AND sig=? AND epoch=?",
                                    (src.name, sig, epoch))
            if cached is not None:
                if cached["etag"]:
                    req.headers["If-None-Match"] = cached["etag"]
                if cached["last_modified"]:
                    req.headers["If-Modified-Since"] = cached["last_modified"]
        resp = client.send(req)
        not_modified = resp.status == 304
        if not_modified:
            if cached is None or cached["body"] is None:
                raise PermanentError("条件付き要求でないのに 304 が返りました")
            body = unpack(cached["body"])
        else:
            body = resp.json()
        recs = jpath.get(body, src.records)
        if recs is jpath.MISSING or recs is None:
            if src.records not in ("", "$") and page_no == 1 and recs is jpath.MISSING:
                raise PermanentError(f"応答に records（{src.records}）がありません")
            recs = []
        if isinstance(recs, dict):
            recs = [recs]
        if not isinstance(recs, list):
            raise PermanentError(f"records（{src.records}）が配列ではありません")

        # 増分の位置（全ページを取り終えたときだけ確定する）
        inc = src.incremental
        if inc is not None and inc.type == "max_value":
            for r in recs:
                v = jpath.get(r, inc.field or "")
                if v is not jpath.MISSING and v is not None and _newer(v, st.get("inc_max")):
                    st["inc_max"] = v
        elif inc is not None and inc.type == "cursor":
            v = jpath.get(body, inc.cursor_path or "")
            if v is not jpath.MISSING and v not in (None, ""):
                st["inc_cursor"] = v

        nxt: dict | None = dict(st)
        nxt["page_no"] = page_no
        n = len(recs)
        if p.type == "none":
            nxt = None
        elif p.type == "page":
            total_pages = jpath.get(body, p.total_pages_path) if p.total_pages_path else jpath.MISSING
            if n == 0 or (p.size and n < p.size):
                nxt = None
            elif isinstance(total_pages, int) and st["page"] >= total_pages + (p.start - 1):
                nxt = None
            else:
                nxt["page"] = st["page"] + 1
        elif p.type == "offset":
            total = jpath.get(body, p.total_path) if p.total_path else jpath.MISSING
            new_off = st["offset"] + n
            if n == 0 or (p.size and n < p.size) or (isinstance(total, int) and new_off >= total):
                nxt = None
            else:
                nxt["offset"] = new_off
        elif p.type == "cursor":
            c = jpath.get(body, p.cursor_path or "")
            more = jpath.get(body, p.has_more_path) if p.has_more_path else True
            if c is jpath.MISSING or c in (None, "") or (more is not jpath.MISSING and not more):
                nxt = None
            elif canonical(c) in st.get("seen_cursors", []):
                raise PermanentError(f"同じカーソルが 2 回返りました（{REDACT(str(c))}）。ページングの設定を確かめてください")
            else:
                nxt["cursor"] = c
                nxt["seen_cursors"] = (st.get("seen_cursors", []) + [canonical(c)])[-50:]
        elif p.type in ("next_url", "link_header"):
            if p.type == "next_url":
                u = jpath.get(body, p.next_url_path or "")
                u = None if u is jpath.MISSING else u
            else:
                u = _link_next(resp.headers.get("link"))
            if not u or (n == 0 and page_no > 1):
                nxt = None
            else:
                u = urllib.parse.urljoin(req.url, str(u))
                if not p.allow_cross_host and urllib.parse.urlsplit(u).netloc != urllib.parse.urlsplit(req.url).netloc:
                    raise PermanentError(f"次ページの URL が別のホストです（{REDACT(u)}）。認証情報を渡さないよう止めます"
                                         "（意図したものなら pagination.allow_cross_host: true）")
                if canonical(u) in st.get("seen_cursors", []):
                    raise PermanentError("同じ次ページ URL が 2 回返りました。ページングの設定を確かめてください")
                nxt["next_url"] = u
                nxt["seen_cursors"] = (st.get("seen_cursors", []) + [canonical(u)])[-50:]

        with self.store.tx() as db:
            db.execute("INSERT OR REPLACE INTO fetch_pages(fetch_id, page_no, records) VALUES(?,?,?)",
                       (fid, page_no, pack(recs)))
            if src.conditional and not not_modified and resp.status == 200 and \
                    (resp.headers.get("etag") or resp.headers.get("last-modified")):
                db.execute("INSERT OR REPLACE INTO http_cache(source, sig, epoch, etag, last_modified, body, stored_at) "
                           "VALUES(?,?,?,?,?,?,?)", (src.name, sig, epoch, resp.headers.get("etag"),
                                                     resp.headers.get("last-modified"), pack(body), now()))
            final_state = dict(nxt) if nxt is not None else dict(st, page_no=page_no, done=True)
            db.execute("UPDATE fetches SET pages=?, records=records+?, requests=requests+1, "
                       "not_modified=not_modified+?, page_state=?, updated_at=? WHERE id=?",
                       (page_no, n, 1 if not_modified else 0, json.dumps(final_state), now(), fid))
        log(f"[{src.name}] {page_no} ページ目: {n} 件" + ("（変更なし・304）" if not_modified else ""))
        return nxt

    def _complete(self, src: SourceSpec, fid: int, epoch: int, position: Any, outcome: FetchOutcome,
                  client: Client) -> FetchOutcome:
        f = self.store.one("SELECT * FROM fetches WHERE id=?", (fid,))
        st = json.loads(f["page_state"])
        inc = src.incremental
        new_pos = position
        if inc is not None and inc.type == "max_value" and st.get("inc_max") is not None:
            new_pos = st["inc_max"] if _newer(st["inc_max"], position) else position
        elif inc is not None and inc.type == "cursor" and st.get("inc_cursor") is not None:
            new_pos = st["inc_cursor"]
        records = load_records(self.store, fid) if src.key else None
        with self.store.tx() as db:
            db.execute("UPDATE fetches SET status='complete', finished_at=?, updated_at=?, page_state=NULL, "
                       "position_to=?, error=NULL WHERE id=?", (now(), now(), _dumps(new_pos), fid))
            db.execute("UPDATE sources SET position=?, last_success_at=?, last_status='ok', last_error=NULL, "
                       "last_fetch_id=?, consecutive_failures=0, next_allowed_at=NULL WHERE name=?",
                       (_dumps(new_pos), now(), fid, src.name))
            if records is not None:
                keys = []
                for r in records:
                    v = jpath.get(r, src.key or "")
                    if v is jpath.MISSING or v is None:
                        continue
                    k = key_string([v])
                    keys.append(k)
                    db.execute("INSERT OR REPLACE INTO source_records(source, rkey, epoch, data, fetch_id) "
                               "VALUES(?,?,?,?,?)", (src.name, k, epoch, pack(r), fid))
                if f["mode"] == "full":
                    have = {r["rkey"] for r in db.execute("SELECT rkey FROM source_records WHERE source=?",
                                                          (src.name,))}
                    for k in have - set(keys):
                        db.execute("DELETE FROM source_records WHERE source=? AND rkey=?", (src.name, k))
        outcome.status = "complete"
        outcome.records = int(f["records"])
        outcome.pages = int(f["pages"])
        outcome.requests = client.requests
        outcome.not_modified = int(f["not_modified"])
        outcome.message = f"{outcome.pages} ページ・{outcome.records} 件（{f['mode']}）"
        return outcome
