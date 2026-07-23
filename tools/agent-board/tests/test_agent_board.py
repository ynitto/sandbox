#!/usr/bin/env python3
"""agent-board のプロトコル単体テスト（LLM 不要・stub のみ・標準ライブラリ unittest）。

実行: python3 -m unittest discover -s tools/agent-board/tests
      または python3 tools/agent-board/tests/test_agent_board.py

検証範囲: 決定的タイブレークで勝者は 1 人・lease 失効で再入札・repos 照合による入札選別・
flow inbox / amigos-command への引き渡しの形・投機の first-valid 一本化・owner-picks の応募/落札。
"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
_PKG = HERE.parent / "agent_board"

# 開発者 cwd の設定ファイルがテストへ漏れないよう中立な一時 cwd で走らせる。
os.chdir(tempfile.mkdtemp(prefix="ab-tests-cwd-"))

_spec = importlib.util.spec_from_file_location(
    "agent_board", _PKG / "__init__.py", submodule_search_locations=[str(_PKG)])
ab = importlib.util.module_from_spec(_spec)
sys.modules["agent_board"] = ab
_spec.loader.exec_module(ab)

from agent_board import core as C          # noqa: E402
from agent_board import board as B         # noqa: E402
from agent_board import daemon as D        # noqa: E402
from agent_board import repos as R         # noqa: E402


def _post(did="dg-1", workload="flow", **kw):
    env = {"op": "post", "version": 1, "id": did, "workload": workload,
           "goal": "何かを作る"}
    env.update(kw)
    if workload == "amigos" and "engine" not in env:
        env["engine"] = {"amigos": {"roles": [{"id": "architect"}]}}
    return B.validate_post(env)


class BusClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ab-bus-")
        self.bus = C.Bus(self.tmp)
        self.bus.ensure_root()
        self.bus.write_post("dg-1", _post("dg-1"))

    def test_open_then_winner_then_done(self):
        self.assertIsNone(self.bus.winner("dg-1"))
        self.assertTrue(self.bus.try_bid("dg-1", "pc-a", 60))
        self.assertEqual(self.bus.winner("dg-1"), "pc-a")
        self.bus.write_result("dg-1", {"winner": "pc-a", "status": "done"})
        self.assertTrue(self.bus.has_result("dg-1"))

    def test_deterministic_winner_earliest_ts(self):
        # pc-b が先に入札 → pc-a が後から入札しても勝者は pc-b（ts 昇順）
        self.bus._write_bid("dg-1", "pc-b", 60, {})
        time.sleep(0.001)
        self.bus._write_bid("dg-1", "pc-a", 60, {})
        self.assertEqual(self.bus.winner("dg-1"), "pc-b")

    def test_expired_lease_is_reclaimable(self):
        self.bus._write_bid("dg-1", "pc-a", -1, {})  # 既に失効
        self.assertIsNone(self.bus.winner("dg-1"))
        self.assertTrue(self.bus.try_bid("dg-1", "pc-b", 60))
        self.assertEqual(self.bus.winner("dg-1"), "pc-b")

    def test_concurrent_bid_single_winner(self):
        results = {}

        def bid(who):
            results[who] = self.bus.try_bid("dg-1", who, 60)

        ts = [threading.Thread(target=bid, args=(f"pc-{i}",)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        winners = [who for who, won in results.items() if won]
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.bus.winner("dg-1"), winners[0])

    def test_extend_bid_keeps_ts(self):
        self.bus.try_bid("dg-1", "pc-a", 60)
        info0 = self.bus._list_bids("dg-1")["pc-a"]
        self.assertTrue(self.bus.extend_bid("dg-1", "pc-a", 120))
        info1 = self.bus._list_bids("dg-1")["pc-a"]
        self.assertEqual(info0["ts"], info1["ts"])
        self.assertGreater(info1["lease_until"], info0["lease_until"])

    def test_bid_ranking_order(self):
        self.bus._write_bid("dg-1", "pc-b", 60, {})
        time.sleep(0.001)
        self.bus._write_bid("dg-1", "pc-a", 60, {})
        self.assertEqual(self.bus.bid_ranking("dg-1"), ["pc-b", "pc-a"])

    def test_no_bid_after_result_or_cancel(self):
        self.bus.write_result("dg-1", {"winner": "x", "status": "done"})
        self.assertFalse(self.bus.try_bid("dg-1", "pc-a", 60))
        self.bus.write_post("dg-2", _post("dg-2"))
        self.bus.write_cancelled("dg-2", "やめた", "owner")
        self.assertFalse(self.bus.try_bid("dg-2", "pc-a", 60))


class EnvelopeTests(unittest.TestCase):
    def test_flow_owner_picks_rejected(self):
        with self.assertRaises(ValueError):
            B.validate_post({"op": "post", "version": 1, "id": "dg-x",
                             "workload": "flow", "goal": "g",
                             "policy": {"assignment": "owner-picks"}})

    def test_amigos_requires_roles(self):
        with self.assertRaises(ValueError):
            B.validate_post({"op": "post", "version": 1, "id": "dg-x",
                             "workload": "amigos", "goal": "g"})

    def test_bad_id(self):
        with self.assertRaises(ValueError):
            B.validate_post({"op": "post", "version": 1, "id": "bad id!",
                             "workload": "flow", "goal": "g"})

    def test_mint_id_shape(self):
        did = B.mint_id()
        self.assertTrue(did.startswith("dg-"))
        self.assertTrue(all(c in B._ID_OK for c in did))


class ReposEligibilityTests(unittest.TestCase):
    def _node(self, **kw):
        base = {"node": "pc-a", "workloads": [], "tags": [], "agent_cli": [], "repos": {}}
        base.update(kw)
        return base

    def test_normalize_identity(self):
        specs = R.normalize_registry({
            "app": {"url": "git@h:team/app.git", "base": "main", "owns": ["src/**"]},
            "docs": {"url": "git@h:team/docs.git", "readonly": True},
        })
        self.assertTrue(R.covers(specs, {"url": "git@h:team/app.git"}, writable=True))
        # readonly は書込先候補にしない
        self.assertFalse(R.covers(specs, {"url": "git@h:team/docs.git"}, writable=True))
        # .git / 末尾スラッシュの揺れを吸収
        self.assertTrue(R.covers(specs, {"url": "git@h:team/app"}, writable=True))

    def test_workspace_repo_selects_node(self):
        node = self._node(repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        ok, _ = B.node_eligible(node, _post(workspace={"url": "git@h:team/app.git"}))
        self.assertTrue(ok)
        # 担当外リポジトリの公示には入札しない
        ok2, why = B.node_eligible(node, _post(workspace={"url": "git@h:team/other.git"}))
        self.assertFalse(ok2)
        self.assertIn("担当していない", why)

    def test_tags_and_cli_and_workload(self):
        node = self._node(workloads=["flow"], tags=["python"], agent_cli=["codex"])
        ok, _ = B.node_eligible(node, _post(requires={"tags": ["python"], "agent_cli": ["codex"]}))
        self.assertTrue(ok)
        self.assertFalse(B.node_eligible(node, _post(requires={"tags": ["rust"]}))[0])
        self.assertFalse(B.node_eligible(node, _post(workload="amigos"))[0])

    def test_requires_repos_by_name(self):
        node = self._node(repos={"app": {"url": "git@h:team/app.git", "owns": ["**"]}})
        self.assertTrue(B.node_eligible(node, _post(requires={"repos": ["app"]}))[0])
        self.assertFalse(B.node_eligible(node, _post(requires={"repos": ["nope"]}))[0])


class HandoffTests(unittest.TestCase):
    def test_flow_inbox_shape(self):
        tmp = tempfile.mkdtemp(prefix="ab-flow-")
        env = _post("dg-f", workspace={"url": "git@h:team/app.git", "base": "main"},
                    design="設計本文")
        path = B.handoff_flow(env, tmp, submitter="agent-board:pc-a")
        rec = json.load(open(path))
        self.assertEqual(path, os.path.join(tmp, "inbox", "dg-f.json"))
        self.assertEqual(rec["id"], "dg-f")
        self.assertIn("## 設計", rec["request"])
        self.assertEqual(rec["workspace"], {"url": "git@h:team/app.git", "base": "main"})
        self.assertEqual(rec["delegation"], {"id": "dg-f", "board": True})
        self.assertEqual(rec["submitter"], "agent-board:pc-a")

    def test_amigos_command_shape(self):
        tmp = tempfile.mkdtemp(prefix="ab-amigos-")
        env = _post("dg-a", workload="amigos",
                    engine={"amigos": {"roles": [{"id": "architect"}, {"id": "reviewer"}]}},
                    policy={"assignment": "owner-picks"})
        path = B.handoff_amigos(env, tmp, node_id="pc-a")
        rec = json.load(open(path))
        self.assertEqual(rec["command"], "post")
        self.assertEqual(rec["mission_id"], "dg-a")
        self.assertEqual(len(rec["roles"]), 2)
        self.assertEqual(rec["mission"]["assignment_policy"], "owner-picks")
        self.assertTrue(rec["design"])  # design 省略 → 合成される
        # ドロップ先は amigos の commands 契約パス
        self.assertIn(os.path.join(".agents", "agent-amigos", "commands"), path)

    def test_amigos_command_enum_matches_schema(self):
        # 生成する command が amigos-command.schema.json の enum にあることを突き合わせる
        schema_path = HERE.parents[2] / "schemas" / "amigos-command.schema.json"
        schema = json.load(open(schema_path))
        enum = schema["properties"]["command"]["enum"]
        rec = B.amigos_command_record(_post("dg-a", workload="amigos"))
        self.assertIn(rec["command"], enum)


class ResolveTests(unittest.TestCase):
    def test_first_valid_earliest_completed(self):
        reports = [
            {"who": "pc-b", "status": "done", "verified": True, "completed_ts": 200},
            {"who": "pc-a", "status": "done", "verified": True, "completed_ts": 100},
            {"who": "pc-c", "status": "failed", "completed_ts": 50},
        ]
        win = B.resolve_first_valid(reports)
        self.assertEqual(win["who"], "pc-a")

    def test_first_valid_skips_unverified(self):
        reports = [
            {"who": "pc-a", "status": "done", "verified": False, "completed_ts": 100},
            {"who": "pc-b", "status": "done", "verified": True, "completed_ts": 300},
        ]
        self.assertEqual(B.resolve_first_valid(reports)["who"], "pc-b")

    def test_no_valid(self):
        self.assertIsNone(B.resolve_first_valid(
            [{"who": "x", "status": "failed", "completed_ts": 1}]))


class ServeCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ab-serve-")
        self.bus = C.Bus(self.tmp)
        self.bus.ensure_root()
        self.flow_bus = tempfile.mkdtemp(prefix="ab-serve-flow-")

    def _settings(self, **kw):
        s = {"board": self.tmp, "workloads": ["flow"], "tags": [], "agent_cli": ["codex"],
             "repos": {"app": {"url": "git@h:team/app.git", "owns": ["**"]}},
             "flow_bus": self.flow_bus, "amigos_home": None, "lease": 60.0,
             "interval": 15.0, "max_concurrent": 0}
        s.update(kw)
        return s

    def test_win_and_dispatch_flow(self):
        self.bus.write_post("dg-1", _post("dg-1", workspace={"url": "git@h:team/app.git"}))
        handed = D.serve_cycle(self.bus, "pc-a", self._settings())
        self.assertEqual(handed, ["dg-1"])
        self.assertEqual(self.bus.winner("dg-1"), "pc-a")
        self.assertTrue(os.path.exists(os.path.join(self.flow_bus, "inbox", "dg-1.json")))
        st = self.bus.read_status("dg-1", "pc-a")
        self.assertEqual(st["state"], "dispatched")
        # ノードが登録されている
        self.assertTrue(any(n["node"] == "pc-a" for n in self.bus.list_nodes()))

    def test_ineligible_repo_no_bid(self):
        self.bus.write_post("dg-1", _post("dg-1", workspace={"url": "git@h:team/other.git"}))
        handed = D.serve_cycle(self.bus, "pc-a", self._settings())
        self.assertEqual(handed, [])
        self.assertIsNone(self.bus.winner("dg-1"))

    def test_second_node_does_not_double_dispatch(self):
        self.bus.write_post("dg-1", _post("dg-1", workspace={"url": "git@h:team/app.git"}))
        D.serve_cycle(self.bus, "pc-a", self._settings())
        # 別ノードが同じ板を巡回しても、既に勝者が居るので落札しない（先勝ち）
        flow_b = tempfile.mkdtemp(prefix="ab-serve-flow2-")
        handed_b = D.serve_cycle(self.bus, "pc-b", self._settings(flow_bus=flow_b))
        self.assertEqual(handed_b, [])
        self.assertEqual(self.bus.winner("dg-1"), "pc-a")

    def test_owner_picks_applies_not_dispatch(self):
        self.bus.write_post("dg-1", _post(
            "dg-1", workload="amigos",
            engine={"amigos": {"roles": [{"id": "architect"}]}},
            policy={"assignment": "owner-picks"}))
        home = tempfile.mkdtemp(prefix="ab-serve-home-")
        s = self._settings(workloads=["amigos"], amigos_home=home)
        handed = D.serve_cycle(self.bus, "pc-a", s)
        # owner-picks は入札（応募）のみで引き渡さない
        self.assertEqual(handed, [])
        st = self.bus.read_status("dg-1", "pc-a")
        self.assertTrue(st.get("applied"))
        # award 後に再巡回すると引き渡す
        self.bus.write_award("dg-1", "pc-a", awarded_by="owner")
        handed2 = D.serve_cycle(self.bus, "pc-a", s)
        self.assertEqual(handed2, ["dg-1"])

    def test_max_concurrent_gate(self):
        for i in range(3):
            self.bus.write_post(f"dg-{i}", _post(f"dg-{i}", workspace={"url": "git@h:team/app.git"}))
        handed = D.serve_cycle(self.bus, "pc-a", self._settings(max_concurrent=2))
        self.assertEqual(len(handed), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
