import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "upkeep_health_check.py"
NOW = "2026-11-20T08:00:00+00:00"
NOW_TIMESTAMP = datetime.fromisoformat(NOW).timestamp()


class UpkeepHealthCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state_file = self.root / "exports" / ".kiro_export_state.json"
        self.knowledge_file = self.root / "knowledge.json"
        self.config_file = self.root / "config.json"
        self.output_file = self.root / "result.json"
        self.config = {
            "kiro_exports": [
                {"name": "Kiro", "dir": "exports", "max_age_hours": 24},
            ],
            "knowledge": {
                "path": "knowledge.json",
                "max_age_hours": 24,
                "thresholds": {
                    "dormant_oldest_days": 30,
                    "pending_oldest_days": 5,
                    "index_drift": 0,
                    "outbox_pending": 2,
                    "outbox_oldest_days": 7,
                },
            },
        }
        self.knowledge = {
            "generated_at": "2026-11-20",
            "thresholds": {},
            "layers": {
                "ltm": {
                    "configured": True,
                    "stores": [{"path": "/ltm", "dormant_oldest_days": 30, "index_drift": 0}],
                },
                "wiki": {
                    "configured": True,
                    "stores": [{"path": "/wiki", "index_drift": 0}],
                },
                "persona": {
                    "configured": True,
                    "stores": [{"path": "/persona", "pending_oldest_days": 5}],
                },
                "moltbook": {
                    "configured": True,
                    "stores": [
                        {"path": "/moltbook", "outbox_pending": 2, "outbox_oldest_days": 7},
                    ],
                },
            },
        }

    def write_evidence(self, state_age_hours=1, knowledge_age_hours=1):
        self.state_file.parent.mkdir()
        self.state_file.write_text("{}", encoding="utf-8")
        self.knowledge_file.write_text(json.dumps(self.knowledge), encoding="utf-8")
        for path, age in (
            (self.state_file, state_age_hours),
            (self.knowledge_file, knowledge_age_hours),
        ):
            mtime = NOW_TIMESTAMP - age * 3600
            os.utime(path, (mtime, mtime))

    def run_cli(self):
        self.config_file.write_text(json.dumps(self.config), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "--config",
                str(self.config_file),
                "--output",
                str(self.output_file),
                "--now",
                NOW,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

    def check(self):
        run = self.run_cli()
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(self.output_file.read_text(encoding="utf-8"))
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["source"], "upkeep-health-check")
        checked_at = result["checked_at"].replace("Z", "+00:00")
        self.assertEqual(datetime.fromisoformat(checked_at), datetime.fromisoformat(NOW))
        for item in result["items"]:
            self.assertEqual(
                set(item), {"name", "kind", "status", "detail", "value", "threshold"},
            )
            self.assertTrue(item["detail"])
            self.assertIn(item["status"], ("ok", "stale", "unknown"))
        self.assertEqual(
            result["summary"],
            {
                status: sum(item["status"] == status for item in result["items"])
                for status in ("ok", "stale", "unknown")
            },
        )
        return result

    def test_fresh_state_and_healthy_knowledge(self):
        self.write_evidence(state_age_hours=24, knowledge_age_hours=24)

        result = self.check()

        self.assertEqual(result["summary"]["stale"], 0)
        self.assertEqual(result["summary"]["unknown"], 0)
        self.assertEqual(
            {item["kind"] for item in result["items"]},
            {"kiro_export", "knowledge_report", "knowledge_metric"},
        )

    def test_stale_state_report_and_both_outbox_metrics(self):
        outbox = self.knowledge["layers"]["moltbook"]["stores"][0]
        outbox.update(outbox_pending=3, outbox_oldest_days=8)
        self.write_evidence(state_age_hours=25, knowledge_age_hours=26)

        result = self.check()

        stale = [item for item in result["items"] if item["status"] == "stale"]
        self.assertEqual(len(stale), 4)
        self.assertEqual(
            {item["kind"] for item in stale},
            {"kiro_export", "knowledge_report", "knowledge_metric"},
        )
        for metric, value, threshold in (("outbox_pending", 3, 2), ("outbox_oldest_days", 8, 7)):
            with self.subTest(metric=metric):
                matches = [item for item in stale if metric in item["name"]]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["value"], value)
                self.assertEqual(matches[0]["threshold"], threshold)

    def test_missing_export_directory_and_knowledge_report(self):
        result = self.check()

        self.assertEqual(result["summary"], {"ok": 0, "stale": 0, "unknown": 2})
        self.assertEqual(
            {item["kind"] for item in result["items"]},
            {"kiro_export", "knowledge_report"},
        )

    def test_unconfigured_layer_is_unknown(self):
        self.knowledge["layers"]["moltbook"] = {"configured": False, "stores": []}
        self.write_evidence()

        result = self.check()

        self.assertEqual(result["summary"]["stale"], 0)
        unknown = [item for item in result["items"] if item["status"] == "unknown"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["kind"], "knowledge_layer")
        self.assertIn("moltbook", unknown[0]["name"])
        self.assertIn("未収集", unknown[0]["detail"])

    def test_fifo_report_is_unknown_without_waiting_for_a_writer(self):
        os.mkfifo(self.knowledge_file)
        result = self.check()
        item = next(item for item in result["items"] if item["kind"] == "knowledge_report")
        self.assertEqual(item["status"], "unknown")
        self.assertIsNone(item["value"])

    def test_missing_state_file_and_malformed_report(self):
        self.state_file.parent.mkdir()
        self.knowledge_file.write_text("{", encoding="utf-8")

        result = self.check()

        self.assertEqual(result["summary"], {"ok": 0, "stale": 0, "unknown": 2})

    def test_omitted_thresholds_skip_comparisons(self):
        del self.config["kiro_exports"][0]["max_age_hours"]
        del self.config["knowledge"]["max_age_hours"]
        del self.config["knowledge"]["thresholds"]
        self.knowledge["layers"]["moltbook"]["stores"][0]["outbox_pending"] = 999
        self.write_evidence(state_age_hours=999, knowledge_age_hours=999)

        result = self.check()

        self.assertEqual(result["summary"], {"ok": 2, "stale": 0, "unknown": 0})

    def test_invalid_configuration_returns_two(self):
        self.config["knowledge"]["max_age_hours"] = "invalid"

        run = self.run_cli()

        self.assertEqual(run.returncode, 2)
        self.assertTrue(run.stderr)
        self.assertFalse(self.output_file.exists())

    def test_invalid_configuration_removes_previous_result(self):
        self.write_evidence()
        self.assertEqual(self.check()["summary"], {"ok": 8, "stale": 0, "unknown": 0})
        self.config["knowledge"]["max_age_hours"] = "invalid"

        run = self.run_cli()

        self.assertEqual(run.returncode, 2)
        self.assertIn("max_age_hours", run.stderr)
        self.assertFalse(self.output_file.exists())

    def test_null_metric_with_threshold_is_unknown(self):
        self.knowledge["layers"]["ltm"]["stores"][0]["index_drift"] = None
        self.write_evidence()

        result = self.check()

        unknown = [item for item in result["items"] if item["status"] == "unknown"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["name"], "ltm / /ltm / index_drift")
        self.assertEqual(unknown[0]["kind"], "knowledge_metric")
        self.assertIsNone(unknown[0]["value"])
        self.assertEqual(unknown[0]["threshold"], 0)

    def test_future_mtime_is_unknown_for_export_and_report(self):
        self.write_evidence(state_age_hours=-1, knowledge_age_hours=-1)

        result = self.check()

        unknown = [item for item in result["items"] if item["status"] == "unknown"]
        self.assertEqual(len(unknown), 2)
        self.assertEqual({item["kind"] for item in unknown}, {"kiro_export", "knowledge_report"})
        for item in unknown:
            with self.subTest(kind=item["kind"]):
                self.assertIn("時刻逆転", item["detail"])
                self.assertIsNone(item["value"])
                self.assertEqual(item["threshold"], 24)


class ChangeWatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.state_dir = root / "change-watch"
        self.config_file = root / "config.json"
        self.output_file = root / "result.json"
        self.entry = {"name": "change-watch", "state_dir": "change-watch", "max_age_hours": 1}
        self.config = {"change_watch": [self.entry]}

    run_cli = UpkeepHealthCheckTests.run_cli
    check = UpkeepHealthCheckTests.check

    def write_state(self, age_hours, filename="state.db"):
        self.state_dir.mkdir(exist_ok=True)
        path = self.state_dir / filename
        path.touch()
        mtime = NOW_TIMESTAMP - age_hours * 3600
        os.utime(path, (mtime, mtime))

    def test_fresh_change_watch_database(self):
        self.write_state(0.5)

        entry, = self.check()["items"]
        self.assertEqual(entry["kind"], "change_watch")
        self.assertEqual(entry["name"], "change-watch")
        self.assertEqual(entry["status"], "ok")

    def test_stale_change_watch_database(self):
        self.write_state(2)

        entry, = self.check()["items"]
        self.assertEqual(entry["status"], "stale")
        self.assertEqual(entry["value"], 2)
        self.assertEqual(entry["threshold"], 1)

    def test_missing_change_watch_database(self):
        for create_directory in (False, True):
            with self.subTest(create_directory=create_directory):
                if create_directory:
                    self.state_dir.mkdir()
                entry, = self.check()["items"]
                self.assertEqual(entry["status"], "unknown")
                self.assertEqual(entry["detail"], "状態 DB を確認できません")
                self.assertIsNone(entry["value"])
                self.assertEqual(entry["threshold"], 1)

    def test_change_watch_uses_newer_wal(self):
        self.write_state(2)
        self.write_state(0.25, "state.db-wal")

        entry, = self.check()["items"]
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["value"], 0.25)

    def test_future_change_watch_mtime_is_unknown(self):
        self.write_state(-1)

        entry, = self.check()["items"]
        self.assertEqual(entry["status"], "unknown")
        self.assertIn("時刻逆転", entry["detail"])

    def test_change_watch_without_threshold(self):
        del self.entry["max_age_hours"]
        self.write_state(999)

        entry, = self.check()["items"]
        self.assertEqual(entry["status"], "ok")
        self.assertIsNone(entry["threshold"])

    def test_invalid_change_watch_configuration_returns_two(self):
        for entries, error in (
            ([dict(self.entry, max_age_hours="1")], "max_age_hours"),
            ([{"state_dir": "change-watch"}], "名前"),
            ({}, "配列"),
            ([], "点検対象を設定してください"),
        ):
            with self.subTest(entries=entries):
                self.config["change_watch"] = entries
                run = self.run_cli()
                self.assertEqual(run.returncode, 2)
                self.assertIn(error, run.stderr)
                self.assertFalse(self.output_file.exists())


if __name__ == "__main__":
    unittest.main()
