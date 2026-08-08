#!/usr/bin/env python3
"""Phase 2A sandbox create/cleanup contract tests."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self._sandbox_root = al._SANDBOX_ROOT

    def tearDown(self):
        al._SANDBOX_ROOT = self._sandbox_root

    def test_resolve_non_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(al.resolve_git_toplevel(tmp))

    def test_create_and_clean_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # fake git repo
            (root / ".git").mkdir()
            sid = "sid1"
            wt = al.sandbox_worktree_path(sid)
            # redirect sandbox root into tmp
            resolved = root.resolve()
            al._SANDBOX_ROOT = root / "sandboxes"
            with mock.patch.object(al, "resolve_git_toplevel", return_value=resolved), \
                 mock.patch.object(al.subprocess, "run") as run:
                def fake_run(cmd, **kwargs):
                    if "worktree" in cmd and "add" in cmd:
                        Path(cmd[cmd.index("--detach") + 1]).mkdir(parents=True)
                        return mock.Mock(returncode=0, stdout="", stderr="")
                    if "status" in cmd:
                        return mock.Mock(returncode=0, stdout="", stderr="")
                    if "worktree" in cmd and "remove" in cmd:
                        p = Path(cmd[-1])
                        if p.is_dir():
                            p.rmdir()
                        return mock.Mock(returncode=0, stdout="", stderr="")
                    return mock.Mock(returncode=0, stdout=str(resolved), stderr="")

                run.side_effect = fake_run
                reg = al.create_sandbox(repo_root=resolved, request_id="r1", sandbox_id=sid)
                self.assertEqual(reg["id"], sid)
                self.assertTrue(Path(reg["path"]).is_dir())
                status = al.cleanup_sandbox(sid)
                self.assertEqual(status, "cleaned")

    def test_dirty_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al._SANDBOX_ROOT = root / "sandboxes"
            sid = "dirty1"
            wt = al.sandbox_worktree_path(sid)
            wt.mkdir(parents=True)
            al.write_sandbox_registry({
                "version": 1, "id": sid, "repo_root": str(root),
                "path": str(wt), "request_id": "r", "owner_pid": None,
                "status": "active", "created_at": 1.0, "last_used_at": 1.0,
            })
            with mock.patch.object(al, "worktree_is_clean", return_value=False):
                status = al.cleanup_sandbox(sid)
            self.assertEqual(status, "retained_dirty")
            self.assertTrue(wt.is_dir())

    def test_protected_path_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al._SANDBOX_ROOT = root / "sandboxes"
            sid = "prot"
            # point worktree at agents home → protected
            agents = al.agent_home_dir().resolve()
            al.write_sandbox_registry({
                "version": 1, "id": sid, "repo_root": str(root),
                "path": str(agents), "request_id": "r", "owner_pid": None,
                "status": "active", "created_at": 1.0, "last_used_at": 1.0,
            })
            status = al.cleanup_sandbox(sid)
            self.assertEqual(status, "cleanup_failed")

    def test_registry_cannot_redirect_cleanup_to_another_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al._SANDBOX_ROOT = root / "sandboxes"
            sid = "redirected"
            victim = root / "other-worktree"
            victim.mkdir()
            al.write_sandbox_registry({
                "version": 1, "id": sid, "repo_root": str(root),
                "path": str(victim), "request_id": "r", "owner_pid": None,
                "status": "active", "created_at": 1.0, "last_used_at": 1.0,
            })
            with mock.patch.object(al, "worktree_is_clean", return_value=True) as clean, \
                 mock.patch.object(al.subprocess, "run") as run:
                status = al.cleanup_sandbox(sid)
            self.assertEqual(status, "cleanup_failed")
            self.assertTrue(victim.is_dir())
            clean.assert_not_called()
            run.assert_not_called()

    def test_stale_dead_owner_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al._SANDBOX_ROOT = root / "sandboxes"
            sid = "stale"
            wt = al.sandbox_worktree_path(sid)
            wt.mkdir(parents=True)
            old = 1.0
            al.write_sandbox_registry({
                "version": 1, "id": sid, "repo_root": str(root),
                "path": str(wt), "request_id": "r", "owner_pid": 99999999,
                "status": "active", "created_at": old, "last_used_at": old,
            })
            with mock.patch.object(al, "_pid_alive", return_value=False), \
                 mock.patch.object(al, "worktree_is_clean", return_value=True), \
                 mock.patch.object(al, "cleanup_sandbox", return_value="cleaned") as clean:
                stats = al.cleanup_stale_sandboxes(now=old + al._SANDBOX_STALE_SECONDS + 10)
            clean.assert_called_once_with(sid)
            self.assertEqual(stats["cleaned"], 1)


if __name__ == "__main__":
    unittest.main()
