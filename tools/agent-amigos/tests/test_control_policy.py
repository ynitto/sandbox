# 候補ベース実行（agent-control version 2 selection_policy → agentcore Resolver）の
# agent-amigos 側統合テスト（実装計画 2026-08-15 E4）。
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
from _shared import AmigosTestCase, base_spec  # noqa: E402

from agent_amigos import agentcli, control  # noqa: E402
from agent_amigos.mission import load_roles, role_operation_contract  # noqa: E402
from agent_amigos.runner import AmigoRunner  # noqa: E402


POLICY = {
    "strategy": "economy", "retry_limit": 1, "no_candidate": "park",
    "qualification_revision": 3,
    "candidates": [
        {"agent_cli": "ollama", "model": "gemma4:e4b", "rank": 1,
         "qualification_refs": ["ollama-gemma4-e4b-role-turn-v1"]},
    ],
}


class _ControlCase(AmigosTestCase):
    def setUp(self):
        super().setUp()
        self.control_dir = os.path.join(self.tmp, "control")
        os.makedirs(self.control_dir, exist_ok=True)
        os.environ["AGENT_CONTROL_DIR"] = self.control_dir
        self.addCleanup(os.environ.pop, "AGENT_CONTROL_DIR", None)
        control._CACHE["mtime"] = None
        self.addCleanup(control._CACHE.__setitem__, "mtime", None)

    def _control(self, ctl: dict) -> None:
        with open(os.path.join(self.control_dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump(ctl, f)
        control._CACHE["mtime"] = None

    def _runner(self) -> AmigoRunner:
        mid = self.post()
        return AmigoRunner(self.bus, mid, "architect", "owner-node", agent_cli="stub")


class ControlPolicyTests(_ControlCase):
    def test_v1_policy_field_is_ignored(self):
        self._control({"version": 1, "workloads": {"amigos": {"selection_policy": POLICY}}})
        self.assertIsNone(control.policy_decision("architect"))

    def test_policy_replaces_override_layer(self):
        self._control({"version": 2, "revision": 9,
                       "workloads": {"amigos": {"agent_cli": "legacy-cli",
                                                "selection_policy": POLICY}}})
        runner = self._runner()
        cli, model = runner._resolve_cli({"id": "architect"}, {})
        self.assertEqual((cli, model), ("ollama", "gemma4:e4b"))
        block = runner._policy_decision
        self.assertEqual(block["selection_source"], "qualified-candidate")

    def test_park_is_control_classified_env_error(self):
        ctl = {"version": 2, "workloads": {"amigos": {"selection_policy": dict(POLICY, candidates=[])}}}
        self._control(ctl)
        runner = self._runner()
        with self.assertRaises(RuntimeError) as ctx:
            runner._resolve_cli({"id": "architect"}, {})
        message = str(ctx.exception)
        self.assertIn("[agent-error:control]", message)
        triage = agentcli.classify_agent_failure(message)
        self.assertEqual(triage[0], "control")
        self.assertIn(triage[0], agentcli.AGENT_ERROR_ENV_CLASSES)  # error でなく paused へ

    def test_turn_receipt_carries_decision(self):
        self._control({"version": 2, "revision": 9,
                       "workloads": {"amigos": {"selection_policy": POLICY}}})
        runner = self._runner()
        role = {"id": "architect", "deliverables": ["architecture.md"]}
        cli, model = runner._resolve_cli(role, {})
        rec = runner._turn_receipt(role, 1, 1.5, cli, model, actions=1, rejected=0)
        self.assertEqual(rec["agent_cli"], "ollama")
        self.assertEqual(rec["operation_class"], "role-turn")
        block = rec["execution_decision"]
        self.assertEqual(block["control_revision"], 9)
        self.assertEqual(block["qualification_id"], "ollama-gemma4-e4b-role-turn-v1")
        from agentcore.executioncontract import execution_receipt_errors
        self.assertEqual(execution_receipt_errors(
            {"attempt_id": "am-test/architect:1", "execution_decision": block}), [])

    def test_no_policy_keeps_legacy_and_no_decision_in_receipt(self):
        self._control({"version": 1, "workloads": {"amigos": {"agent_cli": "kiro"}}})
        runner = self._runner()
        role = {"id": "architect"}
        cli, _model = runner._resolve_cli(role, {})
        self.assertEqual(cli, "kiro")  # legacy override はそのまま
        rec = runner._turn_receipt(role, 1, 0.5, cli, None, actions=0, rejected=0)
        self.assertNotIn("execution_decision", rec)


TWO = dict(POLICY, candidates=[
    {"agent_cli": "ollama", "model": "gemma4:e4b", "rank": 1},
    {"agent_cli": "claude", "model": "sonnet", "rank": 2},
])


class PromptSelectionTests(_ControlCase):
    """適格候補が複数のとき、ターンの依頼文を見て 1 件を選ぶ（agentcore.modelselect）。"""

    def setUp(self):
        super().setUp()
        from agentcore import modelselect
        self.seen = []

        def fake_selector(prompt, **kw):
            self.seen.append((prompt, kw.get("purpose")))
            return lambda cands: {"agent_cli": "claude", "model": "sonnet", "stage": "judge",
                                  "confidence": 0.9, "reason": "test"}
        patcher = mock.patch.object(modelselect, "resolver_selector", fake_selector)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prompt_picks_among_policy_candidates_and_receipt_keeps_it(self):
        self._control({"version": 2, "revision": 9, "workloads": {"amigos": {"selection_policy": TWO}}})
        runner = self._runner()
        role = {"id": "architect"}
        cli, model = runner._resolve_cli(role, {}, prompt_fn=lambda: "big design")
        self.assertEqual((cli, model), ("claude", "sonnet"))
        self.assertEqual(self.seen, [("big design", "architect")])
        rec = runner._turn_receipt(role, 1, 1.0, cli, model, actions=0, rejected=0)
        self.assertEqual(rec["execution_decision"]["selector"]["stage"], "judge")

    def test_single_candidate_never_builds_the_prompt(self):
        self._control({"version": 2, "workloads": {"amigos": {"selection_policy": POLICY}}})
        runner = self._runner()

        def boom():
            raise AssertionError("候補が 1 件なら依頼文を組まない")
        cli, _ = runner._resolve_cli({"id": "architect"}, {}, prompt_fn=boom)
        self.assertEqual(cli, "ollama")
        self.assertEqual(self.seen, [])

    def test_without_prompt_keeps_rank_order(self):
        self._control({"version": 2, "workloads": {"amigos": {"selection_policy": TWO}}})
        cli, _ = self._runner()._resolve_cli({"id": "architect"}, {})
        self.assertEqual(cli, "ollama")

    def test_turn_runs_the_same_prompt_the_selector_saw(self):
        self._control({"version": 2, "workloads": {"amigos": {"selection_policy": TWO}}})
        runner = self._runner()
        calls = []

        def fake_run(prompt, cli, model):
            calls.append((prompt, cli, model))
            return '{"actions": []}'
        with mock.patch.object(agentcli, "run_agent", fake_run):
            runner.turn_once()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], ("claude", "sonnet"))
        self.assertEqual(self.seen[0][0], calls[0][0])


class RoleOperationContractTests(AmigosTestCase):
    def test_declared_operation_kept_and_broken_dropped(self):
        spec = base_spec()
        spec["roles"][0]["operation"] = {
            "operation_class": "design-review",
            "scope": {"write": ["artifacts/architect/architecture.md"]}}
        spec["roles"][1]["operation"] = {"scope": "broken"}  # operation_class 欠落 + 型不正
        mid = self.post(spec)
        roles = load_roles(self.bus.mission(mid))
        self.assertEqual(roles["architect"]["operation"]["operation_class"], "design-review")
        self.assertNotIn("operation", roles["impl"])

    def test_auto_contract_from_role_fields(self):
        contract = role_operation_contract(
            {"id": "impl", "deliverables": ["src/main.py"]})
        self.assertEqual(contract["operation_class"], "role-turn")
        self.assertEqual(contract["deliverables"], ["artifacts/impl/src/main.py"])
        self.assertEqual(contract["scope"]["write"], ["artifacts/impl/src/main.py"])
        bare = role_operation_contract({"id": "reviewer"})
        self.assertEqual(bare["operation_class"], "role-turn")
        self.assertNotIn("scope", bare)


if __name__ == "__main__":
    unittest.main()
