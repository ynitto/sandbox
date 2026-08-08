"""agent-flow の単体テスト — waits（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from _shared import _park_args  # noqa: E402,F401 — `import *` は _ 始まりを持ってこない


class VerifyGateTests(unittest.TestCase):
    def test_normalize_verify_from_json(self):
        d = kf._normalize_verify("verify=fail", {"ok": False, "issues": ["x"]})
        self.assertFalse(d["ok"])
        self.assertEqual(d["issues"], ["x"])

    def test_normalize_verify_from_text(self):
        self.assertFalse(kf._normalize_verify("verify=fail: 件数不一致", None)["ok"])
        self.assertTrue(kf._normalize_verify("verify=pass 問題なし", None)["ok"])

    def test_is_gate_result(self):
        self.assertTrue(kf._is_gate_result({"kind": "verify", "data": {"ok": True}}))
        self.assertTrue(kf._is_gate_result({"output": "verify=pass", "data": {"ok": True}}))
        self.assertFalse(kf._is_gate_result({"kind": "work", "data": {"ok": True}}))
        self.assertFalse(kf._is_gate_result({"data": [1, 2]}))
        self.assertFalse(kf._is_gate_result({"output": "x"}))


class ReviewGateTests(unittest.TestCase):
    def test_fanout_inserts_gate_before_synthesize(self):
        strat, tasks = kf.plan_strategy_stub("A; B; C", review=True)
        by = {t["id"]: t for t in tasks}
        self.assertIn("gate", by)
        self.assertEqual(by["gate"]["kind"], "verify")
        self.assertIn("gate", by["synth"]["deps"])        # 統合は gate を待つ
        self.assertIn("t1", by["synth"]["deps"])          # 統合は成果も集約する
        self.assertIn("adversarial-verification", strat["patterns"])

    def test_map_reduce_gate_between_map_and_reduce(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["x", "y"]}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, max_fanout=50, review=True)
        by = {t["id"]: t for t in new}
        self.assertIn("s-gate", by)
        self.assertEqual(by["s-gate"]["kind"], "verify")
        self.assertIn("s-gate", by["s-reduce"]["deps"])    # reduce は gate を待つ
        self.assertIn("s-m1", by["s-reduce"]["deps"])      # reduce は map 成果を集約
        self.assertEqual(by["s-gate"]["deps"], ["s-m1", "s-m2"])


class WaitingStateTests(unittest.TestCase):
    """park & poll: waits/ レコードと waiting 状態（node_state の縮退・pick_claimable/_quiesced 連携）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-wait-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")
        self.bus.write_graph({"nodes": {"n1": {"goal": "g", "deps": []}}, "iteration": 0})
        self.bus.write_task({"id": "n1", "goal": "g", "deps": []})

    def _rec(self, **over):
        base = {"id": "n1", "who": "w1", "kind": "work", "executor": "gitlab",
                "issue": {"host": "h", "project": "p", "iid": 5, "url": "u"},
                "throttled": False, "active_seen": False,
                "wait_lease_until": time.time() + 1000, "next_poll_at": 0,
                "started_at": time.time(), "timeout": 0, "approved_timeout": 0}
        base.update(over)
        return base

    def test_live_wait_is_waiting_state_and_not_claimable(self):
        self.bus.write_wait("n1", self._rec())
        self.assertEqual(self.bus.node_state("n1"), "waiting")
        self.assertIsNone(kf.pick_claimable(self.bus))           # waiting は claim 不可
        self.assertEqual(self.bus.run_view("run1").node_state("n1"), "waiting")
        # run_claimable_count からも除外される（daemon が worker を起こさない）
        self.assertEqual(self.bus.run_claimable_count("run1"), 0)

    def test_status_render_shows_waiting(self):
        # 回帰: park 中のノードが status に出ないと、全ノード承認待ちの run が「進捗 0/N・
        # 実行中ゼロ」としか見えず、止まっているのか待っているのか画面から区別できない。
        self.bus.write_wait("n1", self._rec())
        _, text = kf._render_status(self.bus, "run1", 0)
        self.assertIn("waiting=1", text)
        self.assertIn(kf._STATE_GLYPH["waiting"], text)

    def test_status_render_shows_run_phase_and_labels_graph_progress_as_work(self):
        self.bus.set_phase("verifying", "orch")
        _, text = kf._render_status(self.bus, "run1", 0)
        self.assertIn("phase   : 検証中", text)
        self.assertIn("work    :", text)
        self.assertNotIn("progress:", text)

    def test_expired_wait_falls_back_to_pending(self):
        # wait_lease 失効＝監視主体が居ない → pending へ縮退（full worker が再アタッチで拾える）
        self.bus.write_wait("n1", self._rec(wait_lease_until=time.time() - 1))
        self.assertEqual(self.bus.node_state("n1"), "pending")
        self.assertIsNotNone(kf.pick_claimable(self.bus))

    def test_result_wins_over_wait(self):
        # 決着（result）は wait より優先。node_state は result の status を返す
        self.bus.write_wait("n1", self._rec())
        self.bus.write_result("n1", "svc", "done", "ok")
        self.assertEqual(self.bus.node_state("n1"), "done")

    def test_quiesced_treats_waiting_as_in_flight(self):
        self.bus.write_wait("n1", self._rec())
        graph = self.bus.read_graph()
        self.assertFalse(kf._quiesced(self.bus, graph["nodes"]))  # waiting は静止させない

    def test_open_wait_count_excludes_throttled(self):
        self.bus.write_wait("n1", self._rec())                    # 起票済み
        self.bus.write_wait("n2", self._rec(id="n2", throttled=True, issue=None))
        self.assertEqual(self.bus.open_wait_count(), 1)           # throttled は数えない

    def test_release_claim_frees_slot(self):
        self.assertTrue(self.bus.try_claim("n1", "w1", 100))
        self.assertEqual(self.bus.node_state("n1"), "claimed")
        self.bus.release_claim("n1", "w1")
        self.assertEqual(self.bus.node_state("n1"), "pending")


class ServiceWaitsTests(unittest.TestCase):
    """service_waits: park 済みノードを poll して決着（done/failed）・据え置き・締切・throttle 解除。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-svc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")
        self.bus.set_status("running")

    def _park(self, nid="n1", **over):
        rec = {"id": nid, "who": "w1", "kind": "work", "executor": "gitlab",
               "issue": {"host": "h", "project": "p", "iid": 5, "url": "u"},
               "throttled": False, "active_seen": False,
               "wait_lease_until": time.time() + 1000, "next_poll_at": 0,
               "started_at": time.time(), "timeout": 0, "approved_timeout": 0}
        rec.update(over)
        self.bus.write_wait(nid, rec)

    def _run(self, poll_fn):
        args = _park_args()
        with mock.patch.object(kf, "executor_hook",
                               side_effect=lambda a, name: poll_fn if name == "poll" else None):
            return kf.service_waits(self.bus, args, only_runs=["run1"], daemon_id="t")

    def test_approved_writes_done_result_and_clears_wait(self):
        self._park()
        self._run(lambda st: {"decision": "approved", "text": "ok", "data": {"decision": "approved"}})
        self.assertEqual(self.bus.node_state("n1"), "done")
        self.assertIsNone(self.bus.read_wait("n1"))              # wait 記録は掃除される

    def test_rejected_writes_failed_result(self):
        self._park()
        self._run(lambda st: {"decision": "rejected", "text": "[gitlab-reject] x",
                              "data": {"decision": "rejected", "guidance": "直して"}})
        res = self.bus.read_result("n1")
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["data"]["decision"], "rejected")
        self.assertIsNone(self.bus.read_wait("n1"))

    def test_undecided_keeps_wait_and_renews_lease(self):
        self._park(wait_lease_until=time.time() + 1)
        self._run(lambda st: {"decision": None, "active_seen": True})
        rec = self.bus.read_wait("n1")
        self.assertIsNotNone(rec)                                # 据え置き
        self.assertTrue(rec["active_seen"])                      # 人の作業検知を反映
        self.assertGreater(rec["wait_lease_until"], time.time() + 100)  # lease を更新
        self.assertGreater(rec["next_poll_at"], time.time())    # バックオフ

    def test_deadline_exceeded_fails(self):
        # started_at が古く timeout 到達 → poll せず failed に終端（消費者の永久待機を防ぐ）
        self._park(started_at=time.time() - 100, timeout=1)
        called = {"n": 0}
        def poll(st):
            called["n"] += 1
            return {"decision": None}
        self._run(poll)
        self.assertEqual(self.bus.node_state("n1"), "failed")
        self.assertEqual(called["n"], 0)                        # 締切超過は poll せず即終端

    def test_next_poll_at_backoff_skips_poll(self):
        self._park(next_poll_at=time.time() + 1000)
        called = {"n": 0}
        self._run(lambda st: called.__setitem__("n", called["n"] + 1) or {"decision": None})
        self.assertEqual(called["n"], 0)                        # まだ再確認時刻でない

    def test_next_poll_at_backoff_still_renews_lease(self):
        # バックオフ中も監視主体が生きている証拠として lease を更新する
        old_lease = time.time() + 5
        self._park(next_poll_at=time.time() + 1000, wait_lease_until=old_lease)
        called = {"n": 0}
        self._run(lambda st: called.__setitem__("n", called["n"] + 1) or {"decision": None})
        self.assertEqual(called["n"], 0)
        rec = self.bus.read_wait("n1")
        self.assertIsNotNone(rec)
        self.assertGreater(rec["wait_lease_until"], old_lease + 100)

    def test_throttled_released_when_slot_frees(self):
        # 起票済み 0 件・cap 1 → throttled park を解除（node は pending へ）
        self._park("n2", throttled=True, issue=None)
        args = _park_args(gitlab={"watch_interval": 90.0, "max_open_issues": 1})
        with mock.patch.object(kf, "executor_hook",
                               side_effect=lambda a, name: (lambda st: {"decision": None})
                               if name == "poll" else None):
            kf.service_waits(self.bus, args, only_runs=["run1"], daemon_id="t")
        self.assertIsNone(self.bus.read_wait("n2"))             # 解除された

    def test_builtin_executor_noop(self):
        # poll フックが無い executor（kiro/stub）では何もしない
        self._park()
        n = kf.service_waits(self.bus, _park_args(executor="stub"), only_runs=["run1"])
        self.assertEqual(n, 0)
        self.assertIsNotNone(self.bus.read_wait("n1"))          # 触られない

    def test_defer_disabled_makes_service_waits_noop(self):
        # defer_waits=false（従来モード）では service_waits は何もしない（park が無いので監視も不要）
        self._park()
        args = _park_args(gitlab={"defer_waits": False, "watch_interval": 90.0})
        called = {"n": 0}
        with mock.patch.object(kf, "executor_hook",
                               side_effect=lambda a, name:
                               (lambda st: called.__setitem__("n", called["n"] + 1) or {"decision": None})
                               if name == "poll" else None):
            n = kf.service_waits(self.bus, args, only_runs=["run1"])
        self.assertEqual(n, 0)
        self.assertEqual(called["n"], 0)                        # poll すら呼ばれない
        self.assertIsNotNone(self.bus.read_wait("n1"))          # 触られない

    def test_only_runs_partitions_watching(self):
        # 分散: 担当外の run の park は触らない（監視を run オーナーに分担＝重複ポーリングを防ぐ）。
        self.bus.run_view("run2")  # ビューだけ（別 run）
        b2 = kf.Bus(self.tmp, "run2")
        b2.ensure_run("req2")
        b2.set_status("running")
        self._park()                                             # run1 に park
        b2.write_wait("m1", {"id": "m1", "who": "w", "kind": "work", "executor": "gitlab",
                             "issue": {"host": "h", "project": "p", "iid": 9, "url": "u"},
                             "throttled": False, "active_seen": False,
                             "wait_lease_until": time.time() + 1000, "next_poll_at": 0,
                             "started_at": time.time(), "timeout": 0, "approved_timeout": 0})
        polled = []
        with mock.patch.object(kf, "executor_hook",
                               side_effect=lambda a, name:
                               (lambda st: polled.append(st["issue"]["iid"]) or {"decision": None})
                               if name == "poll" else None):
            kf.service_waits(self.bus, _park_args(), only_runs=["run1"], daemon_id="ownerA")
        self.assertEqual(polled, [5])                            # run1 の #5 だけ。run2 の #9 は触らない
        self.assertIsNotNone(b2.read_wait("m1"))                # run2 の park は別オーナーが見る


class CancelTests(unittest.TestCase):
    """cancel: cancelled 終端状態・マーカー・mark_canceled・run 化前 cancel・waits 掃除。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-cancel-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")
        self.bus.set_status("running")

    def test_canceled_is_terminal(self):
        self.assertIn("cancelled", kf.TERMINAL)

    def test_legacy_spelling_is_still_read_as_terminal(self):
        """語彙統一（W0-9）前に cancel された run はバス上に旧綴りのまま残る。
        非終端と読むと active_runs に戻り、孤児回収で failed 化されて蘇る。"""
        self.assertIn("canceled", kf.TERMINAL)
        meta_path = self.bus.run_view("run1").meta_path
        meta = kf.read_json(meta_path)
        meta["status"] = "canceled"
        kf.write_json_atomic(meta_path, meta)
        self.assertNotIn("run1", self.bus.active_runs())
        self.assertFalse(self.bus.mark_canceled("run1"))     # 既に終端＝上書きしない

    def test_mark_canceled_sets_status_and_excludes_from_active(self):
        self.assertTrue(self.bus.mark_canceled("run1", "手動"))
        self.assertEqual(self.bus.run_meta("run1").get("status"), "cancelled")
        self.assertEqual(self.bus.run_meta("run1").get("cancel_reason"), "手動")
        self.assertNotIn("run1", self.bus.active_runs())        # 終端＝孤児 reclaim もしない

    def test_mark_canceled_noop_when_terminal(self):
        self.bus.set_status("done")
        self.assertFalse(self.bus.mark_canceled("run1"))        # done は上書きしない
        self.assertEqual(self.bus.run_meta("run1").get("status"), "done")

    def test_cancel_request_marker_and_clear_waits(self):
        self.bus.write_wait("n1", {"id": "n1", "wait_lease_until": time.time() + 1000,
                                   "issue": {"iid": 1}})
        self.bus.cancel_request("run1", "host", "止める", close_issues=False)
        self.assertTrue(self.bus.is_canceled_requested("run1"))
        self.assertEqual(self.bus.cancel_info("run1")["reason"], "止める")
        self.assertEqual(self.bus.clear_waits_for_run("run1"), 1)
        self.assertIsNone(self.bus.read_wait("n1"))

    def test_cancel_request_run_before_run_exists(self):
        # run 化前の要求を cancelled で終端化（消費者が終端を観測でき、daemon が再受理しない）
        b = kf.Bus(self.tmp, "req-new")
        b.submit_request("req-new", "やること", "submitter")
        self.assertFalse(b.run_exists("req-new"))
        self.assertTrue(b.cancel_request_run("req-new", "run 化前 cancel"))
        self.assertEqual(b.run_meta("req-new").get("status"), "cancelled")

    def test_cmd_cancel_marks_and_keeps_intent_until_owner_acknowledges(self):
        self.bus.write_wait("n1", {"id": "n1", "wait_lease_until": time.time() + 1000,
                                   "issue": {"iid": 1}})
        args = argparse.Namespace(bus=self.tmp, run_id="run1", reason="緊急停止",
                                  close_issues=False, git=None, executor="stub",
                                  config=None, lease=30.0)
        with mock.patch.object(kf, "make_bus", return_value=self.bus):
            rc = kf.cmd_cancel(args)
        self.assertEqual(rc, 0)
        self.assertEqual(self.bus.run_meta("run1").get("status"), "cancelled")
        self.assertTrue(self.bus.is_canceled_requested("run1"), "実行所有者が止まるまで意図を残す")
        self.assertIsNone(self.bus.read_wait("n1"))             # park 再ポーリングを止める

    def test_touch_run_converges_to_cancelled_when_cancel_races_heartbeat(self):
        original_write = kf.write_json_atomic
        heartbeat_read = threading.Event()
        allow_heartbeat_write = threading.Event()

        def delayed_write(path, data):
            if (path == self.bus.meta_path and data.get("orch_lease_until") is not None
                    and data.get("status") == "running"):
                heartbeat_read.set()
                allow_heartbeat_write.wait(1.0)
            original_write(path, data)

        with mock.patch.object(kf, "write_json_atomic", side_effect=delayed_write):
            th = threading.Thread(target=lambda: self.bus.touch_run("run1", 120.0))
            th.start()
            self.assertTrue(heartbeat_read.wait(1.0))
            self.bus.cancel_request("run1", "host", "停止")
            self.bus.mark_canceled("run1", "停止")
            allow_heartbeat_write.set()
            th.join(1.0)
        self.assertEqual(self.bus.run_meta("run1").get("status"), "cancelled")

    def test_cmd_cancel_terminal_clears_waits_but_keeps_owner_intent(self):
        self.bus.set_status("done")
        self.bus.write_wait("n1", {"id": "n1", "wait_lease_until": time.time() + 1000})
        self.bus.cancel_request("run1", "host", "古い")
        args = argparse.Namespace(bus=self.tmp, run_id="run1", reason="",
                                  close_issues=False, git=None, executor="stub",
                                  config=None, lease=30.0)
        with mock.patch.object(kf, "make_bus", return_value=self.bus):
            rc = kf.cmd_cancel(args)
        self.assertEqual(rc, 0)
        self.assertIsNone(self.bus.read_wait("n1"))
        self.assertTrue(self.bus.is_canceled_requested("run1"))

    def test_orch_check_canceled(self):
        args = argparse.Namespace(run_id="run1")
        self.assertFalse(kf._orch_check_canceled(self.bus, args, "orch"))
        self.bus.write_wait("n1", {"id": "n1", "wait_lease_until": time.time() + 1000,
                                   "issue": {"iid": 1}})
        self.bus.cancel_request("run1", "host", "止める")
        self.assertTrue(kf._orch_check_canceled(self.bus, args, "orch"))
        self.assertEqual(self.bus.run_meta("run1").get("status"), "cancelled")
        self.assertIsNone(self.bus.read_wait("n1"), "orch 終端時に waits も消す")

    def test_orch_stops_when_meta_already_canceled_without_marker(self):
        # daemon が適用後にマーカーを消しても、meta=cancelled なら orch は止まる
        args = argparse.Namespace(run_id="run1")
        self.bus.mark_canceled("run1", "先に終端")
        self.assertFalse(self.bus.is_canceled_requested("run1"))
        self.assertTrue(kf._orch_check_canceled(self.bus, args, "orch"))

    def test_clear_cancel_removes_applied_marker(self):
        self.bus.cancel_request("run1", "host", "止める")
        self.assertTrue(self.bus.is_canceled_requested("run1"))
        self.assertTrue(self.bus.clear_cancel("run1"))
        self.assertFalse(self.bus.is_canceled_requested("run1"))
        self.assertFalse(self.bus.clear_cancel("run1"))  # 冪等

    def test_cmd_cancel_keeps_marker_before_run_exists(self):
        # run 化前 cancel: マーカーを残し daemon の cancel_request_run に渡す
        # （run_meta() の {} を truthy と誤判定して消さない）
        b = kf.Bus(self.tmp, "req-pre")
        b.submit_request("req-pre", "やること", "submitter")
        args = argparse.Namespace(bus=self.tmp, run_id="req-pre", reason="取り下げ",
                                  close_issues=False, git=None, executor="stub",
                                  config=None, lease=30.0)
        with mock.patch.object(kf, "make_bus", return_value=b):
            rc = kf.cmd_cancel(args)
        self.assertEqual(rc, 0)
        self.assertTrue(b.is_canceled_requested("req-pre"), "run 化前はマーカーを残す")
        self.assertFalse(b.run_exists("req-pre"))

    def test_set_status_refuses_to_resurrect_terminal(self):
        self.bus.mark_canceled("run1", "止める")
        self.bus.set_status("running")
        self.assertEqual(self.bus.run_meta("run1").get("status"), "cancelled")


class SessionCommandsTests(unittest.TestCase):
    """セッション開始コマンド（agent-session-commands 契約）: 計画の決定性・実行・非伝播・status。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kf-sess-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.home = tempfile.mkdtemp(prefix="kf-sess-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        os.environ["AGENT_SESSION_DIR"] = self.home
        self.addCleanup(os.environ.pop, "AGENT_SESSION_DIR", None)
        os.environ.pop("AGENT_FLOW_NO_SESSION_COMMANDS", None)
        self.addCleanup(os.environ.pop, "AGENT_FLOW_NO_SESSION_COMMANDS", None)
        kf._SESSION_COMMANDS_REV_APPLIED = None
        self.addCleanup(setattr, kf, "_SESSION_COMMANDS_REV_APPLIED", None)

    def _write(self, obj):
        with open(os.path.join(self.home, "session.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    # -- 読取のフェイルセーフ ------------------------------------------------

    def test_load_missing_and_broken_are_no_op(self):
        self.assertIsNone(kf.load_session_commands())
        with open(os.path.join(self.home, "session.json"), "w", encoding="utf-8") as f:
            f.write("{ 壊れた JSON")
        self.assertIsNone(kf.load_session_commands())
        self.assertEqual(kf.plan_session_commands(None, {"engine": "agent-flow"}), [])

    def test_disabled_is_complete_no_op(self):
        data = {"enabled": False, "commands": [{"id": "a", "run": "echo hi"}]}
        self.assertEqual(kf.plan_session_commands(data, {"engine": "agent-flow"}), [])

    # -- 計画の決定性 --------------------------------------------------------

    def test_placeholders_expand_deterministically_without_quoting(self):
        ctx = {"cwd": "/w/my repo", "run_id": "r1"}
        self.assertEqual(kf.expand_session_placeholders("cd {cwd} && ls", ctx), "cd /w/my repo && ls")
        self.assertEqual(kf.expand_session_placeholders("x {node_id} y", ctx), "x  y")
        self.assertEqual(kf.expand_session_placeholders("{unknown}", ctx), "{unknown}")

    def test_when_is_and_joined_and_absent_axes_pass(self):
        when = {"engines": ["agent-flow"], "workloads": ["flow"]}
        self.assertTrue(kf.session_command_matches(when, {"engine": "agent-flow", "workload": "flow"}))
        self.assertFalse(kf.session_command_matches(when, {"engine": "kiro-loop", "workload": "flow"}))
        self.assertTrue(kf.session_command_matches(None, {"engine": "kiro-loop"}))
        self.assertTrue(kf.session_command_matches(when, {}))

    def test_chat_is_skipped_on_single_shot_engine(self):
        data = {"commands": [{"id": "c", "mode": "chat", "run": "docs を読んで"}]}
        entries = kf.plan_session_commands(data, {"engine": "agent-flow"})
        self.assertEqual(entries[0]["skip"], "no-session")

    def test_total_budget_truncates_then_skips(self):
        data = {"max_total_timeout": 100, "commands": [
            {"id": "a", "run": "x", "timeout": 60},
            {"id": "b", "run": "y", "timeout": 60},
            {"id": "c", "run": "z", "timeout": 30},
        ]}
        entries = kf.plan_session_commands(data, {"engine": "agent-flow"})
        self.assertEqual(entries[0]["timeout"], 60)
        self.assertEqual(entries[1]["timeout"], 40)
        self.assertEqual(entries[2]["skip"], "budget")

    def test_plan_matches_dashboard_preview_shape(self):
        """dashboard（JS の plan）と同じキー・同じ既定値を返す（プレビューと実行が一致する）。"""
        entries = kf.plan_session_commands(
            {"commands": [{"id": "a", "run": "echo hi"}]}, {"engine": "agent-flow", "cwd": "/w"})
        self.assertEqual(entries[0]["cwd"], "/w")
        self.assertEqual(entries[0]["timeout"], 60)
        self.assertEqual(entries[0]["on_error"], "warn")
        self.assertIsNone(entries[0]["skip"])

    # -- 実行 ----------------------------------------------------------------

    def test_commands_run_in_array_order(self):
        marker = os.path.join(self.dir, "order.txt")
        self._write({"commands": [
            {"id": "first", "run": f"echo 1 >> {marker}"},
            {"id": "second", "run": f"echo 2 >> {marker}"},
        ]})
        self.assertTrue(kf.run_session_commands("w1", {"engine": "agent-flow"}))
        with open(marker, encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["1", "2"])

    def test_warn_continues_and_fail_aborts(self):
        marker = os.path.join(self.dir, "after.txt")
        self._write({"commands": [
            {"id": "bad", "run": "exit 3", "on_error": "warn"},
            {"id": "after", "run": f"echo ok > {marker}"},
        ]})
        self.assertTrue(kf.run_session_commands("w1", {"engine": "agent-flow"}))
        self.assertTrue(os.path.exists(marker), "warn は後続を止めない")

        os.remove(marker)
        self._write({"commands": [
            {"id": "bad", "run": "exit 3", "on_error": "fail"},
            {"id": "after", "run": f"echo ok > {marker}"},
        ]})
        self.assertFalse(kf.run_session_commands("w1", {"engine": "agent-flow"}))
        self.assertFalse(os.path.exists(marker), "fail は後続を実行しない")

    def test_env_and_cwd_are_applied(self):
        out = os.path.join(self.dir, "env.txt")
        self._write({"commands": [{
            "id": "e", "run": f"printf '%s %s' \"$SESS_TEST\" \"$(pwd)\" > {out}",
            "cwd": self.dir, "env": {"SESS_TEST": "v-{run_id}"},
        }]})
        self.assertTrue(kf.run_session_commands("w1", {"engine": "agent-flow", "run_id": "r9"}))
        with open(out, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("v-r9", body)
        self.assertIn(os.path.realpath(self.dir), os.path.realpath(body.split(" ", 1)[1]))

    def test_timeout_is_bounded(self):
        self._write({"commands": [{"id": "slow", "run": "sleep 5", "timeout": 1, "on_error": "fail"}]})
        started = time.time()
        self.assertFalse(kf.run_session_commands("w1", {"engine": "agent-flow"}))
        self.assertLess(time.time() - started, 4, "timeout で打ち切る")

    def test_env_opt_out_disables_everything(self):
        marker = os.path.join(self.dir, "never.txt")
        self._write({"commands": [{"id": "a", "run": f"echo x > {marker}"}]})
        os.environ["AGENT_FLOW_NO_SESSION_COMMANDS"] = "1"
        self.assertTrue(kf.run_session_commands("w1", {"engine": "agent-flow"}))
        self.assertFalse(os.path.exists(marker))

    # -- 非伝播と status -----------------------------------------------------

    def test_commands_are_never_snapshotted_into_meta(self):
        """副作用のあるコマンドは meta.json（＝GitBus）へ載せない。本契約の不変条件。"""
        src = pathlib.Path(kf.__file__).parent.joinpath("bus.py").read_text(encoding="utf-8") \
            if hasattr(kf, "__file__") and kf.__file__ else ""
        self.assertNotIn("session_commands", src)
        self.assertNotIn("session.json", src)

    def test_status_carries_session_commands_revision_applied(self):
        _prev_control = os.environ["AGENT_CONTROL_DIR"]
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        # pop すると**モジュール既定の隔離先ごと消え**、以降のテストが開発者の実
        # `~/.agents/control` を読む（テスト順で agent_cli 系が落ちる原因だった）。
        self.addCleanup(os.environ.__setitem__, "AGENT_CONTROL_DIR", _prev_control)
        kf._CONTROL_CACHE["mtime"] = None
        self._write({"revision": 7, "commands": [{"id": "a", "run": "true"}]})
        kf.run_session_commands("w1", {"engine": "agent-flow"})
        with mock.patch.object(kf, "_run_agent_once", return_value="ok"):
            kf.run_agent("x", None, purpose="worker")
        status_dir = os.path.join(self.dir, "status")
        files = [n for n in os.listdir(status_dir) if n.endswith(".json")]
        with open(os.path.join(status_dir, files[0]), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["session_commands_revision_applied"], 7)
