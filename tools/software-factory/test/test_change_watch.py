"""change-watch 経由の GitLab クライアント（本物の change-watch を偽の GitLab につないで確かめる）。"""
import importlib.util
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


SOURCE = Path(__file__).resolve().parents[1] / "factory.py"
CHANGE_WATCH = Path(__file__).resolve().parents[2] / "change-watch" / "change_watch.py"
SPEC = importlib.util.spec_from_file_location("factory_cw", SOURCE)
factory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = factory
SPEC.loader.exec_module(factory)

PROJECT = "/api/v4/projects/group%2Fproject"
TOKEN = "dummy-token-for-test"


class FakeGitLab:
    def __init__(self):
        self.issues = {}
        self.mrs = {}
        self.notes = {}
        self.requests = []
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.handle(self, "GET")

            def do_POST(self):
                outer.handle(self, "POST")

            def do_PUT(self):
                outer.handle(self, "PUT")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def count(self, method, path):
        return sum(1 for m, p in self.requests if m == method and p == path)

    def send(self, handler, status, body):
        raw = json.dumps(body).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    def page(self, items, query):
        per, page = int(query.get("per_page", 20)), int(query.get("page", 1))
        return items[(page - 1) * per: page * per]

    def handle(self, handler, method):
        url = urlparse(handler.path)
        path = url.path
        query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        length = int(handler.headers.get("Content-Length") or 0)
        body = json.loads(handler.rfile.read(length)) if length else {}
        with self.lock:
            self.requests.append((method, path))
        if handler.headers.get("PRIVATE-TOKEN") != TOKEN:
            return self.send(handler, 401, {"message": "401 Unauthorized"})
        rest = path[len(PROJECT):] if path.startswith(PROJECT) else None
        parts = [part for part in (rest or "").split("/") if part]
        if rest is None:
            return self.send(handler, 404, {"message": "not found"})
        if not parts and method == "GET":
            return self.send(handler, 200, {"default_branch": "main"})
        if parts == ["issues"] and method == "GET":
            items = [issue for issue in self.issues.values() if issue["state"] == query.get("state", "opened")]
            if "labels" in query:
                items = [issue for issue in items if query["labels"] in issue["labels"]]
            return self.send(handler, 200, self.page(items, query))
        if len(parts) == 2 and parts[0] == "issues":
            issue = self.issues.get(int(parts[1]))
            if issue is None:
                return self.send(handler, 404, {"message": "not found"})
            if method == "PUT":
                removed = set(filter(None, body.get("remove_labels", "").split(",")))
                labels = [label for label in issue["labels"] if label not in removed]
                for label in filter(None, body.get("add_labels", "").split(",")):
                    if label not in labels:
                        labels.append(label)
                issue["labels"] = labels
            return self.send(handler, 200, issue)
        if len(parts) == 3 and parts[2] == "notes":
            key = (parts[0], int(parts[1]))
            if method == "POST":
                self.notes.setdefault(key, []).append({"body": body["body"]})
                return self.send(handler, 201, {"id": len(self.notes[key])})
            return self.send(handler, 200, self.page(self.notes.get(key, []), query))
        if parts == ["merge_requests"] and method == "GET":
            items = [{key: value for key, value in mr.items() if key != "head_pipeline"}
                     for mr in self.mrs.values() if mr["state"] == "opened"]
            return self.send(handler, 200, self.page(items, query))
        if parts == ["merge_requests"] and method == "POST":
            iid = max(self.mrs, default=41) + 1
            self.mrs[iid] = {"iid": iid, "web_url": f"{self.url}/mr/{iid}", "sha": "sha-new", "state": "opened",
                             "draft": True, "source_branch": body["source_branch"], "labels": [body["labels"]],
                             "head_pipeline": None}
            return self.send(handler, 201, self.mrs[iid])
        if len(parts) == 2 and parts[0] == "merge_requests":
            mr = self.mrs.get(int(parts[1]))
            return self.send(handler, 200, mr) if mr else self.send(handler, 404, {"message": "not found"})
        if len(parts) == 3 and parts[2] == "changes":
            return self.send(handler, 200, {"changes": [{"old_path": "a", "new_path": "a", "diff": "@@ -1 +1 @@\n"}]})
        return self.send(handler, 404, {"message": "not found"})


class ChangeWatchClientTests(unittest.TestCase):
    def setUp(self):
        self.gitlab = FakeGitLab()
        self.addCleanup(self.gitlab.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        connections = self.tmp / "connections.json"
        connections.write_text(json.dumps({"gitlab": [{"label": "default", "url": self.gitlab.url,
                                                       "token": "${FACTORY_TEST_TOKEN}"}]}), encoding="utf-8")
        common = {"connection": "gitlab", "key": "iid", "min_interval_sec": 3600, "failure_backoff_sec": 0,
                  "pagination": {"type": "page", "size_param": "per_page", "size": 100},
                  "rate_limit": {"group": "gitlab"}, "retry": {"attempts": 1}}
        headers = {"PRIVATE-TOKEN": "{{ conn.token }}"}
        sources = {
            "factory-issues": {**common, "request": {
                "url": "{{ conn.url }}" + PROJECT + "/issues", "headers": headers,
                "query": {"state": "opened", "labels": "factory"}},
                "item": {"url": "{{ conn.url }}" + PROJECT + "/issues/{{ key }}"}},
            "factory-mrs": {**common, "request": {
                "url": "{{ conn.url }}" + PROJECT + "/merge_requests", "headers": headers,
                "query": {"state": "opened", "labels": "factory"}},
                "item": {"url": "{{ conn.url }}" + PROJECT + "/merge_requests/{{ key }}"}},
        }
        config = self.tmp / "change-watch.json"
        config.write_text(json.dumps({"version": 1, "state_dir": str(self.tmp / "state"), "sources": sources}),
                          encoding="utf-8")
        settings = factory._change_watch_config({
            "command": [sys.executable, str(CHANGE_WATCH)], "config": str(config),
            "connections": str(connections), "ci_max_age_sec": 300,
        }, ["group/project"])
        env = patch.dict(os.environ, {"FACTORY_TEST_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)
        self.client = factory.ChangeWatchGitLabClient(factory.Runner(), settings, "factory")

    def add_issue(self, iid, *labels):
        self.gitlab.issues[iid] = {"iid": iid, "title": f"課題 {iid}", "description": None, "state": "opened",
                                   "labels": ["factory", *labels], "updated_at": "2026-11-27T00:00:00Z"}

    def add_mr(self, iid, branch, status):
        self.gitlab.mrs[iid] = {"iid": iid, "web_url": f"{self.gitlab.url}/mr/{iid}", "sha": "sha-one",
                                "state": "opened", "draft": True, "source_branch": branch, "labels": ["factory"],
                                "head_pipeline": {"status": status}}

    def test_issue_list_is_shared_and_own_label_changes_are_seen_before_next_fetch(self):
        self.add_issue(1)
        self.add_issue(2, "factory:mr-open")
        issues = self.client.list_factory_issues("group/project", "factory")
        self.assertEqual([issue["number"] for issue in issues], [1, 2])
        self.assertEqual(issues[0]["body"], "")
        self.client.set_progress_label("group/project", 1, "factory:implementing")
        self.client.set_progress_label("group/project", 2, "factory:mr-open")          # 変わらなければ送らない
        self.assertEqual(self.gitlab.count("PUT", PROJECT + "/issues/2"), 0)
        self.assertEqual(self.gitlab.issues[1]["labels"], ["factory", "factory:implementing"])
        self.gitlab.issues[1]["labels"].append("人が付けた")
        self.client.set_progress_label("group/project", 1, "factory:mr-open")
        self.assertEqual(self.gitlab.issues[1]["labels"], ["factory", "人が付けた", "factory:mr-open"])
        again = self.client.list_factory_issues("group/project", "factory")
        self.assertEqual(self.gitlab.count("GET", PROJECT + "/issues"), 1)            # 間隔の内は問い合わせない
        self.assertIn("factory:mr-open", factory.label_names(again[0]))               # 自分の書き込みは反映済み

    def test_ci_state_and_head_share_one_request_within_max_age(self):
        self.add_mr(42, "factory/issue-1", "running")
        mr = self.client.find_mr_by_head("group/project", "factory/issue-1")
        self.assertEqual((mr["number"], mr["headRefOid"], mr["isDraft"]), (42, "sha-one", True))
        self.assertIsNone(self.client.find_mr_by_head("group/project", "factory/issue-9"))
        self.assertEqual(self.client.mr_head_sha("group/project", 42), "sha-one")
        self.assertEqual(self.client.mr_checks_state("group/project", 42), "pending")
        self.assertEqual(self.client.mr_head_sha("group/project", 42), "sha-one")
        self.assertEqual(self.gitlab.count("GET", PROJECT + "/merge_requests/42"), 1)
        self.gitlab.mrs[42]["head_pipeline"] = {"status": "success"}
        self.assertEqual(self.client.mr_checks_state("group/project", 42), "pending")   # まだ使い回す
        self.assertEqual(self.client.mr_checks_state("group/project", 42, fresh=True), "success")
        self.assertEqual(self.gitlab.count("GET", PROJECT + "/merge_requests/42"), 2)

    def test_created_mr_is_found_before_next_fetch(self):
        self.assertIsNone(self.client.find_mr_by_head("group/project", "factory/issue-1"))
        number = self.client.create_draft_mr("group/project", "factory/issue-1", "直す", "Closes #1", "factory")
        self.assertEqual(self.gitlab.mrs[number]["source_branch"], "factory/issue-1")
        self.assertEqual(self.client.find_mr_by_head("group/project", "factory/issue-1")["number"], number)
        self.assertEqual(self.gitlab.count("GET", PROJECT + "/merge_requests"), 1)
        self.client.default_branch("group/project")
        self.assertEqual(self.gitlab.count("GET", PROJECT), 1)

    def test_long_comments_and_marker_search(self):
        body = "<!-- factory-review run=r1 -->\n" + "指摘 " * 20000
        self.client.comment_mr("group/project", 42, body)
        self.assertEqual(self.gitlab.notes[("merge_requests", 42)][0]["body"], body)
        self.assertTrue(self.client.mr_comment_exists("group/project", 42, "factory-review run=r1"))
        self.assertFalse(self.client.mr_comment_exists("group/project", 42, "factory-review run=r2"))
        review = "<!-- factory:review run=r1 sha=sha-one -->\n判定"
        self.client.comment_mr("group/project", 42, review)
        self.assertEqual(self.client.find_review_comment("group/project", 42, "sha-one"), review)
        self.assertIsNone(self.client.find_review_comment("group/project", 42, "sha-two"))
        self.client.comment_issue("group/project", 1, "失敗しました")
        self.assertEqual(self.gitlab.notes[("issues", 1)], [{"body": "失敗しました"}])
        self.assertEqual(self.client.mr_diff("group/project", 42), "diff --git a/a b/a\n@@ -1 +1 @@\n")

    def test_change_watch_failure_is_a_gitlab_error(self):
        self.client.base[self.client.base.index("--config") + 1] = str(self.tmp / "missing.json")
        with self.assertRaisesRegex(factory.GitLabError, "change-watch get が終了コード"):
            self.client.list_factory_issues("group/project", "factory")


class ChangeWatchConfigTests(unittest.TestCase):
    def values(self, **section):
        values = json.loads((SOURCE.parent / "factory.example.json").read_text(encoding="utf-8"))
        values.pop("gitlab_url")
        values["change_watch"] = {"command": "change-watch", "config": "cw.yaml", "connections": "conn.yaml",
                                  **section}
        return values

    def test_defaults_for_one_repository_and_no_gitlab_url_needed(self):
        with patch.dict(os.environ, {}, clear=True):
            config = factory.Config(self.values())
        self.assertEqual(config.change_watch["command"], ["change-watch"])
        self.assertEqual(config.change_watch["sources"],
                         {"group/project": {"issues": "factory-issues", "merge_requests": "factory-mrs"}})
        self.assertEqual(config.change_watch["ci_max_age_sec"], 240)

    def test_rejects_invalid_sections(self):
        for section in ({"command": []}, {"config": ""}, {"ci_max_age_sec": -1}, {"extra": 1},
                        {"sources": {"group/project": {"issues": "a"}}},
                        {"sources": {"other/project": {"issues": "a", "merge_requests": "b"}}}):
            with self.subTest(section=section), self.assertRaises(factory.ConfigError):
                factory.Config(self.values(**section))
        values = self.values()
        values["repositories"] = ["group/a", "group/b"]
        with self.assertRaisesRegex(factory.ConfigError, "change_watch.sources"):
            factory.Config(values)

    def test_main_uses_change_watch_without_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "factory.json"
            path.write_text(json.dumps(self.values()), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch.object(factory, "Controller") as controller:
                self.assertEqual(factory.main(["status", "--config", str(path)]), 0)
            self.assertIsInstance(controller.call_args[0][1], factory.ChangeWatchGitLabClient)

    def test_runner_keeps_token_only_for_change_watch(self):
        code = "import os; print(os.environ.get('GITLAB_TOKEN', ''))"
        with patch.dict(os.environ, {"GITLAB_TOKEN": "dummy"}):
            self.assertEqual(factory.Runner().run([sys.executable, "-c", code])[1].strip(), "")
            self.assertEqual(factory.Runner().run([sys.executable, "-c", code], keep_token=True)[1].strip(), "dummy")


if __name__ == "__main__":
    unittest.main()
