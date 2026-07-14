"""codd_gate_detect / codd_gate_status の単体テスト（標準ライブラリ unittest）。

CLI あり（バージョン互換）・CLI なし・バージョン非互換の3ケースで、検出結果
（resolve_codd_gate / get_version が返す生の判定値）と、その結果が CoddGateStatus の
no-op 縮退（usable=False → command() が None）へ正しく合流することを検証する。
subprocess は起動せず、`which=`/`run=` の依存性注入（agent-project.py の
`doctor_env_findings(cfg, which=shutil.which)` と同じパターン）で全分岐を決定的に再現する。

    python -m unittest discover -s tools/agent-project/tests
"""
import subprocess
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codd_gate_detect as detect
import codd_gate_status as status


def _fake_run(returncode=0, stdout="", stderr=""):
    """subprocess.run 互換のフェイク。呼び出された argv を .calls に記録する。"""
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


class TestCoddGateDetectResolution(unittest.TestCase):
    """resolve_codd_gate の実在解決連鎖（explicit → PATH → 同梱パス）。"""

    def test_resolve_codd_gate_found_via_path(self):
        which = lambda name: "/usr/local/bin/codd-gate" if name == detect.BINARY_NAME else None
        self.assertEqual(detect.resolve_codd_gate(which=which), ["/usr/local/bin/codd-gate"])

    def test_resolve_codd_gate_explicit_overrides_which(self):
        which = mock.Mock(side_effect=AssertionError("explicit 指定時は which を呼んではいけない"))
        self.assertEqual(
            detect.resolve_codd_gate("/opt/tools/codd-gate", which=which),
            ["/opt/tools/codd-gate"],
        )

    def test_resolve_codd_gate_explicit_py_script_uses_interpreter(self):
        result = detect.resolve_codd_gate("/opt/tools/codd-gate.py", which=lambda _n: None)
        self.assertEqual(result, [sys.executable, "/opt/tools/codd-gate.py"])

    def test_resolve_codd_gate_absent_when_path_and_bundled_both_missing(self):
        which = lambda _name: None
        with mock.patch.object(detect.Path, "exists", return_value=False):
            result = detect.resolve_codd_gate(which=which)
        self.assertIsNone(result)

    def test_resolve_codd_gate_found_via_bundled_path_when_path_lookup_fails(self):
        # PATH（which）では見つからないが、tools/codd-gate/codd-gate.py の同梱パスは実在するケース
        # （3段解決連鎖の中間分岐——「found via PATH」「absent（両方無し）」の間の唯一の未検証経路）。
        which = lambda _name: None
        expected_local = detect.Path(detect.__file__).resolve().parent.parent / "codd-gate" / "codd-gate.py"
        with mock.patch.object(detect.Path, "exists", return_value=True):
            result = detect.resolve_codd_gate(which=which)
        self.assertEqual(result, [sys.executable, str(expected_local)])


class TestCoddGateDetectVersion(unittest.TestCase):
    """get_version — バージョン取得は失敗をすべて「不明」（None）に倒す。"""

    def test_get_version_parses_compatible_version(self):
        run = _fake_run(0, stdout="codd-gate 1.0.0\n")
        self.assertEqual(detect.get_version(["codd-gate"], run=run), (1, 0, 0))
        self.assertEqual(run.calls[-1], ["codd-gate", "--version"])

    def test_get_version_parses_newer_compatible_version(self):
        run = _fake_run(0, stdout="codd-gate 2.4.10\n")
        self.assertEqual(detect.get_version(["codd-gate"], run=run), (2, 4, 10))

    def test_get_version_unknown_on_nonzero_exit(self):
        run = _fake_run(2, stdout="")
        self.assertIsNone(detect.get_version(["codd-gate"], run=run))

    def test_get_version_unknown_on_unparsable_stdout(self):
        run = _fake_run(0, stdout="not a version string\n")
        self.assertIsNone(detect.get_version(["codd-gate"], run=run))

    def test_get_version_unknown_on_timeout(self):
        run = _raising_run(subprocess.TimeoutExpired(cmd="codd-gate", timeout=5))
        self.assertIsNone(detect.get_version(["codd-gate"], run=run))

    def test_get_version_unknown_on_binary_missing(self):
        run = _raising_run(FileNotFoundError("no such file"))
        self.assertIsNone(detect.get_version(["codd-gate"], run=run))


class TestCoddGateStatusNoOpDegradation(unittest.TestCase):
    """CoddGateStatus の no-op 縮退を、CLI あり（バージョン互換）・CLI なし・
    バージョン非互換の3ケースで検証する（d1 3節のフォールバック方針表に対応）。
    """

    def test_cli_present_and_version_compatible_is_usable(self):
        which = lambda name: "/usr/local/bin/codd-gate" if name == detect.BINARY_NAME else None
        binary = detect.resolve_codd_gate(which=which)
        self.assertEqual(binary, ["/usr/local/bin/codd-gate"])

        run = _fake_run(0, stdout="codd-gate 1.0.0\n")
        version = detect.get_version(binary, run=run)
        self.assertEqual(version, (1, 0, 0))

        result = status.build_status(binary, version=version, version_known=True, schema_ok=True)
        self.assertTrue(result.usable)
        self.assertEqual(result.findings, [])
        self.assertEqual(result.reason, "")
        self.assertEqual(
            result.command("verify", "--strict"),
            ["/usr/local/bin/codd-gate", "verify", "--strict"],
        )

        integrated = status.detect_status(which=which)
        self.assertTrue(integrated.usable)
        self.assertEqual(integrated.binary, binary)

    def test_cli_absent_degrades_to_noop(self):
        which = lambda _name: None
        with mock.patch.object(detect.Path, "exists", return_value=False):
            binary = detect.resolve_codd_gate(which=which)
            integrated = status.detect_status(which=which)
        self.assertIsNone(binary)

        result = status.build_status(binary)
        self.assertFalse(result.usable)
        self.assertIsNone(result.command("verify", "--strict"))
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0]["severity"], "info")
        self.assertIn("見つからない", result.reason)

        # detect_status（実在検出のみのエントリポイント）も同じ no-op 縮退へ合流する
        self.assertFalse(integrated.usable)
        self.assertIsNone(integrated.command("verify", "--strict"))

    def test_cli_absent_survives_unexpected_resolution_exception(self):
        # detect_status は resolve_codd_gate が想定外の例外を出しても「未検出」へ縮退させる
        which = mock.Mock(side_effect=OSError("environment I/O failure"))
        result = status.detect_status(which=which)
        self.assertFalse(result.usable)
        self.assertIsNone(result.command("verify"))

    def test_version_incompatible_degrades_to_noop(self):
        binary = ["codd-gate"]
        run = _fake_run(0, stdout="codd-gate 0.9.0\n")
        version = detect.get_version(binary, run=run)
        self.assertEqual(version, (0, 9, 0))
        self.assertLess(version, status.MIN_SUPPORTED_VERSION)

        result = status.build_status(binary, version=version, version_known=True, schema_ok=True)
        self.assertFalse(result.usable)
        self.assertIsNone(result.command("verify", "--strict"))
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0]["severity"], "warn")
        self.assertIn("下限未満", result.reason)

    def test_version_unknown_also_degrades_to_noop(self):
        binary = ["codd-gate"]
        run = _fake_run(0, stdout="garbage output\n")
        version = detect.get_version(binary, run=run)
        self.assertIsNone(version)

        result = status.build_status(binary, version=version, version_known=False)
        self.assertFalse(result.usable)
        self.assertIsNone(result.command("verify"))
        self.assertIn("バージョンを取得できない", result.reason)


class TestCoddGateDetectCapabilitiesAndSchema(unittest.TestCase):
    """detect_capabilities / check_repos_schema_compat の生の判定値（d1 2.3(a)）。"""

    def test_detect_capabilities_all_supported(self):
        def run(argv, **kwargs):
            if argv[-1] == "--help" and len(argv) == 2:
                stdout = "usage: codd-gate ... {scan,impact,verify,tasks,check} ..."
            elif argv[-2:] in (["verify", "--help"], ["tasks", "--help"]):
                stdout = "options:\n  --debt  負債ラチェットを含める"
            else:
                return subprocess.CompletedProcess(argv, 2, stdout="", stderr="unexpected")
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        self.assertEqual(
            detect.detect_capabilities(["codd-gate"], run=run),
            {"verify": True, "tasks": True, "debt": True},
        )

    def test_detect_capabilities_partial_debt_support_is_not_usable(self):
        # verify は --debt に対応しているが tasks は対応していない古いバイナリ（a2 の申し送り）
        def run(argv, **kwargs):
            if argv[-1] == "--help" and len(argv) == 2:
                stdout = "usage: codd-gate ... {scan,impact,verify,tasks,check} ..."
            elif argv[-2:] == ["verify", "--help"]:
                stdout = "options:\n  --debt  負債ラチェットを含める"
            elif argv[-2:] == ["tasks", "--help"]:
                stdout = "options:\n  (this old subcommand takes no extra flags)"
            else:
                return subprocess.CompletedProcess(argv, 2, stdout="", stderr="unexpected")
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        capabilities = detect.detect_capabilities(["codd-gate"], run=run)
        self.assertTrue(capabilities["verify"])
        self.assertTrue(capabilities["tasks"])
        self.assertFalse(capabilities["debt"])

    def test_detect_capabilities_all_false_when_help_probe_fails(self):
        run = _raising_run(subprocess.TimeoutExpired(cmd="codd-gate", timeout=5))
        self.assertEqual(
            detect.detect_capabilities(["codd-gate"], run=run),
            {"verify": False, "tasks": False, "debt": False},
        )

    def test_check_repos_schema_compat_accepts_valid_registry(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "repos.json"
            p.write_text(json.dumps({"_generated": "note", "sandbox": {"base": "main"}}), encoding="utf-8")
            ok, detail = detect.check_repos_schema_compat(p)
        self.assertTrue(ok)
        self.assertEqual(detail, "")

    def test_check_repos_schema_compat_rejects_non_object_entry(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "repos.json"
            p.write_text(json.dumps({"sandbox": "not-an-object"}), encoding="utf-8")
            ok, detail = detect.check_repos_schema_compat(p)
        self.assertFalse(ok)
        self.assertIn("sandbox", detail)

    def test_check_repos_schema_compat_rejects_missing_file(self):
        ok, detail = detect.check_repos_schema_compat("/no/such/repos.json")
        self.assertFalse(ok)
        self.assertTrue(detail)


if __name__ == "__main__":
    unittest.main()
