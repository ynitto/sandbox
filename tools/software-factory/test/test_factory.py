import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "factory.py"
SPEC = importlib.util.spec_from_file_location("factory", SOURCE)
factory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = factory
SPEC.loader.exec_module(factory)


class FakeGh:
    def __init__(self):
        self.issues = {}
        self.prs = {}
        self.issue_comments = []
        self.pr_comments = []
        self.checks = "pending"
        self.create_calls = []
        self.mutate_diff = False

    def add_issue(self, number, progress=None):
        self.issues[number] = {
            "number": number, "title": "タイトル", "body": "要件 {{BRANCH}}",
            "labels": [{"name": label} for label in ("factory", progress) if label],
            "updatedAt": "2026-11-26T00:00:00Z",
        }

    def list_factory_issues(self, repo, label):
        return list(self.issues.values())

    def set_progress_label(self, repo, number, progress):
        labels = factory.label_names(self.issues[number]).difference(factory.PROGRESS)
        if progress:
            labels.add(progress)
        self.issues[number]["labels"] = [{"name": label} for label in labels]

    def comment_issue(self, repo, number, body):
        self.issue_comments.append((number, body))

    def comment_pr(self, repo, number, body):
        self.pr_comments.append((number, body))

    def find_pr_by_head(self, repo, branch):
        return self.prs.get(branch)

    def add_pr(self, branch):
        pr = {"number": len(self.prs) + 42, "url": "https://example.test/pull/42",
              "state": "OPEN", "isDraft": True, "headRefOid": "sha-one"}
        self.prs[branch] = pr
        return pr

    def pr_head_sha(self, repo, number):
        return next(pr["headRefOid"] for pr in self.prs.values() if pr["number"] == number)

    def pr_checks_state(self, repo, number):
        return self.checks

    def pr_diff(self, repo, number):
        if self.mutate_diff:
            next(pr for pr in self.prs.values() if pr["number"] == number)["headRefOid"] = "sha-two"
        return "diff --git a/example b/example\n"

    def create_draft_pr(self, repo, head, title, body, label):
        self.create_calls.append((head, body, label))
        return self.add_pr(head)["number"]

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
        self.dirty = False

    def branch_exists(self, work_dir, branch):
        return True

    def commits_ahead(self, work_dir, base, branch):
        return 1

    def tracked_dirty(self, work_dir):
        return self.dirty

    def push(self, work_dir, branch):
        self.pushes.append(branch)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.values = json.loads((SOURCE.parent / "factory.example.json").read_text(encoding="utf-8"))
        self.values.update(work_root=str(self.root), journal=str(self.root / "journal.jsonl"),
                           job_timeout_sec=60, max_concurrent=2)
        self.now = 1000
        self.gh = FakeGh()
        self.goalie = FakeGoalie()
        self.git = FakeGit()
        self.config_path = self.root / "factory.json"
        self.gh.add_issue(1)

    def controller(self):
        self.config_path.write_text(json.dumps(self.values), encoding="utf-8")
        return factory.Controller(factory.Config.load(self.config_path),
                                  self.gh, self.goalie, self.git, lambda: self.now)

    def poll(self):
        controller = self.controller()
        controller.poll()
        return controller.journal

    def assert_progress(self, progress, number=1):
        self.assertEqual(factory.label_names(self.gh.issues[number]),
                         {"factory", progress} if progress else {"factory"})

    def open_pr(self):
        self.poll()
        self.goalie.finish("job-1", "BRANCH: factory/issue-1")
        self.poll()
        return self.gh.prs["factory/issue-1"]

    def test_round_trip(self):
        self.poll()
        self.assert_progress("factory:implementing")
        self.assertEqual(self.goalie.calls[0][2], "factory-impl-1")
        self.assertIn("要件 {{BRANCH}}", self.goalie.calls[0][1].read_text(encoding="utf-8"))
        self.goalie.finish("job-1", "BRANCH: factory/issue-1")
        self.poll()
        self.assert_progress("factory:pr-open")
        self.assertEqual(self.git.pushes, ["factory/issue-1"])
        self.assertEqual(self.gh.create_calls, [
            ("factory/issue-1", "Closes #1\n\n<!-- factory:issue=1 -->\n", "factory"),
        ])
        self.gh.checks = "success"
        self.poll()
        self.assert_progress("factory:in-review")
        work_dir, prompt, name = self.goalie.calls[1]
        self.assertEqual(name, "factory-review-42")
        diff = prompt.with_name(prompt.stem + "-diff.patch")
        self.assertIn(diff.relative_to(work_dir).as_posix(), prompt.read_text(encoding="utf-8"))
        self.goalie.finish("job-2", "FACTORY-VERDICT: clear\n指摘はありません")
        journal = self.poll()
        self.assert_progress("factory:reviewed")
        self.assertEqual(len(self.gh.pr_comments), 1)
        self.assertIn("指摘はありません", self.gh.pr_comments[0][1])
        self.assertFalse(journal.unresolved())
        self.assertEqual(journal.last_sha(self.values["repositories"][0], 1), "sha-one")
        self.assertNotIn("指摘はありません", journal.path.read_text(encoding="utf-8"))

    def test_pending_is_idempotent_and_work_dir_has_one_slot(self):
        self.gh.add_issue(2)
        self.poll()
        journal = self.poll()
        self.assertEqual(len(self.goalie.calls), 1)
        self.assertEqual(journal.unresolved()[0]["job_id"], "job-1")
        self.assert_progress(None, 2)

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
        pr = self.open_pr()
        self.gh.checks = "success"
        self.poll()
        self.goalie.path("job-2", "pending").unlink()
        self.goalie.write("job-2", "dispatched", "request")
        self.now += 61
        journal = self.poll()
        self.assert_progress("factory:failed")
        self.assertTrue(self.goalie.path("job-2", "dispatched").is_file())
        self.assertIn(4, [event["exit_code"] for event in journal.events if event["event"] == "job_finished"])
        pr["headRefOid"] = "sha-two"
        self.poll()
        self.assert_progress("factory:failed")
        self.assertEqual(len(self.goalie.calls), 2)
        self.goalie.path("job-2", "dispatched").unlink()
        self.poll()
        self.assert_progress("factory:pr-open")

    def test_ci_pending_failure_deduplication_and_new_head(self):
        pr = self.open_pr()
        self.poll()
        self.assert_progress("factory:pr-open")
        self.gh.checks = "failure"
        self.poll()
        self.assert_progress("factory:failed")
        self.assertEqual(len(self.goalie.calls), 1)
        self.gh.set_progress_label("", 1, "factory:pr-open")
        self.poll()
        self.assertEqual(len(self.gh.pr_comments), 1)
        pr["headRefOid"] = "sha-two"
        self.poll()
        self.assert_progress("factory:pr-open")
        self.poll()
        self.assertEqual(len(self.gh.pr_comments), 2)
        self.assert_progress("factory:failed")

    def test_hold_until_config_changes(self):
        self.gh.add_issue(2)
        self.goalie.usage_error = True
        self.poll()
        self.assertEqual(len(self.goalie.calls), 1)
        self.assertEqual(len(self.gh.issue_comments), 1)
        self.goalie.usage_error = False
        self.poll()
        self.assertEqual(len(self.goalie.calls), 1)
        self.values["poll_interval_sec"] = 60
        self.poll()
        self.assertEqual(len(self.goalie.calls), 2)

    def test_review_does_not_use_moving_diff_or_stale_result(self):
        pr = self.open_pr()
        self.gh.checks = "success"
        self.gh.mutate_diff = True
        self.poll()
        self.assertEqual(len(self.goalie.calls), 1)
        self.gh.mutate_diff = False
        self.poll()
        self.goalie.finish("job-2", "FACTORY-VERDICT: major")
        pr["headRefOid"] = "sha-three"
        journal = self.poll()
        self.assert_progress("factory:pr-open")
        self.assertFalse(self.gh.pr_comments)
        self.assertIn("stale_revision", [event["reason"] for event in journal.events])

    def test_review_waits_for_checks_again_before_posting(self):
        self.open_pr()
        self.gh.checks = "success"
        self.poll()
        self.goalie.finish("job-2", "FACTORY-VERDICT: major\n重要な指摘")
        self.gh.checks = "pending"
        self.assertEqual(len(self.poll().unresolved()), 1)
        self.assertFalse(self.gh.pr_comments)
        self.gh.checks = "success"
        self.poll()
        self.assert_progress("factory:failed")
        self.assertIn("重要な指摘", self.gh.pr_comments[0][1])

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
        self.poll()
        journal = self.controller().journal
        journal.path.write_text(
            "".join(line + "\n" for line in journal.path.read_text(encoding="utf-8").splitlines()
                    if json.loads(line)["event"] != "job_submitted"), encoding="utf-8",
        )
        journal = self.poll()
        self.assert_progress("factory:failed")
        self.assertEqual(len(self.gh.issue_comments), 1)
        self.assertIn("interrupted_before_submit", [event["reason"] for event in journal.events])

    def test_partial_result_waits_and_missing_job_is_not_retried(self):
        self.poll()
        self.goalie.path("job-1", "pending").unlink()
        self.goalie.write("job-1", "results", "stat")
        self.now += 61
        self.assertEqual(len(self.poll().unresolved()), 1)
        self.goalie.path("job-1", "results").unlink()
        self.assertFalse(self.poll().unresolved())
        self.assert_progress("factory:failed")
        self.poll()
        self.assertEqual(len(self.goalie.calls), 1)


class ClientTests(unittest.TestCase):
    def test_check_rollup(self):
        class ChecksRunner:
            def run(self, argv, cwd):
                return 0, json.dumps({"statusCheckRollup": self.checks}), ""

        runner = ChecksRunner()
        gh = factory.GhClient(runner)
        success = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "NEUTRAL"}
        pending = {"__typename": "CheckRun", "status": "IN_PROGRESS"}
        failure = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "TIMED_OUT"}
        for checks, expected in (
            ([], "pending"),
            (None, "pending"),
            ([success, {"state": "SUCCESS"}], "success"),
            ([success, {"state": "EXPECTED"}], "pending"),
            ([pending, failure], "failure"),
            ([pending, {"state": "ERROR"}], "failure"),
        ):
            with self.subTest(checks=checks):
                runner.checks = checks
                self.assertEqual(gh.pr_checks_state("owner/repo", 1), expected)


class CliAndFormattingTests(unittest.TestCase):
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
            values["repositories"] = ["owner/repo", "other/repo"]
            path.write_text(json.dumps(values), encoding="utf-8")
            self.assertEqual(factory.main(["poll", *args]), 2)
            values["repositories"] = ["owner/repo"]
            del values["goalie_py"]
            path.write_text(json.dumps(values), encoding="utf-8")
            self.assertEqual(factory.main(["run-once", *args]), 2)


if __name__ == "__main__":
    unittest.main()
