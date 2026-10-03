from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHECK = ROOT / ".github" / "skills" / "self-checking" / "scripts" / "check.py"
SPEC = importlib.util.spec_from_file_location("self_checking_check", CHECK)
assert SPEC and SPEC.loader
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


class SelfCheckingTestFileDetectionTests(unittest.TestCase):
    def test_production_names_containing_test_or_spec_are_not_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for name in ("contest.py", "latest.py", "specification.py"):
                path = Path(tmp) / name
                path.write_text("value = 1\n", encoding="utf-8")
                files.append(str(path))

            result = check.check_code(files, "")

        self.assertIn("test_presence", result["checks"])
        self.assertFalse(result["checks"]["test_presence"]["passed"])
        self.assertIn("test_presence", result["failed_checks"])

    def test_common_test_names_and_test_directories_are_tests(self):
        cases = (
            "test_app.py",
            "app_test.py",
            "app.spec.js",
            "spec_app.rb",
            "tests/helper.py",
            "__tests__/helper.js",
        )
        for relative in cases:
            with self.subTest(relative=relative):
                self.assertTrue(check._is_test_file(relative))

    def test_source_plus_real_test_passes_test_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "contest.py"
            test = root / "tests" / "contest.py"
            test.parent.mkdir()
            source.write_text("value = 1\n", encoding="utf-8")
            test.write_text("assert True\n", encoding="utf-8")

            result = check.check_code([str(source), str(test)], "")

        self.assertTrue(result["checks"]["test_presence"]["passed"])
        self.assertNotIn("test_presence", result["failed_checks"])


if __name__ == "__main__":
    unittest.main()
