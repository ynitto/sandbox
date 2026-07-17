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

    def test_rejects_unknown_collaborator_and_p2_policies(self):
        spec = base_spec()
        spec["roles"][1]["collaborates_with"] = ["ghost"]
        with self.assertRaises(SystemExit):
            normalize_mission(spec)
        with self.assertRaises(SystemExit):
            normalize_mission(base_spec(assignment_policy="owner-picks"))
        with self.assertRaises(SystemExit):
            normalize_mission(base_spec(acceptance="codd-gate"))


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


if __name__ == "__main__":
    unittest.main()
