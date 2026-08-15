#!/usr/bin/env python3
"""ワークスペース finalize の差分品質チェック（空白の自動修正）。"""
from _shared import *  # noqa: F401,F403


def _init_repo(root: str, files: "dict[str, str]") -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for rel, body in files.items():
        p = pathlib.Path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, newline="")
    for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
              ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(c, cwd=root, env=env, check=True, capture_output=True)
    return root


class FixStagedWhitespaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _stage(self, clone, rel, body):
        pathlib.Path(clone, rel).write_text(body, newline="")
        subprocess.run(["git", "add", "-A"], cwd=clone, check=True, capture_output=True)

    def _check(self, clone):
        return subprocess.run(["git", "-C", clone, "diff", "--cached", "--check"],
                              capture_output=True, text=True).returncode

    def test_fixes_only_the_lines_git_reported(self):
        # 既にコミット済みの行末空白（node が触っていない）は残す＝差分を広げない。
        clone = _init_repo(os.path.join(self.tmp, "repo"), {"a.js": "old   \nkeep\n"})
        self._stage(clone, "a.js", "old   \nkeep\nadded   \n")
        self.assertNotEqual(self._check(clone), 0)

        fixed = kf._fix_staged_whitespace(clone)

        self.assertEqual(fixed, ["a.js"])
        self.assertEqual(pathlib.Path(clone, "a.js").read_text(), "old   \nkeep\nadded\n")
        subprocess.run(["git", "-C", clone, "add", "--", *fixed], check=True, capture_output=True)
        self.assertEqual(self._check(clone), 0)

    def test_fixes_space_before_tab_and_eof_blank_lines(self):
        clone = _init_repo(os.path.join(self.tmp, "repo"), {"seed.txt": "seed\n"})
        self._stage(clone, "b.py", "def f():\n \treturn 1\n\n\n")
        self.assertNotEqual(self._check(clone), 0)

        kf._fix_staged_whitespace(clone)

        self.assertEqual(pathlib.Path(clone, "b.py").read_text(), "def f():\n\treturn 1\n")
        subprocess.run(["git", "-C", clone, "add", "-A"], check=True, capture_output=True)
        self.assertEqual(self._check(clone), 0)

    def test_converts_touched_crlf_lines_to_lf(self):
        # git は CR も行末空白として弾く。CR を残すと直したつもりで再検査に落ちる。
        clone = _init_repo(os.path.join(self.tmp, "repo"), {"seed.txt": "seed\n"})
        self._stage(clone, "c.txt", "one  \r\ntwo\r\n")

        kf._fix_staged_whitespace(clone)

        with open(os.path.join(clone, "c.txt"), encoding="utf-8", newline="") as fh:
            self.assertEqual(fh.read(), "one\ntwo\n")
        subprocess.run(["git", "-C", clone, "add", "-A"], check=True, capture_output=True)
        self.assertEqual(self._check(clone), 0)

    def test_clean_diff_is_untouched(self):
        clone = _init_repo(os.path.join(self.tmp, "repo"), {"seed.txt": "seed\n"})
        self._stage(clone, "d.txt", "fine\n")
        self.assertEqual(kf._fix_staged_whitespace(clone), [])


class WorkspacePublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(kf.cleanup_workspace)

    def test_failed_remote_push_keeps_a_local_recovery_ref(self):
        local = _init_repo(os.path.join(self.tmp, "local"), {"seed.txt": "seed\n"})
        remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        subprocess.run(["git", "-C", local, "remote", "add", "origin", remote], check=True)
        subprocess.run(["git", "-C", local, "push", "-q", "-u", "origin", "main"], check=True)
        hook = pathlib.Path(remote, "hooks", "pre-receive")
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        run_id = "recover-push"
        ws = kf.ensure_workspace_clone(
            {"url": remote, "local": local, "base": "main", "path": "", "desc": "test"},
            run_id,
        )
        pathlib.Path(ws["clone"], "result.txt").write_text("result\n")

        with self.assertRaises(kf.WorkspacePublishError) as raised:
            kf.finalize_workspace(ws, run_id, "work")

        publication = raised.exception.data["publication"]
        self.assertEqual(publication["state"], "failed")
        self.assertEqual(publication["branch"], f"af/{run_id}")
        self.assertEqual(publication["recovery"]["repository"], local)
        self.assertEqual(publication["recovery"]["ref"], f"refs/agent-flow/recovery/{run_id}")
        recovery = subprocess.run(
            ["git", "-C", local, "rev-parse", "--verify", f"refs/agent-flow/recovery/{run_id}"],
            capture_output=True, text=True,
        )
        self.assertEqual(recovery.returncode, 0, recovery.stderr)

    def test_successful_remote_push_records_publication_and_removes_recovery_ref(self):
        local = _init_repo(os.path.join(self.tmp, "local-ok"), {"seed.txt": "seed\n"})
        remote = os.path.join(self.tmp, "remote-ok.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        subprocess.run(["git", "-C", local, "remote", "add", "origin", remote], check=True)
        subprocess.run(["git", "-C", local, "push", "-q", "-u", "origin", "main"], check=True)
        run_id = "publish-ok"
        ws = kf.ensure_workspace_clone(
            {"url": remote, "local": local, "base": "main", "path": "", "desc": "test"},
            run_id,
        )
        pathlib.Path(ws["clone"], "result.txt").write_text("result\n")

        delivery = kf.finalize_workspace(ws, run_id, "work")

        self.assertEqual(delivery["publication"]["state"], "published")
        self.assertEqual(delivery["publication"]["commit"], delivery["commit"])
        recovery = subprocess.run(
            ["git", "-C", local, "rev-parse", "--verify", f"refs/agent-flow/recovery/{run_id}"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(recovery.returncode, 0)


if __name__ == "__main__":
    unittest.main()
