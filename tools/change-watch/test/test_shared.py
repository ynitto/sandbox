"""同じ取得元を複数のワークフロー（consumer）が get しても、API への問い合わせを 1 回にまとめること
（schedule・min_interval_sec・jitter_sec・busy_wait_sec・失敗の待ち・rate_limit.group）を確かめる。"""
from __future__ import annotations

import contextlib
import io
import json
import threading
import time
import unittest

from helpers import Env

from changewatch import config as configmod
from changewatch.fetch import Fetcher
from changewatch.store import Store
from changewatch.util import ConfigError, now


class Base(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)

    def write(self, **src):
        self.env.write_config({"items": self.env.source("/all", key="id", **src)})

    def get(self, consumer: str, *args: str) -> tuple[int, str]:
        return self.env.run("get", "--source", "items", "--consumer", consumer, "--no-jitter", *args)

    def _fetcher(self, sleeps: list):
        # cli.main を通さないので、標準出力を UTF-8 にする設定が効かない（Windows の cp1252 で日本語のログが書けない）。
        # 本体のログは文字列に受ける
        out = io.StringIO()
        stack = contextlib.ExitStack()
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(out))
        self.addCleanup(stack.close)
        cfg = configmod.load(self.env.config_path, self.env.conn_file)
        store = Store(cfg.state_dir)
        self.addCleanup(store.close)
        return cfg, store, Fetcher(cfg, store, sleep=lambda s: (sleeps.append(s), time.sleep(min(s, 0.05))))


class SharedFetchTest(Base):
    def test_two_workflows_share_one_fetch(self):
        self.write(min_interval_sec=600, busy_wait_sec=60)
        self.env.api.put(1, status="a")
        self.assertEqual(self.env.take(self, "a", "--initial", "emit")[0]["key"], "1")
        self.assertEqual(self.env.take(self, "b", "--initial", "emit")[0]["key"], "1")
        self.assertEqual(self.env.api.count(), 1)
        self.env.api.put(1, status="b")
        self.env.ok(self, "fetch", "--force")                       # a のワークフローが取得して処理
        self.assertEqual([c["kind"] for c in self.env.take(self, "a")], ["changed"])
        code, out = self.get("b")                                   # b は取得せず、同じ結果で処理
        self.assertEqual(code, 0, out)
        self.assertIn("取得しません", out)
        self.assertEqual([c["kind"] for c in json.loads(self.env.stdout)], ["changed"])
        self.assertEqual(self.env.api.count(), 2)

    def test_failure_is_shared_and_backs_off(self):
        self.write(min_interval_sec=600, busy_wait_sec=60, failure_backoff_sec=300)
        self.env.api.fail("/all", 503, count=100)
        code, _ = self.env.run("fetch")
        self.assertEqual(code, 3)
        first = self.env.api.count()
        self.assertGreater(first, 0)
        code, out = self.get("b")                                   # 別のワークフローは問い合わせない（前回までの結果で答える）
        self.assertEqual(code, 0, out)
        self.assertIn("問い合わせません", out)
        self.assertEqual(self.env.api.count(), first)
        s = self.env.store()
        try:
            row = s.source_row("items")
            self.assertAlmostEqual(row["retry_not_before"] - now(), 300, delta=30)
            with s.tx() as db:                                      # 待ちが明けたらもう一度だけ試し、待ちを倍にする
                db.execute("UPDATE sources SET retry_not_before=? WHERE name='items'", (now() - 1,))
        finally:
            s.close()
        code, _ = self.env.run("fetch")
        self.assertEqual(code, 3)
        s = self.env.store()
        try:
            self.assertAlmostEqual(s.source_row("items")["retry_not_before"] - now(), 600, delta=30)
        finally:
            s.close()
        self.env.api.failures.clear()
        self.env.ok(self, "fetch", "--force")                       # 手で叩くときは待たない。成功したら待ちを解く
        s = self.env.store()
        try:
            row = s.source_row("items")
            self.assertIsNone(row["retry_not_before"])
            self.assertEqual(row["consecutive_failures"], 0)
        finally:
            s.close()

    def test_per_minute_counts_requests_of_previous_processes(self):
        self.write(rate_limit={"per_minute": 2})
        self.env.api.put(1, status="a")
        sleeps: list = []
        cfg, store, f = self._fetcher(sleeps)
        self.assertEqual(f.run(cfg.sources["items"]).status, "complete")
        self.assertEqual(f.run(cfg.sources["items"]).status, "complete")
        self.assertEqual(sleeps, [])
        _, _, g = self._fetcher(sleeps)                             # 別のプロセス（ワークフロー）の 3 回目
        self.assertEqual(g.run(cfg.sources["items"]).status, "complete")
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 30)                          # 前の 2 回を数えて 1 分の残りを待つ

    def test_jitter(self):
        self.write(jitter_sec=100, min_interval_sec=600)
        sleeps: list = []
        cfg, _, f = self._fetcher(sleeps)
        self.assertEqual(f.run(cfg.sources["items"]).status, "complete")
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(0 <= sleeps[0] <= 100)
        self.assertEqual(f.run(cfg.sources["items"]).status, "skipped")     # 取得し終えていれば待たない
        self.assertEqual(len(sleeps), 1)
        self.assertEqual(f.run(cfg.sources["items"], force=True).status, "complete")
        self.assertEqual(f.run(cfg.sources["items"], force=True, jitter=False).status, "complete")
        self.assertEqual(len(sleeps), 1)                                     # --force・--no-jitter はずらさない

    def test_waits_for_fetch_in_progress_and_uses_its_result(self):
        self.write(busy_wait_sec=10)
        sleeps: list = []
        cfg, store, f = self._fetcher(sleeps)
        store.source_row("items")
        fp = cfg.sources["items"].fingerprint(cfg.resolve_connection(cfg.sources["items"].connection))
        held = threading.Event()

        def other_workflow():                        # 別のワークフローの change-watch が取得している
            other = Store(cfg.state_dir)
            try:
                self.assertTrue(other.acquire("fetch:items", 300))
                held.set()
                time.sleep(0.3)
                with other.tx() as db:
                    db.execute("UPDATE sources SET fingerprint=?, last_success_at=? WHERE name='items'", (fp, now()))
                other.release("fetch:items")
            finally:
                other.close()
        th = threading.Thread(target=other_workflow)
        th.start()
        held.wait(5)
        o = f.run(cfg.sources["items"])
        th.join()
        self.assertEqual(o.status, "skipped", o.message)
        self.assertIn("いま取得した結果", o.message)
        self.assertEqual(self.env.api.count(), 0)

    def test_without_busy_wait_returns_busy(self):
        self.write()
        cfg, _, f = self._fetcher([])
        other = Store(cfg.state_dir)
        self.addCleanup(other.close)
        self.assertTrue(other.acquire("fetch:items", 300))
        self.assertEqual(f.run(cfg.sources["items"]).status, "busy")


class CronTest(unittest.TestCase):
    def test_previous_and_next(self):
        from datetime import datetime
        from changewatch import cron
        t = datetime(2026, 11, 4, 10, 37, 20)                      # 水曜
        c = cron.parse("0 * * * *")
        self.assertEqual(c.previous(t), datetime(2026, 11, 4, 10, 0))
        self.assertEqual(c.next(t), datetime(2026, 11, 4, 11, 0))
        c = cron.parse("*/10 9-18 * * MON-FRI")
        self.assertEqual(c.previous(t), datetime(2026, 11, 4, 10, 30))
        self.assertEqual(c.previous(datetime(2026, 11, 8, 12, 0)), datetime(2026, 11, 6, 18, 50))   # 日曜 → 金曜
        self.assertEqual(c.next(datetime(2026, 11, 6, 18, 55)), datetime(2026, 11, 9, 9, 0))
        c = cron.parse("30 9 1 * 0")                               # 1 日か日曜（どちらかに当たれば当たり）
        self.assertEqual(c.previous(t), datetime(2026, 11, 1, 9, 30))
        self.assertEqual(c.next(t), datetime(2026, 11, 8, 9, 30))
        self.assertEqual(cron.parse("@daily").previous(t), datetime(2026, 11, 4, 0, 0))
        self.assertEqual(cron.parse("0 0 * * 7").previous(t), datetime(2026, 11, 1, 0, 0))  # 7 も日曜
        self.assertEqual(cron.parse("0 0 1 JAN *").previous(t), datetime(2026, 1, 1, 0, 0))

    def test_invalid(self):
        from changewatch import cron
        for bad in ("* * * *", "60 * * * *", "*/0 * * * *", "a * * * *", "0 0 30 2 *"):
            with self.assertRaises(ConfigError, msg=bad):
                cron.parse(bad)


class ScheduleTest(Base):
    def _set_success(self, ts: float):
        s = self.env.store()
        try:
            with s.tx() as db:
                db.execute("UPDATE sources SET last_success_at=? WHERE name='items'", (ts,))
        finally:
            s.close()

    def test_fetch_only_when_schedule_is_due(self):
        from datetime import datetime
        from changewatch import cron
        self.write(schedule="0 * * * *")
        self.env.api.put(1, status="a")
        self.assertEqual(self.get("a")[0], 0)
        self.assertEqual(self.env.api.count(), 1)
        prev = cron.parse("0 * * * *").previous(datetime.now()).timestamp()
        self._set_success(prev + 1)                                # 毎時 0 分をまたいでも結果が変わらないよう、取得した時刻を固定する
        code, out = self.get("b")                                  # 同じ予定の時刻のうちは、別のワークフローも取得しない
        self.assertEqual(code, 0, out)
        self.assertIn("取得済み", out)
        self.assertEqual(self.env.api.count(), 1)
        prev = cron.parse("0 * * * *").previous(datetime.now()).timestamp()
        self._set_success(prev - 1)                                # 前の予定の時刻より前に取得したきり → 取得する番
        self.get("b")
        self.assertEqual(self.env.api.count(), 2)
        self.env.ok(self, "fetch", "--force")                      # --force は予定を見ない
        self.assertEqual(self.env.api.count(), 3)

    def test_without_schedule_fetches_every_time(self):
        self.write()
        self.env.api.put(1, status="a")
        self.get("a")
        self.get("a")
        self.assertEqual(self.env.api.count(), 2)                  # 起動の間隔は Dagu の schedule に任せる


class RateGroupTest(Base):
    def write_two(self, **rl):
        rl = {"group": "api", **rl}
        cfg = {"version": 1, "state_dir": str(self.env.tmp / "state"),
               "sources": {"items": self.env.source("/all", rate_limit=rl),
                           "pages": self.env.source("/all", rate_limit=dict(rl), query={"x": "1"})}}
        self.env.config_path.write_text(json.dumps(cfg), encoding="utf-8")

    def test_per_minute_is_shared_by_the_group(self):
        self.write_two(per_minute=2)
        self.env.api.put(1, status="a")
        sleeps: list = []
        cfg, _, f = self._fetcher(sleeps)
        self.assertEqual(f.run(cfg.sources["items"]).status, "complete")
        self.assertEqual(f.run(cfg.sources["pages"]).status, "complete")
        self.assertEqual(sleeps, [])
        self.assertEqual(f.run(cfg.sources["items"]).status, "complete")   # group で 3 回目 → 1 分の残りを待つ
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 30)

    def test_failure_cools_down_the_whole_group(self):
        self.write_two()
        cfg = json.loads(self.env.config_path.read_text(encoding="utf-8"))
        for src in cfg["sources"].values():
            src["failure_backoff_sec"] = 300
        self.env.config_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.env.api.fail("/all", 503, count=100)
        code, _ = self.env.run("fetch", "--source", "items")
        self.assertEqual(code, 3)
        n = self.env.api.count()
        out = self.env.ok(self, "fetch", "--source", "pages")      # 同じ API のほかの取得元も問い合わせない
        self.assertIn("同じ group", out)
        self.assertEqual(self.env.api.count(), n)

    def test_fetches_in_a_group_do_not_overlap(self):
        self.write_two()
        cfg, _, f = self._fetcher([])
        other = Store(cfg.state_dir)
        self.addCleanup(other.close)
        self.assertTrue(other.acquire("fetch-group:api", 300))     # 同じ group の別の取得元が取得中
        self.assertEqual(f.run(cfg.sources["pages"]).status, "busy")

    def test_group_limits_must_match(self):
        self.write_two(per_minute=2)
        cfg = json.loads(self.env.config_path.read_text(encoding="utf-8"))
        cfg["sources"]["pages"]["rate_limit"]["per_minute"] = 5
        self.env.config_path.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaises(ConfigError) as cm:
            configmod.load(self.env.config_path, self.env.conn_file)
        self.assertIn("同じ値", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
