"""旧エントリーポイント、作業フォルダの移行、runner への委譲を検証する。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GOALIE = ROOT / "tools" / "goalie" / "goalie.py"
RUNNER = GOALIE.with_name("runner.py")


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.goal = self.root / "goal"
        self.assert_ok(self.call(GOALIE, "init", "--dir", str(self.goal)))
        self.mailbox = self.root / "mailbox"
        self.mailbox.mkdir()

    def call(self, script, *args):
        return subprocess.run([sys.executable, str(script), *args],
                              capture_output=True, encoding="utf-8", timeout=15)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shims_start(self):
        self.assert_ok(self.call(ROOT / "tools" / "agent-dots" / "dot.py",
                                 "status", "--dir", str(self.goal)))
        self.assert_ok(self.call(ROOT / "tools" / "copilot-app-jobs" / "copilot_app_job.py",
                                 "status", "--dir", str(self.mailbox)))

    def test_first_write_migrates_and_preserves_files(self):
        current = self.goal / ".goalie"
        legacy = self.goal / ".dot"
        current.rename(legacy)
        files = {"discovery/signals.jsonl": '{"example": "index"}\n',
                 "runs/daily/r1.json": '{"example": "run"}\n'}
        for name, body in files.items():
            path = legacy / name
            path.parent.mkdir(parents=True)
            path.write_text(body, encoding="utf-8")

        self.assert_ok(self.call(GOALIE, "status", "--dir", str(self.goal)))
        self.assert_ok(self.call(GOALIE, "proposals", "list", "--dir", str(self.goal)))
        self.assertTrue(legacy.is_dir())
        self.assertFalse(current.exists())
        for message in ("first", "second"):
            self.assert_ok(self.call(GOALIE, "event", "--dir", str(self.goal), "--text", message))

        self.assertFalse(legacy.exists())
        self.assertFalse((current / "lock").exists())
        for name, body in files.items():
            self.assertEqual((current / name).read_text(encoding="utf-8"), body)
        journal = (self.goal / "journal.jsonl").read_text(encoding="utf-8")
        migrations = [row for line in journal.splitlines()
                      if (row := json.loads(line))["kind"] == "migration"]
        self.assertEqual([(row["from"], row["to"]) for row in migrations], [(".dot", ".goalie")])

    def test_existing_hook_file_keeps_its_name(self):
        legacy = self.goal / ".github" / "hooks" / "agent-dots.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("{}", encoding="utf-8")
        self.assert_ok(self.call(GOALIE, "hooks", "--dir", str(self.goal)))
        self.assertIn("preToolUse", json.loads(legacy.read_text(encoding="utf-8"))["hooks"])
        self.assertFalse(legacy.with_name("goalie.json").exists())

    def test_current_work_folder_takes_precedence_over_legacy(self):
        current = self.goal / ".goalie"
        legacy = self.goal / ".dot"
        legacy.mkdir()
        marker = legacy / "marker.txt"
        marker.write_text("legacy", encoding="utf-8")

        policy_path = self.goal / "policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["routines"] = {"skills_dirs": [str(self.root / "skills")]}
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        (current / "routines-review.json").write_text(
            json.dumps({"reviewed_at": 0, "findings": []}), encoding="utf-8")
        status = self.call(GOALIE, "status", "--dir", str(self.goal))
        self.assert_ok(status)
        self.assertIn("要見直しなし", status.stdout)

        journal_path = self.goal / "journal.jsonl"
        journal_path.touch()
        self.assert_ok(self.call(GOALIE, "event", "--dir", str(self.goal), "--text", "test"))
        self.assertEqual(marker.read_text(encoding="utf-8"), "legacy")
        journal = journal_path.read_text(encoding="utf-8")
        self.assertNotIn("migration", [json.loads(line)["kind"] for line in journal.splitlines()])

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

    def test_shims_export_public_functions(self):
        for folder, module, target in (
            ("copilot-app-jobs", "copilot_app_job", self.mailbox),
            ("agent-dots", "dot", self.goal),
        ):
            with self.subTest(module=module):
                code = (
                    "import sys\n"
                    "sys.path.insert(0, sys.argv[1])\n"
                    f"import {module}\n"
                    "args = ['status', '--dir', sys.argv[2]]\n"
                    f"{module}.build_parser().parse_args(args)\n"
                    f"sys.exit({module}.main(args))\n"
                )
                self.assert_ok(self.call("-c", code, str(ROOT / "tools" / folder), str(target)))
