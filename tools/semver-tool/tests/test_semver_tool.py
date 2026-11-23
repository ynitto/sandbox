import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "semver_tool.py"


class SemverToolTests(unittest.TestCase):
    def assert_cli(self, *args, code=0, output="", error=False, error_text=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stdout, output + "\n" if output else "")
        self.assertEqual(bool(result.stderr), error)
        if error_text is not None:
            self.assertIn(error_text, result.stderr)

    def test_check_accepts_valid_versions(self):
        for version in ("0.0.0", "v1.2.3-0.alpha-1+001.BUILD", "1.0.0-01a"):
            with self.subTest(version=version):
                self.assert_cli("check", version, output="valid")

    def test_check_rejects_invalid_versions(self):
        for version in ("01.2.3", "1.2", "1.2.3-01", "1.2.3+a..b",
                        "1.2.3-", "1.2.3+", "1.2.3-é", "1.2.3\n", "VV1.2.3"):
            with self.subTest(version=version):
                self.assert_cli("check", version, code=1, error=True)

    def test_official_precedence_chain(self):
        versions = (
            "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
            "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0",
        )
        for a, b in zip(versions, versions[1:]):
            with self.subTest(a=a, b=b):
                self.assert_cli("compare", a, b, output="-1")
                self.assert_cli("compare", b, a, output="1")

    def test_compare_core_and_build_metadata(self):
        self.assert_cli("compare", "2.0.0", "10.0.0", output="-1")
        self.assert_cli("compare", "1.0.0", "1.0.0", output="0")
        self.assert_cli("compare", "v1.0.0+001", "1.0.0+abc", output="0")

    def test_compare_prerelease_prefix(self):
        self.assert_cli("compare", "1.0.0-alpha", "1.0.0-alpha.0", output="-1")

    def test_bump_core(self):
        for part, expected in (("major", "2.0.0"), ("minor", "1.3.0"),
                               ("patch", "1.2.10")):
            with self.subTest(part=part):
                self.assert_cli("bump", "v1.2.9-rc.3+build", "--part", part, output=expected)

    def test_bump_prerelease(self):
        for version, options, expected in (
            ("1.2.3", [], "1.2.3-rc.1"),
            ("1.2.3+build", ["--pre-id", "beta"], "1.2.3-beta.1"),
            ("1.2.3-alpha.9", [], "1.2.3-alpha.10"),
            ("1.2.3-alpha", [], "1.2.3-alpha.1"),
            ("1.2.3-9", [], "1.2.3-10"),
        ):
            with self.subTest(version=version, options=options):
                self.assert_cli("bump", version, "--part", "pre", *options, output=expected)

    def test_large_numbers(self):
        large = "9" * 4500
        bumped = "1" + "0" * 4500
        self.assert_cli("bump", f"1.2.{large}", "--part", "patch", output=f"1.2.{bumped}")
        self.assert_cli("compare", f"{large}.0.0", f"{bumped}.0.0", output="-1")

    def assert_ranges(self, cases):
        for version, expression, matches in cases:
            with self.subTest(version=version, expression=expression):
                self.assert_cli("satisfies", version, expression,
                                code=0 if matches else 1, output="true" if matches else "false")

    def test_caret_ranges(self):
        self.assert_ranges((
            ("1.9.0", "^1.2.3", True), ("2.0.0", "^1.2.3", False),
            ("0.2.3", "^0.2.3", True), ("0.2.2", "^0.2.3", False),
            ("0.3.0-rc.1", "^0.2.3", True),
            ("0.3.0", "^0.2.3", False), ("0.0.3", "^0.0.3", True),
            ("0.0.4", "^0.0.3", False),
            ("0.0.0", "^0.0.0", True), ("0.0.1", "^0.0.0", False),
        ))

    def test_partial_caret_ranges(self):
        self.assert_ranges((
            ("0.9.9", "^1", False), ("1.0.0", "^1", True), ("2.0.0", "^1", False),
            ("1.1.9", "^1.2", False), ("1.2.0", "^1.2", True),
            ("1.9.0", "^1.2", True), ("2.0.0", "^1.2", False),
            ("0.0.0", "^0", True), ("0.9.0", "^0", True), ("1.0.0", "^0", False),
            ("0.1.9", "^0.2", False), ("0.2.0", "^0.2", True), ("0.3.0", "^0.2", False),
            ("0.0.0", "^0.0", True), ("0.0.1", "^0.0", True), ("0.1.0", "^0.0", False),
            ("1.2.0", "^v1.2", True),
        ))

    def test_tilde_ranges(self):
        self.assert_ranges((
            ("1.2.2", "~1.2.3", False), ("1.2.3", "~1.2.3", True),
            ("1.2.9", "~1.2.3", True), ("1.3.0", "~1.2.3", False),
            ("0.2.2", "~0.2.3", False), ("0.2.9", "~0.2.3", True),
            ("0.3.0", "~0.2.3", False), ("0.2.0", "~0.2", True),
            ("0.3.0", "~0.2", False), ("0.9.0", "~0", True),
            ("1.0.0", "~0", False), ("1.2.0", "~1.2", True), ("1.5.0", "~1", True),
        ))

    def test_wildcard_ranges(self):
        self.assert_ranges((
            ("1.2.9", "1.2.x", True), ("1.3.0", "1.2.x", False),
            ("1.0.0", "1.x", True), ("2.0.0", "1.x", False),
            ("0.0.0-rc.1", "*", True), ("9.0.0", "x", True),
        ))

    def test_exact_and_comparator_ranges(self):
        self.assert_ranges((
            ("v1.2.3+one", "1.2.3", True), ("1.2.3", "v1.2.3+two", True),
            ("1.2.3-rc.1", "1.2.3", False), ("1.2.3", "=1.2.3", True),
            ("1.1.9", ">=1.2.0", False), ("1.2.0", ">=1.2.0", True),
            ("1.0.0", ">1.0.0", False), ("1.0.1", ">1.0.0", True),
            ("1.2.0", ">=1.2.0 <2.0.0", True), ("1.1.9", ">=1.2.0 <2.0.0", False),
            ("2.0.0", ">=1.2.0 <2.0.0", False),
            ("2.0.0-rc.1", ">=1.2.0 <2.0.0", True),
            ("2.0.0", ">1.0.0 <=2.0.0", True), ("1.0.0", ">1.0.0 <=2.0.0", False),
        ))

    def test_invalid_arguments_return_two(self):
        for args in (
            (), ("check",), ("compare", "1.2.3", "bad"), ("compare", "bad", "1.2.3"),
            ("bump", "bad", "--part", "major"), ("bump", "1.2.3"),
            ("bump", "1.2.3", "--part", "build"),
            ("bump", "1.2.3", "--part", "pre", "--pre-id", "01"),
            ("satisfies", "bad", "*"),
            ("satisfies", "1.2.3", ">2.0.0 <bad"), ("satisfies", "1.2.3", "~01"),
            ("satisfies", "1.2.3", "1.0.0 || 1.2.3"), ("satisfies", "1.2.3", "1.02.x"),
        ):
            with self.subTest(args=args):
                self.assert_cli(*args, code=2, error=True)

    def test_empty_range_reports_reason(self):
        self.assert_cli("satisfies", "1.2.3", "", code=2, error=True,
                        error_text="empty version range")
