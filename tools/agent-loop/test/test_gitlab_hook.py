#!/usr/bin/env python3
"""GitLab hook tests with mocked HTTP and git remote."""
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
sys.path.insert(0, str(HOOKS))

import _gitlab_client as gl  # noqa: E402


def _load_hook(name: str):
    path = HOOKS / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GitLabClientTests(unittest.TestCase):
    def test_parse_ssh_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            with mock.patch.object(
                gl.subprocess,
                "check_output",
                return_value=b"git@gitlab.example.com:group/project.git",
            ):
                host, project = gl.parse_git_remote(repo)
        self.assertEqual(host, "gitlab.example.com")
        self.assertEqual(project, "group/project")

    def test_parse_https_remote_strips_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            with mock.patch.object(
                gl.subprocess,
                "check_output",
                return_value=b"https://oauth2:SECRET@gitlab.example.com/group/project.git",
            ):
                host, project = gl.parse_git_remote(repo)
        self.assertEqual(host, "gitlab.example.com")
        self.assertEqual(project, "group/project")

    def test_parse_https_remote_preserves_port(self):
        with mock.patch.object(
            gl.subprocess,
            "check_output",
            return_value=b"https://gitlab.example.com:8443/group/project.git",
        ):
            host, project = gl.parse_git_remote("/tmp")
        self.assertEqual(host, "gitlab.example.com:8443")
        self.assertEqual(project, "group/project")

    def test_parse_https_remote_rejects_invalid_port_as_gitlab_error(self):
        with mock.patch.object(
            gl.subprocess,
            "check_output",
            return_value=b"https://gitlab.example.com:bad/group/project.git",
        ):
            with self.assertRaises(gl.GitLabError):
                gl.parse_git_remote("/tmp")

    def test_connection_resolution_order(self):
        with tempfile.TemporaryDirectory() as ws:
            ws_path = pathlib.Path(ws)
            (ws_path / ".agents").mkdir()
            (ws_path / ".agents" / "connections.yaml").write_text(
                "gitlab:\n"
                "  - name: ws\n"
                "    host: gitlab.example.com\n"
                "    api_url: https://api.internal.example.com/api/v4\n"
                "    public_url: https://gitlab.example.com\n"
                "    token_env: WS_GITLAB_TOKEN\n",
                encoding="utf-8",
            )
            conn = gl.resolve_connection("gitlab.example.com", "group/project", str(ws_path))
        self.assertEqual(conn.api_url, "https://api.internal.example.com/api/v4")
        self.assertEqual(conn.public_url, "https://gitlab.example.com")
        self.assertEqual(conn.token_env, "WS_GITLAB_TOKEN")

    def test_token_resolution_prefers_connection_env(self):
        conn = gl.GitLabConnection(
            host="h", project_path="p", api_url="https://h/api/v4",
            public_url=None, token_env="CUSTOM_TOKEN",
        )
        with mock.patch.dict(os.environ, {"CUSTOM_TOKEN": "tok", "GITLAB_TOKEN": "other"}):
            self.assertEqual(gl.resolve_token(conn), "tok")

    def test_public_url_rewrite(self):
        conn = gl.GitLabConnection(
            host="internal", project_path="p", api_url="https://internal/api/v4",
            public_url="https://public.example.com", token_env=None,
        )
        item = {"web_url": "https://internal/group/project/-/issues/1"}
        out = gl.rewrite_item_urls(item, conn)
        self.assertEqual(out["web_url"], "https://public.example.com/group/project/-/issues/1")

    def test_http_does_not_log_token(self):
        conn = gl.GitLabConnection(
            host="gitlab.example.com", project_path="g/p",
            api_url="https://gitlab.example.com/api/v4",
            public_url=None, token_env=None,
        )
        payload = json.dumps([{"iid": 1, "updated_at": "t"}]).encode()
        resp = io.BytesIO(payload)
        resp.status = 200  # type: ignore[attr-defined]
        resp.getcode = lambda: 200  # type: ignore[method-assign]

        class _Ctx:
            def __enter__(self):
                return resp

            def __exit__(self, *args):
                return False

        log_buffer: list[str] = []

        def _capture(msg, *args, **kwargs):
            log_buffer.append(str(msg) % args if args else str(msg))

        with mock.patch.dict(os.environ, {"GITLAB_TOKEN": "glpat-SECRETVALUE"}), \
             mock.patch.object(gl.urllib.request, "urlopen", return_value=_Ctx()), \
             mock.patch.object(gl.log, "warning", side_effect=_capture), \
             mock.patch.object(gl.log, "error", side_effect=_capture), \
             mock.patch.object(gl.log, "info", side_effect=_capture):
            issues = gl.list_issues(conn, "glpat-SECRETVALUE")
        self.assertEqual(len(issues), 1)
        joined = " ".join(log_buffer)
        self.assertNotIn("glpat-SECRETVALUE", joined)
        self.assertNotIn("SECRETVALUE", joined)


class GitLabIssueHookTests(unittest.TestCase):
    def test_baseline_then_new_event_ack(self):
        issue_hook = _load_hook("gitlab-issue-hook.py")
        issues = [
            {"iid": 1, "updated_at": "2026-08-01T00:00:00Z", "labels": []},
            {"iid": 2, "updated_at": "2026-08-02T00:00:00Z", "labels": []},
        ]
        cfg = {"entry_id": "entry-a", "cwd": "/tmp/repo"}
        fetch_sequence = [
            (issues, "group/project"),
            ([issues[0], {**issues[1], "updated_at": "2026-08-03T00:00:00Z"}], "group/project"),
            ([issues[0], {**issues[1], "updated_at": "2026-08-03T00:00:00Z"}], "group/project"),
            ([{**issues[0], "updated_at": "2026-08-04T00:00:00Z"},
              {**issues[1], "updated_at": "2026-08-03T00:00:00Z"}], "group/project"),
        ]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(issue_hook, "hook_state_dir", return_value=pathlib.Path(tmp)), \
             mock.patch.object(issue_hook, "_fetch_issues", side_effect=fetch_sequence):
            self.assertIsNone(issue_hook.check(cfg))
            second = issue_hook.check(cfg)
            retried = issue_hook.check(cfg)
            issue_hook.ack()
            third = issue_hook.check(cfg)
        self.assertIn('"iid": 2', second or "")
        self.assertIn('"iid": 2', retried or "")
        self.assertIn('"iid": 1', third or "")


if __name__ == "__main__":
    unittest.main()
