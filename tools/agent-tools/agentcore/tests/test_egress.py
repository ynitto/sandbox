import hashlib
import json
import unittest

from agentcore import egress


def envelope(decision="allow", url="ssh://example/repo.git"):
    value = {"version": 1, "approval": {"status": "approved"},
             "egress": {"git.push": {"decision": decision,
                                      "targets": [{"url": url}]}}}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value["digest"] = hashlib.sha256(raw.encode()).hexdigest()
    return value


class EgressGuardTests(unittest.TestCase):
    def test_three_policy_results_are_deterministic(self):
        target = {"url": "ssh://example/repo.git", "branch": "af/run"}
        self.assertEqual(egress.decide("git.push", target, envelope("allow"))["decision"], "allow")
        self.assertEqual(egress.decide("git.push", target, envelope("deny"))["decision"], "deny")
        pending = egress.decide("git.push", target, envelope("approval_required"))
        self.assertEqual(pending["decision"], "approval_required")
        self.assertEqual(pending["outcome"], "deny")
        approved = egress.decide("git.push", target, envelope("approval_required"), {
            "outcome": "approved", "egress_action_digest": pending["action_digest"]})
        self.assertEqual(approved["outcome"], "allow")
        self.assertEqual(approved["approval_resolution"], "approved")

    def test_missing_envelope_and_changed_target_fail_closed(self):
        self.assertEqual(egress.decide("git.push", {"url": "x"}, {})["decision"], "deny")
        old = envelope("approval_required")
        changed = egress.decide("git.push", {"url": "ssh://example/other.git"}, old)
        self.assertEqual(changed["decision"], "deny")
        self.assertIn("target", changed["reason"])

    def test_tampered_approved_snapshot_is_rejected(self):
        value = envelope()
        value["egress"]["git.push"]["targets"][0]["url"] = "changed"
        self.assertEqual(egress.decide("git.push", {"url": "changed"}, value)["decision"], "deny")

    def test_approval_for_old_target_cannot_be_reused(self):
        value = envelope("approval_required")
        old = egress.decide("git.push", {"url": "ssh://example/repo.git"}, value)
        changed = egress.decide("git.push", {"url": "ssh://example/repo.git", "branch": "new"},
                                value, {"outcome": "approved",
                                        "egress_action_digest": old["action_digest"]})
        self.assertEqual(changed["outcome"], "deny")
        self.assertIn("not bound", changed["reason"])


if __name__ == "__main__":
    unittest.main()
