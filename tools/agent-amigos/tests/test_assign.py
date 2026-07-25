"""agent-amigos の単体テスト — assign（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class ClaimTests(AmigosTestCase):
    def test_deterministic_single_winner(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        ok_a = claim_role(self.bus, mp, "impl", "node-a")
        ok_b = claim_role(self.bus, mp, "impl", "node-b")
        self.assertTrue(ok_a)
        self.assertFalse(ok_b)          # 先着 claim が (ts, node) 最小 → 勝者は 1 人
        self.assertEqual(winner(mp, "impl"), "node-a")

    def test_tiebreak_is_derived_identically_from_files(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        # 同時 claim をファイル直書きで再現（ts 同値 → node 昇順で決定的）
        for node in ("node-z", "node-b", "node-m"):
            write_json_atomic(mp.assignment("impl", node),
                              {"node": node, "ts": 100.0, "lease_until": time.time() + 60})
        self.assertEqual(winner(mp, "impl"), "node-b")

    def test_lease_expiry_reopens_role(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        write_json_atomic(mp.assignment("impl", "node-a"),
                          {"node": "node-a", "ts": 1.0, "lease_until": time.time() - 1})
        self.assertIsNone(winner(mp, "impl"))       # 孤児 claim は無視
        roles = load_roles(mp)
        write_json_atomic(mp.roster(), {"impl": {"node": "node-a"}})
        roster = mirror_roster(self.bus, mp, roles, "owner-node")
        self.assertNotIn("impl", roster)            # roster からも外れ、再募集に戻る
        self.assertTrue(claim_role(self.bus, mp, "impl", "node-b"))

    def test_heartbeat_does_not_resurrect_a_removed_claim(self):
        """剪定・取り下げ・オーナーの再編で claim が消えたあと、走っているランナーの
        心拍がそれを書き戻すと、誰も動いていないロールを占有し続ける zombie 勝者になる。"""
        from agent_amigos.assign import renew_lease
        mid = self.post()
        mp = self.bus.mission(mid)
        self.assertTrue(claim_role(self.bus, mp, "impl", "node-a"))
        os.remove(mp.assignment("impl", "node-a"))      # claim が消えた
        renew_lease(mp, "impl", "node-a")
        self.assertFalse(os.path.exists(mp.assignment("impl", "node-a")))
        self.assertIsNone(winner(mp, "impl"))           # 再募集に戻ったまま

    def test_heartbeat_extends_own_claim_and_keeps_ts(self):
        from agent_amigos.assign import renew_lease
        mid = self.post()
        mp = self.bus.mission(mid)
        write_json_atomic(mp.assignment("impl", "node-a"),
                          {"who": "node-a", "node": "node-a", "ts": 42.0,
                           "agent_cli": "claude", "lease_until": time.time() + 1})
        renew_lease(mp, "impl", "node-a", lease=600.0)
        rec = read_json(mp.assignment("impl", "node-a"))
        self.assertEqual(rec["ts"], 42.0)               # 先勝ちの根拠は動かさない
        self.assertEqual(rec["agent_cli"], "claude")    # 既存フィールドを引き継ぐ
        self.assertGreater(rec["lease_until"], time.time() + 500)
        self.assertEqual(winner(mp, "impl"), "node-a")


class AwayProtocolTests(AmigosTestCase):
    """away プロトコル（P1、設計書 §6.6）: 計画停止ではロールを奪わない。"""

    def _stage_away(self, mid, resume_at_epoch):
        mp = self.bus.mission(mid)
        # node-a の claim は失効済み・status は away
        write_json_atomic(mp.assignment("impl", "node-a"),
                          {"node": "node-a", "ts": 1.0, "lease_until": time.time() - 1})
        write_json_atomic(mp.roster(), {"impl": {"node": "node-a"}})
        write_json_atomic(mp.status("node-a--impl"),
                          {"node": "node-a", "role": "impl", "state": "away",
                           "resume_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      time.gmtime(resume_at_epoch))})
        return mp

    def test_away_within_grace_keeps_role(self):
        mid = self.post()
        mp = self._stage_away(mid, time.time() + 3600)     # 復帰予定は 1 時間後
        # 他ノードが claim してきても、away 中の担当からロールを奪わない
        claim_role(self.bus, mp, "impl", "node-b")
        roster = mirror_roster(self.bus, mp, load_roles(mp), "owner-node")
        self.assertEqual(roster["impl"]["node"], "node-a")

    def test_away_grace_exceeded_reopens_role(self):
        os.environ["AGENT_AMIGOS_AWAY_GRACE"] = "0"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_AWAY_GRACE", None)
        mid = self.post()
        mp = self._stage_away(mid, time.time() - 10)       # 復帰予定を過ぎている
        claim_role(self.bus, mp, "impl", "node-b")
        roster = mirror_roster(self.bus, mp, load_roles(mp), "owner-node")
        self.assertEqual(roster["impl"]["node"], "node-b")  # 再募集 → 後任へ

    def test_crash_without_away_reopens_immediately(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        write_json_atomic(mp.assignment("impl", "node-a"),
                          {"node": "node-a", "ts": 1.0, "lease_until": time.time() - 1})
        write_json_atomic(mp.roster(), {"impl": {"node": "node-a"}})
        # away 宣言なし（クラッシュ）→ 即座に再募集
        roster = mirror_roster(self.bus, mp, load_roles(mp), "owner-node")
        self.assertNotIn("impl", roster)

    def test_offboard_marks_away_and_resume_recovers(self):
        mid = self.post()
        d = self.daemon()
        d.cycle()                                          # claim + 初回ターン
        d.offboard(resume_hours=1.0)
        mp = self.bus.mission(mid)
        st = read_json(mp.status("owner-node--impl"))
        self.assertEqual(st["state"], "away")
        self.assertIn("resume_at", st)
        # away でも roster は保持される（lease を強制失効させて確認）
        write_json_atomic(mp.assignment("impl", "owner-node"),
                          {"node": "owner-node", "ts": 1.0,
                           "lease_until": time.time() - 1})
        roster = mirror_roster(self.bus, mp, load_roles(mp), "owner-node")
        self.assertEqual(roster["impl"]["node"], "owner-node")
        # 復帰: 次のターンで working に戻り、続きから進む
        d.cycle()
        st = read_json(mp.status("owner-node--impl"))
        self.assertEqual(st["state"], "working")


class OwnerPicksTests(AmigosTestCase):
    """owner-picks（P2、設計書 §6.3）: claim は応募、確定はオーナーの assign。"""

    def post_op(self, mid="am-op"):
        spec = base_spec(assignment_policy="owner-picks", staffing_timeout=9999)
        return self.post(spec, mid)

    def test_claims_are_applications_not_confirmations(self):
        from agent_amigos.assign import applicants, apply_role
        mid = self.post_op()
        mp = self.bus.mission(mid)
        roles = load_roles(mp)
        apply_role(self.bus, mp, "impl", "node-a", "stub")
        apply_role(self.bus, mp, "impl", "node-b", "codex")
        # 応募が 2 件並び、mirror_roster では自動確定されない
        self.assertEqual([a["node"] for a in applicants(mp, "impl")], ["node-a", "node-b"])
        roster = mirror_roster(self.bus, mp, roles, "owner-node", policy="owner-picks")
        self.assertNotIn("impl", roster)

    def test_owner_confirms_applicant(self):
        from agent_amigos.assign import apply_role, confirm_assignment
        mid = self.post_op()
        mp = self.bus.mission(mid)
        apply_role(self.bus, mp, "impl", "node-a", "stub")
        apply_role(self.bus, mp, "impl", "node-b", "codex")
        roster = confirm_assignment(self.bus, mp, "impl", "node-b")   # 後着でも選べる
        self.assertEqual(roster["impl"]["node"], "node-b")
        self.assertEqual(roster["impl"]["agent_cli"], "codex")
        # 応募していないノードは確定できない
        with self.assertRaises(SystemExit):
            confirm_assignment(self.bus, mp, "impl", "node-ghost")

    def test_owner_picks_end_to_end_with_self_staff(self):
        # staffing_timeout=0: オーナーが応募 + 即時自己確定して 1 ノードで完走する
        spec = base_spec(assignment_policy="owner-picks", staffing_timeout=0)
        mid = self.post(spec, "am-op-e2e")
        d = self.daemon()
        for _ in range(12):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")


class RestaffTests(AmigosTestCase):
    """G5: 実行中のチーム編成変更（restaff = ロール追加・剪定）。"""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")

    def _post_two(self):
        spec = {"mission": {"title": "t", "goal": "g", "staffing_timeout": 0,
                            "convergence": {"done_when": "all-required-done", "quiescence_turns": 99}},
                "roles": [{"id": "worker", "mission": "作る", "deliverables": ["out.md"]},
                          {"id": "extra", "mission": "任意", "required": False}]}
        return self.post(spec, mid="am-restaff")

    def test_restaff_add_and_prune(self):
        from agent_amigos.ownerops import restaff_mission
        from agent_amigos.mission import pruned_roles
        mid = self._post_two()
        mp = self.bus.mission(mid)
        res = restaff_mission(self.bus, mp, add=[{"id": "reviewer", "mission": "見る",
                                                 "approver": True}], prune=["extra"], by="owner-node")
        self.assertEqual(res["added"], ["reviewer"])
        self.assertEqual(res["pruned"], ["extra"])
        self.assertIn("extra", pruned_roles(mp))
        self.assertIn("reviewer", load_roles(mp))          # roles/reviewer.json が書かれた
        # 剪定ロールは収束計算から外れる
        cs = convergence_state(load_mission(mp), load_roles(mp), mp)
        # active roles に extra は含まれない
        from agent_amigos.mission import active_roles
        self.assertNotIn("extra", active_roles(load_roles(mp), mp))

    def test_pruned_role_runner_exits(self):
        from agent_amigos.runner import AmigoRunner
        from agent_amigos.ownerops import restaff_mission
        mid = self._post_two()
        mp = self.bus.mission(mid)
        write_json_atomic(mp.roster(), {rid: {"node": "owner-node"} for rid in load_roles(mp)})
        restaff_mission(self.bus, mp, prune=["extra"], by="owner-node")
        r = AmigoRunner(self.bus, mid, "extra", "owner-node", agent_cli="stub")
        self.assertEqual(r.turn_once(), "exit")

    def test_restaff_prune_unknown_rejected(self):
        from agent_amigos.ownerops import restaff_mission
        mid = self._post_two()
        with self.assertRaises(SystemExit):
            restaff_mission(self.bus, self.bus.mission(mid), prune=["ghost"], by="owner-node")

    def test_restaff_command_owner_only(self):
        from agent_amigos.commands import ingest_commands
        from agent_amigos.configfile import commands_dir
        mid = self._post_two()
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "rs.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "restaff", "mission": mid, "prune": ["extra"]}, f)
        # 非オーナーノードは拒否 → .rejected
        ingest_commands(self.bus, "other-node", self.home)
        self.assertIn("rs.json.rejected", os.listdir(cdir))

    def test_restaff_command_add_prune(self):
        from agent_amigos.commands import ingest_commands
        from agent_amigos.configfile import commands_dir
        from agent_amigos.mission import pruned_roles
        mid = self._post_two()
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "rs.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "restaff", "mission": mid,
                       "add": [{"id": "qa", "mission": "検証", "approver": True}],
                       "prune": ["extra"]}, f, ensure_ascii=False)
        ingest_commands(self.bus, "owner-node", self.home)
        self.assertIn("qa", load_roles(self.bus.mission(mid)))
        self.assertIn("extra", pruned_roles(self.bus.mission(mid)))
