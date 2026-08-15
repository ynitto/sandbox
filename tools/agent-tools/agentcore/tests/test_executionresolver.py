"""Execution Resolver（E1）の契約テスト。

fixture は段A の正典 schema の examples（schemas/agent-control.schema.json）。
受入は設計書 §15.1 の共通契約に対応する。

テスト形式は同じ段の test_executioncontract.py と揃えて unittest にする——CI は
`python -m unittest discover` で走らせるので、pytest 形式で書くと収集されない
（import が通っても関数テストが黙って実行されない）。
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib
import unittest

from agentcore.executioncontract import execution_receipt_errors
from agentcore.executionresolver import receipt_execution_decision, resolve_execution

CONTROL_SCHEMA = pathlib.Path(__file__).parents[4] / "schemas" / "agent-control.schema.json"

NOW = dt.datetime(2026, 8, 15, 6, 0, tzinfo=dt.timezone.utc)
CONTRACT = {
    "operation_class": "existing-test-repair",
    "scope": {"read": ["src/format.py", "tests/test_format.py"], "write": ["src/format.py"]},
    "deliverables": ["src/format.py"],
    "acceptance": ["pytest tests/test_format.py が成功する"],
    "verification": {"commands": [["pytest", "tests/test_format.py"]]},
}


class ResolverTestCase(unittest.TestCase):
    """schema examples[0] — dual-write 中の version 2 control（正典 fixture）。"""

    def setUp(self):
        schema = json.loads(CONTROL_SCHEMA.read_text(encoding="utf-8"))
        self.control = copy.deepcopy(schema["examples"][0])

    def resolve(self, control=None, **kwargs):
        kwargs.setdefault("execution_contract", CONTRACT)
        kwargs.setdefault("now", NOW)
        return resolve_execution(
            "flow", compiled_control=self.control if control is None else control, **kwargs)


# --- 再現性と自動選択 -----------------------------------------------------------------


class AutoSelectionTests(ResolverTestCase):
    def test_same_input_same_decision(self):
        first = self.resolve()
        second = self.resolve()
        self.assertEqual(first, second)
        self.assertEqual(first["selected"], {"agent_cli": "aider", "model": "gemma4:e4b"})
        self.assertEqual(first["selection_source"], "qualified-candidate")
        self.assertEqual(first["rank"], 1)
        self.assertEqual(first["control_revision"], 42)
        self.assertEqual(first["qualification_revision"], 12)
        self.assertEqual(first["gate"], "verification-command")

    def test_availability_exclusion_moves_to_rank2_not_legacy(self):
        # dual-write の legacy fallback（workload 直下）と rank1 は同じ候補。rank1 が
        # 使えないとき rank2 へ進む＝legacy を二重適用していないことの確認（§6.6）。
        decision = self.resolve(unavailable={"aider/gemma4:e4b"})
        self.assertEqual(decision["selected"], {"agent_cli": "cursor", "model": "grok-4.5"})
        self.assertEqual(decision["selection_source"], "qualified-candidate")
        self.assertEqual(decision["fallback_candidates"], [])

    def test_all_candidates_unavailable_parks_without_downgrade(self):
        decision = self.resolve(unavailable={"aider/gemma4:e4b", "cursor/grok-4.5"})
        self.assertIs(decision["parked"], True)
        self.assertEqual(decision["park_reason"], "no-eligible-candidate")
        self.assertIsNone(decision["selected"])

    def test_equal_rank_uses_configured_order(self):
        policy = self.control["workloads"]["flow"]["selection_policy"]
        for candidate in policy["candidates"]:
            candidate["rank"] = 1
        decision = self.resolve()
        self.assertEqual(decision["selected"]["agent_cli"], "aider")  # 配列順（利用者設定順）

    def test_blocked_or_trial_status_not_auto_selected(self):
        policy = self.control["workloads"]["flow"]["selection_policy"]
        policy["candidates"][0]["status"] = "trial"
        decision = self.resolve()
        self.assertEqual(decision["selected"], {"agent_cli": "cursor", "model": "grok-4.5"})

    def test_retry_exhausted_candidate_excluded(self):
        # retry_limit=1 → 許容 attempt は 2。2 回失敗済みの rank1 は除外される。
        decision = self.resolve(attempt_counts={"aider/gemma4:e4b": 2})
        self.assertEqual(decision["selected"], {"agent_cli": "cursor", "model": "grok-4.5"})

    def test_broken_policy_parks_instead_of_legacy(self):
        self.control["workloads"]["flow"]["selection_policy"]["candidates"] = []
        decision = self.resolve()
        self.assertIs(decision["parked"], True)
        self.assertEqual(decision["park_reason"], "invalid-selection-policy")


# --- pin の非迂回 ---------------------------------------------------------------------


class PinTests(ResolverTestCase):
    def test_pin_cannot_bypass_hard_budget(self):
        decision = self.resolve(budget_state={"hard_exhausted": True},
                                explicit_pin={"agent_cli": "aider", "model": "gemma4:e4b"})
        self.assertIs(decision["parked"], True)
        self.assertEqual(decision["park_reason"], "hard-budget-exhausted")

    def test_pin_cannot_bypass_lifecycle(self):
        self.control["workloads"]["flow"]["lifecycle"] = "stop"
        decision = self.resolve(explicit_pin={"agent_cli": "aider", "model": "gemma4:e4b"})
        self.assertIs(decision["parked"], True)
        self.assertEqual(decision["park_reason"], "lifecycle-stop")

    def test_pin_of_policy_candidate_is_explicit_pin(self):
        decision = self.resolve(explicit_pin={"agent_cli": "cursor", "model": "grok-4.5"})
        self.assertEqual(decision["selected"], {"agent_cli": "cursor", "model": "grok-4.5"})
        self.assertEqual(decision["selection_source"], "explicit-pin")
        self.assertEqual(decision["gate"], "verification-command")  # pin でも gate は落ちない

    def test_pin_outside_policy_needs_trial_approval(self):
        unapproved = self.resolve(explicit_pin={"agent_cli": "codex", "model": "gpt-6"})
        self.assertIs(unapproved["parked"], True)
        self.assertEqual(unapproved["park_reason"], "pin-not-qualified")
        approved = self.resolve(explicit_pin={
            "agent_cli": "codex", "model": "gpt-6", "trial_approved": True})
        self.assertEqual(approved["selection_source"], "trial-candidate")

    def test_pin_tier_needs_ceiling_override(self):
        pin = {"agent_cli": "codex", "model": "gpt-6", "tier": "large", "trial_approved": True}
        blocked = self.resolve(explicit_pin=pin)  # workload tier = medium
        self.assertIs(blocked["parked"], True)
        self.assertEqual(blocked["park_reason"], "pin-exceeds-tier")
        allowed = self.resolve(explicit_pin={**pin, "tier_ceiling_override": "large"})
        self.assertEqual(allowed["selected"], {"agent_cli": "codex", "model": "gpt-6"})

    def test_policy_trial_candidate_needs_envelope_approval(self):
        # Compiler が trial 裏付けのみの候補へ status: trial を明記する。自動選択からは
        # 除外され、pin だけでも走らず、Envelope の trial 承認がある run でだけ選択できる。
        policy = self.control["workloads"]["flow"]["selection_policy"]
        policy["candidates"].append({"agent_cli": "ollama", "model": "gemma4:12b",
                                     "rank": 3, "status": "trial",
                                     "qualification_refs": ["ollama-12b-review-trial"]})
        auto = self.resolve(unavailable={"aider/gemma4:e4b", "cursor/grok-4.5"})
        self.assertIs(auto["parked"], True)                 # trial へ黙って降格しない
        pin = {"agent_cli": "ollama", "model": "gemma4:12b"}
        unapproved = self.resolve(explicit_pin=pin)
        self.assertEqual(unapproved["park_reason"], "pin-not-qualified")
        approved = self.resolve(explicit_pin={**pin, "trial_approved": True})
        self.assertEqual(approved["selection_source"], "trial-candidate")
        self.assertEqual(approved["rank"], 3)
        self.assertEqual(approved["qualification_id"], "ollama-12b-review-trial")

    def test_pin_of_blocked_status_candidate_never_runs(self):
        # blocked は policy に載せてよい status ではない（Compiler が落とす契約）。
        # 紛れ込んだ場合は policy 全体が不正 = park——trial 承認付き pin でも実行されない。
        self.control["workloads"]["flow"]["selection_policy"]["candidates"][0]["status"] = "blocked"
        decision = self.resolve(explicit_pin={
            "agent_cli": "aider", "model": "gemma4:e4b", "trial_approved": True})
        self.assertIs(decision["parked"], True)
        self.assertEqual(decision["park_reason"], "invalid-selection-policy")

    def test_pin_retry_exhausted_parks(self):
        decision = self.resolve(attempt_counts={"aider/gemma4:e4b": 2},
                                explicit_pin={"agent_cli": "aider", "model": "gemma4:e4b"})
        self.assertIs(decision["parked"], True)
        self.assertEqual(decision["park_reason"], "pin-retry-exhausted")


# --- control の期限と version ---------------------------------------------------------


class ControlValidityTests(ResolverTestCase):
    def test_expired_control_parks(self):
        decision = self.resolve(now=dt.datetime(2026, 8, 15, 12, 0, 1,
                                                tzinfo=dt.timezone.utc))
        self.assertIs(decision["parked"], True)
        self.assertEqual(decision["park_reason"], "control-expired")

    def test_unknown_version_parks(self):
        self.control["version"] = 3
        decision = self.resolve()
        self.assertIs(decision["parked"], True)
        self.assertEqual(decision["park_reason"], "unsupported-control-version")


# --- legacy 経路（selection_policy が無いときだけ）------------------------------------


class LegacyFallbackTests(ResolverTestCase):
    def test_legacy_workload_single(self):
        del self.control["workloads"]["flow"]["selection_policy"]
        self.control["version"] = 1
        decision = self.resolve()
        self.assertEqual(decision["selected"], {"agent_cli": "aider", "model": "gemma4:e4b"})
        self.assertEqual(decision["selection_source"], "legacy-fallback")

    def test_legacy_purpose_override_wins(self):
        del self.control["workloads"]["flow"]["selection_policy"]
        self.control["version"] = 1
        self.control["workloads"]["flow"]["agents"] = {"review": {"model": "gemma4:12b"}}
        decision = self.resolve(purpose_or_role="review")
        self.assertEqual(decision["selected"], {"agent_cli": "aider", "model": "gemma4:12b"})

    def test_legacy_profiles_default_is_last(self):
        self.control["workloads"] = {"flow": {}}
        self.control["version"] = 1
        decision = self.resolve(profiles_default={"agent_cli": "ollama", "model": "gemma4:e4b"})
        self.assertEqual(decision["selected"], {"agent_cli": "ollama", "model": "gemma4:e4b"})
        none = self.resolve({"version": 1, "workloads": {"flow": {}}})
        self.assertIs(none["parked"], True)
        self.assertEqual(none["park_reason"], "no-candidate")

    def test_legacy_unavailable_parks_not_downgrades(self):
        del self.control["workloads"]["flow"]["selection_policy"]
        self.control["version"] = 1
        decision = self.resolve(unavailable={"aider/gemma4:e4b"})
        self.assertIs(decision["parked"], True)
        self.assertEqual(decision["park_reason"], "legacy-unavailable")


# --- receipt への写像 -----------------------------------------------------------------


class ReceiptMappingTests(ResolverTestCase):
    def test_decision_fills_receipt_block(self):
        decision = self.resolve()
        receipt = {
            "attempt_id": "node-7:aider-gemma4-e4b:1",
            "execution_decision": receipt_execution_decision(decision),
            "verification": {"kind": "command", "verdict": "pass", "attempt": 1},
            "resource_snapshot": {"budget_remaining": 0.63},
        }
        self.assertEqual(execution_receipt_errors(receipt), [])
        block = receipt["execution_decision"]
        self.assertEqual(block["agent_cli"], "aider")
        self.assertEqual(block["model"], "gemma4:e4b")
        self.assertTrue(block["reason"])
        self.assertEqual(block["selection_source"], "qualified-candidate")
        self.assertEqual(block["eligible_candidate_ids"], ["aider/gemma4:e4b", "cursor/grok-4.5"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
