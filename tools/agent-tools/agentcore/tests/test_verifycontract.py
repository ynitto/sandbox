"""agentcore.verifycontract — verification plan / receipt 契約の単体テスト（P1-A1）。

digest は生成側と実行側が同じ 1 実装で計算する不変条件。receipt の全体判定は自称でなく
中身から再導出し、証跡の無い pass・digest 不一致・revision 不一致は採用しない（fail-close）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentcore import verifycontract as vc  # noqa: E402


def _plan(**kw):
    args = dict(criteria=["基準その 1", "基準その 2"],
                commands=["pytest -q", {"command": "codd-gate verify --debt", "source": "policy"}])
    args.update(kw)
    return vc.build_plan("T-1", **args)


def _pass_criteria(plan):
    return [{"id": c["id"], "text": c["text"], "verdict": "pass",
             "evidence": [{"kind": "command", "command": "pytest -q", "exit_code": 0}]}
            for c in plan["criteria"]]


def _ok_commands(plan):
    return [{"command": c["command"], "exit_code": 0} for c in plan["commands"]]


class PlanTests(unittest.TestCase):
    def test_integration_plan_uses_v2_and_binds_target_into_digest(self):
        p = _plan(integration={"target": "main"})
        self.assertEqual(p["version"], 2)
        self.assertEqual(p["integration"], {"target": "main"})
        other = _plan(integration={"target": "develop"})
        self.assertNotEqual(p["digest"], other["digest"])

    def test_digest_is_deterministic_and_key_order_independent(self):
        p1 = _plan()
        p2 = dict(reversed(list(p1.items())))          # キー順を変えても
        self.assertEqual(vc.plan_digest(p1), vc.plan_digest(p2))
        self.assertEqual(p1["digest"], vc.plan_digest(p1))

    def test_digest_changes_when_content_changes(self):
        self.assertNotEqual(_plan()["digest"], _plan(criteria=["基準その 1"])["digest"])

    def test_criterion_ids_are_positional_1_based(self):
        p = _plan(criteria=["a", " ", "b"])            # 空行は落ちて詰まる
        self.assertEqual([c["id"] for c in p["criteria"]], ["C1", "C2"])

    def test_duplicate_commands_fold_to_one(self):
        p = _plan(commands=["pytest -q", {"command": "pytest -q", "source": "policy"}])
        self.assertEqual(len(p["commands"]), 1)        # task 固有と regression の重複は 1 回だけ実行

    def test_empty_plan_is_rejected(self):
        with self.assertRaises(ValueError):
            vc.build_plan("T-1", criteria=[], commands=[])

    def test_plan_errors_accepts_built_plan(self):
        self.assertEqual(vc.plan_errors(_plan()), [])

    def test_plan_errors_rejects_tampered_plan(self):
        p = _plan()
        p["criteria"][0]["text"] = "改ざん後の基準"
        self.assertTrue(any("digest" in e for e in vc.plan_errors(p)))

    def test_plan_errors_rejects_unknown_version(self):
        p = _plan()
        p["version"] = 99
        self.assertTrue(any("version" in e for e in vc.plan_errors(p)))

    def test_plan_errors_rejects_broken_numbering(self):
        p = _plan()
        p["criteria"][1]["id"] = "C7"
        self.assertTrue(any("採番" in e for e in vc.plan_errors(p)))


class ReceiptTests(unittest.TestCase):
    def test_v2_integration_receipt_requires_matching_pass(self):
        p = _plan(integration={"target": "main"})
        integration = {"target": "main", "target_rev": "def456",
                       "verdict": "pass", "conflict_files": []}
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p),
                             criteria=_pass_criteria(p), integration=integration)
        self.assertEqual(r["version"], 2)
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(vc.receipt_errors(r, plan=p, expected_rev="abc123"), [])

    def test_v2_missing_or_failed_integration_is_fail_close(self):
        p = _plan(integration={"target": "main"})
        missing = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p),
                                   criteria=_pass_criteria(p))
        self.assertEqual(missing["verdict"], "fail")
        self.assertTrue(any("integration" in e for e in vc.receipt_errors(missing, plan=p)))
        failed = vc.build_receipt(
            p, result_rev="abc123", commands=_ok_commands(p), criteria=_pass_criteria(p),
            integration={"target": "main", "target_rev": "def456", "verdict": "fail",
                         "conflict_files": ["f.txt"]})
        self.assertEqual(failed["verdict"], "fail")
        self.assertEqual(vc.receipt_errors(failed, plan=p), [])
        inconclusive = vc.build_receipt(
            p, result_rev="abc123", commands=_ok_commands(p), criteria=_pass_criteria(p),
            integration={"target": "main", "target_rev": "", "verdict": "inconclusive",
                         "conflict_files": []})
        self.assertEqual(inconclusive["verdict"], "inconclusive")
        self.assertEqual(vc.receipt_errors(inconclusive, plan=p), [])

    def test_all_green_receipt_is_pass_and_accepted(self):
        p = _plan()
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p),
                             criteria=_pass_criteria(p))
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(vc.receipt_errors(r, plan=p, expected_rev="abc123"), [])

    def test_pass_without_evidence_is_fail(self):
        p = _plan()
        crit = _pass_criteria(p)
        crit[0]["evidence"] = []
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p), criteria=crit)
        self.assertEqual(r["verdict"], "fail")

    def test_nonzero_exit_is_fail(self):
        p = _plan()
        cmds = _ok_commands(p)
        cmds[1]["exit_code"] = 1
        r = vc.build_receipt(p, result_rev="abc123", commands=cmds, criteria=_pass_criteria(p))
        self.assertEqual(r["verdict"], "fail")

    def test_unlaunchable_command_is_inconclusive_not_fail(self):
        p = _plan()
        cmds = _ok_commands(p)
        cmds[1] = {"command": cmds[1]["command"], "inconclusive": True, "note": "コマンド不在"}
        r = vc.build_receipt(p, result_rev="abc123", commands=cmds, criteria=_pass_criteria(p))
        self.assertEqual(r["verdict"], "inconclusive")

    def test_missing_exit_code_is_fail_close(self):
        p = _plan()
        cmds = _ok_commands(p)
        del cmds[0]["exit_code"]
        r = vc.build_receipt(p, result_rev="abc123", commands=cmds, criteria=_pass_criteria(p))
        self.assertEqual(r["verdict"], "fail")

    def test_flaky_command_is_fail(self):
        p = _plan()
        cmds = _ok_commands(p)
        cmds[0]["flaky"] = True
        r = vc.build_receipt(p, result_rev="abc123", commands=cmds, criteria=_pass_criteria(p))
        self.assertEqual(r["verdict"], "fail")

    def test_inconclusive_without_fail_is_inconclusive(self):
        p = _plan()
        crit = _pass_criteria(p)
        crit[1] = {"id": "C2", "verdict": "inconclusive", "note": "環境にツールが無い"}
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p), criteria=crit)
        self.assertEqual(r["verdict"], "inconclusive")

    def test_fail_beats_inconclusive(self):
        p = _plan(commands=[])
        crit = _pass_criteria(p)
        crit[0]["verdict"] = "fail"
        crit[1] = {"id": "C2", "verdict": "inconclusive"}
        r = vc.build_receipt(p, result_rev="abc123", criteria=crit)
        self.assertEqual(r["verdict"], "fail")

    def test_unknown_verdict_is_fail_close(self):
        p = _plan(commands=[])
        crit = _pass_criteria(p)
        crit[0]["verdict"] = "maybe"
        r = vc.build_receipt(p, result_rev="abc123", criteria=crit)
        self.assertEqual(r["verdict"], "fail")

    def test_empty_receipt_is_fail(self):
        self.assertEqual(vc.receipt_overall({"commands": [], "criteria": []}), "fail")

    def test_digest_mismatch_is_rejected(self):
        p = _plan()
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p),
                             criteria=_pass_criteria(p))
        other = _plan(criteria=["別の基準"])
        self.assertTrue(any("plan_digest" in e
                            for e in vc.receipt_errors(r, plan=other, expected_rev="abc123")))

    def test_rev_mismatch_is_rejected(self):
        p = _plan()
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p),
                             criteria=_pass_criteria(p))
        self.assertTrue(any("result_rev" in e
                            for e in vc.receipt_errors(r, plan=p, expected_rev="def456")))

    def test_missing_rev_is_rejected(self):
        p = _plan()
        r = vc.build_receipt(p, result_rev="", commands=_ok_commands(p),
                             criteria=_pass_criteria(p))
        self.assertTrue(any("result_rev" in e for e in vc.receipt_errors(r, plan=p)))

    def test_command_list_must_match_plan_order(self):
        p = _plan()
        cmds = list(reversed(_ok_commands(p)))
        r = vc.build_receipt(p, result_rev="abc123", commands=cmds, criteria=_pass_criteria(p))
        self.assertTrue(any("同順" in e for e in vc.receipt_errors(r, plan=p, expected_rev="abc123")))

    def test_criterion_ids_must_match_plan(self):
        p = _plan()
        crit = _pass_criteria(p)[:1]                   # C2 が欠けた receipt
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p), criteria=crit)
        self.assertTrue(any("id 列" in e for e in vc.receipt_errors(r, plan=p, expected_rev="abc123")))

    def test_unknown_receipt_version_is_rejected(self):
        p = _plan()
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p),
                             criteria=_pass_criteria(p))
        r["version"] = 99
        self.assertTrue(any("version" in e for e in vc.receipt_errors(r, plan=p, expected_rev="abc123")))


if __name__ == "__main__":
    unittest.main()
