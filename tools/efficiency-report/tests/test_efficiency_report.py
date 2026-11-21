import importlib.util
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "efficiency_report", Path(__file__).resolve().parents[1] / "efficiency_report.py"
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)

MONTH = "2026-10"
MARKER = f"[efficiency-report: {MONTH}]"
HUB = "https://gitlab.example.com/group/sub/project/-/issues/42"


def collection():
    raw = {"merged_mr_count": 5, "discarded_mr_count": 2, "rework_count": 3}
    estimate = {"currency": "JPY", "savings": {"low": -1000, "mid": 3000, "high": 8000}}
    timing = {
        "ai_actual": {"closed_issue_count": 4, "avg_cycle_time_hours": 12.5,
                      "active_counted": 3, "avg_active_time_hours": 4.5},
        "human_actual": {"closed_issue_count": 2, "avg_cycle_time_hours": 20,
                         "active_counted": 0, "avg_active_time_hours": 0},
        "time_saved_days": {"low": -1.2, "mid": 1.5, "high": 3},
        "time_saved_net_days": {"low": -0.5, "mid": 2, "high": 4},
    }
    return {
        "window": {"since": "2026-10-01T00:00:00Z", "until": "2026-10-31T23:59:59Z"},
        "repos": ["group/project"],
        "classification": {"agent_users": ["bot"], "ai_markers": ["worker-node-id"]},
        "params": {"human_hourly_rate": 6000, "write_loc_per_day_low": 30,
                   "write_loc_per_day_mid": 60, "write_loc_per_day_high": 120,
                   "review_bytes_per_minute": 270},
        "totals": {"raw_metrics": raw, "estimate": estimate, "time": timing},
        "by_repo": {"group/project": {"raw_metrics": raw, "estimate": estimate, "time": timing}},
        "by_user": {"alice": {"kinds": ["ai"], "raw_metrics": raw, "estimate": estimate}},
    }


class TestEfficiencyReport(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.input = self.write("collect.json", collection())
        self.config = self.write("config.json", {"post_enabled": True, "hub_issue_url": HUB})
        env = patch.dict(os.environ, {"GITLAB_TOKEN": "test-token", "GITLAB_HUB_ISSUE_URL": HUB})
        env.start()
        self.addCleanup(env.stop)
        transport = patch.object(report, "build_opener")
        self.transport = transport.start()
        self.addCleanup(transport.stop)
        self.open = self.transport.return_value.open
        self.open.side_effect = AssertionError("モックを設定していない HTTP 呼び出し")

    def write(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def run_cli(self, *args):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = report.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def mock_responses(self, *payloads):
        responses = []
        for body, headers in payloads:
            stream = io.StringIO(json.dumps(body))
            stream.headers = headers
            responses.append(stream)
        self.open.side_effect = responses

    def test_aggregation_and_sections(self):
        code, out, err = self.run_cli("--input", self.input, "--month", MONTH)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines()[0], MARKER)
        for snippet in (
            "対象月 2026-10", "対象リポジトリ group/project", "閉じた Issue 6 件",
            "マージ済み MR 5 件", "差し戻し 3 件", "破棄 MR 2 件",
            "| AI | 4 件 | 12.5 h | 3 件 | 4.5 h |",
            "| 人 | 2 件 | 20 h | 0 件 | 対象なし |",
            "| 削減コスト | -1,000 JPY | 3,000 JPY | 8,000 JPY |",
            "| 削減時間（総） | -1.2 日 | 1.5 日 | 3 日 |",
            "| 削減時間（正味） | -0.5 日 | 2 日 | 4 日 |",
            "## 前提", "6,000 JPY/h", "30 LOC/人日 / 60 LOC/人日 / 120 LOC/人日",
            "AI 判定の対象ユーザー bot", "worker-node-id",
            "## リポジトリ別内訳", "| group/project | 6 件", "## ユーザー別内訳",
            "| alice | ai |", "## 改善施策への示唆", "差し戻しが最多の group/project",
            "agent-audit）は未集計",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, out)
        self.assertNotIn("## エージェント利用", out)
        self.open.assert_not_called()

    def test_missing_optional_fields(self):
        path = self.write("minimal.json", {"totals": {"raw_metrics": {}}, "repos": []})
        code, out, err = self.run_cli("--input", path, "--month", MONTH)
        self.assertEqual(code, 0, err)
        self.assertIn("対象リポジトリ 対象なし", out)
        self.assertIn("閉じた Issue 未確認", out)
        self.assertIn("人件費 未確認", out)
        self.assertIn("## ユーザー別内訳\n\n未確認", out)

    def test_duplicate_on_later_page(self):
        self.mock_responses(
            ([{"body": f"本文\n{MARKER}"}], {"X-Next-Page": "2"}),
            ([{"body": f"{MARKER}\r\n本文"}], {}),
        )
        code, _, err = self.run_cli("--input", self.input, "--config", self.config, "--month", MONTH)
        self.assertEqual(code, 0, err)
        self.assertIn("スキップ", err)
        requests = [call.args[0] for call in self.open.call_args_list]
        self.assertEqual([req.get_method() for req in requests], ["GET", "GET"])
        self.assertTrue(requests[1].full_url.endswith("/notes?per_page=100&page=2"))

    def test_post_with_environment_url(self):
        self.mock_responses(([], {}), ({}, {}))
        code, out, err = self.run_cli("--input", self.input, "--post", "--month", MONTH)
        self.assertEqual(code, 0, err)
        get, post = [call.args[0] for call in self.open.call_args_list]
        self.assertEqual([get.get_method(), post.get_method()], ["GET", "POST"])
        self.assertEqual(post.full_url, "https://gitlab.example.com/api/v4/projects/group%2Fsub%2Fproject/issues/42/notes")
        self.assertEqual(json.loads(post.data), {"body": out})
        self.assertEqual(post.get_header("Private-token"), "test-token")

    def test_dry_run_default_and_override(self):
        for options in ([], ["--config", self.config, "--dry-run"],
                        ["--config", self.config, "--post", "--dry-run"]):
            with self.subTest(options=options):
                code, _, err = self.run_cli("--input", self.input, *options)
                self.assertEqual(code, 0, err)
                self.assertIn("dry-run", err)
        self.open.assert_not_called()

    def test_month_selection_and_boundaries(self):
        for today, expected in ((date(2026, 1, 1), "2025-12"),
                                (date(2026, 1, 31), "2025-12"),
                                (date(2024, 3, 31), "2024-02")):
            with self.subTest(today=today), patch.object(report, "datetime") as clock:
                clock.now.return_value.date.return_value = today
                code, out, err = self.run_cli("--input", self.input)
                self.assertEqual(code, 0, err)
                self.assertTrue(out.startswith(f"[efficiency-report: {expected}]\n"))
        code, out, err = self.run_cli("--input", self.input, "--month", "2024-02")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("[efficiency-report: 2024-02]\n"))
        code, _, err = self.run_cli("--input", self.input, "--month", "2024-13")
        self.assertEqual(code, 2)
        self.assertIn("--month", err)

    def test_invalid_input(self):
        for value, message in (
            ({}, "input.totals がありません"),
            ({"totals": {}}, "input.totals.raw_metrics がありません"),
            ({"totals": {"raw_metrics": []}}, "input.totals.raw_metrics はオブジェクト"),
            ({"totals": {"raw_metrics": {"merged_mr_count": True}}},
             "input.totals.raw_metrics.merged_mr_count は非負整数"),
            ([], "input は JSON オブジェクト"),
        ):
            with self.subTest(value=value):
                path = self.write("invalid.json", value)
                code, out, err = self.run_cli("--input", path, "--month", MONTH)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn(message, err)
        Path(path).write_text("{", encoding="utf-8")
        code, _, err = self.run_cli("--input", path, "--month", MONTH)
        self.assertEqual(code, 2)
        self.assertIn("invalid.json", err)
        self.open.assert_not_called()

    def test_audit_usage_and_failures(self):
        usage = {"period": "month", "by": "agent_cli", "rows": [
            {"group": "codex", "measured_in": 100, "measured_out": 23,
             "estimated_tokens": 40, "unmeasured_runs": 1}
        ]}
        stats = {"period": "month", "tools": [{"tool": "agent-flow", "error_class": {"verify": 2}}]}
        direct = self.write("usage.json", usage)
        dated = self.write("audit.json", {"month": MONTH, "usage": usage, "stats": stats})
        code, out, err = self.run_cli("--input", self.input, "--month", MONTH, "--audit-input", direct)
        self.assertEqual(code, 0, err)
        self.assertIn("集計対象月は未確認", out)
        self.assertIn("失敗クラスは未集計", out)
        code, out, err = self.run_cli("--input", self.input, "--month", MONTH, "--audit-input", dated)
        self.assertEqual(code, 0, err)
        self.assertIn("| codex | 100 | 23 | 40 | 1 |", out)
        self.assertIn("| agent-flow | verify | 2 |", out)
        code, _, err = self.run_cli("--input", self.input, "--month", "2026-09", "--audit-input", dated)
        self.assertEqual(code, 2)
        self.assertIn("audit_input.month が対象月と一致しません", err)

    def test_invalid_hub_url_does_not_open_connection(self):
        for url in ("https://user@gitlab.example.com/group/project/-/issues/1",
                    "https://gitlab.example.com/group/%2e%2e/-/issues/1",
                    "https://gitlab.example.com/group/project/-/issues/1?target=elsewhere"):
            with self.subTest(url=url):
                code, _, err = self.run_cli("--input", self.input, "--post", "--hub-issue-url", url)
                self.assertEqual(code, 2)
                self.assertIn("hub_issue_url", err)
        self.transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
