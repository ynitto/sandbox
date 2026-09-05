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

    def test_plan_agent_is_normalized_and_bound_into_the_digest(self):
        """検証条件（何で確かめるか）は plan に載り、digest に入る。

        digest に入るので、条件を変えた検証は別の plan になり、以前の条件で出た receipt は
        検算で落ちる。違う条件で確かめたものを同じ判定として混ぜないための性質
        （設計: docs/plans/2026-08-09-verification-settlement-design.md §4）。"""
        p = _plan(policy={"agent": {"agent_cli": " CODEX ", "model": "opus",
                                    "timeout_sec": "1800"}})
        self.assertEqual(p["policy"]["agent"],
                         {"agent_cli": "codex", "model": "opus", "timeout_sec": 1800.0})
        self.assertEqual(vc.plan_agent(p), p["policy"]["agent"])
        other = _plan(policy={"agent": {"agent_cli": "kiro"}})
        self.assertNotEqual(p["digest"], other["digest"])
        self.assertNotEqual(p["digest"], _plan()["digest"])

    def test_plan_without_an_agent_leaves_the_choice_to_the_runner(self):
        self.assertIsNone(vc.plan_agent(_plan()))
        self.assertIsNone(vc.plan_agent(_plan(policy={"agent": {}})))
        self.assertIsNone(vc.plan_agent(_plan(policy={"agent": {"agent_cli": "  "}})))

    def test_broken_agent_spec_is_ignored_not_fatal(self):
        # 打ち間違いの 1 文字でタスクを検証不能にしない（指定が効いたかは receipt で分かる）
        self.assertIsNone(vc.normalize_plan_agent("codex"))
        self.assertIsNone(vc.normalize_plan_agent(None))
        self.assertEqual(vc.normalize_plan_agent({"agent_cli": "codex", "timeout_sec": "x"}),
                         {"agent_cli": "codex"})
        self.assertEqual(vc.normalize_plan_agent({"agent_cli": "codex", "timeout_sec": -5}),
                         {"agent_cli": "codex"})

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

    def test_policy_bounds_are_enforced(self):
        with self.assertRaises(ValueError):
            vc.build_plan("T-1", criteria=["a"], policy={"confirm": 0})
        with self.assertRaises(ValueError):
            vc.build_plan("T-1", criteria=["a"], policy={"timeout_sec": -1})
        p = _plan()
        p["policy"] = {"confirm": 0, "timeout_sec": -1}
        p["digest"] = vc.plan_digest(p)
        errs = vc.plan_errors(p)
        self.assertTrue(any("confirm" in e for e in errs))
        self.assertTrue(any("timeout_sec" in e for e in errs))


class VerifiedWithTests(unittest.TestCase):
    """receipt は「何で・どれだけ待って確かめたか」を返す。判定には使わない材料。"""

    def test_verified_with_is_recorded_and_empty_fields_dropped(self):
        p = _plan()
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p),
                             criteria=_pass_criteria(p),
                             verified_with={"agent_cli": "codex", "model": "",
                                            "timeout_sec": 1800, "elapsed_sec": 42.3,
                                            "source": "plan"})
        self.assertEqual(r["verified_with"], {"agent_cli": "codex", "timeout_sec": 1800,
                                              "elapsed_sec": 42.3, "source": "plan"})

    def test_verified_with_does_not_affect_acceptance(self):
        # 受理は「何を出したか」で決まる（C6）。誰が・何で確かめたかは判定に混ぜない。
        p = _plan()
        base = dict(result_rev="abc123", commands=_ok_commands(p), criteria=_pass_criteria(p))
        plain = vc.build_receipt(p, **base)
        tagged = vc.build_receipt(p, **base, verified_with={"agent_cli": "codex"})
        self.assertEqual(plain["verdict"], tagged["verdict"])
        self.assertEqual(vc.receipt_errors(tagged, plan=p, expected_rev="abc123"),
                         vc.receipt_errors(plain, plan=p, expected_rev="abc123"))

    def test_absent_verified_with_leaves_no_key(self):
        p = _plan()
        r = vc.build_receipt(p, result_rev="abc123", commands=_ok_commands(p),
                             criteria=_pass_criteria(p))
        self.assertNotIn("verified_with", r)


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

    def test_nonzero_exit_beats_inconclusive_flag(self):
        # exit_code 非 0 と inconclusive が両立する矛盾レコードは fail（成果物の欠陥）。
        self.assertEqual(vc.receipt_overall({
            "commands": [{"command": "x", "exit_code": 7, "inconclusive": True}],
            "criteria": [],
        }), "fail")

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


class WorksetPlanTests(unittest.TestCase):
    """version 3（workset）— 複数の書込先を検証できる契約。

    1 要素のときは version 1/2 のまま作る（同じ条件の検証を版だけの理由で別 plan にしない）。
    設計: docs/plans/2026-09-05-agent-flow-multi-workspace-design.md §5.4。
    """

    def test_single_workspace_still_builds_v1(self):
        p = vc.build_plan("T-1", commands=["true"], workspaces=["api"])
        self.assertEqual(p["version"], 1)
        self.assertNotIn("workspaces", p)
        self.assertEqual(p["workspace"], "api")
        self.assertEqual(vc.plan_errors(p), [])

    def test_two_workspaces_build_v3(self):
        p = vc.build_plan("T-1", commands=["true"], workspaces=["api", "web"])
        self.assertEqual(p["version"], 3)
        self.assertEqual(p["workspaces"], ["api", "web"])
        self.assertEqual(p["workspace"], "api")           # primary は従来キーにも載る
        self.assertEqual(vc.plan_errors(p), [])

    def test_command_cwd_makes_the_same_string_two_commands(self):
        # 同じコマンド文字列でも別 repo で走らせるなら別の検証。畳むと片方が黙って走らない。
        p = vc.build_plan("T-1", workspaces=["api", "web"],
                          commands=[{"command": "make test"},
                                    {"command": "make test", "cwd": "web"}])
        self.assertEqual(len(p["commands"]), 2)
        self.assertEqual(vc.plan_command_cwd(p["commands"][0], p), "api")   # 省略は primary
        self.assertEqual(vc.plan_command_cwd(p["commands"][1], p), "web")

    def test_cwd_outside_the_workset_is_refused(self):
        with self.assertRaisesRegex(ValueError, "workset 外"):
            vc.build_plan("T-1", workspaces=["api", "web"],
                          commands=[{"command": "true", "cwd": "docs"}])

    def test_targets_are_bound_per_element_and_into_the_digest(self):
        a = vc.build_plan("T-1", commands=["true"], workspaces=["api", "web"],
                          integration={"targets": {"api": "develop", "web": "main"}})
        b = vc.build_plan("T-1", commands=["true"], workspaces=["api", "web"],
                          integration={"targets": {"api": "develop", "web": "release"}})
        self.assertEqual(vc.plan_targets(a), {"api": "develop", "web": "main"})
        self.assertNotEqual(a["digest"], b["digest"])
        self.assertEqual(vc.plan_errors(a), [])

    def test_v2_target_reads_as_the_primary_target(self):
        p = vc.build_plan("T-1", commands=["true"], workspace="api",
                          integration={"target": "develop"})
        self.assertEqual(p["version"], 2)
        self.assertEqual(vc.plan_targets(p), {"api": "develop"})

    def test_workspaces_on_a_v1_plan_is_rejected(self):
        p = vc.build_plan("T-1", commands=["true"], workspace="api")
        p["workspaces"] = ["api"]
        p["digest"] = vc.plan_digest(p)
        self.assertTrue(any("version 3 以外" in e for e in vc.plan_errors(p)))

    def test_v3_plan_without_workspaces_is_rejected(self):
        p = vc.build_plan("T-1", commands=["true"], workspaces=["api", "web"])
        del p["workspaces"]
        p["digest"] = vc.plan_digest(p)
        self.assertTrue(any("workspaces が無い" in e for e in vc.plan_errors(p)))


class WorksetReceiptTests(unittest.TestCase):
    def _plan(self):
        return vc.build_plan("T-1", criteria=["基準"], commands=["true"],
                             workspaces=["api", "web"],
                             integration={"targets": {"api": "main", "web": "main"}})

    def _receipt(self, plan, **kw):
        args = dict(
            result_rev="a" * 40,
            commands=[{"command": "true", "exit_code": 0}],
            criteria=[{"id": "C1", "text": "基準", "verdict": "pass",
                       "evidence": [{"kind": "command"}]}],
            revisions={"api": "a" * 40, "web": "b" * 40},
            integrations=[{"name": "api", "target": "main", "target_rev": "c" * 40,
                           "verdict": "pass", "conflict_files": []},
                          {"name": "web", "target": "main", "target_rev": "d" * 40,
                           "verdict": "pass", "conflict_files": []}])
        args.update(kw)
        return vc.build_receipt(plan, **args)

    def test_a_complete_v3_receipt_is_accepted(self):
        plan = self._plan()
        r = self._receipt(plan)
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["workspaces"], ["api", "web"])
        self.assertEqual(sorted(r["revisions"]), ["api", "web"])
        self.assertEqual(vc.receipt_errors(r, plan=plan, expected_rev="a" * 40), [])

    def test_one_element_behind_its_target_fails_the_whole_receipt(self):
        plan = self._plan()
        r = self._receipt(plan)
        r["integrations"][1]["verdict"] = "fail"
        self.assertEqual(vc.receipt_overall(r), "fail")

    def test_missing_revisions_are_not_accepted(self):
        plan = self._plan()
        r = self._receipt(plan, revisions={"api": "a" * 40})
        self.assertTrue(any("revisions" in e
                            for e in vc.receipt_errors(r, plan=plan, expected_rev="a" * 40)))

    def test_integration_targets_must_match_the_plan(self):
        plan = self._plan()
        r = self._receipt(plan)
        r["integrations"] = r["integrations"][:1]
        self.assertTrue(any("integration targets" in e
                            for e in vc.receipt_errors(r, plan=plan, expected_rev="a" * 40)))

    def test_v1_and_v2_receipts_are_unchanged(self):
        # 版上げで旧契約の判定が動かないこと（新旧両方を通す契約テスト）。
        p1 = vc.build_plan("T-1", criteria=["基準"], commands=["true"], workspace="api")
        r1 = vc.build_receipt(p1, result_rev="a" * 40,
                              commands=[{"command": "true", "exit_code": 0}],
                              criteria=[{"id": "C1", "verdict": "pass",
                                         "evidence": [{"kind": "command"}]}])
        self.assertEqual(r1["verdict"], "pass")
        self.assertNotIn("workspaces", r1)
        self.assertNotIn("revisions", r1)
        self.assertEqual(vc.receipt_errors(r1, plan=p1, expected_rev="a" * 40), [])

        p2 = vc.build_plan("T-1", commands=["true"], workspace="api",
                           integration={"target": "main"})
        r2 = vc.build_receipt(p2, result_rev="a" * 40,
                              commands=[{"command": "true", "exit_code": 0}],
                              integration={"target": "main", "target_rev": "c" * 40,
                                           "verdict": "pass", "conflict_files": []})
        self.assertEqual(r2["verdict"], "pass")
        self.assertEqual(vc.receipt_errors(r2, plan=p2, expected_rev="a" * 40), [])


if __name__ == "__main__":
    unittest.main()
