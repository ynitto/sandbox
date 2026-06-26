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


# ---------------------------------------------------------------------------
# check-non-requester-review-defer
# ---------------------------------------------------------------------------

def test_check_non_requester_review_defer_allows_when_no_review_yet(monkeypatch):
    """worker-node-id exists but this node has not reviewed yet → defer=False"""
    gl = load_gl_module()
    captured = {}
    started = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    monkeypatch.setattr(
        gl, "api_list",
        lambda host, token, path: [
            {"body": "着手 <!-- gitlab-idd:worker-node-id:worker-node -->", "created_at": started},
        ],
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "reviewer-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, get=None)
    gl.cmd_check_non_requester_review_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "not_yet_reviewed"


def test_check_non_requester_review_defer_defers_when_already_reviewed_in_cycle(monkeypatch):
    """non-requester-reviewed marker exists after latest worker-node-id → defer=True"""
    gl = load_gl_module()
    captured = {}
    started = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    reviewed = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    monkeypatch.setattr(
        gl, "api_list",
        lambda host, token, path: [
            {"body": "着手 <!-- gitlab-idd:worker-node-id:worker-node -->", "created_at": started},
            {
                "body": "レビュー <!-- gitlab-idd:non-requester-reviewed:reviewer-node -->",
                "created_at": reviewed,
            },
        ],
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "reviewer-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, get=None)
    gl.cmd_check_non_requester_review_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is True
    assert captured["reason"] == "already_reviewed_this_cycle"


def test_check_non_requester_review_defer_allows_when_worker_restarted(monkeypatch):
    """new worker-node-id after previous non-requester-reviewed → defer=False (new cycle)"""
    gl = load_gl_module()
    captured = {}
    first_start = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    reviewed = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    second_start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    monkeypatch.setattr(
        gl, "api_list",
        lambda host, token, path: [
            # First work cycle
            {"body": "着手 <!-- gitlab-idd:worker-node-id:worker-node -->", "created_at": first_start},
            # Non-requester reviewed in first cycle
            {
                "body": "レビュー <!-- gitlab-idd:non-requester-reviewed:reviewer-node -->",
                "created_at": reviewed,
            },
            # Worker restarted after rework → new cycle
            {"body": "再着手 <!-- gitlab-idd:worker-node-id:worker-node -->", "created_at": second_start},
        ],
    )
    monkeypatch.setattr(gl, "get_node_id", lambda: "reviewer-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, get=None)
    gl.cmd_check_non_requester_review_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "not_yet_reviewed"


# ---------------------------------------------------------------------------
# get-max-review-per-run
# ---------------------------------------------------------------------------

def test_get_max_review_per_run_returns_default_when_not_set(monkeypatch):
    """No registry entry → returns DEFAULT_MAX_REVIEW_PER_RUN (1)"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {})
    assert gl.get_max_review_per_run() == 1


def test_get_max_review_per_run_returns_configured_value(monkeypatch):
    """Registry has max_review_per_run set → returns that value"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {
        "skill_configs": {"gitlab-idd": {"max_review_per_run": 3}}
    })
    assert gl.get_max_review_per_run() == 3


def test_get_max_review_per_run_clamps_to_minimum_one(monkeypatch):
    """Registry has max_review_per_run=0 → clamped to 1"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {
        "skill_configs": {"gitlab-idd": {"max_review_per_run": 0}}
    })
    assert gl.get_max_review_per_run() == 1


def test_check_non_requester_review_defer_allows_when_no_worker_node_id(monkeypatch):
    """no worker-node-id comment at all → defer=False (allow review)"""
    gl = load_gl_module()
    captured = {}

    monkeypatch.setattr(gl, "api_list", lambda host, token, path: [])
    monkeypatch.setattr(gl, "get_node_id", lambda: "reviewer-node")
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.update(obj))

    args = SimpleNamespace(issue_id=42, get=None)
    gl.cmd_check_non_requester_review_defer(args, "gitlab.example.com", "group/project", "token")

    assert captured["defer"] is False
    assert captured["reason"] == "not_yet_reviewed"


# ---------------------------------------------------------------------------
# get_self_defer_minutes
# ---------------------------------------------------------------------------

def test_get_self_defer_minutes_returns_default_when_not_set(monkeypatch):
    """No registry entry → returns DEFAULT_SELF_DEFER_MINUTES (60)"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {})
    assert gl.get_self_defer_minutes() == 60.0


def test_get_self_defer_minutes_returns_configured_value(monkeypatch):
    """Registry has self_defer_minutes set → returns that value"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {
        "skill_configs": {"gitlab-idd": {"self_defer_minutes": 120}}
    })
    assert gl.get_self_defer_minutes() == 120.0


def test_get_self_defer_minutes_clamps_to_zero(monkeypatch):
    """Registry has self_defer_minutes=-10 → clamped to 0"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {
        "skill_configs": {"gitlab-idd": {"self_defer_minutes": -10}}
    })
    assert gl.get_self_defer_minutes() == 0.0


# ---------------------------------------------------------------------------
# get_self_review_lock_minutes
# ---------------------------------------------------------------------------

def test_get_self_review_lock_minutes_returns_default_when_not_set(monkeypatch):
    """No registry entry → returns DEFAULT_SELF_REVIEW_LOCK_MINUTES (1440)"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {})
    assert gl.get_self_review_lock_minutes() == 1440.0


def test_get_self_review_lock_minutes_returns_configured_value(monkeypatch):
    """Registry has self_review_lock_minutes set → returns that value"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {
        "skill_configs": {"gitlab-idd": {"self_review_lock_minutes": 720}}
    })
    assert gl.get_self_review_lock_minutes() == 720.0


def test_get_self_review_lock_minutes_clamps_to_zero(monkeypatch):
    """Registry has self_review_lock_minutes=-5 → clamped to 0"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {
        "skill_configs": {"gitlab-idd": {"self_review_lock_minutes": -5}}
    })
    assert gl.get_self_review_lock_minutes() == 0.0


# ---------------------------------------------------------------------------
# get_assigned_lock_minutes
# ---------------------------------------------------------------------------

def test_get_assigned_lock_minutes_returns_default_when_not_set(monkeypatch):
    """No registry entry → returns DEFAULT_ASSIGNED_LOCK_MINUTES (1440)"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {})
    assert gl.get_assigned_lock_minutes() == 1440.0


def test_get_assigned_lock_minutes_returns_configured_value(monkeypatch):
    """Registry has assigned_lock_minutes set → returns that value"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {
        "skill_configs": {"gitlab-idd": {"assigned_lock_minutes": 480}}
    })
    assert gl.get_assigned_lock_minutes() == 480.0


def test_get_assigned_lock_minutes_clamps_to_zero(monkeypatch):
    """Registry has assigned_lock_minutes=-1 → clamped to 0"""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "_load_registry", lambda: {
        "skill_configs": {"gitlab-idd": {"assigned_lock_minutes": -1}}
    })
    assert gl.get_assigned_lock_minutes() == 0.0

# ---------------------------------------------------------------------------
# update-mr / delete-branch / get-mr-changes
# ---------------------------------------------------------------------------

def test_update_mr_closes_with_state_event(monkeypatch):
    """--state-event close → PUT sends state_event=close (unmerged close)."""
    gl = load_gl_module()
    captured = {}

    def fake_api(host, token, method, path, data=None, params=None):
        captured.update({"method": method, "path": path, "data": data})
        return {"iid": 5, "state": "closed"}

    monkeypatch.setattr(gl, "api", fake_api)
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: None)

    args = SimpleNamespace(
        mr_id=5,
        description=None,
        description_file=None,
        no_draft=False,
        state_event="close",
        get=None,
    )

    gl.cmd_update_mr(args, "gitlab.example.com", "group/project", "token")

    assert captured["method"] == "PUT"
    assert captured["path"].endswith("/merge_requests/5")
    assert captured["data"]["state_event"] == "close"


def test_update_mr_errors_when_no_fields(monkeypatch):
    """No update fields → exits with an error instead of an empty PUT."""
    gl = load_gl_module()
    monkeypatch.setattr(gl, "api", lambda *a, **k: {})
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: None)

    args = SimpleNamespace(
        mr_id=5,
        description=None,
        description_file=None,
        no_draft=False,
        state_event=None,
        get=None,
    )

    import pytest
    with pytest.raises(SystemExit):
        gl.cmd_update_mr(args, "gitlab.example.com", "group/project", "token")


def test_delete_branch_issues_delete_with_encoded_name(monkeypatch):
    """delete-branch → DELETE with the branch name URL-encoded (slashes escaped)."""
    gl = load_gl_module()
    captured = {}

    def fake_api(host, token, method, path, data=None, params=None):
        captured.update({"method": method, "path": path})
        return {}

    monkeypatch.setattr(gl, "api", fake_api)
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: None)

    args = SimpleNamespace(branch="feature/issue-42-add-login", get=None)
    gl.cmd_delete_branch(args, "gitlab.example.com", "group/project", "token")

    assert captured["method"] == "DELETE"
    assert captured["path"].endswith("/repository/branches/feature%2Fissue-42-add-login")


def test_get_mr_changes_returns_changes_list(monkeypatch):
    """get-mr-changes → returns the `changes` array from the MR changes endpoint."""
    gl = load_gl_module()
    changes = [{"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1 +1 @@"}]

    def fake_api(host, token, method, path, data=None, params=None):
        assert path.endswith("/merge_requests/9/changes")
        return {"iid": 9, "changes": changes}

    captured = {}
    monkeypatch.setattr(gl, "api", fake_api)
    monkeypatch.setattr(gl, "out", lambda obj, get_field=None: captured.setdefault("out", obj))

    args = SimpleNamespace(mr_id=9, get=None)
    gl.cmd_get_mr_changes(args, "gitlab.example.com", "group/project", "token")

    assert captured["out"] == changes


# ---------------------------------------------------------------------------
# packet ID (cross-repo, human-typeable)
# ---------------------------------------------------------------------------

def test_generate_packet_id_format_and_alphabet():
    gl = load_gl_module()
    pid = gl.generate_packet_id()
    assert pid.startswith("GK-")
    body = pid[len("GK-"):]
    assert len(body) == gl.DEFAULT_PACKET_ID_LENGTH
    # Crockford alphabet only — no ambiguous I/L/O/U
    assert all(c in gl._CROCKFORD_ALPHABET for c in body)
    assert not (set("ILOU") & set(body))


def test_generate_packet_id_respects_length_and_prefix():
    gl = load_gl_module()
    pid = gl.generate_packet_id(length=10, prefix="PKT-")
    assert pid.startswith("PKT-")
    assert len(pid[len("PKT-"):]) == 10


def test_generate_packet_id_is_random():
    gl = load_gl_module()
    ids = {gl.generate_packet_id() for _ in range(50)}
    assert len(ids) > 45  # overwhelmingly distinct


def test_normalize_packet_id_tolerates_case_prefix_and_spacing():
    gl = load_gl_module()
    assert gl.normalize_packet_id("gk 7f3 kq9") == "GK-7F3KQ9"
    assert gl.normalize_packet_id("7F3KQ9") == "GK-7F3KQ9"
    assert gl.normalize_packet_id("GK-7F3KQ9") == "GK-7F3KQ9"


def test_normalize_packet_id_maps_confusable_chars():
    gl = load_gl_module()
    # I/L → 1, O → 0, U dropped
    assert gl.normalize_packet_id("gk-il0o") == "GK-1100"


def test_cmd_gen_packet_id_prints_id(capsys):
    gl = load_gl_module()
    args = SimpleNamespace(length=gl.DEFAULT_PACKET_ID_LENGTH, prefix=gl.DEFAULT_PACKET_ID_PREFIX)
    gl.cmd_gen_packet_id(args, None, None, None)
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("GK-")
    assert len(printed) == len("GK-") + gl.DEFAULT_PACKET_ID_LENGTH
