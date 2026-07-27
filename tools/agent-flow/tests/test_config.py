"""agent-flow の単体テスト — config（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from _shared import _Args, _commit_change, _make_skill_repo  # noqa: E402,F401 — `import *` は _ 始まりを持ってこない


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-test-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("test request")

    def _add_task(self, tid, goal="g", deps=None):
        self.bus.write_task({"id": tid, "goal": goal, "deps": deps or []})
        graph = self.bus.read_graph() or {"nodes": {}, "iteration": 0}
        graph["nodes"][tid] = {"goal": goal, "deps": deps or []}
        self.bus.write_graph(graph)

    def test_pending_then_claimed_then_done(self):
        self._add_task("t1")
        self.assertEqual(self.bus.node_state("t1"), "pending")
        self.assertTrue(self.bus.try_claim("t1", "w1", lease_sec=60))
        self.assertEqual(self.bus.node_state("t1"), "claimed")
        self.bus.write_result("t1", "w1", "done", "out")
        self.assertEqual(self.bus.node_state("t1"), "done")

    def test_deterministic_winner_earliest_ts(self):
        self._add_task("t1")
        # 名前空間付き claim を 2 つ手で書く（ts が古い方が勝つ）
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "wB.json"),
                             {"who": "wB", "ts": 200.0, "lease_until": time.time() + 999})
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "wA.json"),
                             {"who": "wA", "ts": 100.0, "lease_until": time.time() + 999})
        self.assertEqual(self.bus._winner("t1"), "wA")

    def test_expired_lease_is_reclaimable(self):
        """障害注入: ワーカーが死んで claim を残したケース。lease 切れなら再 claim できる。"""
        self._add_task("t1")
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "dead.json"),
                             {"who": "dead", "ts": 1.0, "lease_until": time.time() - 10})
        self.assertIsNone(self.bus._winner("t1"))          # 期限切れは無視
        self.assertEqual(self.bus.node_state("t1"), "pending")
        self.assertTrue(self.bus.try_claim("t1", "w2", lease_sec=60))  # 別ノードが回収
        self.assertEqual(self.bus._winner("t1"), "w2")

    def test_concurrent_claim_single_winner(self):
        """障害注入相当: 複数ワーカーが同時に同じタスクを取りに行っても勝者は 1 人。"""
        self._add_task("t1")
        results = {}
        barrier = threading.Barrier(5)

        def worker(name):
            barrier.wait()
            results[name] = self.bus.try_claim("t1", name, lease_sec=60)

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [n for n, ok in results.items() if ok]
        self.assertEqual(len(winners), 1, f"勝者は 1 人のはず: {results}")

    def test_extend_claim_keeps_ts_and_extends_lease(self):
        # 心拍は lease_until だけを延ばす（ts を振り直すと勝者タイブレークの根拠が動く）
        self._add_task("t1")
        self.assertTrue(self.bus.try_claim("t1", "w1", lease_sec=60))
        before = kf.read_json(os.path.join(self.bus._claim_dir("t1"), "w1.json"))
        self.assertTrue(self.bus.extend_claim("t1", "w1", lease_sec=600))
        after = kf.read_json(os.path.join(self.bus._claim_dir("t1"), "w1.json"))
        self.assertEqual(after["ts"], before["ts"])
        self.assertGreater(after["lease_until"], before["lease_until"])

    def test_extend_claim_refuses_after_loss(self):
        # lease 失効中に他ワーカーが正当に claim したら、旧ワーカーの心拍は延長できない
        # （書き戻すと二重実行になる）
        self._add_task("t1")
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "old.json"),
                             {"who": "old", "ts": 1.0, "lease_until": time.time() - 10})
        self.assertTrue(self.bus.try_claim("t1", "w2", lease_sec=60))
        self.assertFalse(self.bus.extend_claim("t1", "old", lease_sec=600))
        self.assertEqual(self.bus._winner("t1"), "w2")

    def test_extend_claim_refuses_when_released(self):
        # release/withdraw 済みの claim を心拍が復活させない
        self._add_task("t1")
        self.assertFalse(self.bus.extend_claim("t1", "ghost", lease_sec=600))
        self.assertIsNone(self.bus._winner("t1"))

    def test_claim_lock_is_off_bus(self):
        # 排他ロックはバス（git に乗る領域）の外＝一時領域に置く
        # （claim の実装は agentcore.protocol へ委譲済み — 設計 §4.1・R1）。
        lp = kf.protocol._lock_path(self.bus._claim_dir("t1"))
        self.assertNotIn(self.tmp, lp)
        self.assertIn("agentcore-claim-locks", lp)

    def test_all_terminal(self):
        self._add_task("t1")
        self._add_task("t2")
        self.assertFalse(self.bus.all_terminal())
        self.bus.write_result("t1", "w", "done", "o")
        self.bus.write_result("t2", "w", "failed", "o")
        self.assertTrue(self.bus.all_terminal())  # done/failed はどちらも terminal


class InheritTests(unittest.TestCase):
    """リトライ時の引き継ぎ（inherit_from）: 先行 run から確定済みノードを引き継ぎ、
    先行 run を掃除する。タイムアウト/失敗で毎回ゼロからやり直すのを防ぐ。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-inherit-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk_run(self, rid, request="req", workspace=None):
        b = kf.Bus(self.tmp, rid)
        b.ensure_run(request, workspace=workspace)
        return b

    def _add_task(self, bus, tid, deps=None):
        bus.write_task({"id": tid, "goal": "g", "deps": deps or []})
        graph = bus.read_graph() or {"strategy": {}, "nodes": {}, "iteration": 0}
        graph["nodes"][tid] = {"goal": "g", "deps": deps or []}
        bus.write_graph(graph)

    def test_partial_inherit_copies_done_and_removes_old(self):
        old = self._mk_run("req-x-t-r0")
        self._add_task(old, "t1")
        self._add_task(old, "t2")
        old.write_result("t1", "w", "done", "out1", data={"k": 1})
        old.write_result("t2", "w", "failed", "boom")   # 失敗ノードは引き継がない
        old.mark_run_failed("req-x-t-r0", "timed out")   # 終端化（削除の安全条件）

        new = kf.Bus(self.tmp, "req-x-t-r1")
        info = new.inherit_from("req-x-t-r0")

        self.assertEqual(info["seeded_nodes"], 1)
        self.assertTrue(info["inherited"])
        self.assertTrue(info["deleted"])
        # done ノードだけ引き継ぐ＝t1 は done 扱い、t2 は引き継がれず再実行対象
        self.assertEqual(new.node_state("t1"), "done")
        self.assertEqual(new.read_result("t1").get("data"), {"k": 1})
        self.assertIsNone(new.read_result("t2"))
        self.assertIsNotNone(new.read_graph())           # 計画（graph）も引き継ぐ
        # 先行 run は掃除済み
        self.assertNotIn("req-x-t-r0", new.list_runs())
        self.assertEqual(new.run_meta("req-x-t-r1").get("inherited_from"), "req-x-t-r0")

    def test_canceled_predecessor_is_not_inherited(self):
        old = self._mk_run("req-cxl-t-r0")
        self._add_task(old, "t1")
        old.write_result("t1", "w", "done", "out1")
        old.mark_canceled("req-cxl-t-r0", "人の停止")
        new = kf.Bus(self.tmp, "req-cxl-t-r1")
        info = new.inherit_from("req-cxl-t-r0")
        self.assertFalse(info["inherited"])
        self.assertFalse(info["deleted"])
        self.assertEqual(info["seeded_nodes"], 0)
        self.assertIn("cancelled", info["reason"])
        self.assertIn("req-cxl-t-r0", new.list_runs())  # 触らない

    def test_fully_done_predecessor_seeds_nothing_but_cleans_up(self):
        old = self._mk_run("req-y-t-r0")
        self._add_task(old, "t1")
        old.write_result("t1", "w", "done", "out")
        old.set_status("done")                           # verify=NG 相当（全ノード done で終端）

        new = kf.Bus(self.tmp, "req-y-t-r1")
        info = new.inherit_from("req-y-t-r0")

        self.assertEqual(info["seeded_nodes"], 0)        # 同一出力で即 done の無限ループを避ける
        self.assertTrue(info["deleted"])
        self.assertIsNone(kf.read_json(new.meta_path))   # 新 run は白紙（feedback 付きで再計画）
        self.assertNotIn("req-y-t-r0", new.list_runs())

    def test_tombstone_preserves_predecessor_records(self):
        """先行 run の削除前に墓標（inherited/<old>.json）が残る。失敗ノードの結果も
        要約に含む（seed は done だけだが、記録としては全ノード残す）。"""
        old = self._mk_run("req-tb-t-r0")
        self._add_task(old, "t1")
        self._add_task(old, "t2")
        old.write_result("t1", "w", "done", "out1", data={"k": 1})
        old.write_result("t2", "w", "failed", "boom")
        old.mark_run_failed("req-tb-t-r0", "timed out")

        new = kf.Bus(self.tmp, "req-tb-t-r1")
        new.inherit_from("req-tb-t-r0")

        tomb = kf.read_json(os.path.join(new.inherited_dir, "req-tb-t-r0.json"))
        self.assertIsNotNone(tomb, "墓標が残る")
        self.assertEqual(tomb["run_id"], "req-tb-t-r0")
        self.assertEqual(tomb["meta"].get("status"), "failed")
        self.assertIn("t1", tomb["results"])
        self.assertIn("t2", tomb["results"], "失敗ノードの記録も墓標には残す")
        self.assertEqual(tomb["results"]["t2"]["status"], "failed")
        self.assertIsNotNone(tomb.get("graph"))

    def test_tombstone_on_fully_done_predecessor(self):
        """全ノード done（verify NG リトライ）は状態を引き継がないが、墓標は残る＝
        完走した run の成果記録がリトライで消えない。長い出力は抜粋になる。"""
        old = self._mk_run("req-tbd-t-r0")
        self._add_task(old, "t1")
        long_out = "x" * 10000
        old.write_result("t1", "w", "done", long_out)
        old.set_status("done")

        new = kf.Bus(self.tmp, "req-tbd-t-r1")
        info = new.inherit_from("req-tbd-t-r0")

        self.assertEqual(info["seeded_nodes"], 0)
        self.assertIsNone(kf.read_json(new.meta_path))   # 新 run は白紙のまま
        tomb = kf.read_json(os.path.join(new.inherited_dir, "req-tbd-t-r0.json"))
        self.assertIsNotNone(tomb)
        out = tomb["results"]["t1"]["output"]
        self.assertIn("中略", out, "長い出力は抜粋で残す")
        self.assertLess(len(out), len(long_out))

    def test_tombstone_chain_is_carried_forward(self):
        """r0→r1→r2 と世代交代しても、r2 に全世代の墓標が残る（r1 削除時に r1 の
        inherited/ を持ち越す）。"""
        r0 = self._mk_run("req-ch-t-r0")
        self._add_task(r0, "t1")
        self._add_task(r0, "t2")                          # 未完ノードを残す＝部分引き継ぎで r1 が実体化する
        r0.write_result("t1", "w", "done", "out0")
        r0.mark_run_failed("req-ch-t-r0", "gen0")
        r1 = kf.Bus(self.tmp, "req-ch-t-r1")
        r1.inherit_from("req-ch-t-r0")
        r1.mark_run_failed("req-ch-t-r1", "gen1")
        r2 = kf.Bus(self.tmp, "req-ch-t-r2")
        r2.inherit_from("req-ch-t-r1")
        self.assertIsNotNone(kf.read_json(os.path.join(r2.inherited_dir, "req-ch-t-r0.json")))
        self.assertIsNotNone(kf.read_json(os.path.join(r2.inherited_dir, "req-ch-t-r1.json")))

    def test_live_predecessor_is_untouched(self):
        old = self._mk_run("req-z-t-r0")
        self._add_task(old, "t1")
        old.set_status("running")
        old.touch_run("req-z-t-r0", 9999)                # 生存リースが有効＝実行中

        new = kf.Bus(self.tmp, "req-z-t-r1")
        info = new.inherit_from("req-z-t-r0")

        self.assertFalse(info["deleted"])
        self.assertFalse(info["inherited"])
        self.assertIn("req-z-t-r0", new.list_runs())     # 走っている run は消さない

    def test_missing_predecessor_is_noop(self):
        new = kf.Bus(self.tmp, "req-none-r1")
        info = new.inherit_from("req-none-r0")
        self.assertFalse(info["deleted"])
        self.assertFalse(info["inherited"])

    def test_workspace_branch_is_chained_from_old(self):
        ws = {"url": "https://git.example/g/r", "path": "", "base": "main",
              "target": "main", "desc": ""}
        old = self._mk_run("req-w-t-r0", workspace=ws)
        self._add_task(old, "t1")
        self._add_task(old, "t2")                        # 未完ノードを残す＝部分引き継ぎ
        old.write_result("t1", "w", "done", "out")
        old.mark_run_failed("req-w-t-r0", "timed out")

        new = kf.Bus(self.tmp, "req-w-t-r1")
        new.inherit_from("req-w-t-r0")
        # 確定済みノードの commit を失わないよう、新 run は旧ブランチ af/<old> から派生する
        self.assertEqual(new.run_workspace().get("base"), kf.run_branch_name("req-w-t-r0"))


class DoctorTests(unittest.TestCase):
    """agent-flow の稼働診断（doctor）: env/config チェック・シグナル・修正・分類・連携用 JSON。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-doc-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_findings_kiro_cli_and_finite_stop(self):
        args = _Args(os.path.join(self.tmp, "bus"), executor="agent",
                     max_iterations=0, lease=0)
        fs = kf.doctor_env_findings(args, which=lambda _n: None)
        ids = {f["title"]: f for f in fs}
        self.assertTrue(any("kiro-cli" in t for t in ids))          # executor=agent・agent_cli既定kiro → env critical
        self.assertTrue(any("max_iterations" in t for t in ids))    # ≤0 → config critical
        self.assertTrue(any("リース" in t for t in ids))             # lease≤0 → config warn
        # バス未作成は ensure-bus アクション付き
        bus = next(f for f in fs if f.get("fix_action") == "ensure-bus")
        self.assertEqual(bus["category"], "config")

    def test_env_findings_clean_when_ready(self):
        bus = os.path.join(self.tmp, "bus")
        os.makedirs(bus)
        args = _Args(bus)                                            # executor=stub・正の閾値
        fs = kf.doctor_env_findings(args, which=lambda n: "/usr/bin/" + n)
        self.assertEqual(fs, [])                                     # 所見なし

    def test_env_findings_check_binary_matching_agent_cli(self):
        # agent_cli=claude のときは kiro-cli ではなく claude の PATH 不在を報告する
        # （executor/planner=agent は agent_cli に委譲するため、必須バイナリも agent_cli 依存）。
        args = _Args(os.path.join(self.tmp, "bus"), executor="agent", agent_cli="claude")
        fs = kf.doctor_env_findings(args, which=lambda n: None if n == "claude" else "/usr/bin/" + n)
        titles = [f["title"] for f in fs]
        self.assertTrue(any("claude" in t for t in titles))
        self.assertFalse(any("kiro-cli" in t for t in titles))

    def test_non_normalized_node_id_declaration_is_reported(self):
        # §6-2 の決着（2026-07-27）: 明示値は正規化しない。黙って綴りを書き換える代わりに、
        # 正規形でないことを doctor が知らせて切替（人の明示操作）を促す。
        bus = os.path.join(self.tmp, "bus")
        os.makedirs(bus)
        args = _Args(bus, node_id="DESKTOP-X")
        fs = kf.doctor_env_findings(args, which=lambda n: "/usr/bin/" + n)
        self.assertEqual([f["title"] for f in fs], ["宣言した node_id が正規形ではない"])
        self.assertEqual(fs[0]["severity"], "warn")          # 起動は止めない
        self.assertIn("desktop-x", fs[0]["evidence"])        # 正規形を示す

    def test_normalized_or_absent_node_id_is_silent(self):
        bus = os.path.join(self.tmp, "bus")
        os.makedirs(bus)
        for declared in ("desk-a", None, ""):
            args = _Args(bus, node_id=declared)
            self.assertEqual(kf.doctor_env_findings(args, which=lambda n: "/usr/bin/" + n), [],
                             f"node_id={declared!r} で所見が出た")

    def test_apply_fix_ensure_bus(self):
        bus = os.path.join(self.tmp, "bus")
        args = _Args(bus)
        msg = kf.apply_doctor_fix(args, {"fix_action": "ensure-bus"})
        self.assertTrue(os.path.isdir(bus))
        self.assertIn("作成", msg)

    def test_signals_flag_stuck_and_failed_runs(self):
        bus_root = os.path.join(self.tmp, "bus")
        b = kf.Bus(bus_root, "runOld")
        b.ensure_run("古い要求")
        b.write_graph({"nodes": {"t1": {"goal": "g", "deps": []}}, "iteration": 4})
        b.write_task({"id": "t1", "goal": "g", "deps": []})
        b.write_result("t1", "w1", "failed", "例外: Traceback ...")
        # 滞留判定のため updated_at を十分過去にする
        meta = kf.read_json(b.meta_path)
        meta["status"] = "running"
        meta["updated_at"] = "2000-01-01T00:00:00Z"
        meta["created_at"] = "2000-01-01T00:00:00Z"
        kf.write_json_atomic(b.meta_path, meta)

        sig = kf.collect_doctor_signals(_Args(bus_root))
        self.assertEqual(sig["runs_total"], 1)
        self.assertTrue(any(s["run"] == "runOld" for s in sig["stuck"]))   # 非終端＋高齢→滞留
        self.assertTrue(any(fl["run"] == "runOld" for fl in sig["failed"]))
        self.assertTrue(any("runOld" == e.get("run") for e in sig["errors"]))

    def test_cmd_doctor_json_and_program_routing(self):
        bus_root = os.path.join(self.tmp, "bus")
        os.makedirs(bus_root)
        args = _Args(bus_root, json=True, fix=True)
        filed = []

        def agent(prompt, model):
            if "稼働診断医" in prompt:
                return ('[{"category":"program","severity":"critical",'
                        '"title":"グラフ生成バグ","evidence":"run","fix":"例外"}]')
            filed.append("file")
            return "起票"

        import io
        import contextlib as _ctx
        buf = io.StringIO()
        with _ctx.redirect_stdout(buf):
            rc = kf.cmd_doctor(args, agent_run=agent, skill_finder=lambda _n: None)
        # スキルが見つからない → 出力のみ（起票しない）
        self.assertEqual(filed, [])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["tool"], "agent-flow")
        self.assertTrue(any(f["category"] == "program" for f in out["findings"]))
        self.assertEqual(rc, 2)                                     # 未解決 critical program

    def test_cmd_doctor_files_via_gitlab_idd_when_present(self):
        bus_root = os.path.join(self.tmp, "bus")
        os.makedirs(bus_root)
        args = _Args(bus_root, json=True, fix=True)
        skill = os.path.join(self.tmp, "skills", "gitlab-idd")
        os.makedirs(skill)
        filed = []

        def agent(prompt, model):
            if "稼働診断医" in prompt:
                return ('[{"category":"program","severity":"critical",'
                        '"title":"バグ","evidence":"e","fix":"f"}]')
            filed.append("file")
            return "起票しました"

        import io
        import contextlib as _ctx
        with _ctx.redirect_stdout(io.StringIO()):
            rc = kf.cmd_doctor(args, agent_run=agent, skill_finder=lambda _n: skill)
        self.assertEqual(filed, ["file"])                          # gitlab-idd へ委譲
        self.assertEqual(rc, 0)                                    # 唯一の所見が起票で解消 → healthy


class SelfUpdateTests(unittest.TestCase):
    def test_split_subdirs_and_default_carries_shared(self):
        """既定の `update_subdir` が共有物（tools/agent-tools）を含むこと。

        cone mode の sparse-checkout は指定ディレクトリの**兄弟を含まない**。本体だけ取ると
        installer が zipapp へ同梱する agentcore が無く必ず失敗する——自己更新が毎回
        サイレントに見送られる（agent-project 側で実測した既存不具合と同じ形）。"""
        self.assertEqual(kf.split_subdirs("a/b c/d"), ["a/b", "c/d"])
        self.assertEqual(kf.split_subdirs("a/b, c/d"), ["a/b", "c/d"])
        fallback = kf.split_subdirs("")
        self.assertEqual(fallback, kf.TOOL_SUBDIR.split())
        self.assertTrue(all(" " not in p for p in fallback))
        self.assertIn("tools/agent-tools", fallback)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-update-")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state, exist_ok=True)
        self._old = os.environ.get("KIRO_STATE_HOME")
        os.environ["KIRO_STATE_HOME"] = self.state
        self.repo = _make_skill_repo(self.tmp)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KIRO_STATE_HOME", None)
        else:
            os.environ["KIRO_STATE_HOME"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _args(self, **kw):
        base = dict(update_repo=self.repo, update_branch="main",
                    update_subdir="tools/agent-flow", update_installer="install.sh",
                    update_check_interval=60.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_remote_branch_sha(self):
        sha = kf.remote_branch_sha(self.repo, "main")
        self.assertTrue(sha and len(sha) >= 7)
        self.assertIsNone(kf.remote_branch_sha("", "main"))
        self.assertIsNone(kf.remote_branch_sha(self.repo, "no-such-branch"))

    def test_check_update_baseline_then_latest(self):
        a = self._args()
        info = kf.check_update(a)              # 初回: ベースライン記録・更新なし
        self.assertTrue(info["enabled"])
        self.assertTrue(info["baseline"])
        self.assertFalse(info["available"])
        info2 = kf.check_update(a)             # 2 回目: 最新
        self.assertFalse(info2["baseline"])
        self.assertFalse(info2["available"])

    def test_check_update_detects_new_commit(self):
        a = self._args()
        kf.check_update(a)                     # ベースライン
        _commit_change(self.repo, "tools/agent-flow/NEW.txt")
        self.assertTrue(kf.check_update(a)["available"])

    def test_disabled_when_no_repo(self):
        a = self._args(update_repo="")
        self.assertFalse(kf.check_update(a)["enabled"])
        self.assertFalse(kf.maybe_self_update(a, idle=True, state={"last": 0.0}))

    def test_sparse_checkout_only_subdir(self):
        dest = os.path.join(self.tmp, "co", "repo")
        tool_dir = kf.sparse_checkout_tool(self.repo, "main", "tools/agent-flow", dest)
        self.assertTrue(os.path.isfile(os.path.join(tool_dir, "install.sh")))
        # sparse: 無関係な tools/agent-project は作業ツリーに展開されない
        self.assertFalse(os.path.isdir(os.path.join(dest, "tools", "agent-project")))

    def test_sparse_checkout_includes_shared_dir(self):
        # 共有物（tools/agent-tools）を並べれば一緒に落ちてくること。取れないと installer が
        # agentcore を同梱できず、自己更新がサイレントに見送られ続ける。
        dest = os.path.join(self.tmp, "co2", "repo")
        tool_dir = kf.sparse_checkout_tool(
            self.repo, "main", "tools/agent-flow tools/agent-tools", dest)
        self.assertEqual(tool_dir, os.path.join(dest, "tools", "agent-flow"))
        self.assertTrue(os.path.isdir(os.path.join(dest, "tools", "agent-tools", "agentcore")))
        self.assertFalse(os.path.isdir(os.path.join(dest, "tools", "agent-project")))

    def test_run_installer(self):
        dest = os.path.join(self.tmp, "co2", "repo")
        tool_dir = kf.sparse_checkout_tool(self.repo, "main", "tools/agent-flow", dest)
        prefix = os.path.join(self.tmp, "prefix")
        ok, out = kf.run_installer(tool_dir, "install.sh",
                                   runner=lambda c, **k: subprocess.run(
                                       c + ["--prefix", prefix], capture_output=True,
                                       text=True, **k))
        self.assertTrue(ok, out)
        self.assertTrue(os.path.isfile(os.path.join(prefix, "INSTALLED_MARKER")))

    def test_apply_update_records_sha(self):
        a = self._args()
        kf.check_update(a)                     # baseline
        _commit_change(self.repo, "tools/agent-flow/N2.txt")
        info = kf.check_update(a)
        self.assertTrue(info["available"])
        prefix = os.path.join(self.tmp, "prefix2")

        def runner(c, **k):                    # install.sh だけ --prefix を足す
            cmd = c + ["--prefix", prefix] if c[:1] == ["bash"] else c
            return subprocess.run(cmd, capture_output=True, text=True, **k)
        self.assertTrue(kf.apply_update(a, info, runner=runner))
        self.assertEqual(kf.read_update_state()["applied_sha"], info["remote_sha"])
        self.assertFalse(kf.check_update(a)["available"])   # 適用後は最新

    def test_maybe_self_update_hands_off_to_child(self):
        # 取り込み（clone → install.sh）は切り離した子プロセスへ渡す。参加巡回は呼び出し側に
        # 120 秒で kill されるので、この中で install.sh を回すと本体が半分だけ入れ替わりうる。
        a = self._args()
        kf.check_update(a)                                   # baseline
        _commit_change(self.repo, "tools/agent-flow/N4.txt")
        spawned, applied = [], []
        with mock.patch.object(kf, "_spawn_update_child", side_effect=lambda: spawned.append(1)), \
             mock.patch.object(kf, "apply_update", side_effect=lambda *ar, **k: applied.append(1)):
            self.assertTrue(kf.maybe_self_update(a, idle=True, state={"last": 0.0}))
        self.assertEqual(applied, [])                        # 巡回の中では適用しない
        self.assertEqual(len(spawned), 1)                    # 取り込みは子プロセスへ

    def test_spawn_update_child_argv(self):
        captured = {}
        with mock.patch.object(kf.subprocess, "Popen",
                               side_effect=lambda c, **k: captured.update(cmd=c, kw=k)):
            kf._spawn_update_child()
        self.assertEqual(captured["cmd"][-2:], ["update", "--now"])
        if os.name != "nt":          # 親（参加巡回）が kill されても道連れにしない
            self.assertTrue(captured["kw"]["start_new_session"])

    def test_maybe_self_update_interval_gate(self):
        a = self._args(update_check_interval=3600.0, update_enabled=True)
        st = {"last": time.time()}            # 直近にチェック済み → interval 内は何もしない
        self.assertFalse(kf.maybe_self_update(a, idle=True, state=st))
        # idle でなければチェックしない
        self.assertFalse(kf.maybe_self_update(a, idle=False, state={"last": 0.0}))

    def test_update_enabled_false_disables(self):
        a = self._args(update_enabled=False, update_check_interval=3600.0)
        self.assertFalse(kf.maybe_self_update(a, idle=True, state={"last": 0.0}))

    def test_apply_update_skips_when_tool_unchanged(self):
        # リポジトリの HEAD は進んだが update_subdir の内容は前回適用と同一 → installer を
        # 実行せずベースラインだけ進める（自分の push が update_repo の新コミットになる構成
        # での自己増殖ループ防止。agent-project と同じ護り）。
        a = self._args()
        kf.check_update(a)                                     # baseline
        _commit_change(self.repo, "tools/agent-flow/N3.txt")
        prefix = os.path.join(self.tmp, "prefix3")

        def runner(c, **k):
            cmd = c + ["--prefix", prefix] if c[:1] == ["bash"] else c
            return subprocess.run(cmd, capture_output=True, text=True, **k)
        self.assertTrue(kf.apply_update(a, kf.check_update(a), runner=runner))   # 実変更 → 適用
        _commit_change(self.repo, "journal.md")                # subdir 外だけが進む
        info = kf.check_update(a)
        self.assertTrue(info["available"])                     # SHA 上は更新に見える
        calls = []

        def counting(c, **k):
            calls.append(list(c))
            return runner(c, **k)
        self.assertFalse(kf.apply_update(a, info, runner=counting))     # 適用スキップ
        self.assertFalse(any(c[:1] == ["bash"] for c in calls))         # installer 不実行
        self.assertEqual(kf.read_update_state()["applied_sha"], info["remote_sha"])
        self.assertFalse(kf.check_update(a)["available"])      # ベースライン前進 → 最新扱い

    def test_update_check_interval_survives_restart(self):
        # チェック間隔は state ファイルへ持続化され、自己更新の再起動（新プロセス＝呼び出し側
        # state dict のリセット）を跨いで尊重される（再起動直後の即時再チェックを防ぐ）。
        a = self._args(update_check_interval=3600.0, update_enabled=True)
        calls = []
        with mock.patch.object(kf, "check_update",
                               side_effect=lambda *ar, **k: (calls.append(1),
                                                             {"available": False})[1]):
            self.assertFalse(kf.maybe_self_update(a, idle=True, state={"last": 0.0}))
            self.assertEqual(len(calls), 1)                    # 初回はチェックする
            # プロセス再起動を模擬（呼び出し側の state dict が新品になる）
            self.assertFalse(kf.maybe_self_update(a, idle=True, state={"last": 0.0}))
            self.assertEqual(len(calls), 1)                    # 間隔内 → 再チェックしない

    def test_update_state_path_follows_shared_home(self):
        # 回帰: ここだけ旧ホーム ~/.agent を直書きしており、新ホームしか無い環境で
        # ~/.agent/ を新しく作って書いていた（control / budget / instructions と置き場が割れる）。
        os.environ.pop("KIRO_STATE_HOME", None)
        home = os.path.join(self.tmp, "home")
        with mock.patch.dict(os.environ, {"HOME": home}, clear=False):
            os.makedirs(os.path.join(home, ".agents"), exist_ok=True)
            self.assertEqual(kf._update_state_path(),
                             os.path.join(home, ".agents", "agent-flow.update.json"))
            # 旧ホームに既存の記録があればそちらを読む（ベースラインを失わない）
            old = os.path.join(home, ".agent")
            os.makedirs(old, exist_ok=True)
            open(os.path.join(old, "agent-flow.update.json"), "w").close()
            self.assertEqual(kf._update_state_path(),
                             os.path.join(old, "agent-flow.update.json"))
        os.environ["KIRO_STATE_HOME"] = self.state

    def test_registry_auto_resolution(self):
        # update_repo 未指定でも skill-registry.json から repo/branch を解決して検出できる
        regdir = os.path.join(self.tmp, "agenthome")
        os.makedirs(regdir, exist_ok=True)
        pathlib.Path(regdir, "skill-registry.json").write_text(json.dumps({
            "version": 7, "install_dir": self.tmp,
            "repositories": [{"name": "origin", "url": self.repo,
                              "branch": "main", "priority": 1}]}))
        old = os.environ.get("KIRO_SKILL_REGISTRY")
        os.environ["KIRO_SKILL_REGISTRY"] = regdir
        try:
            self.assertEqual(kf.registry_update_source()[0], self.repo)
            a = self._args(update_repo="")     # 明示なし → registry から解決
            info = kf.check_update(a)
            self.assertTrue(info["enabled"])
            self.assertEqual(info["repo"], self.repo)
        finally:
            if old is None:
                os.environ.pop("KIRO_SKILL_REGISTRY", None)
            else:
                os.environ["KIRO_SKILL_REGISTRY"] = old

    def test_explicit_repo_overrides_registry(self):
        a = self._args(update_repo="/explicit/path", update_branch="dev")
        self.assertEqual(kf.resolve_update_target(a), ("/explicit/path", "dev"))


class InheritRequestTests(unittest.TestCase):
    """リトライ引き継ぎ時の meta.request: 新世代の要求文（差し戻しの意図・run ブリーフ入り）を
    正とする。旧 request のコピーでは、リトライの引き金になった指摘が worker の全体文脈
    （run_request）と再実行ノードに届かない。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-inh-req-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _old_run(self):
        old = kf.Bus(self.tmp, "req-x-t-r0")
        old.ensure_run("最初の要求")
        old.write_graph({"strategy": {}, "iteration": 0,
                         "nodes": {"t1": {"goal": "g", "deps": []},
                                   "t2": {"goal": "g", "deps": []}}})
        old.write_task({"id": "t1", "goal": "g", "deps": []})
        old.write_result("t1", "w", "done", "ok")
        old.write_task({"id": "t2", "goal": "g", "deps": []})
        old.write_result("t2", "w", "failed", "boom")
        old.mark_run_failed("req-x-t-r0", "act failed")
        return old

    def test_seed_uses_new_request(self):
        self._old_run()
        new = kf.Bus(self.tmp, "req-x-t-r1")
        info = new.inherit_from("req-x-t-r0",
                                request="最初の要求\n\n人からのフィードバック: 命名規約に従うこと")
        self.assertTrue(info["inherited"])
        meta = kf.read_json(new.meta_path)
        self.assertIn("命名規約", meta["request"])       # 新世代の指摘が worker へ届く
        self.assertEqual(meta["inherited_from"], "req-x-t-r0")

    def test_seed_falls_back_to_old_request_when_empty(self):
        self._old_run()
        new = kf.Bus(self.tmp, "req-x-t-r1")
        new.inherit_from("req-x-t-r0")                   # request 未指定（後方互換）
        self.assertEqual(kf.read_json(new.meta_path)["request"], "最初の要求")


class NodeBudgetTests(unittest.TestCase):
    """ノード予算（node-budget 契約: schemas/node-budget.schema.json）の記帳・抑制。
    共有台帳（$AGENT_BUDGET_DIR）の合計が上限を超えたら run_agent が
    [agent-error:quota] タグ付きで即失敗し、環境要因として run が終端する。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kf-node-budget-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.environ["AGENT_BUDGET_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)

    def _config(self, minutes, period="day", workloads=None):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"execution_minutes": minutes, "period": period,
                       "workloads": workloads or {}}, f)

    def _ledger(self, records, day=None):
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        day = day or time.strftime("%Y%m%d", time.gmtime())
        with open(os.path.join(led, f"{day}.jsonl"), "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

    def test_no_config_is_unlimited(self):
        self.assertIsNone(kf._node_budget_state())

    def test_exceeded_raises_quota_before_cli(self):
        self._config(1)                                  # 上限 1 分
        self._ledger([{"workload": "amigos", "seconds": 40},
                      {"workload": "routine", "seconds": 30}])   # 他ワークロードとの合計で超過
        with self.assertRaises(RuntimeError) as ctx:
            kf.run_agent("x", None, purpose="worker")
        msg = str(ctx.exception)
        self.assertIn("[agent-error:quota]", msg)         # 環境要因として全層に運ばれる
        self.assertIn("[node-budget]", msg)               # quota（CLI 利用上限）と区別できる
        self.assertIsNotNone(kf.classify_agent_failure(msg))
        self.assertEqual(kf.classify_agent_failure(msg)[0], "quota")

    def test_workload_cap_applies_even_if_total_unlimited(self):
        self._config(0, workloads={"flow": 1})
        self._ledger([{"workload": "flow", "seconds": 61}])
        self.assertTrue(kf._node_budget_state()["exceeded"])
        self._ledger([{"workload": "routine", "seconds": 9999}])  # 他 WL は flow 内訳に無関係
        self.assertTrue(kf._node_budget_state()["exceeded"])

    def test_period_day_counts_only_today(self):
        self._config(1, period="day")
        self._ledger([{"workload": "flow", "seconds": 999}], day="19990101")
        self.assertFalse(kf._node_budget_state()["exceeded"])
        self._config(1, period="total")
        self.assertTrue(kf._node_budget_state()["exceeded"])

    def test_record_appends_ledger_line(self):
        kf._node_budget_record(1.5, ref="planner")
        led = os.path.join(self.dir, "ledger")
        files = [n for n in os.listdir(led) if n.endswith(".jsonl")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(led, files[0]), encoding="utf-8") as f:
            rec = json.loads(f.read().strip())
        self.assertEqual(rec["workload"], "flow")
        self.assertEqual(rec["tool"], "agent-flow")
        self.assertEqual(rec["seconds"], 1.5)
        self.assertEqual(rec["ref"], "planner")

    def test_run_agent_records_successful_execution(self):
        # CLI 実行を成功スタブ（実行時間あり）に差し替え、run_agent が台帳へ記帳することを確認する
        def slow_ok(*_a, **_k):
            time.sleep(0.01)
            return "ok"
        with mock.patch.object(kf, "_run_agent_once", side_effect=slow_ok):
            self.assertEqual(kf.run_agent("x", None, purpose="verify"), "ok")
        led = os.path.join(self.dir, "ledger")
        files = [n for n in os.listdir(led) if n.endswith(".jsonl")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(led, files[0]), encoding="utf-8") as f:
            rec = json.loads(f.read().strip())
        self.assertEqual(rec["workload"], "flow")
        self.assertEqual(rec["ref"], "verify")
        self.assertGreater(rec["seconds"], 0)


class NodeBudgetV2Tests(unittest.TestCase):
    """ノード予算 v2（トークン一次・rates 推定・配分 computed・soft/degrade）。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kf-nbv2-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.environ["AGENT_BUDGET_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)

    def _config(self, cfg):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _ledger(self, records, day=None):
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        day = day or time.strftime("%Y%m%d", time.gmtime())
        with open(os.path.join(led, f"{day}.jsonl"), "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

    def test_measured_tokens_used_directly(self):
        self._config({"version": 2, "tokens": 1000})
        self._ledger([{"workload": "flow", "seconds": 5, "tokens_in": 600, "tokens_out": 500}])
        st = kf._node_budget_state()
        self.assertEqual(st["spent_tokens"], 1100)
        self.assertTrue(st["exceeded"])                     # トークン上限で超過

    def test_estimated_tokens_from_rate_when_unreported(self):
        # トークン未報告行は seconds × rate（cli:model → cli → default）で推定
        self._config({"version": 2, "tokens": 1000,
                      "rates": {"default_tokens_per_second": 100,
                                "per_cli": {"kiro": 50, "claude:opus": 200}}})
        self._ledger([{"workload": "flow", "seconds": 10, "agent_cli": "kiro"},           # 500
                      {"workload": "flow", "seconds": 2, "agent_cli": "claude", "model": "opus"}])  # 400
        st = kf._node_budget_state()
        self.assertAlmostEqual(st["spent_tokens"], 900)
        self.assertFalse(st["exceeded"])
        self._ledger([{"workload": "routine", "seconds": 5}])  # default 100 → +500 = 1400
        self.assertTrue(kf._node_budget_state()["exceeded"])

    def test_computed_per_workload_cap(self):
        # computed.workloads.flow.tokens が自ワークロードの実効上限
        self._config({"version": 2, "tokens": 100000,
                      "computed": {"workloads": {"flow": {"tokens": 500}}}})
        self._ledger([{"workload": "flow", "seconds": 1, "tokens_in": 500, "tokens_out": 1}])
        self.assertTrue(kf._node_budget_state()["exceeded"])   # 全体は余裕でも自 WL 上限で超過

    def test_soft_ratio_triggers_degrade_flag(self):
        self._config({"version": 2, "tokens": 1000,
                      "allocation": {"soft_ratio": 0.8}})
        self._ledger([{"workload": "flow", "seconds": 1, "tokens_in": 850, "tokens_out": 0}])
        st = kf._node_budget_state()
        self.assertTrue(st["soft"])                           # 0.8 到達・未超過
        self.assertFalse(st["exceeded"])

    def test_on_exhausted_degrade_does_not_block_run(self):
        # on_exhausted=degrade は超過中も控えず、_agent_for が degraded 指定を適用する
        self._config({"version": 2, "tokens": 100,
                      "allocation": {"workloads": {"flow": {"on_exhausted": "degrade"}}}})
        self._ledger([{"workload": "flow", "seconds": 1, "tokens_in": 200, "tokens_out": 0}])
        _prev_control = os.environ["AGENT_CONTROL_DIR"]
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        # pop すると**モジュール既定の隔離先ごと消え**、以降のテストが開発者の実
        # `~/.agents/control` を読む（テスト順で agent_cli 系が落ちる原因だった）。
        self.addCleanup(os.environ.__setitem__, "AGENT_CONTROL_DIR", _prev_control)
        with open(os.path.join(self.dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "revision": 1,
                       "workloads": {"flow": {"degraded": {"model": "haiku"}}}}, f)
        with mock.patch.object(kf, "_run_agent_once", return_value="ok") as m:
            self.assertEqual(kf.run_agent("x", None, purpose="worker"), "ok")
        m.assert_called_once()                                # 控えず実行された
