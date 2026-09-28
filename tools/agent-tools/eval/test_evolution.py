from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import evolution


SHA1 = "1" * 40
SHA2 = "2" * 40


def identity(sha=SHA1):
    return evolution.checkpoint_identity(artifact_kind="routing-question", artifact_id="team",
        checkpoint=sha, suite="routing.team.held-out.v1", result_ref=f"results/{sha}.json")


def cases(outcomes=None):
    outcomes = outcomes or {}
    rows = []
    for label in ("verify", "compare", "split", "other"):
        for i in range(3):
            cid = f"{label}-{i}"
            outcome = outcomes.get(cid, "pass")
            rows.append({"id": cid, "partition": "held-out", "expected": label,
                         "predicted": label if outcome == "pass" else "other",
                         "outcome": outcome, "confidence": .8})
    return rows


class EvolutionTests(unittest.TestCase):
    def test_fingerprint_is_content_based(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "skill"; p.mkdir(); (p / "SKILL.md").write_text("one")
            first = evolution.content_fingerprint(p)
            (p / "SKILL.md").write_text("two")
            self.assertNotEqual(first, evolution.content_fingerprint(p))

    def test_identity_requires_immutable_checkpoint(self):
        with self.assertRaises(ValueError):
            evolution.checkpoint_identity(artifact_kind="routing-question", artifact_id="team",
                checkpoint="HEAD", suite="v1", result_ref="x")

    def test_metrics_keep_wrong_and_abstention_separate(self):
        report = evolution.qualify(identity(), {"model": "fixed", "tools": "fixed"},
                                   cases({"verify-0": "wrong", "split-1": "abstain"}))
        self.assertEqual(report["sample_status"], "SUFFICIENT")
        self.assertEqual(report["current_capability"]["wrong_case_ids"], ["verify-0"])
        self.assertEqual(report["current_capability"]["abstained_case_ids"], ["split-1"])
        self.assertEqual(report["adaptation"]["status"], "NOT_APPLICABLE")

    def test_readout_adapter_treats_other_as_answer_and_low_confidence_as_abstain(self):
        rows = [
            {"case": "RT5-a", "status": "ok", "expected": {"team": "other"},
             "answers": {"team": {"choice": "other", "confidence": .8, "method": "logprobs"}}},
            {"case": "RT5-b", "status": "ok", "expected": {"team": "split"},
             "answers": {"team": {"choice": "other", "confidence": .4, "method": "logprobs"}}},
        ]
        got = evolution.team_cases_from_readout(rows, min_confidence=.6)
        self.assertEqual([c["outcome"] for c in got], ["pass", "abstain"])

    def test_comparison_reports_regression_mixed_and_retention_ids(self):
        env = {"model": "m", "tools": "t"}
        previous = evolution.qualify(identity(), env, cases({"other-0": "wrong"}))
        current = evolution.qualify(identity(SHA2), env, cases({"verify-0": "wrong"}))
        result = evolution.compare(previous, current)
        self.assertEqual(result["status"], "MIXED")
        self.assertEqual(result["retention"]["regression_case_ids"], ["verify-0"])
        self.assertEqual(result["new_pass_case_ids"], ["other-0"])

    def test_insufficient_data_is_not_pass(self):
        report = evolution.qualify(identity(), {"model": "m"}, cases()[:4])
        self.assertEqual(report["sample_status"], "INSUFFICIENT_DATA")
        self.assertEqual(evolution.compare(report, report)["status"], "INSUFFICIENT_DATA")

    def test_environment_difference_is_not_artifact_difference(self):
        a = evolution.qualify(identity(), {"model": "a"}, cases())
        b = evolution.qualify(identity(SHA2), {"model": "b"}, cases())
        with self.assertRaises(ValueError):
            evolution.compare(a, b)


if __name__ == "__main__":
    unittest.main()
