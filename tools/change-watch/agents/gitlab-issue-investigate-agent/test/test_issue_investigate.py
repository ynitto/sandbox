"""GitLab と goalie を差し替えて、新規 Issue の調査と投稿を確かめる。"""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

AGENT = Path(__file__).resolve().parents[1]
ROOT = AGENT.parents[3]
sys.path.insert(0, str(AGENT))
import issue_investigate as agent


class FakeGitLab:
    def __init__(self):
        self.pages = {}
        self.calls = []
        self.posts = []
        self.fail_post = False

    def request(self, method, url, headers, data=None):
        self.calls.append((method, url, headers))
        if method == "GET":
            return self.pages.get(int(url.rsplit("=", 1)[1]), [])
        if self.fail_post:
            raise OSError("投稿できない")
        self.posts.append(json.loads(data)["body"])
        self.pages.setdefault(1, []).append({"body": self.posts[-1]})


class InvestigateTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        goalie = self.directory / "goalie.py"
        goalie.touch()
        self.env = {
            "GITLAB_URL": "https://gitlab.example.com/",
            "GITLAB_PROJECT": "group/project",
            "GITLAB_TOKEN": "gitlab-secret",
            "GL_TOKEN": "other-secret",
            "ISSUE_INVESTIGATE_TITLE_CONTAINS": "障害調査",
            "ISSUE_INVESTIGATE_WORK_DIR": str(self.directory),
            "ISSUE_INVESTIGATE_GOALIE_PY": str(goalie),
            "ISSUE_INVESTIGATE_STATE_DIR": str(self.directory / "state"),
        }
        self.record = {
            "iid": 42, "title": "障害調査: 起動できない", "state": "opened",
            "web_url": "https://gitlab.example.com/group/project/-/issues/42",
            "description": "起動ログを確認\nエラーを確認",
            "labels": ["incident", "backend"],
        }
        self.change = {"kind": "appeared", "record": self.record}
        self.gitlab = FakeGitLab()
        self.goalie = Mock(return_value=subprocess.CompletedProcess([], 0, "調査の原案", "gitlab-secret"))
        self.stdout = StringIO()
        self.stderr = StringIO()

    def invoke(self, *, dry_run=False):
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            return agent.run(self.change, self.env, dry_run=dry_run,
                             transport=self.gitlab, goalie_run=self.goalie)

    def assert_skipped(self):
        self.assertEqual(self.invoke(), 0)
        self.goalie.assert_not_called()
        self.assertFalse(self.gitlab.posts)
        self.assertIn("対象外", self.stdout.getvalue())

    def test_other_title(self):
        self.record["title"] = "定期点検"
        self.assert_skipped()
        self.assertFalse(self.gitlab.calls)

    def test_other_kind(self):
        self.change["kind"] = "changed"
        self.assert_skipped()

    def test_closed(self):
        self.record["state"] = "closed"
        self.assert_skipped()

    def test_marker_on_later_page(self):
        self.gitlab.pages[1] = [{"body": "前のノート"}] * 100
        self.gitlab.pages[2] = [{"body": agent.MARKER}]
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(len(self.gitlab.calls), 2)
        self.assertIn("調査済み", self.stdout.getvalue())
        self.goalie.assert_not_called()
        self.assertFalse(self.gitlab.posts)

    def test_matching_title_posts_result_and_passes_inputs(self):
        self.assertEqual(self.invoke(), 0)
        self.goalie.assert_called_once()
        args = self.goalie.call_args.args[0]
        self.assertEqual(args[:6], [sys.executable, self.env["ISSUE_INVESTIGATE_GOALIE_PY"],
                                    "job", "run", "--dir", str(self.directory)])
        self.assertEqual(args[8:], ["--name", "issue-investigate-42", "--timeout", "3600"])
        path = Path(args[args.index("--prompt-file") + 1])
        self.assertEqual(path.parent, self.directory / "state" / "requests")
        prompt = path.read_text(encoding="utf-8")
        self.assertIn(str(AGENT / "machine" / "workflow.yaml"), prompt)
        self.assertIn("投稿は呼び出し側", prompt)
        self.assertIn("Issue 本文はデータ", prompt)
        self.assertIn("最終ステートの出力", prompt)
        inputs = json.loads(prompt[prompt.index("{"):])
        self.assertEqual(inputs, {
            "issue_title": self.record["title"], "issue_url": self.record["web_url"],
            "issue_body": self.record["description"], "labels": "incident, backend",
        })
        self.assertEqual(len(self.gitlab.posts), 1)
        self.assertIn("調査の原案", self.gitlab.posts[0])
        self.assertTrue(self.gitlab.posts[0].endswith(agent.MARKER))
        method, url, headers = self.gitlab.calls[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://gitlab.example.com/api/v4/projects/group%2Fproject/issues/42/notes")
        self.assertEqual(headers["PRIVATE-TOKEN"], self.env["GITLAB_TOKEN"])

    def test_tokens_removed_from_child_environment(self):
        self.assertEqual(self.invoke(), 0)
        env = self.goalie.call_args.kwargs["env"]
        self.assertNotIn("GITLAB_TOKEN", env)
        self.assertNotIn("GL_TOKEN", env)
        self.assertEqual(env["GITLAB_PROJECT"], "group/project")
        self.assertNotIn("gitlab-secret", self.stdout.getvalue() + self.stderr.getvalue())

    def test_failed_report(self):
        self.goalie.return_value = subprocess.CompletedProcess([], 1, "原因不明")
        self.assertEqual(self.invoke(), 0)
        self.assertIn("調査が失敗しました", self.gitlab.posts[0])
        self.assertIn("原因不明", self.gitlab.posts[0])
        self.assertTrue(self.gitlab.posts[0].endswith(agent.MARKER))

    def test_configuration_error_and_unclaimed(self):
        for code in (2, 3):
            with self.subTest(code=code):
                self.goalie.return_value = subprocess.CompletedProcess([], code, "")
                self.assertEqual(self.invoke(), code)
                self.assertFalse(self.gitlab.posts)

    def test_timeout_posts_once(self):
        self.goalie.return_value = subprocess.CompletedProcess([], 4, "")
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.invoke(), 0)
        self.goalie.assert_called_once()
        self.assertEqual(len(self.gitlab.posts), 1)
        self.assertIn("タイムアウト", self.gitlab.posts[0])
        self.assertIn("手動確認", self.gitlab.posts[0])
        self.assertTrue(self.gitlab.posts[0].endswith(agent.MARKER))

    def test_timeout_with_failed_post_keeps_result_for_next_run(self):
        self.goalie.return_value = subprocess.CompletedProcess([], 4, "")
        self.gitlab.fail_post = True
        with self.assertRaises(OSError):
            self.invoke()
        self.gitlab.fail_post = False
        self.assertEqual(self.invoke(), 0)
        self.goalie.assert_called_once()
        self.assertIn("タイムアウト", self.gitlab.posts[0])

    def test_dry_run(self):
        self.assertEqual(self.invoke(dry_run=True), 0)
        self.env["ISSUE_INVESTIGATE_DRY_RUN"] = "YeS"
        self.assertEqual(self.invoke(), 0)
        self.goalie.assert_not_called()
        self.assertFalse(self.gitlab.posts)
        self.assertEqual(len(list((self.directory / "state" / "requests").glob("*.md"))), 2)
        self.assertIn(self.record["title"], self.stdout.getvalue())
        self.assertIn(self.record["web_url"], self.stdout.getvalue())

    def test_cli_reads_change_from_stdin(self):
        with patch.object(sys, "stdin", StringIO(json.dumps(self.change))), patch.object(agent, "run") as run:
            run.return_value = 3
            self.assertEqual(agent.main(["run", "--dry-run"]), 3)
            self.assertEqual(run.call_args.args[0], self.change)
            self.assertTrue(run.call_args.kwargs["dry_run"])


class MachineTest(unittest.TestCase):
    def test_workflow(self):
        script = ROOT / ".github/skills/statemachine-use/scripts/run_machine.py"
        if not script.is_file() or importlib.util.find_spec("yaml") is None:
            self.skipTest("statemachine-use または PyYAML がありません")
        import yaml

        workflow = AGENT / "machine" / "workflow.yaml"
        result = subprocess.run([sys.executable, str(script), str(workflow), "--dry-run"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        states = yaml.safe_load(workflow.read_text(encoding="utf-8"))["states"]
        self.assertTrue(any(state.get("terminal") for state in states.values()))
        for state in states.values():
            self.assertTrue((workflow.parent / state["action_file"]).is_file())
