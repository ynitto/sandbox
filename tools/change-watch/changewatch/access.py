"""ワークフローから API を使う口（get / commit）。

条件の判定はワークフロー（Dagu）側が持ち、change-watch は取得をまとめて（予定・間隔・取得中の待ち・失敗の待ち）、
「前回 commit してから変わったレコード」を返すだけにする。

    get    --consumer C   取得元のレコードのうち、C が前回 commit した姿から変わったものを返す（commit するまで何度でも返す）
    commit --consumer C   そのレコードを「処理し終えた」と記録する（--refresh なら 1 件取り直した姿で）
    item   KEY            レコードを 1 件取り直す（--max-age の間は問い合わせず、前に取り直した姿で答える）
    call   --record       書き込みの応答を、そのレコードのいまの姿として取得元に入れる（次の取得まで古い姿で答えない）
"""
from __future__ import annotations

from typing import Any

from . import jpath
from .config import SourceSpec
from .fetch import Deferred, Fetcher, load_records
from .store import Store
from .util import ConfigError, TransientError, canonical, key_string, now, pack, sha, unpack, warn

INITIAL = ("baseline", "emit")


def _join(path: str, key: str) -> str:
    if key.replace("_", "").replace("-", "").isalnum():
        return f"{path}.{key}" if path else key
    return f'{path}["{key}"]'


def diff_values(path: str, a: Any, b: Any, out: list) -> None:
    M = jpath.MISSING
    if a is M and b is M:
        return
    if a is M:
        out.append({"path": path or "$", "type": "added", "after": b})
    elif b is M:
        out.append({"path": path or "$", "type": "removed", "before": a})
    elif isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            diff_values(_join(path, k), a.get(k, M), b.get(k, M), out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            diff_values(f"{path}[{i}]", x, y, out)
    elif not jpath.equal(a, b):
        out.append({"path": path or "$", "type": "changed", "before": a, "after": b})


def diff(before: dict | None, after: dict | None) -> list:
    out: list = []
    b = before or {}
    a = after or {}
    for k in sorted(set(b) | set(a)):
        diff_values(k, b.get(k, jpath.MISSING), a.get(k, jpath.MISSING), out)
    return out


def _need_key(src: SourceSpec, what: str) -> None:
    if not src.key:
        raise ConfigError(f"sources.{src.name}.key: {what}には、レコードを一意に指す key を取得元に書いてください")


def key_order(key: str) -> tuple:
    """レコードを返す順。数の key は数の小さい順（9 の次が 10。文字の順だと 10 が 9 より先になり、--one で新しいものを
    先に渡してしまう）、ほかは文字の順で数のあと。"""
    if key.isascii() and key.isdigit():
        return (0, int(key), key)
    return (1, 0, key)


def current(store: Store, src: SourceSpec) -> dict[str, Any]:
    """取得元のいまの姿（key ごとのレコード。key_order の順）。取得し終えたものが無ければ空。"""
    _need_key(src, "変化を見る")
    epoch = int(store.source_row(src.name)["epoch"])
    rows = store.q("SELECT rkey, data FROM source_records WHERE source=? AND epoch=?", (src.name, epoch))
    return {r["rkey"]: unpack(r["data"]) for r in sorted(rows, key=lambda r: key_order(r["rkey"]))}


def records(store: Store, src: SourceSpec) -> list:
    """最後に取得し終えたレコードの一覧（consumer なしの get）。"""
    if src.key:
        return list(current(store, src).values())
    row = store.one("SELECT id FROM fetches WHERE source=? AND status='complete' ORDER BY id DESC LIMIT 1", (src.name,))
    return load_records(store, int(row["id"])) if row else []


def _watched(rec: Any, watch: list[str]) -> dict:
    if not watch:
        return {"$": rec}
    out = {}
    for p in watch:
        v = jpath.get(rec, p)
        out[p] = None if v is jpath.MISSING else v
    return out


def changes(store: Store, src: SourceSpec, consumer: str, watch: list[str], initial: str = "baseline") -> list[dict]:
    """consumer が前回 commit してから変わったレコード。初めて呼ばれたら（取得条件が変わったときも）、initial が
    baseline ならいまの姿を基準に覚えて何も返さず、emit ならいまあるものを全部 appeared として返す。"""
    for p in watch:
        jpath.parse(p)
    have = current(store, src)
    epoch = int(store.source_row(src.name)["epoch"])
    if not store.one("SELECT 1 FROM fetches WHERE source=? AND epoch=? AND status='complete' LIMIT 1", (src.name, epoch)):
        return []            # まだ取得し終えていない（基準も取らない）
    row = store.one("SELECT epoch FROM consumers WHERE consumer=? AND source=?", (consumer, src.name))
    if row is None or int(row["epoch"]) != epoch:
        with store.tx() as db:
            db.execute("DELETE FROM consumer_records WHERE consumer=? AND source=?", (consumer, src.name))
            db.execute("INSERT OR REPLACE INTO consumers(consumer, source, epoch, created_at) VALUES(?,?,?,?)",
                       (consumer, src.name, epoch, now()))
            if initial == "baseline":
                for k, rec in have.items():
                    db.execute("INSERT INTO consumer_records(consumer, source, rkey, data, updated_at) VALUES(?,?,?,?,?)",
                               (consumer, src.name, k, pack(rec), now()))
                return []
    seen = {r["rkey"]: unpack(r["data"]) for r in
            store.q("SELECT rkey, data FROM consumer_records WHERE consumer=? AND source=?", (consumer, src.name))}
    out: list[dict] = []
    for k in sorted(set(have) | set(seen), key=key_order):
        before, after = seen.get(k), have.get(k)
        if before is None:
            out.append({"key": k, "kind": "appeared", "record": after, "changes": []})
        elif after is None:
            out.append({"key": k, "kind": "removed", "record": before, "changes": []})
        else:
            d = diff(_watched(before, watch), _watched(after, watch))
            if d:
                out.append({"key": k, "kind": "changed", "record": after, "before": before,
                            "changes": sorted({c["path"] for c in d})})
    return out


def fetch_item(fetcher: Fetcher, src: SourceSpec, k: str) -> Any:
    """取得元の item でレコードを 1 件取り直す（無ければ None）。key が k でなければ設定の誤り。"""
    spec = src.item
    body = fetcher.get_json(src, spec.url, spec.headers, spec.query, {"key": k}, spec.timeout_sec, spec.method)
    got = None if body is None else jpath.get(body, spec.records)
    if isinstance(got, list):
        got = got[0] if got else None
    if got is None or got is jpath.MISSING:
        return None
    if src.key:
        v = jpath.get(got, src.key)
        if v is jpath.MISSING or key_string([v]) != k:
            raise ConfigError(f"sources.{src.name}.item: 取り直したレコードの key が {k} ではありません")
    return got


def _item_sig(fetcher: Fetcher, src: SourceSpec) -> str:
    conn = fetcher.cfg.resolve_connection(src.connection)
    return sha(canonical({"source": src.fingerprint(conn), "item": src.raw.get("item")}), 32)


def item(fetcher: Fetcher, src: SourceSpec, k: str, max_age: float | None = None) -> tuple[Any, str]:
    """レコードを 1 件取り直して返す（無ければ None）。(レコード, どこから) を返す。どこからは fetched・cached・stale。

    max_age（省略すると取得元の item.max_age_sec）の秒数以内に誰かが取り直していれば、問い合わせずにその姿で答える
    （同じレコードを何度も見に来るワークフローが、API への問い合わせを分け合う）。問い合わせが失敗した・失敗の待ちの中なら、
    前に取り直した姿があればそれで答える（stale）。無ければ失敗をそのまま上げる。"""
    if src.item is None:
        raise ConfigError(f"sources.{src.name}.item: item には、レコードを 1 件取り直す item.url を取得元に書いてください")
    store = fetcher.store
    age = src.item.max_age_sec if max_age is None else max_age
    sig = _item_sig(fetcher, src)
    row = store.one("SELECT sig, data, fetched_at FROM item_cache WHERE source=? AND rkey=?", (src.name, k))
    cached = row is not None and row["sig"] == sig
    if cached and age > 0 and now() - float(row["fetched_at"]) < age:
        return unpack(row["data"]), "cached"
    try:
        got = fetch_item(fetcher, src, k)
    except (Deferred, TransientError) as e:
        if not cached:
            raise
        warn(f"[{src.name}] {k} を取り直せないので、{now() - float(row['fetched_at']):.0f} 秒前に取り直した姿で答えます: {e}")
        return unpack(row["data"]), "stale"
    with store.tx() as db:
        db.execute("INSERT OR REPLACE INTO item_cache(source, rkey, sig, data, fetched_at) VALUES(?,?,?,?,?)",
                   (src.name, k, sig, pack(got), now()))
    return got, "fetched"


def record(store: Store, src: SourceSpec, rec: Any) -> str | None:
    """書き込みの応答 rec を、そのレコードのいまの姿として取得元に入れる（call --record）。入れた key を返す。

    次の取得までのあいだ、get が自分の書き込みの前の姿で答えないようにする。次の取得が全体を取り直せば、その結果に替わる
    （取得条件に合わなくなったレコードは、そこで消える）。前に item で取り直した姿は使わないようにする。"""
    _need_key(src, "call --record")
    if not isinstance(rec, dict):
        return None
    v = jpath.get(rec, src.key or "")
    if v is jpath.MISSING or v is None:
        return None
    k = key_string([v])
    epoch = int(store.source_row(src.name)["epoch"])
    with store.tx() as db:
        db.execute("INSERT INTO source_records(source, rkey, epoch, data, seen_at) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(source, rkey) DO UPDATE SET epoch=excluded.epoch, data=excluded.data, "
                   "seen_at=excluded.seen_at", (src.name, k, epoch, pack(rec), now()))
        db.execute("DELETE FROM item_cache WHERE source=? AND rkey=?", (src.name, k))
    return k


def commit(fetcher: Fetcher, src: SourceSpec, consumer: str, keys: list[str], refresh: bool) -> tuple[list[str], list[str]]:
    """keys を consumer が処理し終えたと記録する。refresh なら取得元の item で 1 件取り直した姿を記録し、
    取得元の姿もそれに替える（処理の中で自分が書き込んだ分を、次の get で変化として返さないため）。
    (記録した key, 取得元にも consumer の記録にも無かった key) を返す。後者は何も記録していない（key の書き間違いに気づけるように）。"""
    store = fetcher.store
    if refresh and src.item is None:
        raise ConfigError(f"sources.{src.name}.item: --refresh には、レコードを 1 件取り直す item.url を取得元に書いてください")
    have = current(store, src)
    epoch = int(store.source_row(src.name)["epoch"])
    seen = {r["rkey"] for r in
            store.q("SELECT rkey FROM consumer_records WHERE consumer=? AND source=?", (consumer, src.name))}
    done: list[str] = []
    unknown: list[str] = []
    for k in keys:
        rec = have.get(k)
        fresh = False
        if refresh:
            got = fetch_item(fetcher, src, k)
            if got is not None:                                   # 取り直せなければ（消えていれば）取得元の姿で記録する
                rec, fresh = got, True
        if rec is None and k not in seen:
            unknown.append(k)
            continue
        with store.tx() as db:
            if rec is None:
                db.execute("DELETE FROM consumer_records WHERE consumer=? AND source=? AND rkey=?", (consumer, src.name, k))
            else:
                db.execute("INSERT OR REPLACE INTO consumer_records(consumer, source, rkey, data, updated_at) "
                           "VALUES(?,?,?,?,?)", (consumer, src.name, k, pack(rec), now()))
                if fresh:
                    db.execute("INSERT INTO source_records(source, rkey, epoch, data, seen_at) VALUES(?,?,?,?,?) "
                               "ON CONFLICT(source, rkey) DO UPDATE SET epoch=excluded.epoch, "
                               "data=excluded.data, seen_at=excluded.seen_at",
                               (src.name, k, epoch, pack(rec), now()))
                    db.execute("DELETE FROM item_cache WHERE source=? AND rkey=?", (src.name, k))
        done.append(k)
    return done, unknown
