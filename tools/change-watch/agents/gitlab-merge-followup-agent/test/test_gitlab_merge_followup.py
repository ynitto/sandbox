"""手元の GitLab API と、ソケットを使わない同じ API のモックで確認する。"""
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CHANGE_WATCH = ROOT.parents[1]
sys.path[:0] = [str(CHANGE_WATCH), str(CHANGE_WATCH / "test")]
import dagrun
from changewatch import cli as core_cli, httpclient

spec = importlib.util.spec_from_file_location("gitlab_merge_followup", ROOT / "gitlab_merge_followup.py")
followup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(followup)

TOKEN = "fake-token"
MR_PATH = "/api/v4/projects/g%2Fp/merge_requests"
HUB_PATH = "/api/v4/projects/ops%2Fhub/issues/42/notes"


class FakeGitLab:
    def __init__(self):
        self.url = "http://gitlab.test"
        self.state = "merged"
        self.closes_status = 200
        self.requests = []
        self.writes = []

    def start(self, test):
        world = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                code, result, headers = world.route(self.command, self.path, self.headers, raw)
                data = json.dumps(result).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = respond

        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError as exc:
            test.skipTest(f"テスト用 HTTP ソケットを作成できません: {exc}")
        self.url = f"http://127.0.0.1:{server.server_port}"
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        test.addCleanup(server.server_close)
        test.addCleanup(worker.join)
        test.addCleanup(server.shutdown)

    @property
    def mr(self):
        return dict(id=112, iid=12, state=self.state, title="change-watch: 取得時の再試行を追加",
                    updated_at="2026-11-20T12:00:00Z", web_url=self.url + "/g/p/-/merge_requests/12",
                    description=f"一時的な失敗から復帰する。 Closes #7\nFixes #8\n"
                                f"Resolves {self.url}/g/p/-/issues/7,\n"
                                f"Resolved:\n  [別プロジェクトの Issue]({self.url}/ops/other/-/issues/7).\n"
                                f"See #9 and {self.url}/g/p/-/issues/10.")

    def issue(self, iid, *, project="g/p", project_id=1, state="opened"):
        return dict(iid=iid, project_id=project_id, state=state, web_url=f"{self.url}/{project}/-/issues/{iid}")

    def env(self):
        return dict(GITLAB_URL=self.url, GITLAB_PROJECT="g/p", GITLAB_TOKEN=TOKEN,
                    GITLAB_HUB_ISSUE_URL=self.url + "/ops/hub/-/issues/42")

    def route(self, method, url, headers, raw):
        if not any(key.lower() == "private-token" and value == TOKEN for key, value in headers.items()):
            return 401, {}, {}
        parts = urlsplit(url)
        path = unquote(parts.path)
        query = parse_qs(parts.query)
        self.requests.append((method, parts.path, query))
        if method == "POST" and parts.path == HUB_PATH:
            self.writes.append((parts.path, json.loads(raw)["body"]))
            return 201, {"id": 23}, {}
        if method != "GET":
            return 405, {}, {}
        prefix = unquote(MR_PATH)
        issue = self.issue(7)
        other = self.issue(7, project="ops/other", project_id=2, state="closed")
        responses = {
            prefix + "/12": (200, self.mr, {}),
            prefix + "/12/closes_issues": (self.closes_status, [other, issue, issue], {}),
            "/api/v4/projects/g/p/issues/7": (200, issue, {}),
            "/api/v4/projects/g/p/issues/8": (200, self.issue(8), {}),
            "/api/v4/projects/ops/other/issues/7": (200, other, {}),
        }
        if path == prefix and query.get("state") == ["merged"] and self.state == "merged":
            return 200, [self.mr], {"X-Next-Page": ""}
        if path == prefix + "/12/commits":
            if query.get("page") == ["1"]:
                return 200, [{"title": "取得エラー時に再試行する"}], {"X-Next-Page": "2"}
            return 200, [{"title": "失敗時のログを追加"}], {"X-Next-Page": ""}
        return responses.get(path, (404, {}, {}))

    def open(self, request, *, timeout):
        code, result, headers = self.route(request.get_method(), request.full_url, request.headers, request.data)
        if code >= 400:
            raise HTTPError(request.full_url, code, "", None, None)
        response = io.BytesIO(json.dumps(result).encode())
        response.headers = headers
        response.status = code
        return response


class FollowupTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.md = self.tmp / "mr-12.md"
        self.summary = self.tmp / "mr-12.json"
        self.fake = FakeGitLab()

    def cli(self, *args, **settings):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {**self.fake.env(), **settings}, clear=True):
            with redirect_stdout(out), redirect_stderr(err):
                status = followup.main([str(arg) for arg in args])
        return status, out.getvalue(), err.getvalue()

    def draft(self, state="merged"):
        change = json.dumps({"kind": "appeared", "key": "12", "record": {"iid": 12, "state": state}})
        return self.cli("draft", change, "--out", self.md, "--json-out", self.summary)

    def assert_drafts(self, issues):
        summary = json.loads(self.summary.read_text(encoding="utf-8"))
        self.assertEqual(summary["mr_iid"], 12)
        self.assertEqual([(issue["project_id"], issue["iid"]) for issue in summary["related_issues"]], issues)
        self.assertEqual(summary["draft_types"], ["issue_close_confirmation", "changelog"])
        body = self.md.read_text(encoding="utf-8")
        self.assertIn(self.fake.url + "/g/p/-/merge_requests/12", body)
        self.assertIn("この Issue を閉じてよいか", body)
        self.assertIn("クローズ済み", body)
        self.assertIn("## CHANGELOG 追記案（[Unreleased]）\n\n### change-watch: 取得時の再試行を追加\n\n-", body)
        self.assertIn("- 一時的な失敗から復帰する。", body)
        self.assertIn("- 取得エラー時に再試行する\n- 失敗時のログを追加", body)
        return body

    def test_draft_and_post_with_mock_transport(self):
        with patch.object(followup, "build_opener", return_value=self.fake):
            status, stdout, err = self.draft()
            self.assertEqual(status, 0, err)
            self.assertEqual(json.loads(stdout), json.loads(self.summary.read_text(encoding="utf-8")))
            body = self.assert_drafts([(1, 7), (2, 7)])
            requests = list(self.fake.requests)
            for value in (None, "1", "false", ""):
                settings = {} if value is None else {"MERGE_FOLLOWUP_DRY_RUN": value}
                status, stdout, err = self.cli("post", self.md, **settings)
                self.assertEqual(status, 0, err)
                self.assertIn(str(self.md), stdout)
            self.assertEqual(self.fake.requests, requests)
            self.assertEqual(self.fake.writes, [])
            status, _, err = self.cli("post", self.md, MERGE_FOLLOWUP_DRY_RUN="0")
            self.assertEqual(status, 0, err)
        self.assertTrue(body.startswith("【下書き】マージ後処理（MR !12）\n"))
        self.assertEqual(self.fake.writes, [(HUB_PATH, body)])

    def test_description_fallback_and_duplicate_references(self):
        self.fake.closes_status = 404
        with patch.object(followup, "build_opener", return_value=self.fake):
            status, _, err = self.draft()
            self.assertEqual(status, 0, err)
        self.assert_drafts([(1, 7), (1, 8), (2, 7)])
        issue_paths = [path for _, path, _ in self.fake.requests if "/issues/" in path]
        self.assertCountEqual(issue_paths, ["/api/v4/projects/g%2Fp/issues/7",
                                           "/api/v4/projects/g%2Fp/issues/8",
                                           "/api/v4/projects/ops%2Fother/issues/7"])

    def test_issue_reference_keyword_forms(self):
        keywords = ("close", "closes", "closed", "closing", "fix", "fixes", "fixed", "fixing",
                    "resolve", "resolves", "resolved", "resolving")
        url = self.fake.url + "/g/p/-/issues/7"
        for keyword in keywords:
            for reference, expected in (("#7", ("7", "")), (url, ("", url))):
                with self.subTest(keyword=keyword, reference=reference):
                    self.assertEqual(followup.ISSUE_REFERENCE.findall(f"{keyword.upper()} {reference}"),
                                     [expected])

    def test_closes_issues_server_error_stops_draft_without_fallback(self):
        self.fake.closes_status = 500
        with patch.object(followup, "build_opener", return_value=self.fake):
            status, stdout, err = self.draft()
        self.assertNotEqual(status, 0)
        self.assertIn("HTTP 500", err)
        self.assertEqual(stdout, "")
        self.assertEqual([path for _, path, _ in self.fake.requests],
                         [MR_PATH + "/12", MR_PATH + "/12/closes_issues"])
        self.assertFalse(self.md.exists())
        self.assertFalse(self.summary.exists())

    def test_unmerged_change_and_unmerged_gitlab_detail(self):
        with patch.object(followup, "build_opener", return_value=self.fake):
            first = self.draft(state="opened")
            self.assertEqual(self.fake.requests, [])
            self.fake.state = "opened"
            second = self.draft()
        for status, _, err in (first, second):
            self.assertNotEqual(status, 0)
            self.assertIn("マージ済み", err)
        self.assertFalse(self.md.exists())
        self.assertFalse(self.summary.exists())

    def test_post_target_and_quick_actions(self):
        self.md.write_text("【下書き】マージ後処理（MR !12）\n\n/close\n  /assign @bot\n", encoding="utf-8")
        with patch.object(followup, "build_opener", return_value=self.fake):
            settings = {"MERGE_FOLLOWUP_DRY_RUN": "0"}
            for target in ("http://other.test/ops/hub/-/issues/42", self.fake.url + HUB_PATH):
                self.assertNotEqual(self.cli("post", self.md, GITLAB_HUB_ISSUE_URL=target, **settings)[0], 0)
            self.assertEqual(self.fake.requests, [])
            target = self.fake.url + HUB_PATH.removesuffix("/notes")
            self.assertEqual(self.cli("post", self.md, GITLAB_HUB_ISSUE_URL=target, **settings)[0], 0)
        self.assertEqual(self.fake.writes[0][1].splitlines()[2:], [r"\/close", r"  \/assign @bot"])

    def prepare_dag(self):
        import yaml

        config = yaml.safe_load((ROOT / "change-watch.yaml.example").read_text(encoding="utf-8"))
        config["state_dir"] = str(self.tmp / "state")
        config["sources"]["gitlab-merged-mrs"]["jitter_sec"] = 0
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps(config), encoding="utf-8")
        conn = self.tmp / "connections.json"
        conn.write_text(json.dumps({"gitlab": [{"label": "default", "url": self.fake.url, "token": TOKEN}]}),
                        encoding="utf-8")
        dag = ROOT / "change-watch-merge-followup.yaml.example"
        dagrun.check_shape(self, dagrun.load(dag), dag.name)
        prefix = "/home/you/.config/change-watch/gitlab-merge-followup-agent/"
        paths = {prefix + "change-watch.yaml": str(cfg), "/home/you/.github/connections.yaml": str(conn),
                 prefix + "drafts/mr-${CHANGE.record.iid}.md": str(self.md),
                 prefix + "drafts/mr-${CHANGE.record.iid}.json": str(self.summary),
                 prefix + "drafts/mr-${DRAFT.mr_iid}.md": str(self.md),
                 "gitlab_merge_followup": str(ROOT / "gitlab_merge_followup.py")}
        options = dict(aw=[sys.executable, str(CHANGE_WATCH / "change_watch.py")], head=[], paths=paths)
        return dag, options

    def check_dag(self):
        dag, options = self.prepare_dag()
        env = {**self.fake.env(), "MERGE_FOLLOWUP_DRY_RUN": "1"}
        self.assertEqual(dagrun.run(dag, env=env, **options), dict(get=0, draft=0, post=0, commit=0))
        self.assert_drafts([(1, 7), (2, 7)])
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(dagrun.run(dag, env=env, **options), dict(get=0, draft=None, post=None, commit=None))
        result = self.cli("post", self.md, MERGE_FOLLOWUP_DRY_RUN="0")
        self.assertEqual(result[0], 0, result[2])
        self.assertEqual(self.fake.writes[0][0], HUB_PATH)

    def with_mock_dag_transport(self, check):
        def run_cli(argv, *, env, **kwargs):
            out, err = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
                status = core_cli.main(argv[2:])
            return subprocess.CompletedProcess(argv, status, out.getvalue(), err.getvalue())

        with patch.object(dagrun.subprocess, "run", side_effect=run_cli):
            with patch.object(httpclient, "_OPENER", self.fake):
                with patch("urllib.request.build_opener", return_value=self.fake):
                    with patch.object(followup, "build_opener", return_value=self.fake):
                        check()

    def test_dag_with_mock_transport(self):
        self.with_mock_dag_transport(self.check_dag)

    def test_dag_stops_after_draft_failure(self):
        dag, options = self.prepare_dag()
        self.fake.closes_status = 500
        env = {**self.fake.env(), "MERGE_FOLLOWUP_DRY_RUN": "0"}

        def check():
            with redirect_stdout(io.StringIO()):
                result = dagrun.run(dag, env=env, **options)
            self.assertEqual(result, dict(get=0, draft=1))

        self.with_mock_dag_transport(check)
        self.assertEqual(self.fake.writes, [])

    def test_dag_over_http(self):
        self.fake.start(self)
        self.check_dag()


if __name__ == "__main__":
    unittest.main()
