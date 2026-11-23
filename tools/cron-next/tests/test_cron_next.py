from pathlib import Path
import subprocess
import sys
import unittest

CLI = Path(__file__).resolve().parents[1] / "cron_next.py"


class CronNextTest(unittest.TestCase):
    def run_cli(self, expression, start, count=1, zone="UTC"):
        return subprocess.run(
            [sys.executable, str(CLI), expression, "--from", start,
             "--count", str(count), "--tz", zone],
            capture_output=True, text=True, check=False,
        )

    def assert_runs(self, expression, start, expected, zone="UTC"):
        result = self.run_cli(expression, start, len(expected), zone)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), expected)

    def test_quarter_hour_excludes_start(self):
        self.assert_runs("*/15 * * * *", "2026-11-23T00:15:00", [
            "2026-11-23 00:30 (Mon)", "2026-11-23 00:45 (Mon)",
        ])

    def test_weekdays_across_weekend(self):
        self.assert_runs("0 9 * * MON-FRI", "2026-11-27T09:00:01", [
            "2026-11-30 09:00 (Mon)", "2026-12-01 09:00 (Tue)",
        ])

    def test_monthly(self):
        self.assert_runs("0 0 1 * *", "2026-11-23", [
            "2026-12-01 00:00 (Tue)", "2027-01-01 00:00 (Fri)",
        ])

    def test_yearly(self):
        self.assert_runs("0 0 1 1 *", "2026-11-23", [
            "2027-01-01 00:00 (Fri)", "2028-01-01 00:00 (Sat)",
        ])

    def test_leap_day_across_non_leap_century(self):
        self.assert_runs("0 0 29 2 *", "2096-02-29", ["2104-02-29 00:00 (Fri)"])

    def test_day_or_weekday(self):
        self.assert_runs("0 0 1 * MON", "2026-11-29", [
            "2026-11-30 00:00 (Mon)", "2026-12-01 00:00 (Tue)",
        ])

    def test_names_lists_ranges_and_steps(self):
        self.assert_runs("0,10-30/10 8 */2 jAn Sun,7", "2027-01-02", [
            "2027-01-03 08:00 (Sun)", "2027-01-03 08:10 (Sun)",
            "2027-01-03 08:20 (Sun)", "2027-01-03 08:30 (Sun)",
            "2027-01-17 08:00 (Sun)",
        ])

    def test_timezone_with_offset_in_start(self):
        self.assert_runs("30 7 * * *", "2026-11-22T22:30:00Z", [
            "2026-11-24 07:30 (Tue)",
        ], "Asia/Tokyo")

    def test_dst_gap_and_fold(self):
        zone = "America/New_York"
        self.assert_runs("30 2 * * *", "2026-03-07T02:30", [
            "2026-03-09 02:30 (Mon)",
        ], zone)
        self.assert_runs("30 1 * * *", "2026-11-01T00:00", [
            "2026-11-01 01:30 (Sun)", "2026-11-01 01:30 (Sun)",
            "2026-11-02 01:30 (Mon)",
        ], zone)

    def test_invalid_expression(self):
        for expression, reason in (
            ("* * * *", "5フィールド"),
            ("60 * * * *", "分フィールド"),
            ("0 0 * * 8", "範囲外"),
            ("*/0 * * * *", "ステップ"),
            ("0 0 * Foo *", "無効な値または名前"),
        ):
            with self.subTest(expression=expression):
                result = self.run_cli(expression, "2026-11-23")
                self.assertEqual(result.returncode, 2)
                self.assertIn(reason, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_nonexistent_dates(self):
        for expression in ("0 0 31 2 *", "0 0 31 4 *"):
            with self.subTest(expression=expression):
                result = self.run_cli(expression, "2026-11-23")
                self.assertEqual(result.returncode, 1)
                self.assertIn("見つかりません", result.stderr)
                self.assertEqual(result.stdout, "")
