"""get・commit・call（条件の判定はワークフロー側が持ち、change-watch は API へのアクセスだけを受け持つ使い方）。"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from helpers import Env
from changewatch import access, config
from changewatch.fetch import Fetcher
from changewatch.store import Store


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)
        self.env.write_config({"items": self.env.source("/all", key="id",
                                                        item={"url": "{{ conn.url }}/item/{{ key }}"})})

    def get(self, *args: str):
        self.env.ok(self, "get", "--source", "items", "--no-jitter", *args)
        out = self.env.stdout.strip()
        return json.loads(out) if out else None

    def test_records_without_consumer(self):
        self.env.api.put(1, status="a")
        self.env.api.put(2, status="b")
        self.assertEqual([r["id"] for r in self.get()], [1, 2])

    def test_changes_until_commit(self):
        self.env.api.put(1, status="a", note="x")
        self.assertEqual(self.get("--consumer", "flow"), [])               # 初回は今あるものを覚えるだけ
        self.env.api.put(1, status="b")
        self.env.api.put(2, status="a")
        got = self.get("--consumer", "flow", "--watch", "status")
        self.assertEqual([(c["key"], c["kind"]) for c in got], [("1", "changed"), ("2", "appeared")])
        self.assertEqual(got[0]["changes"], ["status"])
        self.assertEqual(got[0]["before"]["status"], "a")
        # commit するまで同じ変化を返す（処理が失敗したら次の起動でもう一度）
        self.assertEqual(self.get("--consumer", "flow", "--watch", "status", "--one")["key"], "1")
        self.env.ok(self, "commit", "--source", "items", "--consumer", "flow", "1")
        self.assertEqual(self.get("--consumer", "flow", "--watch", "status", "--one")["key"], "2")
        self.env.ok(self, "commit", "--source", "items", "--consumer", "flow", "2")
        self.assertIsNone(self.get("--consumer", "flow", "--one"))            # 無ければ何も出さない
        self.env.api.put(1, note="y")                                         # 見ていない項目だけの変化
        self.assertEqual(self.get("--consumer", "flow", "--watch", "status"), [])
        self.env.api.drop(2)
        self.assertEqual([(c["key"], c["kind"]) for c in self.get("--consumer", "flow", "--watch", "status")],
                         [("2", "removed")])

    def test_consumers_are_independent_and_share_one_fetch(self):
        self.env.api.put(1, status="a")
        self.get("--consumer", "a")
        self.get("--consumer", "b", "--initial", "emit")
        self.env.ok(self, "commit", "--source", "items", "--consumer", "b", "1")
        self.env.api.put(1, status="b")
        self.assertEqual(len(self.get("--consumer", "a")), 1)
        self.assertEqual(len(self.get("--consumer", "b", "--no-fetch")), 1)   # 取得せず、保存済みの結果で答える
        self.assertEqual(len(self.get("--consumer", "a", "--no-fetch")), 1)

    def test_emit_returns_everything_first(self):
        self.env.api.put(1, status="a")
        self.assertEqual([c["kind"] for c in self.get("--consumer", "flow", "--initial", "emit")], ["appeared"])

    def test_commit_refresh_absorbs_own_write(self):
        self.env.api.put(1, status="a", notes=0)
        self.get("--consumer", "flow")
        self.env.api.put(1, status="b")
        self.assertEqual(self.get("--consumer", "flow", "--one")["key"], "1")
        self.env.api.put(1, notes=1)                                          # 処理の中で自分がコメントした
        self.env.ok(self, "commit", "--source", "items", "--consumer", "flow", "--refresh", "1")
        self.assertIsNone(self.get("--consumer", "flow", "--no-fetch", "--one"))
        self.assertIsNone(self.get("--consumer", "flow", "--one"))            # 取り直した姿を基準にした

    def test_refresh_needs_item(self):
        self.env.write_config({"items": self.env.source("/all", key="id")})
        self.env.api.put(1, status="a")
        self.get("--consumer", "flow")
        code, out = self.env.run("commit", "--source", "items", "--consumer", "flow", "--refresh", "1")
        self.assertNotEqual(code, 0)
        self.assertIn("item", out)

    def test_fetch_failure_answers_from_saved_records(self):
        self.env.api.put(1, status="a")
        self.get("--consumer", "flow")
        self.env.api.put(1, status="b")
        self.env.api.fail("/all", 500, count=5)
        self.assertEqual(self.get("--consumer", "flow"), [])                 # 前回までの取得結果で答える（止めない）

    def test_call_writes_with_source_auth(self):
        self.env.ok(self, "call", "POST", "/_put", "--source", "items", "--json", '{"id": 3, "status": "done"}')
        self.assertEqual(self.env.api.items[3]["status"], "done")
        path, _, headers = self.env.api.requests[-1]
        self.assertEqual(path, "/_put")
        self.assertTrue(headers["Authorization"].startswith("Bearer "))
        self.env.ok(self, "call", "GET", "{{ conn.url }}/item/3", "--source", "items")
        self.assertEqual(json.loads(self.env.stdout)["status"], "done")

    def test_call_does_not_retry_writes(self):
        self.env.api.fail("/_put", 503, count=1)
        code, _ = self.env.run("call", "POST", "/_put", "--source", "items", "--json", '{"id": 3}')
        self.assertEqual(code, 3)
        self.assertEqual(self.env.api.count("/_put"), 1)

    def test_numeric_keys_come_in_numeric_order(self):
        self.env.api.put(1, status="a")
        self.get("--consumer", "flow")
        for i in (10, 9, 100):
            self.env.api.put(i, status="a")
        self.assertEqual([c["key"] for c in self.get("--consumer", "flow")], ["9", "10", "100"])
        self.assertEqual(self.get("--consumer", "flow", "--one")["key"], "9")       # 古いものから 1 件
        self.assertEqual([r["id"] for r in self.get()], [1, 9, 10, 100])

    def test_commit_warns_about_unknown_keys(self):
        self.env.api.put(1, status="a")
        self.get("--consumer", "flow", "--initial", "emit")
        code, out = self.env.run("commit", "--source", "items", "--consumer", "flow", "1 ", "nope")
        self.assertEqual(code, 0)
        self.assertIn("'1 '", out)
        self.assertIn("'nope'", out)
        self.assertNotIn("記録しました", out)
        self.assertEqual(self.get("--consumer", "flow", "--one")["key"], "1")       # 記録していない
        out = self.env.ok(self, "commit", "--source", "items", "--consumer", "flow", "1")
        self.assertIn("記録しました: 1", out)
        self.env.api.drop(1)
        self.assertEqual(self.get("--consumer", "flow", "--one")["kind"], "removed")
        out = self.env.ok(self, "commit", "--source", "items", "--consumer", "flow", "1")   # 消えたレコードは記録できる
        self.assertIn("記録しました: 1", out)

    def test_commit_refresh_records_a_record_not_fetched_yet(self):
        self.get("--consumer", "flow")
        self.env.api.put(5, status="a")                                       # まだ取得していないが、API にはある
        out = self.env.ok(self, "commit", "--source", "items", "--consumer", "flow", "--refresh", "5")
        self.assertIn("記録しました: 5", out)
        self.assertEqual(self.get("--consumer", "flow", "--no-fetch"), [])
        self.assertEqual([r["id"] for r in self.get("--no-fetch")], [5])
        code, out = self.env.run("commit", "--source", "items", "--consumer", "flow", "--refresh", "6")
        self.assertIn("'6'", out)

    def _check_refresh_error(self, status, expected):
        self.env.api.put(1, status="a")
        self.get("--consumer", "flow")
        self.env.api.fail("/item/1", status, count=5)
        code, out = self.env.run("commit", "--source", "items", "--consumer", "flow", "--refresh", "1")
        self.assertEqual(code, expected)
        self.assertIn(str(status), out)

    def test_commit_refresh_reports_permanent_http_error(self):
        self._check_refresh_error(403, 1)

    def test_commit_refresh_reports_transient_http_error(self):
        self._check_refresh_error(503, 3)

    def test_call_returns_non_json_response_as_text(self):
        self.env.api.fail("/_put", 200, body="OK")
        self.env.ok(self, "call", "POST", "/_put", "--source", "items", "--json", '{"id": 3}')
        self.assertEqual(json.loads(self.env.stdout), "OK")

    def test_plugin_filter_error_is_a_config_error(self):
        plugin = self.env.tmp / "plug.py"
        plugin.write_text("def boom(value, arg=None):\n    raise ValueError(f'bad value: {value!r}')\n\n"
                          "def register(api):\n    api.add_filter('boom', boom)\n", encoding="utf-8")
        self.env.plugins = [str(plugin)]
        code, out = self.env.run("call", "GET", "{{ env.CHANGEWATCH_TEST_UNSET | boom }}/x", "--source", "items")
        self.assertEqual(code, 1)
        self.assertIn("フィルタ boom: bad value: None", out)

    def test_folder_source(self):
        inbox = self.env.tmp / "inbox"
        inbox.mkdir()
        (inbox / "a.csv").write_text("1", encoding="utf-8")
        self.env.write_config({"inbox": {"folder": {"path": str(inbox), "include": ["*.csv"], "settle_sec": 0}}})
        run = lambda: json.loads(self.env.ok(self, "get", "--source", "inbox", "--consumer", "flow") and self.env.stdout)
        self.assertEqual(run(), [])                                            # 今あるファイルを覚えるだけ
        (inbox / "b.csv").write_text("2", encoding="utf-8")
        got = run()
        self.assertEqual([(c["key"], c["kind"]) for c in got], [("b.csv", "appeared")])
        self.env.ok(self, "commit", "--source", "inbox", "--consumer", "flow", "b.csv")
        self.assertEqual(run(), [])

    def test_key_is_required_for_consumers(self):
        self.env.write_config({"items": self.env.source("/all")})
        code, out = self.env.run("get", "--source", "items", "--consumer", "flow")
        self.assertNotEqual(code, 0)
        self.assertIn("key", out)

    # -- item（1 件の取り直しを分け合う）と call --record（自分の書き込みを手元の姿に入れる） --

    def item(self, key: str, *args: str):
        self.env.ok(self, "item", "--source", "items", key, *args)
        return json.loads(self.env.stdout)

    def test_item_shares_one_request_within_max_age(self):
        self.env.api.put(1, status="a")
        self.assertEqual(self.item("1", "--max-age", "300")["status"], "a")
        self.env.api.put(1, status="b")
        self.assertEqual(self.item("1", "--max-age", "300")["status"], "a")      # max-age の内は問い合わせない
        self.assertEqual(self.env.api.count("/item/1"), 1)
        self.assertEqual(self.item("1", "--max-age", "0")["status"], "b")        # 0 なら毎回問い合わせる
        self.assertEqual(self.item("1")["status"], "b")                          # 既定は item.max_age_sec（0）
        self.assertEqual(self.env.api.count("/item/1"), 3)
        self.assertIsNone(self.item("9"))                                         # 無ければ null

    def test_item_max_age_from_config(self):
        self.env.write_config({"items": self.env.source(
            "/all", key="id", item={"url": "{{ conn.url }}/item/{{ key }}", "max_age_sec": 300})})
        self.env.api.put(1, status="a")
        self.item("1")
        self.item("1")
        self.assertEqual(self.env.api.count("/item/1"), 1)

    def test_item_answers_from_cache_when_api_fails(self):
        self.env.api.put(1, status="a")
        self.item("1")
        self.env.api.fail("/item/1", 503, count=5)
        code, out = self.env.run("item", "--source", "items", "1")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(self.env.stdout)["status"], "a")
        self.assertIn("前に取り直した姿", out)
        self.env.api.fail("/item/2", 503, count=5)
        code, _ = self.env.run("item", "--source", "items", "2")              # 前の姿が無ければ一時的な失敗
        self.assertEqual(code, 3)

    def test_item_needs_item_spec(self):
        self.env.write_config({"items": self.env.source("/all", key="id")})
        code, out = self.env.run("item", "--source", "items", "1")
        self.assertEqual(code, 1)
        self.assertIn("item", out)

    def test_call_record_updates_records_until_next_fetch(self):
        self.env.write_config({"items": self.env.source("/all", key="id", min_interval_sec=3600,
                                                        item={"url": "{{ conn.url }}/item/{{ key }}"})})
        self.env.api.put(1, status="a")
        self.assertEqual(self.get()[0]["status"], "a")
        self.item("1", "--max-age", "300")
        self.env.ok(self, "call", "POST", "/items/1/archive", "--source", "items", "--record")
        self.assertEqual(self.get()[0]["status"], "archived")                    # 次の取得の前でも書き込んだ後の姿
        self.assertEqual(self.env.api.count("/all"), 1)
        self.assertEqual(self.item("1", "--max-age", "300")["status"], "archived")   # 前の取り直しは使わない

    def test_call_record_needs_key_before_writing(self):
        self.env.write_config({"items": self.env.source("/all")})
        code, out = self.env.run("call", "POST", "/items/1/archive", "--source", "items", "--record")
        self.assertEqual(code, 1)
        self.assertIn("key", out)
        self.assertEqual(self.env.api.count("/items/1/archive"), 0)

    def test_call_record_without_key_in_response_warns(self):
        code, out = self.env.run("call", "POST", "/_put", "--source", "items", "--json", '{"id": 3}', "--record")
        self.assertEqual(code, 0, out)
        self.assertIn("応答に key が無い", out)


class ItemCacheTest(unittest.TestCase):
    def test_commit_refresh_invalidates_cached_item(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "change-watch.json"
            path.write_text(json.dumps({"version": 1, "state_dir": directory, "sources": {
                "items": {"key": "id", "request": {"url": "http://example.test/all"},
                          "item": {"url": "http://example.test/item/{{ key }}"}},
            }}), encoding="utf-8")
            cfg = config.load(path)
            src = cfg.sources["items"]
            store = Store(cfg.state_dir)
            try:
                fetcher = Fetcher(cfg, store)
                before = {"id": 1, "status": "a"}
                after = {"id": 1, "status": "b"}
                with patch.object(fetcher, "get_json", side_effect=[before, after, after]):
                    self.assertEqual(access.item(fetcher, src, "1", 300)[0], before)
                    self.assertEqual(access.commit(fetcher, src, "flow", ["1"], True), (["1"], []))
                    self.assertEqual(access.item(fetcher, src, "1", 300)[0], after)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
