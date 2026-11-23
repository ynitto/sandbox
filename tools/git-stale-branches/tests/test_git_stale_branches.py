import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "git_stale_branches.py"
OLD = "2000-01-01T00:00:00Z"
RECENT = "2099-01-01T00:00:00Z"


@unittest.skipUnless(shutil.which("git"), "git is required")
class GitStaleBranchesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CEILING_DIRECTORIES=str(self.root.parent))
        self.git("init", "-b", "main")
        self.git("config", "--local", "user.name", "Test User")
        self.git("config", "--local", "user.email", "test@example.invalid")
        self.commit("Initial", OLD)
        for name in ("master", "develop", "keep", "also-keep"):
            self.git("branch", name)
        self.git("switch", "-c", "merged")
        self.commit("Merged change", OLD)
        self.git("switch", "main")
        self.git("merge", "--ff-only", "merged")
        self.git("switch", "-c", "old")
        self.commit("Old unmerged change", OLD)
        self.git("switch", "main")
        self.git("switch", "-c", "recent")
        self.commit("Recent unmerged change", RECENT)
        self.git("switch", "main")
        self.git("switch", "-c", "current")

    def git(self, *args, env=None):
        return subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env if env is None else env,
            capture_output=True, text=True, check=True,
        ).stdout

    def commit(self, message, date):
        self.git("commit", "--allow-empty", "-m", message,
                 env=dict(self.env, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date))

    def cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=cwd or self.repo,
            env=self.env, capture_output=True, text=True,
        )

    def branch_names(self):
        return set(self.git(
            "for-each-ref", "--format=%(refname:short)", "refs/heads/"
        ).splitlines())

    def test_json_report_and_protection(self):
        before = self.branch_names()
        result = self.cli("--json", "--protect", "keep", "--protect", "also-keep")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)
        dates = [row["last_commit_date"] for row in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(dates[0], "2000-01-01T00:00:00+00:00")
        branches = {row["name"]: row for row in rows}
        self.assertIs(branches["merged"]["merged"], True)
        self.assertIs(branches["old"]["merged"], False)
        self.assertGreater(branches["old"]["age_days"], 90)
        self.assertIs(branches["recent"]["candidate"], False)
        self.assertIs(branches["current"]["current"], True)
        self.assertEqual({row["name"] for row in rows if row["candidate"]},
                         {"merged", "old"})
        for name in ("main", "master", "develop", "current", "keep", "also-keep"):
            self.assertIs(branches[name]["protected"], True)
        self.assertEqual(self.branch_names(), before)

    def test_text_report_and_delete_only_merged_candidates(self):
        before = self.branch_names()
        result = self.cli("--protect", "keep", "--protect", "also-keep", "--delete-merged")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Last commit (UTC)", result.stdout)
        self.assertIn("Candidates: merged, old", result.stdout)
        self.assertIn("Deleted: merged", result.stdout)
        self.assertEqual(self.branch_names(), before - {"merged"})

    def test_custom_base_and_days(self):
        self.git("branch", "old-peer", "old")
        result = self.cli("--base", "old", "--days", "100000", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        branches = {row["name"]: row for row in json.loads(result.stdout)}
        self.assertIs(branches["merged"]["merged"], True)
        self.assertIs(branches["old"]["candidate"], False)
        self.assertIs(branches["old-peer"]["merged"], True)
        self.assertIs(branches["recent"]["candidate"], False)

        result = self.cli("--days", "100000", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        branches = {row["name"]: row for row in json.loads(result.stdout)}
        self.assertIs(branches["old"]["candidate"], False)
        self.assertIs(branches["old-peer"]["merged"], False)

    def test_delete_failure_keeps_json_valid_and_continues(self):
        self.git("worktree", "add", str(self.root / "linked"), "merged")
        result = self.cli("--delete-merged", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertIsInstance(json.loads(result.stdout), list)
        self.assertIn("Failed to delete 'merged'", result.stderr)
        self.assertIn("Deleted: also-keep, keep", result.stderr)
        self.assertIn("merged", self.branch_names())
        self.assertNotIn("keep", self.branch_names())

    def test_missing_base(self):
        result = self.cli("--base", "missing", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_outside_repository(self):
        result = self.cli(cwd=self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a git repository", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
