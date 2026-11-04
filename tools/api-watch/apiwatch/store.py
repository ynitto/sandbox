"""状態の保存先（SQLite）。

1 つのファイル（<state_dir>/state.db）に、取得元の取得状態・取得結果・ルールごとの観測値・
イベント・受け渡しをまとめて持つ。状態の遷移は 1 つのトランザクションで書くので、
途中でプロセスや PC が止まっても「半分だけ進んだ」状態は残らない。

表:
    sources         取得元ごとの取得状態（指紋・世代・増分の位置・レート制限の待ち・最後の結果）
    fetches         1 回の取得（running → complete / failed / abandoned）。ページ単位で進み、途中から再開できる
    fetch_pages     取得したページのレコード（圧縮した JSON）
    http_cache      条件付き要求用の ETag / Last-Modified と本文
    source_records  取得元の key ごとの最新のレコード（増分取得の取得元で、全体を組み立てた姿）
    rules           ルールごとの判定状態（どの取得まで判定したか・指紋・最後のエラー）
    rule_records    ルール × レコードの前回の観測値
    events          検知した変更（ルール × レコード × 取得 で 1 件）
    deliveries      イベント × 受け渡し先 ごとの受け渡し状態
    leases          取得・判定の排他（期限つき。持ち主のプロセスが死んでいれば奪える）
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Iterator

from .util import hostname, now, pid_alive

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    fingerprint TEXT,
    epoch INTEGER NOT NULL DEFAULT 0,
    position TEXT,
    next_allowed_at REAL,
    last_request_at REAL,
    last_attempt_at REAL,
    last_success_at REAL,
    last_status TEXT,
    last_error TEXT,
    last_fetch_id INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    retry_not_before REAL,      -- 取得が失敗したあと、どのプロセスもこの時刻までは問い合わせない
    recent_requests TEXT        -- 直近 60 秒に送った要求の時刻（JSON。rate_limit.per_minute をプロセスをまたいで数える）
);
CREATE TABLE IF NOT EXISTS fetches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL,
    updated_at REAL,
    finished_at REAL,
    pages INTEGER NOT NULL DEFAULT 0,
    records INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    not_modified INTEGER NOT NULL DEFAULT 0,
    page_state TEXT,
    position_from TEXT,
    position_to TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS fetches_source ON fetches(source, status, id);
CREATE TABLE IF NOT EXISTS fetch_pages (
    fetch_id INTEGER NOT NULL,
    page_no INTEGER NOT NULL,
    records BLOB NOT NULL,
    PRIMARY KEY (fetch_id, page_no)
);
CREATE TABLE IF NOT EXISTS http_cache (
    source TEXT NOT NULL,
    sig TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    etag TEXT,
    last_modified TEXT,
    body BLOB,
    stored_at REAL,
    PRIMARY KEY (source, sig)
);
CREATE TABLE IF NOT EXISTS source_records (
    source TEXT NOT NULL,
    rkey TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    data BLOB NOT NULL,
    fetch_id INTEGER,
    seen_at REAL,
    PRIMARY KEY (source, rkey)
);
CREATE TABLE IF NOT EXISTS rules (
    name TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    fingerprint TEXT,
    epoch INTEGER,
    last_fetch_id INTEGER,
    baseline_fetch_id INTEGER,
    last_eval_at REAL,
    last_error TEXT,
    last_events INTEGER NOT NULL DEFAULT 0,
    needs_rebaseline INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rule_records (
    rule TEXT NOT NULL,
    rkey TEXT NOT NULL,
    present INTEGER NOT NULL,
    matched INTEGER,
    vhash TEXT,
    value BLOB,
    last_fetch_id INTEGER,
    updated_at REAL,
    PRIMARY KEY (rule, rkey)
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    rule TEXT NOT NULL,
    source TEXT NOT NULL,
    rkey TEXT NOT NULL,
    fetch_id INTEGER,
    kinds TEXT NOT NULL,
    created_at REAL NOT NULL,
    payload BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS events_rule ON events(rule, seq);
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    rule TEXT NOT NULL,
    target TEXT NOT NULL,
    workflow TEXT NOT NULL,
    rkey TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    lease_until REAL,
    owner TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    dispatched_at REAL,
    completed_at REAL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS deliveries_status ON deliveries(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS deliveries_key ON deliveries(rule, target, rkey, seq);
CREATE TABLE IF NOT EXISTS leases (
    name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    host TEXT NOT NULL,
    pid INTEGER NOT NULL,
    expires_at REAL NOT NULL
);
"""

# 受け渡しの状態
PENDING = "pending"          # まだ Dagu に渡していない
SENDING = "sending"          # 渡している最中（期限つき。期限切れは渡せたか分からないので同じ ID で渡し直す）
SEND_FAILED = "send_failed"  # 渡せなかった。時間をおいて同じ ID で渡し直す
DEAD = "dead"                # 渡し直しの上限に達した。手で retry するまで止まる
DISPATCHED = "dispatched"    # Dagu のキューに入った（後続処理の完了待ち）
DONE = "done"                # 後続処理が成功した
FAILED = "failed"            # 後続処理が失敗した（Dagu で再試行・復旧する）
SKIPPED = "skipped"          # 手で飛ばした
FINAL = (DONE, SKIPPED)
ALL_STATUSES = (PENDING, SENDING, SEND_FAILED, DEAD, DISPATCHED, DONE, FAILED, SKIPPED)


class Store:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "events").mkdir(exist_ok=True)
        self.path = self.dir / "state.db"
        self.db = sqlite3.connect(str(self.path), timeout=60, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        # 消した行の領域をファイルから返せるようにする（表を作る前でないと効かない。既存の DB は gc --vacuum で切り替わる）
        self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=60000")
        # executescript は自前でコミットするので、トランザクションの外で流す（すべて IF NOT EXISTS）
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(source_records)")}
        if "seen_at" not in cols:
            self.db.execute("ALTER TABLE source_records ADD COLUMN seen_at REAL")
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(sources)")}
        for col, typ in (("retry_not_before", "REAL"), ("recent_requests", "TEXT")):
            if col not in cols:
                self.db.execute(f"ALTER TABLE sources ADD COLUMN {col} {typ}")
        with self.tx():
            row = self.db.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
            if row is None:
                self.db.execute("INSERT INTO meta(k, v) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
        self.owner = f"{hostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def close(self) -> None:
        self.db.close()

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """書き込みのトランザクション（BEGIN IMMEDIATE で、他のプロセスと書き込みを直列にする）。"""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        return self.db.execute(sql, args).fetchone()

    # -- 排他 -----------------------------------------------------------------

    def acquire(self, name: str, ttl: float) -> bool:
        """期限つきの排他を取る。取れなければ False。持ち主のプロセスが同じ PC で死んでいれば奪う。"""
        host = hostname()
        with self.tx() as db:
            row = db.execute("SELECT * FROM leases WHERE name=?", (name,)).fetchone()
            t = now()
            if row is not None and row["owner"] != self.owner and row["expires_at"] > t:
                if not (row["host"] == host and not pid_alive(int(row["pid"]))):
                    return False
            db.execute("INSERT INTO leases(name, owner, host, pid, expires_at) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, host=excluded.host, "
                       "pid=excluded.pid, expires_at=excluded.expires_at",
                       (name, self.owner, host, os.getpid(), t + ttl))
            return True

    def renew(self, name: str, ttl: float) -> None:
        with self.tx() as db:
            db.execute("UPDATE leases SET expires_at=? WHERE name=? AND owner=?", (now() + ttl, name, self.owner))

    def release(self, name: str) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, self.owner))

    def lease_holder(self, name: str) -> sqlite3.Row | None:
        row = self.one("SELECT * FROM leases WHERE name=?", (name,))
        if row is None or row["expires_at"] <= now():
            return None
        if row["host"] == hostname() and not pid_alive(int(row["pid"])):
            return None
        return row

    @contextlib.contextmanager
    def lease(self, name: str, ttl: float) -> Iterator[bool]:
        got = self.acquire(name, ttl)
        try:
            yield got
        finally:
            if got:
                self.release(name)

    # -- 取得元 ---------------------------------------------------------------

    def source_row(self, name: str) -> sqlite3.Row:
        row = self.one("SELECT * FROM sources WHERE name=?", (name,))
        if row is None:
            with self.tx() as db:
                db.execute("INSERT OR IGNORE INTO sources(name, epoch) VALUES(?, 0)", (name,))
            row = self.one("SELECT * FROM sources WHERE name=?", (name,))
        assert row is not None
        return row

    def event_file(self, event_id: str) -> Path:
        return self.dir / "events" / f"{event_id}.json"

    # -- 容量 -----------------------------------------------------------------

    def storage(self) -> dict:
        """状態の置き場所の大きさ（バイト）と、DB の中の空き。"""
        def size(p: Path) -> int:
            try:
                return p.stat().st_size
            except OSError:
                return 0
        page = self.one("PRAGMA page_size")[0]
        events = [f for f in (self.dir / "events").glob("*.json")]
        return {
            "db_bytes": size(self.path),
            "wal_bytes": size(self.path.with_name(self.path.name + "-wal")),
            "free_bytes": self.one("PRAGMA freelist_count")[0] * page,
            "event_files": len(events),
            "event_files_bytes": sum(size(f) for f in events),
            "auto_vacuum": {0: "none", 1: "full", 2: "incremental"}.get(self.one("PRAGMA auto_vacuum")[0], "?"),
        }

    def shrink(self, full: bool = False) -> int:
        """消した行の領域をファイルから返し、WAL を切り詰める。返したバイト数（概算）を返す。

        full=False は incremental_vacuum（すぐ終わる。auto_vacuum=INCREMENTAL の DB だけ効く）、
        full=True は VACUUM（DB を作り直す。古い DB を INCREMENTAL に切り替える。大きい DB では時間がかかる）。
        """
        before = self.storage()
        if full:
            self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self.db.execute("VACUUM")
        else:
            # 1 回の step で 1 ページずつ返す。execute では Python のバージョンによって 1 回しか step しないので、
            # 最後まで step する executescript（sqlite3_exec）で流す
            self.db.executescript("PRAGMA incremental_vacuum;")
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        after = self.storage()
        return max(0, before["db_bytes"] + before["wal_bytes"] - after["db_bytes"] - after["wal_bytes"])
