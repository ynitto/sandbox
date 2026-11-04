"""通しのテスト: 偽の API・偽の dagu で、取得の集約・ページング・増分・条件付き要求・再試行・途中からの再開・
受け渡し・再送・順序・失敗の隔離・再起動からの復旧・保持期間・秘密の伏せ字を確かめる。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import FAKE_DAGU, ROOT, TOKEN, Env, FakeDaguAPI

SCRIPT = ROOT / "api_watch.py"


def simple_rules(*targets, **kw):
    r = {"source": "items", "id": "id", "watch": ["status"], "targets": list(targets)}
    r.update(kw)
    return r


class AggregationTest(unittest.TestCase):
    def test_one_fetch_shared_by_rules_and_targets(self):
        env = Env(self)
        for i in (1, 2, 3):
            env.api.put(i, status="open", prio=i)
        env.write_config(
            {"items": env.source("/page", records="data",
                                 pagination={"type": "page", "size_param": "per_page", "size": 2})},
            {"status-changed": simple_rules("notify", "audit"),
             "prio-high": {"source": "items", "id": "id", "condition": {"path": "prio", "op": "ge", "value": 3},
                           "on": ["entered"], "targets": ["escalate"]}})
        out = env.ok(self, "run")
        self.assertIn("基準値", out)
        self.assertEqual(env.api.count("/page"), 2)                     # 3 件を 2 ページで
        env.api.put(1, status="closed")
        env.api.put(2, prio=5)
        env.ok(self, "run")
        self.assertEqual(env.api.count("/page"), 4)                     # ルールが 2 本・受け渡し先が 3 つでも 2 回だけ
        runs = sorted((r["dag"], r["params"]["APIWATCH_RECORD_ID"]) for r in env.enqueued())
        self.assertEqual(runs, [("audit", "1"), ("escalate", "2"), ("notify", "1")])
        notify = next(r for r in env.enqueued() if r["dag"] == "notify")
        p = notify["params"]
        self.assertEqual(p["APIWATCH_RULE"], "status-changed")
        self.assertEqual(p["APIWATCH_SOURCE"], "items")
        self.assertEqual(p["APIWATCH_DELIVERY_ID"], notify["run_id"])   # Dagu の実行 ID = 受け渡しの ID
        ev = json.loads(Path(p["APIWATCH_EVENT_FILE"]).read_text(encoding="utf-8"))
        self.assertEqual(ev["before"], {"status": "open"})
        self.assertEqual(ev["after"], {"status": "closed"})
        self.assertEqual(ev["changes"], [{"path": "status", "type": "changed", "before": "open", "after": "closed"}])
        self.assertEqual(ev["record"]["prio"], 1)
        self.assertEqual(ev["event_id"], p["APIWATCH_EVENT_ID"])
        audit = next(r for r in env.enqueued() if r["dag"] == "audit")
        self.assertEqual(audit["params"]["APIWATCH_EVENT_ID"], p["APIWATCH_EVENT_ID"])   # 同じイベント
        self.assertNotEqual(audit["run_id"], notify["run_id"])                           # 受け渡しは別

    def test_initial_emit(self):
        env = Env(self)
        env.api.put(1, status="open")
        env.api.put(2, status="open")
        env.write_config({"items": env.source("/all")}, {"r": simple_rules("d", initial="emit")})
        env.ok(self, "run")
        self.assertEqual(len(env.enqueued()), 2)


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
                env.write_config({"items": env.source(path, pagination=pag, records=recs)},
                                 {"r": simple_rules("d", initial="emit")})
                env.ok(self, "run")
                self.assertEqual(sorted(int(r["params"]["APIWATCH_RECORD_ID"]) for r in env.enqueued()),
                                 [1, 2, 3, 4, 5])
                self.assertEqual(env.api.count(path), 3)

    def test_next_url_to_other_host_is_refused(self):
        env = Env(self)
        env.api.put(1)
        env.api.put(2)
        env.api.put(3)
        env.write_config({"items": env.source("/link", pagination={"type": "link_header"})}, {})
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
                                                           "param": "updated_since"})},
                         {"r": simple_rules("d")})
        env.ok(self, "run")
        first_since = env.api.requests[-1][1].get("updated_since")
        self.assertIsNone(first_since)                                 # 初回は全件
        env.api.put(2, status="closed")
        env.ok(self, "run")
        self.assertEqual(env.api.requests[-1][1]["updated_since"], "103")
        self.assertEqual([r["params"]["APIWATCH_RECORD_ID"] for r in env.enqueued()], ["2"])
        # 取得が失敗したら位置を進めない
        env.api.put(3, status="closed")
        env.api.fail("/page", 500, count=3)
        code, _ = env.run("fetch")
        self.assertEqual(code, 3)
        s = env.store()
        self.assertEqual(json.loads(s.source_row("items")["position"]), 104)
        s.close()
        env.ok(self, "run")
        self.assertEqual([r["params"]["APIWATCH_RECORD_ID"] for r in env.enqueued()], ["2", "3"])

    def test_cursor_and_rebaseline_uses_assembled_view(self):
        env = Env(self)
        for i in (1, 2, 3):
            env.api.put(i, status="open")
        env.write_config({"items": env.source("/sync", records="items", key="id",
                                              incremental={"type": "cursor", "cursor_path": "sync_token",
                                                           "param": "since_rev"})},
                         {"r": simple_rules("d")})
        env.ok(self, "run")
        env.api.put(1, status="closed")
        env.ok(self, "run")
        self.assertEqual(env.api.requests[-1][1]["since_rev"], "3")
        self.assertEqual(len(env.enqueued()), 1)
        # ルールを変えると基準値の取り直し。増分の取得結果は 1 件だけだが、組み立てた全体から取り直すので
        # 2・3 が「新規」になって大量に発火したりしない
        env.write_config({"items": env.source("/sync", records="items", key="id",
                                              incremental={"type": "cursor", "cursor_path": "sync_token",
                                                           "param": "since_rev"})},
                         {"r": simple_rules("d", watch=["status", "prio"])})
        env.api.put(2, prio=1)
        env.ok(self, "run")
        env.api.put(3, status="closed")
        env.ok(self, "run")
        self.assertEqual([r["params"]["APIWATCH_RECORD_ID"] for r in env.enqueued()], ["1", "3"])


class ConditionalAndRetryTest(unittest.TestCase):
    def test_etag(self):
        env = Env(self)
        env.api.put(1, status="open")
        env.write_config({"items": env.source("/all")}, {"r": simple_rules("d")})
        env.ok(self, "run")
        env.ok(self, "run")
        hdrs = env.api.requests[-1][2]
        self.assertIn("If-None-Match", hdrs)
        st = json.loads(env.ok(self, "status", "--json"))
        self.assertEqual(st["sources"][0]["last_fetch"]["not_modified_pages"], 1)
        env.api.put(1, status="closed")
        env.ok(self, "run")
        self.assertEqual(len(env.enqueued()), 1)

    def test_transient_errors_and_retry_after(self):
        env = Env(self)
        env.api.put(1, status="open")
        env.write_config({"items": env.source("/all")}, {"r": simple_rules("d")})
        env.api.fail("/all", 503)
        env.api.fail("/all", 429, headers={"Retry-After": "0"})
        out = env.ok(self, "fetch")
        self.assertIn("再試行", out)
        self.assertEqual(env.api.count("/all"), 3)

    def test_long_retry_after_defers(self):
        env = Env(self)
        env.api.put(1)
        env.write_config({"items": env.source("/all", rate_limit={"max_wait_sec": 5})}, {})
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
        env.write_config({"items": env.source("/all")}, {})
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
        env.write_config({"items": env.source("/page", records="data",
                                              pagination={"type": "page", "size_param": "per_page", "size": 2})},
                         {"r": simple_rules("d", on=["appeared", "changed", "removed"])})
        env.ok(self, "run")
        for i in range(1, 7):
            env.api.put(i, status="closed")
        env.api.fail("/page", 500, count=3, page="3")
        code, _ = env.run("run")
        self.assertEqual(code, 3)
        self.assertEqual(env.enqueued(), [])                          # 途中までの取得では判定しない（6 は消えていない）
        before = env.api.count("/page")
        env.ok(self, "run")
        pages = [q.get("page") for p, q, _ in env.api.requests[before:] if p == "/page"]
        self.assertEqual(pages, ["3", "4"])                           # 1・2 ページ目は取り直さない
        self.assertEqual(sorted(int(r["params"]["APIWATCH_RECORD_ID"]) for r in env.enqueued()), [1, 2, 3, 4, 5, 6])

    def test_killed_fetch_is_taken_over_and_resumed(self):
        env = Env(self)
        for i in range(1, 7):
            env.api.put(i, status="open")
        env.write_config({"items": env.source("/page", records="data", lease_sec=600,
                                              pagination={"type": "page", "size_param": "per_page", "size": 2})},
                         {"r": simple_rules("d", initial="emit")})
        env.api.block_page = ("/page", "2")
        p = subprocess.Popen([sys.executable, str(SCRIPT), "--config", str(env.config_path), "fetch"],
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
        env.ok(self, "run")                                            # 死んだプロセスの排他は奪える
        self.assertEqual(sorted(int(r["params"]["APIWATCH_RECORD_ID"]) for r in env.enqueued()), [1, 2, 3, 4, 5, 6])


class EvaluateTest(unittest.TestCase):
    def test_rule_change_rebaselines_and_continue(self):
        env = Env(self)
        for i in (1, 2):
            env.api.put(i, status="open", title="t")
        src = {"items": env.source("/all")}
        d = {"dag": "d", "order": "none"}
        env.write_config(src, {"r": simple_rules(d)})
        env.ok(self, "run")
        env.write_config(src, {"r": simple_rules(d, watch=["status", "title"])})
        env.api.put(1, title="t2")
        env.ok(self, "run")
        self.assertEqual(env.enqueued(), [])                           # ルールを変えた直後は基準値の取り直し
        env.api.put(1, title="t3")
        env.ok(self, "run")
        self.assertEqual(len(env.enqueued()), 1)
        env.write_config(src, {"r": simple_rules(d, watch=["status", "title", "updated"],
                                                 on_rule_change="continue")})
        env.ok(self, "run")
        self.assertEqual(len(env.enqueued()), 3)                       # continue: 新しい比べ方で前回の値と比べる

    def test_source_credential_change_rebaselines(self):
        env = Env(self)
        env.api.put(1, status="open")
        env.write_config({"items": env.source("/all")}, {"r": simple_rules("d")})
        env.ok(self, "run")
        env.conn_file.write_text(json.dumps({"fakeapi": [{"label": "default", "url": env.api.url,
                                                          "token": "${API_TOKEN}", "extra": "scope2"}]}))
        env.api.put(1, status="closed")
        out = env.ok(self, "run")
        self.assertIn("取得条件か認証が変わった", out)
        self.assertEqual(env.enqueued(), [])
        self.assertNotIn("If-None-Match", env.api.requests[-1][2])      # 前の認証での控えを使わない

    def test_replay_without_api(self):
        env = Env(self)
        env.api.put(1, status="a")
        env.write_config({"items": env.source("/all")}, {"r": simple_rules()})
        env.ok(self, "run")                                            # 基準値
        for s in ("b", "a", "c"):
            env.api.put(1, status=s)
            env.ok(self, "fetch")
        env.ok(self, "evaluate")                                       # 溜まった 3 回分を古い順に判定（A→B→A も）
        n = env.api.count()
        out = env.ok(self, "replay", "--rule", "r", "--dry-run")
        self.assertIn("3 件のイベント", out)
        out = env.ok(self, "replay", "--rule", "r")
        self.assertIn("新しく積んだのは 0 件", out)                       # 既にあるイベントと同じ ID
        self.assertEqual(env.api.count(), n)                           # API には問い合わせない
        events = json.loads(env.ok(self, "events", "--json"))
        self.assertEqual(len(events), 3)

    def test_evaluation_error_is_isolated(self):
        env = Env(self)
        env.api.put(1, status="open")
        env.write_config({"items": env.source("/all")},
                         {"good": simple_rules("d"),
                          "bad": simple_rules("e", targets=[{"dag": "e", "params": {"X": "{{ event.nothing }}"}}])})
        env.ok(self, "run")
        env.api.put(1, status="closed")
        code, out = env.run("run")
        self.assertEqual(code, 4)                                      # bad の受け渡しが失敗
        self.assertEqual([r["dag"] for r in env.enqueued()], ["d"])    # good は渡せている
        st = json.loads(env.ok(self, "status", "--json"))
        self.assertEqual(st["problems"][0]["rule"], "bad")


class DeliveryTest(unittest.TestCase):
    def _two_changes(self, env, **target):
        env.api.put(1, status="s0")
        env.api.put(2, status="s0")
        t = {"dag": "d"}
        t.update(target)
        env.write_config({"items": env.source("/all")}, {"r": simple_rules(t)})
        env.ok(self, "run")
        env.api.put(1, status="s1")
        env.ok(self, "fetch")
        env.api.put(1, status="s2")
        env.api.put(2, status="s1")
        env.ok(self, "fetch")
        env.ok(self, "evaluate")

    def test_order_completion(self):
        env = Env(self)
        self._two_changes(env)
        env.ok(self, "dispatch")
        got = [(r["params"]["APIWATCH_RECORD_ID"], r["run_id"]) for r in env.enqueued()]
        self.assertEqual([g[0] for g in got], ["1", "2"])              # 1 の 2 回目は 1 回目の完了待ち、2 は並ぶ
        out = env.ok(self, "dispatch")
        self.assertIn("完了待ち 1", out)
        env.ok(self, "ack", got[0][1])
        env.ok(self, "dispatch")
        ev = [json.loads(Path(r["params"]["APIWATCH_EVENT_FILE"]).read_text(encoding="utf-8")) for r in env.enqueued()]
        self.assertEqual([(e["record_key"], e["after"]["status"]) for e in ev], [("1", "s1"), ("2", "s1"), ("1", "s2")])

    def test_order_enqueue(self):
        env = Env(self)
        self._two_changes(env, order="enqueue")
        env.ok(self, "dispatch")
        self.assertEqual([r["params"]["APIWATCH_RECORD_ID"] for r in env.enqueued()], ["1", "1", "2"])

    def test_resend_with_same_id_and_isolation(self):
        env = Env(self)
        env.api.put(1, status="a")
        env.api.put(2, status="a")
        env.write_config({"items": env.source("/all")},
                         {"r": simple_rules({"dag": "flaky", "max_attempts": 2, "retry_backoff_sec": 0}, "steady")})
        env.ok(self, "run")
        env.fail_dags("flaky")
        env.api.put(1, status="b")
        env.api.put(2, status="b")
        code, _ = env.run("run")
        self.assertEqual(code, 4)
        self.assertEqual(sorted(r["dag"] for r in env.enqueued()), ["steady", "steady"])   # 他の受け渡し先は進む
        flaky = [d for d in env.deliveries() if d["target"] == "flaky"]
        self.assertEqual({d["status"] for d in flaky}, {"send_failed"})
        env.run("dispatch")
        self.assertEqual({d["status"] for d in env.deliveries() if d["target"] == "flaky"}, {"dead"})
        env.fail_dags()
        env.ok(self, "dispatch")
        self.assertEqual(len(env.enqueued()), 2)                       # dead は手で retry するまで止まる
        env.ok(self, "retry", "--rule", "r")
        env.ok(self, "dispatch")
        flaky_runs = sorted(r["run_id"] for r in env.enqueued() if r["dag"] == "flaky")
        self.assertEqual(flaky_runs, sorted(d["id"] for d in flaky))   # 同じ ID で渡し直す

    def test_crash_during_dispatch_does_not_duplicate(self):
        env = Env(self)
        env.api.put(1, status="a")
        env.write_config({"items": env.source("/all")}, {"r": simple_rules("d")})
        env.ok(self, "run")
        env.api.put(1, status="b")
        env.ok(self, "run")
        d = env.deliveries()[0]
        # Dagu へは渡せたが、記録する前にプロセスが止まった状態を作る
        s = env.store()
        with s.tx() as db:
            db.execute("UPDATE deliveries SET status='sending', lease_until=?, owner='gone' WHERE id=?",
                       (time.time() - 1, d["id"]))
        s.close()
        out = env.ok(self, "dispatch")
        self.assertIn("既に渡し済み 1", out)
        self.assertEqual(len(env.enqueued()), 1)
        self.assertEqual(env.deliveries()[0]["status"], "dispatched")

    def test_ack_failed_and_retry_in_dagu(self):
        env = Env(self)
        env.api.put(1, status="a")
        env.write_config({"items": env.source("/all")}, {"r": simple_rules("d")})
        env.ok(self, "run")
        env.api.put(1, status="b")
        env.ok(self, "run")
        did = env.deliveries()[0]["id"]
        env.ok(self, "ack", did, "--failed", "--message", "step failed")
        self.assertEqual(env.deliveries()[0]["status"], "failed")
        out = env.ok(self, "status")
        self.assertIn("step failed", out)
        env.ok(self, "retry", did)
        self.assertEqual(env.enqueued()[-1], {"cmd": "retry", "dag": "d", "run_id": did})
        self.assertEqual(env.deliveries()[0]["status"], "dispatched")
        env.ok(self, "ack", did)
        self.assertEqual(env.deliveries()[0]["status"], "done")
        code, _ = env.run("ack", "aw_nope")
        self.assertEqual(code, 1)

    def test_skip_unblocks(self):
        env = Env(self)
        self._two_changes(env)
        env.ok(self, "dispatch")
        first = env.enqueued()[0]["run_id"]
        env.ok(self, "skip", first)
        env.ok(self, "dispatch")
        self.assertEqual(len(env.enqueued()), 3)

    def test_api_backend(self):
        env = Env(self)
        dagu = FakeDaguAPI()
        self.addCleanup(dagu.close)
        conns = json.loads(env.conn_file.read_text())
        conns["dagu"] = [{"label": "default", "url": dagu.url, "token": dagu.token}]
        env.conn_file.write_text(json.dumps(conns))
        env.api.put(1, status="a")
        env.write_config({"items": env.source("/all")},
                         {"r": simple_rules({"dag": "handler.yaml", "params": {"TITLE": "{{ event.after.status }}"},
                                             "queue": "q1"})},
                         dagu={"mode": "api", "connection": "dagu"})
        env.ok(self, "run")
        env.api.put(1, status="b")
        env.ok(self, "run")
        did = env.deliveries()[0]["id"]
        self.assertEqual(dagu.runs[("handler", did)]["params"]["TITLE"], "b")
        self.assertEqual(dagu.calls[-1][2]["queue"], "q1")
        out = env.ok(self, "reconcile")
        self.assertIn("変化なし 1", out)
        dagu.runs[("handler", did)]["status"] = "failed"
        env.ok(self, "reconcile")
        self.assertEqual(env.deliveries()[0]["status"], "failed")
        env.ok(self, "retry", did)
        dagu.runs[("handler", did)]["status"] = "succeeded"
        env.ok(self, "reconcile")
        self.assertEqual(env.deliveries()[0]["status"], "done")
        # 再送しても 409 already exists なら渡し済み
        s = env.store()
        with s.tx() as db:
            db.execute("UPDATE deliveries SET status='pending' WHERE id=?", (did,))
        s.close()
        out = env.ok(self, "dispatch")
        self.assertIn("既に渡し済み 1", out)
        self.assertNotIn(dagu.token, out)


class RetentionTest(unittest.TestCase):
    def test_gc_keeps_unprocessed(self):
        env = Env(self)
        env.api.put(1, status="a")
        src = {"items": env.source("/page", records="data", pagination={"type": "page", "size_param": "per_page",
                                                                         "size": 10})}
        ret = {"fetches_keep": 1, "fetch_days": 0, "events_days": 0, "auto_gc": False}
        env.write_config(src, {"r": simple_rules("d")}, retention=ret)
        env.ok(self, "run")
        for s in ("b", "c", "d"):
            env.api.put(1, status=s)
            env.ok(self, "fetch")
        env.ok(self, "gc")
        st = env.store()
        self.assertEqual(st.one("SELECT COUNT(*) c FROM fetches WHERE status='complete'")["c"], 3)   # 未判定の 3 回
        st.close()
        env.ok(self, "evaluate")
        env.ok(self, "dispatch")
        env.ok(self, "gc")
        st = env.store()
        self.assertEqual(st.one("SELECT COUNT(*) c FROM fetches WHERE status='complete'")["c"], 1)
        self.assertEqual(st.one("SELECT COUNT(*) c FROM events")["c"], 3)                         # 未完了は残す
        st.close()
        for d in env.deliveries():
            if d["status"] == "dispatched":
                env.ok(self, "ack", d["id"])
        env.ok(self, "dispatch")
        for d in env.deliveries():
            if d["status"] == "dispatched":
                env.ok(self, "ack", d["id"])
        env.ok(self, "dispatch")
        for d in env.deliveries():
            if d["status"] == "dispatched":
                env.ok(self, "ack", d["id"])
        env.ok(self, "gc")
        st = env.store()
        self.assertEqual(st.one("SELECT COUNT(*) c FROM events")["c"], 0)
        st.close()


class OpsTest(unittest.TestCase):
    def test_dags_and_check(self):
        env = Env(self)
        env.write_config({"items": env.source("/all", schedule="*/10 * * * *")}, {"r": simple_rules("d")},
                         dagu={"mode": "cli", "executable": [sys.executable, str(FAKE_DAGU)],
                               "dotenv": ["~/.config/dagu/api-watch.env"]})
        out = env.ok(self, "check")
        self.assertIn("問題ありません", out)
        self.assertNotIn(TOKEN, out)
        env.ok(self, "dags", "--out", str(env.tmp / "dags"))
        names = sorted(p.name for p in (env.tmp / "dags").iterdir())
        self.assertEqual(names, ["api-watch-dispatch.yaml", "api-watch-items.yaml", "api-watch-maintenance.yaml"])
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML が無いので生成した YAML の中身は見ない")
        d = yaml.safe_load((env.tmp / "dags" / "api-watch-items.yaml").read_text(encoding="utf-8"))
        self.assertEqual(d["schedule"], "*/10 * * * *")
        self.assertEqual([s["name"] for s in d["steps"]], ["fetch", "evaluate", "dispatch"])
        self.assertEqual(d["steps"][1]["depends"], ["fetch"])
        self.assertEqual(d["steps"][0]["retry_policy"]["exit_code"], [3])
        self.assertEqual(d["steps"][0]["with"]["args"][-3:], ["fetch", "--source", "items"])
        self.assertEqual(d["dotenv"], ["~/.config/dagu/api-watch.env"])

    def test_yaml_config_and_workspace_connections(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML が要る")
        env = Env(self)
        env.api.put(1, status="a")
        ws = env.tmp / "ws"
        (ws / ".github").mkdir(parents=True)
        (ws / ".github" / "connections.yaml").write_text(
            f"fakeapi:\n  - label: work\n    url: {env.api.url}\n    token: ${{API_TOKEN}}\n", encoding="utf-8")
        (ws / "conf").mkdir()
        env.config_path = ws / "conf" / "api-watch.yaml"
        env.config_path.write_text(f"""
state_dir: ../state
dagu:
  executable: [{json.dumps(sys.executable)}, {json.dumps(str(FAKE_DAGU))}]
sources:
  items:
    connection: fakeapi:work
    request:
      url: "{{{{ conn.url }}}}/all"
      headers:
        Authorization: "Bearer {{{{ conn.token }}}}"
rules:
  r:
    source: items
    id: id
    initial: emit
    targets: [d]
""", encoding="utf-8")
        env.ok(self, "run")
        self.assertEqual(len(env.enqueued()), 1)
        os.environ.pop("API_TOKEN")
        code, out = env.run("fetch")
        self.assertEqual(code, 1)
        self.assertIn("API_TOKEN が設定されていません", out)

    def test_example_config_loads(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML が要る")
        from apiwatch import config  # noqa: E402
        cfg = config.load(ROOT / "api-watch.yaml.example")
        self.assertEqual(sorted(cfg.sources), ["gitlab-issues", "tickets"])
        self.assertEqual(cfg.rules["urgent-tickets"].targets[1].name, "ticket-audit")
        self.assertEqual(cfg.rules["bug-labeled"].on, ["entered"])          # YAML 1.1 で on: が true にならない


if __name__ == "__main__":
    unittest.main()
