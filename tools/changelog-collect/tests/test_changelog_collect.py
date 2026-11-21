from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import changelog_collect


CHANGELOG = "# CHANGELOG\n\n## [Unreleased]\n### 既存\n\n## [1.0.0]\n### リリース済み\n"


class ChangelogCollectTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.changelog = root / "CHANGELOG.md"
        self.changelog.write_text(CHANGELOG, encoding="utf-8")
        self.fragments = root / "unreleased"

    def fragment(self, name, body):
        self.fragments.mkdir(exist_ok=True)
        path = self.fragments / name
        path.write_text(body, encoding="utf-8")
        return path

    def collect(self, *options):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = changelog_collect.main([
                "--changelog", str(self.changelog),
                "--fragments-dir", str(self.fragments),
                *options,
            ])
        return code, stdout.getvalue(), stderr.getvalue()

    def read_changelog(self):
        return self.changelog.read_text(encoding="utf-8")

    def test_inserts_in_filename_order_and_deletes_fragments(self):
        second = self.fragment("20-second.md", "### 二番目\n\n- 二\n\n")
        first = self.fragment("10-first.md", "\n  ### 一番目\n\n- 一\n")
        readme = self.fragment("README.md", "### 説明")
        code, stdout, stderr = self.collect()

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(self.read_changelog(), CHANGELOG.replace(
            "### 既存", "### 一番目\n\n- 一\n\n### 二番目\n\n- 二\n\n### 既存",
        ))
        self.assertLess(stdout.index(first.name), stdout.index(second.name))
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue(readme.exists())

    def test_dry_run(self):
        fragment = self.fragment("new.md", "### 新規\n\n- 追加\n")
        code, stdout, stderr = self.collect("--dry-run")

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("### 新規\n\n- 追加\n\n", stdout)
        self.assertIn(fragment.name, stdout)
        self.assertEqual(self.read_changelog(), CHANGELOG)
        self.assertTrue(fragment.exists())

    def test_no_fragments_with_missing_or_empty_directory(self):
        for directory_exists in (False, True):
            with self.subTest(directory_exists=directory_exists):
                if directory_exists:
                    self.fragments.mkdir()
                code, stdout, stderr = self.collect()
                self.assertEqual(code, 0)
                self.assertIn("取り込むフラグメントはありません", stdout)
                self.assertEqual(stderr, "")
                self.assertEqual(self.read_changelog(), CHANGELOG)

    def test_missing_unreleased_heading(self):
        original = CHANGELOG.replace("## [Unreleased]", "## [Unreleased draft]")
        self.changelog.write_text(original, encoding="utf-8")
        fragment = self.fragment("new.md", "### 新規")
        code, _, stderr = self.collect()

        self.assertEqual(code, 1)
        self.assertIn("## [Unreleased]", stderr)
        self.assertEqual(self.read_changelog(), original)
        self.assertTrue(fragment.exists())

    def test_missing_changelog(self):
        self.changelog.unlink()
        fragment = self.fragment("new.md", "### 新規")
        code, _, stderr = self.collect()

        self.assertEqual(code, 1)
        self.assertIn(self.changelog.name, stderr)
        self.assertFalse(self.changelog.exists())
        self.assertTrue(fragment.exists())

    def test_skips_duplicate_and_collects_other_fragment(self):
        duplicate = self.fragment("duplicate.md", "### 別の見出し\n\n### リリース済み")
        fresh = self.fragment("new.md", "### 新規")
        code, stdout, stderr = self.collect()

        self.assertEqual(code, 0)
        self.assertIn(duplicate.name, stderr)
        self.assertIn("### リリース済み", stderr)
        self.assertIn(fresh.name, stdout)
        self.assertEqual(self.read_changelog(), CHANGELOG.replace("### 既存", "### 新規\n\n### 既存"))
        self.assertTrue(duplicate.exists())
        self.assertFalse(fresh.exists())

    def test_all_fragments_are_duplicates(self):
        duplicate = self.fragment("duplicate.md", "### 既存")
        code, _, stderr = self.collect()

        self.assertEqual(code, 0)
        self.assertIn(duplicate.name, stderr)
        self.assertIn("### 既存", stderr)
        self.assertEqual(self.read_changelog(), CHANGELOG)
        self.assertTrue(duplicate.exists())
