from __future__ import annotations

import importlib.util
import sys
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