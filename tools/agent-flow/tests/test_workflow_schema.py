"""agent-flow の単体テスト — ワークフロー定義スキーマと実装の突き合わせ。

`schemas/agent-workflow.schema.json` は契約の正典（文書）で、グラフ不変条件は
実装（`plan_strategy_user` / dashboard の `normalizeWorkflow`）が強制する。
両者がずれると「スキーマには合うのに実行が拒む / その逆」が起きるので、
**機械で突き合わせられる部分（enum・上限・既定値）だけをここで固定する**。
検証ライブラリ（jsonschema）はこのリポジトリでは使わない方針なので、
スキーマは読み取って値を比べる（mission.schema.json と同じ流儀）。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）

REPO_ROOT = HERE.parent.parent.parent
SCHEMA = json.loads((REPO_ROOT / "schemas" / "agent-workflow.schema.json").read_text(encoding="utf-8"))


class WorkflowSchemaAgreementTests(unittest.TestCase):
    def test_node_kind_enum_matches_the_engine_contract(self):
        self.assertEqual(set(SCHEMA["$defs"]["nodeKind"]["enum"]), set(kf.VALID_KINDS))

    def test_plan_node_limit_matches_the_engine_guard(self):
        self.assertEqual(SCHEMA["$defs"]["plan"]["properties"]["nodes"]["maxItems"],
                         kf._USER_PLAN_MAX_NODES)

    def test_dependency_input_enum_matches_the_engine(self):
        # plan_strategy_user / _coerce_tasks が受けるのはこの 2 値だけ
        self.assertEqual(set(SCHEMA["$defs"]["planNode"]["properties"]["dependency_input"]["enum"]),
                         {"full", "digest"})

    def test_decision_fact_types_match_the_engine_contract(self):
        facts = SCHEMA["$defs"]["decision"]["properties"]["facts"]["items"]["properties"]
        self.assertEqual(set(facts["type"]["enum"]), set(kf._nodecontract.FACT_TYPES))

    def test_decision_ops_match_the_engine_contract(self):
        """スキーマの op と decide_candidates が実際に解釈する op を揃える。"""
        dec = SCHEMA["$defs"]["decision"]["properties"]
        self.assertEqual(set(dec["criteria"]["items"]["properties"]["op"]["enum"]), {"eq", "ne"})
        self.assertEqual(set(dec["tie_break"]["properties"]["op"]["enum"]), {"min", "max"})
        facts = [{"id": "a", "n": 1}, {"id": "b", "n": 2}]
        self.assertEqual(kf._nodecontract.decide_candidates(
            [{"fact": "n", "op": "ne", "value": 1}], facts)["kept"], ["b"])
        self.assertEqual(kf._nodecontract.decide_candidates(
            [], facts, tie_break={"fact": "n", "op": "max"})["winner"], "b")

    def test_plan_defaults_match_the_engine(self):
        plan = SCHEMA["$defs"]["plan"]["properties"]
        self.assertEqual(plan["review"]["default"], "auto")
        self.assertIs(plan["evaluate"]["default"], False)

    def test_schema_shaped_plan_is_accepted_by_the_engine(self):
        """スキーマどおりの plan が実装に通ること（文書と実行の突き合わせ）。"""
        plan = {
            "name": "サンプル",
            "nodes": [
                {"id": "a", "goal": "作る", "kind": "work", "deps": [], "tier": "small",
                 "readonly": False, "agent": {"agent_cli": "kiro", "model": "m"},
                 "dependency_input": "digest", "retries": 1},
                {"id": "b", "goal": "確かめる", "kind": "verify", "deps": ["a"]},
            ],
            "review": "auto",
        }
        strategy, tasks = kf.plan_strategy_user(plan, "要求")
        self.assertEqual([t["id"] for t in tasks], ["a", "b"])
        self.assertEqual(strategy["patterns"], ["user-defined"])

    def test_engine_rejects_what_the_schema_forbids(self):
        """スキーマが禁じる形は実装も拒む（両者が同じ方向を向いていること）。"""
        base = [{"id": "a", "goal": "g", "kind": "work", "deps": [], "tier": "small"}]
        cases = {
            "未知の kind": [{"id": "a", "goal": "g", "kind": "no-such-kind", "deps": []}],
            "human に tier": [{"id": "a", "goal": "g", "kind": "human", "deps": [],
                               "tier": "small", "interaction": {"mode": "approval", "prompt": "p"}}],
            "human に agent": [{"id": "a", "goal": "g", "kind": "human", "deps": [],
                                "agent": {"agent_cli": "kiro"},
                                "interaction": {"mode": "approval", "prompt": "p"}}],
            "id 重複": base + [{"id": "a", "goal": "g2", "kind": "work", "deps": []}],
            "未知の依存": [{"id": "a", "goal": "g", "kind": "work", "deps": ["nope"]}],
        }
        for label, nodes in cases.items():
            with self.subTest(label):
                with self.assertRaises(kf.UserPlanError):
                    kf.plan_strategy_user({"nodes": nodes}, "要求")
        # ノード数の上限もスキーマ（maxItems）と同じところで弾く
        too_many = [{"id": f"n{i}", "goal": "g", "kind": "work", "deps": []}
                    for i in range(kf._USER_PLAN_MAX_NODES + 1)]
        with self.assertRaises(kf.UserPlanError):
            kf.plan_strategy_user({"nodes": too_many}, "要求")

    def test_review_accepts_only_the_declared_three_values(self):
        nodes = [{"id": "a", "goal": "g", "kind": "work", "deps": [], "tier": "small"}]
        for value in (True, False, "auto"):
            strategy, _ = kf.plan_strategy_user({"nodes": nodes, "review": value}, "要求")
            self.assertIn("review", strategy)
        with self.assertRaises(kf.UserPlanError):
            kf.plan_strategy_user({"nodes": nodes, "review": "yes"}, "要求")


class BundledWorkflowTests(unittest.TestCase):
    """同梱フロー（workflows/*.json）がスキーマの必須項目を満たすこと。"""

    def _bundled(self):
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((REPO_ROOT / "workflows").glob("*.json"))]

    def test_bundled_workflows_declare_the_required_fields(self):
        required = SCHEMA["required"]
        kinds = set(SCHEMA["$defs"]["nodeKind"]["enum"])
        purposes = set(SCHEMA["$defs"]["purpose"]["enum"])
        workflows = self._bundled()
        self.assertTrue(workflows, "同梱フローが 1 件も無い")
        for wf in workflows:
            with self.subTest(wf.get("id")):
                for key in required:
                    self.assertIn(key, wf)
                self.assertEqual(wf["version"], 2)
                self.assertIn(wf["purpose"], purposes)
                for node in wf["nodes"]:
                    self.assertIn(node["kind"], kinds)
                    # human 以外は tier が要る（スキーマの if/then と同じ規則）
                    if node["kind"] != "human":
                        self.assertIn("tier", node, node["id"])

    def test_bundled_design_workflows_follow_the_design_constraints(self):
        for wf in self._bundled():
            if wf.get("purpose") != "design":
                continue
            with self.subTest(wf.get("id")):
                self.assertEqual(len(wf["exit"]), 1)
                self.assertFalse({n["kind"] for n in wf["nodes"]} & {"human", "split"})


if __name__ == "__main__":
    unittest.main()
