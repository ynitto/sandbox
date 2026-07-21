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
from agent_amigos import delivery  # noqa: E402
from agent_amigos.delivery import deliveries_dir, delivery_json  # noqa: E402
from agent_amigos.ownerops import accept_mission  # noqa: E402
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
        home = os.path.join(self.tmp, "home")
        rc = cli.main(["accept", "--bus", self.bus.root, "--node-id", "owner-node",
                       "--home", home, "am-cli"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase("am-cli"), "done")
        # accept は納品棚へ搬出する（push 型納品）
        self.assertTrue(os.path.isfile(
            os.path.join(home, "deliveries", "am-cli", "delivery.json")))
        rc = cli.main(["deliveries", "--bus", self.bus.root, "--home", home, "-v"])
        self.assertEqual(rc, 0)

    def test_cancel_stops_runners(self):
        mid = self.post()
        rc = cli.main(["cancel", "--bus", self.bus.root, "--node-id", "owner-node",
                       mid, "--reason", "test"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase(mid), "cancelled")
        runner = AmigoRunner(self.bus, mid, "impl", "n1", "stub")
        self.assertEqual(runner.turn_once(), "exit")


class DeliveryTests(AmigosTestCase):
    """納品棚（accept 時の push 型搬出）。
    設計: docs/plans/2026-07-19-agent-amigos-deliverable-delivery-design.md"""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")

    def drive_to_reviewing(self, spec=None, mid="am-deliv", **kw):
        mid = self.post(spec, mid)
        d = self.daemon(home=self.home, **kw)
        for _ in range(14):
            d.cycle()
            if self.phase(mid) in ("reviewing", "done"):
                break
        return mid, d

    def test_accept_exports_deliverable_and_writes_receipt(self):
        mid, _d = self.drive_to_reviewing()
        self.assertEqual(self.phase(mid), "reviewing")
        accept_mission(self.bus, self.bus.mission(mid), by="owner-node",
                       home=self.home, mission=load_mission(self.bus.mission(mid)))
        self.assertEqual(self.phase(mid), "done")

        rec = read_json(delivery_json(self.home, mid))
        self.assertEqual(rec["mission"], mid)
        self.assertEqual(rec["accepted_by"], "owner-node")
        self.assertFalse(rec["partial"])
        self.assertGreater(rec["execution_seconds"], 0)
        # 成果物の本体が納品棚にあり、MANIFEST は納品書へ置き換わっている
        paths = [f["path"] for f in rec["files"]]
        self.assertIn("architect/architecture.md", paths)
        self.assertIn("impl/src/main.py", paths)
        self.assertTrue(all(f["exported"] for f in rec["files"]))
        for rel in paths:
            self.assertTrue(os.path.isfile(os.path.join(self.home, "deliveries", mid, rel)))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "deliveries", mid, "MANIFEST.json")))
        # 由来ロールとハッシュは MANIFEST から引き継ぐ
        arch = next(f for f in rec["files"] if f["path"] == "architect/architecture.md")
        self.assertEqual(arch["role"], "architect")
        self.assertTrue(arch["sha256_16"])
        # 受領一覧に 1 行増える
        with open(os.path.join(self.home, "DELIVERY.md"), encoding="utf-8") as f:
            index = f.read()
        self.assertIn(mid, index)
        self.assertIn("| 受入日時 |", index)

    def test_oversized_file_is_referenced_not_exported(self):
        mid, _d = self.drive_to_reviewing()
        mp = self.bus.mission(mid)
        big = os.path.join(mp.deliverable_dir(), "architect", "big.bin")
        with open(big, "wb") as f:
            f.write(b"0" * (delivery.MAX_EXPORT_BYTES + 1))
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        rec = read_json(delivery_json(self.home, mid))
        row = next(f for f in rec["files"] if f["path"] == "architect/big.bin")
        self.assertFalse(row["exported"])
        self.assertEqual(row["skip_reason"], "size")
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "deliveries", mid, "architect", "big.bin")))

    def test_code_deliverable_records_repo_reference_only(self):
        spec = base_spec(workspace={"repo": "ssh://git@gitlab.local/team/faq-bot.git"})
        mid, _d = self.drive_to_reviewing(spec)
        mp = self.bus.mission(mid)
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        rec = read_json(delivery_json(self.home, mid))
        self.assertEqual(rec["code"]["repo"], "ssh://git@gitlab.local/team/faq-bot.git")
        self.assertEqual(rec["code"]["branch"], f"amigos/{mid}/integration")

    def test_reject_then_accept_replaces_stale_shelf_contents(self):
        mid, d = self.drive_to_reviewing()
        mp = self.bus.mission(mid)
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        stale = os.path.join(self.home, "deliveries", mid, "architect", "stale.md")
        os.makedirs(os.path.dirname(stale), exist_ok=True)
        with open(stale, "w", encoding="utf-8") as f:
            f.write("前ラウンドの残骸")
        # 再 accept（同じミッションの搬出をやり直す）は棚を作り直す
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.isfile(delivery_json(self.home, mid)))

    def test_agent_acceptance_exports_too(self):
        spec = base_spec(acceptance="agent")
        mid = self.post(spec, "am-auto")
        d = self.daemon(home=self.home)
        for _ in range(14):
            d.cycle()
            if self.phase(mid) == "done":
                break
        self.assertEqual(self.phase(mid), "done")
        rec = read_json(delivery_json(self.home, mid))
        self.assertTrue(str(rec["accepted_by"]).startswith("agent:"))
        self.assertEqual(rec["acceptance"], "agent")

    def test_accept_command_drop_exports_to_home(self):
        from agent_amigos.configfile import commands_dir
        spec = base_spec(staffing_timeout=0)
        mid = self.post(spec, "am-cmd-deliv")
        d = NodeDaemon(self.bus, "owner-node", agent_cli="stub", interval=0,
                       commands_home=self.home)
        for _ in range(14):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "accept.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "accept", "mission": mid}, f)
        d.cycle()
        self.assertEqual(self.phase(mid), "done")
        self.assertTrue(os.path.isfile(delivery_json(self.home, mid)))

    def test_no_home_keeps_accept_working_without_export(self):
        mid, _d = self.drive_to_reviewing()
        mp = self.bus.mission(mid)
        accept_mission(self.bus, mp, by="owner-node")     # home 無し = 搬出しない
        self.assertEqual(self.phase(mid), "done")
        self.assertFalse(os.path.exists(deliveries_dir(self.home)))

    def test_gc_keeps_shelf_by_default(self):
        mid, _d = self.drive_to_reviewing()
        mp = self.bus.mission(mid)
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        shelf = os.path.join(self.home, "deliveries", mid)
        os.utime(shelf, (0, 0))
        cli.main(["gc", "--bus", self.bus.root, "--home", self.home, "--keep-days", "0"])
        self.assertFalse(self.bus.mission(mid).exists())   # バスからは消える
        self.assertTrue(os.path.isdir(shelf))              # 納品棚は残る
        cli.main(["gc", "--bus", self.bus.root, "--home", self.home,
                  "--keep-days", "0", "--deliveries-keep-days", "1"])
        self.assertFalse(os.path.isdir(shelf))             # 明示指定でのみ消える


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


class NodeBudgetV2AndControlTests(AmigosTestCase):
    """ノード予算 v2（トークン一次・rates 推定）と agent-control（上書き・lifecycle・status）。"""

    def setUp(self):
        super().setUp()
        os.environ["AGENT_CONTROL_DIR"] = os.path.join(self.tmp, "control")
        self.addCleanup(os.environ.pop, "AGENT_CONTROL_DIR", None)
        from agent_amigos import control
        control._CACHE["mtime"] = None

    def _budget(self, cfg):
        d = os.path.join(self.tmp, "node-budget")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _control(self, ctl):
        from agent_amigos import control
        d = os.path.join(self.tmp, "control")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "control.json"), "w", encoding="utf-8") as f:
            json.dump(ctl, f)
        control._CACHE["mtime"] = None

    def test_token_budget_measured_and_estimated(self):
        from agent_amigos import nodebudget
        self._budget({"version": 2, "tokens": 1000, "rates": {"per_cli": {"claude": 100}}})
        nodebudget.record(8.0, agent_cli="claude")                     # 800 推定
        self.assertFalse(nodebudget.state()["exceeded"])
        nodebudget.record(0.1, tokens_in=150, tokens_out=100)          # +250 = 1050 実測
        st = nodebudget.state()
        self.assertTrue(st["exceeded"])
        self.assertGreaterEqual(st["spent_tokens"], 1000)

    def test_save_config_preserves_v2_keys(self):
        from agent_amigos import nodebudget
        self._budget({"version": 2, "tokens": 500,
                      "allocation": {"soft_ratio": 0.5}, "rates": {"per_cli": {"kiro": 10}}})
        nodebudget.save_config(execution_minutes=5)                    # v1 上限だけ更新
        raw = nodebudget._raw_config()
        self.assertEqual(raw["tokens"], 500)                          # v2 キーを消さない
        self.assertEqual(raw["allocation"]["soft_ratio"], 0.5)
        self.assertEqual(raw["execution_minutes"], 5)

    def test_control_override_and_degraded(self):
        from agent_amigos import control
        self._control({"version": 1, "revision": 4,
                       "workloads": {"amigos": {"agents": {"reviewer": {"model": "opus"}},
                                                "degraded": {"model": "haiku"}}}})
        self.assertEqual(control.override("reviewer"), (None, "opus"))
        self.assertEqual(control.degraded(), (None, "haiku"))

    def test_control_lifecycle_pauses_amigo(self):
        self._control({"version": 1, "workloads": {"amigos": {"lifecycle": "stop"}}})
        mid = self.post()
        d = self.daemon()
        for _ in range(4):
            d.cycle()
        mp = self.bus.mission(mid)
        self.assertNotEqual(self.phase(mid), "failed")               # ミッションは殺さない
        statuses = [read_json(mp.status(f"owner-node--{r}"))
                    for r in ("architect", "impl", "reviewer")]
        paused = [s for s in statuses if s and s.get("state") == "paused"]
        self.assertTrue(paused, "lifecycle=stop で amigo は paused になるべき")
        self.assertIn("agent-control", paused[0].get("note", ""))


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
    """schemas/mission.schema.json（正典）と normalize_mission の突き合わせ。
    実行時は stdlib パーサが検証する（jsonschema 依存なし）— スキーマの enum/既定値が
    実装とズレていないことをテストで担保する。"""

    def _schema(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "schemas", "mission.schema.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_schema_enums_match_implementation(self):
        schema = self._schema()
        props = schema["properties"]["mission"]["properties"]
        self.assertEqual(props["assignment_policy"]["enum"], ["first-come", "owner-picks"])
        self.assertEqual(props["staffing_policy"]["enum"], ["self-staff", "wait", "fail"])
        self.assertEqual(props["acceptance"]["enum"], ["manual", "agent"])
        conv = props["convergence"]["properties"]
        from agent_amigos.mission import DONE_WHEN_MODES
        self.assertEqual(conv["done_when"]["enum"], list(DONE_WHEN_MODES))
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


class CommandSchemaTests(AmigosTestCase):
    """schemas/amigos-command.schema.json（commands/ ドロップの契約）と
    commands._dispatch の突き合わせ。投函側（agent-dashboard writeCommand・人）と
    取り込み側でコマンド一覧・必須フィールドがズレていないことをテストで担保する。"""

    def _schema(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "schemas", "amigos-command.schema.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_schema_commands_match_dispatch(self):
        schema = self._schema()
        self.assertEqual(schema["properties"]["command"]["enum"],
                         ["post", "build-team", "claim", "assign", "restaff", "accept",
                          "reject", "cancel", "say"])
        # oneOf の const 一覧も enum と一致する（宣言漏れ・重複なし）
        consts = [e["properties"]["command"]["const"] for e in schema["oneOf"]]
        self.assertEqual(consts, schema["properties"]["command"]["enum"])

    def test_schema_required_fields_match_dispatch_validation(self):
        """スキーマの required と _dispatch の実検証が一致する:
        必須欠落のドロップは .rejected になり、必須が揃えば成功する。"""
        from agent_amigos.commands import ingest_commands
        from agent_amigos.configfile import commands_dir
        home = os.path.join(self.tmp, "home")
        cdir = commands_dir(home)
        os.makedirs(cdir, exist_ok=True)

        def drop(name, rec):
            with open(os.path.join(cdir, name), "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)

        # post: roles 必須・design か design_file が必須（スキーマの required / anyOf と同じ）
        drop("bad-post.json", {"command": "post", "design": "# d\n"})
        drop("bad-post2.json", {"command": "post",
                                "roles": [{"id": "impl", "mission": "実装"}]})
        drop("ok-post.json", {"command": "post", "mission_id": "am-schema",
                              "design": "# d\n", "title": "t",
                              "mission": {"staffing_timeout": 0},
                              "roles": [{"id": "impl", "mission": "実装",
                                         "deliverables": ["main.py"]}]})
        ingest_commands(self.bus, "owner-node", home)
        names = sorted(os.listdir(cdir))
        self.assertIn("bad-post.json.rejected", names, "roles 欠落は棄却")
        self.assertIn("bad-post2.json.rejected", names, "design/design_file 欠落は棄却")
        self.assertNotIn("ok-post.json", names, "必須が揃えば処理されて消える")
        self.assertIsNotNone(read_json(self.bus.mission("am-schema").mission_json()))
        # 未知コマンド（enum 外）も棄却
        drop("bad-cmd.json", {"command": "rm-rf"})
        ingest_commands(self.bus, "owner-node", home)
        self.assertIn("bad-cmd.json.rejected", sorted(os.listdir(cdir)))

    def test_posted_mission_matches_bus_read_contract(self):
        """バスへ書かれる mission.json が $defs.posted_mission（外部ビュアーの読取契約）に
        合う: 実行時フィールド（id / owner_node / posted_at）が required どおり存在する。"""
        schema = self._schema_mission()
        required = schema["$defs"]["posted_mission"]["required"]
        self.assertEqual(sorted(required), ["id", "owner_node", "posted_at"])
        mid = self.post(mid="am-posted")
        doc = read_json(self.bus.mission(mid).mission_json())
        for key in required:
            self.assertIn(key, doc, f"posted mission.json に {key} が無い")
        self.assertEqual(doc["id"], mid)
        self.assertEqual(doc["owner_node"], "owner-node")

    def _schema_mission(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "schemas", "mission.schema.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)


class ConfigFileTests(unittest.TestCase):
    """`.agent/agent-amigos.yaml` 設定（agent-project と同じ CLI > config > 既定の流儀）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amigos-cfg-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write(self, name, data):
        path = os.path.join(self.tmp, ".agent", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_defaults_without_config(self):
        from agent_amigos.configfile import load_settings, resolve_bus_spec
        s = load_settings(cwd=self.tmp)
        self.assertIsNone(s["_config_path"])
        self.assertEqual(s["_home"], os.path.abspath(self.tmp))
        self.assertFalse(s["hub_serve"])
        # bus 既定 "." はホーム自身に解決される
        self.assertEqual(resolve_bus_spec(s, None), os.path.abspath(self.tmp))

    def test_json_config_with_hub_block(self):
        from agent_amigos.configfile import commands_dir, load_settings
        self._write("agent-amigos.json",
                    {"node_id": "n1", "bus": "shared-bus", "manual_claim": True,
                     "hub": {"serve": True, "port": 9999}})
        s = load_settings(cwd=self.tmp)
        self.assertEqual(s["node_id"], "n1")
        self.assertTrue(s["manual_claim"])
        self.assertTrue(s["hub_serve"])
        self.assertEqual(s["hub_port"], 9999)
        from agent_amigos.configfile import resolve_bus_spec
        self.assertEqual(resolve_bus_spec(s, None),
                         os.path.join(os.path.abspath(self.tmp), "shared-bus"))
        # CLI --bus は設定より優先
        self.assertEqual(resolve_bus_spec(s, "git+ssh://x/y.git"), "git+ssh://x/y.git")
        self.assertEqual(commands_dir(self.tmp),
                         os.path.join(self.tmp, ".agent", "agent-amigos", "commands"))

    def test_yaml_config(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML なし")
        from agent_amigos.configfile import load_settings
        self._write("agent-amigos.yaml",
                    "node_id: yaml-node\ntags: [python, web]\nhub:\n  serve: true\n")
        s = load_settings(cwd=self.tmp)
        self.assertEqual(s["node_id"], "yaml-node")
        self.assertEqual(s["tags"], ["python", "web"])
        self.assertTrue(s["hub_serve"])

    def test_home_for_explicit_config_is_parent_dir(self):
        from agent_amigos.configfile import load_settings
        path = os.path.join(self.tmp, "custom.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("node_id: explicit\n")
        s = load_settings(explicit=path, cwd=os.path.join(self.tmp, "other"))
        self.assertEqual(s["_config_path"], path)
        self.assertEqual(s["_home"], os.path.abspath(self.tmp))
        self.assertEqual(s["node_id"], "explicit")

    def test_root_level_config_preferred_over_dot_agent(self):
        from agent_amigos.configfile import load_settings
        root = os.path.join(self.tmp, "agent-amigos.json")
        with open(root, "w", encoding="utf-8") as f:
            json.dump({"node_id": "root"}, f)
        self._write("agent-amigos.json", {"node_id": "nested"})
        s = load_settings(cwd=self.tmp)
        self.assertEqual(s["_config_path"], root)
        self.assertEqual(s["_home"], os.path.abspath(self.tmp))
        self.assertEqual(s["node_id"], "root")

    def test_global_config_home_is_cwd(self):
        from agent_amigos.configfile import load_settings
        fake_home = tempfile.mkdtemp(prefix="amigos-home-")
        self.addCleanup(shutil.rmtree, fake_home, ignore_errors=True)
        gdir = os.path.join(fake_home, ".agent")
        os.makedirs(gdir)
        gpath = os.path.join(gdir, "agent-amigos.json")
        with open(gpath, "w", encoding="utf-8") as f:
            json.dump({"node_id": "global", "bus": "from-global"}, f)
        old = os.environ.get("HOME")
        os.environ["HOME"] = fake_home
        try:
            s = load_settings(cwd=self.tmp)
            self.assertEqual(s["_config_path"], gpath)
            self.assertEqual(s["_home"], os.path.abspath(self.tmp))
            self.assertEqual(s["node_id"], "global")
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old

    def test_argv_rewrite_defaults_to_serve(self):
        self.assertEqual(cli.resolve_argv([]), ["serve"])
        self.assertEqual(cli.resolve_argv(["--cycles", "1"]), ["serve", "--cycles", "1"])
        self.assertEqual(cli.resolve_argv(["status"]), ["status"])
        self.assertEqual(cli.resolve_argv(["-h"]), ["-h"])


class CommandsIngestTests(AmigosTestCase):
    """commands/ ドロップの取り込み（agent-project の commands/ と同じ結合方式）。"""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        from agent_amigos.configfile import commands_dir
        self.cdir = commands_dir(self.home)
        os.makedirs(self.cdir, exist_ok=True)

    def drop(self, name, rec):
        with open(os.path.join(self.cdir, name), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)

    def daemon_home(self, **kw):
        return NodeDaemon(self.bus, "owner-node", agent_cli="stub", interval=0,
                          commands_home=self.home, **kw)

    def test_post_command_publishes_mission(self):
        self.drop("01-post.json", {
            "command": "post", "title": "依頼", "goal": "g", "mission_id": "am-cmd",
            "design": "# design\n受入基準あり",
            "mission": {"staffing_timeout": 0},
            "roles": [{"id": "impl", "mission": "実装", "deliverables": ["main.py"]}]})
        d = self.daemon_home()
        for _ in range(10):
            d.cycle()
            if self.phase("am-cmd") == "reviewing":
                break
        self.assertEqual(self.phase("am-cmd"), "reviewing")
        # 取り込んだ design doc はホームの状態領域（.agents 移行後は .agents/…）へ永続化される
        from agent_amigos.configfile import state_dir
        self.assertTrue(os.path.isfile(os.path.join(
            state_dir(self.home), "designs", "am-cmd.md")))
        # 処理済みドロップは消える
        self.assertEqual([n for n in os.listdir(self.cdir) if n.endswith(".json")], [])

    def test_claim_command_manual_accept(self):
        # staffing_timeout を伸ばして self-staff（ミッションポリシー）も発動させない
        mid = self.post(base_spec(staffing_timeout=9999))
        d = self.daemon_home(manual_claim=True)
        d.cycle()
        roster = read_json(self.bus.mission(mid).roster()) or {}
        self.assertNotIn("impl", roster)          # 自動では引き受けない
        self.drop("02-claim.json", {"command": "claim", "mission": mid, "role": "impl"})
        d.cycle()
        d.cycle()
        roster = read_json(self.bus.mission(mid).roster()) or {}
        self.assertEqual(roster.get("impl", {}).get("node"), "owner-node")

    def test_bad_command_renamed_rejected(self):
        self.drop("03-bad.json", {"command": "nope"})
        self.drop("04-broken.json", {"command": "claim"})   # mission なし
        d = self.daemon_home()
        d.cycle()
        names = sorted(os.listdir(self.cdir))
        self.assertIn("03-bad.json.rejected", names)
        self.assertIn("04-broken.json.rejected", names)
        d.cycle()                                  # .rejected は再処理されない
        self.assertEqual(sorted(os.listdir(self.cdir)), names)

    def test_manual_claim_keeps_self_staff_off_for_auto_apply_only(self):
        # manual_claim でもオーナー職務（self-staff）は mission ポリシーとして動く
        spec = base_spec()   # staffing_timeout=0 + self-staff
        mid = self.post(spec)
        d = self.daemon_home(manual_claim=True)
        for _ in range(12):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")


class HubRescanTests(AmigosTestCase):
    """cwd-as-hub: ローカル直接書き込み（PUT を経ない）が hub の索引へ反映される。"""

    def test_direct_writes_visible_to_hub_clients(self):
        from agent_amigos import hub
        data = os.path.join(self.tmp, "hub-data")
        server = hub.serve(data, "127.0.0.1", 0)
        import threading
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        os.environ["AGENT_AMIGOS_PULL_INTERVAL"] = "0"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_PULL_INTERVAL", None)

        # 常駐デーモン相当: hub のデータディレクトリを **ローカルバスとして直接** 使う
        local = Bus(data)
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(base_spec(staffing_timeout=9999), f)
        post_mission(local, self.design, roles_path, "home-node", "am-direct")

        # リモートは HubBus 経由 — 直接書き込みが rescan で見える
        from agent_amigos.bus import make_bus
        remote = make_bus(f"hub+http://127.0.0.1:{server.server_port}",
                          workdir=os.path.join(self.tmp, "hub-wd"))
        server.hub_state._last_rescan = 0.0        # 律速を외して即時再走査
        self.assertEqual(remote.list_missions(), ["am-direct"])
        mp = remote.mission("am-direct")
        self.assertTrue(read_json(mp.mission_json()))
        # 直接書き込みの更新（オーナーの budget add 相当）も伝わる
        mission = load_mission(local.mission("am-direct"))
        mission["budget"]["execution_minutes"] = 77
        write_json_atomic(local.mission("am-direct").mission_json(), mission)
        server.hub_state._last_rescan = 0.0
        remote.sync_pull(force=True)
        self.assertEqual(
            load_mission(remote.mission("am-direct"))["budget"]["execution_minutes"], 77)


class NodeIdHomeMigrationTests(unittest.TestCase):
    """node.json は共通ホーム移行中も既存のノード ID を失わない。

    ID は claim / assign / メッセージ宛先に使われるため、振り直しは同一性の断絶になる。
    他の状態と違い「新旧の両方を読む」ことで、どちらに置かれていても拾えるようにしてある。
    """

    def setUp(self):
        from agent_amigos import daemon as _daemon
        self.daemon = _daemon
        self.home = tempfile.mkdtemp(prefix="am-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self._real_expanduser = os.path.expanduser
        os.path.expanduser = lambda p: (
            p.replace("~", self.home, 1) if isinstance(p, str) and p.startswith("~") else p)
        self.addCleanup(setattr, os.path, "expanduser", self._real_expanduser)
        os.environ.pop("AGENT_AMIGOS_NODE", None)

    def _write_node(self, home_dir, node_id):
        d = os.path.join(self.home, home_dir, "amigos")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "node.json"), "w", encoding="utf-8") as f:
            json.dump({"id": node_id}, f)

    def test_env_var_wins(self):
        os.environ["AGENT_AMIGOS_NODE"] = "from-env"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_NODE", None)
        self.assertEqual(self.daemon.default_node_id(), "from-env")

    def test_legacy_home_id_is_preserved(self):
        self._write_node(".agent", "legacy-node")
        self.assertEqual(self.daemon.default_node_id(), "legacy-node")

    def test_legacy_id_survives_when_new_home_dir_exists_but_file_does_not(self):
        """移行の途中（新ホームのディレクトリだけ先にできた端末）でも ID を振り直さない。"""
        self._write_node(".agent", "legacy-node")
        os.makedirs(os.path.join(self.home, ".agents", "amigos"), exist_ok=True)
        self.assertEqual(self.daemon.default_node_id(), "legacy-node")

    def test_new_home_takes_precedence(self):
        self._write_node(".agent", "legacy-node")
        self._write_node(".agents", "new-node")
        self.assertEqual(self.daemon.default_node_id(), "new-node")

    def test_fresh_id_is_minted_into_new_home(self):
        nid = self.daemon.default_node_id()
        self.assertTrue(nid)
        self.assertTrue(os.path.exists(
            os.path.join(self.home, ".agents", "amigos", "node.json")))
        self.assertEqual(self.daemon.default_node_id(), nid, "採番後は同じ ID を返す")


class TeamBuildingTests(AmigosTestCase):
    """チームビルディング（ミッションのみ → team-builder スキルで役割設計 → 従来 post へ合流）。

    LLM は使わず agentcli.run_agent を差し替えて設計出力を注入する。
    """

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")

    DESIGN = {
        "mission": {"budget": {"execution_minutes": 45},
                    "convergence": {"done_when": "reviewer-approved"}},
        "roles": [
            {"id": "architect", "mission": "設計を確定する", "deliverables": ["architecture.md"]},
            {"id": "impl", "mission": "実装する", "deliverables": ["src/"],
             "requires": {"tags": ["python"]}, "collaborates_with": ["architect"]},
            {"id": "reviewer", "mission": "レビューする", "approver": True},
        ],
    }

    def _stub_agent(self, output):
        from agent_amigos import agentcli
        original = agentcli.run_agent
        agentcli.run_agent = lambda *a, **k: output
        self.addCleanup(setattr, agentcli, "run_agent", original)

    def _capture_agent(self, output):
        """run_agent を差し替え、渡されたプロンプトを self._last_prompt に記録する。"""
        from agent_amigos import agentcli
        original = agentcli.run_agent
        box = {}

        def _fake(prompt, *a, **k):
            box.setdefault("prompt", prompt)   # 最初の呼び出し（設計）を記録。
            return output                       # 後続の amigo ターンでは上書きしない
        agentcli.run_agent = _fake
        self.addCleanup(setattr, agentcli, "run_agent", original)
        return box

    def test_skill_is_resolved_from_repo(self):
        from agent_amigos import teambuilding
        text, source = teambuilding.resolve_skill_instructions()
        self.assertTrue(source.endswith("SKILL.md") or source == "(builtin)")
        self.assertIn("team-builder", text.lower())

    def test_build_team_designs_and_validates(self):
        from agent_amigos import teambuilding
        # 設計 JSON の前後に地の文があっても extract_json が拾える
        self._stub_agent("設計結果:\n" + json.dumps(self.DESIGN, ensure_ascii=False) + "\n以上")
        brief = {"title": "FAQ", "goal": "FAQ ボットを作る",
                 "capabilities": ["python"], "agent_cli": "claude"}
        mission_over, roles, meta = teambuilding.build_team(brief, "claude")
        ids = [r["id"] for r in roles]
        self.assertEqual(ids, ["architect", "impl", "reviewer"])
        # agent_cli 未指定のロールにはブリーフ既定が補われる
        self.assertTrue(all(r.get("agent_cli") == "claude" for r in roles))
        self.assertEqual(mission_over["title"], "FAQ")   # ブリーフから補完
        self.assertEqual(mission_over["goal"], "FAQ ボットを作る")
        self.assertTrue(meta.get("skill_source"))

    def test_build_team_requires_real_cli(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps(self.DESIGN))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "x"}, "stub")
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "x"}, "")

    def test_build_team_needs_goal_or_design(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps(self.DESIGN))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"title": "no goal"}, "claude")

    def test_build_team_rejects_invalid_design(self):
        from agent_amigos import teambuilding
        bad = {"roles": [{"id": "a", "mission": "x"}, {"id": "a", "mission": "y"}]}  # 重複 id
        self._stub_agent(json.dumps(bad))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "g"}, "claude")

    def test_build_team_rejects_empty_roles(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps({"roles": []}))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "g"}, "claude")

    def test_build_team_command_posts_mission(self):
        """dashboard/人が投函する build-team 指示を常駐デーモンが取り込み公示する。"""
        from agent_amigos.configfile import commands_dir
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "bt.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "build-team", "title": "FAQ", "goal": "FAQ ボットを作る",
                       "capabilities": ["python"], "agent_cli": "claude"}, f, ensure_ascii=False)
        d = NodeDaemon(self.bus, "owner-node", agent_cli="claude", interval=0,
                       commands_home=self.home)
        d.cycle()
        mids = self.bus.list_missions()
        self.assertEqual(len(mids), 1)
        mp = self.bus.mission(mids[0])
        mission = load_mission(mp)
        roles = load_roles(mp)
        self.assertEqual(mission["title"], "FAQ")
        self.assertEqual(mission["convergence"]["done_when"], "reviewer-approved")
        # 設計したロール + 省略された integrator の自動補充 + design doc 自動生成
        self.assertEqual(set(roles), {"architect", "impl", "reviewer", "integrator"})
        self.assertTrue(os.path.isfile(mp.design_doc()))

    def test_build_team_command_uses_given_design_doc(self):
        from agent_amigos.configfile import commands_dir
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "bt.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "build-team", "goal": "g", "design": "# 与えた設計\n受入基準\n",
                       "agent_cli": "claude"}, f, ensure_ascii=False)
        NodeDaemon(self.bus, "owner-node", agent_cli="claude", interval=0,
                   commands_home=self.home).cycle()
        mids = self.bus.list_missions()
        self.assertEqual(len(mids), 1)
        with open(self.bus.mission(mids[0]).design_doc(), encoding="utf-8") as f:
            self.assertIn("与えた設計", f.read())

    # --- オーケストレーションパターン（カタログ・自動選択・明示指定） --------------

    def test_pattern_catalog_loads_and_tiers(self):
        from agent_amigos import teambuilding
        high = teambuilding.list_patterns(tier="high")
        allp = teambuilding.list_patterns()
        self.assertGreaterEqual(len(high), 8)
        self.assertGreater(len(allp), len(high))          # medium も存在する
        ids = {p["id"] for p in high}
        self.assertIn("self-refine", ids)
        self.assertIn("metagpt-sop", ids)
        for p in allp:                                    # 契約の必須キー
            for k in ("id", "name", "category", "tier", "when_to_use", "feasibility"):
                self.assertIn(k, p, p.get("id"))
            if p.get("target") == "agent-flow":
                self.assertIn("flow", p, p.get("id"))     # 委譲パターンは team を持たない
            else:
                self.assertTrue((p.get("team") or {}).get("roles"), p.get("id"))

    def test_build_team_injects_high_patterns_and_records_choice(self):
        from agent_amigos import teambuilding
        box = self._capture_agent(json.dumps(
            {"pattern": "self-refine", **self.DESIGN}, ensure_ascii=False))
        _mo, _roles, meta = teambuilding.build_team({"goal": "磨き上げたい"}, "claude")
        # 高価値パターンのカタログがプロンプトへ注入されている（自動選択）
        self.assertIn("self-refine", box["prompt"])
        self.assertIn("metagpt-sop", box["prompt"])
        self.assertNotIn("reflexion", box["prompt"])       # medium は自動選択に載らない
        self.assertEqual(meta["chosen_pattern"], "self-refine")

    def test_build_team_forced_pattern_injects_only_that(self):
        from agent_amigos import teambuilding
        box = self._capture_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        _mo, _roles, meta = teambuilding.build_team(
            {"goal": "g"}, "claude", pattern="reflexion")       # medium を明示指定
        self.assertIn("reflexion", box["prompt"])
        self.assertIn("厳守", box["prompt"])                    # forced 見出し
        self.assertEqual(meta["chosen_pattern"], "reflexion")   # 指定が優先

    def test_build_team_unknown_pattern_rejected(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "g"}, "claude", pattern="does-not-exist")

    def test_build_team_pattern_none_is_normalized(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps({"pattern": "none", **self.DESIGN}, ensure_ascii=False))
        _mo, _roles, meta = teambuilding.build_team({"goal": "g"}, "claude")
        self.assertIsNone(meta["chosen_pattern"])

    def test_build_team_command_passes_pattern(self):
        from agent_amigos.configfile import commands_dir
        box = self._capture_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "bt.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "build-team", "goal": "g", "agent_cli": "claude",
                       "pattern": "agentcoder"}, f, ensure_ascii=False)
        NodeDaemon(self.bus, "owner-node", agent_cli="claude", interval=0,
                   commands_home=self.home).cycle()
        self.assertIn("agentcoder", box["prompt"])
        self.assertEqual(len(self.bus.list_missions()), 1)

    def test_cli_list_patterns(self):
        rc = cli.main(["build-team", "--list-patterns"])
        self.assertEqual(rc, 0)

    # --- G4: agent-flow への委譲（探索木・動的分解） -------------------------
    def _stub_flow(self):
        self._stub_agent(json.dumps({"target": "agent-flow", "pattern": "tree-of-thoughts",
                                     "flow": {"goal": "24 パズルを解く",
                                              "strategy": "分岐→スコア→ビーム"}}))

    def test_build_team_flow_target_returns_delegation(self):
        from agent_amigos import teambuilding
        self._stub_flow()
        mo, roles, meta = teambuilding.build_team({"goal": "パズル", "title": "p"}, "claude")
        self.assertEqual(meta["target"], "agent-flow")
        self.assertEqual(roles, [])
        d = meta["delegation"]
        self.assertEqual((d["op"], d["workload"], d["version"]), ("post", "flow", 1))
        self.assertTrue(d["id"].startswith("dg-"))
        self.assertIn("戦略ヒント", d["goal"])              # strategy が goal に畳まれる
        # delegation.schema.json の必須キーを満たす
        schema = read_json(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                        "schemas", "delegation.schema.json"))
        for k in schema["required"]:
            self.assertIn(k, d)

    def test_build_team_flow_cli_dry_run_prints_envelope(self):
        import io
        import contextlib
        self._stub_flow()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["build-team", "--bus", self.bus.root, "--goal", "g",
                           "--agent-cli", "claude", "--node-id", "n1"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn('"workload": "flow"', out)
        self.assertIn("agent-flow submit", out)
        self.assertEqual(self.bus.list_missions(), [])     # amigos へは公示しない

    def test_build_team_command_flow_writes_delegation_not_mission(self):
        from agent_amigos.configfile import commands_dir, state_dir
        self._stub_flow()
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "bt.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "build-team", "goal": "探索する", "agent_cli": "claude"}, f,
                      ensure_ascii=False)
        NodeDaemon(self.bus, "owner-node", agent_cli="claude", interval=0,
                   commands_home=self.home).cycle()
        self.assertEqual(self.bus.list_missions(), [])     # amigos ミッションは作らない
        designs = os.path.join(state_dir(self.home), "designs")
        self.assertTrue(any(n.endswith("-delegation.json") for n in os.listdir(designs)))

    def test_build_team_cli_out_dry_run(self):
        from agent_amigos import teambuilding  # noqa: F401 (ensures import path)
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        out = os.path.join(self.tmp, "roles.json")
        rc = cli.main(["build-team", "--bus", self.bus.root, "--goal", "g",
                       "--agent-cli", "claude", "--out", out, "--node-id", "n1"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.bus.list_missions(), [])   # ドライランは公示しない
        spec = read_json(out)
        self.assertEqual([r["id"] for r in spec["roles"]], ["architect", "impl", "reviewer"])

    def test_build_team_cli_post(self):
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        rc = cli.main(["build-team", "--bus", self.bus.root, "--goal", "g", "--title", "T",
                       "--agent-cli", "claude", "--post", "--node-id", "n1"])
        self.assertEqual(rc, 0)
        mids = self.bus.list_missions()
        self.assertEqual(len(mids), 1)
        self.assertEqual(load_mission(self.bus.mission(mids[0]))["title"], "T")


class SeatsAggregationTests(AmigosTestCase):
    """G1（seats>1・並列同一シート）と G2（integrator の決定的集約）。"""

    def _seats_spec(self, mode, seats=3, **extra):
        role = {"id": "solver", "mission": "独立に解く", "seats": seats,
                "deliverables": ["ANSWER.md"]}
        if mode:
            role["aggregate"] = mode
        role.update(extra)
        return {"mission": {"title": "t", "goal": "g", "staffing_timeout": 0,
                            "convergence": {"done_when": "all-required-done",
                                            "quiescence_turns": 9}},
                "roles": [role]}

    def _aggregate(self, mid, answers, scores=None):
        """指定した席回答（と任意の SCORE）を artifacts へ書き、集約だけを走らせる。"""
        from agent_amigos.runner import AmigoRunner
        from agent_amigos.bus import TurnTxn
        mp = self.bus.mission(mid)
        for sid, ans in answers.items():
            if ans is not None:
                p = os.path.join(mp.artifacts_dir(sid), "ANSWER.md")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(ans)
        for sid, sc in (scores or {}).items():
            p = os.path.join(mp.artifacts_dir(sid), "SCORE")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(str(sc))
        runner = AmigoRunner(self.bus, mid, "integrator", "owner-node")
        txn = TurnTxn()
        summary = runner._aggregate_seat_groups(txn, load_roles(mp))
        txn.apply(self.bus)
        agg = read_json(os.path.join(mp.deliverable_dir(), "solver", "AGGREGATE.json"))
        return summary[0], agg

    # --- G1: 展開・検証 ------------------------------------------------------
    def test_seats_expand_into_concrete_roles(self):
        _m, roles = normalize_mission(self._seats_spec("majority", seats=3))
        ids = {r["id"] for r in roles}
        self.assertEqual({"solver#0", "solver#1", "solver#2", "integrator"}, ids)
        s0 = next(r for r in roles if r["id"] == "solver#0")
        self.assertEqual(s0["seat_group"], "solver")
        self.assertEqual(s0["seat_count"], 3)
        self.assertEqual(s0["aggregate"], "majority")

    def test_collaborates_with_remapped_to_seats(self):
        spec = {"mission": {"title": "t", "goal": "g"},
                "roles": [{"id": "solver", "mission": "解く", "seats": 2},
                          {"id": "reviewer", "mission": "見る", "approver": True,
                           "collaborates_with": ["solver"]}]}
        _m, roles = normalize_mission(spec)
        rv = next(r for r in roles if r["id"] == "reviewer")
        self.assertEqual(sorted(rv["collaborates_with"]), ["solver#0", "solver#1"])

    def test_seats_validation(self):
        with self.assertRaises(SystemExit):                     # aggregate on seats<2
            normalize_mission({"roles": [{"id": "x", "seats": 1, "aggregate": "majority"}]})
        with self.assertRaises(SystemExit):                     # unknown aggregate
            normalize_mission({"roles": [{"id": "x", "seats": 2, "aggregate": "nope"}]})
        with self.assertRaises(SystemExit):                     # seats < 1
            normalize_mission({"roles": [{"id": "x", "seats": 0}]})
        with self.assertRaises(SystemExit):                     # '#' reserved in id
            normalize_mission({"roles": [{"id": "a#0"}]})

    # --- G2: 集約モード ------------------------------------------------------
    def test_aggregate_majority(self):
        mid = self.post(self._seats_spec("majority"), mid="am-maj")
        summary, agg = self._aggregate(mid, {"solver#0": "A", "solver#1": "A", "solver#2": "B"})
        self.assertEqual(summary["mode"], "majority")
        self.assertEqual(agg["winner"], "A")
        self.assertEqual(agg["votes"], 3)
        self.assertEqual(agg["tally"], {"A": 2, "B": 1})
        self.assertFalse(agg["agreed"])
        # 勝者が AGGREGATE.md に書かれる
        md = os.path.join(self.bus.mission(mid).deliverable_dir(), "solver", "AGGREGATE.md")
        with open(md, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "A")

    def test_aggregate_majority_tiebreak_is_deterministic(self):
        mid = self.post(self._seats_spec("majority", seats=2), mid="am-tie")
        _s, agg = self._aggregate(mid, {"solver#0": "B", "solver#1": "A"})
        self.assertEqual(agg["winner"], "A")     # 得票同数は回答昇順で決定的

    def test_aggregate_consensus(self):
        mid = self.post(self._seats_spec("consensus"), mid="am-con")
        _s, agg = self._aggregate(mid, {"solver#0": "X", "solver#1": "X", "solver#2": "X"})
        self.assertTrue(agg["agreed"])
        self.assertEqual(agg["winner"], "X")
        mid2 = self.post(self._seats_spec("consensus"), mid="am-con2")
        _s2, agg2 = self._aggregate(mid2, {"solver#0": "X", "solver#1": "Y", "solver#2": "X"})
        self.assertFalse(agg2["agreed"])          # 割れたら agreed=false（最頻値は X）
        self.assertEqual(agg2["winner"], "X")

    def test_aggregate_gather(self):
        mid = self.post(self._seats_spec("gather"), mid="am-gat")
        summary, _agg = self._aggregate(mid, {"solver#0": "one", "solver#1": "two",
                                              "solver#2": "three"})
        self.assertEqual(summary["mode"], "gather")
        self.assertEqual(summary["collected"], 3)
        md = os.path.join(self.bus.mission(mid).deliverable_dir(), "solver", "AGGREGATE.md")
        with open(md, encoding="utf-8") as f:
            body = f.read()
        for token in ("solver#0", "solver#1", "solver#2", "one", "two", "three"):
            self.assertIn(token, body)

    def test_aggregate_missing_answers_are_skipped(self):
        mid = self.post(self._seats_spec("majority"), mid="am-miss")
        _s, agg = self._aggregate(mid, {"solver#0": "A", "solver#1": "A", "solver#2": None})
        self.assertEqual(agg["votes"], 2)         # 未回答席は票に数えない
        self.assertEqual(agg["winner"], "A")
        self.assertFalse(agg["seats"]["solver#2"]["present"])

    def test_aggregate_weighted_vote(self):
        mid = self.post(self._seats_spec("weighted-vote"), mid="am-wv")
        # A が 2 席・B が 1 席だが、B の重みが大きいので B が勝つ
        _s, agg = self._aggregate(mid, {"solver#0": "A", "solver#1": "A", "solver#2": "B"},
                                  scores={"solver#0": 1, "solver#1": 1, "solver#2": 5})
        self.assertEqual(agg["winner"], "B")
        self.assertEqual(agg["tally"], {"A": 2.0, "B": 5.0})

    def test_aggregate_weighted_vote_defaults_to_one(self):
        mid = self.post(self._seats_spec("weighted-vote"), mid="am-wv2")
        _s, agg = self._aggregate(mid, {"solver#0": "A", "solver#1": "A", "solver#2": "B"})
        self.assertEqual(agg["winner"], "A")     # 重み未指定は 1.0 = majority と同じ

    def test_aggregate_approval_count(self):
        mid = self.post(self._seats_spec("approval-count"), mid="am-ap")
        # スコア最大の候補（席）が勝つ
        _s, agg = self._aggregate(mid, {"solver#0": "X", "solver#1": "Y", "solver#2": "Z"},
                                  scores={"solver#0": 2, "solver#1": 9, "solver#2": 3})
        self.assertEqual(agg["winner"], "Y")
        self.assertEqual(agg["winner_seat"], "solver#1")
        self.assertEqual(agg["winner_score"], 9.0)

    # --- done_when: consensus（早期収束） ------------------------------------
    def _stage_consensus(self, spec, answers, mid):
        self.post(spec, mid=mid)
        mp = self.bus.mission(mid)
        roles = load_roles(mp)
        write_json_atomic(mp.roster(), {rid: {"node": "owner-node"} for rid in roles})
        for sid, ans in answers.items():
            p = os.path.join(mp.artifacts_dir(sid), "ANSWER.md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(ans)
        return convergence_state(load_mission(mp), roles, mp)

    def test_done_when_consensus_converges_when_ratio_met(self):
        spec = self._seats_spec("majority")
        spec["mission"]["convergence"] = {"done_when": "consensus", "consensus_ratio": 0.6,
                                          "consensus_min": 2, "quiescence_turns": 0}
        cs = self._stage_consensus(spec, {"solver#0": "A", "solver#1": "A",
                                          "solver#2": "B"}, "am-cons-y")   # 2/3 = 0.66
        self.assertTrue(cs["converged"])
        self.assertEqual(cs["reason"], "done")

    def test_done_when_consensus_waits_when_split(self):
        spec = self._seats_spec("majority")
        spec["mission"]["convergence"] = {"done_when": "consensus", "consensus_ratio": 0.6,
                                          "consensus_min": 2, "quiescence_turns": 0}
        cs = self._stage_consensus(spec, {"solver#0": "A", "solver#1": "B",
                                          "solver#2": "C"}, "am-cons-n")   # 1/3 < 0.6
        self.assertFalse(cs["converged"])

    def test_done_when_consensus_needs_min_answers(self):
        spec = self._seats_spec("majority", seats=5)
        spec["mission"]["convergence"] = {"done_when": "consensus", "consensus_ratio": 0.6,
                                          "consensus_min": 3, "quiescence_turns": 0}
        cs = self._stage_consensus(spec, {"solver#0": "A", "solver#1": "A"},
                                   "am-cons-min")    # 一致だが回答 2 < min 3
        self.assertFalse(cs["converged"])

    # --- E2E: stub で seats が統合まで到達し manifest に集約が載る ------------
    def test_seats_end_to_end_stub_produces_aggregates(self):
        mid = self.post(self._seats_spec("majority"), mid="am-e2e")
        d = NodeDaemon(self.bus, "owner-node", agent_cli="stub", interval=0)
        for _ in range(25):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")
        man = read_json(self.bus.mission(mid).manifest())
        aggs = {a["group"]: a for a in man.get("aggregates") or []}
        self.assertIn("solver", aggs)
        self.assertEqual(aggs["solver"]["mode"], "majority")
        self.assertEqual(aggs["solver"]["votes"], 3)   # 3 席とも ANSWER.md を書いた


class DebateRoundsTests(AmigosTestCase):
    """G3: 同期討論ラウンド（ラウンドバリア）。"""

    def _debate_spec(self, seats=3, rounds=3, done_when="all-required-done", **conv):
        c = {"done_when": done_when, "quiescence_turns": 99}
        c.update(conv)
        return {"mission": {"title": "d", "goal": "g", "staffing_timeout": 0, "convergence": c},
                "roles": [{"id": "debater", "mission": "立場を論じる", "seats": seats,
                           "rounds": rounds, "aggregate": "majority",
                           "deliverables": ["ANSWER.md"]}]}

    def _round_files(self, mid, seat_id):
        mp = self.bus.mission(mid)
        try:
            return sorted(f for f in os.listdir(mp.artifacts_dir(seat_id))
                          if f.startswith("round-"))
        except FileNotFoundError:
            return []

    def test_rounds_validation(self):
        with self.assertRaises(SystemExit):        # rounds on seats<2
            normalize_mission({"roles": [{"id": "x", "seats": 1, "rounds": 2}]})
        with self.assertRaises(SystemExit):        # rounds < 0
            normalize_mission({"roles": [{"id": "x", "seats": 2, "rounds": -1}]})
        _m, roles = normalize_mission({"roles": [{"id": "x", "seats": 2, "rounds": 3}]})
        self.assertTrue(all(r["rounds"] == 3 for r in roles if r.get("seat_group") == "x"))

    def test_round_barrier_blocks_until_peers_catch_up(self):
        from agent_amigos.runner import AmigoRunner
        mid = self.post(self._debate_spec(seats=3, rounds=3), mid="am-barrier")
        mp = self.bus.mission(mid)
        write_json_atomic(mp.roster(),
                          {rid: {"node": "owner-node"} for rid in load_roles(mp)})
        r0 = AmigoRunner(self.bus, mid, "debater#0", "owner-node", agent_cli="stub")
        r0.turn_once()                             # 席0: round-0 を書く
        self.assertEqual(self._round_files(mid, "debater#0"), ["round-0.md"])
        r0.turn_once()                             # 他席が round-0 未 → バリアで待つ
        self.assertEqual(self._round_files(mid, "debater#0"), ["round-0.md"])
        # 他 2 席も round-0 を出すと、席0 が round-1 へ進める
        AmigoRunner(self.bus, mid, "debater#1", "owner-node", agent_cli="stub").turn_once()
        AmigoRunner(self.bus, mid, "debater#2", "owner-node", agent_cli="stub").turn_once()
        r0.turn_once()
        self.assertEqual(self._round_files(mid, "debater#0"), ["round-0.md", "round-1.md"])

    def test_debate_e2e_reaches_reviewing_with_all_rounds(self):
        mid = self.post(self._debate_spec(seats=3, rounds=3), mid="am-debate-e2e")
        d = NodeDaemon(self.bus, "owner-node", agent_cli="stub", interval=0)
        for _ in range(40):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")
        for sid in ("debater#0", "debater#1", "debater#2"):
            self.assertEqual(self._round_files(mid, sid),
                             ["round-0.md", "round-1.md", "round-2.md"])
            mp = self.bus.mission(mid)
            self.assertTrue(os.path.isfile(os.path.join(mp.artifacts_dir(sid), "ANSWER.md")))

    def test_consensus_early_stop_finalizes_before_last_round(self):
        from agent_amigos.runner import AmigoRunner
        mid = self.post(self._debate_spec(seats=3, rounds=5, done_when="consensus",
                                          consensus_ratio=0.6, consensus_min=2),
                        mid="am-early")
        mp = self.bus.mission(mid)
        write_json_atomic(mp.roster(),
                          {rid: {"node": "owner-node"} for rid in load_roles(mp)})
        # 全席が round-0 を同じ主張で出したと仮定（合意）
        for sid in ("debater#0", "debater#1", "debater#2"):
            p = os.path.join(mp.artifacts_dir(sid), "round-0.md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("同じ主張")
        # 席0 のターン: round-1 を出す前に合意を検出して早期確定（ANSWER=round-0）
        AmigoRunner(self.bus, mid, "debater#0", "owner-node", agent_cli="stub").turn_once()
        self.assertEqual(self._round_files(mid, "debater#0"), ["round-0.md"])  # round-1 を作らない
        with open(os.path.join(mp.artifacts_dir("debater#0"), "ANSWER.md"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "同じ主張")


class TopologyTests(AmigosTestCase):
    """同期討論の通信トポロジ（各席が読む相手の制限）。"""

    def test_topology_neighbors(self):
        from agent_amigos.mission import topology_neighbors
        self.assertEqual([topology_neighbors(i, 5, "ring") for i in range(5)],
                         [[1, 4], [0, 2], [1, 3], [2, 4], [0, 3]])
        self.assertEqual(topology_neighbors(0, 4, "star"), [1, 2, 3])
        self.assertEqual(topology_neighbors(2, 4, "star"), [0])
        self.assertEqual(topology_neighbors(0, 7, "tree"), [1, 2])
        self.assertEqual(sorted(topology_neighbors(1, 7, "tree")), [0, 3, 4])

    def test_topology_requires_rounds(self):
        with self.assertRaises(SystemExit):
            normalize_mission({"roles": [{"id": "d", "seats": 3, "topology": "ring"}]})
        with self.assertRaises(SystemExit):
            normalize_mission({"roles": [{"id": "d", "seats": 3, "rounds": 2,
                                          "topology": "mesh"}]})

    def test_star_spoke_reads_only_hub(self):
        from agent_amigos.runner import AmigoRunner
        spec = {"mission": {"title": "d", "goal": "g"},
                "roles": [{"id": "d", "mission": "討論", "seats": 4, "rounds": 2,
                           "topology": "star"}]}
        mid = self.post(spec, mid="am-star")
        roles = load_roles(self.bus.mission(mid))
        spoke = roles["d#2"]
        r = AmigoRunner(self.bus, mid, "d#2", "owner-node")
        peers = sorted(roles)
        self.assertEqual(r._topology_readable(spoke, peers), ["d#0"])   # ハブのみ
        hub = roles["d#0"]
        rh = AmigoRunner(self.bus, mid, "d#0", "owner-node")
        self.assertEqual(rh._topology_readable(hub, peers), ["d#1", "d#2", "d#3"])


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


class ConductorTests(AmigosTestCase):
    """自律コンダクタ（オプトイン・G5 上位ループ）: 実行中に restaff で編成を調整する。"""

    def _post(self, conductor=None, roles=None):
        m = {"title": "t", "goal": "g", "staffing_timeout": 0,
             "convergence": {"done_when": "all-required-done", "quiescence_turns": 99}}
        if conductor is not None:
            m["conductor"] = conductor
        spec = {"mission": m, "roles": roles or
                [{"id": "worker", "mission": "作る", "deliverables": ["out.md"]},
                 {"id": "extra", "mission": "余剰", "required": False}]}
        return self.post(spec, mid="am-cond")

    def _mock_decision(self, decision):
        from agent_amigos import agentcli
        orig = agentcli.run_agent
        calls = []

        def fake(prompt, *a, **k):
            calls.append(prompt)
            return json.dumps(decision)
        agentcli.run_agent = fake
        self.addCleanup(setattr, agentcli, "run_agent", orig)
        return calls

    def test_conductor_disabled_is_skipped(self):
        from agent_amigos.ownerops import conductor_turn
        mid = self._post()                                  # conductor 無し
        mp = self.bus.mission(mid)
        self.assertEqual(conductor_turn(self.bus, mp, load_mission(mp), "owner-node", "claude"),
                         "skipped")

    def test_conductor_applies_add_and_prune_then_round_gated(self):
        from agent_amigos.ownerops import conductor_turn
        from agent_amigos.mission import pruned_roles
        calls = self._mock_decision({"add": [{"id": "reviewer", "mission": "見る",
                                             "approver": True}], "prune": ["extra"], "reason": "x"})
        mid = self._post(conductor={"enabled": True, "cli": "claude"})
        mp = self.bus.mission(mid)
        mission = load_mission(mp)
        self.assertEqual(conductor_turn(self.bus, mp, mission, "owner-node", "claude"), "acted")
        self.assertIn("reviewer", load_roles(mp))
        self.assertIn("extra", pruned_roles(mp))
        n = len(calls)
        # 同一ラウンドの再評価はしない（LLM を毎サイクル呼ばない）
        self.assertEqual(conductor_turn(self.bus, mp, mission, "owner-node", "claude"), "idle")
        self.assertEqual(len(calls), n)

    def test_conductor_stub_is_noop(self):
        from agent_amigos.ownerops import conductor_turn
        mid = self._post(conductor={"enabled": True, "cli": "stub"})
        mp = self.bus.mission(mid)
        self.assertEqual(conductor_turn(self.bus, mp, load_mission(mp), "owner-node", "stub"),
                         "idle")

    def test_conductor_guardrails_protect_core_roles(self):
        from agent_amigos.ownerops import conductor_turn
        from agent_amigos.mission import pruned_roles
        self._mock_decision({"add": [], "prune": ["integrator", "worker"], "reason": "x"})
        mid = self._post(conductor={"enabled": True, "cli": "claude"},
                         roles=[{"id": "worker", "mission": "w"}])   # 唯一の必須ワーカー
        mp = self.bus.mission(mid)
        conductor_turn(self.bus, mp, load_mission(mp), "owner-node", "claude")
        self.assertNotIn("integrator", pruned_roles(mp))   # integrator は守る
        self.assertNotIn("worker", pruned_roles(mp))       # 最後の必須ワーカーは守る

    def test_conductor_respects_max_total_ops(self):
        from agent_amigos.ownerops import conductor_turn
        calls = self._mock_decision({"add": [{"id": "r", "mission": "m"}], "prune": [],
                                     "reason": "x"})
        mid = self._post(conductor={"enabled": True, "cli": "claude", "max_total_ops": 0})
        mp = self.bus.mission(mid)
        self.assertEqual(conductor_turn(self.bus, mp, load_mission(mp), "owner-node", "claude"),
                         "idle")
        self.assertEqual(len(calls), 0)                    # 上限で LLM を呼ぶ前に止まる


if __name__ == "__main__":
    unittest.main()
