from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloud_waste_inventory as tool


FIXTURES = Path(__file__).with_name("fixtures")


class InventoryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "config.json"

    def invoke(self, **settings):
        self.config.write_text(json.dumps(settings), encoding="utf-8")
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = tool.main(["--config", str(self.config)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_inventory_prices_and_default_dry_run(self):
        outputs = [
            subprocess.CompletedProcess([], 0, (FIXTURES / name).read_text(), "")
            for name in ("az-vm-list.json", "az-disk-list.json")
        ]
        with patch.object(tool.subprocess, "run", side_effect=outputs) as run, patch.object(tool, "post") as post:
            code, report, status = self.invoke(
                oversized_sizes={"Standard_D8s_v5": "Standard_D4s_v5"},
                vm_monthly_prices={"Standard_D8s_v5": 120.25, "Standard_D4s_v5": 50},
                disk_monthly_prices={"/disk/spare": 8.50},
                timeout_seconds=12,
            )
        self.assertEqual(code, 0)
        self.assertRegex(report.splitlines()[0], r"^<!-- cloud-waste-inventory \d{4}-\d\d-\d\d 5件 -->$")
        for identity in ("/vm/stopped", "/vm/deallocated", "/vm/oversized", "/disk/spare", "/disk/unpriced"):
            self.assertEqual(report.count(identity), 1, report)
        self.assertNotIn("/vm/normal", report)
        self.assertNotIn("/disk/used", report)
        self.assertRegex(report, r"停止中 VM.*過大 SKU.* /vm/deallocated.*JPY 120.25")
        self.assertRegex(report, r"過大 SKU.* /vm/oversized.*JPY 70.25")
        self.assertRegex(report, r"未接続ディスク.* /disk/spare.*JPY 8.50")
        self.assertRegex(report, r"/vm/stopped.*見積り不可")
        self.assertRegex(report, r"/disk/unpriced.*見積り不可")
        self.assertIn("JPY 199.00/月", report)
        self.assertIn("見積り不可 2件", report)
        self.assertIn("dry-run", status)
        post.assert_not_called()
        self.assertEqual([call.args[0] for call in run.call_args_list], list(tool.AZURE_COMMANDS.values()))
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 12)
            self.assertNotIn("env", call.kwargs)

    def test_cli_errors_skip_post(self):
        cases = [
            (FileNotFoundError(), "CLI が見つかりません"),
            (subprocess.TimeoutExpired("az", 60), "タイムアウト"),
            (subprocess.CompletedProcess([], 1, "", "Please run 'az login'"), "認証"),
            (subprocess.CompletedProcess([], 7, "", "connection reset"), "終了コード 7"),
            (subprocess.CompletedProcess([], 0, "{}", ""), "JSON 出力"),
        ]
        for result, reason in cases:
            with self.subTest(reason=reason), patch.object(tool, "post") as post:
                with patch.object(tool.subprocess, "run", side_effect=[result]):
                    code, output, stderr = self.invoke(dry_run=False)
                self.assertEqual(code, 3)
                self.assertIn("未確認（vms:", output)
                self.assertIn(reason, output)
                self.assertEqual(stderr, "")
                post.assert_not_called()

    def test_scope_can_be_configured_but_write_commands_are_rejected(self):
        commands = {**tool.AZURE_COMMANDS, "vms": ["az", "vm", "stop"]}
        with patch.object(tool.subprocess, "run") as run:
            self.assertEqual(self.invoke(commands=commands)[0], 2)
            run.assert_not_called()
        commands["vms"] = tool.AZURE_COMMANDS["vms"] + ["--resource-group", "rg-test"]
        result = subprocess.CompletedProcess([], 0, "[]", "")
        with patch.object(tool.subprocess, "run", return_value=result) as run:
            self.assertEqual(self.invoke(commands=commands)[0], 0)
            self.assertEqual(run.call_args_list[0].args[0], commands["vms"])

    def test_oversized_missing_target_price(self):
        outputs = [
            subprocess.CompletedProcess([], 0, (FIXTURES / "az-vm-list.json").read_text(), ""),
            subprocess.CompletedProcess([], 0, "[]", ""),
        ]
        with patch.object(tool.subprocess, "run", side_effect=outputs):
            code, report, _ = self.invoke(
                oversized_sizes={"Standard_D8s_v5": "Standard_D4s_v5"},
                vm_monthly_prices={"Standard_D8s_v5": 120},
            )
        self.assertEqual(code, 0)
        self.assertRegex(report, r"/vm/oversized.*見積り不可")

    def test_post_checks_issue_and_skips_same_day_on_later_page(self):
        day = date(2026, 11, 20)
        report = tool.markdown([], day, "JPY")
        self.assertEqual(self.post_with_responses(day, report, [
            self.notes_response([{"body": "別のコメント"}], next_page="2"),
            self.notes_response([{"body": "<!-- cloud-waste-inventory 2026-11-20 10件 -->\n別の本文"}]),
        ]), ["GET", "GET"])

    def test_post_creates_note_when_previous_report_is_from_another_day(self):
        day = date(2026, 11, 20)
        report = tool.markdown([], day, "JPY")
        self.assertEqual(self.post_with_responses(day, report, [
            self.notes_response([{"body": "<!-- cloud-waste-inventory 2026-11-19 0件 -->"}]),
            self.notes_response({}, status=201),
        ]), ["GET", "POST"])

    def post_with_responses(self, day, report, responses):
        opener = Mock()
        opener.open.side_effect = responses
        with patch.object(tool, "build_opener", return_value=opener), patch.object(Path, "home", return_value=self.root):
            with patch.dict(tool.os.environ, GITLAB_HUB_ISSUE_URL="https://gitlab.example/g/hub/-/issues/12",
                            GITLAB_TOKEN="test-token"):
                tool.post(report, day, 10)
        requests = [call.args[0] for call in opener.open.call_args_list]
        for request in requests:
            self.assertTrue(request.full_url.startswith("https://gitlab.example/api/v4/projects/g%2Fhub/issues/12/notes"))
            if request.get_method() == "POST":
                self.assertEqual(json.loads(request.data), {"body": report})
        return [request.get_method() for request in requests]

    @staticmethod
    def notes_response(notes, next_page="", status=200):
        response = BytesIO(json.dumps(notes).encode())
        response.headers = {"X-Next-Page": next_page}
        response.status = status
        return response

    def test_explicit_enable_calls_post_after_inventory(self):
        result = subprocess.CompletedProcess([], 0, "[]", "")
        with patch.object(tool.subprocess, "run", return_value=result), patch.object(tool, "post") as post:
            code, report, _ = self.invoke(dry_run=False)
        self.assertEqual(code, 0)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], report.rstrip())


if __name__ == "__main__":
    unittest.main()
