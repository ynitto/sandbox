"""runner への委譲と .goalie 専用の作業フォルダを検証する。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GOALIE = ROOT / "tools" / "goalie" / "goalie.py"
RUNNER = GOALIE.with_name("runner.py")


class JobDelegationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.goal = self.root / "goal"
        self.mailbox = self.root / "mailbox"
        self.mailbox.mkdir()

    def call(self, script, *args):
        return subprocess.run([sys.executable, str(script), *args],
                              capture_output=True, encoding="utf-8", timeout=15)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dot_only_directory_is_ignored(self):
        legacy = self.goal / ".dot"
        legacy.mkdir(parents=True)
        report = legacy / "routines-review.json"
        body = json.dumps({"reviewed_at": 0, "findings": []})
        report.write_text(body, encoding="utf-8")
        args = ("--dir", str(self.goal))
        current = self.goal / ".goalie"

        status = self.call(GOALIE, "status", *args)
        self.assertNotEqual(status.returncode, 0)
        self.assertIn("目標のフォルダではありません", status.stderr)
        self.assertFalse(current.exists())

        self.assert_ok(self.call(GOALIE, "init", *args))
        self.assertTrue(current.is_dir())
        policy_path = self.goal / "policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["routines"] = {"skills_dirs": [str(self.root / "skills")]}
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        current.rmdir()
        status = self.call(GOALIE, "status", *args)
        self.assert_ok(status)
        self.assertIn("業務の進行", status.stdout)
        self.assertNotIn("要見直しなし", status.stdout)
        self.assertFalse(current.exists())
        self.assert_ok(self.call(GOALIE, "event", *args, "--text", "test"))

        self.assertTrue(current.is_dir())
        self.assertEqual(list(legacy.iterdir()), [report])
        self.assertEqual(report.read_text(encoding="utf-8"), body)
        self.assertFalse((current / report.name).exists())

    def test_job_delegates_status(self):
        args = ("status", "--dir", str(self.mailbox))
        standalone = self.call(RUNNER, *args)
        delegated = self.call(GOALIE, "job", *args)
        self.assert_ok(standalone)
        self.assertEqual((delegated.returncode, delegated.stdout, delegated.stderr),
                         (standalone.returncode, standalone.stdout, standalone.stderr))

    def test_job_help_shows_runner_commands(self):
        delegated = self.call(GOALIE, "job", "--help")
        self.assert_ok(delegated)
        for command in ("run", "next", "finish", "status"):
            self.assertIn(command, delegated.stdout)

    def test_job_delegates_exit_code(self):
        args = ("status", "--dir", str(self.root / "missing"))
        standalone = self.call(RUNNER, *args)
        delegated = self.call(GOALIE, "job", *args)
        self.assertEqual(standalone.returncode, 2, standalone.stderr)
        self.assertEqual(delegated.returncode, standalone.returncode, delegated.stderr)
