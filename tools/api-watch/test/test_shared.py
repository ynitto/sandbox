"""1 つのワークフローで取得から処理まで（command の受け渡し先・work）と、同じ取得元を複数のワークフローが
呼んでも API への問い合わせを 1 回にまとめること（min_interval_sec・jitter_sec・busy_wait_sec）を確かめる。"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import time
import unittest
from pathlib import Path

from helpers import Env

from apiwatch import config as configmod
from apiwatch.fetch import Fetcher
from apiwatch.store import Store
from apiwatch.util import ConfigError, now

# 受け渡されたイベントを 1 行ずつ書き出す。FAIL_FILE があれば、その回数だけ失敗する
HANDLER = '''\
import json, os, sys
log, fail = sys.argv[1], sys.argv[2]
n = int(open(fail).read()) if os.path.exists(fail) else 0
ev = json.load(open(os.environ["APIWATCH_EVENT_FILE"], encoding="utf-8"))
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"rule": os.environ["APIWATCH_RULE"], "record": ev["record_key"],
                        "delivery": os.environ["APIWATCH_DELIVERY_ID"], "arg": sys.argv[3],
                        "ok": n == 0}) + "\\n")
if n:
    open(fail, "w").write(str(n - 1))
    sys.exit(5)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)
        self.env.plugins = []                       # command の受け渡し先はアダプタもプラグインも要らない
        self.handler = self.env.tmp / "handler.py"
        self.handler.write_text(HANDLER, encoding="utf-8")
        self.log = self.env.tmp / "handled.jsonl"
        self.fail_file = self.env.tmp / "fail"

    def target(self, name: str = "job", **kw) -> dict:
        t = {"name": name, "command": [sys.executable, str(self.handler), str(self.log), str(self.fail_file),
                                       "{{ event.record_key }}"],
             "timeout_sec": 30, "retry_backoff_sec": 0}
        t.update(kw)
        return t

    def write(self, rules: dict, **src):
        cfg = {"version": 1, "state_dir": str(self.env.tmp / "state"),
               "sources": {"items": self.env.source("/all", **src)}, "rules": rules}
        self.env.config_path.write_text(json.dumps(cfg), encoding="utf-8")

    def handled(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]

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

    def rule(self, *targets, **kw) -> dict:
        r = {"source": "items", "id": "id", "watch": ["status"], "targets": list(targets)}
        r.update(kw)
        return r


class RunHereTest(Base):
    def test_run_rule_fetches_evaluates_and_runs_in_place(self):
        self.write({"r": self.rule(self.target())})
        self.env.api.put(1, status="a")
        self.env.ok(self, "run", "--rule", "r")                     # 基準値
        self.assertEqual(self.handled(), [])
        self.env.api.put(1, status="b")
        self.env.api.put(2, status="a")
        self.env.ok(self, "run", "--rule", "r")
        h = self.handled()
        self.assertEqual(sorted(x["record"] for x in h), ["1", "2"])
        self.assertEqual(sorted(x["arg"] for x in h), ["1", "2"])    # command にもイベントを埋められる
        self.assertTrue(all(d["status"] == "done" for d in self.env.deliveries()))
        self.env.ok(self, "run", "--rule", "r")                     # 済んだものはもう実行しない
        self.assertEqual(len(self.handled()), 2)

    def test_failure_is_retried_on_next_run_then_given_up(self):
        self.write({"r": self.rule(self.target(max_attempts=2))})
        self.env.api.put(1, status="a")
        self.env.ok(self, "run", "--rule", "r")
        self.env.api.put(1, status="b")
        self.fail_file.write_text("5", encoding="utf-8")
        code, out = self.env.run("run", "--rule", "r")
        self.assertEqual(code, 4, out)                              # 一部が失敗した
        self.assertEqual(self.env.deliveries()[0]["status"], "send_failed")
        code, _ = self.env.run("work", "--rule", "r")
        self.assertEqual(code, 4)
        d = self.env.deliveries()[0]
        self.assertEqual((d["status"], d["attempts"]), ("dead", 2))
        self.assertIn("終了コード 5", d["last_error"])
        self.fail_file.write_text("0", encoding="utf-8")
        self.env.ok(self, "retry", d["id"])
        self.env.ok(self, "work")
        self.assertEqual(self.env.deliveries()[0]["status"], "done")
        self.assertEqual([x["ok"] for x in self.handled()], [False, False, True])
        self.assertEqual(len({x["delivery"] for x in self.handled()}), 1)   # やり直しても同じ受け渡しの ID

    def test_dispatch_leaves_run_here_targets_alone(self):
        self.write({"r": self.rule(self.target())})
        self.env.api.put(1, status="a")
        self.env.ok(self, "fetch")
        self.env.ok(self, "evaluate")
        self.env.api.put(1, status="b")
        self.env.ok(self, "fetch")
        self.env.ok(self, "evaluate")
        self.env.ok(self, "dispatch")                               # delivery（アダプタ）が無くても動く
        self.assertEqual(self.env.deliveries()[0]["status"], "pending")
        self.assertEqual(self.handled(), [])

    def test_timeout_kills_command(self):
        slow = self.env.tmp / "slow.py"
        slow.write_text("import time; time.sleep(30)", encoding="utf-8")
        self.write({"r": self.rule({"name": "slow", "command": [sys.executable, str(slow)], "timeout_sec": 1,
                                    "retry_backoff_sec": 0})})
        self.env.api.put(1, status="a")
        self.env.ok(self, "run", "--rule", "r")
        self.env.api.put(1, status="b")
        t = time.monotonic()
        code, _ = self.env.run("run", "--rule", "r")
        self.assertEqual(code, 4)
        self.assertLess(time.monotonic() - t, 25)
        self.assertIn("打ち切った", self.env.deliveries()[0]["last_error"])

    def test_config_checks(self):
        cases = [
            ({"command": ["x"]}, "name"),
            ({"name": "a", "command": ["x"], "workflow": "w"}, "どちらか一方"),
            ({"workflow": "w", "timeout_sec": 5}, "command を書いたときだけ"),
        ]
        for t, msg in cases:
            self.write({"r": self.rule(t)})
            with self.assertRaises(ConfigError) as cm:
                configmod.load(self.env.config_path, self.env.conn_file)
            self.assertIn(msg, str(cm.exception))
        self.write({"r": self.rule(schedule="0 * * * *")})
        with self.assertRaises(ConfigError):
            configmod.load(self.env.config_path, self.env.conn_file)
        self.write({"r": self.rule({"workflow": "w"})})
        with self.assertRaises(ConfigError) as cm:
            configmod.load(self.env.config_path, self.env.conn_file)
        self.assertIn("delivery.adapter", str(cm.exception))


class SharedFetchTest(Base):
    def test_two_workflows_share_one_fetch(self):
        self.write({"a": self.rule(self.target("ja")), "b": self.rule(self.target("jb"))},
                   min_interval_sec=600, busy_wait_sec=60)
        out = self.env.ok(self, "check")
        self.assertIn("2 本のワークフロー", out)
        self.env.api.put(1, status="a")
        self.env.ok(self, "run", "--rule", "a")
        self.env.ok(self, "run", "--rule", "b")
        self.assertEqual(self.env.api.count(), 1)
        self.env.api.put(1, status="b")
        self.env.ok(self, "run", "--rule", "a", "--force")          # a のワークフローが取得して処理
        out = self.env.ok(self, "run", "--rule", "b")               # b は取得せず、同じ結果で処理
        self.assertIn("取得しません", out)
        self.assertEqual(self.env.api.count(), 2)
        self.assertEqual(sorted(x["rule"] for x in self.handled()), ["a", "b"])

    def _load_error(self) -> str:
        with self.assertRaises(ConfigError) as cm:
            configmod.load(self.env.config_path, self.env.conn_file)
        return str(cm.exception)

    def test_sharing_settings_are_required_when_several_workflows_fetch(self):
        two = {"a": self.rule(self.target("ja")), "b": self.rule(self.target("jb"))}
        self.write(two)
        self.assertIn("min_interval_sec", self._load_error())
        self.write(two, min_interval_sec=600)
        self.assertIn("busy_wait_sec", self._load_error())
        self.write(two, min_interval_sec=600, busy_wait_sec=60, jitter_sec=600)
        self.assertIn("jitter_sec", self._load_error())
        # 取得元のワークフロー（workflow の受け渡し先）とルールのワークフローが同じ取得元を取る組み合わせも数える
        self.write({"a": self.rule(self.target("ja")), "p": self.rule()})
        self.assertIn("2 本のワークフロー", self._load_error())
        # 1 本だけなら要らない
        self.write({"a": self.rule(self.target("ja"))})
        configmod.load(self.env.config_path, self.env.conn_file)

    def test_failure_is_shared_and_backs_off(self):
        self.write({"a": self.rule(self.target("ja")), "b": self.rule(self.target("jb"))},
                   min_interval_sec=600, busy_wait_sec=60, failure_backoff_sec=300)
        self.env.api.fail("/all", 503, count=100)
        code, _ = self.env.run("run", "--rule", "a")
        self.assertEqual(code, 3)
        first = self.env.api.count()
        self.assertGreater(first, 0)
        out = self.env.ok(self, "run", "--rule", "b")              # 別のワークフローは問い合わせない
        self.assertIn("問い合わせません", out)
        self.env.ok(self, "run", "--rule", "a")                     # 失敗したワークフローの再試行も同じ
        self.assertEqual(self.env.api.count(), first)
        s = self.env.store()
        try:
            row = s.source_row("items")
            self.assertAlmostEqual(row["retry_not_before"] - now(), 300, delta=30)
            with s.tx() as db:                                      # 待ちが明けたらもう一度だけ試し、待ちを倍にする
                db.execute("UPDATE sources SET retry_not_before=? WHERE name='items'", (now() - 1,))
        finally:
            s.close()
        code, _ = self.env.run("run", "--rule", "b")
        self.assertEqual(code, 3)
        s = self.env.store()
        try:
            self.assertAlmostEqual(s.source_row("items")["retry_not_before"] - now(), 600, delta=30)
        finally:
            s.close()
        self.env.api.failures.clear()
        self.env.ok(self, "run", "--rule", "a", "--force")         # 手で叩くときは待たない。成功したら待ちを解く
        s = self.env.store()
        try:
            row = s.source_row("items")
            self.assertIsNone(row["retry_not_before"])
            self.assertEqual(row["consecutive_failures"], 0)
        finally:
            s.close()

    def test_per_minute_counts_requests_of_previous_processes(self):
        self.write({"a": self.rule()}, rate_limit={"per_minute": 2})
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
        self.write({"a": self.rule()}, jitter_sec=100, min_interval_sec=600)
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
        self.write({"a": self.rule()}, busy_wait_sec=10)
        sleeps: list = []
        cfg, store, f = self._fetcher(sleeps)
        store.source_row("items")
        fp = cfg.sources["items"].fingerprint(cfg.resolve_connection(cfg.sources["items"].connection))
        held = threading.Event()

        def other_workflow():                        # 別のワークフローの api-watch が取得している
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
        self.write({"a": self.rule()})
        cfg, _, f = self._fetcher([])
        other = Store(cfg.state_dir)
        self.addCleanup(other.close)
        self.assertTrue(other.acquire("fetch:items", 300))
        self.assertEqual(f.run(cfg.sources["items"]).status, "busy")


class CronTest(unittest.TestCase):
    def test_previous_and_next(self):
        from datetime import datetime
        from apiwatch import cron
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
        from apiwatch import cron
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
        from apiwatch import cron
        self.write({"a": self.rule(self.target("ja")), "b": self.rule(self.target("jb"))}, schedule="0 * * * *")
        configmod.load(self.env.config_path, self.env.conn_file)    # schedule があれば共有の設定は要らない
        self.env.api.put(1, status="a")
        self.env.ok(self, "run", "--rule", "a")
        self.assertEqual(self.env.api.count(), 1)
        prev = cron.parse("0 * * * *").previous(datetime.now()).timestamp()
        self._set_success(prev + 1)                                # 毎時 0 分をまたいでも結果が変わらないよう、取得した時刻を固定する
        out = self.env.ok(self, "run", "--rule", "b")              # 同じ予定の時刻のうちは、別のワークフローも取得しない
        self.assertIn("取得済み", out)
        self.assertEqual(self.env.api.count(), 1)
        prev = cron.parse("0 * * * *").previous(datetime.now()).timestamp()
        self._set_success(prev - 1)                                # 前の予定の時刻より前に取得したきり → 取得する番
        self.env.ok(self, "run", "--rule", "b")
        self.assertEqual(self.env.api.count(), 2)
        self.env.ok(self, "run", "--rule", "a", "--force")         # --force は予定を見ない
        self.assertEqual(self.env.api.count(), 3)

    def test_without_schedule_fetches_every_time(self):
        self.write({"a": self.rule(self.target("ja"))})
        self.env.api.put(1, status="a")
        self.env.ok(self, "run")
        self.env.ok(self, "run")
        self.assertEqual(self.env.api.count(), 2)                  # 起動の間隔は Dagu の schedule に任せる


class RateGroupTest(Base):
    def write_two(self, **rl):
        rl = {"group": "api", **rl}
        cfg = {"version": 1, "state_dir": str(self.env.tmp / "state"),
               "sources": {"items": self.env.source("/all", rate_limit=rl),
                           "pages": self.env.source("/all", rate_limit=dict(rl), query={"x": "1"})},
               "rules": {"a": self.rule(), "b": {**self.rule(), "source": "pages"}}}
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


class DagsTest(Base):
    def _yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML が無い")
        return yaml

    def test_rule_workflow_dag(self):
        yaml = self._yaml()
        self.env.plugins = ["dagu"]
        self.write({"a": self.rule(self.target("ja")), "b": self.rule(self.target("jb"))},
                   schedule="0 * * * *", jitter_sec=900, busy_wait_sec=600)
        out = self.env.tmp / "dags"
        self.env.ok(self, "dags", "--out", str(out))
        names = sorted(p.name for p in out.iterdir())
        # どのルールも自分のワークフローで取得するので、取得元の DAG と再送の DAG は作らない
        self.assertEqual(names, ["api-watch-a.yaml", "api-watch-b.yaml", "api-watch-maintenance.yaml"])
        d = yaml.safe_load((out / "api-watch-a.yaml").read_text(encoding="utf-8"))
        self.assertEqual(d["schedule"], "* * * * *")       # 設定の schedule は書き写さない。毎分起動し、取得の予定は api-watch が見る
        self.assertEqual([s["name"] for s in d["steps"]], ["fetch", "evaluate", "work"])
        self.assertEqual(d["steps"][0]["with"]["args"][-3:], ["fetch", "--source", "items"])
        self.assertGreaterEqual(d["steps"][0]["timeout_sec"], 900 + 600)
        self.assertEqual(d["steps"][2]["with"]["args"][-3:], ["work", "--rule", "a"])
        self.assertNotIn("timeout_sec", d["steps"][2])

    def test_dagu_schedule_and_no_overwrite(self):
        yaml = self._yaml()
        self.env.plugins = ["dagu"]
        self.write({"a": self.rule(self.target("ja"))})              # 予定も間隔も無い
        out = self.env.tmp / "dags"
        code, msg = self.env.run("dags", "--out", str(out))
        self.assertNotEqual(code, 0)
        self.assertIn("毎分問い合わせます", msg)                   # 毎分起動なら毎分問い合わせてしまう
        self.env.ok(self, "dags", "--out", str(out), "--schedule", "30 9 * * 1-5")   # Dagu の予定で動かす
        path = out / "api-watch-a.yaml"
        self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8"))["schedule"], "30 9 * * 1-5")
        path.write_text(path.read_text(encoding="utf-8").replace("30 9 * * 1-5", "45 9 * * 1-5"), encoding="utf-8")
        msg = self.env.ok(self, "dags", "--out", str(out), "--schedule", "0 * * * *")
        self.assertIn("既にある", msg)                               # 手で直した DAG は上書きしない
        self.assertIn("45 9 * * 1-5", path.read_text(encoding="utf-8"))
        self.env.ok(self, "dags", "--out", str(out), "--schedule", "0 * * * *", "--force")
        self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8"))["schedule"], "0 * * * *")

    def test_rule_name_must_not_clash(self):
        self.env.plugins = ["dagu"]
        self.write({"items": self.rule(self.target())})
        code, out = self.env.run("dags", "--out", str(self.env.tmp / "dags"))
        self.assertNotEqual(code, 0)
        self.assertIn("重なります", out)


if __name__ == "__main__":
    unittest.main()
