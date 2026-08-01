#!/usr/bin/env python3
"""統一 verify の専用 runner（verifyplan.py・P1-A2）のテスト。

plan は agent-project が確定した digest 付き契約で、runner は成果 revision 上で一度だけ実行し
receipt を返す。壊れた plan は実行しない（receipt 無し＝不採用の fail-close）。同じ digest ×
同じ revision の再検証はしない（command 実行は一回だけ）。
"""
from _shared import *  # noqa: F401,F403
import argparse

from agentcore import verifycontract as vc


def _mkrepo(tmp):
    """コミット 1 つのローカル git リポジトリ（bare origin + clone）を作る。"""
    origin = os.path.join(tmp, "origin.git")
    subprocess.run(["git", "init", "--bare", "-q", origin], check=True)
    work = os.path.join(tmp, "seed")
    subprocess.run(["git", "clone", "-q", origin, work], check=True,
                   capture_output=True)
    pathlib.Path(work, "hello.txt").write_text("hello\n", encoding="utf-8")
    for cmd in (["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed"],
                ["git", "push", "-q", "origin", "HEAD:refs/heads/main"]):
        subprocess.run(cmd, cwd=work, check=True, capture_output=True)
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True,
                         text=True, check=True).stdout.strip()
    return origin, work, rev


def _plan(**kw):
    args = dict(criteria=[], commands=["true"])
    args.update(kw)
    return vc.build_plan("T-1", **args)


class ParsePlanTests(unittest.TestCase):
    def test_accepts_dict_and_json_string(self):
        p = _plan()
        self.assertEqual(kf.parse_verification_plan(p), p)
        self.assertEqual(kf.parse_verification_plan(json.dumps(p)), p)

    def test_rejects_garbage(self):
        self.assertIsNone(kf.parse_verification_plan(None))
        self.assertIsNone(kf.parse_verification_plan(""))
        self.assertIsNone(kf.parse_verification_plan("not json"))
        self.assertIsNone(kf.parse_verification_plan("[1,2]"))


class RunCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-vp-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_exit_zero(self):
        e = kf._vp_run_command("true", self.tmp, 30)
        self.assertEqual(e["exit_code"], 0)

    def test_nonzero_exit_is_recorded(self):
        e = kf._vp_run_command("false", self.tmp, 30)
        self.assertEqual(e["exit_code"], 1)

    def test_command_not_found_is_inconclusive(self):
        e = kf._vp_run_command("no-such-command-xyz-123", self.tmp, 30)
        self.assertTrue(e.get("inconclusive"))
        self.assertNotIn("exit_code", e)

    def test_missing_cwd_is_inconclusive(self):
        e = kf._vp_run_command("true", None, 30)
        self.assertTrue(e.get("inconclusive"))

    def test_command_string_is_not_rewritten(self):
        cmd = "echo 'a  b' && true"
        self.assertEqual(kf._vp_run_command(cmd, self.tmp, 30)["command"], cmd)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-vp-run-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bus = kf.Bus(os.path.join(self.tmp, "bus"), "run-vp-1")
        self.args = argparse.Namespace(run_id="run-vp-1", node_id="orch", model=None, request="req")

    def _seed_meta(self, plan, workspace=None):
        self.bus.ensure_run("req", workspace, [], plan)

    def test_no_plan_returns_none(self):
        self._seed_meta(None)
        self.assertIsNone(kf.run_verification_plan(self.bus, self.args, "orch"))

    def test_broken_plan_writes_no_receipt(self):
        p = _plan()
        p["criteria"] = [{"id": "C9", "text": "改ざん"}]     # digest 不一致
        self._seed_meta(p)
        self.assertIsNone(kf.run_verification_plan(self.bus, self.args, "orch"))
        self.assertIsNone(self.bus.run_receipt("run-vp-1"))

    def test_no_workspace_run_executes_in_cwd(self):
        """workspace の無い run はプロセス cwd（投入ノードの作業ツリー）で検証する（P1-A8）。"""
        _origin, work, rev = _mkrepo(self.tmp)
        cwd = os.getcwd()
        os.chdir(work)
        self.addCleanup(os.chdir, cwd)
        self._seed_meta(_plan(commands=["test -f hello.txt"]))
        r = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["result_rev"], rev)

    def test_no_workspace_run_gets_base_rev_from_meta(self):
        """cwd 実行では新旧の差分基準変数へ meta.base_rev（act 前 HEAD）を渡す。"""
        _origin, work, base = _mkrepo(self.tmp)
        cwd = os.getcwd()
        os.chdir(work)
        self.addCleanup(os.chdir, cwd)
        self._seed_meta(_plan(commands=[
            f'test "$AGENT_BASE_REV" = {base} && test "$KIRO_BASE_REV" = {base}'
        ]))
        # 投入後に成果コミットが積まれても base_rev は動かない
        pathlib.Path(work, "result.txt").write_text("r\n", encoding="utf-8")
        for cmd in (["git", "add", "-A"],
                    ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "-q", "-m", "act"]):
            subprocess.run(cmd, cwd=work, check=True, capture_output=True)
        r = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r["verdict"], "pass")

    def test_no_workspace_run_does_not_discard_worktree(self):
        """cwd（未コミットの成果を含みうる）では verifier 後始末の破棄をしない。"""
        _origin, work, _rev = _mkrepo(self.tmp)
        pathlib.Path(work, "uncommitted.txt").write_text("成果\n", encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(work)
        self.addCleanup(os.chdir, cwd)
        self._seed_meta(_plan(commands=["true"]))
        kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertTrue(pathlib.Path(work, "uncommitted.txt").exists())

    def test_declared_workspace_without_clone_stays_inconclusive(self):
        """workspace 宣言があるのに clone が用意できない run は cwd に倒さない（誤判定防止）。"""
        self._seed_meta(_plan(), workspace={"url": os.path.join(self.tmp, "no-such-origin.git"),
                                            "base": "main", "branch": "main"})
        r = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r["verdict"], "inconclusive")       # 実行場所が無い＝環境。fail ではない
        self.assertEqual(self.bus.run_receipt("run-vp-1")["verdict"], "inconclusive")

    def test_clone_run_passes_head_as_base_rev(self):
        """clone 実行の $AGENT_BASE_REV は成果 HEAD。"""
        origin, _work, rev = _mkrepo(self.tmp)
        plan = _plan(commands=['test "$AGENT_BASE_REV" = ' + rev])
        self._seed_meta(plan, workspace={"url": origin, "base": "main", "branch": "main"})
        r = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r["verdict"], "pass")

    def test_confirm_detects_flaky_command(self):
        """policy.confirm>1 で PASS/FAIL を跨いだら flaky（receipt_overall が fail に落とす）。"""
        marker = os.path.join(self.tmp, "flaky-marker")
        cmd = f"test -e {marker} && rm -f {marker} || {{ touch {marker}; false; }}"
        e = kf._vp_run_command(cmd, self.tmp, 30, None, confirm=2)
        self.assertTrue(e.get("flaky"))

    def test_commands_run_on_result_rev_and_receipt_is_written(self):
        origin, _work, rev = _mkrepo(self.tmp)
        plan = _plan(commands=["test -f hello.txt", "grep -q hello hello.txt"])
        self._seed_meta(plan, workspace={"url": origin, "base": "main", "branch": "main"})
        r = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["result_rev"], rev)
        self.assertEqual(r["plan_digest"], plan["digest"])
        self.assertEqual(vc.receipt_errors(r, plan=plan, expected_rev=rev), [])

    def test_failing_command_yields_fail_receipt(self):
        origin, _work, _rev = _mkrepo(self.tmp)
        plan = _plan(commands=["test -f no-such-file.txt"])
        self._seed_meta(plan, workspace={"url": origin, "base": "main", "branch": "main"})
        r = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r["verdict"], "fail")

    def test_same_digest_same_rev_is_not_rerun(self):
        origin, _work, rev = _mkrepo(self.tmp)
        marker = os.path.join(self.tmp, "ran-count")
        plan = _plan(commands=[f"echo x >> {marker}"])
        self._seed_meta(plan, workspace={"url": origin, "base": "main", "branch": "main"})
        r1 = kf.run_verification_plan(self.bus, self.args, "orch")
        r2 = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r1["result_rev"], rev)
        self.assertEqual(r2, r1)                             # 再実行せず既存 receipt を返す
        with open(marker, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 1)  # command 実行は一回だけ

    def test_criteria_judged_by_injected_agent(self):
        origin, _work, _rev = _mkrepo(self.tmp)
        plan = _plan(commands=["true"], criteria=["hello.txt が存在する"])
        self._seed_meta(plan, workspace={"url": origin, "base": "main", "branch": "main"})
        answer = json.dumps({"criteria": [
            {"id": "C1", "verdict": "pass",
             "evidence": [{"kind": "command", "command": "test -f hello.txt", "exit_code": 0}]}]})
        with mock.patch.object(kf, "run_agent", return_value=answer):
            r = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["criteria"][0]["verdict"], "pass")

    def test_pass_without_evidence_becomes_fail(self):
        origin, _work, _rev = _mkrepo(self.tmp)
        plan = _plan(commands=[], criteria=["基準"])
        self._seed_meta(plan, workspace={"url": origin, "base": "main", "branch": "main"})
        answer = json.dumps({"criteria": [{"id": "C1", "verdict": "pass", "evidence": []}]})
        with mock.patch.object(kf, "run_agent", return_value=answer):
            r = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r["verdict"], "fail")               # 証跡の無い pass は不採用

    def test_agent_failure_is_inconclusive_not_fail(self):
        origin, _work, _rev = _mkrepo(self.tmp)
        plan = _plan(commands=[], criteria=["基準"])
        self._seed_meta(plan, workspace={"url": origin, "base": "main", "branch": "main"})
        with mock.patch.object(kf, "run_agent", side_effect=RuntimeError("CLI 不在")):
            r = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(r["verdict"], "inconclusive")       # 検証不能はリトライを焼かない

    def test_verifier_changes_are_discarded(self):
        origin, _work, _rev = _mkrepo(self.tmp)
        plan = _plan(commands=[], criteria=["基準"])
        self._seed_meta(plan, workspace={"url": origin, "base": "main", "branch": "main"})

        def _tamper(prompt, model, purpose=""):
            clone = kf.ensure_workspace_clone(self.bus.run_workspace(), "run-vp-1")["clone"]
            pathlib.Path(clone, "hello.txt").write_text("tampered\n", encoding="utf-8")
            pathlib.Path(clone, "new-file.txt").write_text("x\n", encoding="utf-8")
            return json.dumps({"criteria": [{"id": "C1", "verdict": "fail"}]})

        with mock.patch.object(kf, "run_agent", side_effect=_tamper):
            kf.run_verification_plan(self.bus, self.args, "orch")
        clone = kf.ensure_workspace_clone(self.bus.run_workspace(), "run-vp-1")["clone"]
        self.assertEqual(pathlib.Path(clone, "hello.txt").read_text(encoding="utf-8"), "hello\n")
        self.assertFalse(pathlib.Path(clone, "new-file.txt").exists())


class FixTaskTests(unittest.TestCase):
    def test_fix_task_names_failures_and_forbids_weakening(self):
        receipt = {"commands": [{"command": "pytest -q", "exit_code": 1, "output_tail": "1 failed"}],
                   "criteria": [{"id": "C1", "text": "基準文", "verdict": "fail", "note": "未実装"}]}
        t = kf.verify_fix_task(receipt, 2)
        self.assertEqual(t["id"], "verify-fix-3")
        self.assertEqual(t["kind"], "work")
        self.assertIn("pytest -q", t["goal"])
        self.assertIn("C1", t["goal"])
        self.assertIn("緩和してはいけません", t["goal"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
