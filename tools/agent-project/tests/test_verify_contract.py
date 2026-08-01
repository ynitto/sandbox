#!/usr/bin/env python3
"""統一 verify（P1-A3）— 正規形アダプタ・verification_plan 生成・receipt 検算のテスト。

digest / 判定は agentcore.verifycontract の 1 実装（agent-flow runner と共有）。receipt が
無い・digest 不一致・revision 不一致の pass は採用しない（fail-close）。旧形式
（acceptance / accept / verify）は読み取り境界で正規形へ読み替える。
"""
from _shared import *  # noqa: F401,F403

from agentcore import verifycontract as vc


def _task(d, tid="T1", lines=()):
    bd = d / "backlog"
    bd.mkdir(parents=True, exist_ok=True)
    body = f"## {tid}: タイトル\n- status: doing\n" + "".join(f"- {ln}\n" for ln in lines)
    (bd / f"{tid}.md").write_text(body, encoding="utf-8")
    return km.parse_task(body, tid)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="kp-vc-"))
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_canonical_criteria_win_over_legacy(self):
        t = _task(self.d, lines=["task_acceptance_criteria: 新基準 A",
                                 "task_acceptance_criteria: 新基準 B",
                                 "acceptance: 旧基準", "accept: もっと旧"])
        self.assertEqual(km.task_acceptance(t), ["新基準 A", "新基準 B"])

    def test_legacy_acceptance_still_readable(self):
        t = _task(self.d, lines=["acceptance: 旧基準"])
        self.assertEqual(km.task_acceptance(t), ["旧基準"])

    def test_verification_commands_and_legacy_verify(self):
        t = _task(self.d, lines=["verification_commands: pytest -q", "verify: `make test`"])
        cmds = km.task_verification_commands(t)
        self.assertEqual([c["command"] for c in cmds], ["pytest -q", "make test"])
        self.assertEqual([c["source"] for c in cmds], ["user", "legacy"])

    def test_template_expanded_verify_is_marked_template(self):
        t = _task(self.d, lines=["verify: `test -e x`", "verify_source: template"])
        self.assertEqual(km.task_verification_commands(t)[0]["source"], "template")

    def test_has_verify_plan_sees_canonical_fields(self):
        t = _task(self.d, lines=["verification_commands: pytest -q"])
        t.verify = ""
        self.assertTrue(km.has_verify_plan(t))


class PlanBuildTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="kp-vc-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.cfg = cfg_for(self.d)

    def test_no_material_no_plan(self):
        t = _task(self.d)
        t.verify = ""
        self.assertIsNone(km.build_task_verification_plan(self.cfg, t))

    def test_criteria_get_diff_criterion_appended(self):
        t = _task(self.d, lines=["task_acceptance_criteria: 基準"])
        t.verify = ""
        plan = km.build_task_verification_plan(self.cfg, t)
        texts = [c["text"] for c in plan["criteria"]]
        self.assertEqual(texts, ["基準", km.DIFF_CRITERION])
        self.assertEqual(vc.plan_errors(plan), [])

    def test_no_diff_swaps_diff_criterion(self):
        """W4: `- no_diff: <理由>` は⑤の述語を「成果物の実在と参照」へ差し替える（基準は消えない）。"""
        t = _task(self.d, lines=["task_acceptance_criteria: 基準",
                                 "no_diff: 調査タスクで差分を作らない"])
        t.verify = ""
        plan = km.build_task_verification_plan(self.cfg, t)
        texts = [c["text"] for c in plan["criteria"]]
        self.assertEqual(len(texts), 2)
        self.assertNotIn(km.DIFF_CRITERION, texts)
        self.assertIn("調査タスクで差分を作らない", texts[1])
        self.assertIn("実在", texts[1])
        self.assertEqual(vc.plan_errors(plan), [])

    def test_command_only_plan_has_no_criteria(self):
        t = _task(self.d, lines=["verify: `pytest -q`"])
        plan = km.build_task_verification_plan(self.cfg, t)
        self.assertEqual(plan["criteria"], [])       # LLM verifier を要求しない（fast path の費用不変）
        self.assertEqual(plan["commands"][0]["source"], "legacy")

    def test_write_workspace_plan_requires_target_integration(self):
        t = _task(self.d, lines=["verify: `pytest -q`"])
        ws = {"url": "repo", "branch": "ap/T1", "target": "main"}
        with mock.patch.object(km, "_workspace_spec_for", return_value=ws):
            plan = km.build_task_verification_plan(self.cfg, t)
        self.assertEqual(plan["version"], 2)
        self.assertEqual(plan["integration"], {"target": "main"})


class SettleFromReceiptTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="kp-vc-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.cfg = cfg_for(self.d)
        self.task = _task(self.d, lines=["task_acceptance_criteria: 基準",
                                         "last_run: run-x"])
        self.task.verify = ""
        self.plan = km.build_task_verification_plan(self.cfg, self.task)

    def _write_receipt(self, receipt):
        rd = self.cfg.bus / "runs" / "run-x"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False),
                                         encoding="utf-8")

    def _receipt(self, rev="abc123", verdicts=("pass", "pass")):
        crit = []
        for c, v in zip(self.plan["criteria"], verdicts):
            rec = {"id": c["id"], "text": c["text"], "verdict": v}
            if v == "pass":
                rec["evidence"] = [{"kind": "command", "command": "pytest -q", "exit_code": 0}]
            crit.append(rec)
        return vc.build_receipt(self.plan, result_rev=rev, commands=[], criteria=crit)

    def test_no_receipt_without_vcwd_returns_none(self):
        # vcwd を渡さない呼び出し（local runner なし）では receipt 不在 = None（人の判断へ）
        self.assertIsNone(km.settle_from_receipt(self.cfg, self.task, self.plan, "abc123"))

    def test_matching_pass_receipt_is_adopted(self):
        self._write_receipt(self._receipt())
        ok, flaky, vmsg, verification = km.settle_from_receipt(
            self.cfg, self.task, self.plan, "abc123")
        self.assertTrue(ok)
        self.assertFalse(flaky)
        self.assertTrue(verification["ok"])
        self.assertTrue(verification.get("receipt"))
        rec = json.loads(dict(self.task.extra)["verification"])
        self.assertTrue(rec["receipt"])
        self.assertEqual(rec["plan_digest"], self.plan["digest"])

    def test_rev_mismatch_is_not_adopted(self):
        self._write_receipt(self._receipt(rev="oldrev"))
        self.assertIsNone(km.settle_from_receipt(self.cfg, self.task, self.plan, "abc123"))

    def test_digest_mismatch_is_not_adopted(self):
        other = vc.build_plan("T1", criteria=["別基準"], commands=[])
        r = self._receipt()
        r["plan_digest"] = other["digest"]
        self._write_receipt(r)
        self.assertIsNone(km.settle_from_receipt(self.cfg, self.task, self.plan, "abc123"))

    def test_fail_receipt_is_adopted_as_fail(self):
        self._write_receipt(self._receipt(verdicts=("fail", "pass")))
        ok, _f, _m, verification = km.settle_from_receipt(
            self.cfg, self.task, self.plan, "abc123")
        self.assertFalse(ok)
        self.assertEqual(verification["fail"], 1)

    def test_inconclusive_maps_to_unverifiable_route(self):
        self._write_receipt(self._receipt(verdicts=("inconclusive", "pass")))
        ok, _f, _m, verification = km.settle_from_receipt(
            self.cfg, self.task, self.plan, "abc123")
        self.assertFalse(ok)
        self.assertEqual(verification["unverifiable"], 1)   # 既存の検証不能経路（委譲/人送り）に乗る
        self.assertEqual(verification["fail"], 0)           # リトライを焼かない

    def test_pass_without_evidence_in_receipt_is_fail(self):
        r = self._receipt()
        r["criteria"][0].pop("evidence")
        r["verdict"] = vc.receipt_overall(r)
        self._write_receipt(r)
        ok, _f, _m, verification = km.settle_from_receipt(
            self.cfg, self.task, self.plan, "abc123")
        self.assertFalse(ok)
        self.assertGreaterEqual(verification["fail"], 1)


class LocalReceiptTests(unittest.TestCase):
    """P1-A8: run が receipt を返さない経路（dry-run・stub・旧 agent-flow）の local runner。
    固定コマンドをこのノードで実行して同じ契約の receipt を組み、同じ検算で採用する。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="kp-vc-local-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.cfg = cfg_for(self.d)

    def test_command_pass_is_adopted_without_flow_receipt(self):
        t = _task(self.d, lines=["verification_commands: test -d backlog"])
        plan = km.build_task_verification_plan(self.cfg, t)
        ok, flaky, vmsg, verification = km.settle_from_receipt(
            self.cfg, t, plan, "", self.d, None)
        self.assertTrue(ok)
        self.assertFalse(flaky)
        self.assertTrue(verification.get("receipt"))
        rec = json.loads(dict(t.extra)["verification"])
        self.assertEqual(rec["plan_digest"], plan["digest"])

    def test_command_fail_is_adopted_as_fail(self):
        t = _task(self.d, lines=["verification_commands: test -f no-such-file"])
        plan = km.build_task_verification_plan(self.cfg, t)
        ok, _f, _m, verification = km.settle_from_receipt(self.cfg, t, plan, "", self.d, None)
        self.assertFalse(ok)
        self.assertEqual(verification["fail"], 1)

    def test_criteria_only_plan_is_inconclusive_locally(self):
        # 自然文基準の判定（verifier セッション）は agent-flow runner だけ。local runner は
        # pass/fail を発明せず unverifiable（委譲/人送り）へ流す。
        t = _task(self.d, lines=["task_acceptance_criteria: 何かが動く"])
        t.verify = ""
        plan = km.build_task_verification_plan(self.cfg, t)
        ok, _f, _m, verification = km.settle_from_receipt(self.cfg, t, plan, "", self.d, None)
        self.assertFalse(ok)
        self.assertEqual(verification["fail"], 0)            # リトライを焼かない
        self.assertEqual(verification["unverifiable"], len(plan["criteria"]))

    def test_env_reaches_local_command(self):
        t = _task(self.d, lines=['verification_commands: test "$KIRO_BASE_REV" = base123'])
        plan = km.build_task_verification_plan(self.cfg, t)
        ok, *_ = km.settle_from_receipt(self.cfg, t, plan, "", self.d,
                                        {"KIRO_BASE_REV": "base123"})
        self.assertTrue(ok)

    def test_rejected_flow_receipt_falls_to_local_runner(self):
        t = _task(self.d, lines=["verification_commands: true", "last_run: run-x"])
        plan = km.build_task_verification_plan(self.cfg, t)
        rd = self.cfg.bus / "runs" / "run-x"
        rd.mkdir(parents=True, exist_ok=True)
        stale = vc.build_receipt(plan, result_rev="oldrev",
                                 commands=[{"command": "true", "exit_code": 0}])
        (rd / "receipt.json").write_text(json.dumps(stale), encoding="utf-8")
        ok, *_ = km.settle_from_receipt(self.cfg, t, plan, "newrev", self.d, None)
        self.assertTrue(ok)                                  # 古い receipt は不採用 → 取り直し
        journal = self.cfg.journal.read_text(encoding="utf-8")
        self.assertIn("receipt を不採用", journal)

    def test_local_flaky_is_quarantined_not_done(self):
        marker = self.d / "flaky-marker"
        cmd = f"test -e {marker} && rm -f {marker} || {{ touch {marker}; false; }}"
        t = _task(self.d, lines=[f"verification_commands: {cmd}"])
        cfg = cfg_for(self.d, verify_confirm=2)
        plan = km.build_task_verification_plan(cfg, t)
        ok, flaky, vmsg, _v = km.settle_from_receipt(cfg, t, plan, "", self.d, None)
        self.assertFalse(ok)
        self.assertTrue(flaky)
        self.assertIn("flaky", vmsg)


class ReceiptToVerificationTests(unittest.TestCase):
    def test_failed_integration_becomes_failed_verification_row(self):
        v = km.receipt_to_verification({
            "integration": {"target": "main", "target_rev": "def456", "verdict": "fail"},
            "commands": [], "criteria": []})
        self.assertEqual(v["fail"], 1)
        self.assertFalse(v["ok"])

    def test_commands_become_rows(self):
        receipt = {"commands": [{"command": "pytest -q", "exit_code": 0},
                                {"command": "make lint", "exit_code": 2, "output_tail": "x"},
                                {"command": "npm test", "inconclusive": True, "note": "npm 不在"}],
                   "criteria": []}
        v = km.receipt_to_verification(receipt)
        self.assertEqual([c["verdict"] for c in v["criteria"]],
                         ["pass", "fail", "unverifiable"])

    def test_evidence_mapping(self):
        receipt = {"commands": [], "criteria": [
            {"id": "C1", "text": "基準", "verdict": "pass",
             "evidence": [{"kind": "command", "command": "grep -q x f", "exit_code": 0},
                          {"kind": "file", "path": "src/a.py", "summary": "実装あり"}]}]}
        v = km.receipt_to_verification(receipt)
        c = v["criteria"][0]
        self.assertEqual(c["evidence"]["commands"], ["grep -q x f"])
        self.assertEqual(c["evidence"]["files"], ["src/a.py"])
        self.assertTrue(v["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
