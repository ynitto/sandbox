"""agent-amigos の単体テスト — bus（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class BoardParticipationTests(AmigosTestCase):
    """委譲公示板（agent-board）への参加（請負・入札）。板 = リポジトリ＋契約で処理を持たず、
    入札・引き渡し（＝オーナーとしてミッション公示）は amigos デーモンが担う。"""

    def _board_post(self, board, did, workload="amigos", **kw):
        d = os.path.join(board, "delegations", did)
        os.makedirs(d, exist_ok=True)
        rec = {"op": "post", "version": 1, "id": did, "workload": workload,
               "goal": "g", "title": "T",
               "engine": {"amigos": {"roles": [{"id": "architect", "required": True}]}}}
        rec.update(kw)
        with open(os.path.join(d, "post.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        return d

    def test_win_and_post_mission(self):
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-1", workspace={"url": "git@h:team/app.git"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        handed = B.poll_board(dm)
        self.assertEqual(handed, ["dg-1"])
        # 落札ノードがオーナーとしてミッションを公示した
        self.assertTrue(self.bus.mission("dg-1").exists())
        self.assertTrue(os.path.exists(os.path.join(d, "bids", "pc-a.json")))
        self.assertTrue(os.path.exists(os.path.join(d, "status", "pc-a.json")))

    def test_owner_picks_applies_without_dispatching(self):
        # owner-picks: award.json が無い間は応募（bid）を書くだけでミッション公示しない。
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-1", workspace={"url": "git@h:team/app.git"},
                             policy={"assignment": "owner-picks"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        handed = B.poll_board(dm)
        self.assertEqual(handed, [])
        self.assertFalse(self.bus.mission("dg-1").exists())
        self.assertTrue(os.path.exists(os.path.join(d, "bids", "pc-a.json")))
        self.assertFalse(os.path.exists(os.path.join(d, "status", "pc-a.json")))

    def test_owner_picks_dispatches_when_awarded_to_self(self):
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-1", workspace={"url": "git@h:team/app.git"},
                             policy={"assignment": "owner-picks"})
        with open(os.path.join(d, "award.json"), "w", encoding="utf-8") as f:
            json.dump({"node": "pc-a", "awarded_by": "requester"}, f)
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        handed = B.poll_board(dm)
        self.assertEqual(handed, ["dg-1"])
        self.assertTrue(self.bus.mission("dg-1").exists())
        self.assertTrue(os.path.exists(os.path.join(d, "status", "pc-a.json")))

    def test_owner_picks_skips_when_awarded_to_other_node(self):
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-1", workspace={"url": "git@h:team/app.git"},
                             policy={"assignment": "owner-picks"})
        with open(os.path.join(d, "award.json"), "w", encoding="utf-8") as f:
            json.dump({"node": "pc-b", "awarded_by": "requester"}, f)
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        self.assertEqual(B.poll_board(dm), [])
        self.assertFalse(self.bus.mission("dg-1").exists())

    def test_dispatched_lease_renewed_when_near_expiry(self):
        # 長時間ミッションが board_lease を超えても勝者を保つには、残りが半分未満のときに延長する。
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-1", workspace={"url": "git@h:team/app.git"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}},
                         board_lease=1000.0)
        B.poll_board(dm)   # 落札→ミッション公示
        mirror = B.BoardMirror(boarddir, "pc-a")
        bid_path = os.path.join(d, "bids", "pc-a.json")
        status_path = os.path.join(d, "status", "pc-a.json")
        orig_ts = json.load(open(bid_path, encoding="utf-8"))["ts"]
        bid = json.load(open(bid_path, encoding="utf-8"))
        bid["lease_until"] = time.time() + 10   # 残りわずか
        with open(bid_path, "w", encoding="utf-8") as f:
            json.dump(bid, f)
        B._renew_dispatched_leases(dm, mirror, 1000.0)
        renewed = json.load(open(bid_path, encoding="utf-8"))
        self.assertGreater(renewed["lease_until"], time.time() + 500)
        self.assertEqual(renewed["ts"], orig_ts)   # タイブレークの根拠 ts は温存する
        self.assertGreater(json.load(open(status_path, encoding="utf-8"))["lease_until"],
                           time.time() + 500)

    def test_dispatched_lease_not_renewed_when_still_fresh(self):
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-1", workspace={"url": "git@h:team/app.git"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}},
                         board_lease=1000.0)
        B.poll_board(dm)
        mirror = B.BoardMirror(boarddir, "pc-a")
        bid_path = os.path.join(d, "bids", "pc-a.json")
        before = json.load(open(bid_path, encoding="utf-8"))["lease_until"]
        B._renew_dispatched_leases(dm, mirror, 1000.0)
        after = json.load(open(bid_path, encoding="utf-8"))["lease_until"]
        self.assertEqual(before, after)

    def test_report_results_writes_result_on_terminal_mission(self):
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-1", workspace={"url": "git@h:team/app.git"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        B.poll_board(dm)   # 落札→ミッション公示
        mirror = B.BoardMirror(boarddir, "pc-a")
        # 実行中はまだ result.json を書かない
        self.assertEqual(B.report_board_results(dm, mirror), [])
        self.assertFalse(os.path.exists(os.path.join(d, "result.json")))
        # ミッションが done に達したら報告する
        mp = self.bus.mission("dg-1")
        write_json_atomic(mp.final(), {"accepted": True})
        reported = B.report_board_results(dm, mirror)
        self.assertEqual(reported, ["dg-1"])
        res = json.load(open(os.path.join(d, "result.json"), encoding="utf-8"))
        self.assertEqual(res["status"], "done")
        self.assertEqual(res["winner"], "pc-a")
        # 冪等: 二重報告しない
        self.assertEqual(B.report_board_results(dm, mirror), [])

    def test_report_results_cancelled_mission(self):
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-2", workspace={"url": "git@h:team/app.git"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        B.poll_board(dm)
        mirror = B.BoardMirror(boarddir, "pc-a")
        mp = self.bus.mission("dg-2")
        write_json_atomic(mp.cancelled(), {"ts": "2026-01-01T00:00:00Z"})
        reported = B.report_board_results(dm, mirror)
        self.assertEqual(reported, ["dg-2"])
        res = json.load(open(os.path.join(d, "result.json"), encoding="utf-8"))
        self.assertEqual(res["status"], "cancelled")

    def test_report_results_includes_latest_reject_guidance(self):
        # 実装計画 W1-9: submit の結果読み戻し（reject 時のガイダンス）と等価にするため、
        # 板 result.json に直近の差し戻しフィードバックを reject_guidance として載せる。
        # 未受入のまま終端（ここでは cancelled）＝やり直し指示が意味を持つケース。
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-3", workspace={"url": "git@h:team/app.git"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        B.poll_board(dm)
        mirror = B.BoardMirror(boarddir, "pc-a")
        mp = self.bus.mission("dg-3")
        reject_mission(self.bus, mp, "テストを追加してください", "owner")
        write_json_atomic(mp.cancelled(), {"cancelled_at": "2026-07-25T00:00:00Z",
                                           "reason": "打ち切り"})
        reported = B.report_board_results(dm, mirror)
        self.assertEqual(reported, ["dg-3"])
        res = json.load(open(os.path.join(d, "result.json"), encoding="utf-8"))
        self.assertEqual(res["reject_guidance"], "テストを追加してください")

    def test_report_results_omits_reject_guidance_when_accepted(self):
        # rejections/ は round ごとの履歴なので、round 0 で差し戻し → round 1 で accept という
        # ミッションでも最新 feedback が残る。契約上 reject_guidance は「却下時のやり直し指示」
        # （board.schema.json）なので、受入済み（done）の成果に載せると消費側が
        # 「やり直しが要る」と読み違える。
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-5", workspace={"url": "git@h:team/app.git"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        B.poll_board(dm)
        mirror = B.BoardMirror(boarddir, "pc-a")
        mp = self.bus.mission("dg-5")
        reject_mission(self.bus, mp, "テストを追加してください", "owner")
        write_json_atomic(mp.final(), {"accepted": True})
        self.assertEqual(B.report_board_results(dm, mirror), ["dg-5"])
        res = json.load(open(os.path.join(d, "result.json"), encoding="utf-8"))
        self.assertEqual(res["status"], "done")
        self.assertNotIn("reject_guidance", res)

    def test_report_results_omits_reject_guidance_when_never_rejected(self):
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        d = self._board_post(boarddir, "dg-4", workspace={"url": "git@h:team/app.git"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        B.poll_board(dm)
        mirror = B.BoardMirror(boarddir, "pc-a")
        mp = self.bus.mission("dg-4")
        write_json_atomic(mp.final(), {"accepted": True})
        B.report_board_results(dm, mirror)
        res = json.load(open(os.path.join(d, "result.json"), encoding="utf-8"))
        self.assertNotIn("reject_guidance", res)

    def test_ineligible_repo_and_wrong_workload(self):
        from agent_amigos import board as B
        boarddir = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(boarddir, "delegations"), exist_ok=True)
        self._board_post(boarddir, "dg-other", workspace={"url": "git@h:team/other.git"})
        self._board_post(boarddir, "dg-flow", workload="flow",
                         workspace={"url": "git@h:team/app.git"})
        dm = self.daemon(node="pc-a", commands_home=self.tmp, board=boarddir,
                         repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        self.assertEqual(B.poll_board(dm), [])   # 担当外・flow は対象外
        self.assertFalse(self.bus.mission("dg-other").exists())

    def test_no_board_is_noop(self):
        from agent_amigos import board as B
        dm = self.daemon(node="pc-a", commands_home=self.tmp)
        self.assertEqual(B.poll_board(dm), [])


@unittest.skipUnless(shutil.which("git"), "git が必要")
class GitBusTests(unittest.TestCase):
    """GitBus（P1、設計書 §4.4）: 専用バスリポジトリ + ミッション別ブランチ。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amigos-git-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = os.path.join(self.tmp, "amigos-bus.git")
        subprocess.run(["git", "init", "--bare", "--quiet", self.origin], check=True)
        self.url = f"git+file://{self.origin}"
        os.environ["AGENT_AMIGOS_PULL_INTERVAL"] = "0"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_PULL_INTERVAL", None)
        os.environ["AGENT_AMIGOS_STUB_COST"] = "0.01"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_STUB_COST", None)
        os.environ["AGENT_BUDGET_DIR"] = os.path.join(self.tmp, "node-budget")
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)
        self.design = os.path.join(self.tmp, "design.md")
        with open(self.design, "w", encoding="utf-8") as f:
            f.write("# design\n")
        self.roles_path = os.path.join(self.tmp, "roles.json")
        with open(self.roles_path, "w", encoding="utf-8") as f:
            json.dump({"mission": {"title": "git", "goal": "g",
                                   "staffing_timeout": 9999,
                                   "convergence": {"done_when": "all-required-done"}},
                       "roles": [
                           {"id": "architect", "mission": "a",
                            "deliverables": ["arch.md"]},
                           {"id": "impl", "mission": "b", "deliverables": ["main.py"],
                            "collaborates_with": ["architect"]}]}, f)

    def make(self, node):
        from agent_amigos.bus import make_bus
        return make_bus(self.url, workdir=os.path.join(self.tmp, f"wd-{node}"))

    def _origin_branches(self):
        out = subprocess.run(["git", "--git-dir", self.origin, "branch",
                              "--format=%(refname:short)"],
                             capture_output=True, text=True).stdout.split()
        return sorted(out)

    def test_distributed_two_nodes_e2e(self):
        bus_a, bus_b = self.make("a"), self.make("b")
        mid = post_mission(bus_a, self.design, self.roles_path, "node-a", "am-git")
        self.assertIn("mission/am-git", self._origin_branches())
        a = NodeDaemon(bus_a, "node-a", agent_cli="stub", interval=0,
                       roles_filter=["architect"])
        b = NodeDaemon(bus_b, "node-b", agent_cli="stub", interval=0,
                       roles_filter=["impl"])
        b.cycle()                                   # worker が先に impl を claim
        phase = None
        for _ in range(12):
            a.cycle()
            b.cycle()
            mp = bus_a.mission(mid)
            phase = derive_phase(load_mission(mp), load_roles(mp), mp)
            if phase == "reviewing":
                break
        self.assertEqual(phase, "reviewing")
        mp = bus_a.mission(mid)
        roster = read_json(mp.roster())
        self.assertEqual(roster["impl"]["node"], "node-b")
        self.assertEqual(roster["architect"]["node"], "node-a")
        manifest = read_json(mp.manifest())
        self.assertFalse(manifest["partial"])
        # 第三のノード（viewer）が clone だけで全状態を読める
        bus_c = self.make("c")
        self.assertEqual(bus_c.list_missions(), ["am-git"])
        mp_c = bus_c.mission(mid)
        self.assertTrue(read_json(mp_c.manifest()))
        # メッセージ往復も伝播している（impl の質問が architect の inbox に）
        self.assertTrue(any(m["type"] == "question"
                            for m in read_inbox(mp_c, "architect")))

    def test_claim_race_across_git(self):
        bus_a, bus_b = self.make("a"), self.make("b")
        mid = post_mission(bus_a, self.design, self.roles_path, "node-a", "am-race")
        mp_a, mp_b = bus_a.mission(mid), bus_b.mission(mid)
        self.assertTrue(claim_role(bus_a, mp_a, "impl", "node-a"))
        self.assertFalse(claim_role(bus_b, mp_b, "impl", "node-b"))
        self.assertEqual(winner(mp_a, "impl"), "node-a")
        self.assertEqual(winner(mp_b, "impl"), "node-a")   # 全ノードが同じ勝者を導く

    def test_turn_is_single_commit(self):
        bus_a = self.make("a")
        mid = post_mission(bus_a, self.design, self.roles_path, "node-a", "am-atomic")
        mp = bus_a.mission(mid)
        claim_role(bus_a, mp, "architect", "node-a")
        write_json_atomic(mp.roster(), {"architect": {"node": "node-a"}})
        bus_a.sync_push("roster")

        def count():
            out = subprocess.run(["git", "--git-dir", self.origin, "rev-list",
                                  "--count", "mission/am-atomic"],
                                 capture_output=True, text=True).stdout.strip()
            return int(out or 0)

        before = count()
        runner = AmigoRunner(bus_a, mid, "architect", "node-a", "stub")
        self.assertEqual(runner.turn_once(), "acted")
        # 1 ターン（成果物 + status + events）= origin 上の 1 コミット（原子性 §5.3）
        self.assertEqual(count(), before + 1)

    def test_gc_removes_branch_and_index(self):
        bus_a = self.make("a")
        mid = post_mission(bus_a, self.design, self.roles_path, "node-a", "am-gc")
        mp = bus_a.mission(mid)
        write_json_atomic(mp.cancelled(), {"ts": "2026-01-01T00:00:00Z"})
        bus_a.sync_push("cancel")
        bus_a.remove_mission(mid)
        self.assertNotIn("mission/am-gc", self._origin_branches())
        bus_c = self.make("c")
        self.assertEqual(bus_c.list_missions(), [])


@unittest.skipUnless(shutil.which("git"), "git が必要")
class BoardMirrorGitTests(unittest.TestCase):
    """BoardMirror の git+<url> モード（agentcore.transport 移植後）: 2 ノード間の
    post/bid の往復が実クローンで成立し、tie-break が全ノードで同じ勝者に収束すること。"""

    def setUp(self):
        from agent_amigos import board as B
        self.B = B
        self.tmp = tempfile.mkdtemp(prefix="amigos-board-git-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = os.path.join(self.tmp, "board.git")
        subprocess.run(["git", "init", "--bare", "--quiet", "-b", "main", self.origin],
                       check=True)
        self.url = f"git+{self.origin}"

    def mirror(self, node):
        return self.B.BoardMirror(self.url, node,
                                  workdir=os.path.join(self.tmp, f"wd-{node}"))

    def test_write_post_roundtrips_across_nodes(self):
        a = self.mirror("pc-a")
        env = {"op": "post", "version": 1, "id": "dg-1", "workload": "amigos", "goal": "g"}
        d = os.path.join(a.dir, "delegations", "dg-1")
        os.makedirs(d, exist_ok=True)
        write_json_atomic(os.path.join(d, "post.json"), env)
        a.sync_push("post dg-1")

        b = self.mirror("pc-b")
        b.sync_pull()
        got = read_json(os.path.join(b.dir, "delegations", "dg-1", "post.json"))
        self.assertEqual(got, env)

    def test_bid_tiebreak_converges_across_nodes(self):
        a, b = self.mirror("pc-a"), self.mirror("pc-b")
        bids_dir_a = os.path.join(a.dir, "delegations", "dg-1", "bids")
        bids_dir_b = os.path.join(b.dir, "delegations", "dg-1", "bids")
        self.assertTrue(self.B._try_bid(a, bids_dir_a, "dg-1", "pc-a", 900))
        b.sync_pull()
        self.assertFalse(self.B._try_bid(b, bids_dir_b, "dg-1", "pc-b", 900))
        # 両ノードから見て同じ勝者に収束する
        a.sync_pull()
        self.assertEqual(self.B._winner(bids_dir_a), "pc-a")
        self.assertEqual(self.B._winner(bids_dir_b), "pc-a")

    def test_recovers_stale_lock_on_reused_clone(self):
        a = self.mirror("pc-a")
        with open(os.path.join(a.dir, "keep.txt"), "w") as f:
            f.write("keep\n")
        a.sync_push("seed")
        lock = os.path.join(a.dir, ".git", "index.lock")
        with open(lock, "w") as f:
            f.write("stale")
        old = time.time() - 3600
        os.utime(lock, (old, old))

        a2 = self.mirror("pc-a")  # 同じ workdir を再利用 → 残骸ロックを自己回復する
        with open(os.path.join(a2.dir, "more.txt"), "w") as f:
            f.write("more\n")
        a2.sync_push("more")  # 残骸ロックのせいで失敗しないこと
        self.assertFalse(os.path.exists(lock))
