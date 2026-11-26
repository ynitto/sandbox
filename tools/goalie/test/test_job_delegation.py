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

    def test_legacy_work_directory_requires_rename(self):
        args = ("--dir", str(self.goal))
        self.assert_ok(self.call(GOALIE, "init", *args))
        current = self.goal / ".goalie"
        legacy = self.goal / ".dot"
        current.rename(legacy)
        report = legacy / "routines-review.json"
        body = json.dumps({"reviewed_at": 0, "findings": []})
        report.write_text(body, encoding="utf-8")

        for command in (("init",), ("status",), ("event", "--text", "test"),
                        ("harvest",), ("proposals", "list")):
            with self.subTest(command=command):
                result = self.call(GOALIE, *command, *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn(".dot フォルダが残っています。.goalie に名前変更してください", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse(current.exists())

        self.assertEqual(list(legacy.iterdir()), [report])
        self.assertEqual(report.read_text(encoding="utf-8"), body)
        legacy.rename(current)
        self.assert_ok(self.call(GOALIE, "status", *args))
        self.assert_ok(self.call(GOALIE, "event", *args, "--text", "test"))
        self.assertEqual((current / report.name).read_text(encoding="utf-8"), body)

    def test_hook_denies_tool_until_work_directory_is_renamed(self):
        self.assert_ok(self.call(GOALIE, "init", "--dir", str(self.goal)))
        current = self.goal / ".goalie"
        current.rename(self.goal / ".dot")

        result = subprocess.run(
            [sys.executable, str(GOALIE), "hook"],
            input=json.dumps({"cwd": str(self.goal), "toolName": "think"}),
            capture_output=True, encoding="utf-8", timeout=15,
        )

        self.assert_ok(result)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn(".goalie に名前変更してください", decision["permissionDecisionReason"])
        self.assertFalse(current.exists())

    def test_init_does_not_modify_directory_with_only_legacy_work(self):
        legacy = self.goal / ".dot"
        legacy.mkdir(parents=True)

        result = self.call(GOALIE, "init", "--dir", str(self.goal))

        self.assertEqual(result.returncode, 2)
        self.assertIn(".dot フォルダが残っています。.goalie に名前変更してください", result.stderr)
        self.assertEqual(list(self.goal.iterdir()), [legacy])

    def test_status_uses_current_work_directory_when_legacy_work_exists(self):
        args = ("--dir", str(self.goal))
        self.assert_ok(self.call(GOALIE, "init", *args, "--name", "current-goal"))
        (self.goal / ".dot").mkdir()

        result = self.call(GOALIE, "status", *args)

        self.assert_ok(result)
        self.assertIn("名前: current-goal", result.stdout)

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
