from contextlib import redirect_stdout
from datetime import date
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import weekly_report as report


BASE = "https://gitlab.example.com"
PROJECT = "/projects/platform%2Fapp"
HUB = "/projects/operations%2Fhub/issues/42/notes"
PERIOD = report.Period(date(2026, 11, 9), date(2026, 11, 16))
START = "2026-11-09T00:00:00Z"
DURING = "2026-11-14T09:00:00Z"
END = "2026-11-16T00:00:00Z"
BEFORE = "2026-11-08T23:59:59Z"


class WeeklyReportTest(unittest.TestCase):
    def setUp(self):
        self.gl = report.GitLab(BASE, "fake-token")
        self.comments = []
        self.config = {
            "gitlab_url": BASE, "token_env": "GITLAB_TOKEN",
            "projects": ["platform/app", 123],
            "hub_issue_url": BASE + "/operations/hub/-/issues/42",
            "include_open": True,
        }

    def fake_request(self, path, *, query=None, body=None):
        if path == HUB and body is not None:
            self.comments.append(body)
            return body, {}
        if path == HUB:
            if query["page"] == 1:
                return [{"body": "通常のコメント\n" + PERIOD.marker}], {"X-Next-Page": "2"}
            return list(self.comments), {"X-Next-Page": ""}
        if path.endswith("/closes_issues"):
            if path == PROJECT + "/merge_requests/1/closes_issues":
                return [{"iid": 7, "web_url": BASE + "/platform/app/-/issues/7"}], {}
            return [], {}
        if query.get("state") == "opened":
            return ([{}] if path.startswith(PROJECT) else []), {}
        if path == PROJECT + "/merge_requests":
            return [
                {"iid": 2, "title": "設定画面でのクラッシュを修正", "merged_at": DURING,
                 "labels": ["feature"], "author": {"username": "bob"}},
                {"iid": 1, "title": "feat(ui): 設定画面を追加", "merged_at": START,
                 "description": "Closes #7, see platform/other#3",
                 "web_url": BASE + "/platform/app/-/merge_requests/1",
                 "author": {"name": "Alice"}},
                {"iid": 90, "title": "期間前 MR", "merged_at": BEFORE, "updated_at": DURING},
                {"iid": 91, "title": "終了境界 MR", "merged_at": END},
            ], {}
        if path == PROJECT + "/issues":
            return [
                {"iid": 7, "title": "設定画面の追加", "closed_at": START},
                {"iid": 90, "title": "期間前 issue", "closed_at": BEFORE, "updated_at": DURING},
                {"iid": 91, "title": "終了境界 issue", "closed_at": END},
            ], {}
        if path == "/projects/123/merge_requests":
            return [
                {"iid": 3, "title": "初回表示を高速化", "labels": ["Performance"], "merged_at": DURING},
                {"iid": 4, "title": "依存関係を更新", "merged_at": DURING},
            ], {}
        if path == "/projects/123/issues":
            return [], {}
        if path.endswith("/pipelines"):
            if query.get("source") == "parent_pipeline":
                return ([{"id": 5, "status": "canceled", "created_at": DURING}]
                        if path.startswith(PROJECT) else []), {}
            if path.startswith(PROJECT):
                if query["page"] == 1:
                    return [
                        {"id": 1, "status": "success", "created_at": START},
                        {"id": 2, "status": "success", "created_at": END},
                    ], {"X-Next-Page": "2"}
                return [
                    {"id": 3, "status": "failed", "created_at": DURING},
                    {"id": 4, "status": "success", "created_at": BEFORE},
                ], {"X-Next-Page": ""}
            return [{"id": 6, "status": "success", "created_at": DURING}], {}
        self.fail(f"Unexpected API request: {path} {query}")

    def collected(self):
        with patch.object(self.gl, "request", side_effect=self.fake_request) as request:
            reports = report.collect(self.gl, self.config["projects"], PERIOD, include_open=True)
        return reports, request

    def test_collect_period_rates_and_open_counts(self):
        (app, other), request = self.collected()
        self.assertEqual([mr["iid"] for mr, _ in app.mrs], [1, 2])
        self.assertEqual([issue["iid"] for issue in app.issues], [7])
        self.assertEqual((app.pipeline_success, app.pipeline_total), (1, 3))
        self.assertEqual((other.pipeline_success, other.pipeline_total), (1, 1))
        self.assertEqual(app.open_counts, (1, 1))
        self.assertEqual(other.open_counts, (0, 0))
        request.assert_any_call(PROJECT + "/pipelines", query={
            "updated_after": "2026-11-09T00:00:00+00:00", "per_page": 100, "page": 2,
        })

    def test_collect_counts_child_pipeline_once_across_both_lists(self):
        child = {"id": 7, "status": "success", "created_at": DURING,
                 "source": "parent_pipeline"}
        failed = {"id": 8, "status": "failed", "created_at": DURING}

        def fake_request(path, *, query):
            if path == PROJECT + "/pipelines":
                if query.get("source") == "parent_pipeline":
                    return [child.copy()], {}
                return [failed, child], {}
            return self.fake_request(path, query=query)

        with patch.object(self.gl, "request", side_effect=fake_request):
            (app,) = report.collect(self.gl, ["platform/app"], PERIOD)

        self.assertEqual((app.pipeline_success, app.pipeline_total), (1, 2))

    def test_generate_weekly_and_release_markdown(self):
        reports, _ = self.collected()
        weekly, release = report.generate(PERIOD, reports)
        self.assertEqual(weekly.splitlines()[0], PERIOD.marker)
        for expected in ("2026-11-09 00:00", "2026-11-16 00:00", "マージ済み MR 4 件",
                         "クローズした issue 1 件", "2/4（50.0%）", "1/3（33.3%）", "1/1（100.0%）",
                         "作成者 Alice", "作成者 bob", "設定画面の追加",
                         "関連 issue [\\#7]", "platform/other\\#3", "MR 1 件、issue 1 件"):
            self.assertIn(expected, weekly)
        self.assertEqual(weekly.count("/-/issues/7"), 1)
        self.assertNotIn("期間前", weekly)
        self.assertNotIn("終了境界", weekly)
        self.assertTrue(release.startswith(report.RELEASE_HEADING))
        for category, title in (("新機能", "設定画面を追加"), ("改善", "初回表示を高速化"),
                                ("修正", "設定画面でのクラッシュを修正"), ("その他", "依存関係を更新")):
            self.assertIn(f"## {category}\n\n- {title}（", release)
        self.assertNotIn("feat(ui):", release)

    def test_empty_period(self):
        weekly, release = report.generate(PERIOD, [])
        self.assertIn("0/0（対象なし）", weekly)
        self.assertIn("## その他\n\n- なし", release)

    def run_cli(self, env, *args):
        stdout = io.StringIO()
        env = {"GITLAB_TOKEN": "fake-token", **env}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(self.config), encoding="utf-8")
            with patch.dict(os.environ, env, clear=True), redirect_stdout(stdout), \
                    patch.object(report.GitLab, "request", side_effect=self.fake_request) as request:
                status = report.main(["--config", str(path), "--end-date", "2026-11-16", *args])
        self.assertEqual(status, 0)
        return stdout.getvalue(), request

    def test_default_dry_run_displays_both_documents_without_accessing_hub(self):
        output, request = self.run_cli({})
        self.assertIn(report.REPORT_HEADING, output)
        self.assertIn(report.RELEASE_HEADING, output)
        self.assertFalse(any(call.args[0] == HUB for call in request.call_args_list))
        self.assertEqual(self.comments, [])

    def test_explicit_dry_run_and_environment_zero_override_post(self):
        self.config["post_enabled"] = True
        for env, args in (({"WEEKLY_REPORT_POST": "1"}, ("--dry-run",)),
                          ({"WEEKLY_REPORT_POST": "0"}, ())):
            with self.subTest(env=env, args=args):
                output, request = self.run_cli(env, *args)
                self.assertTrue(output.startswith(PERIOD.marker))
                self.assertFalse(any(call.args[0] == HUB for call in request.call_args_list))

    def test_post_once_and_skip_same_period_on_second_comments_page(self):
        output, request = self.run_cli({"WEEKLY_REPORT_POST": "1"})
        self.assertIn("投稿しました", output)
        self.assertEqual(len(self.comments), 1)
        self.assertTrue(self.comments[0]["body"].startswith(PERIOD.marker + "\n"))
        self.assertIn(report.RELEASE_HEADING, self.comments[0]["body"])
        self.assertEqual([call.args[0] for call in request.call_args_list if "body" in call.kwargs], [HUB])

        self.config["post_enabled"] = True
        output, request = self.run_cli({})
        self.assertIn("投稿済みのためスキップ", output)
        request.assert_any_call(HUB, query={"per_page": 100, "page": 2})
        self.assertFalse(any("body" in call.kwargs for call in request.call_args_list))
        self.assertEqual(len(self.comments), 1)

        output, _ = self.run_cli({}, "--days", "14")
        self.assertIn("投稿しました", output)
        self.assertTrue(self.comments[1]["body"].startswith("[weekly-report: 2026-11-02/2026-11-16]"))

    def test_hub_url_matches_gitlab_and_accepts_api_path(self):
        self.assertEqual(report.hub_notes_path(BASE + "/git", BASE + "/git/api/v4/projects/7/issues/9"),
                         "/projects/7/issues/9/notes")
        with self.assertRaises(ValueError):
            report.hub_notes_path(BASE, "https://other.example.com/operations/hub/-/issues/42")


if __name__ == "__main__":
    unittest.main()
