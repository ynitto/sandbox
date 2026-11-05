"""判定の単体テスト: パス・条件式・テンプレート・正規化・変更検知の意味（API も Dagu も使わない）。"""
from __future__ import annotations

import unittest

import helpers  # noqa: F401  （sys.path を通す）
from apiwatch import jpath, template
from apiwatch.config import _rule, _source
from apiwatch.detect import RecState, observe
from apiwatch.util import ConfigError


def rule(**kw):
    sources = {"s": _source("s", {"request": {"url": "http://x"}})}
    d = {"source": "s", "id": "id"}
    d.update(kw)
    return _rule("r", d, sources)


def run(r, state, records, fid, full=False, emit=True):
    res = observe(r, state, records, fetch_id=fid, full=full, emit=emit, source="s")
    return [(e["record_id"], e["kinds"]) for e in res.events], res


class PathTest(unittest.TestCase):
    def test_get(self):
        rec = {"a": {"b": [1, {"c": None}]}, "x.y": 3, "labels": [{"name": "bug"}, {"name": "ui"}]}
        self.assertEqual(jpath.get(rec, "a.b[0]"), 1)
        self.assertIsNone(jpath.get(rec, "a.b[1].c"))
        self.assertIs(jpath.get(rec, "a.b[1].d"), jpath.MISSING)
        self.assertIs(jpath.get(rec, "a.b[5]"), jpath.MISSING)
        self.assertEqual(jpath.get(rec, '["x.y"]'), 3)
        self.assertEqual(jpath.get(rec, "$.labels[*].name"), ["bug", "ui"])
        self.assertEqual(jpath.get(rec, "$"), rec)
        with self.assertRaises(ConfigError):
            jpath.parse("a[")

    def test_conditions(self):
        rec = {"state": "open", "n": 3, "labels": ["bug"], "flag": True, "nil": None, "t": "2026-01-02"}
        c = jpath.check
        self.assertTrue(c({"path": "state", "value": "open"}, rec))
        self.assertTrue(c({"path": "n", "op": "ge", "value": 3}, rec))
        self.assertFalse(c({"path": "n", "op": "gt", "value": "2"}, rec))       # 型が違えば比べない
        self.assertTrue(c({"path": "labels", "op": "contains", "value": "bug"}, rec))
        self.assertFalse(c({"path": "flag", "value": 1}, rec))                 # true と 1 は別
        self.assertTrue(c({"path": "nil", "op": "null"}, rec))
        self.assertFalse(c({"path": "nil", "op": "missing"}, rec))
        self.assertTrue(c({"path": "zzz", "op": "missing"}, rec))
        self.assertTrue(c({"path": "zzz", "op": "ne", "value": 1}, rec))
        self.assertTrue(c({"all": [{"path": "state", "op": "in", "value": ["open", "new"]},
                                   {"not": {"path": "t", "op": "lt", "value": "2026-01-01"}}]}, rec))
        self.assertTrue(c({"any": [{"path": "state", "op": "regex", "value": "^cl"}, {"path": "flag"}]}, rec))
        with self.assertRaises(ConfigError):
            jpath.validate_condition({"path": "a", "op": "bogus", "value": 1}, "x")

    def test_template(self):
        ctx = {"conn": {"url": "https://h", "token": "t"}, "position": None, "event": {"after": {"n": 3}}}
        self.assertEqual(template.render("{{ conn.url }}/v1", ctx), "https://h/v1")
        self.assertEqual(template.render("{{ event.after.n }}", ctx), 3)                # 型を保つ
        self.assertEqual(template.render("{{ position | default: 0 }}", ctx), 0)
        self.assertEqual(template.render("q={{ conn.url | urlencode }}", ctx), "q=https%3A%2F%2Fh")
        with self.assertRaises(ConfigError):
            template.render("{{ conn.nothing }}", ctx)


class DetectTest(unittest.TestCase):
    def test_new_and_changed_and_a_b_a(self):
        r = rule(watch=["status"])
        st: dict[str, RecState] = {}
        ev, _ = run(r, st, [{"id": 1, "status": "A"}], 1, emit=False)       # 基準値
        self.assertEqual(ev, [])
        ev, _ = run(r, st, [{"id": 1, "status": "B"}, {"id": 2, "status": "A"}], 2)
        self.assertEqual(ev, [(1, ["changed"]), (2, ["appeared"])])
        ev, _ = run(r, st, [{"id": 1, "status": "A"}, {"id": 2, "status": "A"}], 3)
        self.assertEqual(ev, [(1, ["changed"])])                              # A→B→A をそれぞれ検知

    def test_unwatched_and_key_order_ignored(self):
        r = rule(watch=["status", "meta"])
        st: dict[str, RecState] = {}
        run(r, st, [{"id": 1, "status": "A", "meta": {"a": 1, "b": 2}, "updated": 1}], 1, emit=False)
        ev, _ = run(r, st, [{"updated": 2, "meta": {"b": 2, "a": 1}, "status": "A", "id": 1}], 2)
        self.assertEqual(ev, [])

    def test_whole_record_with_ignore(self):
        r = rule(ignore=["updated", "items[*].seen_at"])
        st: dict[str, RecState] = {}
        run(r, st, [{"id": 1, "x": 1, "updated": 1, "items": [{"v": 1, "seen_at": 1}]}], 1, emit=False)
        ev, _ = run(r, st, [{"id": 1, "x": 1, "updated": 9, "items": [{"v": 1, "seen_at": 9}]}], 2)
        self.assertEqual(ev, [])
        res = observe(r, st, [{"id": 1, "x": 2, "updated": 9, "items": [{"v": 1, "seen_at": 9}]}], fetch_id=3,
                      full=False, emit=True, source="s")
        self.assertEqual(res.events[0]["changes"], [{"path": "x", "type": "changed", "before": 1, "after": 2}])

    def test_array_order(self):
        recs1 = [{"id": 1, "labels": ["a", "b"], "steps": [1, 2]}]
        recs2 = [{"id": 1, "labels": ["b", "a"], "steps": [2, 1]}]
        for kw, expect in (({}, [(1, ["changed"])]),
                           ({"array_order": "ignore"}, []),
                           ({"unordered": ["labels"]}, [(1, ["changed"])])):
            with self.subTest(kw=kw):
                r = rule(**kw)
                st: dict[str, RecState] = {}
                run(r, st, recs1, 1, emit=False)
                ev, _ = run(r, st, recs2, 2)
                self.assertEqual(ev, expect)
        r = rule(unordered=["labels"], watch=["labels"])
        st = {}
        run(r, st, recs1, 1, emit=False)
        self.assertEqual(run(r, st, recs2, 2)[0], [])

    def test_missing_vs_null(self):
        r = rule(watch=["due"])
        st: dict[str, RecState] = {}
        run(r, st, [{"id": 1}], 1, emit=False)
        res = observe(r, st, [{"id": 1, "due": None}], fetch_id=2, full=False, emit=True, source="s")
        self.assertEqual(res.events[0]["changes"], [{"path": "due", "type": "added", "after": None}])
        res = observe(r, st, [{"id": 1}], fetch_id=3, full=False, emit=True, source="s")
        self.assertEqual(res.events[0]["changes"], [{"path": "due", "type": "removed", "before": None}])
        r2 = rule(watch=["due"], missing_is_null=True)
        st2: dict[str, RecState] = {}
        run(r2, st2, [{"id": 1}], 1, emit=False)
        self.assertEqual(run(r2, st2, [{"id": 1, "due": None}], 2)[0], [])

    def test_condition_transitions(self):
        r = rule(condition={"path": "state", "value": "open"}, on=["entered", "exited", "matching"], watch=["state"])
        st: dict[str, RecState] = {}
        run(r, st, [{"id": 1, "state": "closed"}, {"id": 2, "state": "open"}], 1, emit=False)
        ev, _ = run(r, st, [{"id": 1, "state": "open"}, {"id": 2, "state": "open"}], 2)
        self.assertEqual(ev, [(1, ["changed", "entered", "matching"]), (2, ["matching"])])
        ev, _ = run(r, st, [{"id": 1, "state": "closed"}, {"id": 2, "state": "open"}, {"id": 3, "state": "open"}], 3)
        self.assertEqual(ev, [(1, ["changed", "exited"]), (2, ["matching"]),
                              (3, ["appeared", "entered", "matching"])])
        only_entered = rule(condition={"path": "state", "value": "open"}, on=["entered"])
        st = {}
        run(only_entered, st, [{"id": 1, "state": "open"}], 1, emit=False)
        self.assertEqual(run(only_entered, st, [{"id": 1, "state": "open", "x": 1}], 2)[0], [])

    def test_filter_and_removed(self):
        r = rule(filter={"path": "kind", "value": "bug"}, on=["appeared", "changed", "removed"])
        st: dict[str, RecState] = {}
        run(r, st, [{"id": 1, "kind": "bug"}, {"id": 2, "kind": "bug"}, {"id": 3, "kind": "task"}], 1, full=True,
            emit=False)
        # 3 は対象外。1 は消えた（全件取得）。2 が対象外になっても「消えた」ではない
        ev, _ = run(r, st, [{"id": 2, "kind": "task"}, {"id": 3, "kind": "task", "x": 1}], 2, full=True)
        self.assertEqual(ev, [(1, ["removed"])])
        # 増分取得では消えたと見なさない
        ev, _ = run(r, st, [], 3, full=False)
        self.assertEqual(ev, [])
        ev, _ = run(r, st, [{"id": 1, "kind": "bug"}, {"id": 2, "kind": "task"}], 4, full=True)
        self.assertEqual(ev, [(1, ["appeared"])])        # 消えたあとにまた現れた
        ev, _ = run(r, st, [{"id": 1, "kind": "bug"}], 5, full=True)
        self.assertEqual(ev, [])                          # 対象外のまま消えた 2 では発火しない
        ev, _ = run(r, st, [{"id": 1, "kind": "bug"}, {"id": 2, "kind": "bug"}], 6, full=True)
        self.assertEqual(ev, [(2, ["appeared"])])        # 対象に戻ってきた

    def test_composite_id_and_missing_id(self):
        r = rule(id=["project", "iid"], watch=["t"])
        st: dict[str, RecState] = {}
        ev, res = run(r, st, [{"project": "a", "iid": 1, "t": 1}, {"project": "b", "iid": 1, "t": 1}, {"iid": 2}], 1)
        self.assertEqual(ev, [(["a", 1], ["appeared"]), (["b", 1], ["appeared"])])
        self.assertEqual(res.no_id, 1)

    def test_event_ids_are_stable(self):
        r = rule(watch=["s"])
        st1: dict = {}
        st2: dict = {}
        run(r, st1, [{"id": 1, "s": 1}], 1, emit=False)
        run(r, st2, [{"id": 1, "s": 1}], 1, emit=False)
        e1 = observe(r, st1, [{"id": 1, "s": 2}], fetch_id=2, full=False, emit=True, source="s").events
        e2 = observe(r, st2, [{"id": 1, "s": 2}], fetch_id=2, full=False, emit=True, source="s").events
        self.assertEqual(e1[0]["event_id"], e2[0]["event_id"])
        other = rule(watch=["s", "t"])
        self.assertNotEqual(other.fingerprint(), r.fingerprint())


class ConfigTest(unittest.TestCase):
    def test_rejects(self):
        sources = {"s": _source("s", {"request": {"url": "http://x"}})}
        bad = [
            {"source": "nope", "id": "id"},
            {"source": "s"},
            {"source": "s", "id": "id", "on": ["entered"]},
            {"source": "s", "id": "id", "on": ["bogus"]},
            {"source": "s", "id": "id", "targets": [{"workflow": "a", "order": "random"}]},
            {"source": "s", "id": "id", "targets": [{"workflow": "a", "params": {"X": "{{ conn.token }}"}}]},
            {"source": "s", "id": "id", "typo": 1},
        ]
        for d in bad:
            with self.subTest(d=d), self.assertRaises(ConfigError):
                _rule("r", d, sources)
        with self.assertRaises(ConfigError):
            _source("s", {"request": {"url": "http://x"}, "pagination": {"type": "cursor"}})
        with self.assertRaises(ConfigError):
            _source("s", {"request": {"url": "http://x"}, "pagination": {"type": "offset"}})

    def test_fingerprint_tracks_credentials(self):
        s = _source("s", {"request": {"url": "http://x"}, "connection": "svc"})
        self.assertNotEqual(s.fingerprint({"token": "a"}), s.fingerprint({"token": "b"}))
        s2 = _source("s", {"request": {"url": "http://x"}, "connection": "svc", "schedule": "* * * * *"})
        self.assertEqual(s.fingerprint({"token": "a"}), s2.fingerprint({"token": "a"}))   # 間隔は取得条件ではない


if __name__ == "__main__":
    unittest.main()
