import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "timestamp_converter.py"


class TimestampConverterTests(unittest.TestCase):
    def run_cli(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_auto_threshold(self):
        for sign in ("", "-"):
            with self.subTest(sign=sign):
                result = self.run_cli(
                    "--tz", "UTC", "--json",
                    sign + "1000000000000", sign + "999999999999.999999",
                    sign + "1000000000001",
                )
                self.assertEqual(result.returncode, 2)
                outputs = json.loads(result.stdout)
                factor = -1 if sign else 1
                self.assertEqual(
                    [output["epoch_ms"] for output in outputs],
                    [factor * 1000000000000, factor * 1000000000001],
                )
                self.assertIn(sign + "999999999999.999999", result.stderr)

    def test_auto_seconds_and_explicit_units(self):
        for flags, value, seconds in [
            ([], "1700000000.125", 1700000000.125),
            (["--unit", "ms"], "-1500.", -1.5),
            (["--unit", "s"], "1700000000", 1700000000),
            (["--unit", "ms"], "999999999999", 999999999.999),
        ]:
            with self.subTest(flags=flags, value=value):
                result = self.run_cli("--tz", "UTC", "--json", *flags, value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["epoch_s"], seconds)

    def test_iso_dates_and_time_zone(self):
        result = self.run_cli(
            "--tz", "Asia/Tokyo", "--json", "1970-01-01T00:00:00Z",
            "1970-01-01T02:30:00+02:30", "1970-01-01 09:00:00",
            "1970-01-01",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        first, second, third, date = json.loads(result.stdout)
        for output in (first, second, third):
            self.assertEqual(output["epoch_s"], 0)
            self.assertEqual(output["epoch_ms"], 0)
            self.assertEqual(output["utc"], "1970-01-01T00:00:00+00:00")
            self.assertEqual(output["local"], "1970-01-01T09:00:00+09:00")
        self.assertEqual(date["epoch_s"], -32400)

    def test_lowercase_z_suffix(self):
        result = self.run_cli("--tz", "UTC", "--json", "1970-01-01T00:00:00z")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["epoch_s"], 0)
        self.assertEqual(output["utc"], "1970-01-01T00:00:00+00:00")

    def test_text_output(self):
        result = self.run_cli("0", "--tz", "Asia/Tokyo", "1970-01-01T00:00:00Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "Asia/Tokyo: 1970-01-01T09:00:00+09:00",
            "UTC: 1970-01-01T00:00:00+00:00",
            "Epoch (s): 0",
            "Epoch (ms): 0",
            "",
            "Epoch (s): 0",
            "Epoch (ms): 0",
            "UTC: 1970-01-01T00:00:00+00:00",
            "Asia/Tokyo: 1970-01-01T09:00:00+09:00",
        ])

    def test_now_with_and_without_values(self):
        for args, stdin, extra in [((), "", False), (("0",), "", True), ((), "0\n", True)]:
            with self.subTest(args=args, stdin=stdin):
                before = datetime.now(timezone.utc).timestamp()
                result = self.run_cli("--now", "--json", "--tz", "UTC", *args, stdin=stdin)
                after = datetime.now(timezone.utc).timestamp()
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                if extra:
                    output, other = output
                    self.assertEqual(other["epoch_s"], 0)
                self.assertEqual(output["input"], "now")
                timestamp = datetime.fromisoformat(output["utc"])
                self.assertEqual(timestamp.tzinfo, timezone.utc)
                self.assertLessEqual(before, timestamp.timestamp())
                self.assertLessEqual(timestamp.timestamp(), after)

    def test_input_errors(self):
        for args in [
            (), ("bad",), ("--tz", "Invalid/Zone", "0"),
            ("--tz", "", "0"), ("--unit", "hours", "0"),
        ]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr)

    def test_stdin_batch(self):
        result = self.run_cli("--tz", "UTC", "--json", stdin="0\n\n 1970-01-02T00:00:00Z \n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item["epoch_s"] for item in json.loads(result.stdout)], [0, 86400])

    def test_stdin_partial_failure(self):
        result = self.run_cli("--tz", "UTC", "--json", stdin="invalid\n \n0\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid", result.stderr)
        self.assertEqual([item["epoch_s"] for item in json.loads(result.stdout)], [0])


if __name__ == "__main__":
    unittest.main()
