"""agent-amigos の単体テスト — e2e（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


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
