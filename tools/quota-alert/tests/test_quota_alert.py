from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "quota_alert.py"
START = datetime(2026, 11, 19, 20, tzinfo=timezone.utc)


class QuotaAlertTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.ledger = root / "ledger"
        self.ledger.mkdir()
        self.config = root / "config.json"
        self.output = root / "forecast.json"
        self.state = root / "state.json"
        self.configure()

    def configure(self, **settings):
        self.config.write_text(json.dumps({
            "ledger_dir": str(self.ledger), "state_file": str(self.state), **settings,
        }), encoding="utf-8")

    def snapshot(self, hour, used, agent="codex", reset=None, **fields):
        timestamp = START + timedelta(hours=hour)
        path = self.ledger / f"{timestamp.date()}.jsonl"
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps({
                "event": "quota_snapshot",
                "agent_cli": agent,
                "ts": timestamp.isoformat(),
                "quota_used_percent": used,
                "reset_at": (START + timedelta(hours=reset)).isoformat() if reset is not None else "",
                **fields,
            }) + "\n")

    def invoke(self, hour, expected=0):
        result = subprocess.run([
            sys.executable, str(SCRIPT), "--config", str(self.config),
            "--output", str(self.output), "--now", (START + timedelta(hours=hour)).isoformat(),
        ], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def agents(self):
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["version"], 1)
        self.assertEqual(report["source"], "quota-alert")
        return {agent["agent_cli"]: agent for agent in report["agents"]}

    def test_linear_forecast_across_files_and_before_reset(self):
        self.snapshot(4, 24, reset=100)
        self.snapshot(0, 20, reset=100)
        with (self.ledger / "2026-11-19.jsonl").open("ab") as file:
            file.write(b"broken\n\xff\nnull\n")
        self.snapshot(5, 100)
        self.invoke(4)
        agent = self.agents()["codex"]
        self.assertAlmostEqual(agent["pace_percent_per_day"], 24)
        expected = START + timedelta(hours=80)
        predicted = datetime.fromisoformat(agent["predicted_exhaustion_at"].replace("Z", "+00:00"))
        self.assertLess(abs(predicted - expected), timedelta(minutes=1))
        self.assertEqual(agent["status"], "critical")

    def test_remaining_thresholds_and_rate_limit(self):
        self.snapshot(0, 70, "warning")
        self.snapshot(0, 90, "critical")
        self.snapshot(0, 20, "limited", quota_kind="rate_limit")
        self.snapshot(0, 100, "maximum")
        self.invoke(0)
        agents = self.agents()
        self.assertEqual(agents["warning"]["status"], "warning")
        for name in ("critical", "limited", "maximum"):
            self.assertEqual(agents[name]["status"], "critical")
            self.assertTrue(agents[name]["reasons"])

    def test_reset_boundaries(self):
        self.snapshot(0, 1, "drop")
        self.snapshot(3, 85, "drop")
        self.snapshot(4, 75, "drop")
        self.snapshot(7, 76, "drop")
        self.snapshot(0, 1, "time", reset=4)
        self.snapshot(3, 60, "time", reset=4)
        self.snapshot(4, 59, "time", reset=80)
        self.snapshot(7, 60, "time", reset=80)
        self.invoke(7)
        agents = self.agents()
        self.assertEqual(agents["drop"]["status"], "warning")
        self.assertEqual(agents["time"]["status"], "ok")
        for agent in agents.values():
            self.assertAlmostEqual(agent["pace_percent_per_day"], 8)

    def test_short_flat_and_expired_windows_are_unknown(self):
        for name, end, value, reset in (
            ("short", 2, 20, None), ("flat", 4, 10, None), ("expired", 4, 20, 5),
        ):
            self.snapshot(0, 10, name, reset=reset)
            self.snapshot(end, value, name, reset=reset)
        result = self.invoke(5)
        self.assertEqual(result.stdout, "")
        for agent in self.agents().values():
            self.assertEqual(agent["status"], "unknown")
            self.assertIsNone(agent["pace_percent_per_day"])
            self.assertIsNone(agent["predicted_exhaustion_at"])

    def test_prediction_within_warning_days_without_reset(self):
        self.snapshot(0, 10)
        self.snapshot(4, 40)
        self.invoke(4)
        self.assertEqual(self.agents()["codex"]["status"], "warning")

    def test_prediction_at_or_after_reset_within_warning_days_is_ok(self):
        for name, reset in (("at_reset", 10), ("after_reset", 8)):
            self.snapshot(0, 0, name, reset=reset)
            self.snapshot(4, 40, name, reset=reset)
        result = self.invoke(4)
        self.assertEqual(result.stderr, "")
        for agent in self.agents().values():
            self.assertEqual(agent["predicted_exhaustion_at"], "2026-11-20T06:00:00Z")
            self.assertEqual(agent["status"], "ok")
            self.assertEqual(agent["reasons"], [])

    def test_duplicate_timestamps_preserve_forecast(self):
        for hour, used in ((0, 0), (4, 40)):
            self.snapshot(hour, used)
            self.snapshot(hour, used)
        self.invoke(4)
        agent = self.agents()["codex"]
        self.assertAlmostEqual(agent["pace_percent_per_day"], 240)
        self.assertEqual(agent["predicted_exhaustion_at"], "2026-11-20T06:00:00Z")
        self.assertEqual(agent["status"], "warning")

    def test_invalid_reset_is_treated_as_unknown(self):
        self.snapshot(0, 0, reset_at="invalid")
        self.snapshot(4, 40, reset_at="invalid")
        self.invoke(4)
        agent = self.agents()["codex"]
        self.assertIsNone(agent["reset_at"])
        self.assertEqual(agent["predicted_exhaustion_at"], "2026-11-20T06:00:00Z")
        self.assertEqual(agent["status"], "warning")
        self.assertEqual(agent["reasons"], ["警告日数以内に枯渇する見込みです"])

    def test_dry_run_keeps_stdout_empty_and_preserves_state(self):
        self.state.write_text("{}", encoding="utf-8")
        self.snapshot(0, 90)
        result = self.invoke(0)
        self.assertEqual(result.stdout, "")
        self.assertIn("dry-run のため投稿しません", result.stderr)
        self.assertIn("codex", result.stderr)
        self.assertEqual(self.state.read_text(encoding="utf-8"), "{}")
        self.assertEqual(self.agents()["codex"]["status"], "critical")

    def test_cooldown_and_worsening_status(self):
        self.configure(dry_run=False)
        self.snapshot(0, 70)
        self.assertIn("warning", self.invoke(0).stdout)
        first_state = self.state.read_text(encoding="utf-8")
        self.snapshot(1, 70, "another")
        next_result = self.invoke(1)
        self.assertIn("another", next_result.stdout)
        self.assertNotIn("codex", next_result.stdout)
        self.assertEqual(json.loads(self.state.read_text())["codex"], json.loads(first_state)["codex"])
        self.assertEqual(self.invoke(1).stdout, "")
        self.snapshot(2, 90)
        self.assertIn("critical", self.invoke(2).stdout)
        self.assertEqual(self.invoke(3).stdout, "")
        self.assertIn("critical", self.invoke(26).stdout)

    def test_cooldown_suppresses_improvement_and_prediction_date_change(self):
        self.configure(dry_run=False)
        self.snapshot(0, 90)
        self.assertIn("critical", self.invoke(0).stdout)
        first_state = self.state.read_text(encoding="utf-8")
        self.snapshot(4, 92)
        self.assertEqual(self.invoke(4).stdout, "")
        agent = self.agents()["codex"]
        self.assertEqual(agent["status"], "critical")
        self.assertIsNotNone(agent["predicted_exhaustion_at"])
        self.snapshot(5, 70)
        self.assertEqual(self.invoke(5).stdout, "")
        self.assertEqual(self.agents()["codex"]["status"], "warning")
        self.assertEqual(self.state.read_text(encoding="utf-8"), first_state)
        self.assertIn("warning", self.invoke(24).stdout)

    def test_bad_config_removes_previous_forecast(self):
        self.output.write_text("old forecast", encoding="utf-8")
        self.configure(dry_run="false")
        self.assertEqual(self.invoke(0, expected=2).stdout, "")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
