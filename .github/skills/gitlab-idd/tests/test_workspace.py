"""gitlab-idd の作業用フォルダ（gl.py workspace-create / workspace-remove）のテスト（標準ライブラリと git。unittest 形式）。

GitLab の git は手元の bare リポジトリで代える。GitLab の API は呼ばない。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GL = Path(__file__).resolve().parent.parent / "scripts" / "gl.py"


def git(cwd, *args) -> str:
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main",
                        *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "git が無い")
class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "--bare", "-q", str(self.origin))
        src = self.tmp / "src"
        git(self.tmp, "init", "-q", str(src))
        (src / "a.txt").write_text("1\n", encoding="utf-8")
        git(src, "add", ".")
        git(src, "commit", "-qm", "base")
        git(src, "push", "-q", str(self.origin), "HEAD:refs/heads/main")
        self.src = src
        self.mine = self.tmp / "mine"                    # 利用者の作業コピー（書きかけあり）
        git(self.tmp, "clone", "-q", str(self.origin), str(self.mine))
        git(self.mine, "checkout", "-qb", "my-work")
        (self.mine / "a.txt").write_text("書きかけ\n", encoding="utf-8")
        self.base = self.tmp / "ws"

    def gl(self, *args, env=None, cwd=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(GL), *args], cwd=cwd or self.mine, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              env={**os.environ, "GITLAB_IDD_LOCAL_REPO": "", **(env or {})})

    def push_main(self, text):
        (self.src / "a.txt").write_text(text, encoding="utf-8")
        git(self.src, "commit", "-qam", text.strip())
        git(self.src, "push", "-q", str(self.origin), "HEAD:refs/heads/main")

    def test_local_repo_uses_worktree_without_clone(self):
        self.push_main("2\n")                            # 作業コピーの origin/main より新しい
        r = self.gl("workspace-create", "7", "--branch", "feature/issue-7", "--target", "main",
                    "--base-dir", str(self.base), env={"GITLAB_IDD_LOCAL_REPO": str(self.mine)})
        self.assertEqual(r.returncode, 0, r.stderr)
        ws = Path(r.stdout.strip())
        self.assertTrue(ws.name.startswith("gitlab-idd-7-"))
        self.assertEqual((ws / "a.txt").read_text(encoding="utf-8"), "2\n")         # 最新の main から
        self.assertEqual(git(ws, "rev-parse", "--abbrev-ref", "HEAD"), "feature/issue-7")
        self.assertTrue((ws / ".git").is_file())                                     # クローンではなく worktree
        self.assertEqual(git(self.mine, "rev-parse", "--abbrev-ref", "HEAD"), "my-work")
        self.assertEqual((self.mine / "a.txt").read_text(encoding="utf-8"), "書きかけ\n")
        # 作業して push し、片付ける
        (ws / "b.txt").write_text("x\n", encoding="utf-8")
        git(ws, "add", ".")
        git(ws, "commit", "-qm", "work")
        git(ws, "push", "-q", "-u", "origin", "feature/issue-7")
        r = self.gl("workspace-remove", str(ws))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(ws.exists())
        self.assertEqual(git(self.mine, "worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertEqual(git(self.mine, "branch", "--list", "feature/issue-7"), "")  # 手元のブランチも外す
        self.assertIn("feature/issue-7", git(self.origin, "branch", "--list"))        # リモートには残る

    def test_without_local_repo_clones_into_temp(self):
        r = self.gl("workspace-create", "8", "--branch", "feature/issue-8", "--target", "main",
                    "--base-dir", str(self.base))
        self.assertEqual(r.returncode, 0, r.stderr)
        ws = Path(r.stdout.strip())
        self.assertTrue((ws / ".git").is_dir())
        self.assertEqual(git(ws, "rev-parse", "--abbrev-ref", "HEAD"), "feature/issue-8")
        self.assertEqual(self.gl("workspace-remove", str(ws)).returncode, 0)
        self.assertFalse(ws.exists())

    def test_remove_refuses_other_folders(self):
        r = self.gl("workspace-remove", str(self.mine))
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.mine / "a.txt").exists())

    def test_bad_local_repo(self):
        r = self.gl("workspace-create", "9", "--branch", "b", "--target", "main", "--base-dir", str(self.base),
                    env={"GITLAB_IDD_LOCAL_REPO": str(self.tmp)})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a git repository", r.stderr)
        self.assertEqual(list(self.base.iterdir()), [])                  # 空のフォルダを残さない


if __name__ == "__main__":
    unittest.main()
