"""agent-project の単体テスト — board（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class TestBoardOffload(unittest.TestCase):
    """タスク → 委譲公示板（agent-board）への委譲（delegation post 封筒の組み立てと投函）。"""

    def test_task_to_delegation_envelope(self):
        t = km.Task(id="feat/x", title="API を実装")
        t.set("desc", "詳細な指示")
        spec = {"url": "git@h:team/app.git", "name": "app", "base": "main", "path": "apps/api"}
        env = km.task_to_delegation(t, spec, workload="flow")
        self.assertEqual(env["op"], "post")
        self.assertEqual(env["version"], 1)
        self.assertTrue(env["id"].startswith("dg-"))
        self.assertTrue(all(c.isalnum() or c in "_-" for c in env["id"]))  # id 規約
        self.assertEqual(env["workload"], "flow")
        self.assertEqual(env["goal"], "API を実装")
        self.assertEqual(env["design"], "詳細な指示")
        self.assertEqual(env["workspace"], {"url": "git@h:team/app.git", "base": "main",
                                            "path": "apps/api"})
        # requires.repos は載せない: workspace.url が URL 正規化で担当ノードを選別する
        # （spec["name"] は依頼側ローカルなルーティング名なので、請負側が同じリポジトリを
        # 別名で宣言しているとURL一致でも入札不能になる誤検出を生むため）
        self.assertNotIn("requires", env)

    def test_write_board_post_idempotent(self):
        tmp = tempfile.mkdtemp(prefix="ap-board-")
        try:
            env = km.task_to_delegation(km.Task(id="t1", title="やる"), None)
            path = km.write_board_post(tmp, env)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(os.path.basename(path), "post.json")
            rec = json.load(open(path, encoding="utf-8"))
            self.assertEqual(rec["id"], env["id"])
            # 再投函は同一公示（上書きしない）
            with open(path, encoding="utf-8") as f:
                before = f.read()
            km.write_board_post(tmp, {**env, "goal": "書き換え"})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(before, f.read())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestBoardAutoWiring(unittest.TestCase):
    """依頼側の自動配線: location=board（auto の offload 一致 or 明示指定）で daemon が
    タスクを委譲公示板へ自動 post し、board の result.json を _reap_offloaded がポーリング回収する。
    請負側（agent-flow/agent-amigos の board 参加）が result.json を書く前提は
    agent_flow.board.report_board_results / agent_amigos.board.report_board_results 側でテスト済み。"""

    def _cfg(self, d, **kw):
        base = dict(board=str(d / "board"), board_workload="flow")
        base.update(kw)
        return cfg_for(d, dry_run=False, **base)

    # --- decide_location ---

    def test_auto_prefers_board_over_remote_when_offload_matches(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = self._cfg(d, location="auto", git_bus="git@h:x.git")
            policy = km.Policy(offload=["T1"])
            task = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(km.decide_location(task, policy, cfg), "board")

    def test_auto_falls_back_without_offload_match(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = self._cfg(d, location="auto")
            task = km.load_tasks(cfg.backlog)[0]
            # offload 一致なし → board 設定があっても従来どおり daemon 判定へ落ちる（daemon 不在 → local）
            self.assertEqual(km.decide_location(task, km.Policy(), cfg), "local")

    def test_explicit_board_without_config_falls_back_to_local(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = cfg_for(d, dry_run=False, location="board", board="")
            task = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(km.decide_location(task, km.Policy(), cfg), "local")

    def test_explicit_board_location_used_verbatim(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = self._cfg(d, location="board")
            task = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(km.decide_location(task, km.Policy(), cfg), "board")

    # --- _submit_bound ---

    def test_submit_bound_true_for_board_when_configured(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            self.assertTrue(km._submit_bound("board", cfg))

    # --- _act_board: post → pending → (外部が result.json を書く) → done ---

    def test_act_board_posts_then_resolves_after_external_result(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            cfg = self._cfg(d)
            task = km.load_tasks(cfg.backlog)[0]
            status, msg = km._act_board(task, cfg)
            self.assertIsInstance(status, km._Pending)
            did = status.run_id
            self.assertTrue(did.startswith("dg-"))
            board = km.BoardRepo(cfg.board)
            self.assertTrue(board.has_post(did))
            post = json.loads(open(os.path.join(board.delegation_dir(did), "post.json"),
                                   encoding="utf-8").read())
            self.assertEqual(post["workload"], "flow")
            self.assertIn(task.title, post["goal"])   # build_request の全文が goal に載る
            # 請負側が完了を報告した体（agent_flow.board.report_board_results 相当）
            with open(os.path.join(board.delegation_dir(did), "result.json"), "w",
                     encoding="utf-8") as f:
                json.dump({"winner": "pc-a", "native_id": did, "status": "done",
                          "resolved_by": "pc-a", "resolved_at": "2026-01-01T00:00:00Z"}, f)
            status2, msg2 = km._act_board(task, cfg)
            self.assertTrue(status2)
            self.assertIn("done", msg2)
            # 再投函しても post.json は上書きされない（冪等・二重公示防止）
            self.assertEqual(km._board_delegation_id(task, cfg), did)

    def test_act_board_reports_failed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = self._cfg(d)
            task = km.load_tasks(cfg.backlog)[0]
            status, _ = km._act_board(task, cfg)
            did = status.run_id
            board = km.BoardRepo(cfg.board)
            with open(os.path.join(board.delegation_dir(did), "result.json"), "w",
                     encoding="utf-8") as f:
                json.dump({"winner": "pc-a", "status": "failed",
                          "resolved_at": "2026-01-01T00:00:00Z"}, f)
            status2, msg2 = km._act_board(task, cfg)
            self.assertFalse(status2)
            self.assertIn("failed", msg2)

    # --- _reap_offloaded: board 経由の offloaded タスクが result.json 到着で settle する ---

    def _offloaded_board(self, d, tid, did, verify="true"):
        bd = d / "backlog"
        bd.mkdir(parents=True, exist_ok=True)
        (bd / f"{tid}.md").write_text(
            f"## {tid}: {tid}\n- status: offloaded\n- source: human\n- verify: `{verify}`\n"
            f"- retries: 0\n- flow_run: {did}\n- flow_loc: board\n", encoding="utf-8")

    def test_reap_settles_board_result_to_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded_board(d, "T1", "dg-x1", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_board_result_once", return_value=(True, True, "done")):
                deltas = km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertEqual(deltas["settled"], 1)
            self.assertEqual(deltas["archived"], 1)
            self.assertIsNone(km._load_task_file(cfg, "T1"))

    def test_reap_drops_delegation_from_board_after_collecting(self):
        # 終端を読み終えた公示は依頼側がその場で消す（設計 §4.2 gc tick「終端した公示」）。
        # 消してよいと知っているのは依頼側だけ——時間ベースの一括掃除だと、その期間
        # オフラインだった依頼側が結果を読む前に消えて offloaded が永久に固まる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded_board(d, "T1", "dg-drop", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            deleg = Path(cfg.board) / "delegations" / "dg-drop"
            deleg.mkdir(parents=True, exist_ok=True)
            (deleg / "post.json").write_text("{}", encoding="utf-8")
            (deleg / "result.json").write_text(
                json.dumps({"winner": "pc-x", "status": "done"}), encoding="utf-8")
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_board_result_once", return_value=(True, True, "done")):
                km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertFalse(deleg.exists(), "回収済みの公示が板に残っている")

    def test_reap_drops_delegation_only_after_task_leaves_offloaded(self):
        """公示を消すのは**タスクが offloaded を抜けた後**。先に消すと、そこでクラッシュした
        ときタスクは offloaded のまま公示だけ消え、次パスで結果が読めず永久に固まる
        （回収にタイムアウトが無い）——時間ベース掃除で避けたかった状態そのもの。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded_board(d, "T1", "dg-order", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            deleg = Path(cfg.board) / "delegations" / "dg-order"
            deleg.mkdir(parents=True, exist_ok=True)
            (deleg / "result.json").write_text(
                json.dumps({"winner": "pc-x", "status": "done"}), encoding="utf-8")
            tasks = km.load_tasks(cfg.backlog)
            seen = []
            real_persist = km.persist_task

            def spy(cfg_, task_, *a, **kw):
                seen.append((task_.norm_status(), deleg.exists()))
                return real_persist(cfg_, task_, *a, **kw)

            with mock.patch.object(km, "_board_result_once", return_value=(True, True, "done")), \
                 mock.patch.object(km, "persist_task", side_effect=spy):
                km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            # offloaded を抜ける（doing を書く）時点では公示がまだ在る
            self.assertIn(("doing", True), seen,
                          f"offloaded を抜ける前に公示を消している: {seen}")
            self.assertFalse(deleg.exists())     # パス末尾でまとめて消える

    def test_reap_keeps_delegation_while_not_terminal(self):
        # 未終端で消すと結果を取りこぼす。読めるまで残っていること。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded_board(d, "T1", "dg-keep", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            deleg = Path(cfg.board) / "delegations" / "dg-keep"
            deleg.mkdir(parents=True, exist_ok=True)
            (deleg / "post.json").write_text("{}", encoding="utf-8")
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_board_result_once", return_value=(False, False, "")):
                km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertTrue(deleg.exists())

    def test_reap_leaves_nonterminal_board_offload(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded_board(d, "T1", "dg-x1")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_board_result_once", return_value=(False, False, "")):
                deltas = km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertEqual(deltas["settled"], 0)
            self.assertEqual(km._load_task_file(cfg, "T1").norm_status(), "offloaded")

    def test_reap_board_failed_does_not_mark_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded_board(d, "T1", "dg-x1", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_board_result_once",
                                   return_value=(True, False, "board delegation dg-x1 failed")):
                km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            t = km._load_task_file(cfg, "T1")
            self.assertIsNotNone(t)
            self.assertNotEqual(t.norm_status(), "done")

    def test_reap_board_cancelled_reuses_cancelled_retry_path(self):
        # board.schema.json の status:"cancelled" は agent-project 側の人中止特例（人が中止
        # → retries を進めて ready に戻す）に合流するよう、末尾を "cancelled" にして報告する
        # （語彙統一 W0-9 以降、flow/board とも綴りは "cancelled" で共通）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded_board(d, "T1", "dg-x1", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_board_result_once",
                                   return_value=(True, False, "board delegation dg-x1 cancelled")):
                deltas = km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertEqual(deltas["settled"], 1)
            self.assertEqual(deltas["archived"], 0)
            t = km._load_task_file(cfg, "T1")
            self.assertEqual(t.norm_status(), "ready")
            self.assertEqual(t.retries, 1)

    # --- BoardRepo: git+ 経路の post/pull 往復（ブランチ既定 main へのフォールバック込み） ---

    def test_board_repo_git_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                  "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            posted = km.BoardRepo(f"git+{remote}", workdir=str(d / "poster"))
            env_bak = dict(os.environ)
            os.environ.update(env)
            try:
                posted.sync_pull()
                env_id = {"op": "post", "version": 1, "id": "dg-g1", "workload": "flow", "goal": "g"}
                self.assertTrue(posted.write_post(env_id))
                posted.sync_push("post dg-g1")
                # 別クローン（別ノード想定）から同じ委譲が見える
                reader = km.BoardRepo(f"git+{remote}", workdir=str(d / "reader"))
                reader.sync_pull(force=True)
                self.assertTrue(reader.has_post("dg-g1"))
            finally:
                os.environ.clear()
                os.environ.update(env_bak)


class TestAsyncOffload(unittest.TestCase):
    """非ブロッキング委譲（act_async）: daemon/remote への submit で待たず offloaded にし、次パスで
    ポーリングして終端した run だけ settle する（gitlab 等の長期委譲でループを塞がない）。"""

    def _cfg(self, d, **kw):
        return cfg_for(d, dry_run=False, act_async=True, executor="gitlab", **kw)

    def _offloaded(self, d, tid, run_id, verify="true"):
        bd = d / "backlog"
        bd.mkdir(parents=True, exist_ok=True)
        (bd / f"{tid}.md").write_text(
            f"## {tid}: {tid}\n- status: offloaded\n- source: human\n- verify: `{verify}`\n"
            f"- retries: 0\n- flow_run: {run_id}\n- flow_loc: daemon\n", encoding="utf-8")

    def test_pending_act_marks_task_offloaded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = self._cfg(d)
            with mock.patch.object(km, "_flow_result_once", return_value=(False, False, "")):
                km.run_loop(cfg, act=lambda t, c, loc: (km._Pending("run-T1"), "実行中"))
            t = km._load_task_file(cfg, "T1")
            self.assertEqual(t.norm_status(), "offloaded")
            self.assertEqual(t.get("flow_run"), "run-T1")

    def test_reap_settles_terminal_run_to_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded(d, "T1", "run-T1", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_flow_result_once", return_value=(True, True, "done")):
                deltas = km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertEqual(deltas["settled"], 1)
            self.assertEqual(deltas["archived"], 1)            # verify PASS → done → archive
            self.assertIsNone(km._load_task_file(cfg, "T1"))   # backlog から消えた（archive 済み）

    def test_reap_leaves_nonterminal_offloaded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded(d, "T1", "run-T1")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_flow_result_once", return_value=(False, False, "")):
                deltas = km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertEqual(deltas["settled"], 0)
            self.assertEqual(km._load_task_file(cfg, "T1").norm_status(), "offloaded")

    def test_reap_failed_run_does_not_mark_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # verify=true でも act/flow 失敗は偽 done にしない
            self._offloaded(d, "T1", "run-T1", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_flow_result_once",
                                   return_value=(True, False, "daemon run run-T1 failed")):
                km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            t = km._load_task_file(cfg, "T1")
            self.assertIsNotNone(t)
            self.assertNotEqual(t.norm_status(), "done")
            self.assertNotEqual(t.norm_status(), "review")

    def test_reap_canceled_run_does_not_mark_done(self):
        # verify=true でも人が中止した run を done にしない（リトライも焼かない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded(d, "T1", "run-T1", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(
                    km, "_flow_result_once",
                    return_value=(True, False, "daemon run run-T1 cancelled")):
                deltas = km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertEqual(deltas["settled"], 1)
            self.assertEqual(deltas["archived"], 0)
            t = km._load_task_file(cfg, "T1")
            self.assertEqual(t.norm_status(), "ready")
            self.assertEqual(t.retries, 1, "cancelled 後は新 run-id のため retries を進める")
            self.assertFalse(t.get("flow_run"))
            self.assertEqual(t.get("last_run"), "run-T1", "回収時に last_run を残す")

    def test_offload_pins_last_run(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = self._cfg(d)
            t = km._load_task_file(cfg, "T1")
            with mock.patch.object(km, "_flow_result_once", return_value=(False, False, "")):
                with mock.patch.object(km.subprocess, "run",
                                       return_value=subprocess.CompletedProcess(
                                           ["x"], 0, stdout="", stderr="")):
                    pend, msg = km._act_offload(t, cfg, use_git=False)
            self.assertIsInstance(pend, km._Pending)
            fresh = km._load_task_file(cfg, "T1")
            self.assertEqual(fresh.get("last_run"), pend.run_id)

    def test_has_work_true_for_offloaded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded(d, "T1", "run-T1")
            self.assertTrue(km.has_work(self._cfg(d)))

    def test_has_work_false_when_ready_only_has_unmet_deps(self):
        # blocked/doing の後ろに dep-gated ready が並ぶだけでは起こさない
        # （起こすと project_watch が空パスを無限に回す）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "backlog").mkdir()
            (d / "backlog" / "T1.md").write_text(
                "## T1: blocked root\n- status: blocked\n- verify: true\n", encoding="utf-8")
            (d / "backlog" / "T2.md").write_text(
                "## T2: waiting on T1\n- status: ready\n- after: T1\n- verify: true\n",
                encoding="utf-8")
            self.assertFalse(km.has_work(cfg_for(d)))

    def test_has_work_true_for_stale_doing(self):
        # 実行者が失踪した doing は次パスで回復するため起こす
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            t = km.load_tasks(cfg.backlog)[0]
            t.status = "doing"
            km.persist_task(cfg, t)
            # claim 無し＝実行者不明＝stale
            self.assertTrue(km.has_work(cfg))

    def test_act_via_agent_flow_offloads_when_async(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.load_tasks(cfg.backlog)[0]
            with mock.patch.object(km, "daemon_running", return_value=True), \
                 mock.patch.object(km, "_flow_result_once", return_value=(False, False, "")), \
                 mock.patch.object(km.subprocess, "run", return_value=types.SimpleNamespace(
                     returncode=0, stdout="run-T1\n", stderr="")):
                status, _ = km.act_via_agent_flow(t, cfg, "daemon")
            self.assertIsInstance(status, km._Pending)

    def test_sync_path_unaffected_when_async_off(self):
        # act_async 未指定なら従来どおり待つ（_act_submit）。daemon_running False → _act_run（同期）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = cfg_for(d, dry_run=False, executor="stub")   # act_async=False（既定）
            res = km.run_loop(cfg, act=lambda t, c, loc: (True, "ok"))
            self.assertEqual(res["reason"], km.REASON_DRAINED)
            self.assertGreaterEqual(res["archived"], 1)        # done → archive（従来どおり同期で確定）
            self.assertIsNone(km._load_task_file(cfg, "T1"))   # backlog から消えた（archive 済み）
