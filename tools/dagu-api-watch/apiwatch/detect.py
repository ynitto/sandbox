"""ルールごとの判定（変更検知）。

ルールは取得元の「完了した取得」を古い順に 1 つずつ判定し、どこまで判定したかを rules.last_fetch_id に持つ。
1 つの取得の判定（観測値の更新・イベントと受け渡しの追加・位置の前進）は 1 トランザクションで書く。
だから判定が途中で止まっても、次は同じ取得から判定し直すだけで、変更を落とさず二重にもしない。
取得を古い順に全部見るので、A→B→A と観測すれば 2 回の変更として検知する。

1 レコードの 1 回の観測から出る種類:
    appeared  初めて見た ID（消えたあと・filter から外れたあとにまた現れたときも）
    changed   監視する値が前回と違う
    entered   条件に一致していなかった（または初めて見た）→ 一致
    exited    一致 → 不一致（全件取得で消えたときも）
    matching  条件に一致している（観測のたび）
    removed   全件取得で見当たらなくなった
ルールの on に含まれる種類が 1 つでもあれば、その観測を 1 件のイベントにする。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from . import jpath
from .config import Config, RuleSpec, SourceSpec
from .fetch import load_records
from .store import PENDING, Store
from .util import REDACT, canonical, iso, key_string, log, now, pack, sha, unpack

WHOLE = ""   # watch を書かないとき、レコード全体を 1 つの値として監視する


# ---------------------------------------------------------------------------
# 正規化と差分
# ---------------------------------------------------------------------------

def _sort_arrays(v: Any) -> Any:
    if isinstance(v, list):
        items = [_sort_arrays(x) for x in v]
        return sorted(items, key=canonical)
    if isinstance(v, dict):
        return {k: _sort_arrays(x) for k, x in v.items()}
    return v


def _sort_here(v: Any) -> Any:
    return sorted(v, key=canonical) if isinstance(v, list) else v


def normalize(rule: RuleSpec, rec: Any) -> Any:
    """監視対象外の項目を除き、順序を問わない配列を並べ替えたコピー。キー順は比較時に無視される。"""
    r = copy.deepcopy(rec)
    for p in rule.ignore:
        jpath.delete(r, p)
    if rule.array_order == "ignore":
        r = _sort_arrays(r)
    else:
        for p in rule.unordered:
            r = jpath.transform(r, p, _sort_here)
    return r


def watched(rule: RuleSpec, norm: Any) -> dict:
    """監視する値。値が無いパスは辞書に入れない（null とは区別する）。"""
    if rule.watch is None:
        return {WHOLE: norm}
    out: dict[str, Any] = {}
    for p in rule.watch:
        v = jpath.get(norm, p)
        if v is jpath.MISSING:
            if rule.missing_is_null:
                out[p] = None
            continue
        out[p] = v
    return out


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


def present_value(rule: RuleSpec, w: dict | None) -> Any:
    if w is None:
        return None
    if rule.watch is None:
        return w.get(WHOLE)
    return w


# ---------------------------------------------------------------------------
# 1 回の観測の判定
# ---------------------------------------------------------------------------


@dataclass
class RecState:
    present: bool
    matched: bool | None
    vhash: str
    value: dict
    last_fetch_id: int | None = None
    dirty: bool = False
    ids: list | None = None


@dataclass
class Result:
    events: list = field(default_factory=list)
    seen: int = 0
    in_scope: int = 0
    no_id: int = 0


def event_id(rule: RuleSpec, rkey: str, fetch_id: int) -> str:
    return "ev_" + sha(f"{rule.name}\x00{rule.fingerprint()}\x00{rkey}\x00{fetch_id}", 24)


def delivery_id(event: str, target: str) -> str:
    # Dagu の実行 ID（英数字・- _、64 文字以内）にそのまま使う
    return "aw_" + sha(f"{event}\x00{target}", 24)


def observe(rule: RuleSpec, state: dict[str, RecState], records: list, *, fetch_id: int, full: bool, emit: bool,
            source: str) -> Result:
    """records を 1 回の観測として state を更新し、発火するイベントを返す。emit=False なら基準値の保存だけ。"""
    res = Result()
    on = set(rule.on)
    t = now()
    all_keys: set[str] = set()
    for rec in records:
        ids = [jpath.get(rec, p) for p in rule.id_paths]
        if any(v is jpath.MISSING or v is None for v in ids):
            res.no_id += 1
            continue
        rkey = key_string(ids)
        all_keys.add(rkey)
        res.seen += 1
        if rule.filter is not None and not jpath.check(rule.filter, rec):
            prev = state.get(rkey)
            if prev is not None and prev.present:   # 対象から外れた。戻ってきたら appeared として扱う
                state[rkey] = RecState(False, None, prev.vhash, prev.value, fetch_id, dirty=True, ids=prev.ids)
            continue
        res.in_scope += 1
        norm = normalize(rule, rec)
        w = watched(rule, norm)
        h = sha(canonical(w), 40)
        m = jpath.check(rule.condition, rec) if rule.condition is not None else None
        prev = state.get(rkey)
        was_present = prev is not None and prev.present
        kinds = []
        if not was_present:
            kinds.append("appeared")
        elif prev.vhash != h:
            kinds.append("changed")
        if rule.condition is not None:
            prev_m = bool(prev.matched) if was_present else False
            if m and not prev_m:
                kinds.append("entered")
            if prev_m and not m:
                kinds.append("exited")
            if m:
                kinds.append("matching")
        if emit and on.intersection(kinds):
            res.events.append(_event(rule, source, rkey, ids, fetch_id, t, kinds,
                                     prev.value if was_present else None, w,
                                     prev.matched if was_present else None, m, rec))
        if prev is None or not prev.present or prev.vhash != h or prev.matched != m:
            state[rkey] = RecState(True, m, h, w, fetch_id, dirty=True, ids=ids)
    if full:
        for rkey, prev in list(state.items()):
            if prev.present and rkey not in all_keys:
                kinds = ["removed"]
                if prev.matched:
                    kinds.append("exited")
                if emit and on.intersection(kinds):
                    res.events.append(_event(rule, source, rkey, prev.ids or [rkey], fetch_id, t, kinds, prev.value,
                                             None, prev.matched, None, None))
                state[rkey] = RecState(False, None, "", {}, fetch_id, dirty=True, ids=prev.ids)
    return res


def _event(rule: RuleSpec, source: str, rkey: str, ids: list, fetch_id: int, t: float, kinds: list,
           before: dict | None, after: dict | None, m_before: Any, m_after: Any, rec: Any) -> dict:
    eid = event_id(rule, rkey, fetch_id)
    return {
        "event_id": eid,
        "source": source,
        "rule": rule.name,
        "record_key": rkey,
        "record_id": ids[0] if len(ids) == 1 else ids,
        "kinds": kinds,
        "fetch_id": fetch_id,
        "observed_at": iso(t),
        "before": present_value(rule, before),
        "after": present_value(rule, after),
        "changes": diff(before, after),
        "matched": {"before": m_before, "after": m_after} if rule.condition is not None else None,
        "record": rec if rule.include_record else None,
    }


# ---------------------------------------------------------------------------
# 永続化を伴う判定
# ---------------------------------------------------------------------------


@dataclass
class RuleOutcome:
    rule: str
    status: str            # ok / baseline / idle / error / busy
    fetches: int = 0
    events: int = 0
    message: str = ""


class Evaluator:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store

    def _load_state(self, rule: str) -> dict[str, RecState]:
        out: dict[str, RecState] = {}
        for r in self.store.q("SELECT * FROM rule_records WHERE rule=?", (rule,)):
            v = unpack(r["value"]) or {}
            out[r["rkey"]] = RecState(bool(r["present"]), None if r["matched"] is None else bool(r["matched"]),
                                      r["vhash"] or "", v.get("w") or {}, r["last_fetch_id"], ids=v.get("id"))
        return out

    def _complete_fetches(self, source: str, epoch: int, after: int | None = None) -> list:
        return self.store.q("SELECT id, mode FROM fetches WHERE source=? AND epoch=? AND status='complete' "
                            "AND id>? ORDER BY id", (source, epoch, after or 0))

    def _baseline_records(self, src: SourceSpec, latest_id: int, epoch: int) -> tuple[list, bool]:
        """基準値に使うレコード。増分取得で key があれば、組み立てた全体の姿を使う。"""
        if src.incremental is not None and src.key:
            rows = self.store.q("SELECT data FROM source_records WHERE source=? AND epoch=? ORDER BY rkey",
                                (src.name, epoch))
            return [unpack(r["data"]) for r in rows], False
        return load_records(self.store, latest_id), False

    def _persist(self, rule: RuleSpec, state: dict[str, RecState], events: list, *, reset: bool, last_fetch_id: int,
                 epoch: int, baseline: bool) -> int:
        added = 0
        with self.store.tx() as db:
            if reset:
                db.execute("DELETE FROM rule_records WHERE rule=?", (rule.name,))
            for rkey, s in state.items():
                if not (s.dirty or reset):
                    continue
                db.execute("INSERT OR REPLACE INTO rule_records(rule, rkey, present, matched, vhash, value, "
                           "last_fetch_id, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                           (rule.name, rkey, 1 if s.present else 0, None if s.matched is None else int(s.matched),
                            s.vhash, pack({"w": s.value, "id": s.ids}), s.last_fetch_id, now()))
                s.dirty = False
            for ev in events:
                cur = db.execute("INSERT OR IGNORE INTO events(id, rule, source, rkey, fetch_id, kinds, created_at, "
                                 "payload) VALUES(?,?,?,?,?,?,?,?)",
                                 (ev["event_id"], rule.name, ev["source"], ev["record_key"], ev["fetch_id"],
                                  ",".join(ev["kinds"]), now(), pack(ev)))
                if cur.rowcount == 0:
                    continue
                added += 1
                seq = cur.lastrowid
                for t in rule.targets:
                    db.execute("INSERT OR IGNORE INTO deliveries(id, seq, event_id, rule, target, dag, rkey, status, "
                               "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (delivery_id(ev["event_id"], t.name), seq, ev["event_id"], rule.name, t.name, t.dag,
                                ev["record_key"], PENDING, now(), now()))
            db.execute("INSERT INTO rules(name, source, fingerprint, epoch, last_fetch_id, baseline_fetch_id, "
                       "last_eval_at, last_error, last_events, needs_rebaseline) VALUES(?,?,?,?,?,?,?,NULL,?,0) "
                       "ON CONFLICT(name) DO UPDATE SET source=excluded.source, fingerprint=excluded.fingerprint, "
                       "epoch=excluded.epoch, last_fetch_id=excluded.last_fetch_id, "
                       "baseline_fetch_id=COALESCE(?, rules.baseline_fetch_id), last_eval_at=excluded.last_eval_at, "
                       "last_error=NULL, last_events=excluded.last_events, needs_rebaseline=0",
                       (rule.name, rule.source, rule.fingerprint(), epoch, last_fetch_id,
                        last_fetch_id if baseline else None, now(), added, last_fetch_id if baseline else None))
        return added

    def _record_error(self, rule: RuleSpec, msg: str) -> None:
        with self.store.tx() as db:
            db.execute("INSERT INTO rules(name, source, last_error, last_eval_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(name) DO UPDATE SET last_error=excluded.last_error, "
                       "last_eval_at=excluded.last_eval_at", (rule.name, rule.source, msg, now()))

    def evaluate_source(self, source: str, only: list[str] | None = None) -> list[RuleOutcome]:
        src = self.cfg.sources[source]
        rules = [r for r in self.cfg.rules_for(source) if only is None or r.name in only]
        if not rules:
            return []
        lease = f"eval:{source}"
        if not self.store.acquire(lease, 600):
            return [RuleOutcome(r.name, "busy", message="別のプロセスが判定中") for r in rules]
        try:
            out = []
            for r in rules:
                try:
                    out.append(self.evaluate_rule(src, r, lease))
                except Exception as e:  # noqa: BLE001  1 つのルールの失敗で他のルールを止めない
                    msg = REDACT(f"{type(e).__name__}: {e}")
                    self._record_error(r, msg)
                    out.append(RuleOutcome(r.name, "error", message=msg))
            return out
        finally:
            self.store.release(lease)

    def evaluate_rule(self, src: SourceSpec, rule: RuleSpec, lease: str | None = None) -> RuleOutcome:
        srow = self.store.source_row(src.name)
        epoch = int(srow["epoch"])
        rrow = self.store.one("SELECT * FROM rules WHERE name=?", (rule.name,))
        fetches = self._complete_fetches(src.name, epoch)
        if not fetches:
            return RuleOutcome(rule.name, "idle", message="判定できる取得結果がまだありません")
        latest = fetches[-1]
        fp = rule.fingerprint()
        never = rrow is None or rrow["last_fetch_id"] is None
        epoch_changed = not never and rrow["epoch"] != epoch
        fp_changed = not never and rrow["fingerprint"] != fp
        forced = rrow is not None and bool(rrow["needs_rebaseline"])
        if rrow is not None and rrow["source"] != src.name and not never:
            epoch_changed = True
        if never or epoch_changed or forced or (fp_changed and rule.on_rule_change == "rebaseline"):
            emit = never and rule.initial == "emit"
            records, _ = self._baseline_records(src, int(latest["id"]), epoch)
            state: dict[str, RecState] = {}
            res = observe(rule, state, records, fetch_id=int(latest["id"]), full=False, emit=emit, source=src.name)
            added = self._persist(rule, state, res.events, reset=True, last_fetch_id=int(latest["id"]), epoch=epoch,
                                  baseline=True)
            why = ("初回" if never else "取得条件が変わった" if epoch_changed else
                   "基準値の取り直しを指示された" if forced else "ルールが変わった")
            msg = f"{why}ので、取得 #{latest['id']} の {res.in_scope} 件を基準値にしました"
            if emit:
                msg += f"（既存のレコードを新規として {added} 件のイベントにしました）"
            return RuleOutcome(rule.name, "baseline", fetches=1, events=added, message=msg)
        state = self._load_state(rule.name)
        total = 0
        done = 0
        for f in self._complete_fetches(src.name, epoch, rrow["last_fetch_id"]):
            records = load_records(self.store, int(f["id"]))
            res = observe(rule, state, records, fetch_id=int(f["id"]), full=f["mode"] == "full", emit=True,
                          source=src.name)
            total += self._persist(rule, state, res.events, reset=False, last_fetch_id=int(f["id"]), epoch=epoch,
                                   baseline=False)
            done += 1
            if lease:
                self.store.renew(lease, 600)
        if done == 0:
            if fp_changed:   # on_rule_change: continue（指紋だけ更新する）
                with self.store.tx() as db:
                    db.execute("UPDATE rules SET fingerprint=? WHERE name=?", (fp, rule.name))
            return RuleOutcome(rule.name, "idle", message="新しい取得結果はありません")
        return RuleOutcome(rule.name, "ok", fetches=done, events=total, message=f"{done} 回分を判定し {total} 件のイベント")

    # -- 保存済みの取得結果で判定し直す ---------------------------------------

    def replay(self, rule: RuleSpec, from_fetch: int | None, dry_run: bool, emit_first: bool) -> tuple[list, str]:
        src = self.cfg.sources[rule.source]
        epoch = int(self.store.source_row(src.name)["epoch"])
        fetches = [f for f in self._complete_fetches(src.name, epoch) if from_fetch is None or f["id"] >= from_fetch]
        if not fetches:
            return [], "保存済みの取得結果がありません（保持期間を過ぎたか、まだ取得していません）"
        lease = f"eval:{src.name}"
        if not dry_run and not self.store.acquire(lease, 600):
            return [], "別のプロセスが判定中です。しばらくしてからやり直してください"
        try:
            state: dict[str, RecState] = {}
            events: list = []
            for i, f in enumerate(fetches):
                records = load_records(self.store, int(f["id"]))
                res = observe(rule, state, records, fetch_id=int(f["id"]), full=(i > 0 and f["mode"] == "full"),
                              emit=(i > 0 or emit_first), source=src.name)
                events.extend(res.events)
            msg = (f"取得 #{fetches[0]['id']}〜#{fetches[-1]['id']}（{len(fetches)} 回）を判定し直しました: "
                   f"{len(events)} 件のイベント")
            if dry_run:
                return events, msg + "（試し。状態は変えていません）"
            added = self._persist(rule, state, events, reset=True, last_fetch_id=int(fetches[-1]["id"]), epoch=epoch,
                                  baseline=False)
            return events, msg + f"。新しく積んだのは {added} 件（受け渡し済みのものは同じイベント ID なので積みません）"
        finally:
            if not dry_run:
                self.store.release(lease)

    def request_rebaseline(self, rules: list[RuleSpec]) -> None:
        with self.store.tx() as db:
            for r in rules:
                db.execute("INSERT INTO rules(name, source, needs_rebaseline) VALUES(?,?,1) "
                           "ON CONFLICT(name) DO UPDATE SET needs_rebaseline=1", (r.name, r.source))


def log_outcomes(outcomes: list[RuleOutcome]) -> None:
    for o in outcomes:
        log(f"[{o.rule}] {o.status}: {o.message}")
