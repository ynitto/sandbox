"""agent-flow の単体テスト — board（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class DelegationProvenanceTests(unittest.TestCase):
    """委譲公示板（agent-board）由来の来歴を inbox 要求 → run meta へ引き回す（additive）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-test-deleg-")
        self.bus = kf.Bus(self.tmp, "run-d")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_submit_request_carries_delegation(self):
        self.bus.submit_request("run-d", "do it", "agent-board:pc-a",
                                delegation={"id": "dg-1", "board": True})
        rec = self.bus.read_inbox("run-d")
        self.assertEqual(rec["delegation"], {"id": "dg-1", "board": True})

    def test_submit_request_omits_empty_delegation(self):
        self.bus.submit_request("run-d", "do it", "s", delegation=None)
        self.assertNotIn("delegation", self.bus.read_inbox("run-d"))
        self.bus.submit_request("run-d", "do it", "s", delegation={"board": True})  # id 無し
        self.assertNotIn("delegation", self.bus.read_inbox("run-d"))

    def test_note_delegation_writes_meta(self):
        self.bus.ensure_run("do it")
        self.bus.note_delegation({"id": "dg-1", "board": True})
        self.assertEqual(self.bus.run_meta("run-d")["delegation"], {"id": "dg-1", "board": True})
        # id 無し / 非 dict は無視（additive・安全側）
        self.bus.note_delegation(None)
        self.bus.note_delegation({"board": True})
        self.assertEqual(self.bus.run_meta("run-d")["delegation"], {"id": "dg-1", "board": True})


class BoardParticipationTests(unittest.TestCase):
    """委譲公示板（agent-board）への参加（請負・入札）。板 = リポジトリ＋契約で処理を持たず、
    入札・引き渡しは flow デーモンが担う。ローカル板ディレクトリでプロトコルを stub 検証する。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-board-")
        self.local = os.path.join(self.tmp, "localbus")
        self.board = os.path.join(self.tmp, "board")
        self.bus = kf.Bus(self.local, "_")
        os.makedirs(os.path.join(self.board, "delegations"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, did, **kw):
        d = os.path.join(self.board, "delegations", did)
        os.makedirs(d, exist_ok=True)
        rec = {"op": "post", "version": 1, "id": did, "workload": "flow", "goal": "実装", **kw}
        with open(os.path.join(d, "post.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f)
        return d

    def _args(self, **kw):
        base = dict(board=self.board, board_workdir=None, board_branch="main",
                    board_repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}},
                    board_tags=[], board_lease=900.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_eligible_by_workspace_repo(self):
        repos = {"app": {"url": "git@h:team/app.git", "owns": ["**"]}}
        self.assertTrue(kf.board_eligible(
            {"workspace": {"url": "git@h:team/app.git"}}, repos, []))
        # 担当外リポジトリの公示には入札しない
        self.assertFalse(kf.board_eligible(
            {"workspace": {"url": "git@h:team/other.git"}}, repos, []))
        # requires.tags 不足は不可
        self.assertFalse(kf.board_eligible({"requires": {"tags": ["python"]}}, repos, []))

    def test_win_and_handoff_to_inbox(self):
        d = self._post("dg-1", workspace={"url": "git@h:team/app.git", "base": "main"})
        handed = kf.poll_board(self.bus, self._args(), "pc-a")
        self.assertEqual(handed, ["dg-1"])
        # local inbox へ取り込まれ、来歴が付く
        req = self.bus.read_inbox("dg-1")
        self.assertIsNotNone(req)
        self.assertEqual(req["delegation"], {"id": "dg-1", "board": True})
        self.assertEqual(req["workspace"], {"url": "git@h:team/app.git", "base": "main"})
        # 板に入札と状態が残る
        self.assertTrue(os.path.exists(os.path.join(d, "bids", "pc-a.json")))
        self.assertTrue(os.path.exists(os.path.join(d, "status", "pc-a.json")))

    def test_ineligible_repo_no_bid(self):
        self._post("dg-2", workspace={"url": "git@h:team/other.git"})
        handed = kf.poll_board(self.bus, self._args(), "pc-a")
        self.assertEqual(handed, [])
        self.assertIsNone(self.bus.read_inbox("dg-2"))

    def test_skip_amigos_workload_and_terminal(self):
        self._post("dg-a", workload="amigos", workspace={"url": "git@h:team/app.git"})
        d = self._post("dg-done", workspace={"url": "git@h:team/app.git"})
        with open(os.path.join(d, "result.json"), "w") as f:
            json.dump({"winner": "x"}, f)
        handed = kf.poll_board(self.bus, self._args(), "pc-a")
        self.assertEqual(handed, [])   # amigos は対象外・result 済みは触らない

    def test_no_board_is_noop(self):
        self.assertEqual(kf.poll_board(self.bus, self._args(board=None), "pc-a"), [])

    def test_already_taken_not_rehanded(self):
        self._post("dg-1", workspace={"url": "git@h:team/app.git"})
        kf.poll_board(self.bus, self._args(), "pc-a")
        # 2 巡目: 既に inbox にあるので再取り込みしない
        self.assertEqual(kf.poll_board(self.bus, self._args(), "pc-a"), [])

    def test_owner_picks_applies_without_dispatching(self):
        # owner-picks: award.json が無い間は応募（bid）を書くだけで取り込まない。
        d = self._post("dg-1", workspace={"url": "git@h:team/app.git"},
                       policy={"assignment": "owner-picks"})
        handed = kf.poll_board(self.bus, self._args(), "pc-a")
        self.assertEqual(handed, [])
        self.assertIsNone(self.bus.read_inbox("dg-1"))
        self.assertTrue(os.path.exists(os.path.join(d, "bids", "pc-a.json")))
        self.assertFalse(os.path.exists(os.path.join(d, "status", "pc-a.json")))

    def test_owner_picks_dispatches_when_awarded_to_self(self):
        d = self._post("dg-1", workspace={"url": "git@h:team/app.git"},
                       policy={"assignment": "owner-picks"})
        with open(os.path.join(d, "award.json"), "w", encoding="utf-8") as f:
            json.dump({"node": "pc-a", "awarded_by": "requester"}, f)
        handed = kf.poll_board(self.bus, self._args(), "pc-a")
        self.assertEqual(handed, ["dg-1"])
        self.assertIsNotNone(self.bus.read_inbox("dg-1"))
        self.assertTrue(os.path.exists(os.path.join(d, "status", "pc-a.json")))

    def test_owner_picks_skips_when_awarded_to_other_node(self):
        d = self._post("dg-1", workspace={"url": "git@h:team/app.git"},
                       policy={"assignment": "owner-picks"})
        with open(os.path.join(d, "award.json"), "w", encoding="utf-8") as f:
            json.dump({"node": "pc-b", "awarded_by": "requester"}, f)
        handed = kf.poll_board(self.bus, self._args(), "pc-a")
        self.assertEqual(handed, [])
        self.assertIsNone(self.bus.read_inbox("dg-1"))

    def test_owner_picks_multiple_nodes_can_apply_without_evicting(self):
        # first-come と違い、応募段階では他ノードの bid を奪わない（両方の応募が残る）。
        d = self._post("dg-1", workspace={"url": "git@h:team/app.git"},
                       policy={"assignment": "owner-picks"})
        kf.poll_board(self.bus, self._args(), "pc-a")
        bus_b = kf.Bus(os.path.join(self.tmp, "localbus-b"), "_")
        kf.poll_board(bus_b, self._args(), "pc-b")
        self.assertTrue(os.path.exists(os.path.join(d, "bids", "pc-a.json")))
        self.assertTrue(os.path.exists(os.path.join(d, "bids", "pc-b.json")))

    def test_dispatched_lease_renewed_when_near_expiry(self):
        # 長時間 run が board_lease を超えても勝者を保つには、残りが半分未満のときに延長する。
        d = self._post("dg-1", workspace={"url": "git@h:team/app.git"})
        args = self._args(board_lease=1000.0)
        kf.poll_board(self.bus, args, "pc-a")   # 落札→dispatch
        board = kf._board_bus(self.board, "pc-a", args)
        bid_path = os.path.join(d, "bids", "pc-a.json")
        status_path = os.path.join(d, "status", "pc-a.json")
        orig_ts = json.load(open(bid_path))["ts"]
        bid = json.load(open(bid_path))
        bid["lease_until"] = time.time() + 10   # 残りわずか（lease=1000 の半分よりずっと少ない）
        with open(bid_path, "w") as f:
            json.dump(bid, f)
        kf._renew_dispatched_leases(board, "pc-a", 1000.0)
        renewed = json.load(open(bid_path))
        self.assertGreater(renewed["lease_until"], time.time() + 500)
        self.assertEqual(renewed["ts"], orig_ts)   # タイブレークの根拠 ts は温存する
        self.assertGreater(json.load(open(status_path))["lease_until"], time.time() + 500)

    def test_dispatched_lease_not_renewed_when_still_fresh(self):
        # まだ十分残っているうちは書き換えない（board への無駄な push/commit を避ける）。
        d = self._post("dg-1", workspace={"url": "git@h:team/app.git"})
        args = self._args(board_lease=1000.0)
        kf.poll_board(self.bus, args, "pc-a")
        board = kf._board_bus(self.board, "pc-a", args)
        bid_path = os.path.join(d, "bids", "pc-a.json")
        before = json.load(open(bid_path))["lease_until"]
        kf._renew_dispatched_leases(board, "pc-a", 1000.0)
        after = json.load(open(bid_path))["lease_until"]
        self.assertEqual(before, after)

    def test_lease_renewal_keeps_winner_across_dispatcher_polls(self):
        # 実運用に近い経路: poll_board 自体が毎巡 _renew_dispatched_leases を呼ぶので、
        # 短い lease でも自分が生きている限り再入札を経ずに勝者であり続ける。
        d = self._post("dg-1", workspace={"url": "git@h:team/app.git"})
        args = self._args(board_lease=20.0)
        kf.poll_board(self.bus, args, "pc-a")
        bid_path = os.path.join(d, "bids", "pc-a.json")
        bid = json.load(open(bid_path))
        bid["lease_until"] = time.time() + 1   # ほぼ失効寸前まで経過したことにする
        with open(bid_path, "w") as f:
            json.dump(bid, f)
        # 次の poll（自分自身）: dg-1 は既に inbox にあるので再取り込みはしないが、延長は起こる
        kf.poll_board(self.bus, args, "pc-a")
        self.assertGreater(json.load(open(bid_path))["lease_until"], time.time() + 15)
        # 延長が効いているので、この時点で他ノードは勝者になれない
        board_b = kf._board_bus(self.board, "pc-b", self._args(board_lease=20.0))
        self.assertEqual(board_b._winner_in(os.path.join(d, "bids")), "pc-a")

    def test_report_results_writes_result_on_terminal_run(self):
        d = self._post("dg-1", workspace={"url": "git@h:team/app.git"})
        args = self._args()
        kf.poll_board(self.bus, args, "pc-a")   # 落札→取り込み
        board = kf._board_bus(self.board, "pc-a", args)
        # 実行中はまだ result.json を書かない
        self.assertEqual(kf.report_board_results(self.bus, board, "pc-a"), [])
        self.assertFalse(os.path.exists(os.path.join(d, "result.json")))
        # run が done に達したら報告する
        self.bus.run_view("dg-1").set_status("done")
        reported = kf.report_board_results(self.bus, board, "pc-a")
        self.assertEqual(reported, ["dg-1"])
        res = json.load(open(os.path.join(d, "result.json")))
        self.assertEqual(res["status"], "done")
        self.assertEqual(res["winner"], "pc-a")
        # 冪等: 二重報告しない
        self.assertEqual(kf.report_board_results(self.bus, board, "pc-a"), [])

    def test_report_results_includes_notes_discoveries_and_reject_guidance(self):
        # 実装計画 W1-9: submit の結果読み戻し（reject 時のガイダンス・発見事項）と等価にする
        # ため、板 result.json に result_notes / discoveries / reject_guidance を載せる。
        self._post("dg-3", workspace={"url": "git@h:team/app.git"})
        args = self._args()
        kf.poll_board(self.bus, args, "pc-a")
        board = kf._board_bus(self.board, "pc-a", args)
        run = self.bus.run_view("dg-3")
        run.write_graph({"nodes": {"sink": {"kind": "synthesize", "deps": []}}})
        run.write_result("sink", "pc-a", "done", "output text", data={
            "decision": "rejected", "guidance": "テストを追加してください",
            "notes": [{"note_id": "n1", "body": "レビューコメント"}],
            "constraints": [{"text": "DB マイグレーションは要レビュー"}, "第二の発見"],
        })
        run.set_status("done")
        reported = kf.report_board_results(self.bus, board, "pc-a")
        self.assertEqual(reported, ["dg-3"])
        res = json.load(open(os.path.join(self.board, "delegations", "dg-3", "result.json")))
        self.assertEqual(res["reject_guidance"], "テストを追加してください")
        self.assertEqual(res["result_notes"], [{"note_id": "n1", "body": "レビューコメント"}])
        self.assertEqual(res["discoveries"],
                         ["DB マイグレーションは要レビュー", "第二の発見"])

    def test_reject_guidance_falls_back_to_output_marker(self):
        # gitlab executor は「イシュー削除」「人コメント無しの差し戻し」で
        # decision=rejected かつ guidance="" を書き、やり直し指示は output の
        # [gitlab-reject] 以降にしか無い。構造化だけを見ると submit 経路
        # （read_reject_guidance の第 2 パス）と非等価になる。
        self._post("dg-5", workspace={"url": "git@h:team/app.git"})
        args = self._args()
        kf.poll_board(self.bus, args, "pc-a")
        board = kf._board_bus(self.board, "pc-a", args)
        run = self.bus.run_view("dg-5")
        run.write_graph({"nodes": {"sink": {"kind": "synthesize", "deps": []}}})
        # ノード status は実運用どおり failed（却下は park の決着として failed になる）。
        run.write_result("sink", "pc-a", "failed",
                         "[gitlab-reject] 却下されました（イシューが削除された＝取り下げ）。"
                         "人コメントは読めないため自動で原因を判断してやり直してください。",
                         data={"decision": "rejected", "guidance": ""})
        run.set_status("failed")
        self.assertEqual(kf.report_board_results(self.bus, board, "pc-a"), ["dg-5"])
        res = json.load(open(os.path.join(self.board, "delegations", "dg-5", "result.json")))
        self.assertTrue(res["reject_guidance"].startswith("却下されました"))
        self.assertNotIn("[gitlab-reject]", res["reject_guidance"])

    def test_report_results_omits_extras_when_nothing_to_report(self):
        # 何も無ければ result_notes/discoveries/reject_guidance を空で埋めない（省略）。
        self._post("dg-4", workspace={"url": "git@h:team/app.git"})
        args = self._args()
        kf.poll_board(self.bus, args, "pc-a")
        board = kf._board_bus(self.board, "pc-a", args)
        self.bus.run_view("dg-4").set_status("done")
        kf.report_board_results(self.bus, board, "pc-a")
        res = json.load(open(os.path.join(self.board, "delegations", "dg-4", "result.json")))
        self.assertNotIn("reject_guidance", res)
        self.assertNotIn("result_notes", res)
        self.assertNotIn("discoveries", res)

    def test_report_results_reflects_cancelled_status(self):
        # 語彙統一（W0-9）後は flow の終端語彙も板と同じ cancelled（英式）——翻訳不要で素通り。
        self._post("dg-2", workspace={"url": "git@h:team/app.git"})
        args = self._args()
        kf.poll_board(self.bus, args, "pc-a")
        board = kf._board_bus(self.board, "pc-a", args)
        self.bus.run_view("dg-2").set_status("cancelled")
        reported = kf.report_board_results(self.bus, board, "pc-a")
        self.assertEqual(reported, ["dg-2"])
        d = os.path.join(self.board, "delegations", "dg-2")
        res = json.load(open(os.path.join(d, "result.json")))
        self.assertEqual(res["status"], "cancelled")


class BoardLocalCloneTests(unittest.TestCase):
    """S3: 板の請負側が公示 workspace に **自ノードの** ローカルクローンを載せる。

    公示に載るのは依頼側が見た URL だけで、依頼側の local は請負ノードに存在しないので
    正しく落とされている。だが請負側が自分の local を載せる実装が無かったため、板経由の仕事は
    手元に同じリポジトリがあっても毎回ネットワーク越しにミラーを取り直していた
    （C3「flow/amigos の git clone のリモート負荷」そのもの）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-board-local-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.board = os.path.join(self.tmp, "board")
        os.makedirs(os.path.join(self.board, "delegations"), exist_ok=True)
        self.bus = kf.Bus(os.path.join(self.tmp, "bus"), "run-bl")
        self.clone = os.path.join(self.tmp, "mirrors", "app")
        os.makedirs(self.clone, exist_ok=True)

    def _use_host(self, repos):
        home = os.path.join(self.tmp, "agents-home")
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, "agent-project.host.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "repos": repos}, f)
        old = os.environ.get("AGENT_PROJECT_AGENTS_HOME")
        os.environ["AGENT_PROJECT_AGENTS_HOME"] = home
        self.addCleanup(lambda: os.environ.__setitem__("AGENT_PROJECT_AGENTS_HOME", old)
                        if old is not None else os.environ.pop("AGENT_PROJECT_AGENTS_HOME", None))

    def _post(self, did, **kw):
        d = os.path.join(self.board, "delegations", did)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "post.json"), "w", encoding="utf-8") as f:
            json.dump({"op": "post", "version": 1, "id": did, "workload": "flow",
                       "goal": "実装", **kw}, f)
        return d

    def _args(self):
        return types.SimpleNamespace(
            board=self.board, board_workdir=None, board_branch="main",
            board_repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}},
            board_tags=[], board_lease=900.0)

    def test_won_delegation_carries_this_nodes_local_clone(self):
        self._use_host([{"url": "git@h:team/app.git", "local": self.clone}])
        self._post("dg-l1", workspace={"url": "git@h:team/app.git", "base": "main"})
        self.assertEqual(kf.poll_board(self.bus, self._args(), "pc-a"), ["dg-l1"])
        ws = self.bus.read_inbox("dg-l1")["workspace"]
        self.assertEqual(ws["local"], self.clone, "手元のクローンから worktree を切れる")
        self.assertEqual(ws["base"], "main", "公示の内容は保つ")

    def test_undeclared_repo_leaves_workspace_untouched(self):
        self._use_host([{"url": "git@h:team/other.git", "local": self.clone}])
        self._post("dg-l2", workspace={"url": "git@h:team/app.git", "base": "main"})
        self.assertEqual(kf.poll_board(self.bus, self._args(), "pc-a"), ["dg-l2"])
        ws = self.bus.read_inbox("dg-l2")["workspace"]
        self.assertNotIn("local", ws, "宣言が無ければ従来どおりミラーから取る")

    def test_eligibility_is_unchanged_by_url_normalisation(self):
        # 正規化を agentcore へ統一しても入札判定は変わらない（末尾 .git・大小文字の吸収）。
        repos = {"app": {"url": "git@h:team/app.git", "owns": ["**"]}}
        self.assertTrue(kf.board_eligible({"workspace": {"url": "git@h:team/app"}}, repos, []))
        self.assertTrue(kf.board_eligible({"workspace": {"url": "git@H:team/App.git"}}, repos, []))
        self.assertFalse(kf.board_eligible({"workspace": {"url": "git@h:team/apps.git"}}, repos, []))
