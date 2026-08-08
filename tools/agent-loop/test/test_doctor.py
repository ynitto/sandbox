import json
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.agent_home = self.root / "agent-home"
        self.slots_dir = self.root / "slots"
        self.send_dir = self.root / "send-requests"
        self.state_dir = self.root / "loop-state"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _findings_shape_ok(self, findings: list[dict]) -> None:
        for item in findings:
            self.assertIn("id", item)
            self.assertIn("severity", item)
            self.assertIn("evidence", item)
            self.assertIn("fixable", item)
            self.assertIn(item["severity"], ("error", "warning", "info"))

    def test_findings_shape(self):
        findings = al.run_doctor_checks(
            cwd=self.root,
            agent_home=self.agent_home,
            slots_dir=self.slots_dir,
            send_requests_dir=self.send_dir,
            state_dir=self.state_dir,
        )
        self.assertTrue(findings)
        self._findings_shape_ok(findings)

    def test_fix_creates_missing_dirs(self):
        findings = al.run_doctor_checks(
            cwd=self.root,
            agent_home=self.agent_home,
            slots_dir=self.slots_dir,
            send_requests_dir=self.send_dir,
            state_dir=self.state_dir,
            fix=True,
        )
        self._findings_shape_ok(findings)
        self.assertTrue(self.slots_dir.is_dir())
        self.assertTrue(self.send_dir.is_dir())

    def test_fix_deletes_dead_slot_only(self):
        self.slots_dir.mkdir(parents=True)
        alive = self.slots_dir / "pane_alive.json"
        dead = self.slots_dir / "pane_dead.json"
        alive.write_text(json.dumps({"pid": os.getpid(), "acquired_at": 0}), encoding="utf-8")
        dead.write_text(json.dumps({"pid": 999999, "acquired_at": al.time.time()}), encoding="utf-8")

        al.run_doctor_checks(
            cwd=self.root,
            agent_home=self.agent_home,
            slots_dir=self.slots_dir,
            send_requests_dir=self.send_dir,
            state_dir=self.state_dir,
            fix=True,
        )
        self.assertTrue(alive.exists())
        self.assertFalse(dead.exists())

    def test_fix_isolates_corrupt_send_request(self):
        self.send_dir.mkdir(parents=True)
        bad = self.send_dir / "000_bad.json"
        bad.write_text("{broken", encoding="utf-8")

        al.run_doctor_checks(
            cwd=self.root,
            agent_home=self.agent_home,
            slots_dir=self.slots_dir,
            send_requests_dir=self.send_dir,
            state_dir=self.state_dir,
            fix=True,
        )
        self.assertFalse(bad.exists())
        invalid_files = list((self.send_dir / ".invalid").glob("*.json"))
        self.assertEqual(len(invalid_files), 1)

    def test_cmd_doctor_json_output(self):
        args = mock.Mock(json=True, fix=False)
        output = io.StringIO()
        with mock.patch.object(al, "load_config", return_value=({}, Path("/tmp/x.yaml"), False)), \
             mock.patch.object(al, "run_doctor_checks", return_value=[
                 {"id": "test", "severity": "info", "evidence": "ok", "fixable": False},
             ]), \
             mock.patch("sys.stdout", output):
            al.cmd_doctor(args)
        self.assertEqual(json.loads(output.getvalue()), {"findings": [
            {"id": "test", "severity": "info", "evidence": "ok", "fixable": False},
        ]})

    def test_cmd_doctor_exits_on_error(self):
        args = mock.Mock(json=False, fix=False)
        with mock.patch.object(al, "load_config", return_value=({}, Path("/tmp/x.yaml"), False)), \
             mock.patch.object(al, "run_doctor_checks", return_value=[
                 {"id": "bad", "severity": "error", "evidence": "fail", "fixable": False},
             ]), \
             mock.patch("sys.exit", side_effect=SystemExit(1)) as exit_mock:
            with self.assertRaises(SystemExit):
                al.cmd_doctor(args)
        exit_mock.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
