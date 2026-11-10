"""ワークフローから API を使う口（get / commit）。

条件の判定はワークフロー（Dagu）側が持ち、change-watch は取得をまとめて（予定・間隔・取得中の待ち・失敗の待ち）、
「前回 commit してから変わったレコード」を返すだけにする。

    get    --consumer C   取得元のレコードのうち、C が前回 commit した姿から変わったものを返す（commit するまで何度でも返す）
    commit --consumer C   そのレコードを「処理し終えた」と記録する（--refresh なら 1 件取り直した姿で）
"""
from __future__ import annotations

from typing import Any

from . import jpath
from .config import SourceSpec
from .fetch import Fetcher, load_records
from .store import Store
from .util import ConfigError, key_string, now, pack, unpack

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


def current(store: Store, src: SourceSpec) -> dict[str, Any]:
    """取得元のいまの姿（key ごとのレコード）。取得し終えたものが無ければ空。"""
    _need_key(src, "変化を見る")
    epoch = int(store.source_row(src.name)["epoch"])
    rows = store.q("SELECT rkey, data FROM source_records WHERE source=? AND epoch=? ORDER BY rkey", (src.name, epoch))
    return {r["rkey"]: unpack(r["data"]) for r in rows}


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
    for k in sorted(set(have) | set(seen)):
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


def commit(fetcher: Fetcher, src: SourceSpec, consumer: str, keys: list[str], refresh: bool) -> list[str]:
    """keys を consumer が処理し終えたと記録する。refresh なら取得元の item で 1 件取り直した姿を記録し、
    取得元の姿もそれに替える（処理の中で自分が書き込んだ分を、次の get で変化として返さないため）。"""
    store = fetcher.store
    if refresh and src.item is None:
        raise ConfigError(f"sources.{src.name}.item: --refresh には、レコードを 1 件取り直す item.url を取得元に書いてください")
    have = current(store, src)
    epoch = int(store.source_row(src.name)["epoch"])
    done: list[str] = []
    for k in keys:
        rec = have.get(k)
        fresh = False
        if refresh:
            spec = src.item
            body = fetcher.get_json(src, spec.url, spec.headers, spec.query, {"key": k}, spec.timeout_sec, spec.method)
            got = None if body is None else jpath.get(body, spec.records)
            if isinstance(got, list):
                got = got[0] if got else None
            if got is not None and got is not jpath.MISSING:      # 取り直せなければ（消えていれば）取得元の姿で記録する
                v = jpath.get(got, src.key or "")
                if v is jpath.MISSING or key_string([v]) != k:
                    raise ConfigError(f"sources.{src.name}.item: 取り直したレコードの key が {k} ではありません")
                rec, fresh = got, True
        with store.tx() as db:
            if rec is None:
                db.execute("DELETE FROM consumer_records WHERE consumer=? AND source=? AND rkey=?", (consumer, src.name, k))
            else:
                db.execute("INSERT OR REPLACE INTO consumer_records(consumer, source, rkey, data, updated_at) "
                           "VALUES(?,?,?,?,?)", (consumer, src.name, k, pack(rec), now()))
                if fresh:
                    db.execute("UPDATE source_records SET data=?, seen_at=? WHERE source=? AND rkey=? AND epoch=?",
                               (pack(rec), now(), src.name, k, epoch))
        done.append(k)
    return done
