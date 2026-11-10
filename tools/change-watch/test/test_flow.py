"""通しのテスト: 偽の API で、ページング・増分・条件付き要求・再試行・途中からの再開・保持期間・秘密の伏せ字・
設定と接続設定の読み方を確かめる。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest

from helpers import ROOT, TOKEN, Env

SCRIPT = ROOT / "change_watch.py"
PAGED = {"type": "page", "size_param": "per_page", "size": 2}


def keys(found: list[dict]) -> list[str]:
    return sorted((f["key"] for f in found), key=int)


class PaginationTest(unittest.TestCase):
    CASES = {
        "page": ("/page", {"type": "page", "size_param": "per_page", "size": 2}, "data"),
        "page_total": ("/page", {"type": "page", "total_pages_path": "total_pages"}, "data"),
        "offset": ("/offset", {"type": "offset", "size": 2, "total_path": "total"}, "items"),
        "cursor": ("/cursor", {"type": "cursor", "cursor_path": "next_cursor"}, "items"),
        "next_url": ("/next", {"type": "next_url", "next_url_path": "next"}, "results"),
        "link": ("/link", {"type": "link_header"}, "$"),
    }

    def test_all_kinds(self):
        for name, (path, pag, recs) in self.CASES.items():
            with self.subTest(name):
                env = Env(self)
                for i in range(1, 6):
                    env.api.put(i, status="open")
                env.write_config({"items": env.source(path, pagination=pag, records=recs)})
                env.ok(self, "get", "--source", "items")
                self.assertEqual(sorted(r["id"] for r in json.loads(env.stdout)), [1, 2, 3, 4, 5])
                self.assertEqual(env.api.count(path), 3)

    def test_next_url_to_other_host_is_refused(self):
        env = Env(self)
        env.api.put(1)
        env.api.put(2)
        env.api.put(3)
        env.write_config({"items": env.source("/link", pagination={"type": "link_header"})})
        env.api.url = env.api.url.replace("127.0.0.1", "localhost")   # Link を別ホスト名で返させる
        code, out = env.run("fetch")
        self.assertEqual(code, 1)
        self.assertIn("別のホスト", out)


class IncrementalTest(unittest.TestCase):
    def test_max_value(self):
        env = Env(self)
        for i in (1, 2, 3):
            env.api.put(i, status="open")
        env.write_config({"items": env.source("/page", records="data", key="id",
                                              pagination={"type": "page", "size_param": "per_page", "size": 10},
                                              incremental={"type": "max_value", "field": "updated",
                                                           "param": "updated_since"})})
        self.assertEqual(env.take(self), [])                           # 初回は基準を覚えるだけ
        self.assertIsNone(env.api.requests[-1][1].get("updated_since"))  # 初回は全件
        env.api.put(2, status="closed")
        self.assertEqual(keys(env.take(self)), ["2"])
        self.assertEqual(env.api.requests[-1][1]["updated_since"], "103")
        # 取得が失敗したら位置を進めない
        env.api.put(3, status="closed")
        env.api.fail("/page", 500, count=3)
        code, _ = env.run("fetch")
        self.assertEqual(code, 3)
        s = env.store()
        self.assertEqual(json.loads(s.source_row("items")["position"]), 104)
        s.close()
        self.assertEqual(keys(env.take(self)), ["3"])

    def test_cursor_assembles_the_whole_view(self):
        env = Env(self)
        for i in (1, 2, 3):
            env.api.put(i, status="open")
        env.write_config({"items": env.source("/sync", records="items", key="id",
                                              incremental={"type": "cursor", "cursor_path": "sync_token",
                                                           "param": "since_rev"})})
        env.take(self)
        env.api.put(1, status="closed")
        self.assertEqual(keys(env.take(self)), ["1"])
        self.assertEqual(env.api.requests[-1][1]["since_rev"], "3")
        # 増分の取得結果は 1 件だけだが、組み立てた全体を返す。新しい consumer が来ても 2・3 が「新規」で大量に出たりしない
        env.ok(self, "get", "--source", "items", "--no-fetch")
        self.assertEqual(sorted(r["id"] for r in json.loads(env.stdout)), [1, 2, 3])
        self.assertEqual(env.take(self, "other"), [])
        env.api.put(3, status="closed")
        self.assertEqual(keys(env.take(self, "other")), ["3"])


class ConditionalAndRetryTest(unittest.TestCase):
    def test_etag(self):
        env = Env(self)
        env.api.put(1, status="open")
        env.write_config({"items": env.source("/all", key="id")})
        env.take(self)
        env.take(self)
        hdrs = env.api.requests[-1][2]
        self.assertIn("If-None-Match", hdrs)
        st = json.loads(env.ok(self, "status", "--json"))
        self.assertEqual(st["sources"][0]["last_fetch"]["not_modified_pages"], 1)
        env.api.put(1, status="closed")
        self.assertEqual(keys(env.take(self)), ["1"])

    def test_transient_errors_and_retry_after(self):
        env = Env(self)
        env.api.put(1, status="open")
        env.write_config({"items": env.source("/all")})
        env.api.fail("/all", 503)
        env.api.fail("/all", 429, headers={"Retry-After": "0"})
        out = env.ok(self, "fetch")
        self.assertIn("再試行", out)
        self.assertEqual(env.api.count("/all"), 3)

    def test_long_retry_after_defers(self):
        env = Env(self)
        env.api.put(1)
        env.write_config({"items": env.source("/all", rate_limit={"max_wait_sec": 5})})
        env.api.fail("/all", 429, headers={"Retry-After": "3600"})
        code, _ = env.run("fetch")
        self.assertEqual(code, 3)
        n = env.api.count()
        code, out = env.run("fetch")
        self.assertEqual(code, 0)
        self.assertIn("deferred", out)
        self.assertEqual(env.api.count(), n)                          # 待つよう言われた間は問い合わせない

    def test_auth_error_is_not_retried_and_secret_not_logged(self):
        env = Env(self)
        env.api.put(1)
        env.write_config({"items": env.source("/all")})
        env.api.fail("/all", 401, body=json.dumps({"message": f"token {TOKEN} is revoked"}))
        code, out = env.run("fetch")
        self.assertEqual(code, 1)
        self.assertEqual(env.api.count(), 1)
        self.assertNotIn(TOKEN, out)
        self.assertIn("***", out)
        st = env.ok(self, "status", "--json")
        self.assertNotIn(TOKEN, st)
        for f in (env.tmp / "state").rglob("*"):                    # state.db・WAL・イベントのファイル
            if f.is_file():
                self.assertNotIn(TOKEN.encode(), f.read_bytes(), f)


class ResumeTest(unittest.TestCase):
    def test_failure_mid_way_resumes_without_skipping(self):
        env = Env(self)
        for i in range(1, 7):
            env.api.put(i, status="open")
        env.write_config({"items": env.source("/page", records="data", key="id", pagination=PAGED)})
        env.take(self)
        for i in range(1, 7):
            env.api.put(i, status="closed")
        env.api.fail("/page", 500, count=3, page="3")
        code, _ = env.run("fetch")
        self.assertEqual(code, 3)
        env.ok(self, "get", "--source", "items", "--consumer", "c", "--no-fetch")
        self.assertEqual(json.loads(env.stdout), [])                  # 途中までの取得では答えない
        before = env.api.count("/page")
        self.assertEqual(keys(env.take(self)), ["1", "2", "3", "4", "5", "6"])
        pages = [q.get("page") for p, q, _ in env.api.requests[before:] if p == "/page"]
        self.assertEqual(pages, ["3", "4"])                           # 1・2 ページ目は取り直さない

    def test_killed_fetch_is_taken_over_and_resumed(self):
        env = Env(self)
        for i in range(1, 7):
            env.api.put(i, status="open")
        env.write_config({"items": env.source("/page", records="data", key="id", lease_sec=600, pagination=PAGED)})
        env.api.block_page = ("/page", "2")
        p = subprocess.Popen([sys.executable, str(SCRIPT), "--config", str(env.config_path),
                              "--connections", str(env.conn_file), "fetch"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=dict(os.environ))
        self.assertTrue(env.api.blocked.wait(20))
        code, out = env.run("fetch")                                  # 取得中は 2 つ目を始めない
        self.assertEqual(code, 0)
        self.assertIn("busy", out)
        p.kill()                                                       # 取得の途中で強制終了（PC の停止と同じ）
        p.wait(20)
        p.stdout.close()
        env.api.block_page = None
        env.api.release.set()
        found = env.take(self, "c", "--initial", "emit")               # 死んだプロセスの排他は奪える
        self.assertEqual(keys(found), ["1", "2", "3", "4", "5", "6"])


class SourceChangeTest(unittest.TestCase):
    def test_source_credential_change_rebaselines(self):
        env = Env(self)
        env.api.put(1, status="open")
        env.write_config({"items": env.source("/all", key="id")})
        env.take(self)
        env.conn_file.write_text(json.dumps({"fakeapi": [{"label": "default", "url": env.api.url,
                                                          "token": TOKEN, "extra": "scope2"}]}))
        env.api.put(1, status="closed")
        code, out = env.run("get", "--source", "items", "--consumer", "c")
        self.assertEqual(code, 0, out)
        self.assertIn("取得条件か認証が変わった", out)
        self.assertEqual(json.loads(env.stdout), [])                  # 別のデータとして基準を取り直す
        self.assertNotIn("If-None-Match", env.api.requests[-1][2])      # 前の認証での控えを使わない


class RetentionTest(unittest.TestCase):
    def test_gc_keeps_the_latest_fetches(self):
        env = Env(self)
        env.api.put(1, status="a")
        env.write_config({"items": env.source("/all")}, retention={"fetches_keep": 1, "fetch_days": 0, "auto_gc": False})
        for s in ("b", "c", "d"):
            env.api.put(1, status=s)
            env.ok(self, "fetch")
        env.ok(self, "gc")
        st = env.store()
        self.assertEqual(st.one("SELECT COUNT(*) c FROM fetches WHERE status='complete'")["c"], 1)
        st.close()
        env.ok(self, "get", "--source", "items", "--no-fetch")
        self.assertEqual(json.loads(env.stdout)[0]["status"], "d")

    def test_gc_drops_the_tables_of_rules(self):
        env = Env(self)
        env.write_config({"items": env.source("/all")}, retention={"auto_gc": False})
        s = env.store()
        with s.tx() as db:                                              # 0.7 までの DB
            db.execute("CREATE TABLE rules(name TEXT)")
            db.execute("CREATE TABLE deliveries(id TEXT)")
        s.close()
        (env.tmp / "state" / "events").mkdir()
        (env.tmp / "state" / "events" / "x.json").write_text("{}", encoding="utf-8")
        env.ok(self, "gc")
        s = env.store()
        names = {r["name"] for r in s.q("SELECT name FROM sqlite_master WHERE type='table'")}
        s.close()
        self.assertFalse(names & {"rules", "deliveries"})
        self.assertFalse((env.tmp / "state" / "events").exists())


class StorageTest(unittest.TestCase):
    def _age(self, env, sql):
        s = env.store()
        with s.tx() as db:
            db.execute(sql, (time.time() - 100 * 86400,))
        s.close()

    def test_gc_shrinks_the_file(self):
        env = Env(self)
        for i in range(1, 301):
            env.api.put(i, status="open", body=os.urandom(1000).hex())   # 圧縮しにくい大きめのレコード
        ret = {"fetches_keep": 1, "fetch_days": 0, "auto_gc": False}
        env.write_config({"items": env.source("/page", records="data", pagination={"type": "page",
                                                                                  "size_param": "per_page",
                                                                                  "size": 50})},
                         retention=ret)
        for _ in range(4):
            env.api.put(1, status=str(time.time()))
            env.ok(self, "fetch")
        s = env.store()
        self.assertEqual(s.one("SELECT COUNT(*) c FROM fetches")["c"], 4)
        s.close()
        st = json.loads(env.ok(self, "status", "--json"))["storage"]
        self.assertEqual(st["auto_vacuum"], "incremental")              # 新しい DB は消した領域を返せる形
        before = st["db_bytes"] + st["wal_bytes"]
        out = env.ok(self, "gc")
        self.assertIn("返しました", out)
        st = json.loads(env.ok(self, "status", "--json"))["storage"]
        self.assertLess(st["wal_bytes"], 64 * 1024)                     # WAL も切り詰める（status 自身の書き込み分だけ残る）
        self.assertLess(st["db_bytes"], before * 0.5)                   # 4 回分のうち 1 回分だけ残る
        self.assertEqual(st["free_bytes"], 0)

    def test_vacuum_converts_old_db(self):
        env = Env(self)
        env.api.put(1)
        env.write_config({"items": env.source("/all")})
        (env.tmp / "state").mkdir()
        import sqlite3
        sqlite3.connect(str(env.tmp / "state" / "state.db")).execute("CREATE TABLE x(a)").connection.close()
        env.ok(self, "fetch")
        st = json.loads(env.ok(self, "status", "--json"))["storage"]
        self.assertEqual(st["auto_vacuum"], "none")                     # 先に表がある DB は切り替わらない
        self.assertIn("gc --vacuum", env.ok(self, "status"))
        env.ok(self, "gc", "--vacuum")
        st = json.loads(env.ok(self, "status", "--json"))["storage"]
        self.assertEqual(st["auto_vacuum"], "incremental")

    def test_stale_assembled_records_only_when_configured(self):
        env = Env(self)
        for i in (1, 2):
            env.api.put(i, status="open")
        src = {"items": env.source("/sync", records="items", key="id",
                                   incremental={"type": "cursor", "cursor_path": "sync_token", "param": "since_rev"})}
        env.write_config(src, retention={"auto_gc": False})
        env.ok(self, "fetch")
        env.api.put(1, status="closed")
        env.ok(self, "fetch")
        self._age(env, "UPDATE source_records SET seen_at=? WHERE rkey='2'")
        env.ok(self, "gc")
        s = env.store()
        self.assertEqual(s.one("SELECT COUNT(*) c FROM source_records")["c"], 2)   # 既定は消さない
        s.close()
        env.write_config(src, retention={"auto_gc": False, "record_days": 30})
        out = env.ok(self, "gc")
        self.assertIn("長く取得されていないレコード 1 件", out)
        s = env.store()
        self.assertEqual([r["rkey"] for r in s.q("SELECT rkey FROM source_records")], ["1"])
        s.close()


class OpsTest(unittest.TestCase):
    def test_check(self):
        env = Env(self)
        env.write_config({"items": env.source("/all", schedule="*/10 * * * *")})
        out = env.ok(self, "check")
        self.assertIn("問題ありません", out)
        self.assertNotIn(TOKEN, out)

    def test_rules_and_delivery_are_gone(self):
        env = Env(self)
        for extra in ({"rules": {}}, {"delivery": {"adapter": "dagu"}}):
            with self.subTest(extra=extra):
                env.write_config({"items": env.source("/all")}, **extra)
                code, out = env.run("check")
                self.assertEqual(code, 1)
                self.assertIn("0.8.0 でなくなりました", out)

    def test_plugin_filter(self):
        env = Env(self)
        plugin = env.tmp / "p.py"
        plugin.write_text("def register(api):\n    api.add_filter('path_of', lambda v, a: str(v) + '/' + a)\n",
                          encoding="utf-8")
        env.api.put(1, status="a")
        src = env.source()
        src["request"]["url"] = '{{ conn.url | path_of: "all" }}'
        env.write_config({"items": src})
        code, out = env.run("get", "--source", "items")
        self.assertEqual(code, 1)                                       # --plugin を付け忘れたら、設定の誤りとして止まる
        self.assertIn("path_of", out)
        env.plugins = [str(plugin)]
        env.ok(self, "get", "--source", "items")
        self.assertEqual(json.loads(env.stdout)[0]["id"], 1)
        env.plugins = [str(env.tmp / "nosuch.py")]
        code, out = env.run("status")
        self.assertEqual(code, 1)
        self.assertIn("nosuch.py", out)

    def test_connections_only_from_cli_path(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML が要る")
        env = Env(self)
        env.api.put(1, status="a")
        ws = env.tmp / "ws"
        (ws / ".github").mkdir(parents=True)
        # 決まった場所に置いても読まない
        (ws / ".github" / "connections.yaml").write_text(
            f"fakeapi:\n  - label: work\n    url: {env.api.url}\n    token: {TOKEN}\n", encoding="utf-8")
        (ws / "conf").mkdir()
        env.config_path = ws / "conf" / "change-watch.yaml"
        env.config_path.write_text("""
state_dir: ../state
sources:
  items:
    connection: fakeapi:work
    request:
      url: "{{ conn.url }}/all"
      headers:
        Authorization: "Bearer {{ conn.token }}"
    key: id
""", encoding="utf-8")
        old = os.getcwd()
        os.chdir(ws)
        self.addCleanup(os.chdir, old)
        from changewatch import cli
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = cli.main(["--config", str(env.config_path), "fetch"])
        self.assertEqual(code, 1)
        self.assertIn("--connections", buf.getvalue())
        self.assertEqual(env.api.count(), 0)
        env.conn_file = ws / "my-connections.yaml"
        env.conn_file.write_text(
            f"fakeapi:\n  - label: work\n    url: {env.api.url}\n    token: {TOKEN}\n", encoding="utf-8")
        self.assertEqual([c["key"] for c in env.take(self, "c", "--initial", "emit")], ["1"])

    def test_state_dir_from_argument_only(self):
        env = Env(self)
        env.api.put(1)
        env.write_config({"items": env.source("/all")})
        other = env.tmp / "other-state"
        os.environ["CHANGEWATCH_STATE_DIR"] = str(env.tmp / "from-env")     # 環境変数は見ない
        self.addCleanup(os.environ.pop, "CHANGEWATCH_STATE_DIR", None)
        env.ok(self, "--state-dir", str(other), "fetch")
        self.assertTrue((other / "state.db").is_file())
        self.assertFalse((env.tmp / "from-env").exists())
        self.assertFalse((env.tmp / "state").exists())

    def test_example_config_loads(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML が要る")
        from changewatch import config  # noqa: E402
        cfg = config.load(ROOT / "change-watch.yaml.example")   # 接続設定は使う時点まで読まない
        self.assertEqual(sorted(cfg.sources), ["gitlab-issues", "tickets"])
        self.assertEqual(cfg.sources["gitlab-issues"].item.url,
                         "{{ conn.url }}/api/v4/projects/123/issues/{{ key }}")

    def test_windows_example_config_loads(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML が要る")
        from changewatch import config  # noqa: E402
        cfg = config.load(ROOT / "change-watch.windows.yaml.example")
        self.assertEqual(sorted(cfg.sources), ["inbox", "issues"])
        self.assertEqual(cfg.sources["inbox"].folder.include, ["**/*.csv", "**/*.xlsx"])
        self.assertEqual(cfg.sources["inbox"].key, "rel")         # get --consumer で新しく置かれたファイルを受け取れる


if __name__ == "__main__":
    unittest.main()
