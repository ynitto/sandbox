"""agent-flow の単体テスト — run（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class RunFailureTests(unittest.TestCase):
    """orchestrator が done を書く前に異常終了したケースの終端化（失敗終了の検知）。
    これが無いと run が非終端のまま放置され、result/status を待つ消費者
    （agent-project の charter 駆動 watch）が execute フェーズで永久待機する。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-test-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("test request")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mark_run_failed_terminalizes_running(self):
        self.bus.set_status("running")
        self.assertTrue(self.bus.mark_run_failed("run1", "orchestrator crash"))
        meta = self.bus.run_meta("run1")
        self.assertEqual(meta["status"], "failed")
        self.assertEqual(meta["failure_reason"], "orchestrator crash")
        # 終端 = result --json の done=True/status=failed として消費者から即検知できる
        self.assertIn(meta["status"], kf.TERMINAL)

    def test_mark_run_failed_noop_when_already_done(self):
        self.bus.set_status("done")
        self.assertFalse(self.bus.mark_run_failed("run1", "late crash"))
        meta = self.bus.run_meta("run1")
        self.assertEqual(meta["status"], "done")            # 正常完了を上書きしない
        self.assertNotIn("failure_reason", meta)

    def test_mark_run_failed_noop_when_already_failed(self):
        self.bus.set_status("failed")
        self.assertFalse(self.bus.mark_run_failed("run1"))  # 冪等: 既に終端

    def test_mark_run_failed_missing_run(self):
        self.assertFalse(self.bus.mark_run_failed("no-such-run"))

    def test_fail_request_without_run_creates_failed_meta(self):
        # orchestrator が run の meta を一度も書けずに死んだ要求は、fail_request が failed run を
        # 新規作成して終端化する（run_exists が真になり、daemon が同じ要求を毎 poll
        # 再 claim → 起動 → 即死 を繰り返す無限ループが止まる）
        self.bus.submit_request("req9", "do it", "submitter",
                                workspace={"url": "https://x/repo.git"})
        self.assertFalse(self.bus.run_exists("req9"))
        self.assertTrue(self.bus.fail_request("req9", "orchestrator died before run creation"))
        self.assertTrue(self.bus.run_exists("req9"))
        meta = self.bus.run_meta("req9")
        self.assertEqual(meta["status"], "failed")
        self.assertIn(meta["status"], kf.TERMINAL)
        self.assertEqual(meta["request"], "do it")                       # 要求内容を引き写す
        self.assertEqual(meta["workspace"], {"url": "https://x/repo.git"})
        self.assertIn("died before run creation", meta["failure_reason"])

    def test_fail_request_delegates_to_mark_run_failed_when_run_exists(self):
        self.bus.set_status("running")
        self.assertTrue(self.bus.fail_request("run1", "orchestrator crash"))
        self.assertEqual(self.bus.run_meta("run1")["status"], "failed")

    def test_fail_request_noop_when_already_terminal(self):
        self.bus.set_status("done")
        self.assertFalse(self.bus.fail_request("run1", "late crash"))
        self.assertEqual(self.bus.run_meta("run1")["status"], "done")   # 正常完了を上書きしない


class RetryFailedRunTests(unittest.TestCase):
    """failed run の明示 retry（`run --run-id <failed>`）: 失敗ノードを pending へ戻して再実行でき
    るようにし、確定済み done は温存する。これが無いと failed run は再開しても全ノードが終端のまま
    静止し、何も再実行されない（＝failed run を再実行できない）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-retry-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("do it")

    def _task(self, tid, deps=None):
        self.bus.write_task({"id": tid, "goal": "g", "deps": deps or []})
        graph = self.bus.read_graph() or {"strategy": {}, "nodes": {}, "iteration": 0}
        graph["nodes"][tid] = {"goal": "g", "deps": deps or []}
        self.bus.write_graph(graph)

    def test_resets_failed_nodes_keeps_done(self):
        self._task("t1")
        self._task("t2")
        self.bus.write_result("t1", "w", "done", "ok")
        self.bus.write_result("t2", "w", "failed", "boom")
        self.bus.try_claim("t2", "w-old", 9999)          # 失効前の claim が残っていても掃除する
        self.bus.mark_run_failed("run1", "t2 failed")
        self.assertTrue(self.bus.all_terminal())          # retry 前は全ノード終端で静止

        reset = self.bus.retry_failed()
        self.assertEqual(reset, ["t2"])
        self.assertEqual(self.bus.node_state("t1"), "done")      # done は温存（続きから）
        self.assertEqual(self.bus.node_state("t2"), "pending")   # failed → pending（再実行対象）
        self.assertFalse(self.bus.all_terminal())                # もう静止しない＝再実行される
        meta = self.bus.run_meta("run1")
        self.assertEqual(meta["status"], "running")
        self.assertNotIn("failure_reason", meta)

    def test_reruns_incomplete_when_no_failed_results(self):
        # orchestrator クラッシュ等で failed だが結果未書き込みの（pending の）ノードも再開対象にする
        self._task("t1")
        self.bus.mark_run_failed("run1", "orchestrator crash")
        reset = self.bus.retry_failed()
        self.assertEqual(reset, [])                       # 失敗結果は無い
        self.assertEqual(self.bus.node_state("t1"), "pending")
        self.assertEqual(self.bus.run_meta("run1")["status"], "running")

    def test_clears_terminal_and_orphan_bookkeeping(self):
        self._task("t1")
        self.bus.write_result("t1", "w", "failed", "x")
        self.bus.record_resume("run1")                    # resume_count/resume_progress を積む
        self.bus.mark_run_superseded("run1", "run2")      # superseded 簿記＋failed 終端
        self.bus.retry_failed()
        meta = self.bus.run_meta("run1")
        for k in ("failure_reason", "superseded", "superseded_by",
                  "resume_count", "resume_progress"):
            self.assertNotIn(k, meta)
        self.assertEqual(meta["status"], "running")


class RunSlotTests(unittest.TestCase):
    """max_runs（同時実行 run の上限）: バックログ一括投入・再起動直後の孤児一斉再開で
    orchestrator（＋計画エージェント）が run 数ぶん同時に立ち上がるのを防ぐ。
    全ノードが park（承認待ち等）の run は worker も計画エージェントも使わないため
    枠に数えない（gitlab 長期委譲が上限を占有して新規 run を詰まらせない）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-test-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("test request")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _graph(self, view, nodes):
        """{id: deps} からグラフとタスクを作る。"""
        view.write_graph({"strategy": {}, "iteration": 0,
                          "nodes": {nid: {"goal": nid, "deps": deps, "kind": "work"}
                                    for nid, deps in nodes.items()}})
        for nid, deps in nodes.items():
            view.write_task({"id": nid, "goal": nid, "deps": deps})

    def _park(self, view, nid, live=True):
        until = time.time() + (300 if live else -1)
        view.write_wait(nid, {"id": nid, "who": "w", "wait_lease_until": until})

    def test_fully_parked_detection(self):
        v = self.bus.run_view("run1")
        self._graph(v, {"a": [], "b": ["a"]})
        # a が claim 可能な pending → 実行中扱い（枠を使う）
        self.assertFalse(kf._run_fully_parked(self.bus, "run1"))
        # a を park（生存 wait）→ b は依存未達 pending → 全 in-flight が park ＝枠を使わない
        self._park(v, "a")
        self.assertTrue(kf._run_fully_parked(self.bus, "run1"))
        # a が claim される（実行中）→ 枠を使う（node_state は claimed が waiting より優先）
        self.assertTrue(v.try_claim("a", "w1", 300))
        self.assertFalse(kf._run_fully_parked(self.bus, "run1"))

    def test_park_lease_expiry_still_frees_slot(self):
        # wait_lease 失効でも wait ファイルが残れば枠を使わない（一晩の再起動で枠を食い潰さない）
        v = self.bus.run_view("run1")
        self._graph(v, {"a": []})
        self._park(v, "a", live=False)
        self.assertTrue(kf._run_fully_parked(self.bus, "run1"))

    def test_graphless_run_counts_as_busy(self):
        # グラフ未作成（計画中）の run は実行中扱い（計画エージェントが走っている）
        self.assertFalse(kf._run_fully_parked(self.bus, "run1"))
        self.assertEqual(kf._busy_run_count(self.bus, {"run1"}), 1)

    def test_busy_run_count_excludes_parked(self):
        v1 = self.bus.run_view("run1")
        self._graph(v1, {"a": []})
        self._park(v1, "a")
        v2 = self.bus.run_view("run2")
        v2.ensure_run("another")
        self._graph(v2, {"x": []})
        self.assertEqual(kf._busy_run_count(self.bus, {"run1", "run2"}), 1)  # run1 は全 park

    def _make_orphan(self, run_id, parked=False):
        self.bus.submit_request(run_id, "req", "submitter")
        v = self.bus.run_view(run_id)
        v.ensure_run("req")
        v.set_status("running")
        if parked:
            self._graph(v, {"a": []})
            self._park(v, "a")
        meta = kf.read_json(v.meta_path)
        meta["orch_lease_until"] = time.time() - 1.0
        kf.write_json_atomic(v.meta_path, meta)

    def _adopt(self, slots):
        spawned = []

        def fake_spawn(base, args, req_id, req):
            spawned.append(req_id)
            return types.SimpleNamespace(poll=lambda: None)

        adopted, failed = kf._adopt_orphan_runs(
            self.bus, "d2", set(), 120.0,
            types.SimpleNamespace(max_resumes=3, lease=1800.0), [],
            spawn=fake_spawn, slots=slots)
        return adopted, failed, spawned

    def test_adopt_defers_orphans_beyond_slots_without_failing(self):
        # 枠を超える孤児は failed にせず次 poll へ持ち越す（一斉再開でプロセスが溢れない）
        self._make_orphan("run1")
        self._make_orphan("run2")
        adopted, failed, spawned = self._adopt(slots=1)
        self.assertEqual(len(adopted), 1)
        self.assertEqual(failed, [])                      # 持ち越し＝failed にしない
        deferred = ({"run1", "run2"} - set(adopted)).pop()
        self.assertEqual(self.bus.run_meta(deferred)["status"], "running")
        # 枠が空いた次の poll で残りが再開される
        adopted2, failed2, _ = self._adopt(slots=1)
        self.assertEqual(list(adopted2), [deferred])
        self.assertEqual(failed2, [])

    def test_adopt_parked_orphan_exempt_from_slots(self):
        # 全 park の孤児 run は枠を消費しない＝slots=0 でも引き継ぐ
        # （park の監視（service_waits）は駆動オーナーが必要。承認待ちを取りこぼさない）
        self._make_orphan("run1", parked=True)
        adopted, failed, spawned = self._adopt(slots=0)
        self.assertEqual(list(adopted), ["run1"])
        self.assertEqual(failed, [])

    def test_adopt_unlimited_when_slots_none(self):
        # slots=None（max_runs<=0）は従来どおり無制限に引き継ぐ
        for rid in ("run1", "run2", "run3"):
            self._make_orphan(rid)
        adopted, failed, _ = self._adopt(slots=None)
        self.assertEqual(len(adopted), 3)
        self.assertEqual(failed, [])

    def test_adopt_backfills_explicit_phase_for_legacy_run(self):
        self._make_orphan("run1")
        view = self.bus.run_view("run1")
        self._graph(view, {"a": []})
        self.assertNotIn("phase", self.bus.run_meta("run1"))

        adopted, failed, _ = self._adopt(slots=1)

        self.assertEqual(list(adopted), ["run1"])
        self.assertEqual(failed, [])
        meta = self.bus.run_meta("run1")
        self.assertEqual(meta["phase"], "executing")
        self.assertTrue(meta["phase_started_at"])
        phases = [e["phase"] for e in view.recent_events(20) if e.get("kind") == "phase"]
        self.assertEqual(phases, ["executing"])


class SpawnArgvTests(unittest.TestCase):
    """daemon がオンデマンド起動する子（orchestrator/worker）の argv が、実際の CLI パーサで
    そのまま parse できることを保証する。グローバル引数とサブコマンド引数の置き場を取り違えると
    子が起動直後に usage エラー（exit 2）で即死し、resume/引き継ぎが静かに壊れる。"""

    def _args(self, **kw):
        base = dict(granularity="finest", exemplar_first=False, planner="stub",
                    executor="agent", max_iterations=1, max_fanout=4, max_retries=3,
                    model=None, poll=1.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _capture(self, spawn, *spawn_args, **spawn_kw):
        captured = {}

        def fake_popen(cmd, *a, **kw):
            captured["cmd"] = cmd
            return object()

        with mock.patch.object(kf.subprocess, "Popen", side_effect=fake_popen):
            spawn(*spawn_args, **spawn_kw)
        return captured["cmd"]

    def _parse_child(self, cmd):
        # base の先頭2つ（sys.executable, self_path）を除いた残りが CLI 引数。実パーサで検証する。
        return kf.build_parser().parse_args(cmd[2:])

    def _base(self):
        return [sys.executable, kf.self_path(), "--bus", "/tmp/bus"]

    def test_spawn_orchestrator_argv_parses(self):
        args = self._args()
        req = {"request": "do the thing", "pattern": "map-reduce"}
        cmd = self._capture(kf._spawn_orchestrator, self._base(), args, "run-42", req)
        parsed = self._parse_child(cmd)
        self.assertEqual(parsed.cmd, "orchestrate")
        self.assertEqual(parsed.run_id, "run-42")
        self.assertEqual(parsed.request, "do the thing")
        self.assertEqual(parsed.pattern, "map-reduce")

    def test_spawn_orchestrator_with_inherit_from_argv_parses(self):
        # 回帰: --inherit-from は orchestrate サブコマンドの引数。以前は "orchestrate" より前に
        # 置かれており、親パーサが拾って usage エラー（同じ run-id で再開の直後）になっていた。
        args = self._args()
        req = {"request": "retry it", "inherit_from": "run-prev"}
        cmd = self._capture(kf._spawn_orchestrator, self._base(), args, "run-43", req)
        parsed = self._parse_child(cmd)
        self.assertEqual(parsed.cmd, "orchestrate")
        self.assertEqual(parsed.inherit_from, "run-prev")
        self.assertEqual(parsed.run_id, "run-43")

    # --- 計画パラメータの置き場（グローバル → サブコマンド） -----------------
    # `--granularity` / `--split-policy` / `--exemplar-first` / `--plan-gate` 系は計画する
    # サブコマンド（run / orchestrate）だけの引数へ移した。以前はグローバルだったため
    # `agent-flow --granularity finest doctor` のように計画しないサブコマンドでも受理され、
    # 指定が黙って捨てられていた。置き場を戻すと子が usage エラーで即死する。

    def test_planning_args_land_after_the_subcommand_name(self):
        args = self._args(granularity="coarse", split_policy="file", exemplar_first=True,
                          plan_gate=True, plan_gate_timeout=120.0)
        cmd = self._capture(kf._spawn_orchestrator, self._base(), args, "run-44",
                            {"request": "do it"})
        after = cmd[cmd.index("orchestrate"):]
        for flag in ("--granularity", "--split-policy", "--exemplar-first", "--plan-gate",
                     "--plan-gate-timeout"):
            self.assertIn(flag, after, flag)
        parsed = self._parse_child(cmd)
        self.assertEqual(parsed.granularity, "coarse")
        self.assertEqual(parsed.split_policy, "file")
        self.assertTrue(parsed.exemplar_first)
        self.assertTrue(parsed.plan_gate)
        self.assertEqual(parsed.plan_gate_timeout, 120.0)

    def test_inbox_pattern_wins_over_the_configured_one(self):
        # 要求が名指しした標準パターンが、設定ファイル由来の pattern より優先される。
        args = self._args(pattern="tournament")
        cmd = self._capture(kf._spawn_orchestrator, self._base(), args, "run-45",
                            {"request": "do it", "pattern": "map-reduce"})
        self.assertEqual(self._parse_child(cmd).pattern, "map-reduce")
        cmd = self._capture(kf._spawn_orchestrator, self._base(), args, "run-46",
                            {"request": "do it"})
        self.assertEqual(self._parse_child(cmd).pattern, "tournament")

    def test_spawn_orchestrator_uses_the_request_planning(self):
        # daemon のオンデマンド起動は daemon 自身の args ではなく要求の内容が正典
        # （1 つの daemon が別々の分け方を名指しした複数の要求を受け持つ）。
        args = self._args(granularity="coarse", split_policy="behavior")
        cmd = self._capture(kf._spawn_orchestrator, self._base(), args, "run-50",
                            {"request": "do it", "granularity": "finest", "split_policy": "file"})
        parsed = self._parse_child(cmd)
        self.assertEqual((parsed.granularity, parsed.split_policy), ("finest", "file"))

    def test_spawn_orchestrator_rejects_a_broken_request(self):
        # 手書きの inbox など語彙外の値は起動前に断る（誤った分け方で走らせない）。
        args = self._args()
        with self.assertRaises(kf.InboxRequestError):
            self._capture(kf._spawn_orchestrator, self._base(), args, "run-51",
                          {"request": "do it", "granularity": "medium"})

    def test_non_planning_subcommands_reject_planning_args(self):
        # 意味のないオプションを黙って受け取らない（usage エラー = rc 2 で断る）。
        parser = kf.build_parser()
        for argv in (["--granularity", "finest", "doctor"],
                     ["--split-policy", "file", "status"],
                     ["--exemplar-first", "work"],
                     ["--plan-gate", "participate"]):
            with self.assertRaises(SystemExit, msg=argv), \
                 contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(argv)

    def test_cmd_run_gives_planning_args_to_the_orchestrator_only(self):
        # worker（`work`）は計画しない。計画パラメータを base（orchestrator と worker の共通
        # 部分）へ積んでいた頃は、意味の無いまま worker にも渡っていた。サブコマンドの引数へ
        # 移した今は worker の argv に混ざると usage エラーで即死するので、両方を実パーサで見る。
        bus = tempfile.mkdtemp(prefix="kf-bus-planning-")
        self.addCleanup(shutil.rmtree, bus, ignore_errors=True)
        spawned = []

        class _FakePopen:
            def __init__(self, cmd, *a, **k):
                spawned.append(cmd)
            def poll(self): return 0
            def wait(self, *a, **k): return 0
            def terminate(self): pass

        args = argparse.Namespace(
            config=None, bus=bus, git=None, git_branch="main", git_subdir=None, lease=30.0,
            run_id="run-planning", workers=1, request="x", planner="stub", executor=None,
            model=None, poll=0.01, max_iterations=1, max_fanout=4, max_retries=1, review=None,
            granularity="coarse", split_policy="file", exemplar_first=True, plan_gate=True,
            plan_gate_timeout=90.0, cleanup_clone=True, repos=None, keep_clone=False)
        kf.resolve_config(args)
        fake_bus = mock.Mock()
        fake_bus.run_exists.return_value = False   # 新規 run（再開だと request が Mock になる）
        fake_bus.run_is_orphaned.return_value = False
        fake_bus.retry_failed.return_value = []
        with mock.patch.object(kf.subprocess, "Popen", _FakePopen), \
             mock.patch.object(kf, "make_bus", lambda *a, **k: fake_bus):
            try:
                kf.cmd_run(args)
            except Exception:
                pass   # bus/poll をモックしているので途中で抜けてよい（argv だけ検証する）
        # bus をモックしているので Mock 値を含む argv が混ざりうる。実パーサに掛けられる
        # （全要素が文字列の）agent-flow の子だけを見る。
        argvs = [c for c in spawned
                 if c and c[0] == sys.executable and all(isinstance(x, str) for x in c)]
        children = [self._parse_child(c) for c in argvs]
        by_cmd = {c.cmd: c for c in children}
        self.assertIn("orchestrate", by_cmd, "orchestrator が起動されていない")
        self.assertIn("work", by_cmd, "worker が起動されていない")
        orch = by_cmd["orchestrate"]
        self.assertEqual((orch.granularity, orch.split_policy), ("coarse", "file"))
        self.assertTrue(orch.exemplar_first)
        self.assertTrue(orch.plan_gate)
        self.assertEqual(orch.plan_gate_timeout, 90.0)
        worker_argv = [c for c in argvs if "work" in c][0]
        for flag in ("--granularity", "--split-policy", "--exemplar-first", "--plan-gate"):
            self.assertNotIn(flag, worker_argv, flag)


class WorkerWhoTests(unittest.TestCase):
    """worker の名義（バス上の `who`）に PC 名が入ること。

    以前は `worker-{i}` 固定で、共有バスに 2 台が参加すると両者が
    `claims/<node>/worker-1.json` と `events/worker-1.jsonl` という同一パスへ書いた
    （設計 付録 A の「クレーマごとにファイル名が分かれる」不変条件の破れ）。同時に
    「どの PC がどの work ノードを実行したか」が status / dashboard / バスのどこにも
    出ない状態でもあった。"""

    def _args(self, node_id=None):
        return types.SimpleNamespace(node_id=node_id)

    def test_includes_node_id(self):
        who = kf.worker_who(self._args("desk-a"), 1)
        self.assertEqual(who, "desk-a-w1")

    def test_heal_generation_is_distinct(self):
        args = self._args("desk-a")
        self.assertEqual(kf.worker_who(args, 2, heal=3), "desk-a-h3w2")
        # 世代が違えば名義も違う（heal で起こし直した worker が旧世代の claim を継がない）
        self.assertNotEqual(kf.worker_who(args, 2, heal=3), kf.worker_who(args, 2))

    def test_two_nodes_do_not_share_a_claim_path(self):
        a = kf.worker_who(self._args("desk-a"), 1)
        b = kf.worker_who(self._args("desk-b"), 1)
        self.assertNotEqual(a, b)
        # claim ファイル名まで割れていることを板・バス共通の綴り規則で確かめる
        self.assertNotEqual(kf.protocol.safe_name(a), kf.protocol.safe_name(b))

    def test_falls_back_to_this_pc_name(self):
        # node_id 未宣言なら PC 名の正規形（agentcore.nodeid）を使う
        who = kf.worker_who(self._args(None), 1)
        self.assertEqual(who, f"{kf.default_node_id()}-w1")

    def test_spelling_follows_safe_name(self):
        # 明示 node_id に板のファイル名へ置けない文字があっても綴りは規則に従う
        who = kf.worker_who(self._args("Desk/A"), 1)
        self.assertEqual(who, kf.protocol.safe_name(who))
        self.assertNotIn("/", who)

    def test_workers_on_one_pc_are_distinct(self):
        args = self._args("desk-a")
        self.assertEqual([kf.worker_who(args, i + 1) for i in range(2)],
                         ["desk-a-w1", "desk-a-w2"])


class ExecutionByPcTests(unittest.TestCase):
    """PC 別の実行内訳。「どの PC がどの work ノードを実行したか」は CLI・GUI・バスの
    どこにも出ていなかった（棚卸し 2026-07-27 §2b.1）。worker が結果へ書いた `node` を
    正典として集計する——`who` の綴りを割って PC を推測すると、名義の作り方の 2 実装目になる。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-bypc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")
        self.nodes = {"t1": {"goal": "g", "deps": []}, "t2": {"goal": "g", "deps": []},
                      "t3": {"goal": "g", "deps": []}}
        self.bus.write_graph({"nodes": self.nodes, "iteration": 0})

    def test_counts_by_executing_pc(self):
        self.bus.write_result("t1", "desk-a-w1", "done", "o", node="desk-a")
        self.bus.write_result("t2", "desk-a-w2", "done", "o", node="desk-a")
        self.bus.write_result("t3", "desk-b-w1", "failed", "o", node="desk-b")
        self.assertEqual(kf.execution_by_pc(self.bus, self.nodes),
                         [("desk-a", 2), ("desk-b", 1)])

    def test_missing_pc_record_is_marked_not_guessed(self):
        # 旧い結果（PC を書く前の agent-flow が確定したもの）は who に `?` を付けて出す
        self.bus.write_result("t1", "worker-1", "done", "o")
        self.assertEqual(kf.execution_by_pc(self.bus, self.nodes), [("worker-1?", 1)])

    def test_unfinished_nodes_are_not_counted(self):
        self.assertEqual(kf.execution_by_pc(self.bus, self.nodes), [])

    def test_status_render_shows_breakdown(self):
        self.bus.write_result("t1", "desk-a-w1", "done", "o", node="desk-a")
        self.bus.write_result("t2", "desk-b-w1", "done", "o", node="desk-b")
        _, text = kf._render_status(self.bus, "run1", 0)
        self.assertIn("by pc", text)
        self.assertIn("desk-a=1", text)
        self.assertIn("desk-b=1", text)

    def test_worker_records_this_pc(self):
        # work は `--node-id` が worker の名義で埋まるので、PC は設定宣言 →
        # ホスト名の順で解決する（args.node_id は見ない）
        args = types.SimpleNamespace(node_id="desk-a-w1", _config={"node_id": "desk-a"})
        self.assertEqual(kf.this_pc(args), "desk-a")
        self.assertEqual(kf.this_pc(types.SimpleNamespace(node_id="desk-a-w1", _config={})),
                         kf.default_node_id())


class StalledRunRetryTests(unittest.TestCase):
    """停滞した run（orchestrator が消えて非終端のまま止まったもの）も、失敗ノードを戻して再開する。

    status だけを見ると救えない: orchestrator が落ちると run は status=running のままリースだけが
    切れて残り、失敗ノードも pending ノードも誰も進めない。再開しても failed の results が終端
    として残るため、その工程は永久に再実行されなかった（25 ノード中 14 done / 1 failed のまま
    「実行中」に見え続け、やり直す手段が無かった）。"""

    def _bus(self, status, lease_delta):
        root = tempfile.mkdtemp(prefix="kf-stall-")
        self.addCleanup(shutil.rmtree, root, True)
        rid = "req-x-T1-r0"
        rd = pathlib.Path(root, "runs", rid)
        (rd / "results").mkdir(parents=True)
        (rd / "tasks").mkdir(parents=True)
        meta = {"status": status, "request": "x", "created_at": kf.now_iso(),
                "orch_lease_until": time.time() + lease_delta}
        (rd / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (rd / "graph.json").write_text(json.dumps(
            {"nodes": {"t1": {"goal": "a", "deps": []}, "t2": {"goal": "b", "deps": []}}}),
            encoding="utf-8")
        (rd / "results" / "t1.json").write_text(json.dumps({"id": "t1", "status": "done"}),
                                                encoding="utf-8")
        (rd / "results" / "t2.json").write_text(json.dumps({"id": "t2", "status": "failed"}),
                                                encoding="utf-8")
        return root, rid, rd

    def test_stalled_run_is_detected_as_orphaned(self):
        root, rid, _ = self._bus("running", -60)          # リース切れ＝orchestrator 消失
        self.assertTrue(kf.Bus(root, rid).run_is_orphaned(rid, 0.0))

    def test_live_run_is_not_orphaned(self):
        root, rid, _ = self._bus("running", +600)         # まだ走っている
        self.assertFalse(kf.Bus(root, rid).run_is_orphaned(rid, 0.0))

    def test_retry_failed_resets_only_the_failed_node(self):
        # 成功した工程は温存し、失敗した工程だけを pending へ戻す
        root, rid, rd = self._bus("running", -60)
        reset = kf.Bus(root, rid).retry_failed()
        self.assertEqual(reset, ["t2"])
        self.assertTrue((rd / "results" / "t1.json").exists(), "done は温存")
        self.assertFalse((rd / "results" / "t2.json").exists(), "failed は戻す")
        meta = json.loads((rd / "meta.json").read_text())
        self.assertEqual(meta["status"], "running")

    def test_lease_less_old_run_is_orphaned(self):
        # リース未記録（heartbeat 前に死んだ／旧版の run）は age で停滞と判定する
        root = tempfile.mkdtemp(prefix="kf-nolease-")
        self.addCleanup(shutil.rmtree, root, True)
        rid = "run-20260712-213419-5922"
        rd = pathlib.Path(root, "runs", rid)
        rd.mkdir(parents=True)
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 2 * 3600))
        (rd / "meta.json").write_text(json.dumps({"status": "running", "updated_at": old}),
                                      encoding="utf-8")
        self.assertTrue(kf.Bus(root, rid).run_is_orphaned(rid, 600.0))


class ArgvLimitTests(unittest.TestCase):
    """大きなプロンプトをコマンドライン長制限で落とさず、一時ファイル参照に切り替える。"""

    def test_argv_limit_from_config(self):
        import argparse
        # 解決済み設定値（argv_limit）はモジュール変数へ確定し、free 関数が参照する
        orig = kf._ARGV_LIMIT
        self.addCleanup(setattr, kf, "_ARGV_LIMIT", orig)
        kf._configure_thresholds(argparse.Namespace(argv_limit=123))
        self.assertEqual(kf._agent_argv_limit(), 123)
        kf._configure_thresholds(argparse.Namespace(argv_limit=None))  # 未指定は据え置き
        self.assertEqual(kf._agent_argv_limit(), 123)
        kf._ARGV_LIMIT = 0  # 0/不正は組み込み既定へフォールバック
        self.assertEqual(kf._agent_argv_limit(), kf.CONFIG_DEFAULTS["argv_limit"])

    def test_inbox_execution_overrides_are_forwarded_to_children(self):
        root = tempfile.mkdtemp(prefix="kf-run-overrides-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        bus = kf.Bus(root, "run-1")
        overrides = {"version": 1, "roles": {"planner": {"agent_cli": "codex"}}}
        kf.write_json_atomic(os.path.join(bus.inbox_dir, "run-1.json"), {
            "id": "run-1", "request": "request", "execution_overrides": overrides,
        })
        args = argparse.Namespace(run_id="run-1", request="", workspace=None, references=None,
                                  inherit_from=None, verification_plan=None, pattern=None,
                                  execution_overrides=None, lease=30, git=None,
                                  cleanup_clone=True, cleanup_per_node=False, agent_cli=None)
        kf._apply_inbox_request(bus, args)
        self.assertEqual(json.loads(args.execution_overrides), overrides)
        self.assertIn("--execution-overrides", kf._child_base(args, root))

    # --- inbox 要求からの分け方（L2） --------------------------------------
    # dashboard が画面から granularity / split_policy を指定できるようにした経路。
    # 以前は inbox のキーではなく、画面からは分け方をまったく触れなかった。

    def _inbox_args(self, **kw):
        base = dict(run_id="run-1", request="", workspace=None, references=None,
                    inherit_from=None, verification_plan=None, pattern=None,
                    execution_overrides=None, lease=30, git=None, cleanup_clone=True,
                    cleanup_per_node=False, agent_cli=None, config=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def _inbox_bus(self, rec):
        root = tempfile.mkdtemp(prefix="kf-run-planning-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        bus = kf.Bus(root, "run-1")
        kf.write_json_atomic(os.path.join(bus.inbox_dir, "run-1.json"),
                             {"id": "run-1", "request": "request", **rec})
        return bus

    def test_inbox_planning_beats_the_config_file(self):
        # 要求は run 単位の意思なので、そのノードの agent-flow.yaml より強い。
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-planning-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        with open(os.path.join(cfg_dir, "agent-flow.json"), "w") as f:
            json.dump({"granularity": "coarse", "split_policy": "behavior"}, f)
        bus = self._inbox_bus({"granularity": "finest", "split_policy": "file"})
        args = self._inbox_args(config=os.path.join(cfg_dir, "agent-flow.json"),
                                granularity=None, split_policy=None)
        kf.resolve_config(args)
        self.assertEqual((args.granularity, args.split_policy), ("coarse", "behavior"))
        kf._apply_inbox_request(bus, args)
        self.assertEqual((args.granularity, args.split_policy), ("finest", "file"))

    def test_cli_beats_the_inbox_request(self):
        # 人がその場で打った値は要求に覆されない（resolve_config が控える _cli_explicit）。
        bus = self._inbox_bus({"granularity": "finest", "split_policy": "file"})
        args = self._inbox_args(granularity="coarse", split_policy="behavior")
        kf.resolve_config(args)
        kf._apply_inbox_request(bus, args)
        self.assertEqual((args.granularity, args.split_policy), ("coarse", "behavior"))

    def test_absent_planning_keys_leave_the_resolved_values(self):
        # 未指定の要求（従来の形）は既定挙動を 1 バイトも変えない。
        bus = self._inbox_bus({})
        args = self._inbox_args(granularity=None, split_policy=None)
        kf.resolve_config(args)
        kf._apply_inbox_request(bus, args)
        self.assertEqual((args.granularity, args.split_policy), ("auto", "behavior"))

    def test_unknown_planning_value_is_rejected(self):
        # split_policy() は未知値を既定へ丸めるので、素通しすると「指定したのに効かない run」に
        # なる。誤記は明示的に失敗させる。
        bus = self._inbox_bus({"split_policy": "module"})
        args = self._inbox_args(granularity=None, split_policy=None)
        kf.resolve_config(args)
        with self.assertRaises(kf.InboxRequestError) as ctx:
            kf._apply_inbox_request(bus, args)
        self.assertIn("split_policy", str(ctx.exception))

    def test_argv_limit_resolved_from_config_file(self):
        # 設定ファイルの argv_limit が resolve_config 経由で args に載る（env 非依存）
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "agent-flow.json")
        with open(cfg, "w") as f:
            json.dump({"argv_limit": 4096}, f)
        args = argparse.Namespace(config=cfg, argv_limit=None)
        kf.resolve_config(args)
        self.assertEqual(args.argv_limit, 4096)

    def test_agent_timeout_resolved_from_config_file(self):
        # 設定ファイルの新キー agent_timeout が resolve_config 経由で args に載る
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "agent-flow.json")
        with open(cfg, "w") as f:
            json.dump({"agent_timeout": 45}, f)
        args = argparse.Namespace(config=cfg, agent_timeout=None)
        kf.resolve_config(args)
        self.assertEqual(args.agent_timeout, 45)

    def test_legacy_kiro_timeout_config_key_aliased(self):
        # 後方互換: 旧キー kiro_timeout が agent_timeout として受理される
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "agent-flow.json")
        with open(cfg, "w") as f:
            json.dump({"kiro_timeout": 77}, f)
        args = argparse.Namespace(config=cfg, agent_timeout=None)
        kf.resolve_config(args)
        self.assertEqual(args.agent_timeout, 77)

    def test_new_agent_timeout_key_beats_legacy(self):
        # 新旧併記時は新キーが優先される
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "agent-flow.json")
        with open(cfg, "w") as f:
            json.dump({"agent_timeout": 10, "kiro_timeout": 999}, f)
        args = argparse.Namespace(config=cfg, agent_timeout=None)
        kf.resolve_config(args)
        self.assertEqual(args.agent_timeout, 10)

    def test_gitlab_block_resolved_from_config_file(self):
        # 設定ファイルの gitlab: ブロック（repo_url 含む）が args.gitlab に載り、_config_path も確定する。
        # これで --config を渡された worker が repo_url を gl.py へ伝えられる（GL_PROJECT_URL）。
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "agent-flow.json")
        with open(cfg, "w") as f:
            json.dump({"gitlab": {"repo_url": "https://gitlab.com/grp/repo"}}, f)
        args = argparse.Namespace(config=cfg, gitlab=None)
        kf.resolve_config(args)
        self.assertEqual(args.gitlab.get("repo_url"), "https://gitlab.com/grp/repo")
        self.assertEqual(args._config_path, cfg)
        # make_executor がこの gitlab ブロックを AGENT_FLOW_EXECUTOR_CONFIG へ載せる
        prev = os.environ.get("AGENT_FLOW_EXECUTOR_CONFIG")
        self.addCleanup(lambda: os.environ.__setitem__("AGENT_FLOW_EXECUTOR_CONFIG", prev)
                        if prev is not None else os.environ.pop("AGENT_FLOW_EXECUTOR_CONFIG", None))
        kf.make_executor(argparse.Namespace(executor="gitlab", gitlab=args.gitlab))
        self.assertEqual(json.loads(os.environ["AGENT_FLOW_EXECUTOR_CONFIG"]).get("repo_url"),
                         "https://gitlab.com/grp/repo")

    def test_child_spawn_propagates_config(self):
        # run/daemon が子（orchestrator/worker）へ --config を引き継ぐ。これが無いと worker は設定を
        # 再解決できず gitlab.repo_url が既定（空）になり、起票先が git origin にフォールバックする。
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "agent-flow.json")
        with open(cfg, "w") as f:
            json.dump({"executor": "gitlab", "gitlab": {"repo_url": "https://gitlab.com/grp/repo"}}, f)
        bus = tempfile.mkdtemp(prefix="kf-bus-")
        self.addCleanup(shutil.rmtree, bus, ignore_errors=True)
        spawned = []

        class _FakePopen:
            def __init__(self, cmd, *a, **k):
                spawned.append(cmd)
            def poll(self): return 0
            def wait(self, *a, **k): return 0
            def terminate(self): pass

        base_args = dict(config=cfg, bus=bus, git=None, git_branch="main", git_subdir=None,
                         lease=30.0, run_id="run-x", workers=1, request="x", planner="stub",
                         executor=None, model=None, poll=0.01, max_iterations=1, max_fanout=4,
                         max_retries=1, review=None, granularity="finest", exemplar_first=False,
                         cleanup_clone=True, repos=None, keep_clone=False)
        args = argparse.Namespace(**base_args)
        kf.resolve_config(args)   # executor=gitlab / gitlab block / _config_path を確定
        # cmd_run は「停滞 run なら失敗ノードを戻して再開する」判定をする。素の Mock だと
        # run_is_orphaned が truthy を返して停滞扱いになるので、通常の再開として振る舞わせる。
        fake_bus = mock.Mock()
        fake_bus.run_is_orphaned.return_value = False
        fake_bus.retry_failed.return_value = []
        with mock.patch.object(kf.subprocess, "Popen", _FakePopen), \
             mock.patch.object(kf, "make_bus", lambda *a, **k: fake_bus):
            try:
                kf.cmd_run(args)
            except Exception:
                pass  # bus/poll をモックしているので途中で抜けてよい（spawn コマンドだけ検証）
        # subprocess.run 経由の補助プロセス（executor プラグイン解決の git rev-parse 等）も
        # パッチした Popen に載るため、agent-flow の子（python 実行）だけに絞って検証する。
        children = [c for c in spawned if c and c[0] == sys.executable]
        self.assertTrue(children, "子プロセスが起動されていない")
        for cmd in children:
            self.assertIn("--config", cmd)
            self.assertEqual(cmd[cmd.index("--config") + 1], os.path.abspath(cfg))

    def test_small_prompt_passed_inline(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_agent("短いプロンプト", None)
        self.assertIn("短いプロンプト", seen["cmd"])  # そのまま argv に乗る

    def test_large_prompt_spilled_to_tempfile(self):
        big = "依存成果物" + "X" * 200000  # argv 長制限を超える巨大プロンプト
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            # 退避ファイルへのパスが argv 末尾に入り、実行中はその中身が読めること
            path = cmd[-1].split(": ")[-1]
            seen["spill_path"] = path
            with open(path, encoding="utf-8") as f:
                seen["spill_body"] = f.read()
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_agent(big, None)
        # 巨大プロンプト本体は argv に乗らない（コマンドライン長制限を回避）
        self.assertNotIn(big, seen["cmd"])
        self.assertLess(len(seen["cmd"][-1]), 500)
        self.assertEqual(seen["spill_body"], big)          # ファイルには全文がある
        self.assertFalse(os.path.exists(seen["spill_path"]))  # 実行後に掃除される


class CircuitBreakerTests(unittest.TestCase):
    """judge/評価役のサーキットブレーカー: 達成不可能な完了条件で無限に再タスクを積まない。"""

    def test_node_entry_preserves_retries(self):
        e = kf._node_entry({"id": "x", "goal": "g", "deps": [], "kind": "verify", "retries": 2})
        self.assertEqual(e["retries"], 2)
        e0 = kf._node_entry({"id": "y", "goal": "g", "deps": [], "kind": "work"})
        self.assertNotIn("retries", e0)  # 0/未指定は持たない（ノイズを足さない）

    def test_verify_fail_increments_retries(self):
        nodes = {"gen1": {"goal": "FLAKY", "deps": [], "kind": "generate"},
                 "v1": {"goal": "検証", "deps": ["gen1"], "kind": "verify"}}
        results = {"gen1": {"status": "done", "output": "issue"},
                   "v1": {"status": "done", "output": "verify=fail"}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, max_retries=3)
        by = {t["id"]: t for t in new}
        self.assertEqual(by["v1-r1"]["retries"], 1)
        self.assertEqual(by["gen1-r1"]["retries"], 1)

    def test_circuit_breaker_stops_verify_retries_at_cap(self):
        # retries が上限に達した verify-fail は作り直しを生成せず done で打ち切る
        nodes = {"gen1": {"goal": "g", "deps": [], "kind": "generate", "retries": 3},
                 "v1": {"goal": "検証", "deps": ["gen1"], "kind": "verify", "retries": 3}}
        results = {"gen1": {"status": "done", "output": "issue"},
                   "v1": {"status": "done", "output": "verify=fail"}}
        decision, new, reason = kf.continue_stub("req", nodes, results, 5, max_retries=3)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])
        self.assertIn("サーキットブレーカー", reason)

    def test_circuit_breaker_stops_failed_task_retries_at_cap(self):
        nodes = {"t2": {"goal": "FAIL", "deps": [], "kind": "work", "retries": 3}}
        results = {"t2": {"status": "failed"}}
        decision, new, reason = kf.continue_stub("req", nodes, results, 5, max_retries=3)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])
        self.assertIn("サーキットブレーカー", reason)

    def test_failed_retry_below_cap_still_retries(self):
        nodes = {"t2": {"goal": "FAIL", "deps": [], "kind": "work", "retries": 1}}
        results = {"t2": {"status": "failed"}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0, max_retries=3)
        self.assertEqual(decision, "replan")
        self.assertEqual(new[0]["id"], "t2r")
        self.assertEqual(new[0]["retries"], 2)

    def test_retry_depth_from_id_chain(self):
        self.assertEqual(kf._retry_depth("gen1", {}), 0)
        self.assertEqual(kf._retry_depth("gen1-r1", {}), 1)
        self.assertEqual(kf._retry_depth("gen1-r1-r2", {}), 2)
        self.assertEqual(kf._retry_depth("x", {"retries": 4}), 4)  # 明示カウンタ優先

    def test_continue_agent_evaluator_error_fails_closed(self):
        # 評価役 LLM の失敗（例外）を done に倒さない: 未達ノードが残るなら failed で終端し、
        # resume/リトライに回す（失敗 run を「成功」として消費者へ渡さない）。
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "failed", "output": "boom"}}
        with mock.patch.object(kf, "run_agent", side_effect=RuntimeError("llm down")):
            decision, new, reason = kf.continue_agent("req", nodes, results, 0)
        self.assertEqual(decision, "failed")
        self.assertEqual(new, [])
        self.assertIn("t1", reason)

    def test_continue_agent_evaluator_error_all_done_is_done(self):
        # 全ノード done なら評価役が落ちても自明に done（偽 failed で手戻りさせない）
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(kf, "run_agent", side_effect=RuntimeError("llm down")):
            decision, new, _ = kf.continue_agent("req", nodes, results, 0)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])

    def test_normalize_verify_ambiguous_fails_closed(self):
        # verify=pass / verify=fail のどちらも無い曖昧出力は fail（偽成功でゲートを通さない）
        self.assertFalse(kf._normalize_verify("LGTM 問題ありません", None)["ok"])
        self.assertFalse(kf._normalize_verify("", None)["ok"])
        # 両方書かれた矛盾出力も fail
        self.assertFalse(kf._normalize_verify("verify=pass … いや verify=fail", None)["ok"])

    def test_continue_agent_circuit_breaker_short_circuits(self):
        # 評価役 LLM を呼ぶ前に、上限到達の系統を検知して done で打ち切る（LLM 不要）
        nodes = {"v1-r1-r2-r3": {"goal": "検証", "deps": [], "kind": "verify"}}
        results = {"v1-r1-r2-r3": {"status": "done", "output": "verify=fail"}}
        with mock.patch.object(kf, "run_agent",
                               side_effect=AssertionError("LLM を呼んではいけない")):
            decision, new, reason = kf.continue_agent("req", nodes, results, 9, max_retries=3)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])
        self.assertIn("サーキットブレーカー", reason)


class TransientRetryTests(unittest.TestCase):
    """レイヤ1（in-place リトライ）: transient 分類の失敗は run_agent 内で再試行され、
    上位（グラフ再計画の retries 予算）へ持ち上げない。非 transient は従来どおり即座に上位へ。"""

    def _patch(self, side_effect, retries=2):
        return (mock.patch.object(kf, "_run_agent_once", side_effect=side_effect),
                mock.patch.object(kf, "_TRANSIENT_RETRIES", retries),
                mock.patch.object(kf, "_TRANSIENT_BACKOFF", 0.0),
                mock.patch.object(kf.random, "uniform", return_value=0.0))

    def test_transient_error_retried_in_place(self):
        calls = []
        def flaky(prompt, model, purpose="", cwd=None, **_kw):
            calls.append(purpose)
            if len(calls) < 3:
                raise RuntimeError("connection reset by peer")
            return "ok"
        p1, p2, p3, p4 = self._patch(flaky)
        with p1, p2, p3, p4:
            self.assertEqual(kf.run_agent("p", None, purpose="work"), "ok")
        self.assertEqual(len(calls), 3)                  # 2 回失敗 → 3 回目で成功

    def test_non_transient_not_retried(self):
        calls = []
        def denied(prompt, model, purpose="", cwd=None, **_kw):
            calls.append(1)
            raise RuntimeError("AccessDenied: please login")
        p1, p2, p3, p4 = self._patch(denied)
        with p1, p2, p3, p4:
            with self.assertRaises(RuntimeError) as ctx:
                kf.run_agent("p", None)
        self.assertEqual(len(calls), 1)                  # auth は再試行しない（人が直す）
        self.assertEqual(getattr(ctx.exception, "attempts", None), 1)

    def test_quota_not_retried_in_place(self):
        calls = []
        def quota(prompt, model, purpose="", cwd=None, **_kw):
            calls.append(1)
            raise RuntimeError("usage limit reached")
        p1, p2, p3, p4 = self._patch(quota)
        with p1, p2, p3, p4:
            with self.assertRaises(RuntimeError):
                kf.run_agent("p", None)
        self.assertEqual(len(calls), 1)                  # quota は回復が長い → レイヤ4/人へ

    def test_transient_exhausted_raises_with_attempts(self):
        def always(prompt, model, purpose="", cwd=None, **_kw):
            raise RuntimeError("service unavailable")
        p1, p2, p3, p4 = self._patch(always, retries=2)
        with p1, p2, p3, p4:
            with self.assertRaises(RuntimeError) as ctx:
                kf.run_agent("p", None)
        self.assertEqual(getattr(ctx.exception, "attempts", None), 3)
        self.assertIn("3 回試行後", str(ctx.exception))   # レイヤ1 を経たことが読める

    def test_retries_zero_disables_layer1(self):
        calls = []
        def always(prompt, model, purpose="", cwd=None, **_kw):
            calls.append(1)
            raise RuntimeError("connection refused")
        p1, p2, p3, p4 = self._patch(always, retries=0)
        with p1, p2, p3, p4:
            with self.assertRaises(RuntimeError):
                kf.run_agent("p", None)
        self.assertEqual(len(calls), 1)


class TransientRunBreakTests(unittest.TestCase):
    """レイヤ3純化: レイヤ1 を使い切った transient 失敗ノードは retry タスクを生成せず、
    run をタグ付き failed で打ち切る（レイヤ4 の auto-heal が拾う）。"""

    def _continue(self, results):
        nodes = {nid: {"goal": "g", "deps": [], "kind": "work"} for nid in results}
        args = types.SimpleNamespace(executor="stub", max_fanout=50, review=False,
                                     exemplar_first=False, max_retries=3)
        # env/transient 失敗はこれらのテストが確認する早期 return（_env_failure_reason）で
        # 決着するため bus は参照されない。ダミーの meta_path で十分。
        bus = types.SimpleNamespace(meta_path="/nonexistent/meta.json")
        return kf._continue(args, bus, "req", nodes, results, 0)

    def test_base_sync_preserves_transient_failure_class(self):
        self.assertEqual(kf._work_failure_class("base-sync", "connection timed out"), "transient")
        self.assertEqual(kf._work_failure_class("base-sync", "merge conflict unresolved"), "integration")

    def test_transient_failure_breaks_run_not_retries(self):
        decision, new_tasks, reason = self._continue(
            {"t1": {"status": "failed",
                    "output": "実行エラー: [agent-error:transient] ETIMEDOUT（3 回試行後）"}})
        self.assertEqual(decision, "failed")
        self.assertEqual(new_tasks, [])                  # retry タスクを積まない
        self.assertIn("[agent-error:transient]", reason)
        self.assertIn("auto-heal", reason)               # 自動再開候補であることが読める

    def test_structured_error_class_preferred(self):
        # worker が data.error_class を構造化していれば output のタグが無くても判定できる
        decision, _, reason = self._continue(
            {"t1": {"status": "failed", "output": "実行エラー: 落ちた",
                    "data": {"error_class": "transient", "attempts": 3}}})
        self.assertEqual(decision, "failed")
        self.assertIn("[agent-error:transient]", reason)

    def test_integration_failure_breaks_run_before_replanning(self):
        decision, new_tasks, reason = self._continue(
            {"base-sync": {"status": "failed", "output": "競合解消に失敗",
                           "data": {"error_class": "integration"}}})
        self.assertEqual(decision, "failed")
        self.assertEqual(new_tasks, [])
        self.assertIn("[agent-error:integration]", reason)
