import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CLI = Path(__file__).resolve().parents[1] / "ticket_verify.py"


class TicketVerifyTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.cwd = Path(workspace.name)
        self.command_file = self.cwd / "verify-command.json"
        self.evidence = self.cwd / "evidence.json"
        self.markdown = self.cwd / "evidence.md"
        self.script = self.cwd / "smoke.py"
        self.argv = [sys.executable, str(self.script)]
        self.env = dict(os.environ, TICKET_VERIFY_ALLOW=json.dumps([self.argv]),
                        TICKET_VERIFY_TIMEOUT_SEC="5")
        self.env.pop("TICKET_VERIFY_MAX_EVIDENCE_AGE_HOURS", None)

    def run_command(self, source, extra_args=()):
        self.script.write_text(source, encoding="utf-8")
        self.command_file.write_text(json.dumps(self.argv + list(extra_args)), encoding="utf-8")
        return self.run_cli()

    def run_cli(self):
        return subprocess.run(
            [sys.executable, str(CLI), "run", "--command-file", str(self.command_file),
             "--cwd", str(self.cwd), "--out", str(self.evidence), "--md-out", str(self.markdown)],
            env=self.env, capture_output=True, text=True, timeout=10,
        )

    def read_evidence(self):
        return json.loads(self.evidence.read_text(encoding="utf-8"))

    def gate(self):
        result = subprocess.run(
            [sys.executable, str(CLI), "gate", "--evidence", str(self.evidence)],
            env=self.env, capture_output=True, text=True, check=True, timeout=10,
        )
        return json.loads(result.stdout)

    def test_success(self):
        result = self.run_command("from pathlib import Path\nprint(Path.cwd())\nprint('smoke ok')")
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.read_evidence()
        self.assertEqual(evidence["version"], 1)
        self.assertEqual(evidence["command"], self.argv)
        self.assertEqual(evidence["exit_code"], 0)
        self.assertTrue(evidence["verified"])
        self.assertFalse(evidence["timed_out"])
        self.assertIn(str(self.cwd.resolve()), evidence["output"])
        md = self.markdown.read_text(encoding="utf-8")
        for value in ("検証証拠（スモークテスト）", "smoke.py", "終了コード 0", "smoke ok"):
            self.assertIn(value, md)
        self.assertTrue(self.gate()["complete"])

    def test_failure_removes_stale_markdown(self):
        self.assertEqual(self.run_command("pass").returncode, 0)
        result = self.run_command("raise SystemExit(7)")
        self.assertEqual(result.returncode, 1)
        evidence = self.read_evidence()
        self.assertEqual(evidence["exit_code"], 7)
        self.assertFalse(evidence["verified"])
        self.assertFalse(self.markdown.exists())
        self.assertFalse(self.gate()["complete"])

    def test_missing_or_invalid_evidence(self):
        self.assertFalse(self.gate()["complete"])
        for data in ("{", '{"version":1,"verified":"true","exit_code":0,"timed_out":false}'):
            with self.subTest(data=data):
                self.evidence.write_text(data, encoding="utf-8")
                self.assertFalse(self.gate()["complete"])

    def test_missing_or_nonmatching_allowlist_blocks_execution(self):
        nonmatching = [[sys.executable, str(self.script) + ".other"]]
        for allow, code in ((None, 2), ("", 2), ("[]", 2), ("[1]", 2),
                            ('[["x",1]]', 2), ("[[]]", 2), (json.dumps(nonmatching), 1)):
            with self.subTest(allow=allow):
                if allow is None:
                    self.env.pop("TICKET_VERIFY_ALLOW", None)
                else:
                    self.env["TICKET_VERIFY_ALLOW"] = allow
                result = self.run_command("from pathlib import Path\nPath('ran').touch()")
                self.assertEqual(result.returncode, code)
                self.assertIsNone(self.read_evidence()["exit_code"])
                self.assertFalse(self.read_evidence()["verified"])
                self.assertFalse((self.cwd / "ran").exists())
                self.assertFalse(self.markdown.exists())
                self.assertFalse(self.gate()["complete"])

    def test_gate_rejects_inconsistent_success(self):
        for fields in ({"timed_out": True}, {"exit_code": 7}, {"exit_code": False}):
            with self.subTest(fields=fields):
                evidence = {"version": 1, "verified": True, "exit_code": 0, "timed_out": False}
                evidence.update(fields)
                self.evidence.write_text(json.dumps(evidence), encoding="utf-8")
                self.assertFalse(self.gate()["complete"])

    def test_gate_optional_freshness(self):
        self.assertEqual(self.run_command("pass").returncode, 0)
        evidence = self.read_evidence()
        self.env["TICKET_VERIFY_MAX_EVIDENCE_AGE_HOURS"] = "0.5"
        self.assertTrue(self.gate()["complete"])
        for limit in ("", "invalid", "0", "inf", "nan"):
            with self.subTest(limit=limit):
                self.env["TICKET_VERIFY_MAX_EVIDENCE_AGE_HOURS"] = limit
                self.assertFalse(self.gate()["complete"])
        self.env["TICKET_VERIFY_MAX_EVIDENCE_AGE_HOURS"] = "0.5"
        for timestamp in ("2000-01-01T00:00:00+00:00", "invalid", None, "2999-01-01T00:00:00+00:00"):
            with self.subTest(timestamp=timestamp):
                evidence["ran_at"] = timestamp
                self.evidence.write_text(json.dumps(evidence), encoding="utf-8")
                self.assertFalse(self.gate()["complete"])
        del self.env["TICKET_VERIFY_MAX_EVIDENCE_AGE_HOURS"]
        self.assertTrue(self.gate()["complete"])

    def test_colliding_symlink_output_removes_existing_evidence(self):
        self.command_file.write_text(json.dumps(self.argv), encoding="utf-8")
        self.evidence.write_text(
            '{"version":1,"verified":true,"exit_code":0,"timed_out":false}', encoding="utf-8"
        )
        self.markdown.symlink_to(self.evidence)
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.evidence.exists())
        self.assertFalse(self.markdown.is_symlink())
        self.assertFalse(self.gate()["complete"])

    def test_duplicate_output_path_removes_existing_evidence(self):
        self.markdown = self.evidence
        self.evidence.write_text("stale", encoding="utf-8")
        result = self.run_command("pass")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.evidence.exists())

    def test_symlink_json_output_preserves_target(self):
        self.evidence.symlink_to(self.command_file)
        result = self.run_command("pass")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.evidence.is_symlink())
        self.assertEqual(json.loads(self.command_file.read_text(encoding="utf-8")), self.argv)
        self.assertFalse(self.markdown.exists())

    def test_truncation_note(self):
        for source, expected in (("print('x' * 4001)", "x" * 4000),
                                 ("print('old'); print('x\\n' * 39 + 'x')", "\n".join(["x"] * 40))):
            with self.subTest(source=source):
                self.assertEqual(self.run_command(source).returncode, 0)
                self.assertEqual(self.read_evidence()["output"], expected)
                self.assertIn("（出力は末尾の 40 行・4000 字まで）", self.markdown.read_text(encoding="utf-8"))

    def test_timeout(self):
        self.env["TICKET_VERIFY_TIMEOUT_SEC"] = "1"
        result = self.run_command("import time\nprint('started', flush=True)\ntime.sleep(5)")
        self.assertEqual(result.returncode, 1)
        evidence = self.read_evidence()
        self.assertIn("started", evidence["output"])
        self.assertTrue(evidence["timed_out"])
        self.assertFalse(evidence["verified"])
        self.assertFalse(self.markdown.exists())
        self.assertFalse(self.gate()["complete"])

    def test_secrets_are_removed_from_environment_and_evidence(self):
        token = "mock-secret-token-638"
        key = "mock-api-key-920"
        self.env.update(GITLAB_TOKEN=token, REDMINE_API_KEY=key)
        result = self.run_command(
            "import os, sys\nassert 'GITLAB_TOKEN' not in os.environ\n"
            "assert 'REDMINE_API_KEY' not in os.environ\n"
            "print(sys.argv[1])\nprint(sys.argv[2], file=sys.stderr)",
            [token, key],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (self.evidence, self.markdown):
            content = path.read_text(encoding="utf-8")
            self.assertIn("***", content)
            self.assertNotIn(token, content)
            self.assertNotIn(key, content)

    def test_launch_failure_and_invalid_config(self):
        self.argv = [str(self.cwd / "missing-executable")]
        self.env["TICKET_VERIFY_ALLOW"] = json.dumps([self.argv])
        result = self.run_command("pass")
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(self.read_evidence()["exit_code"])
        self.assertFalse(self.markdown.exists())
        self.assertFalse(self.gate()["complete"])
        self.env["TICKET_VERIFY_ALLOW"] = "invalid JSON"
        self.assertEqual(self.run_command("pass").returncode, 2)
        self.assertFalse(self.read_evidence()["verified"])
        self.assertFalse(self.markdown.exists())


if __name__ == "__main__":
    unittest.main()
