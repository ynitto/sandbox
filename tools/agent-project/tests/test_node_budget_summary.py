"""node-budget-summary 契約テスト（Phase1 射影 / write_status additive 埋込）。

範囲: schema 適合・write_status の budget 埋込・enforce 既定 false・旧 viewer 耐性。
out_of_scope: allocate/claim/eligible・board ミラー・enforcement true。
"""
from __future__ import annotations

import os as _os
import sys as _sys
import time

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403
from _contract_fixtures import (  # noqa: E402
    NODE_BUDGET_LEDGER_V2,
    NODE_BUDGET_SUMMARY_SCHEMA,
    NODE_BUDGET_V1_CONFIG,
    NODE_BUDGET_V2_CONFIG,
    STATUS_NODE_MINIMAL_LEGACY,
    STATUS_NODE_WITH_UNKNOWN,
)
from test_contract_baseline import (  # noqa: E402
    STATUS_NODE_KNOWN_KEYS,
    _assert_matches_def,
    _install_budget_dir,
    _load_schema,
)


class NodeBudgetSummarySchemaTests(unittest.TestCase):
    def test_schema_file_is_json_schema_with_required_fields(self):
        self.assertTrue(NODE_BUDGET_SUMMARY_SCHEMA.is_file())
        schema = _load_schema(NODE_BUDGET_SUMMARY_SCHEMA)
        self.assertIn("properties", schema)
        for key in ("contract_version", "observed_at", "source", "capacity",
                    "can_accept", "reason_codes"):
            self.assertIn(key, schema["properties"])
        self.assertEqual(schema["properties"]["enforce"].get("default"), False)
        self.assertEqual(
            schema["properties"]["source"]["enum"],
            ["local-ledger", "cli-reported", "manual", "unavailable"])


class WriteStatusBudgetEmbedTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="kp-budget-sum-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.environ["AGENT_BUDGET_DIR"] = str(self.dir)
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)

    def test_write_status_embeds_budget_from_nodebudget_unlimited(self):
        # 設定無し = unlimited（local-ledger 観測・enforce false）
        state_root = Path(tempfile.mkdtemp(prefix="kp-status-"))
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)
        cfg = cfg_for(state_root, node="pc-A", watch=True, level="assisted")
        km.ensure_dirs(cfg)
        km.write_status(cfg)
        rec = json.loads((state_root / "status" / "pc-a.json").read_text(encoding="utf-8"))
        budget = rec["budget"]
        _assert_matches_def(self, budget, _load_schema(NODE_BUDGET_SUMMARY_SCHEMA),
                            label="unlimited")
        self.assertEqual(budget["source"], "local-ledger")
        self.assertTrue(budget["can_accept"])
        self.assertEqual(budget["reason_codes"], ["unlimited"])
        self.assertIs(budget["enforce"], False)
        self.assertEqual(budget["contract_version"], 1)
        self.assertEqual(budget["workload"], "project")
        self.assertEqual(budget["observed_at"], rec["updated_iso"])

    def test_write_status_embeds_capacity_from_v2_ledger(self):
        _install_budget_dir(self.dir, NODE_BUDGET_V2_CONFIG, NODE_BUDGET_LEDGER_V2)
        state_root = Path(tempfile.mkdtemp(prefix="kp-status-v2-"))
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)
        cfg = cfg_for(state_root, node="node-b", watch=True)
        km.ensure_dirs(cfg)
        km.write_status(cfg)
        budget = json.loads(
            (state_root / "status" / "node-b.json").read_text(encoding="utf-8"))["budget"]
        _assert_matches_def(self, budget, _load_schema(NODE_BUDGET_SUMMARY_SCHEMA),
                            label="v2")
        self.assertEqual(budget["source"], "local-ledger")
        self.assertIn(budget["unit"], ("tokens", "seconds"))
        self.assertIsNotNone(budget["capacity"]["limit"])
        self.assertGreaterEqual(budget["capacity"]["used"], 0)
        self.assertEqual(budget["capacity"]["reserved"], 0.0)
        self.assertIs(budget["enforce"], False)
        # 実効上限は config 射影（project に max_tokens / minutes）
        self.assertIn("project", budget["workloads"])
        self.assertTrue(budget["can_accept"] in (True, False))
        self.assertTrue(budget["reason_codes"])

    def test_write_status_v1_time_capacity_unit_seconds(self):
        _install_budget_dir(self.dir, NODE_BUDGET_V1_CONFIG, [])
        state_root = Path(tempfile.mkdtemp(prefix="kp-status-v1-"))
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)
        cfg = cfg_for(state_root, node="pc-v1")
        km.ensure_dirs(cfg)
        km.write_status(cfg)
        budget = json.loads(
            (state_root / "status" / "pc-v1.json").read_text(encoding="utf-8"))["budget"]
        self.assertEqual(budget["unit"], "seconds")
        self.assertGreater(budget["capacity"]["limit"], 0)
        self.assertIs(budget["enforce"], False)

    def test_legacy_viewer_peer_nodes_ignores_budget_and_unknown(self):
        """旧 viewer（_peer_nodes）は budget / 未知キーがあっても生存一覧だけ返す。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            mk_state_repo(d)
            sd = d / "status"
            sd.mkdir()
            (sd / "pc-peer.json").write_text(
                json.dumps(STATUS_NODE_MINIMAL_LEGACY, ensure_ascii=False),
                encoding="utf-8")
            (sd / "pc-a.json").write_text(
                json.dumps(STATUS_NODE_WITH_UNKNOWN, ensure_ascii=False),
                encoding="utf-8")
            cfg = cfg_for(d, node="pc-a")
            at = datetime(2026, 8, 1, 12, 0, 30, tzinfo=timezone.utc)
            peers = km._peer_nodes(cfg, at=at)
            self.assertEqual(peers, {"pc-peer"})

    def test_status_json_and_node_file_share_same_budget_body(self):
        state_root = Path(tempfile.mkdtemp(prefix="kp-status-both-"))
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)
        cfg = cfg_for(state_root, node="shared-n")
        km.ensure_dirs(cfg)
        km.write_status(cfg)
        root = json.loads((state_root / "status.json").read_text(encoding="utf-8"))
        node = json.loads(
            (state_root / "status" / "shared-n.json").read_text(encoding="utf-8"))
        self.assertEqual(root["budget"], node["budget"])
        for key in STATUS_NODE_KNOWN_KEYS:
            self.assertIn(key, root)
            self.assertIn(key, node)
