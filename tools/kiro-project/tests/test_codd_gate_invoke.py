"""codd_gate_invoke の単体テスト（標準ライブラリ unittest）。

CoddGateResult（status = ok / failed / skipped、exit_code、stdout、reason）が
「codd-gate 未検出・非互換・起動失敗のいずれであっても、本体処理を一切ブロックせず
skipped へ縮退する」契約を守っていることを、実プロセスを起動せず `run=` の
依存性注入（codd_gate_detect / codd_gate_status の既存テストと同じパターン）で検証する。

    python -m unittest discover -s tools/kiro-project/tests
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codd_gate_invoke as invoke
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


class TestCoddGateResultShape(unittest.TestCase):
    """CoddGateResult 自体のフィールド・.ok プロパティ。"""

    def test_ok_result_has_exit_code_zero_and_empty_reason(self):
        result = invoke.CoddGateResult(status="ok", exit_code=0, stdout="OK: 一貫性ゲート通過\n")
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.reason, "")

    def test_skipped_result_has_none_exit_code(self):
        result = invoke.CoddGateResult(status="skipped", exit_code=None, stdout="", reason="未検出")
        self.assertFalse(result.ok)
        self.assertIsNone(result.exit_code)


class TestInvokeCoddGateSkipsWhenUnusable(unittest.TestCase):
    """usable=False（未検出・非互換）なら、プロセスを一切起動せず skipped を返す。"""

    def test_not_found_status_returns_skipped_without_running(self):
        status = CoddGateStatus(binary=None, findings=[{"title": "codd-gate が見つからない"}])
        run = _raising_run(AssertionError("skipped 経路で run を呼んではいけない"))
        result = invoke.invoke_codd_gate(status, "verify", "--strict", run=run)
        self.assertEqual(result.status, "skipped")
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.stdout, "")
        self.assertIn("codd-gate が見つからない", result.reason)

    def test_incompatible_status_returns_skipped_with_status_reason(self):
        status = CoddGateStatus(
            binary=["codd-gate"],
            findings=[{"title": "codd-gate のバージョンが対応下限未満"}],
        )
        run = _raising_run(AssertionError("skipped 経路で run を呼んではいけない"))
        result = invoke.invoke_codd_gate(status, "verify", run=run)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "codd-gate のバージョンが対応下限未満")

    def test_usable_but_no_findings_reason_falls_back_to_default_message(self):
        # findings が空リストでも binary が None なら usable=False（防御的な組み合わせ）。
        status = CoddGateStatus(binary=None, findings=[])
        result = invoke.invoke_codd_gate(status, "verify", run=_raising_run(AssertionError("no")))
        self.assertEqual(result.status, "skipped")
        self.assertTrue(result.reason)  # 空文字列にはならない


class TestInvokeCoddGateRunsWhenUsable(unittest.TestCase):
    """usable=True なら CoddGateStatus.command() の argv で実行し、exit code で ok/failed を分ける。"""

    def test_exit_zero_is_ok_with_stdout_captured(self):
        status = CoddGateStatus(binary=["codd-gate"])
        run = _fake_run(returncode=0, stdout="OK: 一貫性ゲート通過\n")
        result = invoke.invoke_codd_gate(
            status, "verify", "--repos", "repos.json", "--strict", run=run
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "OK: 一貫性ゲート通過\n")
        self.assertEqual(result.reason, "")
        self.assertEqual(
            run.calls[0],
            ["codd-gate", "verify", "--repos", "repos.json", "--strict"],
        )

    def test_nonzero_exit_is_failed_with_reason_from_output(self):
        status = CoddGateStatus(binary=["codd-gate"])
        run = _fake_run(returncode=1, stdout="NG: ドリフトあり\n")
        result = invoke.invoke_codd_gate(status, "verify", "--strict", run=run)
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("NG: ドリフトあり", result.reason)
        self.assertIn("exit=1", result.reason)

    def test_timeout_is_skipped_not_failed(self):
        status = CoddGateStatus(binary=["codd-gate"])
        run = _raising_run(subprocess.TimeoutExpired(cmd="codd-gate", timeout=1))
        result = invoke.invoke_codd_gate(status, "verify", run=run, timeout=1)
        self.assertEqual(result.status, "skipped")
        self.assertIsNone(result.exit_code)
        self.assertIn("タイムアウト", result.reason)

    def test_oserror_on_launch_is_skipped_not_raised(self):
        status = CoddGateStatus(binary=["codd-gate"])
        run = _raising_run(OSError("binary vanished"))
        result = invoke.invoke_codd_gate(status, "verify", run=run)
        self.assertEqual(result.status, "skipped")
        self.assertIn("起動に失敗", result.reason)

    def test_never_raises_for_any_injected_run_failure(self):
        status = CoddGateStatus(binary=["codd-gate"])
        for exc in (OSError("x"), subprocess.SubprocessError("y"),
                    subprocess.TimeoutExpired(cmd="codd-gate", timeout=1)):
            with self.subTest(exc=type(exc).__name__):
                result = invoke.invoke_codd_gate(status, "verify", run=_raising_run(exc))
                self.assertEqual(result.status, "skipped")


if __name__ == "__main__":
    unittest.main()
