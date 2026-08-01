"""agent-project の単体テスト — coordination（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class TestAtomicClaim(unittest.TestCase):
    """原子的クレーム（共有 backlog／並列での二重実行防止）。"""

    def _task(self, d, tid="T1"):
        mkb(d, tid, verify="true")
        return km.Task(id=tid, title="x", status="ready", verify="true")

    def test_claim_excludes_second_then_release_reopens(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            self.assertTrue(km.claim_task(cfg, t))        # 1人目は取得
            self.assertFalse(km.claim_task(cfg, t))       # 2人目は弾かれる（新鮮なクレーム）
            km.release_claim(cfg, t)
            self.assertTrue(km.claim_task(cfg, t))         # 解放後は再取得できる

    def test_distributed_stale_doing_requires_human_reassignment(self):
        # coordination（複数 PC 制御）は設定キーでなく観測で決まる（実装計画 W1-8。
        # 設定キー "coordination: git-cas" は廃止）。「分散モード」を模すには origin を持つ
        # git リポジトリにした上で、取り合う相手（ピア）も宣言する——origin だけでは
        # 単独 PC 扱いで CAS を通さない。remote は reachable でなくてよい。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="doing")
            subprocess.run(["git", "init", "-q", str(d)], check=True)
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                            "file:///no-such-remote.git"], check=True)
            mk_peer(d)
            cfg = cfg_for(d, node="pc-a")
            task = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(km.recover_stale_doing(cfg, [task]), ["T1"])
            recovered = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(recovered.status, "blocked")
            self.assertTrue((cfg.needs / "T1.md").exists())

    def _origin_only_project(self, d: Path):
        """origin はあるが到達できない（オフライン）プロジェクト。"""
        mkb(d, "T1")
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                        "file:///no-such-remote.git"], check=True)
        return cfg_for(d, node="pc-a")

    def test_single_node_with_unreachable_origin_still_claims(self):
        # W1-8 は coordination を「origin があるか」で判定していたが、W1-7 で state_git: から
        # origin が自動設定されるようになったため、単独 PC のプロジェクトまで分散モードに入り、
        # リモートが落ちているだけで CAS が全て失敗して 1 件も claim できなくなっていた。
        # 取り合う相手がいなければ CAS を通す意味は無いので、ローカル claim を通す。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._origin_only_project(Path(d))
            self.assertFalse(km._coordination_active(cfg))
            self.assertTrue(km.claim_task(cfg, km.load_tasks(cfg.backlog)[0]))

    def test_peer_present_with_unreachable_origin_fails_closed(self):
        # 逆にピアがいるなら CAS を迂回してはいけない（同じタスクの二重取得を防ぐ）。
        # リモートに触れない以上、claim は成立させず fail closed のままにする。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._origin_only_project(d)
            mk_peer(d)
            self.assertTrue(km._coordination_active(cfg))
            self.assertFalse(km.claim_task(cfg, km.load_tasks(cfg.backlog)[0]))

    def test_peer_liveness_uses_freshness_not_availability(self):
        # 「排他が要るか」は鮮度だけで見る（drain 中のピアもまだ claim を握っているため）。
        # 期限切れの status は数えない（消えた PC でロックし続けない）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._origin_only_project(d)
            mk_peer(d, "pc-draining", availability="draining")
            self.assertEqual(km._peer_nodes(cfg), {"pc-draining"})
            mk_peer(d, "pc-stale", fresh_after_sec=-1.0)
            self.assertNotIn("pc-stale", km._peer_nodes(cfg))
            mk_peer(d, "pc-a")                      # 自分自身はピアに数えない
            self.assertNotIn("pc-a", km._peer_nodes(cfg))

    def test_stale_claim_is_stolen(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            lock = d / "claims" / "T1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text('{"host":"old","pid":1,"ts":0,"id":"T1"}', encoding="utf-8")  # 大昔
            self.assertTrue(km.claim_task(cfg, t))         # owner 失踪とみなし奪取

    def _dead_pid(self) -> int:
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid                                   # 確実に死んでいる pid

    def test_dead_same_host_owner_is_stolen_without_waiting_ttl(self):
        # kill/クラッシュで死んだ owner のロックは、TTL（既定 41 分）を待たず pid の生死で奪取する。
        # 待たされると、その間そのタスクは誰にも拾われず drained になる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            lock = d / "claims" / "T1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"host": socket.gethostname(), "pid": self._dead_pid(),
                                        "ts": time.time(), "id": "T1"}), encoding="utf-8")  # ts は新鮮
            self.assertTrue(km.claim_task(cfg, t))

    def test_dead_owner_is_stolen_even_when_ttl_is_infinite(self):
        # act_timeout<=0（無制限待ち）は _claim_ttl を inf にする。TTL だけで判定していると
        # 死んだ owner のロックが **永久に** 失効せず、そのタスクは二度と実行されない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.act_timeout = 0
            self.assertEqual(km._claim_ttl(cfg), float("inf"))
            t = self._task(d)
            lock = d / "claims" / "T1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"host": socket.gethostname(), "pid": self._dead_pid(),
                                        "ts": time.time(), "id": "T1"}), encoding="utf-8")
            self.assertTrue(km.claim_task(cfg, t))

    def test_live_owner_is_not_stolen(self):
        # 生きている owner のロックは（ts がいくら古くても）奪わない＝二重実行しない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            lock = d / "claims" / "T1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"host": socket.gethostname(), "pid": os.getpid(),
                                        "ts": 0, "id": "T1"}), encoding="utf-8")   # ts は大昔
            self.assertFalse(km.claim_task(cfg, t))

    def test_claim_revalidates_against_disk(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            (d / "backlog" / "T1.md").unlink()             # 別インスタンスが消化(archive)した想定
            self.assertFalse(km.claim_task(cfg, t))        # 取得後の再検証で弾く（二重実行防止）
            self.assertFalse((d / "claims" / "T1.lock").exists())  # ロックも残さない
            # 状態が consumable でない（review）なら同様に弾く
            t2 = self._task(d, "T2")
            (d / "backlog" / "T2.md").write_text(
                "## T2: x\n- status: review\n- verify: `true`\n", encoding="utf-8")
            self.assertFalse(km.claim_task(cfg, t2))

    def test_run_loop_releases_all_claims(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true"); mkb(d, "T2", verify="true")
            res = km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False, max_cycles=10))
            self.assertEqual(res["counts"]["done"], 2)
            claims = d / "claims"
            self.assertEqual(list(claims.glob("*.lock")) if claims.exists() else [], [])

    def test_approve_clears_stale_claim_lock(self):
        # worker クラッシュ等で残った古い claim ロックは、人手 approve で掃除される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "backlog" / "R1.md").parent.mkdir(parents=True, exist_ok=True)
            (d / "backlog" / "R1.md").write_text(
                "## R1: x\n- status: review\n- verify: `true`\n", encoding="utf-8")
            lock = d / "claims" / "R1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text('{"host":"dead","pid":1,"ts":0,"id":"R1"}', encoding="utf-8")
            km.cmd_approve(cfg_for(d, learn=False), "R1", "ok")
            self.assertFalse(lock.exists())                  # 承認時に古いロックを掃除

    def test_hold_clears_stale_claim_lock(self):
        # hold（blocked 化）でも doing を離れるので claim ロックを残さない
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "H1", verify="true")
            lock = d / "claims" / "H1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text('{"host":"dead","pid":1,"ts":0,"id":"H1"}', encoding="utf-8")
            km.cmd_hold(cfg_for(d, learn=False), "H1", "保留")
            self.assertFalse(lock.exists())

    def test_held_claim_makes_task_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true"); mkb(d, "T2", verify="true")
            (d / "claims").mkdir(parents=True, exist_ok=True)
            (d / "claims" / "T1.lock").write_text(           # 他インスタンスが保持中（新鮮）
                f'{{"host":"other","pid":99999,"ts":{time.time()},"id":"T1"}}', encoding="utf-8")
            calls = []
            res = km.run_loop(cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False,
                                      max_cycles=10),
                              act=lambda t, c, loc: calls.append(t.id) or (True, "ok"))
            self.assertEqual(calls, ["T2"])                  # T1 は他者保持で飛ばす
            self.assertEqual(res["counts"]["done"], 1)
            t1 = km.parse_task((d / "backlog" / "T1.md").read_text(), "T1")
            self.assertEqual(t1.norm_status(), "ready")      # T1 は手つかずのまま


class TestParallelConsumption(unittest.TestCase):
    """並列消費（§11）— 委譲公示板へ独立タスクを並行 post。請負側の worker 並列へ寄せる。"""

    def _tasks(self, n):
        return [km.Task(id=f"T{i}", title=f"t{i}", status="ready", verify="true")
                for i in range(n)]

    def _cfg(self, d, **kw):
        base = dict(location="board", board="board-repo", concurrency=3, dry_run=False,
                    learn=False, auto_adjudicate=False, max_cycles=50)
        base.update(kw)
        return cfg_for(Path(d), **base)

    def test_submit_bound(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            self.assertTrue(km._submit_bound("board", cfg))
            self.assertFalse(km._submit_bound("local", cfg))

    def test_select_batch_width_and_caps(self):
        with tempfile.TemporaryDirectory() as d:
            pol = km.parse_policy("")
            order = self._tasks(4)
            self.assertEqual(len(km._select_batch(order, self._cfg(d), pol, 10)), 3)  # concurrency=3
            self.assertEqual(len(km._select_batch(order, self._cfg(d), pol, 2)), 2)   # 残予算で制限
            self.assertEqual(len(km._select_batch(order, self._cfg(d, concurrency=1), pol, 10)), 1)
            self.assertEqual(len(km._select_batch(order, self._cfg(d, once=True), pol, 10)), 1)
            # 先頭が local 実行なら逐次（1件）に落とす
            local = self._cfg(d, location="local", board="")
            self.assertEqual(len(km._select_batch(order, local, pol, 10)), 1)

    def test_acts_run_concurrently(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(3):
                mkb(d, f"T{i}", verify="true")
            active = {"n": 0, "max": 0}
            lock = threading.Lock()

            def act(t, c, loc):
                with lock:
                    active["n"] += 1
                    active["max"] = max(active["max"], active["n"])
                time.sleep(0.05)
                with lock:
                    active["n"] -= 1
                return (True, "ok")

            res = km.run_loop(self._cfg(d), act=act)
            self.assertEqual(active["max"], 3)               # 3件が同時に走った
            self.assertEqual(res["counts"]["done"], 3)
            self.assertEqual(res["cycles"], 3)               # 1タスク=1サイクルを維持

    def test_location_passed_to_act(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(3):
                mkb(d, f"T{i}", verify="true")
            seen = []
            lock = threading.Lock()

            def act(t, c, loc):
                with lock:
                    seen.append(loc)
                return (True, "ok")

            km.run_loop(self._cfg(d), act=act)
            self.assertEqual(set(seen), {"board"})           # 板へ公示された

    def test_dry_run_parallel_skips_act(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(3):
                mkb(d, f"T{i}", verify="true")
            calls = []
            res = km.run_loop(self._cfg(d, dry_run=True),
                              act=lambda t, c, loc: calls.append(t.id) or (True, "x"))
            self.assertEqual(calls, [])                       # dry-run は act を呼ばない
            self.assertEqual(res["counts"]["done"], 3)        # verify=true で done

    def test_once_processes_single_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(3):
                mkb(d, f"T{i}", verify="true")
            res = km.run_loop(self._cfg(d, once=True), act=lambda t, c, loc: (True, "ok"))
            self.assertEqual(res["cycles"], 1)                # once は 1 件だけ
            self.assertEqual(res["reason"], "once")


class TestLocation(unittest.TestCase):
    def test_decide_and_cmd(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            t = km.Task(id="T1", title="heavy batch", verify="true")
            pol = km.Policy(offload=["heavy"])
            # auto: board 未設定 → local
            self.assertEqual(km.decide_location(t, pol, cfg_for(d)), "local")
            # auto: offload 一致＋board 設定あり → board
            c = cfg_for(d, board="board-repo", git_bus="git@x:team/bus.git")
            self.assertEqual(km.decide_location(t, pol, c), "board")
            # 明示 location
            self.assertEqual(km.decide_location(t, km.Policy(), cfg_for(d, location="board",
                                                                       board="board-repo")), "board")
            # board 指定だが board 未設定 → local
            self.assertEqual(km.decide_location(t, km.Policy(), cfg_for(d, location="board")), "local")
            self.assertIn("--git", km.build_agent_flow_cmd(t, c, use_git=True))
            self.assertNotIn("--git", km.build_agent_flow_cmd(t, c, use_git=False))

    def test_run_offloads(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "policy.md").write_text("offload: heavy\n")
            mkb(d, "T1", title="heavy job", verify="true")
            mkb(d, "T2", title="light job", verify="true")
            seen = {}

            def fake_act(task, cfg, location="local"):
                seen[task.id] = location
                return True, "ok"

            km.run_loop(cfg_for(d, dry_run=False, board="board-repo"), act=fake_act)
            self.assertEqual(seen["T1"], "board")
            self.assertEqual(seen["T2"], "local")


class TestFlowCliRouting(unittest.TestCase):
    def test_kf_base_git_flag(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d, git_bus="git@x:bus.git")
            self.assertNotIn("--git", km._kf_base(c, False))
            self.assertIn("--git", km._kf_base(c, True))

    def test_kf_base_passes_flow_config(self):
        """run / result / doctor のどの起動にも flow_config（--config）を渡す。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            yaml = d / "agent-flow.yaml"
            yaml.write_text("executor: stub\n", encoding="utf-8")
            c = cfg_for(d, flow_config=str(yaml))
            base = km._kf_base(c, False)
            self.assertIn("--config", base)
            got = base[base.index("--config") + 1]
            # 突き合わせは「同じファイルか」で行う。文字列一致にすると macOS の
            # /var → /private/var のような symlink 表記の違いだけで落ちる——本体は
            # abspath 止まりで symlink を解決しない（人が設定したパスの形を保つ）。
            self.assertTrue(os.path.isabs(got))
            self.assertTrue(os.path.samefile(got, yaml))


class RecoverStaleDoingTests(unittest.TestCase):
    """実行者が失踪した doing を ready へ戻す（再起動・クラッシュの自己回復）。

    doing は CONSUMABLE（ready/todo）ではないので次のパスでも拾われない。実行していた
    プロセスがいなくなると、claim ロックだけを残して永久に doing のまま止まる
    （viewer には「実行中」と見えるのに何も進まない）。"""

    def _doing(self, cfg, tid="T1"):
        t = km.Task(id=tid, title="x", status="doing", verify="true")
        km.persist_task(cfg, t)
        return t

    def _claim(self, cfg, tid, pid, host=None, ts=None):
        d = km._claims_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{tid}.lock").write_text(json.dumps({
            "host": host or socket.gethostname(), "pid": pid,
            "ts": ts if ts is not None else time.time(), "id": tid}), encoding="utf-8")

    def test_dead_owner_is_recovered(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            km.ensure_dirs(cfg)
            t = self._doing(cfg)
            self._claim(cfg, "T1", pid=999999)          # 存在しない pid＝失踪
            self.assertEqual(km.recover_stale_doing(cfg, [t]), ["T1"])
            self.assertEqual(t.norm_status(), "ready")
            self.assertFalse((km._claims_dir(cfg) / "T1.lock").exists(), "claim を解放する")
            self.assertEqual(t.retries, 0, "retries は据え置き（worker の失敗ではない）")

    def test_live_owner_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            km.ensure_dirs(cfg)
            t = self._doing(cfg)
            self._claim(cfg, "T1", pid=os.getpid())     # 自分＝生きている
            self.assertEqual(km.recover_stale_doing(cfg, [t]), [])
            self.assertEqual(t.norm_status(), "doing")

    def test_missing_claim_is_recovered(self):
        # claim ごと失われた doing（同期事故など）も救う
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            km.ensure_dirs(cfg)
            t = self._doing(cfg)
            self.assertEqual(km.recover_stale_doing(cfg, [t]), ["T1"])

    def test_remote_host_follows_ttl(self):
        # 別ホストは pid の生死を確かめられない → TTL に従う（新鮮なら触らない）
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            km.ensure_dirs(cfg)
            t = self._doing(cfg)
            self._claim(cfg, "T1", pid=1, host="other-host", ts=time.time())
            self.assertEqual(km.recover_stale_doing(cfg, [t]), [])
            self._claim(cfg, "T1", pid=1, host="other-host", ts=0)   # TTL 超過
            self.assertEqual(km.recover_stale_doing(cfg, [t]), ["T1"])


class TestUnknownQuarantine(unittest.TestCase):
    """W7: unknown 隔離の上限（既存 report 降格の出口）と、次パスの fencing 再確認 1 回。"""

    def _quarantined(self, d, tid, node="pc-a", rechecked=False):
        mkb(d, tid, status="blocked", verify="true")
        body = (d / "backlog" / f"{tid}.md").read_text(encoding="utf-8")
        t = km.parse_task(body, tid)
        t.set("fence_unknown", node)
        t.set("claim_owner", node)
        t.set("claim_token", "tok")
        t.set("claim_generation", "2")
        if rechecked:
            t.set("fence_recheck", "1")
        (d / "backlog" / f"{tid}.md").write_text(km.serialize_task(t), encoding="utf-8")
        return t

    def test_quarantine_cap_returns_throttle_reason(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, node="pc-a", unknown_quarantine_max=2)
            for tid in ("Q1", "Q2"):
                self._quarantined(d, tid)
            tasks = km.load_tasks(cfg.backlog)
            self.assertEqual(km._budget_reason(cfg, 0, time.time(), 0, 0.0, tasks),
                             km.REASON_THROTTLE)

    def test_other_nodes_quarantine_does_not_throttle_me(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, node="pc-b", unknown_quarantine_max=2)
            for tid in ("Q1", "Q2"):
                self._quarantined(d, tid, node="pc-a")   # 隔離元は pc-a
            tasks = km.load_tasks(cfg.backlog)
            self.assertIsNone(km._budget_reason(cfg, 0, time.time(), 0, 0.0, tasks))

    def test_recheck_restores_when_remote_matches(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, node="pc-a")
            km.ensure_dirs(cfg)
            t = self._quarantined(d, "Q1")
            (cfg.needs / "Q1.md").write_text("x", encoding="utf-8")
            remote = km.parse_task(km.serialize_task(t), "Q1")
            with mock.patch.object(km, "_coordination_active", return_value=True), \
                 mock.patch.object(km, "_fetch_remote_task", return_value=(remote, True)):
                out = km.requeue_unknown_once(cfg, km.load_tasks(cfg.backlog))
            self.assertEqual(out, ["Q1"])
            back = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(back.norm_status(), "ready")
            self.assertFalse(back.get("fence_unknown"))
            self.assertFalse((cfg.needs / "Q1.md").exists())

    def test_recheck_happens_only_once(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, node="pc-a")
            km.ensure_dirs(cfg)
            self._quarantined(d, "Q1")
            # 1 回目: 依然リモート不通 → blocked のまま・fence_recheck が立つ
            with mock.patch.object(km, "_coordination_active", return_value=True), \
                 mock.patch.object(km, "_fetch_remote_task", return_value=(None, False)) as f:
                self.assertEqual(km.requeue_unknown_once(cfg, km.load_tasks(cfg.backlog)), [])
                self.assertEqual(f.call_count, 1)
            back = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(back.norm_status(), "blocked")
            self.assertEqual(back.get("fence_recheck"), "1")
            # 2 回目: もう自動では触らない（人待ち）
            with mock.patch.object(km, "_coordination_active", return_value=True), \
                 mock.patch.object(km, "_fetch_remote_task") as f2:
                self.assertEqual(km.requeue_unknown_once(cfg, km.load_tasks(cfg.backlog)), [])
                f2.assert_not_called()
