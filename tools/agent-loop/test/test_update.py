"""update / version（Phase 2C-2/2C-3）の contract テスト。"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


def _write_zipapp(path: Path, *, build_info: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "__main__.py",
            "from agent_loop import main\n\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n",
        )
        zf.writestr("agent_loop/__init__.py", "def main():\n    return 0\n")
        if build_info is not None:
            zf.writestr("build-info.json", json.dumps(build_info) + "\n")


class VersionTests(unittest.TestCase):
    def test_version_from_build_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agent-loop"
            _write_zipapp(
                exe,
                build_info={"commit": "abc123def456", "remote": "https://example/repo.git"},
            )
            self.assertEqual(al.get_version(exe), "abc123def456")

    def test_source_version_dev_without_git(self):
        with mock.patch.object(al, "_source_version", return_value="dev"):
            self.assertEqual(al.get_version(Path("/tmp/not-a-zipapp")), "dev")


class ZipappDetectionTests(unittest.TestCase):
    def test_rejects_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(al.is_zipapp_install(Path(tmp)))

    def test_rejects_zip_without_build_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agent-loop"
            _write_zipapp(exe, build_info=None)
            self.assertFalse(al.is_zipapp_install(exe))

    def test_accepts_zip_with_build_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agent-loop"
            _write_zipapp(exe, build_info={"commit": "deadbeef", "remote": "https://x/y.git"})
            self.assertTrue(al.is_zipapp_install(exe))


class CmdUpdateTests(unittest.TestCase):
    def test_symlink_invocation_is_rejected_before_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "agent-loop-real"
            link = Path(tmp) / "agent-loop"
            _write_zipapp(target, build_info={"commit": "old", "remote": "https://x/y.git"})
            link.symlink_to(target)
            with mock.patch.object(sys, "argv", [str(link)]), \
                 mock.patch.object(al, "is_zipapp_install", return_value=False) as is_install:
                with self.assertRaises(SystemExit) as cm:
                    al.cmd_update(SimpleNamespace())
            self.assertEqual(cm.exception.code, 1)
            is_install.assert_not_called()

    def test_source_checkout_rejected(self):
        args = SimpleNamespace()
        with mock.patch.object(al, "resolve_executable_path", return_value=Path(__file__)), \
             mock.patch.object(al, "is_zipapp_install", return_value=False), \
             mock.patch.object(sys, "exit", side_effect=SystemExit(1)) as exit_mock:
            with self.assertRaises(SystemExit):
                al.cmd_update(args)
        exit_mock.assert_called_once_with(1)

    def test_same_commit_exits_zero_without_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agent-loop"
            info = {
                "commit": "samecommit0001",
                "remote": "https://example/repo.git",
                "branch": "main",
            }
            _write_zipapp(exe, build_info=info)
            before = hashlib.sha256(exe.read_bytes()).hexdigest()
            args = SimpleNamespace()
            with mock.patch.object(al, "resolve_executable_path", return_value=exe), \
                 mock.patch.object(al, "is_zipapp_install", return_value=True), \
                 mock.patch.object(al, "read_build_info", return_value=info), \
                 mock.patch.object(al, "acquire_update_lock", return_value=mock.Mock()), \
                 mock.patch.object(al, "release_update_lock"), \
                 mock.patch.object(al, "_remote_commit", return_value="samecommit0001"), \
                 mock.patch.object(al, "_replace_executable") as replace_mock, \
                 mock.patch.object(sys, "exit", side_effect=SystemExit) as exit_mock:
                with self.assertRaises(SystemExit):
                    al.cmd_update(args)
            replace_mock.assert_not_called()
            exit_mock.assert_called_once_with(0)
            after = hashlib.sha256(exe.read_bytes()).hexdigest()
            self.assertEqual(before, after)

    def test_candidate_verify_failure_keeps_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agent-loop"
            info = {
                "commit": "oldcommit00001",
                "remote": "https://example/repo.git",
                "branch": "main",
            }
            _write_zipapp(exe, build_info=info)
            before = hashlib.sha256(exe.read_bytes()).hexdigest()
            args = SimpleNamespace()
            with mock.patch.object(al, "resolve_executable_path", return_value=exe), \
                 mock.patch.object(al, "is_zipapp_install", return_value=True), \
                 mock.patch.object(al, "read_build_info", return_value=info), \
                 mock.patch.object(al, "acquire_update_lock", return_value=mock.Mock()), \
                 mock.patch.object(al, "release_update_lock"), \
                 mock.patch.object(al, "_remote_commit", return_value="newcommit00001"), \
                 mock.patch.object(al, "_sparse_checkout", return_value="newcommit00001"), \
                 mock.patch.object(al, "_build_zipapp_candidate"), \
                 mock.patch.object(al, "verify_update_candidate", side_effect=RuntimeError("bad candidate")), \
                 mock.patch.object(al, "_replace_executable") as replace_mock, \
                 mock.patch.object(sys, "exit", side_effect=SystemExit(1)):
                with self.assertRaises(SystemExit):
                    al.cmd_update(args)
            replace_mock.assert_not_called()
            after = hashlib.sha256(exe.read_bytes()).hexdigest()
            self.assertEqual(before, after)

    def test_candidate_is_created_in_private_temp_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agent-loop"
            info = {"commit": "old", "remote": "https://example/repo.git", "branch": "main"}
            _write_zipapp(exe, build_info=info)
            captured = []

            def capture_candidate(_checkout, candidate, **_kwargs):
                captured.append(Path(candidate))
                Path(candidate).write_bytes(b"candidate")

            with mock.patch.object(al, "resolve_executable_path", return_value=exe), \
                 mock.patch.object(al, "is_zipapp_install", return_value=True), \
                 mock.patch.object(al, "read_build_info", return_value=info), \
                 mock.patch.object(al, "acquire_update_lock", return_value=mock.Mock()), \
                 mock.patch.object(al, "release_update_lock"), \
                 mock.patch.object(al, "_remote_commit", return_value="new"), \
                 mock.patch.object(al, "_sparse_checkout", return_value="new"), \
                 mock.patch.object(al, "_build_zipapp_candidate", side_effect=capture_candidate), \
                 mock.patch.object(al, "verify_update_candidate"), \
                 mock.patch.object(al, "_replace_executable"), \
                 mock.patch.object(sys, "exit", side_effect=SystemExit(0)):
                with self.assertRaises(SystemExit):
                    al.cmd_update(SimpleNamespace())
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].name, "agent-loop.pyz")
            self.assertFalse(captured[0].parent.exists())


if __name__ == "__main__":
    unittest.main()
