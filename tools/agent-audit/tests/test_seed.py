# おすすめ構成（agent-recommendation）→ qualifications.json の入口（seed）。
#
# 推奨は**読み取り専用の配布物**で制御面ではない。seed はその中身を検証して control へ
# 置くだけの口であり、生成はしない——writer を agent-audit の 1 つに保ったまま、
# GUI から起動できるようにするためのもの（2026-08-23 提案 §2.5 の「起動口だけ」）。
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from _shared import AuditTestCase  # noqa: E402

from agent_audit import qualifications  # noqa: E402


def _qualification(qid, status="qualified", source="eval-archive"):
    return {
        "qualification_id": qid,
        "status": status,
        "evaluation_profile_id": "extract-v1",
        "samples": 6,
        "passed": 6,
        "timeout_rate": 0,
        "success_rate_lower_bound": 0.61,
        "p50_seconds": 4.0,
        "critical_failure_risk": 0,
        "constraints": {},
        "source": source,
        "last_evaluated_at": "2026-08-26T00:00:00Z",
        "valid_until": "2026-11-24T00:00:00Z",
    }


def _recommendation(revision=1):
    return {
        "version": 1,
        "revision": revision,
        "generated_at": "2026-08-26T00:00:00Z",
        "qualifications": {
            "version": 1,
            "revision": revision,
            "evaluation_profiles": {
                "extract-v1": {
                    "operation_class": "extract", "min_samples": 6, "min_pass_rate": 0.9,
                    "max_timeout_rate": 0.1, "window_days": 90, "valid_for_days": 90,
                },
            },
            "candidates": [{
                "agent_cli": "ollama", "model": "gemma4:e4b",
                "execution_site": "device", "resource_group": "local-llm",
                "economics": {"estimated_cost": 0, "currency": "JPY"},
                "qualifications": {"extract": _qualification("ollama-e4b-extract-v1")},
            }],
        },
    }


class Args:
    def __init__(self, **kwargs):
        self.from_recommendation = kwargs.pop("from_recommendation", "")
        self.qualifications_file = kwargs.pop("qualifications_file", "")
        self.apply = kwargs.pop("apply", False)
        self.force = kwargs.pop("force", False)
        for key, value in kwargs.items():
            setattr(self, key, value)


class SeedTests(AuditTestCase):
    def setUp(self):
        super().setUp()
        self.source = os.path.join(self.tmp, "recommendation.json")
        self.target = os.path.join(self.tmp, "qualifications.json")
        self._write(self.source, _recommendation())

    def _write(self, path, document):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False)

    def _read(self, path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _seed(self, **kwargs):
        return qualifications.cmd_seed(Args(
            from_recommendation=self.source, qualifications_file=self.target, **kwargs))

    def test_dry_run_is_the_default_and_writes_nothing(self):
        summary = self._seed()
        self.assertFalse(summary["applied"])
        self.assertEqual(summary["candidates"], 1)
        self.assertFalse(os.path.exists(self.target), "dry-run では書かない")

    def test_apply_places_the_qualifications_block(self):
        summary = self._seed(apply=True)
        self.assertTrue(summary["applied"])
        document = self._read(self.target)
        self.assertEqual(document["version"], 1)
        self.assertEqual([(c["agent_cli"], c["model"]) for c in document["candidates"]],
                         [("ollama", "gemma4:e4b")])

    def test_missing_source_is_an_error_not_a_guess(self):
        summary = qualifications.cmd_seed(Args(qualifications_file=self.target))
        self.assertIn("--from-recommendation", summary["error"])
        summary = self._seed_from(os.path.join(self.tmp, "nope.json"))
        self.assertIn("推奨を読めません", summary["error"])

    def _seed_from(self, path, **kwargs):
        return qualifications.cmd_seed(Args(
            from_recommendation=path, qualifications_file=self.target, **kwargs))

    def test_unknown_recommendation_version_refuses(self):
        """未知の version は推測で適用しない。"""
        document = _recommendation()
        document["version"] = 2
        self._write(self.source, document)
        self.assertIn("未知の推奨 version", self._seed()["error"])

    def test_recommendation_without_qualifications_refuses(self):
        document = _recommendation()
        del document["qualifications"]
        self._write(self.source, document)
        self.assertIn("qualifications ブロックがありません", self._seed()["error"])

    def test_contract_violation_refuses(self):
        """契約に合わない適格性は置かない（qualify と同じ検査を通す）。"""
        document = _recommendation()
        document["qualifications"]["candidates"][0]["qualifications"]["extract"]["status"] = "なにか"
        self._write(self.source, document)
        self.assertTrue(self._seed()["error"])
        self.assertFalse(os.path.exists(self.target))

    def test_measured_evidence_is_not_overwritten_without_force(self):
        """seed は初期値であって、運用開始後の観測より新しい根拠ではない。"""
        self._write(self.target, {
            "version": 1, "revision": 7,
            "evaluation_profiles": {},
            "candidates": [{
                "agent_cli": "ollama", "model": "gemma4:e4b",
                "qualifications": {"extract": _qualification("x", source="receipt")},
            }],
        })
        summary = self._seed(apply=True)
        self.assertFalse(summary["applied"])
        self.assertIn("--force", summary["error"])
        self.assertEqual(summary["replaces_measured"],
                         [{"agent_cli": "ollama", "model": "gemma4:e4b",
                           "operation_class": "extract"}])
        self.assertEqual(self._read(self.target)["revision"], 7, "踏み潰していない")

    def test_force_overwrites_measured_evidence(self):
        self._write(self.target, {
            "version": 1, "revision": 7, "evaluation_profiles": {},
            "candidates": [{
                "agent_cli": "ollama", "model": "gemma4:e4b",
                "qualifications": {"extract": _qualification("x", source="receipt")},
            }],
        })
        self.assertTrue(self._seed(apply=True, force=True)["applied"])
        self.assertEqual(self._read(self.target)["revision"], 1)

    def test_seed_over_an_earlier_seed_needs_no_force(self):
        """eval-archive 由来だけなら、置き直しは初期値の更新にすぎない。"""
        self._seed(apply=True)
        document = _recommendation(revision=2)
        self._write(self.source, document)
        self.assertTrue(self._seed(apply=True)["applied"])
        self.assertEqual(self._read(self.target)["revision"], 2)

    def test_seed_never_generates(self):
        """置くのは推奨の中身そのもの（agent-audit が作り直さない）。"""
        self._seed(apply=True)
        self.assertEqual(self._read(self.target), _recommendation()["qualifications"])


if __name__ == "__main__":
    unittest.main()
