import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


SOURCE = Path(__file__).resolve().parents[1] / "factory.py"
SPEC = importlib.util.spec_from_file_location("factory", SOURCE)
factory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = factory
SPEC.loader.exec_module(factory)


class FakeGitLab:
    def __init__(self):
        self.issues = {}
        self.mrs = {}
        self.issue_comments = []
        self.mr_comments = []
        self.checks = "pending"
        self.create_calls = []
        self.mutate_diff = False

    def add_issue(self, number, progress=None, repository=None):
        self.issues[number] = {
            "repository": repository,
            "number": number, "title": "タイトル", "body": "要件 {{BRANCH}}",
            "labels": [{"name": label} for label in ("factory", progress) if label],
            "updatedAt": "2026-11-26T00:00:00Z",
        }

    def list_factory_issues(self, repo, label):
        return [issue for issue in self.issues.values()
                if issue["repository"] in (None, repo)]

    def set_progress_label(self, repo, number, progress):
        labels = factory.label_names(self.issues[number]).difference(factory.PROGRESS)
        if progress:
            labels.add(progress)
        self.issues[number]["labels"] = [{"name": label} for label in labels]

    def comment_issue(self, repo, number, body):
        self.issue_comments.append((number, body))

    def comment_mr(self, repo, number, body):
        self.mr_comments.append((number, body))

    def mr_comment_exists(self, repo, number, marker):
        return any(mr == number and marker in body for mr, body in self.mr_comments)

    def find_mr_by_head(self, repo, branch):
        mr = self.mrs.get(branch)
        return mr if mr and mr["state"] == "OPEN" else None

    def add_mr(self, branch):
        mr = {"number": len(self.mrs) + 42, "url": "https://example.test/-/merge_requests/42",
              "state": "OPEN", "isDraft": True, "headRefOid": "sha-one"}
        self.mrs[branch] = mr
        return mr

    def mr_head_sha(self, repo, number, fresh=False):
        return next(mr["headRefOid"] for mr in self.mrs.values() if mr["number"] == number)

    def mr_checks_state(self, repo, number, fresh=False):
        return self.checks

    def mr_diff(self, repo, number):
        if self.mutate_diff:
            next(mr for mr in self.mrs.values() if mr["number"] == number)["headRefOid"] = "sha-two"
        return "diff --git a/example b/example\n"

    def create_draft_mr(self, repo, head, title, body, label):
        self.create_calls.append((head, body, label))
        return self.add_mr(head)["number"]

    def default_branch(self, repo):
        return "main"


class NoCommands(factory.Runner):
    def run(self, argv, cwd=None):
        raise AssertionError(f"Unexpected subprocess: {argv[0]}")


class FakeGoalie(factory.GoalieClient):
    # Use the real file protocol against the temporary inbox; replace job submission.
    def __init__(self):
        super().__init__(NoCommands(), Path("unused"))
        self.calls = []
        self.usage_error = False

    def submit(self, work_dir, prompt_file, name):
        self.calls.append((work_dir, prompt_file, name))
        if self.usage_error:
            raise factory.GoalieUsageError("invalid goalie setting")
        job_id = f"job-{len(self.calls)}"
        self.write(job_id, "pending", "prompt")
        return job_id

    def path(self, job_id, state):
        work_dir = self.calls[int(job_id.split("-")[1]) - 1][0]
        return work_dir / ".copilot-jobs" / state / f"{job_id}.md"

    def write(self, job_id, state, body):
        path = self.path(job_id, state)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def finish(self, job_id, body, status="ok"):
        self.path(job_id, "pending").unlink()
        self.write(job_id, "results", f"status: {status}\n\n{body}")


class FakeGit:
    def __init__(self):
        self.pushes = []
        self.has_branch = True
        self.ahead = 1
        self.dirty = False
        self.verification_calls = []

    def fetch(self, work_dir):
        self.verification_calls.append(("fetch", work_dir))

    def branch_exists(self, work_dir, branch):
        self.verification_calls.append(("branch_exists", work_dir, branch))
        return self.has_branch

    def commits_ahead(self, work_dir, base, branch):
        self.verification_calls.append(("commits_ahead", work_dir, base, branch))
        return self.ahead

    def tracked_dirty(self, work_dir):
        return self.dirty

    def push(self, work_dir, branch):
        self.pushes.append(branch)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # Windows の一時フォルダは短い名前（RUNNER~1）で渡ることがある。作業フォルダは resolve() 済みなので揃える。
        self.root = Path(directory.name).resolve()
        self.values = json.loads((SOURCE.parent / "factory.example.json").read_text(encoding="utf-8"))
        self.values.update(work_root=str(self.root), journal=str(self.root / "journal.jsonl"),
                           job_timeout_sec=60, max_concurrent=2)
        self.now = 1000
        self.gitlab = FakeGitLab()
        self.goalie = FakeGoalie()
        self.git = FakeGit()
        self.config_path = self.root / "factory.json"
        self.gitlab.add_issue(1)

    def controller(self):
        self.config_path.write_text(json.dumps(self.values), encoding="utf-8")
        return factory.Controller(factory.Config.load(self.config_path),
                                  self.gitlab, self.goalie, self.git, lambda: self.now)

    def poll(self):
        controller = self.controller()
        controller.poll()
        return controller.journal

    def assert_progress(self, progress, number=1):
        self.assertEqual(factory.label_names(self.gitlab.issues[number]),
                         {"factory", progress} if progress else {"factory"})

    def open_mr(self):
        self.poll()
        self.goalie.finish("job-1", "BRANCH: factory/issue-1")
        self.poll()
        return self.gitlab.mrs["factory/issue-1"]

    def test_round_trip(self):
        self.poll()
        self.assert_progress("factory:implementing")
        self.assertEqual(self.goalie.calls[0][2], "factory-impl-1")
        self.assertIn("要件 {{BRANCH}}", self.goalie.calls[0][1].read_text(encoding="utf-8"))
        self.goalie.finish("job-1", "BRANCH: factory/issue-1")
        self.poll()
        self.assert_progress("factory:mr-open")
        self.assertEqual(self.git.pushes, ["factory/issue-1"])
        self.assertEqual(self.gitlab.create_calls, [
            ("factory/issue-1", "Closes #1\n\n<!-- factory:issue=1 -->\n", "factory"),
        ])
        self.gitlab.checks = "success"
        self.poll()
        self.assert_progress("factory:in-review")
        work_dir, prompt, name = self.goalie.calls[1]
        self.assertEqual(name, "factory-review-42")
        diff = prompt.with_name(prompt.stem + "-diff.patch")
        self.assertIn(diff.relative_to(work_dir).as_posix(), prompt.read_text(encoding="utf-8"))
        self.goalie.finish("job-2", "FACTORY-VERDICT: clear\n指摘はありません")
        journal = self.poll()
        self.assert_progress("factory:reviewed")
        self.assertEqual(len(self.gitlab.mr_comments), 1)
        self.assertIn("指摘はありません", self.gitlab.mr_comments[0][1])
        self.assertFalse(journal.unresolved())
        self.assertEqual(journal.last_sha(self.values["repositories"][0], 1), "sha-one")
        self.assertNotIn("指摘はありません", journal.path.read_text(encoding="utf-8"))
        comments = self.gitlab.mr_comments.copy()
        self.poll()
        self.assert_progress("factory:reviewed")
        self.assertEqual([name for _, _, name in self.goalie.calls],
                         ["factory-impl-1", "factory-review-42"])
        self.assertEqual(self.gitlab.mr_comments, comments)

    def test_in_review_does_not_submit_another_review(self):
        self.open_mr()
        self.gitlab.checks = "success"
        self.poll()
        for _ in range(2):
            self.assert_progress("factory:in-review")
            journal = self.poll()
            self.assertEqual([name for _, _, name in self.goalie.calls],
                             ["factory-impl-1", "factory-review-42"])
            self.assertEqual([run["job_id"] for run in journal.unresolved()], ["job-2"])
        self.assert_progress("factory:in-review")

    def test_pending_is_idempotent_and_work_dir_has_one_slot(self):
        self.values["max_concurrent"] = 5
        self.gitlab.add_issue(2)
        self.poll()
        journal = self.poll()
        self.assertEqual(len(self.goalie.calls), 1)
        self.assertEqual(journal.unresolved()[0]["job_id"], "job-1")
        self.assert_progress(None, 2)

    def test_two_repositories_submit_implementations_in_one_poll(self):
        first_repo, second_repo = "group/first", "group/second"
        self.values.update(repositories=[first_repo, second_repo], max_concurrent=2)
        self.gitlab.add_issue(1, repository=first_repo)
        self.gitlab.add_issue(2, repository=second_repo)
        journal = self.poll()
        self.assertCountEqual([(work_dir, name) for work_dir, _, name in self.goalie.calls], [
            (self.root / "first", "factory-impl-1"),
            (self.root / "second", "factory-impl-2"),
        ])
        self.assertCountEqual([(run["repository"], run["issue"]) for run in journal.unresolved()],
                              [(first_repo, 1), (second_repo, 2)])
        self.assert_progress("factory:implementing", 1)
        self.assert_progress("factory:implementing", 2)

    def test_withdraw_and_retry_on_next_cycle(self):
        self.poll()
        self.now += 61
        journal = self.poll()
        withdrawn = self.goalie.path("job-1", "tmp").with_name("job-1.withdrawn.md")
        self.assertTrue(withdrawn.is_file())
        self.assertFalse(self.goalie.path("job-1", "pending").exists())
        self.assertEqual(len(self.goalie.calls), 1)
        self.assert_progress(None)
        self.assertIn(3, [event["exit_code"] for event in journal.events if event["event"] == "job_finished"])
        self.poll()
        self.assertEqual(len(self.goalie.calls), 2)
        self.assert_progress("factory:implementing")

    def test_running_timeout_leaves_job_and_blocks_retry(self):
        mr = self.open_mr()
        self.gitlab.checks = "success"
        self.poll()
        self.goalie.path("job-2", "pending").unlink()
        self.goalie.write("job-2", "dispatched", "request")
        self.now += 61
        journal = self.poll()
        self.assert_progress("factory:failed")
        self.assertTrue(self.goalie.path("job-2", "dispatched").is_file())
        self.assertIn(4, [event["exit_code"] for event in journal.events if event["event"] == "job_finished"])
        mr["headRefOid"] = "sha-two"
        self.poll()
        self.assert_progress("factory:failed")
        self.assertEqual(len(self.goalie.calls), 2)
        self.goalie.path("job-2", "dispatched").unlink()
        self.poll()
        self.assert_progress("factory:mr-open")
        self.assertEqual(len(self.goalie.calls), 2)
        journal = self.poll()
        self.assert_progress("factory:in-review")
        self.assertEqual([name for _, _, name in self.goalie.calls],
                         ["factory-impl-1", "factory-review-42", "factory-review-42"])
        self.assertEqual([(run["job_id"], run["input_revision"]) for run in journal.unresolved()],
                         [("job-3", "sha-two")])

    def test_running_implementation_times_out_without_retry(self):
        self.poll()
        self.goalie.path("job-1", "pending").unlink()
        self.goalie.write("job-1", "running", "request")
        self.now += self.values["job_timeout_sec"] - 1
        self.poll()
        self.assert_progress("factory:implementing")
        self.now += 2
        journal = self.poll()
        self.assert_progress("factory:failed")
        self.assertTrue(self.goalie.path("job-1", "running").is_file())
        self.assertEqual([event["exit_code"] for event in journal.events
                          if event["event"] == "job_finished"], [4])
        self.poll()
        self.assert_progress("factory:failed")
        self.assertEqual([name for _, _, name in self.goalie.calls], ["factory-impl-1"])
        self.assertFalse(self.gitlab.create_calls)

    def test_ci_pending_failure_deduplication_and_new_head(self):
        mr = self.open_mr()
        self.poll()
        self.assert_progress("factory:mr-open")
        self.gitlab.checks = "failure"
        self.poll()
        self.assert_progress("factory:failed")
        self.assertEqual(len(self.goalie.calls), 1)
        self.gitlab.set_progress_label("", 1, "factory:mr-open")
        self.poll()
        self.assertEqual(len(self.gitlab.mr_comments), 1)
        mr["headRefOid"] = "sha-two"
        self.poll()
        self.assert_progress("factory:mr-open")
        self.poll()
        self.assertEqual(len(self.gitlab.mr_comments), 2)
        self.assert_progress("factory:failed")

    def test_ci_success_on_same_head_reopens_failed_issue(self):
        mr = self.open_mr()
        self.gitlab.checks = "failure"
        self.poll()
        self.assert_progress("factory:failed")
        self.gitlab.checks = "pending"
        self.poll()
        self.assert_progress("factory:failed")
        self.gitlab.checks = "success"
        journal = self.poll()
        self.assert_progress("factory:mr-open")
        passed = [event for event in journal.events if event["event"] == "ci_passed"]
        self.assertEqual([(event["mr"], event["input_revision"]) for event in passed],
                         [(mr["number"], mr["headRefOid"])])
        self.assertEqual(len(self.goalie.calls), 1)

    def test_major_review_after_ci_failure_keeps_failed_label(self):
        self.open_mr()
        self.gitlab.checks = "failure"
        self.poll()
        self.gitlab.set_progress_label("", 1, "factory:mr-open")
        self.gitlab.checks = "success"
        self.poll()
        self.goalie.finish("job-2", "FACTORY-VERDICT: major")
        self.poll()
        journal = self.poll()
        self.assert_progress("factory:failed")
        self.assertFalse(any(event["event"] == "ci_passed" for event in journal.events))

    def test_existing_review_comment_is_not_reposted(self):
        mr = self.open_mr()
        self.gitlab.checks = "success"
        run = self.poll().unresolved()[0]
        body = "FACTORY-VERDICT: clear\n指摘はありません"
        comment = factory.format_review_comment(run["run_id"], run["input_revision"], body)
        self.gitlab.comment_mr(run["repository"], mr["number"], comment)
        self.goalie.finish("job-2", body)
        journal = self.poll()
        self.assert_progress("factory:reviewed")
        self.assertEqual(self.gitlab.mr_comments, [(mr["number"], comment)])
        posted = [event for event in journal.events if event["event"] == "review_posted"]
        self.assertEqual([(event["run_id"], event["verdict"]) for event in posted],
                         [(run["run_id"], "clear")])
        self.assertFalse(journal.unresolved())

    def test_existing_mr_is_adopted_without_implementation(self):
        self.assert_existing_mr_adopted(None)

    def test_existing_mr_is_adopted_while_implementing(self):
        self.assert_existing_mr_adopted("factory:implementing")

    def assert_existing_mr_adopted(self, progress):
        mr = self.gitlab.add_mr("factory/issue-1")
        self.gitlab.set_progress_label("", 1, progress)
        journal = self.poll()
        self.assert_progress("factory:mr-open")
        self.assertFalse(self.goalie.calls)
        self.assertFalse(self.gitlab.create_calls)
        created = [event for event in journal.events if event["event"] == "mr_created"]
        self.assertEqual([(event["mr"], event["input_revision"]) for event in created],
                         [(mr["number"], mr["headRefOid"])])

    def test_null_issue_title_and_body_are_submitted_as_empty(self):
        self.gitlab.issues[1].update(title=None, body=None)
        self.poll()
        self.assert_progress("factory:implementing")
        self.assertEqual(len(self.goalie.calls), 1)
        prompt = self.goalie.calls[0][1].read_text(encoding="utf-8")
        self.assertNotIn("None", prompt)
        self.assertNotIn("{{ISSUE_", prompt)

    def test_implementation_fetches_before_comparing_to_origin_default_branch(self):
        self.open_mr()
        work_dir = self.goalie.calls[0][0]
        self.assertEqual(self.git.verification_calls, [
            ("fetch", work_dir),
            ("branch_exists", work_dir, "factory/issue-1"),
            ("commits_ahead", work_dir, "origin/main", "factory/issue-1"),
        ])

    def test_implementation_reuses_open_mr_without_push(self):
        self.poll()
        mr = self.gitlab.add_mr("factory/issue-1")
        self.goalie.finish("job-1", "BRANCH: factory/issue-1")
        journal = self.poll()
        self.assert_progress("factory:mr-open")
        self.assertEqual(self.git.pushes, [])
        self.assertEqual(self.gitlab.create_calls, [])
        self.assertEqual([event["mr"] for event in journal.events if event["event"] == "mr_created"],
                         [mr["number"]])

    def test_implementation_pushes_and_creates_new_mr_when_existing_mr_is_closed(self):
        self.poll()
        closed = self.gitlab.add_mr("factory/issue-1")
        closed["state"] = "CLOSED"
        self.goalie.finish("job-1", "BRANCH: factory/issue-1")
        journal = self.poll()
        self.assert_progress("factory:mr-open")
        self.assertEqual(self.git.pushes, ["factory/issue-1"])
        self.assertEqual([call[0] for call in self.gitlab.create_calls], ["factory/issue-1"])
        new_mr = self.gitlab.mrs["factory/issue-1"]
        self.assertNotEqual(new_mr["number"], closed["number"])
        self.assertEqual([event["mr"] for event in journal.events if event["event"] == "mr_created"],
                         [new_mr["number"]])

    def test_implementation_fails_when_branch_is_missing(self):
        self.git.has_branch = False
        self.assert_implementation_verification_failed("が見つかりません")

    def test_implementation_fails_when_branch_has_no_commits_ahead(self):
        self.git.ahead = 0
        self.assert_implementation_verification_failed("既定ブランチより先のコミットがありません")

    def test_implementation_fails_when_tracked_files_are_dirty(self):
        self.git.dirty = True
        self.assert_implementation_verification_failed("追跡対象のファイルに未コミットの変更があります")

    def assert_implementation_verification_failed(self, message):
        self.poll()
        self.assert_progress("factory:implementing")
        self.goalie.finish("job-1", "BRANCH: factory/issue-1")
        journal = self.poll()
        self.assert_progress("factory:failed")
        self.assertFalse(self.gitlab.create_calls)
        self.assertFalse(self.git.pushes)
        self.assertFalse(journal.unresolved())
        self.assertEqual(len(self.gitlab.issue_comments), 1)
        number, comment = self.gitlab.issue_comments[0]
        self.assertEqual(number, 1)
        self.assertIn(message, comment)

    def test_hold_until_config_changes(self):
        self.gitlab.add_issue(2)
        self.goalie.usage_error = True
        self.poll()
        self.assertEqual(len(self.goalie.calls), 1)
        self.assertEqual(len(self.gitlab.issue_comments), 1)
        self.goalie.usage_error = False
        self.poll()
        self.assertEqual(len(self.goalie.calls), 1)
        self.values["poll_interval_sec"] = 60
        self.poll()
        self.assertEqual(len(self.goalie.calls), 2)

    def test_review_does_not_use_moving_diff_or_stale_result(self):
        mr = self.open_mr()
        self.gitlab.checks = "success"
        self.gitlab.mutate_diff = True
        self.poll()
        self.assertEqual(len(self.goalie.calls), 1)
        self.gitlab.mutate_diff = False
        self.poll()
        self.goalie.finish("job-2", "FACTORY-VERDICT: major")
        mr["headRefOid"] = "sha-three"
        journal = self.poll()
        self.assert_progress("factory:mr-open")
        self.assertFalse(self.gitlab.mr_comments)
        self.assertIn("stale_revision", [event["reason"] for event in journal.events])

    def test_review_waits_for_checks_again_before_posting(self):
        self.open_mr()
        self.gitlab.checks = "success"
        self.poll()
        self.goalie.finish("job-2", "FACTORY-VERDICT: major\n重要な指摘")
        self.gitlab.checks = "pending"
        self.assertEqual(len(self.poll().unresolved()), 1)
        self.assertFalse(self.gitlab.mr_comments)
        self.gitlab.checks = "success"
        self.poll()
        self.assert_progress("factory:failed")
        self.assertIn("重要な指摘", self.gitlab.mr_comments[0][1])

    def test_status_is_read_only_and_reports_pending_run(self):
        journal = self.poll()
        before = journal.path.read_bytes()
        output = io.StringIO()
        controller = self.controller()
        with contextlib.redirect_stdout(output):
            controller.status()
        self.assertEqual(journal.path.read_bytes(), before)
        self.assertIn("未解決の実行: 1", output.getvalue())
        self.assertIn("job=job-1", output.getvalue())
        self.assertIn("保留: なし", output.getvalue())

    def test_fresh_lock_skips_cycle_and_stale_lock_is_reclaimed(self):
        lock = self.root / "factory.lock"
        lock.write_text("other poll", encoding="utf-8")
        os.utime(lock, (self.now, self.now))
        with contextlib.redirect_stdout(io.StringIO()):
            self.poll()
        self.assertTrue(lock.exists())
        self.assertFalse(self.goalie.calls)
        self.now += 3601
        self.poll()
        self.assertFalse(lock.exists())
        self.assertEqual(len(self.goalie.calls), 1)

    def test_interrupted_submission_is_not_retried(self):
        journal = self.poll()
        self.goalie.path("job-1", "pending").unlink()
        self.remove_submission_record(journal)
        journal = self.poll()
        self.assert_progress("factory:failed")
        self.assertEqual(len(self.gitlab.issue_comments), 1)
        self.assertIn("interrupted_before_submit", [event["reason"] for event in journal.events])

    def remove_submission_record(self, journal):
        journal.path.write_text(
            "".join(json.dumps(event) + "\n" for event in journal.events
                    if event["event"] != "job_submitted"), encoding="utf-8",
        )

    def test_interrupted_submission_adopts_orphan_job_by_name(self):
        journal = self.poll()
        self.remove_submission_record(journal)
        self.goalie.finish("job-1", "BRANCH: factory/issue-1")
        result = self.goalie.path("job-1", "results")
        job_id = "job-1-factory-impl-1"
        orphan = result.rename(result.with_name(f"{job_id}.md"))
        os.utime(orphan, (self.now - 5, self.now - 5))
        journal = self.poll()
        self.assert_progress("factory:mr-open")
        self.assertFalse(journal.unresolved())
        self.assertEqual(len(self.goalie.calls), 1)
        submitted = [event for event in journal.events if event["event"] == "job_submitted"]
        self.assertEqual([(event["job_id"], event["submitted_at"]) for event in submitted],
                         [(job_id, self.now - 5)])

    def test_partial_result_waits_and_missing_job_is_not_retried(self):
        self.poll()
        self.goalie.path("job-1", "pending").unlink()
        self.goalie.write("job-1", "results", "stat")
        self.now += 59
        self.assertEqual(len(self.poll().unresolved()), 1)
        self.now += 1
        journal = self.poll()
        self.assertFalse(journal.unresolved())
        self.assertEqual([event["reason"] for event in journal.events
                          if event["event"] == "job_finished"], ["missing"])
        self.assert_progress("factory:failed")
        self.poll()
        self.assertEqual(len(self.goalie.calls), 1)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, path, params=None, json_body=None):
        self.calls.append((method, path, params, json_body))
        return next(self.responses)


class ClientTests(unittest.TestCase):
    def test_urllib_transport_builds_authenticated_json_request(self):
        transport = factory.UrllibTransport("https://gitlab.example.test/", "dummy-token")
        with patch.object(transport.opener, "open", return_value=io.BytesIO(b'{"iid": 42}')) as open_request:
            result = transport.request("POST", "/projects/group%2Frepo/merge_requests",
                                       params={"source_branch": "test/branch"}, json_body={"title": "題名"})
        request = open_request.call_args.args[0]
        self.assertEqual(result, {"iid": 42})
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url,
                         "https://gitlab.example.test/api/v4/projects/group%2Frepo/merge_requests"
                         "?source_branch=test%2Fbranch")
        self.assertEqual(request.get_header("Private-token"), "dummy-token")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), {"title": "題名"})

    def test_urllib_transport_hides_http_error_body_and_reason(self):
        transport = factory.UrllibTransport("https://gitlab.example.test", "dummy-token")
        error = HTTPError("https://gitlab.example.test", 401, "dummy-token", None,
                          io.BytesIO(b"dummy-token"))
        with patch.object(transport.opener, "open", side_effect=error):
            with self.assertRaisesRegex(factory.GitLabError, "HTTP 401") as raised:
                transport.request("GET", "/projects/group%2Frepo")
        self.assertNotIn("dummy-token", str(raised.exception))

    def test_mr_comment_exists_matches_marker_in_body(self):
        marker = "<!-- factory:review run=run sha=abc -->"
        notes = [{"body": None}, {"body": "other"}, {"body": "prefix " + marker + "\ntext"}]
        transport = FakeTransport(notes, notes)
        gitlab = factory.GitLabClient(transport)
        self.assertTrue(gitlab.mr_comment_exists("group/repo", 42, marker))
        self.assertFalse(gitlab.mr_comment_exists("group/repo", 42, marker.replace("abc", "def")))
        self.assertEqual(transport.calls, [
            ("GET", "/projects/group%2Frepo/merge_requests/42/notes", {"per_page": 100, "page": 1}, None),
        ] * 2)

    def test_mr_comment_exists_finds_marker_on_second_page(self):
        marker = "<!-- factory:review run=run sha=abc -->"
        transport = FakeTransport([{"body": None}] * 100, [{"body": marker}])
        gitlab = factory.GitLabClient(transport)
        self.assertTrue(gitlab.mr_comment_exists("group/repo", 42, marker))
        self.assertEqual(transport.calls, [
            ("GET", "/projects/group%2Frepo/merge_requests/42/notes", {"per_page": 100, "page": page}, None)
            for page in (1, 2)
        ])

    def test_list_pagination_stops_after_fifty_full_pages(self):
        transport = FakeTransport(*([[{"body": None}] * 100] * 50))
        gitlab = factory.GitLabClient(transport)
        self.assertFalse(gitlab.mr_comment_exists("group/repo", 42, "marker"))
        self.assertEqual([params["page"] for _, _, params, _ in transport.calls], list(range(1, 51)))

    def test_runner_excludes_gitlab_tokens_from_child_environment(self):
        argv = ["git", "status"]
        env = {"GITLAB_TOKEN": "primary", "GL_TOKEN": "fallback", "PATH": "/test/bin"}
        result = subprocess.CompletedProcess(argv, 0, stdout="output", stderr="")
        with patch.dict(os.environ, env, clear=True), patch("factory.subprocess.run", return_value=result) as run:
            self.assertEqual(factory.Runner().run(argv), (0, "output", ""))
            self.assertEqual(dict(os.environ), env)
        self.assertEqual(run.call_args.kwargs["env"], {
            "PATH": "/test/bin", "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0",
        })

    def test_find_job_by_name_prefers_inbox_state_order(self):
        goalie = factory.GoalieClient(NoCommands(), Path("unused"))
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            paths = []
            for index, state in enumerate(("pending", "running", "dispatched", "results", "done")):
                path = work_dir / ".copilot-jobs" / state / f"job-{index}-factory-impl-1.md"
                path.parent.mkdir(parents=True)
                path.touch()
                os.utime(path, (1000 + index, 1000 + index))
                paths.append(path)
            paths[0].with_name("job-factory-impl-10.md").touch()
            for index, path in enumerate(paths):
                self.assertEqual(goalie.find_job_by_name(work_dir, "factory-impl-1"),
                                 (path.stem, 1000 + index))
                path.unlink()
            self.assertIsNone(goalie.find_job_by_name(work_dir, "factory-impl-1"))

    def test_list_factory_issues_normalizes_response_and_encodes_project(self):
        transport = FakeTransport([
            {"iid": 7, "title": "要件", "description": None, "labels": ["factory", "extra"],
             "updated_at": "2026-11-26T00:00:00Z"},
        ])
        gitlab = factory.GitLabClient(transport)
        self.assertEqual(gitlab.list_factory_issues("group/sub/project", "factory"), [
            {"number": 7, "title": "要件", "body": "", "labels": [{"name": "factory"}, {"name": "extra"}],
             "updatedAt": "2026-11-26T00:00:00Z"},
        ])
        self.assertEqual(transport.calls, [
            ("GET", "/projects/group%2Fsub%2Fproject/issues",
             {"labels": "factory", "state": "opened", "per_page": 100, "page": 1}, None),
        ])

    def test_progress_label_replacement_preserves_other_labels(self):
        path = "/projects/group%2Frepo/issues/7"
        transport = FakeTransport(
            {"labels": ["factory", "factory:implementing", "extra"]}, {},
            {"labels": ["factory", "extra", "factory:mr-open"]}, {},
            {"labels": ["factory", "extra"]},
        )
        gitlab = factory.GitLabClient(transport)
        gitlab.set_progress_label("group/repo", 7, "factory:mr-open")
        gitlab.set_progress_label("group/repo", 7, None)
        gitlab.set_progress_label("group/repo", 7, None)
        self.assertEqual(transport.calls, [
            ("GET", path, None, None),
            ("PUT", path, None, {"labels": "factory,extra,factory:mr-open"}),
            ("GET", path, None, None),
            ("PUT", path, None, {"labels": "factory,extra"}),
            ("GET", path, None, None),
        ])

    def test_head_pipeline_status(self):
        for status, expected in (
            ("success", "success"),
            ("skipped", "success"),
            ("failed", "failure"),
            ("canceled", "failure"),
            ("running", "pending"),
            ("manual", "pending"),
            (None, "pending"),
        ):
            with self.subTest(status=status):
                pipeline = {"status": status} if status is not None else None
                transport = FakeTransport({"head_pipeline": pipeline})
                gitlab = factory.GitLabClient(transport)
                self.assertEqual(gitlab.mr_checks_state("group/repo", 1), expected)
                self.assertEqual(transport.calls, [
                    ("GET", "/projects/group%2Frepo/merge_requests/1", None, None),
                ])

    def test_create_draft_mr_uses_default_branch_and_draft_prefix(self):
        for title in ("題名", "Draft: 題名"):
            with self.subTest(title=title):
                transport = FakeTransport({"default_branch": "main"}, {"iid": 42})
                gitlab = factory.GitLabClient(transport)
                self.assertEqual(gitlab.create_draft_mr("group/repo", "feature", title, "本文", "factory"), 42)
                self.assertEqual(transport.calls, [
                    ("GET", "/projects/group%2Frepo", None, None),
                    ("POST", "/projects/group%2Frepo/merge_requests", None,
                     {"source_branch": "feature", "target_branch": "main", "title": "Draft: 題名",
                      "description": "本文", "labels": "factory"}),
                ])

    def test_find_mr_normalizes_first_open_match_and_draft_fields(self):
        for draft_fields, expected in (
            ({"draft": True}, True),
            ({"draft": False, "work_in_progress": True}, False),
            ({"work_in_progress": True}, True),
            ({}, False),
        ):
            with self.subTest(draft_fields=draft_fields):
                url = "https://example.test/group/repo/-/merge_requests/42"
                mr = {"iid": 42, "web_url": url, "sha": "abc", "state": "opened", **draft_fields}
                closed = {**mr, "iid": 41, "state": "closed"}
                transport = FakeTransport([closed, mr, {**mr, "iid": 43}], [closed])
                gitlab = factory.GitLabClient(transport)
                self.assertEqual(gitlab.find_mr_by_head("group/repo", "feature"),
                                 {"number": 42, "url": url, "headRefOid": "abc",
                                  "state": "OPEN", "isDraft": expected})
                self.assertIsNone(gitlab.find_mr_by_head("group/repo", "feature"))
                self.assertEqual(transport.calls, [
                    ("GET", "/projects/group%2Frepo/merge_requests",
                     {"source_branch": "feature", "state": "opened", "per_page": 100, "page": 1}, None),
                ] * 2)

    def test_mr_diff_includes_header_for_each_change(self):
        transport = FakeTransport({"changes": [
            {"old_path": "old.txt", "new_path": "new.txt", "diff": "@@ -1 +1 @@\n-old\n+new"},
            {"old_path": "rename.txt", "new_path": "renamed.txt", "diff": ""},
        ]})
        gitlab = factory.GitLabClient(transport)
        self.assertEqual(gitlab.mr_diff("group/repo", 42),
                         "diff --git a/old.txt b/new.txt\n@@ -1 +1 @@\n-old\n+new\n"
                         "diff --git a/rename.txt b/renamed.txt\n")
        self.assertEqual(transport.calls, [
            ("GET", "/projects/group%2Frepo/merge_requests/42/changes", None, None),
        ])

    def test_mr_diff_rejects_incomplete_changes(self):
        for response in ({"overflow": True}, {"overflow": False, "changes": [{"too_large": True}]}):
            with self.subTest(response=response):
                gitlab = factory.GitLabClient(FakeTransport(response))
                with self.assertRaisesRegex(factory.GitLabError, "^差分が大きすぎて取得できません$"):
                    gitlab.mr_diff("group/repo", 42)


class CliAndFormattingTests(unittest.TestCase):
    def test_config_rejects_invalid_limits_and_empty_repositories(self):
        values = json.loads((SOURCE.parent / "factory.example.json").read_text(encoding="utf-8"))
        for key, value in (
            ("job_timeout_sec", 0),
            ("job_timeout_sec", -1),
            ("poll_interval_sec", 0),
            ("poll_interval_sec", -1),
            ("max_concurrent", 0),
            ("max_concurrent", 1.5),
            ("repositories", []),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(factory.ConfigError, key):
                    factory.Config({**values, key: value})

    def test_config_accepts_nested_paths_and_rejects_invalid_segments_or_duplicate_names(self):
        values = json.loads((SOURCE.parent / "factory.example.json").read_text(encoding="utf-8"))
        config = factory.Config({**values, "repositories": ["group/sub/project"]})
        self.assertEqual(config.work_dir("group/sub/project"), (config.work_root / "project").resolve())
        for paths in (["/project"], ["group/"], ["group//project"], ["project"],
                      ["group/../x"], ["group/./x"], ["group/my project"], ["https://group/project"],
                      ["group/sub/project", "other/project"]):
            with self.subTest(paths=paths), self.assertRaises(factory.ConfigError):
                factory.Config({**values, "repositories": paths})

    def test_config_gitlab_url_precedence_and_missing_value(self):
        values = json.loads((SOURCE.parent / "factory.example.json").read_text(encoding="utf-8"))
        with patch.dict(os.environ, {"GITLAB_URL": "https://env.example.test"}, clear=True):
            self.assertEqual(factory.Config(values).gitlab_url, values["gitlab_url"])
            del values["gitlab_url"]
            self.assertEqual(factory.Config(values).gitlab_url, "https://env.example.test")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(factory.ConfigError, "GITLAB_URL"):
                factory.Config(values)

    def test_config_rejects_gitlab_urls_with_invalid_scheme_or_missing_host(self):
        values = json.loads((SOURCE.parent / "factory.example.json").read_text(encoding="utf-8"))
        del values["gitlab_url"]
        for url in ("ftp://gitlab.example.test", "https:///path", "https://[invalid"):
            with self.subTest(url=url), patch.dict(os.environ, {"GITLAB_URL": url}):
                with self.assertRaisesRegex(factory.ConfigError, "gitlab_url"):
                    factory.Config(values)
                with self.assertRaisesRegex(factory.ConfigError, "gitlab_url"):
                    factory.Config({**values, "gitlab_url": url})

    def test_cli_reads_token_only_from_environment(self):
        values = json.loads((SOURCE.parent / "factory.example.json").read_text(encoding="utf-8"))
        values["gitlab_token"] = "ignored-dummy"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "factory.json"
            path.write_text(json.dumps(values), encoding="utf-8")
            args = ["status", "--config", str(path)]
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(factory.main(args), 2)
                self.assertIn("GITLAB_TOKEN または GL_TOKEN", stderr.getvalue())
                self.assertNotIn("ignored-dummy", stderr.getvalue())
            for env, token in (
                ({"GITLAB_TOKEN": "primary-dummy", "GL_TOKEN": "fallback-dummy"}, "primary-dummy"),
                ({"GL_TOKEN": "fallback-dummy"}, "fallback-dummy"),
            ):
                with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                    with patch.object(factory, "UrllibTransport") as transport, patch.object(factory, "Controller"):
                        self.assertEqual(factory.main(args), 0)
                        transport.assert_called_once_with(values["gitlab_url"], token)

    def test_config_rejects_duplicate_resolved_work_dirs(self):
        values = json.loads((SOURCE.parent / "factory.example.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first").mkdir()
            (root / "alias").symlink_to(root / "first", target_is_directory=True)
            values.update(repositories=["group/first", "group/second"], work_root=str(root))
            factory.Config(values)
            for overrides in (
                {"group/second": str(root / "alias")},
                {"group/first": str(root / "shared"),
                 "group/second": str(root / "nested" / ".." / "shared")},
            ):
                with self.subTest(overrides=overrides):
                    values["work_dirs"] = overrides
                    with self.assertRaisesRegex(factory.ConfigError, "作業フォルダが重複"):
                        factory.Config(values)

    def test_review_format(self):
        body = "FACTORY-VERDICT: major\n\n- [重大] 例\n"
        self.assertEqual(factory.parse_verdict(body), "major")
        self.assertEqual(factory.parse_verdict("FACTORY-VERDICT: clear"), "clear")
        self.assertEqual(factory.parse_verdict("判定行なし"), "clear")
        self.assertEqual(
            factory.format_review_comment("run", "abc", body),
            "<!-- factory:review run=run sha=abc -->\n"
            "## ファクトリーレビュー結果\n\n"
            "対象リビジョン: `abc`\n判定: 重大な指摘あり\n\n- [重大] 例\n",
        )

    def test_parse_verdict_major_finding_overrides_clear(self):
        body = "FACTORY-VERDICT: clear\n  - [重大] 例\n"
        self.assertEqual(factory.parse_verdict(body), "major")

    def test_parse_verdict_non_major_findings_remain_clear(self):
        body = "FACTORY-VERDICT: clear\n- [指摘] 例\n- [提案] 例\n"
        self.assertEqual(factory.parse_verdict(body), "clear")

    def test_init_and_configuration_errors(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            path = Path(directory) / "factory.json"
            args = ["--config", str(path)]
            self.assertEqual(factory.main(["init", *args]), 0)
            self.assertEqual(path.read_bytes(), (SOURCE.parent / "factory.example.json").read_bytes())
            factory.Config.load(path)
            self.assertEqual(factory.main(["init", *args]), 2)
            self.assertEqual(factory.main(["init", *args, "--force"]), 0)
            values = json.loads(path.read_text(encoding="utf-8"))
            values["repositories"] = ["group/repo", "other/repo"]
            path.write_text(json.dumps(values), encoding="utf-8")
            self.assertEqual(factory.main(["poll", *args]), 2)
            values["repositories"] = ["group/repo"]
            del values["goalie_py"]
            path.write_text(json.dumps(values), encoding="utf-8")
            self.assertEqual(factory.main(["run-once", *args]), 2)


if __name__ == "__main__":
    unittest.main()
