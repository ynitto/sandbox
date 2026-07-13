"""codd_gate_hooks の単体テスト（標準ライブラリ unittest）。

t8（codd-gate-hook-interface-spec.md）が確定した契約を検証する:
  - `run_diff_gate`/`collect_debt_specs` はいずれも usable=False（未検出・非互換）で
    無音の no-op へ倒れる。
  - usable=True だが実行時に縮退した（skipped）場合のみ理由付きで可視化する。
  - 本物のゲート失敗（failed）だけが run_diff_gate の False を引き起こす。
  - `build_routing_args`/`resolve_base_rev` の合成順序（verify/--strict, tasks/--debt）が
    仕様通りの argv を組み立てる。

実プロセスは起動せず、`run=`/`which=` の依存性注入（他 codd_gate_* テストと同じパターン）で検証する。

    python -m unittest discover -s tools/kiro-project/tests
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codd_gate_hooks as hooks
from codd_gate_status import CoddGateStatus


def _fake_run(returncode=0, stdout="", stderr=""):
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    run.calls = calls
    return run


def _raising_run(exc):
    def run(argv, **kwargs):
        raise exc

    return run


USABLE_STATUS = CoddGateStatus(binary=["codd-gate"], version=(1, 0, 0), findings=[])
UNUSABLE_STATUS = CoddGateStatus(binary=None, findings=[{"title": "codd-gate が見つからない"}])


class TestRunDiffGateNoOp(unittest.TestCase):
    def test_unusable_status_is_silent_noop_without_running(self):
        run = _raising_run(AssertionError("usable=False で run を呼んではいけない"))
        ok, msg = hooks.run_diff_gate(
            "/repos.json", "sandbox", "/vcwd", status=UNUSABLE_STATUS, run=run)
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_status_omitted_falls_back_to_detect_status_not_found(self):
        ok, msg = hooks.run_diff_gate(
            "/repos.json", "sandbox", "/vcwd", which=lambda name: None, run=_fake_run())
        self.assertTrue(ok)
        self.assertEqual(msg, "")


class TestRunDiffGateResultMapping(unittest.TestCase):
    def test_ok_result_returns_true_with_empty_message(self):
        ok, msg = hooks.run_diff_gate(
            "/repos.json", "sandbox", "/vcwd", status=USABLE_STATUS,
            run=_fake_run(returncode=0, stdout="OK\n"))
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_failed_result_returns_false_with_prefixed_reason(self):
        ok, msg = hooks.run_diff_gate(
            "/repos.json", "sandbox", "/vcwd", status=USABLE_STATUS,
            run=_fake_run(returncode=1, stdout="drift detected"))
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("codd-gate: "))
        self.assertIn("exit=1", msg)

    def test_skipped_at_runtime_returns_true_with_prefixed_reason(self):
        run = _raising_run(subprocess.TimeoutExpired(cmd="codd-gate", timeout=5))
        ok, msg = hooks.run_diff_gate(
            "/repos.json", "sandbox", "/vcwd", status=USABLE_STATUS, run=run)
        self.assertTrue(ok)
        self.assertTrue(msg.startswith("codd-gate: "))


class TestRunDiffGateArgv(unittest.TestCase):
    def test_argv_is_verify_strict_with_routing_and_base(self):
        run = _fake_run(returncode=0)
        hooks.run_diff_gate(
            "/repos.json", "sandbox", "/vcwd", "main", status=USABLE_STATUS, run=run)
        self.assertEqual(len(run.calls), 1)
        argv = run.calls[0]
        self.assertEqual(argv[0], "codd-gate")
        self.assertEqual(argv[1], "verify")
        self.assertIn("--repos", argv)
        self.assertIn("--repo-dir", argv)
        self.assertEqual(argv[argv.index("--repo-dir") + 1], "sandbox=.")
        self.assertIn("--base", argv)
        self.assertEqual(argv[argv.index("--base") + 1], "main")
        self.assertIn("--strict", argv)

    def test_missing_task_base_branch_falls_back_to_head_tilde_1(self):
        run = _fake_run(returncode=0)
        hooks.run_diff_gate(
            "/repos.json", "sandbox", "/vcwd", None, status=USABLE_STATUS,
            env={}, run=run)
        argv = run.calls[0]
        self.assertEqual(argv[argv.index("--base") + 1], "HEAD~1")


class TestCollectDebtSpecsNoOp(unittest.TestCase):
    def test_unusable_status_returns_empty_list_without_running(self):
        run = _raising_run(AssertionError("usable=False で run を呼んではいけない"))
        specs, msg = hooks.collect_debt_specs(
            "/repos.json", "sandbox", "/vcwd", status=UNUSABLE_STATUS, run=run)
        self.assertEqual(specs, [])
        self.assertEqual(msg, "")


class TestCollectDebtSpecsResultMapping(unittest.TestCase):
    def test_ok_result_parses_stdout_into_specs(self):
        stdout = json.dumps([{"title": "未文書化の関数がある", "id": "abc123"}])
        specs, msg = hooks.collect_debt_specs(
            "/repos.json", "sandbox", "/vcwd", status=USABLE_STATUS,
            run=_fake_run(returncode=0, stdout=stdout))
        self.assertEqual(specs, [{"title": "未文書化の関数がある", "id": "abc123"}])
        self.assertEqual(msg, "")

    def test_ok_result_with_malformed_record_reports_error_but_keeps_valid_items(self):
        stdout = json.dumps([{"title": "有効"}, {"no_title": True}])
        specs, msg = hooks.collect_debt_specs(
            "/repos.json", "sandbox", "/vcwd", status=USABLE_STATUS,
            run=_fake_run(returncode=0, stdout=stdout))
        self.assertEqual(specs, [{"title": "有効"}])
        self.assertIn("title", msg)

    def test_failed_result_returns_empty_list_with_prefixed_reason(self):
        specs, msg = hooks.collect_debt_specs(
            "/repos.json", "sandbox", "/vcwd", status=USABLE_STATUS,
            run=_fake_run(returncode=1, stdout="", stderr="boom"))
        self.assertEqual(specs, [])
        self.assertTrue(msg.startswith("codd-gate: "))

    def test_skipped_at_runtime_returns_empty_list_with_prefixed_reason(self):
        run = _raising_run(OSError("binary vanished"))
        specs, msg = hooks.collect_debt_specs(
            "/repos.json", "sandbox", "/vcwd", status=USABLE_STATUS, run=run)
        self.assertEqual(specs, [])
        self.assertTrue(msg.startswith("codd-gate: "))


class TestCollectDebtSpecsArgv(unittest.TestCase):
    def test_argv_is_tasks_debt_with_routing_and_no_base(self):
        run = _fake_run(returncode=0, stdout="[]")
        hooks.collect_debt_specs(
            "/repos.json", "sandbox", "/vcwd", status=USABLE_STATUS, run=run)
        argv = run.calls[0]
        self.assertEqual(argv[0], "codd-gate")
        self.assertEqual(argv[1], "tasks")
        self.assertIn("--debt", argv)
        self.assertIn("--repos", argv)
        self.assertIn("--repo-dir", argv)
        self.assertNotIn("--base", argv)


if __name__ == "__main__":
    unittest.main()
