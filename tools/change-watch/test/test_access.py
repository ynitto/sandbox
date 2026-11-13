"""get・commit・call（条件の判定はワークフロー側が持ち、change-watch は API へのアクセスだけを受け持つ使い方）。"""
from __future__ import annotations

import json
import unittest

from helpers import Env


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


if __name__ == "__main__":
    unittest.main()
