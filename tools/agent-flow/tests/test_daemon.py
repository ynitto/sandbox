"""agent-flow の単体テスト — daemon（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class OrphanRecoveryTests(unittest.TestCase):
    """owning daemon が消失した非終端 run（孤児）を生存リースで検知して回収する。
    これが無いと、再起動した新プロセスが前プロセスの status:running を見て何もせず、
    remote submit を待つ消費者が act_timeout まで永久待機する。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-test-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("test request")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_meta(self, **kw):
        meta = kf.read_json(self.bus.meta_path) or {}
        meta.update(kw)
        kf.write_json_atomic(self.bus.meta_path, meta)

    def test_touch_run_marks_alive(self):
        self.bus.set_status("running")
        self.bus.touch_run("run1", 120.0)
        meta = self.bus.run_meta("run1")
        self.assertIn("orch_lease_until", meta)
        self.assertIn("heartbeat_at", meta)
        self.assertFalse(self.bus.run_is_orphaned("run1", 120.0))   # 新鮮なリース = 生存

    def test_touch_run_noop_when_terminal(self):
        self.bus.set_status("done")
        self.bus.touch_run("run1", 120.0)
        self.assertNotIn("orch_lease_until", self.bus.run_meta("run1"))

    def test_expired_lease_is_orphan(self):
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        self.assertTrue(self.bus.run_is_orphaned("run1", 120.0))

    def test_no_lease_old_run_is_orphan(self):
        # owner が heartbeat する前に死んだ／本変更前から残る run はリース未記録 → age で判定
        self.bus.set_status("running")
        self._set_meta(created_at="2000-01-01T00:00:00Z", updated_at="2000-01-01T00:00:00Z")
        self.assertTrue(self.bus.run_is_orphaned("run1", 120.0))

    def test_no_lease_fresh_run_not_orphan(self):
        # リース未記録でも作成直後の run は孤児扱いしない（orchestrator spawn 直後の race を守る）
        self.bus.set_status("running")        # created_at は ensure_run の now
        self.assertFalse(self.bus.run_is_orphaned("run1", 120.0))

    def test_terminal_run_never_orphan(self):
        self.bus.set_status("done")
        self._set_meta(orch_lease_until=time.time() - 999.0)
        self.assertFalse(self.bus.run_is_orphaned("run1", 120.0))

    def test_orphan_recovered_via_mark_failed(self):
        # 孤児（非終端＋リース切れ）は mark_run_failed で終端化でき、消費者が失敗を検知して復旧できる
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        self.assertTrue(self.bus.run_is_orphaned("run1", 120.0))
        self.assertTrue(self.bus.mark_run_failed("run1", "orphaned"))
        self.assertEqual(self.bus.run_meta("run1")["status"], "failed")
        self.assertFalse(self.bus.run_is_orphaned("run1", 120.0))    # 終端化後は孤児でない

    def test_run_lease_window_bounds(self):
        self.assertEqual(kf._run_lease_window(types.SimpleNamespace(poll=2.0)), 120.0)
        self.assertEqual(kf._run_lease_window(types.SimpleNamespace(poll=20.0)), 200.0)
        self.assertEqual(kf._run_lease_window(types.SimpleNamespace(poll=None)), 120.0)

    def test_blocking_orchestrator_phase_keeps_heartbeat_alive(self):
        calls = []

        def heartbeat(force=False):
            calls.append(force)

        result = kf._with_run_heartbeat(
            heartbeat, 0.03, lambda: (time.sleep(0.05), "done")[1])
        self.assertEqual(result, "done")
        self.assertGreaterEqual(len(calls), 2)

    def test_blocking_orchestrator_phase_waits_for_inflight_heartbeat(self):
        entered = threading.Event()
        release = threading.Event()

        def heartbeat(force=False):
            entered.set()
            release.wait()

        done = threading.Event()
        outer = threading.Thread(target=lambda: (
            kf._with_run_heartbeat(heartbeat, 0.03, lambda: entered.wait()), done.set()))
        outer.start()
        self.assertTrue(entered.wait(1.0))
        time.sleep(0.02)
        self.assertFalse(done.is_set(), "heartbeat 実行中に wrapper が戻ってはいけない")
        release.set()
        outer.join(1.0)
        self.assertTrue(done.is_set())

    def _args(self, **kw):
        base = dict(max_resumes=3, lease=1800.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _adopt(self, owned=None, spawn=None, **kw):
        spawned = []

        def fake_spawn(base, args, req_id, req):
            spawned.append(req_id)
            return types.SimpleNamespace(poll=lambda: None)   # 生きている子のふり

        adopted, failed = kf._adopt_orphan_runs(
            self.bus, "d2", owned or set(), 120.0, self._args(**kw), [],
            spawn=spawn or fake_spawn)
        return adopted, failed, spawned

    def test_orphan_inbox_run_is_resumed_not_failed(self):
        # PC シャットダウン等で owning daemon が消えた run は failed でなく再開（引き継ぎ）する
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, spawned = self._adopt()
        self.assertEqual(list(adopted), ["run1"])
        self.assertEqual(failed, [])
        self.assertEqual(spawned, ["run1"])
        meta = self.bus.run_meta("run1")
        self.assertEqual(meta["status"], "running")             # failed にしない
        self.assertEqual(meta["resume_count"], 1)
        self.assertGreater(meta.get("orch_lease_until", 0), time.time())  # 生存リースを張り直す

    def test_orphan_failed_after_max_resumes_without_progress(self):
        # 進捗（results/）ゼロのまま連続再開が上限を超えたら従来どおり failed に確定する
        self.bus.submit_request("run1", "req", "submitter")
        for i in range(2):
            self.bus.set_status("running")
            self._set_meta(orch_lease_until=time.time() - 1.0)
            adopted, failed, _ = self._adopt(max_resumes=2)
            self.assertEqual(list(adopted), ["run1"], f"resume #{i+1}")
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, _ = self._adopt(max_resumes=2)
        self.assertEqual(adopted, {})
        self.assertEqual(failed, ["run1"])
        self.assertEqual(self.bus.run_meta("run1")["status"], "failed")
        self.assertIn("orphaned", self.bus.run_meta("run1")["failure_reason"])

    def test_resume_count_resets_on_progress(self):
        # 前回の再開以降に results が増えていれば数え直す＝進捗のある長期 run は何度でも再開できる
        self.bus.submit_request("run1", "req", "submitter")
        for i in range(4):                                       # max_resumes=2 を超える回数
            self.bus.set_status("running")
            self._set_meta(orch_lease_until=time.time() - 1.0)
            adopted, failed, _ = self._adopt(max_resumes=2)
            self.assertEqual(list(adopted), ["run1"], f"resume #{i+1}")
            self.assertEqual(self.bus.run_meta("run1")["resume_count"], 1)  # 進捗ありで毎回リセット
            self.bus.write_result(f"t{i}", "w1", "done", "ok")   # 再開後に進捗が出た
        self.assertEqual(failed, [])

    def test_resume_count_resets_on_live_park(self):
        # 生存 park（承認待ち）だけの run も「進捗なし」扱いしない＝一晩の再起動で orphaned にしない
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.write_graph({"nodes": {"n1": {"id": "n1", "kind": "task", "deps": []}}})
        for i in range(4):
            self.bus.write_wait("n1", {
                "id": "n1", "who": "w1",
                "wait_lease_until": time.time() + 1000,
                "next_poll_at": time.time() + 100,
            })
            self.bus.set_status("running")
            self._set_meta(orch_lease_until=time.time() - 1.0)
            adopted, failed, _ = self._adopt(max_resumes=2)
            self.assertEqual(list(adopted), ["run1"], f"park resume #{i+1}")
            self.assertEqual(failed, [])
            self.assertEqual(self.bus.run_meta("run1")["resume_count"], 1)

    def test_resume_count_resets_on_expired_park_lease(self):
        # 一晩電源断で wait_lease だけ切れても、wait ファイルが残れば進捗あり＝数え直し
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.write_graph({"nodes": {"n1": {"id": "n1", "kind": "task", "deps": []}}})
        for i in range(4):
            self.bus.write_wait("n1", {
                "id": "n1", "who": "w1",
                "wait_lease_until": time.time() - 10,
                "next_poll_at": time.time() - 5,
            })
            self.bus.set_status("running")
            self._set_meta(orch_lease_until=time.time() - 1.0)
            adopted, failed, _ = self._adopt(max_resumes=2)
            self.assertEqual(list(adopted), ["run1"], f"expired-park resume #{i+1}")
            self.assertEqual(failed, [])
            self.assertEqual(self.bus.run_meta("run1")["resume_count"], 1)

    def test_orphan_failed_when_resume_disabled(self):
        # max_resumes<=0 は従来動作（孤児は即 failed）へのオプトアウト
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, spawned = self._adopt(max_resumes=0)
        self.assertEqual(adopted, {})
        self.assertEqual(failed, ["run1"])
        self.assertEqual(spawned, [])
        self.assertEqual(self.bus.run_meta("run1")["status"], "failed")

    def test_adopt_skips_owned_run(self):
        # 自分が今 orchestrator を回している run は（リースが古くても）引き継がない
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, _ = self._adopt(owned={"run1"})
        self.assertEqual((adopted, failed), ({}, []))
        self.assertEqual(self.bus.run_meta("run1")["status"], "running")

    def test_adopt_skips_live_run(self):
        # 別デーモンが heartbeat 中（リース新鮮）の run は引き継がない（誤って横取りしない）
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.set_status("running")
        self.bus.touch_run("run1", 120.0)
        adopted, failed, _ = self._adopt()
        self.assertEqual((adopted, failed), ({}, []))
        self.assertEqual(self.bus.run_meta("run1")["status"], "running")

    def test_adopt_waits_for_stale_claim_lease(self):
        # 消失した旧 owner の inbox claim がまだ lease 内なら引き継ぎを保留する
        # （lease 失効後の poll で自然に再試行される。failed にはしない）
        self.bus.submit_request("run1", "req", "submitter")
        self.assertTrue(self.bus.reclaim_request("run1", "dead-daemon", 1800.0))
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, _ = self._adopt()
        self.assertEqual((adopted, failed), ({}, []))
        self.assertEqual(self.bus.run_meta("run1")["status"], "running")

    def _expire_inbox_claim(self, req_id: str, who: str) -> None:
        """inbox claim の lease を過去へ倒す（＝旧 owner が消えた状態を作る）。

        **極端に短い lease を張って sleep で失効させない。** `try_claim` は「自分の claim を
        書く → 勝者判定」の順で動くので、lease がその往復より短いと *claim した瞬間に自分の
        lease が切れて* 勝者判定に負け、`reclaim_request` 自体が偶発的に False を返す
        （0.01 秒 lease + 0.05 秒 sleep の旧実装は、この機構で 40 回中 3 回落ちていた）。
        リースの失効は時間ではなく値で作る——他のリース系テスト（`_set_meta` で
        `orch_lease_until` を過去にする）と同じ流儀。"""
        path = os.path.join(self.bus.inbox_claims_dir, req_id,
                            f"{kf.protocol.safe_name(who)}.json")
        rec = kf.read_json(path)
        rec["lease_until"] = time.time() - 1.0
        kf.write_json_atomic(path, rec)

    def test_reclaim_after_owner_lease_expiry(self):
        # 旧 owner の claim が lease 切れなら reclaim できる（run が存在していても）
        self.bus.submit_request("run1", "req", "submitter")
        self.assertTrue(self.bus.reclaim_request("run1", "dead-daemon", 120.0))
        self._expire_inbox_claim("run1", "dead-daemon")
        self.assertFalse(self.bus.claim_request("run1", "d2", 120.0))   # 従来 API は run 存在で拒否
        self.assertTrue(self.bus.reclaim_request("run1", "d2", 120.0))  # 引き継ぎ用は claim できる

    # --- 世代交代（superseded）: 新世代のリトライに引き継がれた旧 run を復活させない ---
    def _make_orphan(self, run_id, inherit_from=None):
        """run_id を「実行中だが生存リース切れ（孤児）」で作る（inherit_from 付きの inbox 要求も）。"""
        self.bus.submit_request(run_id, "req", "submitter", inherit_from=inherit_from)
        v = self.bus.run_view(run_id)
        v.ensure_run("req")
        v.set_status("running")
        meta = kf.read_json(v.meta_path)
        meta["orch_lease_until"] = time.time() - 1.0    # リース切れ = 孤児
        kf.write_json_atomic(v.meta_path, meta)

    def test_superseded_map_built_from_inherit_from(self):
        # inbox の inherit_from から {先行 run: 新世代 req} の対応表を作る
        self.bus.submit_request("run1", "req", "s")
        self.bus.submit_request("run2", "req", "s", inherit_from="run1")
        self.bus.submit_request("run3", "req", "s", inherit_from="run2")
        self.assertEqual(kf._superseded_run_ids(self.bus), {"run1": "run2", "run2": "run3"})

    def test_mark_run_superseded_records_and_is_terminal(self):
        self.bus.set_status("running")
        self.assertTrue(self.bus.mark_run_superseded("run1", "run2"))
        m = self.bus.run_meta("run1")
        self.assertEqual(m["status"], "failed")     # 終端（active_runs/孤児判定から外れる）
        self.assertTrue(m["superseded"])
        self.assertEqual(m["superseded_by"], "run2")
        self.assertFalse(self.bus.run_is_orphaned("run1", 120.0))   # 終端化後は孤児でない

    def test_mark_run_superseded_noop_when_terminal(self):
        # 既に終端した run は上書きしない（done を failed で潰さない）
        self.bus.set_status("done")
        self.assertFalse(self.bus.mark_run_superseded("run1", "run2"))
        self.assertEqual(self.bus.run_meta("run1")["status"], "done")

    def test_superseded_run_drops_out_of_active_runs(self):
        # superseded で終端化した run は active_runs から外れ、worker が湧かない（二重実行を止める）
        self.bus.set_status("running")
        self.assertIn("run1", self.bus.active_runs())
        self.bus.mark_run_superseded("run1", "run2")
        self.assertNotIn("run1", self.bus.active_runs())

    def test_superseded_orphan_is_terminated_not_resumed(self):
        # daemon 再起動: 新世代 run2 に inherit_from で引き継がれた孤児 run1 は、再開せず終端化する。
        # 素朴に全孤児を再開すると世代交代で消えるべき旧リトライが復活して二重実行になるのを防ぐ。
        self._make_orphan("run1")                       # 旧世代
        self._make_orphan("run2", inherit_from="run1")  # 新世代（run1 を引き継ぐ）
        adopted, failed, spawned = self._adopt()
        self.assertNotIn("run1", adopted)               # 旧世代は復活させない
        self.assertNotIn("run1", spawned)               # orchestrator も起動しない
        self.assertIn("run1", failed)                   # 終端化した
        m1 = self.bus.run_meta("run1")
        self.assertEqual(m1["status"], "failed")
        self.assertTrue(m1.get("superseded"))
        self.assertEqual(m1.get("superseded_by"), "run2")
        self.assertIn("run2", adopted)                  # 新世代は通常どおり再開
        self.assertIn("run2", spawned)

    def test_only_latest_generation_survives_restart(self):
        # r0←r1←r2 の 3 世代が孤児として残っていても、再起動で再開するのは最新世代だけ。
        self._make_orphan("r0")
        self._make_orphan("r1", inherit_from="r0")
        self._make_orphan("r2", inherit_from="r1")
        adopted, failed, spawned = self._adopt()
        self.assertEqual(list(adopted), ["r2"])         # 最新世代のみ再開
        self.assertEqual(sorted(failed), ["r0", "r1"])  # 旧世代は終端化
        self.assertEqual(self.bus.run_meta("r0")["status"], "failed")
        self.assertEqual(self.bus.run_meta("r1")["status"], "failed")
        self.assertEqual(self.bus.run_meta("r2")["status"], "running")

    def test_latest_generation_still_resumed_when_predecessor_present(self):
        # 引き継ぎ元（先行 run）が存在しない孤児（初回 run）は従来どおり再開する（誤終端しない）
        self._make_orphan("solo")
        adopted, failed, spawned = self._adopt()
        self.assertEqual(list(adopted), ["solo"])
        self.assertEqual(failed, [])


class DaemonTickOrderingTests(unittest.TestCase):
    """cmd_daemon から抽出した tick 関数群（実装計画 W1-2）の順序制約:
    `_tick_cancel` は必ず `_tick_orphan_adopt` より先に呼ぶ。

    `_adopt_orphan_runs` 自体は cancel マーカーを見ない（`run_is_orphaned` が
    status in TERMINAL で弾くだけ）。cancel と孤児化が同時に成立した run で
    正しい順序（cancel が先）なら再開されず、逆順なら誤って resume されることを
    往復で固定する。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-daemon-order-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.ensure_run("test request")
        self.bus.set_status("running")
        meta = kf.read_json(self.bus.meta_path) or {}
        meta["orch_lease_until"] = time.time() - 1.0   # owning daemon 消失（孤児条件）
        kf.write_json_atomic(self.bus.meta_path, meta)
        self.bus.cancel_request("run1", "tester", "テスト都合")   # cancel 条件も同時に成立

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _args(self):
        return types.SimpleNamespace(max_resumes=3, lease=1800.0)

    def _fake_spawn(self):
        spawned = []

        def spawn(base, args, req_id, req):
            spawned.append(req_id)
            return types.SimpleNamespace(poll=lambda: None, terminate=lambda: None)
        return spawn, spawned

    def test_cancel_before_orphan_adopt_does_not_resume(self):
        orchestrators = {}
        kf._tick_cancel(self.bus, self._args(), "d1", orchestrators, [])
        self.assertEqual(self.bus.run_meta("run1")["status"], "cancelled")

        spawn, spawned = self._fake_spawn()
        adopted, failed = kf._adopt_orphan_runs(
            self.bus, "d1", set(orchestrators), 120.0, self._args(), [], spawn=spawn)
        self.assertEqual(adopted, {})
        self.assertEqual(spawned, [])   # cancel 済み run を resume しない

    def test_orphan_adopt_before_cancel_wrongly_resumes(self):
        # 逆順で呼ぶと _adopt_orphan_runs は cancel マーカーを見ないため resume してしまう
        # （このテストは順序制約の理由そのものを固定する — 正しい呼び出し順は上のテスト）。
        spawn, spawned = self._fake_spawn()
        adopted, failed = kf._adopt_orphan_runs(
            self.bus, "d1", set(), 120.0, self._args(), [], spawn=spawn)
        self.assertEqual(list(adopted), ["run1"])
        self.assertEqual(spawned, ["run1"])   # 誤って resume された


class OrchestratorLeaseTests(unittest.TestCase):
    """orchestrator は自分が駆動している run に生存リース（heartbeat）を張る。

    張らないと、daemon を介さず `agent-flow run` で都度起動される run（agent-project の主経路）は
    orch_lease_until を永久に持たない。消費者側は「lease が無い run は生きているのか死んで
    いるのか」を決められず、orchestrator が消えた run は status=running のまま固まって、
    失敗ノードも未実行ノードも二度と動かない（9/31 ノードまで進んだ run が宙吊りになった）。
    計画は LLM 呼び出しで数十秒かかるので、その前に張る必要がある。"""

    def test_orchestrate_takes_lease_before_planning(self):
        root = tempfile.mkdtemp(prefix="kf-lease-")
        self.addCleanup(shutil.rmtree, root, True)
        rid = "run-x"
        args = argparse.Namespace(
            config=None, bus=root, git=None, git_branch="main", git_subdir=None,
            lease=30.0, run_id=rid, request="x", planner="stub", executor=None, model=None,
            poll=0.01, max_iterations=1, max_fanout=4, max_retries=1, review=None,
            granularity="finest", exemplar_first=False, cleanup_clone=True, repos=None,
            keep_clone=False, node_id="orchestrator", workspace=None, references=None,
            inherit_from=None, orphan_grace=0.0)
        kf.resolve_config(args)
        # 計画（LLM）に入る手前で止める。ここまでに lease が張られていなければならない。
        with mock.patch.object(kf, "_plan_strategy", side_effect=RuntimeError("stop-here")):
            with self.assertRaises(RuntimeError):
                kf.cmd_orchestrate(args)
        meta = json.loads((pathlib.Path(root, "runs", rid, "meta.json")).read_text())
        self.assertIsInstance(meta.get("orch_lease_until"), (int, float),
                              "計画前に生存リースを張ること")
        self.assertGreater(meta["orch_lease_until"], time.time(),
                           "張ったリースは未来を指すこと")


class DaemonPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-daemon-")
        self.bus = kf.Bus(self.tmp, "_")

    def test_default_daemon_id_is_stable_hostname_without_pid(self):
        # 実装計画 W1-10: node_id は PC 名で安定させる（daemon 再起動ごとに変わらない）。
        args = types.SimpleNamespace(node_id=None)
        first = kf._default_daemon_id(args)
        second = kf._default_daemon_id(args)
        self.assertEqual(first, second)
        # 綴りは agentcore の共通正規化。_safe だと大小文字がそのまま残り、同じ PC が
        # flow で `Mac`・amigos で `mac` になって板に 2 ノードとして現れる。
        self.assertEqual(first, kf.normalize_node_id(kf.socket.gethostname()))
        self.assertNotIn(str(os.getpid()), first)

    def test_default_daemon_id_matches_amigos_for_same_host(self):
        # 同一 PC の板上の身元は 1 つ（設計 §3.1）。エンジンをまたいで綴りが一致すること。
        host = "My PC"
        flow_id = kf.normalize_node_id(host)
        amigos_id = kf.normalize_node_id(host)   # amigos も同じ agentcore 実装を使う
        self.assertEqual(flow_id, amigos_id)
        self.assertEqual(kf._safe(flow_id), flow_id)   # 板の書き込み側に掛けても不変

    def test_default_daemon_id_respects_explicit_node_id(self):
        args = types.SimpleNamespace(node_id="pc-explicit")
        self.assertEqual(kf._default_daemon_id(args), "pc-explicit")

    def test_submit_and_inbox(self):
        self.bus.submit_request("req1", "do things", "tester")
        self.assertIn("req1", self.bus.list_inbox())
        self.assertEqual(self.bus.read_inbox("req1")["request"], "do things")

    def test_claim_request_single_winner(self):
        self.bus.submit_request("req1", "x", "t")
        a = self.bus.claim_request("req1", "daemonA", 60)
        b = self.bus.claim_request("req1", "daemonB", 60)
        self.assertNotEqual(a, b)            # ちょうど 1 台が勝つ
        self.assertTrue(a and not b)         # 先に claim した A が勝者

    def test_claim_request_false_if_run_exists(self):
        self.bus.submit_request("req1", "x", "t")
        kf.Bus(self.tmp, "req1").ensure_run("x")  # 既に run が作られている
        self.assertFalse(self.bus.claim_request("req1", "daemonC", 60))

    def test_workspace_roundtrip_via_meta(self):
        # ワークスペース（唯一の書込先）は run meta に載り、submit→inbox でも伝搬する
        ws = {"url": "https://x/a.git", "path": "apps/api", "base": "main", "target": "develop"}
        b = kf.Bus(self.tmp, "runR")
        b.ensure_run("goal", workspace=ws)
        self.assertEqual(b.run_workspace(), ws)
        self.bus.submit_request("reqR", "do", "t", workspace={"url": "https://x/c.git"})
        self.assertEqual(self.bus.read_inbox("reqR")["workspace"], {"url": "https://x/c.git"})
        # ワークスペース無し → 読み取り専用 run（None）
        b2 = kf.Bus(self.tmp, "runRO")
        b2.ensure_run("just investigate")
        self.assertIsNone(b2.run_workspace())

    def test_references_roundtrip_via_meta(self):
        # 参照リポジトリ（読むだけ）は run meta に載り、submit→inbox でも伝搬する
        refs = kf.parse_references(["https://x/spec.git",
                                    '{"url":"https://x/lib.git","path":"src","desc":"lib"}'])
        self.assertEqual([r["url"] for r in refs], ["https://x/spec.git", "https://x/lib.git"])
        b = kf.Bus(self.tmp, "runRef")
        b.ensure_run("goal", workspace={"url": "https://x/app.git"}, references=refs)
        self.assertEqual(b.run_references(), refs)
        self.bus.submit_request("reqRef", "do", "t", references=refs)
        self.assertEqual(self.bus.read_inbox("reqRef")["references"], refs)
        # 参照節の指示文（エージェント向け）に url が載る
        self.assertIn("https://x/lib.git", kf.reference_instruction(refs))
        self.assertEqual(kf.reference_instruction([]), "")

    def _make_remote(self, name="remote_repo", base="main", subfile=None):
        """ローカルの『リモート』を git init で用意する（push 先になる非 bare リポジトリ）。"""
        remote = os.path.join(self.tmp, name)
        os.makedirs(remote)
        for cmd in (["git", "init", "-q", "-b", base, remote],
                    ["git", "-C", remote, "config", "user.email", "t@t"],
                    ["git", "-C", remote, "config", "user.name", "t"]):
            subprocess.run(cmd, check=True)
        target = os.path.join(remote, subfile) if subfile else os.path.join(remote, "f.txt")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w").close()
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "init"], check=True)
        return remote

    def test_clone_repo_retries_then_succeeds(self):
        # 委譲される側（実作業ノード）のワークスペース clone も一過性障害でリトライして成功する。
        remote = self._make_remote()
        dest = os.path.join(self.dir if hasattr(self, "dir") else tempfile.mkdtemp(), "ws-clone")
        real_run = subprocess.run
        calls = {"n": 0}

        def flaky(cmd, *a, **kw):
            # 1 パス目（`-b main` とフォールバックの 2 呼び出し）を両方失敗させ、2 パス目で成功させる
            if isinstance(cmd, list) and cmd[:2] == ["git", "clone"]:
                calls["n"] += 1
                if calls["n"] <= 2:
                    return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access")
            return real_run(cmd, *a, **kw)

        slept = []
        # 差し替えるのはリトライのバックオフ seam。素の time.sleep は stdlib の共有モジュールで、
        # 差し替えると subprocess の内部ポーリング（0.001s 倍々）の sleep まで記録に混入する。
        with mock.patch.object(kf.subprocess, "run", side_effect=flaky), \
             mock.patch.object(kf, "backoff_sleep", side_effect=lambda s: slept.append(s)):
            out = kf._clone_repo(remote, "main", dest)
        self.assertEqual(out, dest)                       # 最終的に成功
        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))
        self.assertEqual(slept, [1])                      # 1 パス目失敗 → バックオフ → 2 パス目で成功

    def test_clone_repo_gives_up_after_retries(self):
        # ずっと失敗するなら最後の git エラーを保って失敗する。
        def always_fail(cmd, *a, **kw):
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access")

        with mock.patch.object(kf.subprocess, "run", side_effect=always_fail), \
             mock.patch.object(kf, "backoff_sleep", side_effect=lambda s: None):
            with self.assertRaisesRegex(RuntimeError, "fatal: unable to access"):
                kf._clone_repo("https://x/y.git", "main", os.path.join(tempfile.mkdtemp(), "d"))

    def test_ensure_workspace_clone_creates_run_branch(self):
        # 作業ツリーが用意され、作業ブランチ名 af/<run-id> がエージェントへ渡る。
        # 共有 cache 経由では detached worktree（.git はファイル）で、実ブランチは push 時に作る。
        remote = self._make_remote()
        try:
            ws = kf.ensure_workspace_clone({"url": remote, "base": "main"}, "run-1")
            path = ws["clone"]
            self.assertTrue(path and os.path.exists(os.path.join(path, ".git")))  # worktree=ファイル/clone=dir
            self.assertEqual(ws["branch"], "af/run-1")
            inside = subprocess.run(["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
                                    capture_output=True, text=True).stdout.strip()
            self.assertEqual(inside, "true")               # git 作業ツリーである
            head = subprocess.run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            self.assertEqual(head, "HEAD")                 # detached（ブランチ二重 checkout 制約を受けない）
            self.assertIn(path, kf.workspace_instruction(ws))
        finally:
            kf.cleanup_workspace()
        self.assertFalse(path and os.path.exists(path))    # 作業後に消える（クリーン必須）

    def test_finalize_workspace_commits_and_pushes_changes(self):
        # エージェントが編集 → finalize が作業ブランチへ commit して push する
        remote = self._make_remote(name="ws_push")
        try:
            ws = kf.ensure_workspace_clone({"url": remote, "base": "main", "target": "main"},
                                           "run-2")
            with open(os.path.join(ws["clone"], "new.txt"), "w") as fh:
                fh.write("change")
            delivery = kf.finalize_workspace(ws, "run-2", "t1")
            self.assertIsNotNone(delivery)
            self.assertEqual(delivery["branch"], "af/run-2")
            self.assertTrue(delivery["commit"])
            # リモートに作業ブランチが反映され、変更が含まれる
            ls = subprocess.run(["git", "-C", remote, "rev-parse", "--verify", "af/run-2"],
                                capture_output=True, text=True)
            self.assertEqual(ls.returncode, 0)
            files = subprocess.run(["git", "-C", remote, "ls-tree", "-r", "--name-only", "af/run-2"],
                                   capture_output=True, text=True).stdout
            self.assertIn("new.txt", files)
        finally:
            kf.cleanup_workspace()

    def test_finalize_workspace_noop_when_no_changes(self):
        # 調査タスク等（変更ゼロ）はブランチを push しない＝読み取り専用グラフでは何もしない
        remote = self._make_remote(name="ws_noop")
        try:
            ws = kf.ensure_workspace_clone({"url": remote, "base": "main"}, "run-3")
            self.assertIsNone(kf.finalize_workspace(ws, "run-3", "t1"))  # 変更なし → None
            ls = subprocess.run(["git", "-C", remote, "rev-parse", "--verify", "af/run-3"],
                                capture_output=True, text=True)
            self.assertNotEqual(ls.returncode, 0)         # 作業ブランチは push されない
        finally:
            kf.cleanup_workspace()

    def test_finalize_normal_work_autofixes_whitespace_errors(self):
        # 行末空白だけで成果を捨てない（機械的に直して commit する）
        remote = self._make_remote(name="ws_whitespace")
        try:
            ws = kf.ensure_workspace_clone({"url": remote, "base": "main"}, "run-bad-ws")
            with open(os.path.join(ws["clone"], "bad.txt"), "w", encoding="utf-8") as fh:
                fh.write("new trailing whitespace  \n")
            delivery = kf.finalize_workspace(ws, "run-bad-ws", "t1")
            self.assertIsNotNone(delivery)
            pushed = subprocess.run(["git", "-C", remote, "show", "af/run-bad-ws:bad.txt"],
                                    capture_output=True, text=True, check=True).stdout
            self.assertEqual(pushed, "new trailing whitespace\n")
        finally:
            kf.cleanup_workspace()

    def test_finalize_normal_work_rejects_unfixable_whitespace_errors(self):
        # 直せない指摘（設定依存の tab-in-indent 等）は従来どおり失敗させる
        remote = self._make_remote(name="ws_whitespace_hard")
        try:
            ws = kf.ensure_workspace_clone({"url": remote, "base": "main"}, "run-hard-ws")
            subprocess.run(["git", "-C", ws["clone"], "config", "core.whitespace",
                            "tab-in-indent"], check=True, capture_output=True)
            with open(os.path.join(ws["clone"], "bad.py"), "w", encoding="utf-8") as fh:
                fh.write("def f():\n\treturn 1\n")
            with self.assertRaisesRegex(RuntimeError, "差分品質"):
                kf.finalize_workspace(ws, "run-hard-ws", "t1")
        finally:
            kf.cleanup_workspace()

    def test_sync_workspace_base_merges_clean_target_without_agent_git(self):
        remote = self._make_remote(name="base_sync_clean")
        subprocess.run(["git", "-C", remote, "checkout", "-qb", "ap/T1"], check=True)
        with open(os.path.join(remote, "task.txt"), "w", encoding="utf-8") as fh:
            fh.write("task\n")
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "task"], check=True)
        subprocess.run(["git", "-C", remote, "checkout", "-q", "main"], check=True)
        # target 側が tree 差分を持たないコミットでも、祖先関係を作る merge commit は必要。
        subprocess.run(["git", "-C", remote, "commit", "--allow-empty", "-qm", "main"], check=True)
        try:
            ws = kf.ensure_workspace_clone(
                {"url": remote, "base": "main", "target": "main", "branch": "ap/T1"}, "run-sync")
            synced = kf.sync_workspace_base(ws)
            self.assertEqual(synced["status"], "merged")
            delivery = kf.finalize_workspace(ws, "run-sync", "base-sync")
            self.assertIsNotNone(delivery)
            target = subprocess.run(["git", "-C", remote, "rev-parse", "main"],
                                    capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(subprocess.run(
                ["git", "-C", remote, "merge-base", "--is-ancestor", target, "ap/T1"]
            ).returncode, 0)
        finally:
            kf.cleanup_workspace()

    def test_sync_workspace_base_marks_unclassified_fetch_failure_transient(self):
        failed = types.SimpleNamespace(returncode=1, stdout="", stderr="remote unavailable")
        with mock.patch.object(kf, "_ws_git", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, r"\[agent-error:transient\]"):
                kf.sync_workspace_base({"clone": "/tmp/work", "target": "main"})

    def test_sync_workspace_base_exposes_conflicts_and_refuses_markers(self):
        remote = self._make_remote(name="base_sync_conflict")
        with open(os.path.join(remote, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "base"], check=True)
        subprocess.run(["git", "-C", remote, "checkout", "-qb", "ap/T1"], check=True)
        with open(os.path.join(remote, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("task\n")
        subprocess.run(["git", "-C", remote, "commit", "-qam", "task"], check=True)
        subprocess.run(["git", "-C", remote, "checkout", "-q", "main"], check=True)
        with open(os.path.join(remote, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("main\n")
        subprocess.run(["git", "-C", remote, "commit", "-qam", "main"], check=True)
        try:
            ws = kf.ensure_workspace_clone(
                {"url": remote, "base": "main", "target": "main", "branch": "ap/T1"}, "run-conflict")
            synced = kf.sync_workspace_base(ws)
            self.assertEqual(synced["status"], "conflict")
            self.assertEqual(synced["conflict_files"], ["f.txt"])
            with self.assertRaisesRegex(RuntimeError, "競合マーカー"):
                kf.finalize_workspace(ws, "run-conflict", "base-sync")
        finally:
            kf.cleanup_workspace()

    def test_finalize_base_sync_accepts_target_trailing_whitespace(self):
        remote = self._make_remote(name="base_sync_whitespace")
        subprocess.run(["git", "-C", remote, "checkout", "-qb", "ap/T1"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "--allow-empty", "-qm", "task"], check=True)
        subprocess.run(["git", "-C", remote, "checkout", "-q", "main"], check=True)
        with open(os.path.join(remote, "doc.md"), "w", encoding="utf-8") as fh:
            fh.write("既存の末尾空白  \n")
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "docs"], check=True)
        try:
            ws = kf.ensure_workspace_clone(
                {"url": remote, "base": "main", "target": "main", "branch": "ap/T1"}, "run-ws")
            self.assertEqual(kf.sync_workspace_base(ws)["status"], "merged")
            self.assertIsNotNone(kf.finalize_workspace(ws, "run-ws", "base-sync"))
        finally:
            kf.cleanup_workspace()

    def test_inject_base_sync_precedes_every_root_node(self):
        nodes = {
            "t1": {"goal": "a", "deps": [], "kind": "work"},
            "t2": {"goal": "b", "deps": ["t1"], "kind": "verify"},
        }
        task = kf.inject_base_sync(nodes, {
            "url": "https://example/repo.git", "branch": "ap/T1", "target": "main"})
        self.assertEqual(task["id"], "base-sync")
        self.assertEqual(nodes["t1"]["deps"], ["base-sync"])
        self.assertEqual(nodes["t2"]["deps"], ["t1"])

    def test_inject_base_sync_skips_read_only_and_same_branch(self):
        self.assertIsNone(kf.inject_base_sync({}, None))
        self.assertIsNone(kf.inject_base_sync({}, {
            "url": "https://example/repo.git", "branch": "main", "target": "main"}))

    def test_verifier_refreshes_branch_after_base_sync_push(self):
        remote = self._make_remote(name="verify_refresh")
        try:
            ws = kf.ensure_workspace_clone(
                {"url": remote, "base": "main", "target": "main", "branch": "ap/T1"},
                "run-refresh")
            clone = ws["clone"]
            before = kf._vp_result_rev(clone)
            subprocess.run(["git", "-C", remote, "checkout", "-qb", "ap/T1"], check=True)
            subprocess.run(["git", "-C", remote, "commit", "--allow-empty", "-qm", "sync"], check=True)
            after = subprocess.run(["git", "-C", remote, "rev-parse", "HEAD"],
                                   capture_output=True, text=True, check=True).stdout.strip()
            kf._vp_refresh_result(clone, "ap/T1")
            self.assertNotEqual(before, after)
            self.assertEqual(kf._vp_result_rev(clone), after)
        finally:
            kf.cleanup_workspace()

    def test_ensure_workspace_clone_checks_out_base_content(self):
        # base 指定があればそのブランチ内容から作業ブランチを作る（base の成果物が見える）
        remote = self._make_remote(name="branched_repo", base="main")
        subprocess.run(["git", "-C", remote, "checkout", "-q", "-b", "develop"], check=True)
        open(os.path.join(remote, "dev.txt"), "w").close()
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "dev"], check=True)
        ws_spec = {"url": remote, "base": "develop", "path": "apps/api",
                   "target": "main", "desc": "API"}
        try:
            ws = kf.ensure_workspace_clone(ws_spec, "run-4")
            path = ws["clone"]
            self.assertTrue(os.path.exists(os.path.join(path, "dev.txt")))   # develop の内容
            instr = kf.workspace_instruction(ws)
            self.assertIn("apps/api", instr)               # path（モノレポのフォルダ）
            self.assertIn("af/run-4", instr)               # 作業ブランチ
            self.assertIn("main", instr)                   # target（MR/PR ターゲット）
        finally:
            kf.cleanup_workspace()

    def test_provision_tree_reuses_shared_mirror(self):
        # 共有 cache: 2 回 provision してもミラー clone は 1 回きり（再 clone しない＝負荷削減の本体）。
        remote = self._make_remote(name="cache_reuse")
        cache_dir = os.path.join(self.tmp, "gitcache")
        prev = os.environ.get("KIRO_GIT_CACHE_DIR")
        os.environ["KIRO_GIT_CACHE_DIR"] = cache_dir
        mirror_calls = {"n": 0}
        real_clone = kf._mirror_clone

        def counting(url, cache):
            mirror_calls["n"] += 1
            return real_clone(url, cache)

        try:
            with mock.patch.object(kf, "_mirror_clone", side_effect=counting):
                d1 = os.path.join(self.tmp, "wt1")
                d2 = os.path.join(self.tmp, "wt2")
                p1 = kf.provision_tree(remote, ["main"], d1)
                p2 = kf.provision_tree(remote, ["main"], d2)
            self.assertEqual(p1, d1)
            self.assertEqual(p2, d2)
            self.assertEqual(mirror_calls["n"], 1)         # ミラーは初回 1 回のみ
            # 2 つの worktree は同じ共有ミラーに登録される
            self.assertTrue(os.path.exists(os.path.join(d1, ".git")))
            self.assertTrue(os.path.exists(os.path.join(d2, ".git")))
        finally:
            kf._prune_caches(kf._provisioned_urls)
            kf._provisioned_urls.clear()
            if prev is None:
                os.environ.pop("KIRO_GIT_CACHE_DIR", None)
            else:
                os.environ["KIRO_GIT_CACHE_DIR"] = prev

    def test_provision_tree_reflects_latest_after_fetch(self):
        # INV-1 鮮度: ミラーは再利用しつつ、provision のたびに fetch して最新コミットで worktree を作る。
        remote = self._make_remote(name="cache_fresh")
        cache_dir = os.path.join(self.tmp, "gitcache2")
        prev = os.environ.get("KIRO_GIT_CACHE_DIR")
        os.environ["KIRO_GIT_CACHE_DIR"] = cache_dir
        try:
            d1 = os.path.join(self.tmp, "f1")
            self.assertEqual(kf.provision_tree(remote, ["main"], d1), d1)
            self.assertFalse(os.path.exists(os.path.join(d1, "added.txt")))
            # リモートに新コミットを追加 → 次の provision はそれを反映する
            open(os.path.join(remote, "added.txt"), "w").close()
            subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
            subprocess.run(["git", "-C", remote, "commit", "-qm", "add"], check=True)
            d2 = os.path.join(self.tmp, "f2")
            self.assertEqual(kf.provision_tree(remote, ["main"], d2), d2)
            self.assertTrue(os.path.exists(os.path.join(d2, "added.txt")))   # 最新が見える
        finally:
            kf._prune_caches(kf._provisioned_urls)
            kf._provisioned_urls.clear()
            if prev is None:
                os.environ.pop("KIRO_GIT_CACHE_DIR", None)
            else:
                os.environ["KIRO_GIT_CACHE_DIR"] = prev

    def test_provision_tree_falls_back_to_direct_clone(self):
        # INV-3: cache が使えない（ミラー作成不可）ときは従来の direct clone に倒れて作業を継続する。
        remote = self._make_remote(name="cache_fallback")
        cache_dir = os.path.join(self.tmp, "gitcache3")
        prev = os.environ.get("KIRO_GIT_CACHE_DIR")
        os.environ["KIRO_GIT_CACHE_DIR"] = cache_dir
        try:
            dest = os.path.join(self.tmp, "fb")
            with mock.patch.object(kf, "ensure_cache", return_value=None):
                out = kf.provision_tree(remote, ["main"], dest)
            self.assertEqual(out, dest)
            self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))   # direct clone（.git はディレクトリ）
        finally:
            kf._prune_caches(kf._provisioned_urls)
            kf._provisioned_urls.clear()
            if prev is None:
                os.environ.pop("KIRO_GIT_CACHE_DIR", None)
            else:
                os.environ["KIRO_GIT_CACHE_DIR"] = prev

    def test_parse_workspace_url_or_json(self):
        # 素の URL は url だけの spec。JSON は構造化メタを受ける。空は None（読み取り専用）
        u = kf.parse_workspace("https://git/app.git")
        self.assertEqual((u["url"], u["path"], u["base"]), ("https://git/app.git", "", ""))
        j = kf.parse_workspace(
            '{"url":"https://git/shop.git","path":"apps/api","base":"main",'
            '"target":"develop","desc":"API"}')
        self.assertEqual((j["path"], j["base"], j["target"], j["desc"]),
                         ("apps/api", "main", "develop", "API"))
        self.assertIsNone(kf.parse_workspace(None))
        self.assertIsNone(kf.parse_workspace(""))

    def test_parse_workspace_explicit_branch(self):
        # 明示 branch（agent-project のタスク単位ブランチ）は spec に載り、
        # ensure_workspace_clone が run 毎の af/<run-id> の代わりにそれを使う
        j = kf.parse_workspace('{"url":"https://git/app.git","base":"main","branch":"ap/T12"}')
        self.assertEqual(j["branch"], "ap/T12")
        captured = {}

        def fake_provision(url, refs, dest, local=""):
            captured["refs"] = list(refs)
            captured["local"] = local              # repos の local（手元クローン）も伝搬する
            return ""                              # clone 失敗扱い（実 git を叩かない）

        with mock.patch.object(kf, "provision_tree", side_effect=fake_provision):
            ws = kf.ensure_workspace_clone(j, "req-x-t-r1")
        self.assertEqual(ws["branch"], "ap/T12")   # af/req-x-t-r1 ではなくタスクブランチ
        self.assertEqual(captured["refs"][0], "ap/T12")   # 既存ブランチから再開（refs 先頭）
        # branch 無しなら従来どおり run 毎ブランチ
        plain = kf.parse_workspace('{"url":"https://git/app2.git","base":"main"}')
        with mock.patch.object(kf, "provision_tree", side_effect=fake_provision):
            ws2 = kf.ensure_workspace_clone(plain, "req-x-t-r1")
        self.assertEqual(ws2["branch"], kf.run_branch_name("req-x-t-r1"))

    def test_workspace_id_includes_path_and_base(self):
        # 同 URL でも path（モノレポのフォルダ）や base（作業ブランチ）が違えば別ワークスペース
        a = {"url": "https://git/shop.git", "path": "apps/api", "base": "main"}
        b = {"url": "https://git/shop.git", "path": "apps/web", "base": "main"}
        c = {"url": "https://git/shop.git", "path": "apps/api", "base": "develop"}
        self.assertNotEqual(kf.workspace_id(a), kf.workspace_id(b))   # path 違い
        self.assertNotEqual(kf.workspace_id(a), kf.workspace_id(c))   # base 違い
        self.assertEqual(kf.workspace_id(a), kf.workspace_id(dict(a)))

    def test_sweep_work_repo_dirs_reaps_dead_pid_only(self):
        # SIGKILL リーク回収: 死んだ pid の孤立 clone は消し、生存 pid（自分）のものは残す
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        dead_pid = 2147480000          # 存在しない（はずの）pid
        live = os.path.join(tmp, f"agent-flow-ws-{os.getpid()}-aaa")
        dead = os.path.join(tmp, f"agent-flow-ws-{dead_pid}-bbb")
        other = os.path.join(tmp, "unrelated-dir")
        for d in (live, dead, other):
            os.makedirs(d)
        with mock.patch("tempfile.gettempdir", return_value=tmp):
            removed = kf.sweep_work_repo_dirs(min_age_sec=0.0)
        self.assertEqual(removed, 1)
        self.assertTrue(os.path.isdir(live))      # 生存 pid → 残す
        self.assertFalse(os.path.exists(dead))    # 死亡 pid → 回収
        self.assertTrue(os.path.isdir(other))     # 無関係ディレクトリ → 触らない

    def test_sweep_work_repo_dirs_keeps_recent_even_if_pid_unknown(self):
        # min_age 未満かつ pid 生存中は残す（稼働中の clone を誤削除しない）
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        recent = os.path.join(tmp, f"agent-flow-ws-{os.getpid()}-ccc")
        os.makedirs(recent)
        with mock.patch("tempfile.gettempdir", return_value=tmp):
            self.assertEqual(kf.sweep_work_repo_dirs(min_age_sec=3600.0), 0)
        self.assertTrue(os.path.isdir(recent))

    def test_node_entry_drops_repos(self):
        # ワークスペースは run 単位なので、ノードに repo は持たせない
        e = kf._node_entry({"goal": "g", "deps": [], "kind": "work", "repos": ["a", "b"]})
        self.assertNotIn("repos", e)
        self.assertEqual((e["goal"], e["kind"]), ("g", "work"))

    def test_workspace_instruction_describes_single_writable(self):
        ws = {"url": "https://git/app.git", "path": "apps/api", "base": "develop",
              "target": "main", "desc": "API", "branch": "af/run-9", "clone": "/tmp/app"}
        instr = kf.workspace_instruction(ws)
        self.assertIn("唯一の書込先", instr)
        self.assertIn("apps/api", instr)
        self.assertIn("af/run-9", instr)
        self.assertIn("commit と push は agent-flow", instr)   # エージェントは編集のみ
        # clone 失敗時は書き込めない旨を明示
        self.assertIn("clone に失敗", kf.workspace_instruction({"url": "https://x/y.git"}))
        self.assertEqual(kf.workspace_instruction(None), "")

    def test_remove_run_also_purges_inbox(self):
        # gc（remove_run）は対応する inbox 要求と claim も消す。残すと run_exists が
        # 再び False になり、デーモンが完了済み要求を再実行してしまう（resurrection 防止）。
        self.bus.submit_request("req1", "x", "t")
        self.bus.claim_request("req1", "daemonA", 60)  # inbox/claims/req1 を作る
        kf.Bus(self.tmp, "req1").ensure_run("x")
        self.assertIn("req1", self.bus.list_inbox())
        self.bus.remove_run("req1")
        self.assertNotIn("req1", self.bus.list_inbox())          # inbox 要求が消えた
        self.assertFalse(self.bus.run_exists("req1"))            # run も消えた
        import os
        self.assertFalse(os.path.exists(
            os.path.join(self.bus.inbox_claims_dir, "req1")))   # claim も消えた
        # 消えた後は再 claim 可能にならない（要求自体が無い）
        self.assertEqual(self.bus.list_inbox(), [])

    def test_active_runs_and_claimable_count(self):
        v = kf.Bus(self.tmp, "runA")
        v.ensure_run("req")
        v.write_graph({"nodes": {"t1": {"goal": "a", "deps": []},
                                 "t2": {"goal": "b", "deps": ["t1"]}}, "iteration": 0})
        v.write_task({"id": "t1", "goal": "a", "deps": []})
        v.write_task({"id": "t2", "goal": "b", "deps": ["t1"]})
        v.set_status("running")
        # t1 は claim 可能、t2 は依存未充足なので不可 → count == 1
        self.assertIn("runA", self.bus.active_runs())
        self.assertEqual(self.bus.run_claimable_count("runA"), 1)
        # t1 完了で t2 が解放される
        v.write_result("t1", "w", "done", "o")
        self.assertEqual(self.bus.run_claimable_count("runA"), 1)
        # 終端した run は active_runs から外れる
        v.write_result("t2", "w", "done", "o")
        v.set_status("done")
        self.assertNotIn("runA", self.bus.active_runs())


class ParticipateE2ETests(unittest.TestCase):
    """inbox 投函 → `participate` が受理 → `run --from-inbox` が完走、の黒箱 e2e。

    DaemonPrimitiveTests が bus プリミティブ（submit/claim/inbox）を in-process で検証するのに対し、
    こちらは実プロセスとして 2 コマンドを繋ぎ、final.json 生成まで通す。常駐一本化後の実配線
    （ノード常駐体が participate の出力を run へディスパッチする）と同じ順序を再現している。"""

    def setUp(self):
        self.bus = tempfile.mkdtemp(prefix="kf-participate-e2e-")
        self._submitted = 0

    def tearDown(self):
        shutil.rmtree(self.bus, ignore_errors=True)

    def _submit(self, request):
        """公式の入力契約（inbox/<req-id>.json）へ直接投函する。
        agent-dashboard の委譲アダプタもこの契約で投函する（CLI は経由しない）。"""
        self._submitted += 1
        run_id = f"run-e2e-{self._submitted}"
        kf.Bus(self.bus, "test-submitter").submit_request(run_id, request, "test")
        return run_id

    def _participate(self, running=()):
        cmd = [sys.executable, str(SCRIPT), "--bus", self.bus, "participate",
               "--json", "--executor", "stub"]
        if running:
            cmd += ["--running", ",".join(running)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return [it["run_id"] for it in json.loads(p.stdout or "[]")]

    def _run(self, run_id):
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", self.bus, "--run-id", run_id,
             "run", "--from-inbox", "--planner", "stub", "--executor", "stub",
             "--workers", "3", "--poll", "0.2"],
            capture_output=True, text=True, timeout=180)
        final = os.path.join(self.bus, "runs", run_id, "final.json")
        data = kf.read_json(final) if os.path.exists(final) else None
        self.assertIsNotNone(data, f"final.json が現れず: {run_id}\n{p.stdout[-800:]}\n{p.stderr[-800:]}")
        return data

    def test_participate_accepts_inbox_and_run_completes(self):
        run_id = self._submit("x; y; z")
        self.assertEqual(self._participate(), [run_id])   # 受理して実行を呼び出し側へ引き渡す
        final = self._run(run_id)
        results = final["results"]
        # orchestrator → worker で fan-out-and-synthesize が完走（並列ノード + 統合）
        self.assertGreaterEqual(len(results), 4)
        self.assertIn("synth", results)
        for nid, r in results.items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")
            self.assertTrue(r["who"])             # worker が実行した

    def test_participate_accepts_multiple_requests(self):
        r1 = self._submit("a; b")
        r2 = self._submit("c; d")
        self.assertEqual(sorted(self._participate()), sorted([r1, r2]))
        for run_id in (r1, r2):
            final = self._run(run_id)
            self.assertIn("synth", final["results"])
            for nid, r in final["results"].items():
                self.assertEqual(r["status"], "done", f"{run_id}/{nid}: {r}")

    def test_participate_skips_runs_the_caller_is_already_driving(self):
        """`--running` の run は再受理しない。渡さないと、起動待ちの run を毎周
        『駆動者が居ない』と読んで再開回数を焼き切り failed に確定してしまう。"""
        run_id = self._submit("a; b")
        self.assertEqual(self._participate(running=[run_id]), [])


class ParticipateConcurrencyControlTests(unittest.TestCase):
    """同時実行数を agent-control（管理面の宣言）から律速する（dashboard の全体設定）。

    優先順位は agent-control 契約どおり control > CLI 引数 > 設定ファイル > 既定。
    宣言が無い巡回は従来の解決のまま（`--max-runs` / `max_runs:` が効き続ける）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-pconc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.control = tempfile.mkdtemp(prefix="kf-pconc-control-")
        self.addCleanup(shutil.rmtree, self.control, ignore_errors=True)
        prev = os.environ["AGENT_CONTROL_DIR"]
        os.environ["AGENT_CONTROL_DIR"] = self.control
        self.addCleanup(os.environ.__setitem__, "AGENT_CONTROL_DIR", prev)
        kf._CONTROL_CACHE["mtime"] = None
        self.addCleanup(kf._CONTROL_CACHE.__setitem__, "mtime", None)

    def _declare(self, concurrency):
        with open(os.path.join(self.control, "control.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "revision": 1,
                       "workloads": {"flow": {"concurrency": concurrency}}}, f)
        kf._CONTROL_CACHE["mtime"] = None

    def _args(self, **kw):
        base = dict(bus=self.tmp, git=None, run_id=None, lease=60.0, poll=1.0,
                    node_id="pc-a", running="", max_runs=0, max_resumes=3,
                    executor="stub", board=None, state_git=None, auto_heal=False,
                    json=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _participate(self, **kw):
        with mock.patch.object(kf, "maybe_self_update"):
            return kf._participate_once(self._args(**kw))

    def _submit(self, *run_ids):
        bus = kf.Bus(self.tmp, "test-submitter")
        for run_id in run_ids:
            bus.submit_request(run_id, "a; b", "test")

    def test_declared_max_runs_limits_acceptance(self):
        self._submit("run-c-1", "run-c-2", "run-c-3")
        self._declare({"max_runs": 1})
        # CLI 引数は無制限（0）でも宣言が勝つ。超過分は inbox に残り、枠が空いた巡回で受理。
        self.assertEqual(len(self._participate()), 1)

    def test_without_declaration_caller_value_is_used(self):
        self._submit("run-c-1", "run-c-2")
        self.assertEqual(len(self._participate(max_runs=0)), 2)   # 従来どおり無制限


class ParticipateSelfUpdateTests(unittest.TestCase):
    """自己更新の確認は「受理も引き継ぎも無い巡回」でだけ走る（常駐廃止で失われた配線の復帰）。

    仕事のある巡回で ls-remote を挟むと受理が遅れるうえ、呼び出し側（常駐体の flow tick）は
    120 秒で kill するため、更新の取り込みが巡回の中に居座ってはいけない。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-pupd-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _args(self, **kw):
        base = dict(bus=self.tmp, git=None, run_id=None, lease=60.0, poll=1.0,
                    node_id="pc-a", running="", max_runs=0, max_resumes=3,
                    executor="stub", board=None, state_git=None, auto_heal=False,
                    json=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _participate(self):
        calls = []
        with mock.patch.object(kf, "maybe_self_update",
                               side_effect=lambda *a, **k: calls.append(k.get("idle"))):
            dispatched = kf._participate_once(self._args())
        return dispatched, calls

    def test_checks_update_when_idle(self):
        dispatched, calls = self._participate()
        self.assertEqual(dispatched, [])
        self.assertEqual(calls, [True])

    def test_skips_update_check_when_work_was_accepted(self):
        kf.Bus(self.tmp, "_").submit_request("run-upd-1", "a; b", "test")
        dispatched, calls = self._participate()
        self.assertEqual(dispatched, ["run-upd-1"])
        self.assertEqual(calls, [])

    def test_update_failure_does_not_break_the_tick(self):
        with mock.patch.object(kf, "maybe_self_update", side_effect=RuntimeError("boom")):
            self.assertEqual(kf._participate_once(self._args()), [])


class AutoHealTests(unittest.TestCase):
    """レイヤ4（auto-heal）: transient 起因で failed 終端した run を cooldown 後に自動再開する。
    done は温存・進捗リセット付き max_heals・cancelled/superseded は尊重。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-heal-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fail_transient(self, rid="run1"):
        self.bus.mark_run_failed(rid, "[agent-error:transient] 一時的エラーの失敗（t1）")

    def _args(self, **kw):
        base = dict(auto_heal=True, max_heals=2, heal_backoff=0.0, heal_quota=False,
                    quota_cooldown=0.0, lease=1800.0, max_resumes=3)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _heal(self, owned=None, **kw):
        spawned = []

        def fake_spawn(base, args, req_id, req):
            spawned.append(req_id)
            return types.SimpleNamespace(poll=lambda: None)

        healed = kf._heal_failed_runs(self.bus, "d1", owned or set(), 120.0,
                                      self._args(**kw), [], spawn=fake_spawn)
        return healed, spawned

    def test_heal_class_detection(self):
        self._fail_transient()
        self.assertEqual(self.bus.heal_class("run1"), "transient")
        self.bus.mark_run_failed("run2", "普通の失敗")   # run 不在 → mark は False だが安全確認
        self.assertIsNone(self.bus.heal_class("run2"))

    def test_content_and_env_failures_not_heal_candidates(self):
        for reason in ("検証 NG", "[agent-error:auth] 認証切れ", "[agent-error:env] CLI 不在"):
            b = kf.Bus(self.tmp, f"r-{hash(reason) % 1000}")
            b.ensure_run("req")
            b.mark_run_failed(os.path.basename(b.run_dir), reason)
            self.assertIsNone(self.bus.heal_class(os.path.basename(b.run_dir)), reason)

    def test_transient_failed_run_healed(self):
        self.bus.submit_request("run1", "req", "s")
        self.bus.write_graph({"strategy": {}, "iteration": 0,
                              "nodes": {"t1": {"goal": "g", "deps": []},
                                        "t2": {"goal": "g", "deps": []}}})
        self.bus.write_task({"id": "t1", "goal": "g", "deps": []})
        self.bus.write_result("t1", "w", "done", "ok")
        self.bus.write_task({"id": "t2", "goal": "g", "deps": []})
        self.bus.write_result("t2", "w", "failed", "[agent-error:transient] ETIMEDOUT")
        self._fail_transient()
        healed, spawned = self._heal()
        self.assertEqual(list(healed), ["run1"])
        self.assertEqual(spawned, ["run1"])
        meta = self.bus.run_meta("run1")
        self.assertEqual(meta["status"], "running")
        self.assertEqual(meta["heal_count"], 1)
        # failed ノードは pending へ戻り、done は温存される
        self.assertIsNone(self.bus.read_result("t2"))
        self.assertEqual((self.bus.read_result("t1") or {}).get("status"), "done")

    def test_heal_respects_cooldown(self):
        self.bus.submit_request("run1", "req", "s")
        self._fail_transient()
        healed, _ = self._heal(heal_backoff=3600.0)      # 初見は武装のみ（cooldown 中）
        self.assertEqual(healed, {})
        self.assertGreater(self.bus.run_meta("run1").get("heal_next_at", 0), time.time())
        healed, _ = self._heal(heal_backoff=3600.0)      # まだ cooldown 中
        self.assertEqual(healed, {})

    def test_heal_exhausted_after_max_heals_without_progress(self):
        self.bus.submit_request("run1", "req", "s")
        for i in range(2):
            self._fail_transient()
            healed, _ = self._heal(max_heals=2)
            self.assertEqual(list(healed), ["run1"], f"heal #{i+1}")
        self._fail_transient()
        healed, _ = self._heal(max_heals=2)              # 3 回目（進捗なし）→ 打ち切り
        self.assertEqual(healed, {})
        meta = self.bus.run_meta("run1")
        self.assertTrue(meta.get("heal_exhausted"))
        self.assertEqual(meta["status"], "failed")       # failed のまま人/消費者へ
        self.assertIsNone(self.bus.heal_class("run1"))   # 以後は候補から即座に外れる

    def test_heal_count_resets_on_progress(self):
        self.bus.submit_request("run1", "req", "s")
        for i in range(4):                               # max_heals=2 を超える回数
            self._fail_transient()
            healed, _ = self._heal(max_heals=2)
            self.assertEqual(list(healed), ["run1"], f"heal #{i+1}")
            self.assertEqual(self.bus.run_meta("run1")["heal_count"], 1)  # 進捗ありでリセット
            self.bus.write_result(f"t{i}", "w", "done", "ok")             # heal 後に前進した

    def test_quota_healed_only_when_opted_in(self):
        self.bus.submit_request("run1", "req", "s")
        self.bus.mark_run_failed("run1", "[agent-error:quota] 利用上限")
        healed, _ = self._heal()                         # 既定 heal_quota=False
        self.assertEqual(healed, {})
        healed, _ = self._heal(heal_quota=True)
        self.assertEqual(list(healed), ["run1"])

    def test_canceled_and_superseded_not_healed(self):
        self.bus.submit_request("run1", "req", "s")
        self._fail_transient()
        # superseded: 新世代（inherit_from 付き）が inbox に居る → 新世代に委ねる
        self.bus.submit_request("run1-r1", "req", "s", inherit_from="run1")
        healed, _ = self._heal()
        self.assertEqual(healed, {})
        # cancelled は heal_class の段階で対象外
        b2 = kf.Bus(self.tmp, "run2")
        b2.ensure_run("req")
        b2.mark_canceled("run2", "人の停止")
        self.assertIsNone(self.bus.heal_class("run2"))

    def test_auto_heal_disabled(self):
        self.bus.submit_request("run1", "req", "s")
        self._fail_transient()
        healed, _ = self._heal(auto_heal=False)
        self.assertEqual(healed, {})

    def test_human_retry_clears_heal_bookkeeping(self):
        self.bus.submit_request("run1", "req", "s")
        self._fail_transient()
        self._heal()
        self.assertEqual(self.bus.run_meta("run1").get("heal_count"), 1)
        self._fail_transient()
        self.bus.retry_failed()                          # 人の明示 retry は簿記を白紙に戻す
        meta = self.bus.run_meta("run1")
        self.assertNotIn("heal_count", meta)
        self.assertNotIn("heal_exhausted", meta)
