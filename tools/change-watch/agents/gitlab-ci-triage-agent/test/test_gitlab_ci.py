"""分類・マスク・設定の例と、偽 GitLab を使った診断の流れを確かめる。"""
from __future__ import annotations

from collections import defaultdict
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
from unittest import mock
from urllib.parse import parse_qs, quote, unquote, urlsplit

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURES = HERE / "fixtures"
PLUGIN = ROOT / "gitlab_ci.py"
CHANGE_WATCH = ROOT.parents[1] / "change_watch.py"
spec = importlib.util.spec_from_file_location("gitlab_ci", PLUGIN)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
sys.path.insert(0, str(CHANGE_WATCH.parent / "test"))
import dagrun

TOKEN = "fake.env.secret"
PROJECT = "g/p"
SHA = "a" * 40
PYTEST = "FAILED tests/test_cart.py::test_empty_cart"
PIP = "ERROR: No matching distribution found for requests==99.0"
DISK = "fatal: No space left on device"
PATTERNS = ci.BUILTIN_PATTERNS["patterns"]
MR_NOTES = "merge_requests/12/notes"
COMMIT_COMMENTS = f"repository/commits/{SHA}/comments"
ISSUE_NOTES = "issues/42/notes"


def cli(*args, env=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env or {}, clear=True), redirect_stdout(out), redirect_stderr(err):
        code = ci.main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


def job(id=1, name="build", **fields):
    return dict({"id": id, "name": name, "stage": "build", "failure_reason": "script_failure",
                 "web_url": f"https://gl.example/g/p/-/jobs/{id}"}, **fields)


def pipeline(**fields):
    return dict({"id": 123, "project_id": 7, "sha": SHA, "ref": "feature/cart", "source": "push",
                 "web_url": "https://gl.example/g/p/-/pipelines/123"}, **fields)


def rule(id, category="dependency", priority=50, **match):
    return dict(id=id, category=category, priority=priority, title=id,
                match=match or {"regex": ["hit"]}, next_action="ログを確かめてください。")


class ClassifyTest(unittest.TestCase):
    def test_fixtures(self):
        expected = ci.read_json(FIXTURES / "expected.json")
        self.assertCountEqual([path.relative_to(FIXTURES).as_posix() for path in FIXTURES.glob("traces/*/*.log")],
                              [name for name in expected if name.startswith("traces/")])
        self.assertCountEqual([p["id"] for p in PATTERNS], [
            Path(name).stem for name in expected if "failure_reason" in expected[name]
            or "/unknown/" not in name and "/mixed/" not in name
        ])
        for name, want in expected.items():
            with self.subTest(name=name):
                trace = ci.clean_trace((FIXTURES / name).read_bytes()) if name.startswith("traces/") else ""
                result = ci.classify(job(failure_reason=want.get("failure_reason")), trace, PATTERNS)
                self.assertEqual((result["category"], result["pattern"], "fix" in result),
                                 (want["category"], want["pattern"], want["fix"]))
                for line in want["excerpt_contains"]:
                    self.assertIn(line, result["excerpt"])

    def test_all_builtin_failure_reasons(self):
        for pattern in PATTERNS:
            for reason in pattern["match"].get("failure_reason", []):
                with self.subTest(pattern=pattern["id"], reason=reason):
                    result = ci.classify(job(failure_reason=reason), "", PATTERNS)
                    self.assertEqual(result["pattern"], pattern["id"])

    def test_tie_breaks(self):
        rules = [rule("a"), rule("b", regex=["early"]), rule("c", regex=["early"]),
                 rule("d", "configuration"), rule("e", "test_failure", 60),
                 rule("f", failure_reason=["script_failure"])]
        result = ci.classify(job(), "early\nhit\nhit", rules)
        self.assertEqual(result["pattern"], "e")
        self.assertEqual(result["line"], 2)
        self.assertEqual(result["also_matched"], ["d", "b", "c", "a", "f"])

    def test_match_conditions(self):
        p = rule("specific", regex=["unrelated", "error"], failure_reason=["other", "script_failure"],
                 job_name="^build", stage="^build$", ignore_case=True)
        for source, trace, matches in [
            (job(), "ERROR", True), (job(name="deploy"), "ERROR", False),
            (job(stage="test"), "ERROR", False), (job(failure_reason="other"), "", False),
            (job(failure_reason="unknown_failure"), "ERROR", False),
        ]:
            with self.subTest(source=source, trace=trace):
                self.assertEqual(ci.classify(source, trace, [p])["pattern"] == "specific", matches)

    def test_scan_limits(self):
        recipes = [rule("specific")]
        for trace in ("hit\n" + "\n".join(["normal"] * ci.SCAN_LINES), "x" * ci.SCAN_LINE_CHARS + "hit"):
            self.assertIsNone(ci.classify(job(), trace, recipes)["pattern"])
        self.assertEqual(ci.classify(job(), "x" * (ci.SCAN_LINE_CHARS - 3) + "hit", recipes)["line"], 1)

    def test_diagnose_is_deterministic_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ci, "GitLab", side_effect=AssertionError):
            tmp = Path(tmp)
            ci.write_json(tmp / "pipeline.json", pipeline())
            ci.write_json(tmp / "jobs.json", [job(trace="trace-1.log")])
            (tmp / "trace-1.log").write_text(PIP, encoding="utf-8")
            self.assertEqual(cli("diagnose", "#123", "--dir", tmp)[0], 0)
            paths = [tmp / name for name in ("diagnosis.json", "comment.md")]
            first = [path.read_bytes() for path in paths]
            self.assertEqual(cli("diagnose", 123, "--dir", tmp)[0], 0)
            self.assertEqual([path.read_bytes() for path in paths], first)
            code, out, _ = cli("diagnose", "--trace-file", tmp / "trace-1.log", "--job-name", "first")
            self.assertEqual(code, 0)
            self.assertIn("pip-no-matching-distribution", out)
            self.assertIn(PIP, out)


class AggregateTest(unittest.TestCase):
    def test_grouping_and_limits(self):
        jobs = [job(i, f"job-{i}", skipped_trace=i == 21, allow_failure=i == 22) for i in range(1, 23)]
        traces = {1: PYTEST, 2: PIP, 3: PIP, 4: DISK, 5: "tool: command not found"}
        result = ci.aggregate(pipeline(), list(reversed(jobs)), traces, PATTERNS)
        self.assertEqual(result["category"], "configuration")
        self.assertTrue(result["mixed"])
        self.assertFalse(result["known"])
        self.assertEqual(result["counts"], dict(configuration=1, infrastructure=1, dependency=2,
                                                test_failure=1, unknown=15))
        self.assertEqual([g["job"]["id"] for g in result["groups"]], [5, 4, 2, 1, 6])
        self.assertEqual(result["groups"][2]["same_cause"], ["job-3"])
        self.assertEqual(result["others"][0], dict(name="job-7", category="unknown", skipped_trace=False))
        self.assertEqual(result["others"][-1], dict(name="job-21", category="unknown", skipped_trace=True))

    def test_fingerprint_and_no_jobs(self):
        def make(jobs, traces):
            return ci.aggregate(pipeline(), jobs, traces, PATTERNS)
        first = make([job(1), job(2, "unit")], {1: PIP, 2: PYTEST})
        rerun = make([job(4, "unit"), job(3)], {3: "new output\n" + PIP, 4: PYTEST})
        self.assertEqual(first["fp"], rerun["fp"])
        self.assertTrue(first["known"])
        self.assertNotEqual(make([job(1, "other"), job(2, "unit")], {1: PIP, 2: PYTEST})["fp"], first["fp"])
        self.assertNotEqual(make([job(1), job(2, "unit")], {1: DISK, 2: PYTEST})["fp"], first["fp"])
        self.assertIn("失敗したジョブが見つかりません", ci.render_comment(make([], {})))

    def test_yaml_errors(self):
        message = "jobs:build:script config should be a string"
        result = ci.aggregate(pipeline(yaml_errors=message), [job()], {}, PATTERNS)
        self.assertEqual(result["category"], "configuration")
        self.assertEqual(result["groups"][0]["excerpt"], message)
        self.assertEqual(result["groups"][0]["pattern"], "gitlab-ci-yaml-invalid")


class PatternsFileTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "patterns.json"

    def check(self, patterns, **fields):
        ci.write_json(self.path, dict({"version": 1, "patterns": patterns}, **fields))
        return cli("patterns", "--patterns", self.path, "--check")

    def test_merge_and_example(self):
        self.assertEqual(self.check([
            rule("pip-no-matching-distribution", regex=["replacement"]),
            {"id": "pytest-failed", "enabled": False},
            rule("site-error"),
        ]), (0, "", ""))
        effective = ci.load_patterns(self.path)["patterns"]
        self.assertIsNone(ci.classify(job(), PYTEST, effective)["pattern"])
        self.assertIsNone(ci.classify(job(), PIP, effective)["pattern"])
        self.assertEqual(ci.classify(job(), "replacement", effective)["pattern"], "pip-no-matching-distribution")
        self.assertEqual(ci.classify(job(), "hit", effective)["pattern"], "site-error")
        self.assertEqual(cli("patterns", "--check", "--patterns", ROOT / "ci-triage-patterns.json.example"),
                         (0, "", ""))

    def test_invalid_patterns_report_each_error(self):
        candidate = rule("duplicate")
        code, out, err = self.check([
            candidate, candidate, rule("typo", regex=["("]), rule("type", priority=True),
            dict(rule("template"), fix="{missing}"), rule("category", "unknown"),
            dict(rule("extra"), unknown_key=True),
        ])
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(len(err.splitlines()), 6)
        for index in range(1, 7):
            self.assertIn(f"patterns[{index}]", err)
        self.assertIn("version", self.check([], version=2)[2])
        self.assertIn("mask[0]", self.check([], mask=["("])[2])

    def test_captures_are_disarmed(self):
        p = rule("capture", regex=["missing (?P<item>.*)", "fallback"])
        p["fix"] = "{{依存}} {item} を足してください。"
        self.assertEqual(self.check([p]), (0, "", ""))
        result = ci.classify(job(), "missing @user#24`" + "x" * 100, [p])
        self.assertEqual(result["fix"], "{依存} `" + ("@user#24" + "x" * 100)[:80] + "` を足してください。")
        self.assertIn("（不明）", ci.classify(job(), "fallback", [p])["fix"])


class PaginationTest(unittest.TestCase):
    def test_invalid_next_page_is_an_error(self):
        gl = ci.GitLab("https://gitlab.example.com", TOKEN, "group/project")
        for invalid in ("1", "broken"):
            with self.subTest(next_page=invalid):
                with mock.patch.object(gl, "call", return_value=([job()], {"x-next-page": invalid})):
                    with self.assertRaisesRegex(ValueError, "次ページ番号"):
                        list(gl.paginate("/pipelines/1/jobs"))


class TraceTest(unittest.TestCase):
    def test_masks(self):
        result = ci.clean_trace((FIXTURES / "secrets.log").read_bytes(), TOKEN, ["INTERNAL-[A-Z0-9]{12}"])
        self.assertEqual(result, (FIXTURES / "secrets.expected.log").read_text(encoding="utf-8"))

    def test_encoded_token_is_masked(self):
        token = "fake+b/c=d%secret"
        self.assertEqual(ci.mask_lines([token, quote(token), quote(token, safe="")], token), ["***"] * 3)

    def test_tail_and_formatting(self):
        data = b"drop\n" + b"x" * ci.TRACE_TAIL_BYTES + b"\nold\r\x1b[0Kkept\r\nsection_end:123:step\n"
        tail, clipped, key = ci.read_trace_tail(io.BytesIO(data))
        self.assertEqual(len(tail), ci.TRACE_TAIL_BYTES)
        self.assertTrue(clipped)
        self.assertEqual(ci.clean_trace(tail, clipped=clipped, in_key=key), "kept\n\n")
        private = b"-----BEGIN PRIVATE KEY-----\n" + b"secret\n" * (ci.TRACE_TAIL_BYTES // 6)
        tail, clipped, key = ci.read_trace_tail(io.BytesIO(private))
        self.assertEqual(ci.clean_trace(tail, clipped=clipped, in_key=key), "***")

    def test_excerpt_limits_and_center(self):
        trace = "\n".join(["a" * 400] * 25 + [DISK] + ["z" * 400] * 25)
        p = dict(next(p for p in PATTERNS if p["id"] == "disk-full"), context={"before": 20, "after": 20})
        result = ci.classify(job(), trace, [p])
        self.assertTrue(result["excerpt_truncated"])
        self.assertIn(DISK, result["excerpt"])
        self.assertLessEqual(len(result["excerpt"]), ci.EXCERPT_MAX_CHARS)
        self.assertTrue(result["excerpt"].startswith("…（省略）"))
        self.assertTrue(result["excerpt"].endswith("…（省略）"))
        self.assertLessEqual(max(map(len, result["excerpt"].splitlines())), ci.EXCERPT_LINE_CHARS)
        self.assertLessEqual(len(ci.excerpt(["line"] * 41, 20, p["context"])[0].splitlines()), ci.EXCERPT_MAX_LINES)
        lines = ["early error", *["progress"] * 20, "last fatal", *["cleanup"] * 20, "ERROR: Job failed"]
        snippet, _ = ci.excerpt(lines, None, {})
        self.assertIn("last fatal", snippet)
        self.assertNotIn("early error", snippet)
        self.assertEqual(ci.excerpt(["normal"] * 22, None, {})[0].splitlines(), ["normal"] * 20)


class RenderTest(unittest.TestCase):
    def test_comments(self):
        cases = {
            "single": (pipeline(), [job()], {1: PIP}, {}),
            "multi": (pipeline(), [job(), job(2, "build-arm64"), job(3, "unit", stage="test"),
                                   job(4, "deploy", skipped_trace=True)], {1: PIP, 2: PIP, 3: PYTEST}, {}),
            "yaml-errors": (pipeline(yaml_errors="jobs:build:script config should be a string"), [], {}, {}),
            "rerun": (pipeline(), [job()], {1: DISK}, {"rerun": True, "newer": True}),
        }
        for name, (record, jobs, traces, options) in cases.items():
            with self.subTest(name=name):
                response = ci.aggregate(record, jobs, traces, PATTERNS)
                body = ci.render_comment(response, **options)
                self.assertEqual(body, (FIXTURES / "comments" / (name + ".md")).read_text(encoding="utf-8"))
                self.assertEqual(ci.MARK.match(body).groups(), ("123", response["fp"]))

    def test_long_fences_and_inline_values(self):
        response = ci.aggregate(pipeline(ref="feature/`\n/close"),
                                [job(name="build`\n/assign")], {1: DISK + "\n````\n/close"}, PATTERNS)
        body = ci.render_comment(response)
        self.assertIn("`````text\n", body)
        self.assertIn("`build /assign`", body)
        self.assertIn("`feature/ /close`", body)
        self.assertNotIn("修正案", body)

    def test_body_limit(self):
        jobs = [job(i, f"build-{i}") for i in range(1, 6)]
        trace = "\n".join(["a" * 280] * 8 + ["specific hit"] + ["b" * 280] * 8)
        traces = {i: trace.replace("specific hit", f"hit-{i}") for i in range(1, 6)}
        patterns = [dict(rule(f"rule-{i}", regex=[f"hit-{i}"]), fix="修" * 600, next_action="次" * 300,
                         context=dict(before=20, after=20)) for i in range(1, 6)]
        body = ci.render_comment(ci.aggregate(pipeline(), jobs, traces, patterns))
        self.assertLessEqual(len(body), ci.BODY_MAX_CHARS)
        self.assertIn("hit-1", body)
        self.assertNotIn("hit-5", body)
        others = next(line for line in body.splitlines() if line.startswith("ほかに失敗したジョブ:"))
        self.assertIn("`build-5`（依存関係）", others)


class FakeGitLab:
    """テストごとの HTTP サーバ。パイプラインとコメントの状態を手元で変えられる。"""

    def __init__(self):
        self.pipelines = {}
        self.jobs = defaultdict(list)
        self.traces = {}
        self.mrs = {}
        self.commits = {SHA: {"id": SHA}}
        self.notes = defaultdict(list)
        self.requests = []
        self.writes = []
        self.fail_next = None
        self.pipeline_errors = {}
        self.lose_post_response = False
        self.redirect = None
        self.clock = 0
        self.lock = threading.Lock()
        world = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.route("GET")

            def do_POST(self):
                self.route("POST")

            def route(self, method):
                if self.headers.get("PRIVATE-TOKEN") != TOKEN:
                    self.send(401, {})
                    return
                url = urlsplit(self.path)
                prefix = "/api/v4/projects/"
                project, _, tail = url.path.removeprefix(prefix).partition("/")
                if not url.path.startswith(prefix) or unquote(project) not in (PROJECT, "7", "ops/hub"):
                    self.send(404, {})
                    return
                query = {key: values[0] for key, values in parse_qs(url.query).items()}
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw) if raw else None
                with world.lock:
                    world.requests.append((method, tail, query))
                    code, value, headers = world.handle(method, tail, query, body)
                self.send(code, value, **headers)

            def send(self, code, value, **headers):
                raw = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain" if isinstance(value, bytes) else "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for key, val in headers.items():
                    self.send_header(key, val)
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def put_pipeline(self, id, **fields):
        self.clock += 1
        self.pipelines[id] = pipeline(**dict(
            {"id": id, "status": "failed", "iid": id, "yaml_errors": None, "tag": False,
             "updated_at": f"2026-11-19T00:{self.clock:02d}:00Z",
             "web_url": f"{self.url}/g/p/-/pipelines/{id}"}, **fields))

    def put_job(self, pipeline, id, name, trace=None, failure_reason="script_failure", **fields):
        self.jobs[pipeline].append(job(id, name, failure_reason=failure_reason, **fields))
        self.traces[id] = trace.encode("utf-8") if isinstance(trace, str) else trace

    def put_mr(self, iid=12, **fields):
        self.mrs[iid] = dict({"iid": iid, "source_project_id": 7, "source_branch": "feature/cart",
                              "state": "opened", "sha": SHA, "web_url": f"{self.url}/g/p/-/merge_requests/{iid}"},
                             **fields)

    def retry(self, id, job_id, trace, name="build"):
        previous = dict(self.pipelines[id])
        self.clock += 1
        previous.update(updated_at=f"2026-11-19T00:{self.clock:02d}:00Z", status="failed")
        self.pipelines[id] = previous
        self.jobs[id] = []
        self.put_job(id, job_id, name, trace=trace)

    def handle(self, method, tail, query, body):
        def page(items):
            size, number = int(query.get("per_page", 100)), int(query.get("page", 1))
            start = (number - 1) * size
            end = start + size
            return 200, items[start:end], {"X-Next-Page": str(number + 1) if len(items) > end else ""}

        def item(value):
            return (200, value, {}) if value is not None else (404, {}, {})

        if self.fail_next:
            code, self.fail_next = self.fail_next, None
            return code, {"message": TOKEN}, {}
        if self.redirect:
            location, self.redirect = self.redirect, None
            return 302, {}, {"Location": location}
        parts = tail.split("/")
        if tail == "":
            return item({"id": 7})
        if tail == "pipelines":
            items = [p for p in self.pipelines.values() if p["status"] == query.get("status", p["status"])
                     and p["updated_at"] > query.get("updated_after", "")]
            return page(sorted(items, key=lambda p: p["updated_at"]))
        if parts[0] == "pipelines":
            pid = int(parts[1])
            if len(parts) == 2:
                if pid in self.pipeline_errors:
                    return self.pipeline_errors[pid], {}, {}
                return item(self.pipelines.get(pid))
            if parts[2] == "jobs" and pid in self.pipelines:
                return page(self.jobs[pid] if query.get("scope[]") == "failed" else [])
        if parts[0] == "jobs" and parts[-1] == "trace":
            return item(self.traces.get(int(parts[1])))
        if tail == "merge_requests":
            return page([mr for mr in self.mrs.values()
                         if mr["state"] == query.get("state") and mr["source_branch"] == query.get("source_branch")])
        if parts[0] == "merge_requests" and len(parts) == 2:
            return item(self.mrs.get(int(parts[1])))
        if parts[:2] == ["repository", "commits"] and len(parts) == 3:
            return item(self.commits.get(parts[2]))
        if (parts[-1] == "notes" and parts[0] in ("merge_requests", "issues")) or tail == COMMIT_COMMENTS:
            field = "note" if parts[-1] == "comments" else "body"
            if method == "GET":
                return page(list(reversed(self.notes[tail])))
            self.writes.append((tail, body[field]))
            note = {"id": len(self.writes), field: body[field]}
            self.notes[tail].append(note)
            if self.lose_post_response:
                self.lose_post_response = False
                return 503, {}, {}
            return 201, note, {}
        return 404, {}, {}


class _GitLabBase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.gl = FakeGitLab()
        self.addCleanup(self.gl.close)
        self.env = {"GITLAB_URL": self.gl.url, "GITLAB_TOKEN": TOKEN, "GITLAB_PROJECT": PROJECT,
                    "GITLAB_HUB_ISSUE_URL": "", "CI_TRIAGE_PATTERNS": "", "CI_TRIAGE_DRY_RUN": "0",
                    "CI_TRIAGE_COMMIT_SOURCES": "push", "CI_TRIAGE_WORK_DIR": str(self.tmp / "work")}
        self.directory = Path(self.env["CI_TRIAGE_WORK_DIR"]) / "123"

    def cli(self, *args):
        code, out, self.stderr = cli(*args, env=self.env)
        return code, out

    def seed(self, **fields):
        self.gl.put_pipeline(123)
        self.gl.pipelines[123].update(fields)
        self.gl.put_job(123, 1, "build", trace=PIP)

    def triage(self, *options):
        code, out = self.cli("triage", 123, *options)
        self.assertEqual(code, 0, self.stderr)
        return json.loads(out)

    def hub(self):
        self.env["GITLAB_HUB_ISSUE_URL"] = f"{self.gl.url}/ops/hub/-/issues/42"


class CollectDirectoryTest(unittest.TestCase):
    def test_refuses_paths_outside_work_root_or_the_root_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "work"
            outside = base / "work-other"
            root.mkdir()
            outside.mkdir()
            for path in (root, outside):
                (path / "keep").touch()
            env = {"GITLAB_URL": "https://gl.example", "GITLAB_TOKEN": TOKEN,
                   "GITLAB_PROJECT": PROJECT, "CI_TRIAGE_WORK_DIR": str(root)}
            for path in (outside, outside / "missing", root / ".." / outside.name, root):
                with self.subTest(path=path), mock.patch.object(ci.GitLab, "get") as get:
                    code, out, err = cli("collect", 123, "--dir", path, env=env)
                    self.assertEqual((code, out), (1, ""))
                    self.assertIn("作業ルート配下", err)
                    get.assert_not_called()
                    self.assertTrue((root / "keep").is_file())
                    self.assertTrue((outside / "keep").is_file())
                    self.assertFalse((outside / "missing").exists())

    @unittest.skipIf(os.name == "nt", "シンボリックリンクの作成には権限が必要")
    def test_refuses_path_through_symlink_outside_work_root(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, CI_TRIAGE_WORK_DIR=tmp + "/work"):
            root = ci.work_root()
            root.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "keep").touch()
            (root / "link").symlink_to(tmp, target_is_directory=True)
            gl = mock.Mock()
            with self.assertRaisesRegex(ValueError, "作業ルート配下"):
                ci.collect(gl, 123, root / "link" / "outside", ci.BUILTIN_PATTERNS)
            gl.get.assert_not_called()
            self.assertTrue((outside / "keep").is_file())


class CommandTest(_GitLabBase):
    def test_collect_masks_and_cleans_work(self):
        self.seed()
        self.gl.jobs[123] = []
        self.gl.put_job(123, 3, "quota", failure_reason="ci_quota_exceeded")  # 404
        self.gl.put_job(123, 2, "empty", trace="")
        self.gl.put_job(123, 1, "build", trace=(FIXTURES / "secrets.log").read_bytes() + PIP.encode())
        self.gl.put_job(123, 4, "optional", trace=DISK, allow_failure=True)
        stale = self.directory.parent / "22"
        stale.mkdir(parents=True)
        os.utime(stale, (0, 0))
        path = self.tmp / "site.json"
        ci.write_json(path, dict(version=1, patterns=[], mask=["INTERNAL-[A-Z0-9]{12}"]))
        self.env["CI_TRIAGE_PATTERNS"] = str(path)
        code, out = self.cli("collect", "#123")
        self.assertEqual((code, json.loads(out)), (0, dict(pipeline=123, failed_jobs=3, yaml_errors=False)))
        self.assertFalse(stale.exists())
        self.assertEqual([(method, path) for method, path, _ in self.gl.requests], [
            ("GET", "pipelines/123"), ("GET", "pipelines/123/jobs"),
            ("GET", "jobs/1/trace"), ("GET", "jobs/2/trace"), ("GET", "jobs/3/trace"),
        ])
        self.assertEqual(self.gl.requests[1][2]["scope[]"], "failed")
        self.assertEqual([j["trace"] for j in ci.read_json(self.directory / "jobs.json")], ["trace-1.log", None, None])
        self.assertEqual(self.cli("diagnose", 123)[0], 0)
        for path in self.directory.iterdir():
            data = path.read_text(encoding="utf-8")
            for secret in (TOKEN, "synthetic private key contents", "glpat-ABCDEFGHIJKLMNOPQRST0",
                           "INTERNAL-ABCDEFGHIJKL", "two words"):
                self.assertNotIn(secret, data, path.name)
        self.assertFalse(self.gl.writes)

    def test_collect_paginates_and_caps_traces(self):
        self.seed()
        self.gl.jobs[123] = []
        for id in reversed(range(1, 103)):
            self.gl.put_job(123, id, f"build-{id}", trace=DISK)
        code, out = self.cli("collect", 123)
        self.assertEqual((code, json.loads(out)["failed_jobs"]), (0, 102))
        jobs = ci.read_json(self.directory / "jobs.json")
        self.assertEqual([job["id"] for job in jobs], list(range(1, 103)))
        self.assertEqual(sum(job["skipped_trace"] for job in jobs), 82)
        self.assertEqual([path for _, path, _ in self.gl.requests if path.endswith("/trace")],
                         [f"jobs/{id}/trace" for id in range(1, 21)])

    def test_status_and_yaml_error_do_not_read_jobs(self):
        self.seed(status="running")
        code, out = self.cli("triage", 123)
        self.assertEqual((code, json.loads(out)["status"]), (0, "running"))
        self.assertEqual(len(self.gl.requests), 1)
        self.gl.pipelines[123].update(status="failed", yaml_errors=f"jobs:build must contain a script ({TOKEN})")
        code, out = self.cli("collect", 123)
        self.assertEqual((code, json.loads(out)["yaml_errors"]), (0, True))
        self.assertEqual(len(self.gl.requests), 2)
        self.assertEqual(json.loads(self.cli("diagnose", 123)[1])["category"], "configuration")
        for name in ("pipeline.json", "comment.md"):
            with self.subTest(name=name):
                data = (self.directory / name).read_text(encoding="utf-8")
                self.assertIn("jobs:build must contain a script (***)", data)
                self.assertNotIn(TOKEN, data)

    def test_mr_pipeline_refs_and_newer_commit(self):
        self.gl.put_mr(state="merged", sha="b" * 40)
        for pid, suffix in enumerate(("head", "merge", "train"), 123):
            with self.subTest(suffix=suffix):
                self.gl.put_pipeline(pid, ref=f"refs/merge-requests/12/{suffix}")
                self.gl.put_job(pid, pid, "build", trace=PIP)
                code, out = self.cli("triage", pid)
                self.assertEqual(code, 0, self.stderr)
                self.assertEqual(json.loads(out)["target"], "mr")
                self.assertIn("新しいコミット", self.gl.writes[-1][1])

    def test_branch_mr_then_commit_then_hub(self):
        self.seed()
        self.gl.put_mr()
        self.gl.put_mr(13)
        self.gl.put_mr(14, source_project_id=99)  # フォーク
        self.gl.put_mr(15, source_branch="other")
        self.assertEqual(self.triage()["ref"], "!13")
        for mr in self.gl.mrs.values():
            mr["state"] = "merged"
        self.assertEqual(self.triage()["target"], "commit")
        self.assertEqual(self.gl.writes[-1][0], COMMIT_COMMENTS)
        self.hub()
        self.gl.pipelines[123]["source"] = "schedule"
        self.assertEqual(self.triage()["url"], self.env["GITLAB_HUB_ISSUE_URL"] + "#note_3")
        self.assertIn("`g/p` のパイプライン", self.gl.writes[-1][1])
        self.assertEqual(self.gl.writes[-1][0], ISSUE_NOTES)
        self.gl.pipelines[123]["source"] = "push"
        self.gl.commits.clear()
        self.assertEqual(self.triage()["reason"], "duplicate")

    def test_tag_and_commit_sources(self):
        self.seed(tag=True, source="web")
        self.gl.put_mr()
        self.hub()
        self.env["CI_TRIAGE_COMMIT_SOURCES"] = "web, push"
        self.assertEqual(self.triage()["target"], "commit")
        self.assertFalse(any(path.startswith("merge_requests") for _, path, _ in self.gl.requests))
        self.env["CI_TRIAGE_COMMIT_SOURCES"] = ""
        self.assertEqual(self.triage()["target"], "issue")

    def test_duplicate_rerun_old_marker_and_single_post(self):
        self.seed()
        self.gl.put_mr()
        self.assertEqual(self.cli("collect", 123)[0], 0)
        fp = json.loads(self.cli("diagnose", 123)[1])["fp"]
        self.gl.notes[MR_NOTES].append({"body": f"quoted\n<!-- ci-triage-agent:diagnosis pipeline=123 fp={fp} -->"})
        self.gl.lose_post_response = True
        self.assertEqual(self.cli("post", 123)[0], 3)
        self.assertEqual(len(self.gl.writes), 1)
        code, out = self.cli("post", 123)
        self.assertEqual((code, json.loads(out)["reason"]), (0, "duplicate"))
        self.gl.retry(123, 31, DISK)
        self.assertTrue(self.triage()["posted"])
        self.assertIn("（再実行後）", self.gl.writes[-1][1])
        self.gl.notes[MR_NOTES].extend({"body": f"other {i}"} for i in range(205))
        self.gl.retry(123, 32, "new output\n" + PIP)
        self.assertEqual(self.triage()["reason"], "duplicate")
        self.assertEqual(len(self.gl.writes), 2)
        self.assertIn(("GET", MR_NOTES, {"sort": "desc", "order_by": "created_at", "per_page": "100", "page": "3"}),
                      self.gl.requests)

    def test_commit_marker_duplicate_then_rerun(self):
        self.seed()
        self.assertEqual(self.triage()["target"], "commit")
        self.assertEqual(self.triage()["reason"], "duplicate")
        self.assertEqual(len(self.gl.writes), 1)
        self.gl.retry(123, 31, DISK)
        self.assertTrue(self.triage()["posted"])
        self.assertEqual(self.gl.writes[-1][0], COMMIT_COMMENTS)
        self.assertIn("（再実行後）", self.gl.writes[-1][1])

    def test_commit_scan_applies_limit_after_sorting_all_pages(self):
        self.seed()
        self.assertEqual(self.cli("collect", 123)[0], 0)
        self.assertEqual(self.cli("diagnose", 123)[0], 0)
        marker = (self.directory / "comment.md").read_text(encoding="utf-8")
        # 偽 API は挿入順の逆で返すため、この印は最後のページに届く。
        first = {"note": marker, "created_at": "2026-11-19T03:00:00+01:00"}
        self.gl.notes[COMMIT_COMMENTS] = [first] + [
            {"note": "unrelated", "created_at": "2026-11-19T01:30:00Z"}
            for _ in range(ci.DEDUP_SCAN_NOTES)
        ]
        code, out = self.cli("post", 123)
        self.assertEqual((code, json.loads(out)["reason"]), (0, "duplicate"))
        self.assertFalse(self.gl.writes)
        first["created_at"] = "2026-11-19T03:00:00+02:00"
        code, out = self.cli("post", 123)
        self.assertEqual((code, json.loads(out)["reason"]), (0, "posted"))
        self.assertNotIn("（再実行後）", self.gl.writes[-1][1])

    def test_errors_and_cross_origin(self):
        for status, code in [(408, 3), (425, 3), (429, 3), (503, 3), (401, 1), (403, 1), (404, 1)]:
            with self.subTest(status=status):
                self.gl.fail_next = status
                self.assertEqual(self.cli("collect", 123)[0], code)
                self.assertNotIn(TOKEN, self.stderr)
        self.assertEqual(cli("collect", 123)[0], 2)
        self.seed(source="schedule")
        code, out = self.cli("triage", 123)
        self.assertEqual((code, json.loads(out)["reason"]), (1, "no_target"))
        self.env["GITLAB_HUB_ISSUE_URL"] = "https://other.example/ops/hub/-/issues/42"
        self.assertEqual(self.cli("post", 123)[0], 1)
        self.gl.redirect = "https://other.example/pipeline"
        self.assertEqual(self.cli("collect", 123)[0], 1)
        self.assertIn("リダイレクト", self.stderr)
        self.assertFalse(self.gl.writes)

    def test_change_watch_plugin(self):
        self.seed()
        self.gl.put_mr()
        result = subprocess.run(
            [sys.executable, str(CHANGE_WATCH), "--plugin", str(PLUGIN), "ci", "triage", "123"],
            env={**os.environ, **self.env}, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["posted"])


class DryRunTest(_GitLabBase):
    def test_dry_run_states_and_flags(self):
        self.seed()
        self.gl.put_mr()
        self.assertEqual(self.cli("collect", 123)[0], 0)
        fp = json.loads(self.cli("diagnose", 123)[1])["fp"]
        body = (self.directory / "comment.md").read_text(encoding="utf-8")
        for value, flags, existing, status in [
            ("0", ["--dry-run"], None, "なし"),
            ("1", [], fp, "あり"),
            ("TrUE", [], "000000000000", "別の内容であり"),
            ("", ["--dry-run"], None, "なし"),
        ]:
            self.gl.notes[MR_NOTES] = [{"body": f"<!-- ci-triage-agent:diagnosis pipeline=123 fp={existing} -->"}] if existing else []
            self.env["CI_TRIAGE_DRY_RUN"] = value
            code, out = self.cli("post", 123, *flags)
            self.assertEqual(code, 0)
            self.assertTrue(out.startswith("[dry-run] 投稿先: MR !12"))
            self.assertIn(f"既存の診断: {status}", out)
            if existing and existing != fp:
                self.assertIn("（再実行後）", out)
            else:
                self.assertTrue(out.endswith(body))
            self.assertFalse(self.gl.writes)
        self.gl.notes[MR_NOTES] = []
        self.assertTrue(json.loads(self.cli("post", 123)[1])["posted"])
        self.env["CI_TRIAGE_DRY_RUN"] = "0"
        self.gl.retry(123, 2, DISK)
        self.assertTrue(self.triage()["posted"])

    def test_rendered_preview_matches_post(self):
        self.seed(source="schedule")
        self.hub()
        self.gl.notes[ISSUE_NOTES].append({"body": "<!-- ci-triage-agent:diagnosis pipeline=123 fp=000000000000 -->"})
        code, out = self.cli("triage", 123, "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(self.gl.writes)
        self.assertIn("（再実行後）", out)
        self.assertIn("`g/p` のパイプライン", out)
        self.assertEqual(self.cli("post", 123)[0], 0)
        body = self.gl.writes[-1][1]
        self.assertTrue(out.endswith(f"[dry-run] 本文（{len(body)} 字）:\n{body}"))

    def test_no_target(self):
        self.seed(source="schedule")
        code, out = self.cli("triage", 123, "--dry-run")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("[dry-run] 投稿先: なし"))
        self.assertIn("pip-no-matching-distribution", out)
        self.assertFalse(self.gl.writes)


class FlowTest(_GitLabBase):
    def setUp(self):
        super().setUp()
        config = dagrun.load(ROOT / "change-watch.yaml.example")
        config["state_dir"] = str(self.tmp / "state")
        source = config["sources"]["gitlab-pipelines"]
        for key in ("schedule", "jitter_sec", "busy_wait_sec"):
            source.pop(key)
        source.update(failure_backoff_sec=0, retry={"attempts": 1})
        self.cfg = self.tmp / "change-watch.yaml"
        self.cfg.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        self.conn = self.tmp / "connections.json"
        ci.write_json(self.conn, {"gitlab": [{"label": "default", "url": self.gl.url, "token": TOKEN}]})
        self.paths = {
            "/home/you/.config/change-watch/gitlab-ci-triage-agent/change-watch.yaml": str(self.cfg),
            "/home/you/.github/connections.yaml": str(self.conn),
        }
        self.dag = ROOT / "change-watch-ci-triage.yaml.example"
        dagrun.check_shape(self, dagrun.load(self.dag), self.dag.name)

    def run_dag(self, path=None):
        return dagrun.run(path or self.dag, aw=[sys.executable, str(CHANGE_WATCH)], head=[],
                          env=self.env, paths=self.paths)

    def test_default_example(self):
        self.gl.put_mr()
        self.gl.put_pipeline(122)
        self.gl.put_job(122, 122, "build", trace=DISK)
        idle = dict(get=0, collect=None, diagnose=None, post=None, commit=None)
        handled = dict.fromkeys(idle, 0)
        self.assertEqual(self.run_dag(), idle)
        self.seed()
        self.assertEqual(self.run_dag(), handled)
        self.assertEqual(len(self.gl.writes), 1)
        self.assertEqual(self.run_dag(), idle)
        self.gl.retry(123, 3, "different trace lines\n" + PIP)
        self.assertEqual(self.run_dag(), handled)
        self.assertEqual(len(self.gl.writes), 1)
        self.gl.retry(123, 4, DISK)
        self.assertEqual(self.run_dag(), handled)
        self.assertEqual(len(self.gl.writes), 2)
        self.assertIn("（再実行後）", self.gl.writes[-1][1])

    def test_replay_before_commit(self):
        self.gl.put_mr()
        self.assertEqual(self.run_dag()["get"], 0)
        self.seed()
        # post が成功して commit に届く前に止まった起動を再現する。
        document = dagrun.load(self.dag)
        self.assertEqual(document["steps"].pop()["name"], "commit")
        interrupted = self.tmp / "before-commit.yaml"
        interrupted.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
        self.assertEqual(self.run_dag(interrupted)["post"], 0)
        self.assertEqual(self.run_dag()["commit"], 0)
        self.assertEqual(len(self.gl.writes), 1)

    def test_failure_commits_and_example_env_is_dry_run(self):
        self.gl.put_mr()
        self.assertIsNone(self.run_dag()["commit"])
        self.seed()
        self.gl.pipeline_errors[123] = 403
        result = self.run_dag()
        self.assertEqual(result, dict(get=0, collect=1, diagnose=None, post=None, commit=0))
        self.gl.pipeline_errors.clear()
        self.assertIsNone(self.run_dag()["collect"])
        self.gl.retry(123, 4, DISK)
        env_example = (ROOT / "gitlab-ci-triage-agent.env.example").read_text(encoding="utf-8")
        self.env["CI_TRIAGE_DRY_RUN"] = next(line.partition("=")[2] for line in env_example.splitlines()
                                             if line.startswith("CI_TRIAGE_DRY_RUN="))
        self.assertEqual(self.run_dag()["post"], 0)
        self.assertFalse(self.gl.writes)
        self.env["CI_TRIAGE_DRY_RUN"] = "0"
        self.assertIsNone(self.run_dag()["post"])

    def test_maintenance(self):
        path = ROOT / "change-watch-gitlab-ci-triage-agent-maintenance.yaml.example"
        dagrun.check_shape(self, dagrun.load(path), path.name)
        self.assertEqual(self.run_dag(path), {"gc": 0})


if __name__ == "__main__":
    unittest.main()
