"""現行契約の実 fixture 基準線。

node-budget / status/<node>.json / board nodes / decisions・rules 昇格の現行形を
スキーマ（または write_status 現状キー）と突き合わせ、旧ファイルのみ・未知フィールド
additive でも読取ツールが落ちないことを固定する。

Phase1: status の `budget`（node-budget-summary）は write_status が書く既知キー。
allocate/claim/eligible の enforcement は範囲外（既定 false）。
"""
from __future__ import annotations

import os as _os
import sys as _sys
import time

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403
from _contract_fixtures import (  # noqa: E402
    BOARD_NODE_CURRENT,
    BOARD_NODE_MINIMAL,
    BOARD_NODE_WITH_UNKNOWN,
    BOARD_SCHEMA,
    DECISION_OLD_ONLY,
    DECISION_RULES_PROMOTED,
    DECISION_WITH_LEARN,
    NODE_BUDGET_CONFIG_WITH_UNKNOWN,
    NODE_BUDGET_LEDGER_V1,
    NODE_BUDGET_LEDGER_V2,
    NODE_BUDGET_LEDGER_WITH_UNKNOWN,
    NODE_BUDGET_SCHEMA,
    NODE_BUDGET_SUMMARY_SCHEMA,
    NODE_BUDGET_V1_CONFIG,
    NODE_BUDGET_V2_CONFIG,
    RULES_MD_HUMAN_ONLY,
    RULES_MD_WITH_AUTO,
    STATUS_NODE_BUDGET_SUMMARY,
    STATUS_NODE_CURRENT,
    STATUS_NODE_MINIMAL_LEGACY,
    STATUS_NODE_WITH_UNKNOWN,
)

from agentcore import board as boardcore  # noqa: E402
from agentcore import nodebudget as nb  # noqa: E402

# write_status が現状書くキー（status/<node>.json の契約正。budget は summary schema）
STATUS_NODE_KNOWN_KEYS = frozenset({
    "host", "watch", "level", "paused", "node", "availability",
    "updated_iso", "fresh_after_sec", "runtime", "wsl_distro", "budget",
})


def _load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_matches_def(testcase: unittest.TestCase, data: dict, schema_def: dict,
                        *, label: str) -> None:
    """jsonschema 非依存の軽い契約突き合わせ（required・型・enum・additionalProperties）。"""
    for req in schema_def.get("required") or []:
        testcase.assertIn(req, data, f"{label}: required `{req}` 欠落")
    props = schema_def.get("properties") or {}
    addl = schema_def.get("additionalProperties", True)
    for key, value in data.items():
        if key not in props:
            testcase.assertTrue(
                addl is True or isinstance(addl, dict),
                f"{label}: 未知キー `{key}` が additionalProperties=false なのに存在")
            continue
        prop = props[key]
        if "enum" in prop and value is not None:
            testcase.assertIn(value, prop["enum"], f"{label}.{key} enum 外: {value!r}")
        typ = prop.get("type")
        if typ is None or value is None:
            continue
        types = typ if isinstance(typ, list) else [typ]
        pymap = {"string": str, "number": (int, float), "integer": int,
                 "boolean": bool, "array": list, "object": dict}
        flat = []
        for name in types:
            m = pymap.get(name)
            if m is None:
                continue
            flat.extend(m if isinstance(m, tuple) else (m,))
        if flat:
            testcase.assertTrue(isinstance(value, tuple(flat)),
                                f"{label}.{key}={value!r} が型 {types} 外")


def _install_budget_dir(base: Path, config: dict, records: list) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "config.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8")
    led = base / "ledger"
    led.mkdir(exist_ok=True)
    day = time.strftime("%Y%m%d", time.gmtime())
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    (led / f"{day}.jsonl").write_text(body, encoding="utf-8")
    return base


class SchemaFixtureContractTests(unittest.TestCase):
    """実 fixture が現行 schema / status 現状キー集合に適合する。"""

    def test_node_budget_fixtures_match_schema_defs(self):
        defs = _load_schema(NODE_BUDGET_SCHEMA)["$defs"]
        for label, cfg in (
            ("v1", NODE_BUDGET_V1_CONFIG),
            ("v2", NODE_BUDGET_V2_CONFIG),
            ("unknown", NODE_BUDGET_CONFIG_WITH_UNKNOWN),
        ):
            _assert_matches_def(self, cfg, defs["config"], label=f"config/{label}")
        for label, rows in (
            ("v1", NODE_BUDGET_LEDGER_V1),
            ("v2", NODE_BUDGET_LEDGER_V2),
            ("unknown", NODE_BUDGET_LEDGER_WITH_UNKNOWN),
        ):
            for i, row in enumerate(rows):
                _assert_matches_def(self, row, defs["ledger_record"],
                                    label=f"ledger/{label}[{i}]")

    def test_board_node_fixtures_match_schema_def(self):
        node_def = _load_schema(BOARD_SCHEMA)["$defs"]["node"]
        for label, node in (
            ("current", BOARD_NODE_CURRENT),
            ("minimal", BOARD_NODE_MINIMAL),
            ("unknown", BOARD_NODE_WITH_UNKNOWN),
        ):
            _assert_matches_def(self, node, node_def, label=f"node/{label}")

    def test_status_node_current_keys_are_write_status_shape(self):
        # 未知キーを許す（additive）。既知キーは write_status が書く集合に含まれる。
        self.assertTrue(STATUS_NODE_KNOWN_KEYS.issuperset(
            k for k in STATUS_NODE_CURRENT if k in STATUS_NODE_KNOWN_KEYS))
        for key in ("node", "updated_iso", "fresh_after_sec", "availability", "budget"):
            self.assertIn(key, STATUS_NODE_CURRENT)
        for key in ("node", "updated_iso", "fresh_after_sec", "availability"):
            self.assertIn(key, STATUS_NODE_MINIMAL_LEGACY)
        unknown = set(STATUS_NODE_WITH_UNKNOWN) - STATUS_NODE_KNOWN_KEYS
        self.assertIn("knowledge", unknown)
        self.assertNotIn("budget", unknown)

    def test_status_budget_fixture_matches_summary_schema(self):
        schema = _load_schema(NODE_BUDGET_SUMMARY_SCHEMA)
        _assert_matches_def(self, STATUS_NODE_BUDGET_SUMMARY, schema,
                            label="budget-summary/fixture")
        self.assertFalse(STATUS_NODE_BUDGET_SUMMARY.get("enforce", False))


class NodeBudgetReaderBaselineTests(unittest.TestCase):
    """旧 config/ledger のみ・未知キー付きでも agentcore.nodebudget が動く。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="kp-contract-nb-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.environ["AGENT_BUDGET_DIR"] = str(self.dir)
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)

    def test_v1_only_fixture_yields_engine_state(self):
        _install_budget_dir(self.dir, NODE_BUDGET_V1_CONFIG, NODE_BUDGET_LEDGER_V1)
        st = nb.state("project", dir=str(self.dir), view="engine")
        self.assertIsNotNone(st)
        self.assertIn("exceeded", st)
        self.assertFalse(st["exceeded"])
        # agent-project 薄い委譲も同じ dir を見る
        self.assertEqual(km._node_budget_state(), st)

    def test_v2_fixture_with_measured_tokens(self):
        _install_budget_dir(self.dir, NODE_BUDGET_V2_CONFIG, NODE_BUDGET_LEDGER_V2)
        st = nb.state("project", dir=str(self.dir), view="engine")
        self.assertIsNotNone(st)
        self.assertGreaterEqual(st["spent_tokens"], 1600)  # tokens_in+out（観測行は 0）
        acc = nb.can_accept("project", dir=str(self.dir))
        self.assertIn("can_accept", acc)
        self.assertIn("reason_codes", acc)

    def test_unknown_fields_are_additive(self):
        _install_budget_dir(self.dir, NODE_BUDGET_CONFIG_WITH_UNKNOWN,
                            NODE_BUDGET_LEDGER_WITH_UNKNOWN)
        cfg = nb.read_config(str(self.dir))
        self.assertIn("future_budget_summary", cfg)
        st = nb.state("project", dir=str(self.dir), view="engine")
        self.assertIsNotNone(st)
        self.assertFalse(st["exceeded"])


class StatusNodeReaderBaselineTests(unittest.TestCase):
    """status/<node>.json 旧形・未知キーでも peer / write_status 読取が保たれる。"""

    def test_peer_nodes_reads_legacy_and_ignores_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            mk_state_repo(d)
            # 鮮度内のピア（最小旧形）
            sd = d / "status"
            sd.mkdir()
            (sd / "pc-peer.json").write_text(
                json.dumps(STATUS_NODE_MINIMAL_LEGACY, ensure_ascii=False),
                encoding="utf-8")
            # 未知キー付き自ノード形も壊さない
            (sd / "pc-a.json").write_text(
                json.dumps(STATUS_NODE_WITH_UNKNOWN, ensure_ascii=False),
                encoding="utf-8")
            cfg = cfg_for(d, node="pc-a")
            at = datetime(2026, 8, 1, 12, 0, 30, tzinfo=timezone.utc)
            peers = km._peer_nodes(cfg, at=at)
            self.assertEqual(peers, {"pc-peer"})

    def test_write_status_output_covers_fixture_known_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            cfg = cfg_for(d, node="pc-A", watch=True, level="assisted")
            km.ensure_dirs(cfg)
            km.write_status(cfg)
            rec = json.loads((d / "status" / "pc-a.json").read_text(encoding="utf-8"))
            for key in STATUS_NODE_KNOWN_KEYS:
                self.assertIn(key, rec, f"write_status が `{key}` を書いていない")
            budget = rec["budget"]
            schema = _load_schema(NODE_BUDGET_SUMMARY_SCHEMA)
            _assert_matches_def(self, budget, schema, label="write_status/budget")
            self.assertFalse(budget.get("enforce", True),
                             "budget_summary.enforce 既定は false")


class BoardNodeReaderBaselineTests(unittest.TestCase):
    """板上 nodes/<id>.json 現行形・未知キーでも eligible / NodeCapability が動く。"""

    def test_eligible_from_current_fixture_fields(self):
        node = BOARD_NODE_CURRENT
        post = {
            "op": "post", "id": "dg-1", "workload": "flow",
            "workspace": {"url": "https://git.example.com/team/app.git"},
            "requires": {"tags": ["python"], "agent_cli": ["claude"],
                         "contract_version": node["contract_version"]},
        }
        self.assertTrue(boardcore.eligible(
            post,
            repos=node.get("repos"),
            tags=node.get("tags"),
            agent_cli=node.get("agent_cli"),
            workloads=node.get("workloads"),
            contract_version=node.get("contract_version"),
            max_concurrent=node.get("max_concurrent"),
            inflight=0,
        ))

    def test_unknown_fields_do_not_break_eligible_or_capability(self):
        node = BOARD_NODE_WITH_UNKNOWN
        self.assertIn("budget", node)
        cap = km.NodeCapability(
            node=node["node"],
            workloads=list(node.get("workloads") or []),
            tags=list(node.get("tags") or []),
            agent_cli=list(node.get("agent_cli") or []),
            repos=node.get("repos"),
            availability=node.get("availability"),
            max_concurrent=int(node.get("max_concurrent") or 0),
            heartbeat=node.get("heartbeat"),
            fresh_after_sec=node.get("fresh_after_sec"),
            contract_version=int(node.get("contract_version") or 1),
        )
        dumped = cap.to_dict()
        self.assertEqual(dumped["node"], "pc-a")
        self.assertNotIn("budget", dumped)  # 書き手現状は未知キーを載せない
        post = {"op": "post", "id": "dg-2", "workload": "flow"}
        self.assertTrue(boardcore.eligible(
            post, repos=node.get("repos"), tags=node.get("tags"),
            agent_cli=node.get("agent_cli"), workloads=node.get("workloads"),
            contract_version=node.get("contract_version"),
            max_concurrent=node.get("max_concurrent")))

    def test_minimal_legacy_node_is_eligible_without_workloads_key(self):
        node = BOARD_NODE_MINIMAL
        self.assertNotIn("workloads", node)
        self.assertTrue(boardcore.eligible(
            {"op": "post", "id": "dg-3", "workload": "amigos"},
            repos=None, tags=node["tags"], agent_cli=node["agent_cli"],
            workloads=None, contract_version=node["contract_version"],
            max_concurrent=node["max_concurrent"]))


class DecisionsRulesBaselineTests(unittest.TestCase):
    """旧 decisions のみ・昇格済み fixture で stats / recall / rules 注入が動く。"""

    def test_old_decisions_only_tools_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "backlog").mkdir()
            (d / "decisions").mkdir()
            (d / "decisions" / "T1.md").write_text(DECISION_OLD_ONLY, encoding="utf-8")
            (d / "rules.md").write_text(RULES_MD_HUMAN_ONLY, encoding="utf-8")
            cfg = cfg_for(d, learn=False)
            s = km.compute_stats(cfg)
            self.assertEqual(
                (s["rules_injected"], s["learn_hits"], s["promotions"], s["evidence_missing"]),
                (1, 0, 0, 0))
            self.assertEqual(s["actions"].get("feedback-resume"), 1)
            # 注入・recall も旧ファイルで落ちない
            self.assertIn("人手だけ", km.project_rules_context(cfg))
            task = km.Task(id="T9", title="無関係")
            self.assertIsNone(km.find_learned_resolution(cfg, task))

    def test_learn_and_rules_promoted_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "backlog").mkdir()
            (d / "decisions").mkdir()
            (d / "decisions" / "OLD.md").write_text(
                DECISION_RULES_PROMOTED, encoding="utf-8")
            (d / "decisions" / "LEARN.md").write_text(
                DECISION_WITH_LEARN, encoding="utf-8")
            (d / "rules.md").write_text(RULES_MD_WITH_AUTO, encoding="utf-8")
            cfg = cfg_for(d, learn=False, promote_threshold=2)
            s = km.compute_stats(cfg)
            self.assertEqual(s["promotions"], 2)  # rules-promoted + promoted
            self.assertGreaterEqual(s["rules_injected"], 2)
            # 既に rules-promoted 済み → promote_rules は冪等で追加しない
            self.assertEqual(km.promote_rules(cfg), [])
            task = km.Task(id="T9", title="build を直す")
            res = km.find_learned_resolution(cfg, task)
            self.assertIsNotNone(res)
            self.assertEqual(res[1], "make を使え")


if __name__ == "__main__":
    unittest.main()
