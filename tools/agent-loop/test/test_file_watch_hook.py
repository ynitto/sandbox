#!/usr/bin/env python3
"""File watch hook tests."""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
sys.path.insert(0, str(HOOKS))


def _load_hook():
    path = HOOKS / "file-watch-hook.py"
    spec = importlib.util.spec_from_file_location("file_watch_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FileWatchHookTests(unittest.TestCase):
    def test_baseline_then_detect_changes(self):
        hook = _load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "a.py").write_text("one", encoding="utf-8")
            state_root = root / "state"
            cfg = {
                "entry_id": "fw-1",
                "cwd": str(root),
                "event_hook_config": {
                    "watch_dirs": ["src"],
                    "patterns": ["**/*.py"],
                    "ignore_patterns": ["**/__pycache__/**"],
                    "max_files": 100,
                },
            }
            with mock.patch.object(hook, "hook_state_dir", return_value=state_root):
                first = hook.check(cfg)
                self.assertIsNone(first)

                (src / "b.py").write_text("two", encoding="utf-8")
                second = hook.check(cfg)
                self.assertIsNotNone(second)
                self.assertEqual(second["vars"]["added"], ["src/b.py"])
                self.assertEqual(second["vars"]["change_count"], 1)

                retried = hook.check(cfg)
                self.assertEqual(retried["vars"]["added"], ["src/b.py"])

                hook.ack()
                third = hook.check(cfg)
                self.assertIsNone(third)

                (src / "a.py").write_text("changed", encoding="utf-8")
                fourth = hook.check(cfg)
                self.assertEqual(fourth["vars"]["changed"], ["src/a.py"])

    def test_ignore_git_and_symlink_dir(self):
        hook = _load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "keep.py").write_text("x", encoding="utf-8")
            link_target = root / "real"
            link_target.mkdir()
            (link_target / "linked.py").write_text("y", encoding="utf-8")
            (src / "linked").symlink_to(link_target, target_is_directory=True)

            state_root = root / "state"
            cfg = {
                "entry_id": "fw-2",
                "cwd": str(root),
                "event_hook_config": {"watch_dirs": ["src"], "patterns": ["**/*.py"]},
            }
            with mock.patch.object(hook, "hook_state_dir", return_value=state_root):
                baseline = hook.check(cfg)
                self.assertIsNone(baseline)
                snap = hook.load_json(state_root / hook.STATE_FILE_NAME, {})
                paths = set((snap.get("snapshot") or {}).keys())
            self.assertIn("src/keep.py", paths)
            self.assertNotIn("src/linked/linked.py", paths)
            self.assertTrue(all(".git" not in p for p in paths))

    def test_deleted_file_detected(self):
        hook = _load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            src = root / "src"
            src.mkdir()
            target = src / "gone.py"
            target.write_text("x", encoding="utf-8")
            state_root = root / "state"
            cfg = {"entry_id": "fw-del", "cwd": str(root), "event_hook_config": {"watch_dirs": ["src"]}}
            with mock.patch.object(hook, "hook_state_dir", return_value=state_root):
                hook.check(cfg)
                target.unlink()
                result = hook.check(cfg)
            self.assertEqual(result["vars"]["deleted"], ["src/gone.py"])

    def test_max_files_aborts_without_delivery(self):
        hook = _load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            src = root / "src"
            src.mkdir()
            for i in range(5):
                (src / f"f{i}.py").write_text("x", encoding="utf-8")
            state_root = root / "state"
            cfg = {
                "entry_id": "fw-3",
                "cwd": str(root),
                "event_hook_config": {
                    "watch_dirs": ["src"],
                    "patterns": ["**/*.py"],
                    "max_files": 2,
                },
            }
            with mock.patch.object(hook, "hook_state_dir", return_value=state_root):
                self.assertIsNone(hook.check(cfg))
                (src / "f0.py").write_text("changed", encoding="utf-8")
                self.assertIsNone(hook.check(cfg))


if __name__ == "__main__":
    unittest.main()
