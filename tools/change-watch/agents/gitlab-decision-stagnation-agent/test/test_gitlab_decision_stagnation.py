from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gitlab_decision_stagnation as agent

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
HOST = "https://gitlab.test"
HUB = "ops%2Fhub/issues/42/notes"
ENV = dict(GITLAB_URL=HOST, GITLAB_TOKEN="fake-token",
           GITLAB_PROJECTS="g/p,77",
           GITLAB_HUB_ISSUE_URL=HOST + "/ops/hub/-/issues/42")


def record(iid, **extra):
    return dict(id=iid, iid=iid, state="opened", title=f"項目 {iid}",
                created_at="2026-11-01T12:00:00Z", web_url=f"{HOST}/g/p/-/issues/{iid}",
                assignees=[{"username": "owner"}]) | extra


def label_event(iid, action, day):
    return dict(id=iid, action=action, label={"name": "needs"},
                created_at=f"2026-11-{day:02d}T12:00:00Z")


class FakeGitLab:
    def __init__(self):
        self.requests = []
        self.writes = []
        self.notes = [{"body": "既存コメント"}]
        self.responses = {
            ("g%2Fp/merge_requests", "1"): ([record(1, detailed_merge_status="not_approved"),
                                            record(2, draft=True)], {"X-Next-Page": "2"}),
            ("g%2Fp/merge_requests", "2"): ([
                record(3, reviewers=[{"username": "reviewer"}]),
                record(4, reviewers=[{"username": "reviewer"}]),
                record(5, title="WIP: 旧形式"),
                record(6),
            ], {}),
            "g%2Fp/merge_requests/3/approvals": {"approvals_left": 0},
            "g%2Fp/merge_requests/3/reviewers": [
                {"state": "review_started", "created_at": "2026-11-13T12:00:00Z"}],
            "g%2Fp/merge_requests/4/approvals": {"approved": True},
            "g%2Fp/merge_requests/4/reviewers": [{"state": "reviewed"}],
            "g%2Fp/merge_requests/6/approvals": {"approvals_left": 2},
            "g%2Fp/issues": [record(11, labels=["needs", "decision-packet"]),
                             record(12, labels=["unrelated"]),
                             record(13, labels=["status:review-ready"]),
                             record(14, labels=["needs"], state="closed")],
            ("g%2Fp/issues/11/resource_label_events", "1"): (
                [label_event(2, "remove", 4), label_event(1, "add", 2)], {"X-Next-Page": "2"}),
            ("g%2Fp/issues/11/resource_label_events", "2"): ([label_event(3, "add", 10)], {}),
            "g%2Fp/issues/12/resource_label_events": [],
            "g%2Fp/issues/13/resource_label_events": 403,
            "77/merge_requests": [],
            "77/issues": [record(15, labels=["decision"], assignees=[], assignee={"username": "other"},
                                 created_at="2026-11-20T13:00:00Z", web_url=HOST + "/g/other/-/issues/15")],
            "77/issues/15/resource_label_events": [],
        }

    def open(self, request, *, timeout):
        self.assert_token(request)
        url = urlsplit(request.full_url)
        project_path = url.path.removeprefix("/api/v4/projects/")
        method = request.get_method()
        query = parse_qs(url.query)
        self.requests.append((method, project_path, query))
        if method == "POST":
            if project_path != HUB:
                raise AssertionError(f"unexpected write to {project_path}")
            note = json.loads(request.data)
            self.writes.append(note["body"])
            self.notes.append(note)
            result = {"id": 1}
        elif project_path == HUB:
            page = query["page"][0]
            result = ([self.notes[0]], {"X-Next-Page": "2"}) if page == "1" else self.notes[1:]
        else:
            page = query.get("page", ["1"])[0]
            result = self.responses.get((project_path, page), self.responses.get(project_path, 500))
        if isinstance(result, int):
            raise HTTPError(request.full_url, result, "", None, None)
        payload, headers = result if isinstance(result, tuple) else (result, {})
        response = io.BytesIO(json.dumps(payload).encode())
        response.headers = headers
        return response

    def assert_token(self, request):
        if request.get_header("Private-token") != ENV["GITLAB_TOKEN"]:
            raise AssertionError("missing fake token")


class DecisionStagnationTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitLab()
        opener = patch.object(agent, "build_opener", return_value=self.fake)
        opener.start()
        self.addCleanup(opener.stop)
        settings = patch.dict(os.environ, ENV, clear=True)
        settings.start()
        self.addCleanup(settings.stop)

    def collect(self):
        return agent.collect(agent.GitLab(), {"g/p", "77"}, {"needs", "decision"},
                             {"decision-packet", "status:review-ready"}, NOW)

    def test_collect_filters_and_paginates(self):
        items = self.collect()
        self.assertEqual({item["iid"] for item in items}, {1, 3, 6, 11, 13, 15})
        reasons = {item["iid"]: item["reasons"] for item in items}
        self.assertEqual(reasons[1], ["承認待ち"])
        self.assertEqual(reasons[3], ["レビュー待ち"])
        self.assertEqual(reasons[11], ["判断待ち", "判断パケット関連"])
        self.assertEqual(reasons[13], ["判断パケット関連"])
        for method, path, query in self.fake.requests:
            self.assertEqual(method, "GET")
            if path.endswith(("/merge_requests", "/issues")):
                self.assertEqual(query["state"], ["opened"])
                self.assertEqual(query["scope"], ["all"])
        self.assertTrue(all("/merge_requests/2/" not in path and "/merge_requests/5/" not in path
                            for _, path, _ in self.fake.requests))

    def test_age_and_report_with_overdue_items(self):
        items = self.collect()
        self.assertEqual({item["iid"]: item["days"] for item in items},
                         {1: 19, 3: 7, 6: 19, 11: 10, 13: 19, 15: 0})
        body = agent.render(items, NOW.date(), 7)
        self.assertTrue(body.startswith("<!-- gitlab-decision-stagnation 2026-11-20 count=6 -->\n"))
        rows = [line for line in body.splitlines() if line.startswith("- ")]
        self.assertEqual([line.split(" — ")[1].split()[0] for line in rows], ["19", "19", "19", "10", "7", "0"])
        self.assertIn("⚠️ 要判断（滞留）** @owner。判断をお願いします。", rows[3])
        self.assertNotIn("⚠️", rows[4])
        self.assertNotIn("@owner", rows[4])

    def test_report_with_no_overdue_items(self):
        body = agent.render(self.collect(), NOW.date(), 19)
        self.assertIn("判断待ちサマリ（2026-11-20、6 件）", body)
        self.assertNotIn("⚠️", body)
        self.assertNotIn("判断をお願いします", body)

    def test_report_with_no_items(self):
        self.assertEqual(agent.render([], NOW.date(), 7),
                         "<!-- gitlab-decision-stagnation 2026-11-20 count=0 -->\n判断待ちはありません\n")

    def test_dry_run_displays_markdown_without_api_requests(self):
        body = agent.render([], NOW.date(), 7)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.md"
            report.write_text(body, encoding="utf-8")
            for setting in ({}, {"DECISION_STAGNATION_DRY_RUN": "false"}):
                with self.subTest(setting=setting), patch.dict(os.environ, setting):
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        self.assertEqual(agent.main(["post", str(report)]), 0)
                    self.assertEqual(stdout.getvalue(), body)
        self.assertEqual(self.fake.requests, [])

    def test_live_post_skips_existing_date_even_if_count_changes(self):
        body = agent.render([], NOW.date(), 7)
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            with patch.dict(os.environ, {"DECISION_STAGNATION_DRY_RUN": "0"}):
                report = Path(directory) / "report.md"
                report.write_text(body, encoding="utf-8")
                self.assertEqual(agent.main(["post", str(report)]), 0)
                report.write_text(agent.render(self.collect(), NOW.date(), 7), encoding="utf-8")
                self.assertEqual(agent.main(["post", str(report)]), 0)
        self.assertEqual(self.fake.writes, [body])

    def test_report_command_uses_configured_labels(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "DECISION_STAGNATION_LABELS": "unrelated",
            "DECISION_STAGNATION_PACKET_LABELS": "",
        }):
            report = Path(directory) / "report.md"
            self.assertEqual(agent.main(["report", "--out", str(report)]), 0)
            body = report.read_text(encoding="utf-8")
        self.assertIn("count=4", body)
        self.assertIn("Issue #12", body)
        self.assertNotIn("Issue #13", body)


if __name__ == "__main__":
    unittest.main()
