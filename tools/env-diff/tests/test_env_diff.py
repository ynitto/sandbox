import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CLI = Path(__file__).resolve().parents[1] / "env_diff.py"


class EnvDiffTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.a = Path(directory.name) / ".env.example"
        self.b = Path(directory.name) / ".env"

    def run_cli(self, a, b, *options):
        self.a.write_text(a, encoding="utf-8")
        if b is not None:
            self.b.write_text(b, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(CLI), *options, str(self.a), str(self.b)],
            capture_output=True,
            text=True,
        )

    def test_keys_present_in_only_one_file(self):
        result = self.run_cli("ONLY_A=private-a\n", "ONLY_B=private-b\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("A にだけあるキー\n  ONLY_A", result.stdout)
        self.assertIn("B にだけあるキー\n  ONLY_B", result.stdout)
        for secret in ("private-a", "private-b"):
            self.assertNotIn(secret, result.stdout)

    def test_different_values_are_hidden_by_default(self):
        result = self.run_cli("TOKEN=first-secret\n", "TOKEN=second-secret\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("値が違うキー\n  TOKEN", result.stdout)
        for secret in ("first-secret", "second-secret"):
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_show_values(self):
        result = self.run_cli("TOKEN=first-secret\n", "TOKEN=second-secret\n", "--show-values")
        self.assertEqual(result.returncode, 1)
        self.assertIn("A = 'first-secret'", result.stdout)
        self.assertIn("B = 'second-secret'", result.stdout)

    def test_duplicates_only_use_last_definition_in_comparison(self):
        a = "K=first-secret\n# comment\nexport K=shared-secret\n"
        b = "K=second-secret\nK=shared-secret\n"
        result = self.run_cli(a, b)
        self.assertEqual(result.returncode, 1)
        self.assertIn("A の重複キー\n  K (1, 3 行目)", result.stdout)
        self.assertIn("B の重複キー\n  K (1, 2 行目)", result.stdout)
        self.assertNotIn("値が違うキー", result.stdout)
        for secret in ("first-secret", "second-secret", "shared-secret"):
            self.assertNotIn(secret, result.stdout)

    def test_equivalent_files(self):
        a = "  # comment\n\n export TOKEN = 'a=b'  \n EMPTY=  \n"
        b = 'TOKEN="a=b"\nEMPTY=""\n'
        result = self.run_cli(a, b)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "差分なし")

    def test_empty_values_in_text(self):
        result = self.run_cli("ONLY_A=\nTOKEN=\n", "TOKEN=hidden-secret\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ONLY_A (空)", result.stdout)
        self.assertIn("TOKEN [A (空)]", result.stdout)
        self.assertNotIn("hidden-secret", result.stdout)

    def test_json_values_are_opt_in(self):
        a = "ONLY_A=\nTOKEN=discarded-secret\nTOKEN=first-secret\n"
        b = "ONLY_B=other-secret\nTOKEN=\n"
        result = self.run_cli(a, b, "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {
            "only_in_a": [{"key": "ONLY_A", "empty": True}],
            "only_in_b": [{"key": "ONLY_B", "empty": False}],
            "different": [{"key": "TOKEN", "a": {"empty": False}, "b": {"empty": True}}],
            "duplicates_a": [{"key": "TOKEN", "lines": [2, 3]}],
            "duplicates_b": [],
        })
        result = self.run_cli(a, b, "--json", "--show-values")
        data = json.loads(result.stdout)
        self.assertEqual(data["only_in_a"][0]["value"], "")
        self.assertEqual(data["only_in_b"][0]["value"], "other-secret")
        self.assertEqual(data["different"][0]["a"]["value"], "first-secret")
        self.assertEqual(data["different"][0]["b"]["value"], "")

    def test_missing_file(self):
        result = self.run_cli("TOKEN=present\n", None)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(self.b), result.stderr)

    def test_invalid_line_does_not_echo_input(self):
        result = self.run_cli("# comment\nraw-secret\n", "", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("2 行目", result.stderr)
        self.assertNotIn("raw-secret", result.stderr)

    def test_missing_arguments(self):
        result = subprocess.run(
            [sys.executable, str(CLI)], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ファイルA", result.stderr)


if __name__ == "__main__":
    unittest.main()
