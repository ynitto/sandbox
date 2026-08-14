"""Execution Resolver（E1）の契約テスト。

fixture は段A の正典 schema の examples（schemas/agent-control.schema.json）。
受入は設計書 §15.1 の共通契約に対応する。
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib

import pytest

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


@pytest.fixture()
def control() -> dict:
    """schema examples[0] — dual-write 中の version 2 control（正典 fixture）。"""
    schema = json.loads(CONTROL_SCHEMA.read_text(encoding="utf-8"))
    return copy.deepcopy(schema["examples"][0])


def resolve(control, **kwargs):
    kwargs.setdefault("execution_contract", CONTRACT)
    kwargs.setdefault("now", NOW)
    return resolve_execution("flow", compiled_control=control, **kwargs)


# --- 再現性と自動選択 -----------------------------------------------------------------


def test_same_input_same_decision(control):
    first = resolve(control)
    second = resolve(control)
    assert first == second
    assert first["selected"] == {"agent_cli": "aider", "model": "gemma4:e4b"}
    assert first["selection_source"] == "qualified-candidate"
    assert first["rank"] == 1
    assert first["control_revision"] == 42
    assert first["qualification_revision"] == 12
    assert first["gate"] == "verification-command"


def test_availability_exclusion_moves_to_rank2_not_legacy(control):
    # dual-write の legacy fallback（workload 直下）と rank1 は同じ候補。rank1 が
    # 使えないとき rank2 へ進む＝legacy を二重適用していないことの確認（§6.6）。
    decision = resolve(control, unavailable={"aider/gemma4:e4b"})
    assert decision["selected"] == {"agent_cli": "cursor", "model": "grok-4.5"}
    assert decision["selection_source"] == "qualified-candidate"
    assert decision["fallback_candidates"] == []


def test_all_candidates_unavailable_parks_without_downgrade(control):
    decision = resolve(control, unavailable={"aider/gemma4:e4b", "cursor/grok-4.5"})
    assert decision["parked"] is True
    assert decision["park_reason"] == "no-eligible-candidate"
    assert decision["selected"] is None


def test_equal_rank_uses_configured_order(control):
    policy = control["workloads"]["flow"]["selection_policy"]
    for candidate in policy["candidates"]:
        candidate["rank"] = 1
    decision = resolve(control)
    assert decision["selected"]["agent_cli"] == "aider"  # 配列順（利用者設定順）


def test_blocked_or_trial_status_not_auto_selected(control):
    policy = control["workloads"]["flow"]["selection_policy"]
    policy["candidates"][0]["status"] = "trial"
    decision = resolve(control)
    assert decision["selected"] == {"agent_cli": "cursor", "model": "grok-4.5"}


def test_retry_exhausted_candidate_excluded(control):
    # retry_limit=1 → 許容 attempt は 2。2 回失敗済みの rank1 は除外される。
    decision = resolve(control, attempt_counts={"aider/gemma4:e4b": 2})
    assert decision["selected"] == {"agent_cli": "cursor", "model": "grok-4.5"}


def test_broken_policy_parks_instead_of_legacy(control):
    control["workloads"]["flow"]["selection_policy"]["candidates"] = []
    decision = resolve(control)
    assert decision["parked"] is True
    assert decision["park_reason"] == "invalid-selection-policy"


# --- pin の非迂回 ---------------------------------------------------------------------


def test_pin_cannot_bypass_hard_budget(control):
    decision = resolve(control, budget_state={"hard_exhausted": True},
                       explicit_pin={"agent_cli": "aider", "model": "gemma4:e4b"})
    assert decision["parked"] is True
    assert decision["park_reason"] == "hard-budget-exhausted"


def test_pin_cannot_bypass_lifecycle(control):
    control["workloads"]["flow"]["lifecycle"] = "stop"
    decision = resolve(control, explicit_pin={"agent_cli": "aider", "model": "gemma4:e4b"})
    assert decision["parked"] is True
    assert decision["park_reason"] == "lifecycle-stop"


def test_pin_of_policy_candidate_is_explicit_pin(control):
    decision = resolve(control, explicit_pin={"agent_cli": "cursor", "model": "grok-4.5"})
    assert decision["selected"] == {"agent_cli": "cursor", "model": "grok-4.5"}
    assert decision["selection_source"] == "explicit-pin"
    assert decision["gate"] == "verification-command"  # pin でも gate は落ちない


def test_pin_outside_policy_needs_trial_approval(control):
    unapproved = resolve(control, explicit_pin={"agent_cli": "codex", "model": "gpt-6"})
    assert unapproved["parked"] is True
    assert unapproved["park_reason"] == "pin-not-qualified"
    approved = resolve(control, explicit_pin={
        "agent_cli": "codex", "model": "gpt-6", "trial_approved": True})
    assert approved["selection_source"] == "trial-candidate"


def test_pin_tier_needs_ceiling_override(control):
    pin = {"agent_cli": "codex", "model": "gpt-6", "tier": "large", "trial_approved": True}
    blocked = resolve(control, explicit_pin=pin)  # workload tier = medium
    assert blocked["parked"] is True
    assert blocked["park_reason"] == "pin-exceeds-tier"
    allowed = resolve(control, explicit_pin={**pin, "tier_ceiling_override": "large"})
    assert allowed["selected"] == {"agent_cli": "codex", "model": "gpt-6"}


def test_pin_of_blocked_status_candidate_never_runs(control):
    control["workloads"]["flow"]["selection_policy"]["candidates"][0]["status"] = "blocked"
    decision = resolve(control, explicit_pin={
        "agent_cli": "aider", "model": "gemma4:e4b", "trial_approved": True})
    assert decision["parked"] is True
    assert decision["park_reason"] == "pin-not-qualified"


def test_pin_retry_exhausted_parks(control):
    decision = resolve(control, attempt_counts={"aider/gemma4:e4b": 2},
                       explicit_pin={"agent_cli": "aider", "model": "gemma4:e4b"})
    assert decision["parked"] is True
    assert decision["park_reason"] == "pin-retry-exhausted"


# --- control の期限と version ---------------------------------------------------------


def test_expired_control_parks(control):
    decision = resolve(control, now=dt.datetime(2026, 8, 15, 12, 0, 1,
                                                tzinfo=dt.timezone.utc))
    assert decision["parked"] is True
    assert decision["park_reason"] == "control-expired"


def test_unknown_version_parks(control):
    control["version"] = 3
    decision = resolve(control)
    assert decision["parked"] is True
    assert decision["park_reason"] == "unsupported-control-version"


# --- legacy 経路（selection_policy が無いときだけ）------------------------------------


def test_legacy_workload_single(control):
    del control["workloads"]["flow"]["selection_policy"]
    control["version"] = 1
    decision = resolve(control)
    assert decision["selected"] == {"agent_cli": "aider", "model": "gemma4:e4b"}
    assert decision["selection_source"] == "legacy-fallback"


def test_legacy_purpose_override_wins(control):
    del control["workloads"]["flow"]["selection_policy"]
    control["version"] = 1
    control["workloads"]["flow"]["agents"] = {"review": {"model": "gemma4:12b"}}
    decision = resolve(control, purpose_or_role="review")
    assert decision["selected"] == {"agent_cli": "aider", "model": "gemma4:12b"}


def test_legacy_profiles_default_is_last(control):
    control["workloads"] = {"flow": {}}
    control["version"] = 1
    decision = resolve(control, profiles_default={"agent_cli": "ollama", "model": "gemma4:e4b"})
    assert decision["selected"] == {"agent_cli": "ollama", "model": "gemma4:e4b"}
    none = resolve({"version": 1, "workloads": {"flow": {}}})
    assert none["parked"] is True and none["park_reason"] == "no-candidate"


def test_legacy_unavailable_parks_not_downgrades(control):
    del control["workloads"]["flow"]["selection_policy"]
    control["version"] = 1
    decision = resolve(control, unavailable={"aider/gemma4:e4b"})
    assert decision["parked"] is True
    assert decision["park_reason"] == "legacy-unavailable"


# --- receipt への写像 -----------------------------------------------------------------


def test_decision_fills_receipt_block(control):
    decision = resolve(control)
    receipt = {
        "attempt_id": "node-7:aider-gemma4-e4b:1",
        "execution_decision": receipt_execution_decision(decision),
        "verification": {"kind": "command", "verdict": "pass", "attempt": 1},
        "resource_snapshot": {"budget_remaining": 0.63},
    }
    assert execution_receipt_errors(receipt) == []
    block = receipt["execution_decision"]
    assert block["agent_cli"] == "aider" and block["model"] == "gemma4:e4b"
    assert block["reason"] and block["selection_source"] == "qualified-candidate"
    assert block["eligible_candidate_ids"] == ["aider/gemma4:e4b", "cursor/grok-4.5"]
