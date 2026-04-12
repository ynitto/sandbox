from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
MODULE_PATH = SCRIPT_DIR / "gl.py"


def load_gl_module():
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        spec = importlib.util.spec_from_file_location("gitlab_idd_gl", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_create_mr_sets_remove_source_branch_when_requested(monkeypatch):
    gl = load_gl_module()
    captured = {}

    def fake_api(host, token, method, path, data=None, params=None):
        captured.update({
            "host": host,
            "token": token,
            "method": method,
            "path": path,
            "data": data,
            "params": params,
        })
        return {"iid": 7}

    monkeypatch.setattr(gl, "api", fake_api)
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: None)

    args = SimpleNamespace(
        title="タイトル",
        source_branch="feature/issue-42-sample",
        target_branch="main",
        description="",
        description_file=None,
        draft=True,
        remove_source_branch=True,
        get=None,
    )

    gl.cmd_create_mr(args, "gitlab.example.com", "group/project", "token")

    assert captured["method"] == "POST"
    assert captured["path"].endswith("/merge_requests")
    assert captured["data"]["draft"] is True
    assert captured["data"]["remove_source_branch"] is True
    assert captured["data"]["title"] == "Draft: タイトル"


def test_create_mr_omits_remove_source_branch_by_default(monkeypatch):
    gl = load_gl_module()
    captured = {}

    def fake_api(host, token, method, path, data=None, params=None):
        captured["data"] = data
        return {"iid": 8}

    monkeypatch.setattr(gl, "api", fake_api)
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: None)

    args = SimpleNamespace(
        title="タイトル",
        source_branch="feature/issue-42-sample",
        target_branch="main",
        description="body",
        description_file=None,
        draft=False,
        remove_source_branch=False,
        get=None,
    )

    gl.cmd_create_mr(args, "gitlab.example.com", "group/project", "token")

    assert "remove_source_branch" not in captured["data"]
    assert captured["data"]["description"] == "body"


def test_check_review_defer_allows_when_worker_node_id_missing(monkeypatch):
    gl = load_gl_module()
    captured = {}

    monkeypatch.setattr(gl, "api_list", lambda host, token, path: [])
    monkeypatch.setattr(gl, "get_node_id", lambda: "node-a")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=1440.0, get=None)
    gl.cmd_check_review_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "no_worker_node_id"


def test_check_review_defer_blocks_within_lock_window(monkeypatch):
    gl = load_gl_module()
    captured = {}
    started = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    monkeypatch.setattr(
        gl,
        "api_list",
        lambda host, token, path: [{
            "body": "start <!-- gitlab-idd:worker-node-id:node-a -->",
            "created_at": started,
        }],
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "node-a")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=1440.0, get=None)
    gl.cmd_check_review_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is True
    assert captured["reason"] == "self_implemented_locked"
    assert captured["lock_minutes"] == 1440.0


def test_check_review_defer_allows_after_lock_expired(monkeypatch):
    gl = load_gl_module()
    captured = {}
    started = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()

    monkeypatch.setattr(
        gl,
        "api_list",
        lambda host, token, path: [{
            "body": "start <!-- gitlab-idd:worker-node-id:node-a -->",
            "created_at": started,
        }],
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "node-a")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=1440.0, get=None)
    gl.cmd_check_review_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "self_implemented_lock_expired"
    assert captured["lock_minutes"] == 1440.0