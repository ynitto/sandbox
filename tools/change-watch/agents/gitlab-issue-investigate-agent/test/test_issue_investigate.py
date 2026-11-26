"""GitLab と goalie を差し替えて、新規 Issue の調査と投稿を確かめる。"""
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

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
        self.post_error = None

    def request(self, method, url, headers, data=None):
        self.calls.append((method, url, headers))
        if method == "GET":
            return self.pages.get(int(url.rsplit("=", 1)[1]), [])
        if self.fail_post:
            raise OSError("投稿できない")
        if self.post_error is not None:
            raise self.post_error
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

    def invoke_main(self):
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch.object(sys, "stdin", StringIO(json.dumps(self.change))),
            patch.object(agent, "UrllibTransport", return_value=self.gitlab),
            patch.object(agent, "run", partial(agent.run, goalie_run=self.goalie)),
            redirect_stdout(self.stdout),
            redirect_stderr(self.stderr),
        ):
            return agent.main(["run"])

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
        prompt = None

        def read_prompt(args, **kwargs):
            nonlocal prompt
            path = Path(args[args.index("--prompt-file") + 1])
            prompt = path.read_text(encoding="utf-8")
            return self.goalie.return_value

        self.goalie.side_effect = read_prompt
        self.assertEqual(self.invoke(), 0)
        self.goalie.assert_called_once()
        args = self.goalie.call_args.args[0]
        self.assertEqual(args[:6], [sys.executable, self.env["ISSUE_INVESTIGATE_GOALIE_PY"],
                                    "job", "run", "--dir", str(self.directory)])
        self.assertEqual(args[8:], ["--name", "issue-investigate-42", "--timeout", "3600"])
        path = Path(args[args.index("--prompt-file") + 1])
        self.assertEqual(path.parent, self.directory / "state" / "requests")
        self.assertFalse(path.exists())
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

    def test_secret_names_and_redaction_lengths(self):
        secrets = {
            "service_token": "token-value",
            "Client_Secret": "secret-value",
            "DB_PASSWORD": "long-password",
            "DB_PASSWD": "pass",
            "MY_API_KEY": "12345678",
            "MY_APIKEY": "another-api-key",
            "SSH_PRIVATE_KEY": "private-key",
            "APP_CREDENTIALS": "credential-value",
            "GITLAB_TOKEN": "gl1",
            "GL_TOKEN": "gl2",
        }
        self.env.update(secrets, PWD=str(self.directory))
        report = " ".join([*secrets.values(), self.env["PWD"]])
        self.record["description"] = report
        self.goalie.return_value = subprocess.CompletedProcess([], 0, report)
        self.assertEqual(self.invoke(dry_run=True), 0)
        self.assertEqual(self.invoke(), 0)
        env = self.goalie.call_args.kwargs["env"]
        self.assertTrue(secrets.keys().isdisjoint(env))
        self.assertEqual(env["PWD"], self.env["PWD"])
        for output in (self.stdout.getvalue(), self.gitlab.posts[0]):
            self.assertIn(" pass ", output)
            self.assertIn(self.env["PWD"], output)
            for name, value in secrets.items():
                if name != "DB_PASSWD":
                    self.assertNotIn(value, output)

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

    def test_main_unexpected_goalie_exit(self):
        self.goalie.return_value = subprocess.CompletedProcess([], 5, "")
        self.assertEqual(self.invoke_main(), 2)
        self.assertFalse(self.gitlab.posts)

    def test_main_missing_required_env(self):
        del self.env["GITLAB_TOKEN"]
        self.assertEqual(self.invoke_main(), 2)
        self.assertIn("GITLAB_TOKEN", self.stderr.getvalue())
        self.goalie.assert_not_called()

    def test_main_http_500_retries_pending_result(self):
        self.gitlab.post_error = HTTPError("", 500, "", {}, None)
        self.assertEqual(self.invoke_main(), 3)
        self.assertFalse(self.gitlab.posts)
        path = Path(self.goalie.call_args.args[0][7])
        self.assertFalse(path.exists())
        self.gitlab.post_error = None
        self.assertEqual(self.invoke(), 0)
        self.goalie.assert_called_once()
        self.assertEqual(len(self.gitlab.posts), 1)
        self.assertIn("調査の原案", self.gitlab.posts[0])

    def test_main_classifies_note_lookup_errors(self):
        for error, code in (
            (HTTPError("", 429, "", {}, None), 3),
            (HTTPError("", 403, "", {}, None), 2),
            (URLError("接続不可"), 3),
        ):
            with self.subTest(error=error):
                with patch.object(self.gitlab, "request", side_effect=error):
                    self.assertEqual(self.invoke_main(), code)
        self.goalie.assert_not_called()

    def test_main_local_file_error(self):
        with patch.object(agent, "write_request", side_effect=PermissionError):
            self.assertEqual(self.invoke_main(), 2)
        self.goalie.assert_not_called()

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
        with self.assertRaises(agent.TransientError):
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
    def test_workflow_structure(self):
        if importlib.util.find_spec("yaml") is None:
            self.skipTest("PyYAML がありません")
        import yaml

        machine = AGENT / "machine"
        workflow = yaml.safe_load((machine / "workflow.yaml").read_text(encoding="utf-8"))
        states = workflow["states"]
        for transition in workflow["transitions"]:
            rule = transition.get("condition_rule", "")
            if rule.startswith("startswith:last_output:"):
                prefix = rule.split(":", 2)[2]
                with self.subTest(transition=transition):
                    validator = states[transition["from"]].get("output_validator", "")
                    self.assertTrue(validator.startswith("startswith:"), validator)
                    candidates = validator.split(":", 1)[1].split(",")
                    self.assertIn(prefix, [candidate.strip() for candidate in candidates])

        keys = {
            "input", "last_output", "history", "today", "now", "step_count",
            "current_state", "context",
        }
        keys.update(workflow["inputs"])
        keys.update(state["output_key"] for state in states.values() if "output_key" in state)
        for action in sorted((machine / "actions").glob("*.md")):
            with self.subTest(action=action.name):
                placeholders = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", action.read_text(encoding="utf-8"))
                self.assertEqual(set(placeholders) - keys, set())

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
