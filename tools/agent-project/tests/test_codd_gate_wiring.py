"""codd_gate_wiring の単体テスト（標準ライブラリ unittest）。

regression_cmd/intake_cmd の結線判定（regression_wired/intake_wired）、推奨コマンド組み立て
（recommend_regression_cmd/recommend_intake_cmd）、実測配線（detect_wiring）の3ケース
（未検出・検出済み未結線・検出済み結線済み）を、subprocess を起動せず `which=`/`run=` の
依存性注入で決定的に検証する（test_codd_gate_detect.py と同じパターン）。

    python -m unittest discover -s tools/agent-project/tests
"""
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codd_gate_detect as detect
import codd_gate_status as status
import codd_gate_wiring as wiring


def _fake_run(returncode=0, stdout="", stderr=""):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)
    return run


class TestWiredDetection(unittest.TestCase):
    """regression_wired/intake_wired — 手書き文字列が codd-gate を指しているかの判定。"""

    def test_regression_wired_matches_hand_written_config(self):
        cmd = 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
        self.assertTrue(wiring.regression_wired(cmd))

    def test_regression_wired_false_when_missing(self):
        self.assertFalse(wiring.regression_wired(None))
        self.assertFalse(wiring.regression_wired(""))
        self.assertFalse(wiring.regression_wired("pytest -q"))

    def test_regression_wired_false_when_base_flag_absent(self):
        self.assertFalse(wiring.regression_wired("codd-gate verify --strict"))

    def test_intake_wired_matches_hand_written_config(self):
        cmd = "codd-gate tasks --debt --repos .agent-project/repos.json"
        self.assertTrue(wiring.intake_wired(cmd))

    def test_intake_wired_false_when_debt_flag_absent(self):
        self.assertFalse(wiring.intake_wired("codd-gate tasks"))


class TestRecommendedCommands(unittest.TestCase):
    def test_recommend_regression_cmd_keeps_shell_var_literal(self):
        cmd = wiring.recommend_regression_cmd(".agent-project/repos.json")
        self.assertEqual(
            cmd, 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json')
        self.assertTrue(wiring.regression_wired(cmd))  # 自己無矛盾: 推奨コマンド自身が結線判定を満たす

    def test_recommend_intake_cmd(self):
        cmd = wiring.recommend_intake_cmd(".agent-project/repos.json")
        self.assertEqual(cmd, "codd-gate tasks --debt --repos .agent-project/repos.json")
        self.assertTrue(wiring.intake_wired(cmd))


class TestJudgeWiringPure(unittest.TestCase):
    """judge_wiring — I/O なしの純粋関数として、実測値を渡すだけで判定できることを検証する。"""

    def test_usable_and_unwired_recommends_both(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result, regression_cmd=None, intake_cmd=None,
            capabilities={"verify": True, "tasks": True, "debt": True},
            repos_path=".agent-project/repos.json")
        self.assertTrue(judgment.usable)
        self.assertFalse(judgment.fully_wired)
        self.assertTrue(judgment.actionable)
        self.assertEqual(judgment.recommended_regression_cmd,
                          'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json')
        self.assertEqual(judgment.recommended_intake_cmd,
                          "codd-gate tasks --debt --repos .agent-project/repos.json")

    def test_usable_and_already_wired_recommends_nothing(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result,
            regression_cmd='codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json',
            intake_cmd="codd-gate tasks --debt --repos .agent-project/repos.json",
            repos_path=".agent-project/repos.json")
        self.assertTrue(judgment.fully_wired)
        self.assertFalse(judgment.actionable)
        self.assertIsNone(judgment.recommended_regression_cmd)
        self.assertIsNone(judgment.recommended_intake_cmd)

    def test_not_usable_recommends_nothing_even_when_unwired(self):
        judgment = wiring.judge_wiring(
            status.build_status(None), regression_cmd=None, intake_cmd=None,
            repos_path=".agent-project/repos.json")
        self.assertFalse(judgment.usable)
        self.assertFalse(judgment.actionable)
        self.assertIsNone(judgment.recommended_regression_cmd)
        self.assertIsNone(judgment.recommended_intake_cmd)

    def test_missing_repos_path_recommends_nothing(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(result, regression_cmd=None, intake_cmd=None,
                                        repos_path=None)
        self.assertIsNone(judgment.recommended_regression_cmd)
        self.assertIsNone(judgment.recommended_intake_cmd)

    def test_capability_gate_suppresses_recommendation(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result, regression_cmd=None, intake_cmd=None,
            capabilities={"verify": True, "tasks": True, "debt": False},
            repos_path=".agent-project/repos.json")
        self.assertIsNotNone(judgment.recommended_regression_cmd)
        self.assertIsNone(judgment.recommended_intake_cmd)  # --debt 非対応なら intake は推奨しない


class TestDetectWiringIntegrated(unittest.TestCase):
    """detect_wiring — resolve/get_version/check_repos_schema_compat/detect_capabilities を
    一気通貫で実測配線し、WiringJudgment まで組み立てる（依存性注入で subprocess は起動しない）。"""

    def test_binary_absent_degrades_to_noop(self):
        which = lambda _name: None
        # 同梱パス（tools/codd-gate/codd-gate.py）も無い状態を再現する
        # （test_codd_gate_detect.py の test_resolve_codd_gate_absent_when_path_and_bundled_both_missing と同じ手法）
        with mock.patch.object(detect.Path, "exists", return_value=False):
            judgment = wiring.detect_wiring(which=which)
        self.assertFalse(judgment.usable)
        self.assertFalse(judgment.actionable)
        self.assertEqual(len(judgment.status.findings), 1)

    def test_binary_present_and_unwired_recommends_commands(self):
        which = lambda name: "/usr/local/bin/codd-gate" if name == detect.BINARY_NAME else None

        def run(argv, **kwargs):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, stdout="codd-gate 1.2.0\n")
            if argv[-1] == "--help" and len(argv) == 2:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="usage: codd-gate {verify,tasks} ...\n")
            return subprocess.CompletedProcess(argv, 0, stdout="--debt\n")

        with tempfile.TemporaryDirectory() as d:
            repos_path = Path(d) / "repos.json"
            repos_path.write_text('{"svc": {"url": "https://example/svc.git"}}', encoding="utf-8")

            judgment = wiring.detect_wiring(
                regression_cmd=None, intake_cmd=None, repos_path=repos_path,
                which=which, run=run)

        self.assertTrue(judgment.usable)
        self.assertTrue(judgment.actionable)
        self.assertIn("codd-gate verify --base", judgment.recommended_regression_cmd)
        self.assertIn("codd-gate tasks --debt", judgment.recommended_intake_cmd)

    def test_schema_incompatible_repos_json_degrades_to_noop(self):
        which = lambda name: "/usr/local/bin/codd-gate" if name == detect.BINARY_NAME else None
        run = _fake_run(0, stdout="codd-gate 1.2.0\n")

        with tempfile.TemporaryDirectory() as d:
            repos_path = Path(d) / "repos.json"
            repos_path.write_text("[]", encoding="utf-8")  # トップレベルが object でない = 非互換

            judgment = wiring.detect_wiring(repos_path=repos_path, which=which, run=run)

        self.assertFalse(judgment.usable)
        self.assertFalse(judgment.actionable)

    def test_missing_repos_json_file_treated_as_schema_unknown_not_incompatible(self):
        which = lambda name: "/usr/local/bin/codd-gate" if name == detect.BINARY_NAME else None
        run = _fake_run(0, stdout="codd-gate 1.2.0\n")

        judgment = wiring.detect_wiring(
            repos_path="/nonexistent/repos.json", which=which, run=run)

        self.assertTrue(judgment.usable)


class TestDoctorFindings(unittest.TestCase):
    def test_not_usable_reuses_status_findings(self):
        judgment = wiring.judge_wiring(status.build_status(None), None, None)
        findings = wiring.doctor_findings(judgment)
        self.assertEqual(findings, judgment.status.findings)

    def test_fully_wired_has_no_findings(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result,
            regression_cmd='codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json',
            intake_cmd="codd-gate tasks --debt --repos repos.json",
            repos_path="repos.json")
        self.assertEqual(wiring.doctor_findings(judgment), [])

    def test_actionable_reports_info_findings_with_fix_suggestion(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result, regression_cmd=None, intake_cmd=None,
            capabilities={"verify": True, "tasks": True, "debt": True},
            repos_path="repos.json")
        findings = wiring.doctor_findings(judgment)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f["severity"] == "info" for f in findings))
        self.assertIn(judgment.recommended_regression_cmd, findings[0]["fix"])
        self.assertIn(judgment.recommended_intake_cmd, findings[1]["fix"])


if __name__ == "__main__":
    unittest.main()
