import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import draft_request


MEMO = "  通知を追加する\r\nテストで確認する。\n\n"
VALID_DRAFT = """## 子イシュー案

### 通知を追加する

#### 概要
- 更新を通知する

#### 受け入れ条件
- [ ] 更新後に通知される

### 通知のテストを追加する

#### 概要
- 通知をテストする

#### 受け入れ条件
- [ ] テストが成功する
"""


class DraftRequestTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.memo = root / "memo.txt"
        self.memo.write_bytes(MEMO.encode("utf-8"))
        self.draft = root / "draft.md"
        self.summary = root / "draft.json"
        self.agent = root / "agent.py"
        env = patch.dict(os.environ, {
            "GL_PROJECT_URL": "https://gitlab.example/team/repo",
            "GITLAB_TOKEN": "fake-token",
            "GL_TOKEN": "another-fake-token",
            "IDD_DRAFT_TEST_SECRET": "secret",
            "XDG_CONFIG_HOME": "parent-config",
            "LC_CTYPE": "C.UTF-8",
        })
        env.start()
        self.addCleanup(env.stop)
        for name in ("IDD_DRAFT_AGENT", "IDD_DRAFT_TIMEOUT_SEC", "REQUEST_DRAFT_DRY_RUN"):
            os.environ.pop(name, None)
        http = patch.object(draft_request.gl.urllib.request, "urlopen", return_value=io.BytesIO(b'{"id": 9}'))
        self.http = http.start()
        self.addCleanup(http.stop)

    def configure_agent(self, source):
        self.agent.write_text(source, encoding="utf-8")
        os.environ["IDD_DRAFT_AGENT"] = json.dumps([sys.executable, str(self.agent), "{prompt}"])

    def build(self):
        self.assertEqual(draft_request.main([
            "build", "--memo", str(self.memo), "--out", str(self.draft), "--json-out", str(self.summary),
        ]), 0)
        self.http.assert_not_called()
        body = self.draft.read_bytes().decode("utf-8")
        self.assertEqual(body.splitlines()[0], draft_request.DRAFT_MARK)
        return body, json.loads(self.summary.read_text(encoding="utf-8"))

    def assert_fallback(self):
        body, summary = self.build()
        self.assertIn(MEMO, body)
        self.assertIn("（自動生成できませんでした。メモを転記しています）", body)
        self.assertIn("TODO", body)
        self.assertEqual(summary, {"count": 1, "titles": ["TODO: タイトルを記入"], "method": "fallback"})

    def test_cli_generates_proposals(self):
        self.configure_agent(
            "import os, sys\n"
            f"assert {MEMO!r} in sys.argv[1]\n"
            "assert all(key not in os.environ for key in "
            "('GITLAB_TOKEN', 'GL_TOKEN', 'IDD_DRAFT_TEST_SECRET', 'XDG_CONFIG_HOME'))\n"
            "assert os.environ['LC_CTYPE'] == 'C.UTF-8'\n"
            f"sys.stdout.buffer.write({VALID_DRAFT.encode()!r})\n"
        )
        body, summary = self.build()
        self.assertIn(VALID_DRAFT.strip(), body)
        self.assertEqual(summary, {
            "count": 2, "titles": ["通知を追加する", "通知のテストを追加する"], "method": "cli",
        })

    def test_unconfigured_cli_falls_back(self):
        self.assert_fallback()

    def test_invalid_prompt_arguments_fall_back_without_launching_agent(self):
        for command in (
            [sys.executable, str(self.agent), "--prompt={prompt}"],
            [sys.executable, str(self.agent), "{prompt}", "--prompt={prompt}"],
            ["{prompt}", "{prompt}"],
            ["/bin/sh", "-c", "{prompt}"],
        ):
            with self.subTest(command=command), patch.object(draft_request.subprocess, "Popen") as launch:
                os.environ["IDD_DRAFT_AGENT"] = json.dumps(command)
                self.assert_fallback()
                launch.assert_not_called()

    def test_crlf_headings_are_recognized(self):
        self.assertEqual(draft_request.draft_titles(VALID_DRAFT.replace("\n", "\r\n")), [
            "通知を追加する", "通知のテストを追加する",
        ])

    def test_launch_failure_falls_back(self):
        os.environ["IDD_DRAFT_AGENT"] = json.dumps([str(self.agent), "{prompt}"])
        self.assert_fallback()

    def test_nonzero_exit_falls_back_even_with_valid_output(self):
        self.configure_agent(f"print({VALID_DRAFT!r})\nraise SystemExit(1)\n")
        self.assert_fallback()

    def test_timeout_falls_back(self):
        self.configure_agent("import time\ntime.sleep(30)\n")
        os.environ["IDD_DRAFT_TIMEOUT_SEC"] = "1"
        self.assert_fallback()

    def test_invalid_output_falls_back(self):
        for output in (
            VALID_DRAFT.replace("## 子イシュー案", "## 別の見出し"),
            VALID_DRAFT.replace("#### 受け入れ条件", "#### 確認", 1),
            "## 子イシュー案\n\n#### 受け入れ条件",
        ):
            with self.subTest(output=output):
                self.configure_agent(f"print({output!r})\n")
                self.assert_fallback()

    def post(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(draft_request.main([
                "post", "--hub-issue", "42", "--draft", str(self.draft),
            ]), 0)
        return json.loads(stdout.getvalue())

    def test_post_defaults_to_dry_run_without_resolving_connection(self):
        self.build()
        with patch.object(draft_request.gl, "get_project_info") as connection:
            for setting in (None, "", "1", "false", " 0"):
                with self.subTest(setting=setting):
                    if setting is not None:
                        os.environ["REQUEST_DRAFT_DRY_RUN"] = setting
                    self.assertEqual(self.post(), {"dry_run": True, "hub_issue": 42})
            connection.assert_not_called()
        self.http.assert_not_called()

    def test_post_rejects_draft_without_mark(self):
        self.draft.write_text(VALID_DRAFT, encoding="utf-8")
        self.assert_post_rejected("42", 1, "下書きの先頭に所定の印が必要です")

    def test_post_rejects_nonpositive_hub_issue(self):
        self.build()
        for hub_issue in ("0", "-1"):
            with self.subTest(hub_issue=hub_issue):
                self.assert_post_rejected(hub_issue, 2, "--hub-issue は正の整数を指定してください")

    def assert_post_rejected(self, hub_issue, exit_code, message):
        os.environ["REQUEST_DRAFT_DRY_RUN"] = "0"
        with patch.object(draft_request.gl, "get_project_info") as connection, contextlib.redirect_stderr(
            io.StringIO()
        ) as stderr, self.assertRaises(SystemExit) as raised:
            draft_request.main(["post", "--hub-issue", hub_issue, "--draft", str(self.draft)])
        self.assertEqual(raised.exception.code, exit_code)
        self.assertIn(message, stderr.getvalue())
        connection.assert_not_called()
        self.http.assert_not_called()

    def test_live_post_only_sends_a_note(self):
        self.build()
        with self.draft.open("a", encoding="utf-8") as output:
            output.write("\n  /clone other/project\n")
        os.environ["REQUEST_DRAFT_DRY_RUN"] = "0"
        with patch.object(draft_request.gl, "get_project_info",
                          return_value=("gitlab.example", "team/repo")), patch.object(
            draft_request.gl, "get_token", return_value="fake-token"
        ):
            self.assertEqual(self.post(), {"id": 9})
        self.http.assert_called_once()
        request = self.http.call_args[0][0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://gitlab.example/api/v4/projects/team%2Frepo/issues/42/notes")
        expected = self.draft.read_text(encoding="utf-8").replace("  /clone", "  &#47;clone")
        self.assertEqual(json.loads(request.data), {"body": expected})


if __name__ == "__main__":
    unittest.main()
