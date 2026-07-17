# agent-amigos プロトコルテスト
#
# LLM 不要（stub のみ）。標準ライブラリの unittest で完結する。
# 実行: python3 -m unittest discover -s tools/agent-amigos/tests
#
# 検証対象（設計書 §16 P0 のコアテスト）:
#   - 役割ミッション表の正規化・検証
#   - claim の決定的タイブレーク（二重アサインなし）・lease 失効 → 再募集
#   - 1 ノード self-staff での E2E（質問/回答 → 成果物 → 承認 → 統合 → 受入）
#   - 差し戻し（reject）ラウンドの再作業
#   - 予算会計（wrap-up の partial 納品 / on_exhausted=fail）
#   - 静穏化（quiescence）収束
#   - アクション封筒の検証（パス逸脱・不正宛先の棄却）
#   - 未回答質問の owner エスカレーション
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_amigos.assign import claim_role, mirror_roster, winner  # noqa: E402
from agent_amigos.bus import Bus  # noqa: E402
from agent_amigos.daemon import NodeDaemon  # noqa: E402
from agent_amigos.mission import (convergence_state, derive_phase, load_mission,  # noqa: E402
                                  load_roles, normalize_mission, post_mission)
from agent_amigos.messages import read_inbox, unanswered_questions  # noqa: E402
from agent_amigos.runner import AmigoRunner  # noqa: E402
from agent_amigos.util import read_json, safe_relpath, write_json_atomic  # noqa: E402
from agent_amigos import cli  # noqa: E402


def base_spec(**mission_over):
    m = {"title": "t", "goal": "g", "staffing_timeout": 0,
         "convergence": {"done_when": "reviewer-approved", "quiescence_turns": 5},
         "budget": {"execution_minutes": 10}}
    m.update(mission_over)
    return {
        "mission": m,
        "roles": [
            {"id": "architect", "mission": "設計", "deliverables": ["architecture.md"]},
            {"id": "impl", "mission": "実装", "deliverables": ["src/main.py"],
             "collaborates_with": ["architect"]},
            {"id": "reviewer", "mission": "レビュー", "approver": True},
        ],
    }


class AmigosTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amigos-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = Bus(os.path.join(self.tmp, "bus"))
        self.design = os.path.join(self.tmp, "design.md")
        with open(self.design, "w", encoding="utf-8") as f:
            f.write("# design\n受入基準: 成果物が揃うこと。\n")
        os.environ["AGENT_AMIGOS_STUB_COST"] = "0.01"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_STUB_COST", None)
        os.environ["AGENT_BUDGET_DIR"] = os.path.join(self.tmp, "node-budget")
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)

    def post(self, spec=None, mid="am-test") -> str:
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(spec or base_spec(), f, ensure_ascii=False)
        return post_mission(self.bus, self.design, roles_path, "owner-node", mid)

    def daemon(self, node="owner-node", **kw) -> NodeDaemon:
        return NodeDaemon(self.bus, node, agent_cli="stub", interval=0, **kw)

    def phase(self, mid):
        mp = self.bus.mission(mid)
        return derive_phase(load_mission(mp), load_roles(mp), mp)


class NormalizeTests(unittest.TestCase):
    def test_integrator_auto_added_and_defaults(self):
        mission, roles = normalize_mission(base_spec())
        ids = [r["id"] for r in roles]
        self.assertIn("integrator", ids)
        self.assertEqual(mission["assignment_policy"], "first-come")
        self.assertEqual(mission["convergence"]["question_timeout"], 2)
        self.assertEqual(mission["budget"]["on_exhausted"], "wrap-up")

    def test_rejects_duplicate_and_reserved_ids(self):
        spec = base_spec()
        spec["roles"].append({"id": "impl"})
        with self.assertRaises(SystemExit):
            normalize_mission(spec)
        spec = base_spec()
        spec["roles"][0]["id"] = "owner"
        with self.assertRaises(SystemExit):
            normalize_mission(spec)

    def test_rejects_unknown_collaborator_and_invalid_policies(self):
        spec = base_spec()
        spec["roles"][1]["collaborates_with"] = ["ghost"]
        with self.assertRaises(SystemExit):
            normalize_mission(spec)
        with self.assertRaises(SystemExit):
            normalize_mission(base_spec(assignment_policy="lottery"))
        with self.assertRaises(SystemExit):
            normalize_mission(base_spec(acceptance="codd-gate"))   # 将来拡張（未対応）
        # P2 で追加されたポリシーは通る
        normalize_mission(base_spec(assignment_policy="owner-picks"))
        normalize_mission(base_spec(acceptance="agent"))


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


class EndToEndTests(AmigosTestCase):
    def run_until(self, mid, want_phase, cycles=12, nodes=None):
        daemons = nodes or [self.daemon()]
        for _ in range(cycles):
            for d in daemons:
                d.cycle()
            if self.phase(mid) == want_phase:
                return
        self.fail(f"phase が {want_phase} になりません（現在: {self.phase(mid)}）")

    def test_single_node_self_staff_full_cycle(self):
        mid = self.post()
        self.run_until(mid, "reviewing")
        mp = self.bus.mission(mid)
        manifest = read_json(mp.manifest())
        self.assertFalse(manifest["partial"])
        self.assertEqual(manifest["reason"], "done")
        self.assertIn("architect", manifest["files"])
        self.assertIn("impl", manifest["files"])
        # 質問/回答の往復が実際に起きている（impl → architect）
        arch_inbox = read_inbox(mp, "architect")
        self.assertTrue(any(m["type"] == "question" and m["from"] == "impl"
                            for m in arch_inbox))
        impl_inbox = read_inbox(mp, "impl")
        self.assertTrue(any(m["type"] == "answer" and m["from"] == "architect"
                            for m in impl_inbox))
        self.assertEqual(unanswered_questions(mp, load_roles(mp)), [])
        # 受入 → done
        write_json_atomic(mp.final(), {"accepted": True})
        self.assertEqual(self.phase(mid), "done")

    def test_two_nodes_split_roles(self):
        mid = self.post()
        owner = self.daemon("owner-node", roles_filter=["architect", "reviewer"])
        worker = NodeDaemon(self.bus, "node-b", agent_cli="stub", interval=0,
                            roles_filter=["impl"])
        # worker が先に impl を claim してから owner を回す（分担を確定させる）
        worker.cycle()
        self.run_until(mid, "reviewing", nodes=[owner, worker])
        roster = read_json(self.bus.mission(mid).roster())
        self.assertEqual(roster["impl"]["node"], "node-b")
        self.assertEqual(roster["architect"]["node"], "owner-node")

    def test_reject_roundtrip_rebuilds_round(self):
        mid = self.post()
        self.run_until(mid, "reviewing")
        mp = self.bus.mission(mid)
        # 差し戻し（owner コマンド相当）
        rc = cli.main(["reject", "--bus", self.bus.root, "--node-id", "owner-node",
                       mid, "--feedback", "作り直して"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase(mid), "working")
        self.run_until(mid, "reviewing")
        manifest = read_json(mp.manifest())
        self.assertEqual(manifest["round"], 1)
        with open(os.path.join(mp.artifacts_dir("impl"), "src/main.py"),
                  encoding="utf-8") as f:
            self.assertIn("round: 1", f.read())


class BudgetTests(AmigosTestCase):
    def test_wrap_up_partial_delivery_on_exhaustion(self):
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"      # 1 ターン = 1 秒消費
        spec = base_spec(budget={"execution_minutes": 1.0 / 60})   # 予算 1 秒
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(10):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        mp = self.bus.mission(mid)
        manifest = read_json(mp.manifest())
        self.assertIsNotNone(manifest, "予算枯渇後に wrap-up 統合されるべき")
        self.assertTrue(manifest["partial"])
        self.assertEqual(manifest["reason"], "budget")

    def test_on_exhausted_fail_terminates(self):
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        spec = base_spec(budget={"execution_minutes": 1.0 / 60, "on_exhausted": "fail"})
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(10):
            d.cycle()
        self.assertEqual(self.phase(mid), "failed")

    def test_budget_add_reopens_headroom(self):
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        spec = base_spec(budget={"execution_minutes": 1.0 / 60})
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(6):
            d.cycle()
        rc = cli.main(["budget", "--bus", self.bus.root, "--node-id", "owner-node",
                       "add", mid, "--minutes", "60"])
        self.assertEqual(rc, 0)
        mp = self.bus.mission(mid)
        mission = load_mission(mp)
        cs = convergence_state(mission, load_roles(mp), mp)
        self.assertFalse(cs["budget"]["hard"])


class QuiescenceTests(AmigosTestCase):
    def test_quiescence_converges_partial(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        roles = load_roles(mp)
        mission = load_mission(mp)
        # 全ワーカーが「完了宣言なし・静穏」の状態を手書きで再現する
        write_json_atomic(mp.roster(), {rid: {"node": "n1"} for rid in roles})
        for rid in roles:
            write_json_atomic(mp.status(f"n1--{rid}"),
                              {"node": "n1", "role": rid, "idle_turns": 5,
                               "done_round": None, "approved_round": None})
        cs = convergence_state(mission, roles, mp)
        self.assertTrue(cs["converged"])
        self.assertEqual(cs["reason"], "quiescence")
        self.assertTrue(cs["partial"])

    def test_unanswered_question_blocks_quiescence(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        roles = load_roles(mp)
        mission = load_mission(mp)
        write_json_atomic(mp.roster(), {rid: {"node": "n1"} for rid in roles})
        for rid in roles:
            write_json_atomic(mp.status(f"n1--{rid}"),
                              {"node": "n1", "role": rid, "idle_turns": 5,
                               "done_round": None, "approved_round": None})
        from agent_amigos.messages import build_message, message_path
        _mid, msg = build_message("impl", "architect", "question", "q", "?")
        write_json_atomic(message_path(mp, msg), msg)
        cs = convergence_state(mission, roles, mp)
        self.assertFalse(cs["converged"])


class EnvelopeTests(AmigosTestCase):
    def test_safe_relpath_rejects_traversal(self):
        for bad in ("../x", "a/../../x", "/etc/passwd", "~/x", ""):
            with self.assertRaises(ValueError):
                safe_relpath(bad)
        self.assertEqual(safe_relpath("./a/b.txt"), "a/b.txt")

    def test_apply_actions_rejects_invalid(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        roles = load_roles(mp)
        runner = AmigoRunner(self.bus, mid, "impl", "n1")
        from agent_amigos.bus import TurnTxn
        txn = TurnTxn()
        st = {"turn": 0, "open_questions": {}}
        actions = [
            {"kind": "write_artifact", "path": "../escape.txt", "content": "x"},
            {"kind": "send", "to": "ghost", "type": "info", "body": "x"},
            {"kind": "declare_done", "approve": True},     # impl は approver でない
            {"kind": "nope"},
            {"kind": "write_artifact", "path": "ok.txt", "content": "ok"},
        ]
        applied, rejected = runner._apply_actions(txn, actions, roles,
                                                  roles["impl"], st, 0)
        self.assertEqual(rejected, 4)
        # 不正な approve 付き declare_done は「検証してから変異」なので状態は汚れない
        self.assertIsNone(st.get("done_round"))
        self.assertIn("write_artifact", applied)
        txn.apply(self.bus)
        self.assertTrue(os.path.isfile(
            os.path.join(mp.artifacts_dir("impl"), "ok.txt")))
        self.assertFalse(os.path.exists(
            os.path.join(mp.artifacts_dir("impl"), "..", "escape.txt")) and
            os.path.isfile(os.path.join(mp.root, "artifacts", "escape.txt")))


class EscalationTests(AmigosTestCase):
    def test_stale_question_escalates_to_owner(self):
        spec = base_spec()
        # architect を任意ロールにし、誰も担当しない状態を作る（質問が放置される）
        spec["roles"][0]["required"] = False
        mid = self.post(spec)
        # owner は architect を claim しない
        d = self.daemon(roles_filter=["impl", "reviewer"])
        for _ in range(6):
            d.cycle()
        mp = self.bus.mission(mid)
        owner_inbox = read_inbox(mp, "owner")
        self.assertTrue(any(m["type"] == "decision-request" for m in owner_inbox),
                        "未回答質問が question_timeout 後に owner へ昇格されるべき")


class CliTests(AmigosTestCase):
    def test_post_status_collect_accept(self):
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(base_spec(), f)
        rc = cli.main(["post", "--bus", self.bus.root, "--node-id", "owner-node",
                       "--design", self.design, "--roles", roles_path,
                       "--mission-id", "am-cli", "--serve", "--agent-cli", "stub",
                       "--cycles", "10", "--interval", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase("am-cli"), "reviewing")
        out = os.path.join(self.tmp, "out")
        rc = cli.main(["collect", "--bus", self.bus.root, "am-cli", "--out", out])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(out, "MANIFEST.json")))
        # 非オーナーは受入できない
        with self.assertRaises(SystemExit):
            cli.main(["accept", "--bus", self.bus.root, "--node-id", "other", "am-cli"])
        rc = cli.main(["accept", "--bus", self.bus.root, "--node-id", "owner-node",
                       "am-cli"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase("am-cli"), "done")

    def test_cancel_stops_runners(self):
        mid = self.post()
        rc = cli.main(["cancel", "--bus", self.bus.root, "--node-id", "owner-node",
                       mid, "--reason", "test"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase(mid), "cancelled")
        runner = AmigoRunner(self.bus, mid, "impl", "n1", "stub")
        self.assertEqual(runner.turn_once(), "exit")


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


class NodeBudgetTests(AmigosTestCase):
    """ノード予算（P1 拡張、設計書 §3.3）: 請負側の上限。共有台帳で全ワークロード合計を管理。"""

    def test_zero_config_is_unlimited(self):
        from agent_amigos import nodebudget
        self.assertFalse(nodebudget.state()["exceeded"])   # 設定なし = 0 = 無制限
        mid = self.post()
        d = self.daemon()
        for _ in range(12):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")     # 制限なしで完走

    def test_exhaustion_pauses_and_notifies_owner(self):
        from agent_amigos import nodebudget
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        nodebudget.save_config(execution_minutes=1.0 / 60)   # ノード上限 = 1 秒
        mid = self.post()
        d = self.daemon()
        for _ in range(6):
            d.cycle()
        mp = self.bus.mission(mid)
        # ミッションは failed にならない（ノード予算はノードの都合 — §3.3）
        self.assertNotEqual(self.phase(mid), "failed")
        statuses = [read_json(mp.status(n)) for n in
                    (f"owner-node--{r}" for r in ("architect", "impl", "reviewer"))]
        paused = [s for s in statuses if s and s.get("state") == "paused"]
        self.assertTrue(paused, "ノード予算超過で amigo が paused になるべき")
        self.assertIn("node-budget", paused[0].get("note", ""))
        owner_inbox = read_inbox(mp, "owner")
        self.assertTrue(any("[node-budget]" in m.get("body", "") for m in owner_inbox),
                        "owner へ node-budget 理由の通知が届くべき")
        # 台帳に amigos ワークロードで記帳されている
        self.assertGreater(nodebudget.spent_seconds("day", "amigos"), 0)

    def test_raising_limit_resumes_to_completion(self):
        from agent_amigos import nodebudget
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        nodebudget.save_config(execution_minutes=1.0 / 60)
        mid = self.post()
        d = self.daemon()
        for _ in range(4):
            d.cycle()
        self.assertNotEqual(self.phase(mid), "reviewing")
        nodebudget.save_config(execution_minutes=0)          # 0 = 無制限へ引き上げ
        for _ in range(12):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")       # paused から復帰して完走

    def test_workload_cap_applies_even_if_total_unlimited(self):
        from agent_amigos import nodebudget
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        nodebudget.save_config(execution_minutes=0,
                               workload_minutes={"amigos": 1.0 / 60})
        # 他ワークロード（定常業務など）の消費は amigos 内訳に影響しない
        nodebudget.record(100.0, workload="routine", tool="kiro-loop")
        mid = self.post()
        d = self.daemon()
        for _ in range(6):
            d.cycle()
        mp = self.bus.mission(mid)
        statuses = [read_json(mp.status(f"owner-node--{r}"))
                    for r in ("architect", "impl", "reviewer")]
        self.assertTrue(any(s and s.get("state") == "paused" for s in statuses))

    def test_ledger_is_shared_across_workloads(self):
        from agent_amigos import nodebudget
        nodebudget.save_config(execution_minutes=2.0 / 60)   # 合計 2 秒
        nodebudget.record(100.0, workload="project", tool="agent-project")
        # 定常業務・プロジェクトの消費だけで合計上限に達する → amigos は 1 ターンも回せない
        self.assertTrue(nodebudget.state()["exceeded"])


@unittest.skipUnless(shutil.which("git"), "git が必要")
class GitBusTests(unittest.TestCase):
    """GitBus（P1、設計書 §5.1）: 専用バスリポジトリ + ミッション別ブランチ。"""

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
        # 1 ターン（成果物 + status + events）= origin 上の 1 コミット（原子性 §6.6）
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


class AcceptanceAgentTests(AmigosTestCase):
    """acceptance: agent（P2、設計書 §8.2）: オーナーノードの自動受入判定。
    stub 判定は決定的（partial → 差し戻し、完全 → 受入）。"""

    def test_auto_accept_full_delivery(self):
        spec = base_spec(acceptance="agent")
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(14):
            d.cycle()
            if self.phase(mid) == "done":
                break
        self.assertEqual(self.phase(mid), "done")     # 人の accept なしで done に到達
        mp = self.bus.mission(mid)
        final = read_json(mp.final())
        self.assertTrue(final["accepted"])
        self.assertTrue(str(final["by"]).startswith("agent:"))

    def test_partial_rejected_then_escalates_to_human(self):
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        spec = base_spec(acceptance="agent",
                         convergence={"done_when": "all-required-done", "review_rounds": 2},
                         budget={"execution_minutes": 1.0 / 60})   # 予算枯渇 → partial 納品
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(20):
            d.cycle()
        mp = self.bus.mission(mid)
        # 自動判定は partial を差し戻し続けるが review_rounds で止まり、人へ委ねる
        self.assertNotEqual(self.phase(mid), "done")
        rejections = sorted(os.listdir(mp.rejections_dir()))
        self.assertEqual(len(rejections), 2)          # 上限 review_rounds=2 で停止
        owner_inbox = read_inbox(mp, "owner")
        self.assertTrue(any(m["type"] == "decision-request"
                            and "受入の自動判定" in m.get("subject", "")
                            for m in owner_inbox))
        # final は書かれていない（done を作れるのは人の判断のみ）
        self.assertIsNone(read_json(mp.final()))


class HubBusTests(AmigosTestCase):
    """HubBus（P2、設計書 §5.2）: 薄い中継サーバ経由で P1 と同じ動作。"""

    def setUp(self):
        super().setUp()
        from agent_amigos import hub
        self.data = os.path.join(self.tmp, "hub-data")
        self.server = hub.serve(self.data, "127.0.0.1", 0)   # 空きポート
        self.port = self.server.server_port
        self.thread = __import__("threading").Thread(target=self.server.serve_forever,
                                                     daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        os.environ["AGENT_AMIGOS_PULL_INTERVAL"] = "0"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_PULL_INTERVAL", None)

    def hub_bus(self, name):
        from agent_amigos.bus import make_bus
        return make_bus(f"hub+http://127.0.0.1:{self.port}",
                        workdir=os.path.join(self.tmp, f"hub-wd-{name}"))

    def test_two_nodes_e2e_over_hub(self):
        bus_a, bus_b = self.hub_bus("a"), self.hub_bus("b")
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(base_spec(staffing_timeout=9999), f)
        mid = post_mission(bus_a, self.design, roles_path, "owner-node", "am-hub")
        self.assertEqual(bus_b.list_missions(), ["am-hub"])   # hub 越しに公示が見える
        a = NodeDaemon(bus_a, "owner-node", agent_cli="stub", interval=0,
                       roles_filter=["architect", "reviewer"])
        b = NodeDaemon(bus_b, "node-b", agent_cli="stub", interval=0,
                       roles_filter=["impl"])
        b.cycle()
        phase = None
        for _ in range(14):
            a.cycle()
            b.cycle()
            mp = bus_a.mission(mid)
            phase = derive_phase(load_mission(mp), load_roles(mp), mp)
            if phase == "reviewing":
                break
        self.assertEqual(phase, "reviewing")
        roster = read_json(bus_a.mission(mid).roster())
        self.assertEqual(roster["impl"]["node"], "node-b")
        # 質問/回答が hub 越しに往復している
        mp_b = bus_b.mission(mid)
        self.assertTrue(any(m["type"] == "question"
                            for m in read_inbox(mp_b, "architect")))
        # hub のデータディレクトリはミッションレイアウトそのまま（dashboard が直接読める形）
        self.assertTrue(os.path.isfile(
            os.path.join(self.data, "missions", "am-hub", "deliverable", "MANIFEST.json")))

    def test_claim_race_over_hub(self):
        bus_a, bus_b = self.hub_bus("a"), self.hub_bus("b")
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(base_spec(staffing_timeout=9999), f)
        mid = post_mission(bus_a, self.design, roles_path, "owner-node", "am-race")
        bus_b.sync_pull(force=True)
        mp_a, mp_b = bus_a.mission(mid), bus_b.mission(mid)
        self.assertTrue(claim_role(bus_a, mp_a, "impl", "node-a"))
        self.assertFalse(claim_role(bus_b, mp_b, "impl", "node-b"))
        self.assertEqual(winner(mp_b, "impl"), "node-a")   # 全ノードが同じ勝者を導く

    def test_auth_token_rejects_without_bearer(self):
        from agent_amigos import hub
        server = hub.serve(os.path.join(self.tmp, "hub-auth"), "127.0.0.1", 0,
                           token="secret")
        thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        bus = __import__("agent_amigos.bus", fromlist=["make_bus"]).make_bus(
            f"hub+http://127.0.0.1:{server.server_port}",
            workdir=os.path.join(self.tmp, "hub-wd-auth"))
        with self.assertRaises(RuntimeError):
            bus.sync_pull(force=True)                      # トークンなし → 401
        os.environ["AGENT_AMIGOS_HUB_TOKEN"] = "secret"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_HUB_TOKEN", None)
        bus.sync_pull(force=True)                          # トークンあり → 通る

    def test_gc_removes_tree_on_hub(self):
        bus_a = self.hub_bus("a")
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(base_spec(), f)
        mid = post_mission(bus_a, self.design, roles_path, "owner-node", "am-gc")
        write_json_atomic(bus_a.mission(mid).cancelled(), {"ts": "2026-01-01T00:00:00Z"})
        bus_a.sync_push("cancel")
        bus_a.remove_mission(mid)
        bus_c = self.hub_bus("c")
        self.assertEqual(bus_c.list_missions(), [])
        self.assertFalse(os.path.isdir(os.path.join(self.data, "missions", "am-gc")))


class MissionSchemaTests(AmigosTestCase):
    """schemas/amigos-mission.schema.json（正典）と normalize_mission の突き合わせ。
    実行時は stdlib パーサが検証する（jsonschema 依存なし）— スキーマの enum/既定値が
    実装とズレていないことをテストで担保する。"""

    def _schema(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "schemas", "amigos-mission.schema.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_schema_enums_match_implementation(self):
        schema = self._schema()
        props = schema["properties"]["mission"]["properties"]
        self.assertEqual(props["assignment_policy"]["enum"], ["first-come", "owner-picks"])
        self.assertEqual(props["staffing_policy"]["enum"], ["self-staff", "wait", "fail"])
        self.assertEqual(props["acceptance"]["enum"], ["manual", "agent"])
        conv = props["convergence"]["properties"]
        self.assertEqual(conv["done_when"]["enum"],
                         ["all-required-done", "reviewer-approved"])
        budget = props["budget"]["properties"]
        self.assertEqual(budget["on_exhausted"]["enum"], ["wrap-up", "fail"])

    def test_schema_defaults_match_normalize(self):
        from agent_amigos.mission import (BUDGET_DEFAULTS, CONVERGENCE_DEFAULTS,
                                          DEFAULTS)
        schema = self._schema()
        props = schema["properties"]["mission"]["properties"]
        self.assertEqual(props["assignment_policy"]["default"], DEFAULTS["assignment_policy"])
        self.assertEqual(props["staffing_policy"]["default"], DEFAULTS["staffing_policy"])
        self.assertEqual(props["acceptance"]["default"], DEFAULTS["acceptance"])
        self.assertEqual(props["staffing_timeout"]["default"], DEFAULTS["staffing_timeout"])
        conv = props["convergence"]["properties"]
        for key in ("quiescence_turns", "review_rounds", "question_timeout"):
            self.assertEqual(conv[key]["default"], CONVERGENCE_DEFAULTS[key], key)
        self.assertEqual(conv["done_when"]["default"], CONVERGENCE_DEFAULTS["done_when"])
        budget = props["budget"]["properties"]
        for key in ("execution_minutes", "per_role_turns", "soft_ratio", "on_exhausted"):
            self.assertEqual(budget[key]["default"], BUDGET_DEFAULTS[key], key)

    def test_normalized_roles_validate_against_role_schema_keys(self):
        _mission, roles = __import__("agent_amigos.mission", fromlist=["normalize_mission"]) \
            .normalize_mission(base_spec())
        role_props = set(self._schema()["properties"]["roles"]["items"]["properties"])
        for role in roles:
            self.assertTrue(set(role).issubset(role_props),
                            f"スキーマに無いキー: {set(role) - role_props}")


if __name__ == "__main__":
    unittest.main()
