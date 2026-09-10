"""Deterministic, fail-closed checks for sandbox-owned external side effects."""
from __future__ import annotations

import hashlib
import json


DECISIONS = frozenset({"allow", "deny", "approval_required"})


def canonical_target(target: dict) -> dict:
    """Return the small, stable git.push target used for approval binding."""
    return {"url": str((target or {}).get("url") or "").strip(),
            "branch": str((target or {}).get("branch") or "").strip()}


def action_digest(action: str, target: dict, envelope_digest: str) -> str:
    value = {"action": str(action), "target": canonical_target(target),
             "envelope_digest": str(envelope_digest or "")}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def envelope_digest_valid(envelope: dict) -> bool:
    if not isinstance(envelope, dict) or not envelope.get("digest"):
        return False
    unsigned = {key: value for key, value in envelope.items() if key != "digest"}
    raw = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() == envelope["digest"]


def decide(action: str, target: dict, envelope: dict,
           approval_resolution: "dict | None" = None) -> dict:
    """Evaluate an approved envelope. Unknown/malformed input always denies."""
    target = canonical_target(target)
    envelope_digest = str((envelope or {}).get("digest") or "")
    receipt = {"action": str(action), "target": target,
               "envelope_digest": envelope_digest,
               "action_digest": action_digest(action, target, envelope_digest),
               "decision": "deny", "outcome": "deny",
               "approval_resolution": "not-applicable"}
    if (not envelope_digest_valid(envelope)
            or (envelope.get("approval") or {}).get("status") != "approved"):
        receipt["reason"] = "approved execution envelope is missing or invalid"
        return receipt
    rule = (envelope.get("egress") or {}).get(action)
    if not isinstance(rule, dict) or str(rule.get("decision") or "") not in DECISIONS:
        receipt["reason"] = "egress action is not present in the approved envelope"
        return receipt
    allowed = rule.get("targets") or []
    matches = any(isinstance(item, dict)
                  and str(item.get("url") or "").strip() == target["url"]
                  and (not str(item.get("branch") or "").strip()
                       or str(item.get("branch")).strip() == target["branch"])
                  for item in allowed)
    if not matches:
        receipt["reason"] = "target is not present in the approved envelope"
        return receipt
    receipt["decision"] = str(rule["decision"])
    if receipt["decision"] == "approval_required":
        resolution = approval_resolution if isinstance(approval_resolution, dict) else {}
        outcome = str(resolution.get("outcome") or "pending")
        bound = str(resolution.get("egress_action_digest") or "") == receipt["action_digest"]
        receipt["approval_resolution"] = outcome if outcome in {
            "approved", "rejected", "pending"} else "pending"
        if outcome == "approved" and bound:
            receipt["outcome"] = "allow"
            receipt["reason"] = "approved by bound human interaction resolution"
        elif outcome == "approved":
            receipt["reason"] = "human approval is not bound to this action target"
        else:
            receipt["reason"] = "human approval is required"
    elif receipt["decision"] == "allow":
        receipt["outcome"] = "allow"
        receipt["reason"] = "allowed by execution envelope"
    else:
        receipt["outcome"] = "deny"
        receipt["reason"] = "denied by execution envelope"
    return receipt
