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


# ---------------------------------------------------------------------------
# check-defer (self-created issue deferral)
# ---------------------------------------------------------------------------

def test_check_defer_allows_when_not_my_issue(monkeypatch):
    """creator-node-id in description differs from current node → defer=False"""
    gl = load_gl_module()
    captured = {}

    monkeypatch.setattr(
        gl, "api",
        lambda host, token, method, path, data=None, params=None: {
            "description": "本文 <!-- gitlab-idd:creator-node-id:other-node -->",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "my-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=60.0, get=None)
    gl.cmd_check_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "not_my_issue"


def test_check_defer_defers_when_self_created_recently(monkeypatch):
    """creator-node-id matches current node and issue is within defer window → defer=True"""
    gl = load_gl_module()
    captured = {}
    created = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    monkeypatch.setattr(
        gl, "api",
        lambda host, token, method, path, data=None, params=None: {
            "description": "本文 <!-- gitlab-idd:creator-node-id:my-node -->",
            "created_at": created,
        },
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "my-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=60.0, get=None)
    gl.cmd_check_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is True
    assert captured["reason"] == "self_created_too_recent"
    assert captured["defer_minutes"] == 60.0


def test_check_defer_allows_when_self_created_expired(monkeypatch):
    """creator-node-id matches current node but defer window has passed → defer=False"""
    gl = load_gl_module()
    captured = {}
    created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    monkeypatch.setattr(
        gl, "api",
        lambda host, token, method, path, data=None, params=None: {
            "description": "本文 <!-- gitlab-idd:creator-node-id:my-node -->",
            "created_at": created,
        },
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "my-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=60.0, get=None)
    gl.cmd_check_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "self_created_but_expired"


# ---------------------------------------------------------------------------
# check-assigned-defer (stale-assignee lock)
# ---------------------------------------------------------------------------

def test_check_assigned_defer_allows_when_no_worker_node_id(monkeypatch):
    """no worker-node-id comment exists → defer=False"""
    gl = load_gl_module()
    captured = {}

    monkeypatch.setattr(
        gl, "api_list",
        lambda host, token, path: [
            {"body": "作業開始します", "created_at": datetime.now(timezone.utc).isoformat()},
        ],
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "my-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=1440.0, get=None)
    gl.cmd_check_assigned_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "no_worker_node_id"


def test_check_assigned_defer_allows_when_my_assignment(monkeypatch):
    """worker-node-id matches current node → defer=False (own assignment)"""
    gl = load_gl_module()
    captured = {}
    started = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    monkeypatch.setattr(
        gl, "api_list",
        lambda host, token, path: [
            {"body": "着手 <!-- gitlab-idd:worker-node-id:my-node -->", "created_at": started},
        ],
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "my-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=1440.0, get=None)
    gl.cmd_check_assigned_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "my_assignment"


def test_check_assigned_defer_defers_when_active_lock(monkeypatch):
    """worker-node-id is another node within lock window → defer=True"""
    gl = load_gl_module()
    captured = {}
    started = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    monkeypatch.setattr(
        gl, "api_list",
        lambda host, token, path: [
            {"body": "着手 <!-- gitlab-idd:worker-node-id:other-node -->", "created_at": started},
        ],
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "my-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=1440.0, get=None)
    gl.cmd_check_assigned_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is True
    assert captured["reason"] == "assigned_active_lock"
    assert captured["lock_minutes"] == 1440.0


def test_check_assigned_defer_allows_when_lock_expired(monkeypatch):
    """worker-node-id is another node but lock window has passed → defer=False"""
    gl = load_gl_module()
    captured = {}
    started = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()

    monkeypatch.setattr(
        gl, "api_list",
        lambda host, token, path: [
            {"body": "着手 <!-- gitlab-idd:worker-node-id:other-node -->", "created_at": started},
        ],
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "my-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, minutes=1440.0, get=None)
    gl.cmd_check_assigned_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "assigned_lock_expired"