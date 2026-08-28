"""証跡ゲートの git 差分観測（設計 2026-08-27 §7.3 B 末尾 / 実装計画 段 9b）。

証跡が答えていたのは「受入条件が名指ししたパスの指紋が変わったか」だけだった。
**宣言外のファイルを勝手に触ったこと**に要るのはその補集合——名指ししていないのに
変わったファイル——なので、指紋からは原理的に出てこない。ペインにもヘッドレスにも
同じく効いていた制約である。

ここが見るのは 4 つ。①git 管理下で宣言外の変更が `touched` に出ること、②受入条件の
指紋は残ること（git 管理外・未追跡のために要る）、③**非 git では現行どおり指紋だけへ
落ちること**（後方互換）、④git を使うのは engine 側で、エージェントには渡さないこと。

LLM もエージェント CLI も起こさない。ファイルと git の状態だけで決まる観測である。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore.harness import toolloop  # noqa: E402


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _Repo:
    """コミット済みの `out.md` を 1 枚持つ git リポジトリ。"""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        _git("init", "-q", cwd=self.dir)
        _git("config", "user.email", "t@example.com", cwd=self.dir)
        _git("config", "user.name", "t", cwd=self.dir)
        (Path(self.dir) / "out.md").write_text("前\n", encoding="utf-8")
        (Path(self.dir) / "other.md").write_text("触っていない\n", encoding="utf-8")
        _git("add", "-A", cwd=self.dir)
        _git("commit", "-qm", "seed", cwd=self.dir)
        return self

    def write(self, name, body):
        (Path(self.dir) / name).write_text(body, encoding="utf-8")

    def __exit__(self, *_exc):
        self._tmp.cleanup()


class GitDiffWidensTouchedTests(unittest.TestCase):
    CRITERIA = ["`out.md` が更新されている"]

    def test_a_file_outside_the_criteria_shows_up_in_touched(self):
        """受入条件その 1。宣言していないファイルの変更が観測できる。"""
        with _Repo() as repo:
            before = toolloop.acceptance_stamps(self.CRITERIA, repo.dir)
            git_before = toolloop.git_snapshot(repo.dir)
            self.assertEqual(git_before, {}, "実行前はきれい")
            repo.write("out.md", "後\n")
            repo.write("other.md", "勝手に触った\n")
            got = toolloop.acceptance_outcome(self.CRITERIA, cwd=repo.dir,
                                              stamps_before=before, git_before=git_before)
        names = {os.path.basename(f) for f in got["files"]}
        self.assertEqual(names, {"out.md", "other.md"},
                         "宣言外の other.md も触ったものとして出る")
        self.assertTrue(got["ok"], "観測を広げただけで、判定そのものは変えない")

    def test_a_new_untracked_file_counts_as_touched(self):
        with _Repo() as repo:
            before = toolloop.acceptance_stamps(self.CRITERIA, repo.dir)
            git_before = toolloop.git_snapshot(repo.dir)
            repo.write("out.md", "後\n")
            repo.write("new.md", "作った\n")
            got = toolloop.acceptance_outcome(self.CRITERIA, cwd=repo.dir,
                                              stamps_before=before, git_before=git_before)
        self.assertIn("new.md", {os.path.basename(f) for f in got["files"]})

    def test_a_file_already_dirty_before_the_run_is_not_counted(self):
        """実行前から汚れていたものは、この実行が触った証拠にならない。"""
        with _Repo() as repo:
            repo.write("other.md", "実行前から汚れている\n")
            before = toolloop.acceptance_stamps(self.CRITERIA, repo.dir)
            git_before = toolloop.git_snapshot(repo.dir)
            self.assertIn("other.md", git_before)
            repo.write("out.md", "後\n")
            got = toolloop.acceptance_outcome(self.CRITERIA, cwd=repo.dir,
                                              stamps_before=before, git_before=git_before)
        self.assertEqual({os.path.basename(f) for f in got["files"]}, {"out.md"})

    def test_the_fingerprint_still_decides_the_criteria(self):
        """受入条件その 2。指紋は残す——git 差分だけにすると管理外で何も見えなくなる。"""
        with _Repo() as repo:
            before = toolloop.acceptance_stamps(self.CRITERIA, repo.dir)
            git_before = toolloop.git_snapshot(repo.dir)
            repo.write("other.md", "宣言外だけ触った\n")
            got = toolloop.acceptance_outcome(self.CRITERIA, cwd=repo.dir,
                                              stamps_before=before, git_before=git_before)
        self.assertFalse(got["ok"], "名指しした out.md が変わっていないので fail のまま")
        self.assertTrue(any("out.md" in e for e in got["evidenceErrors"]))


    def test_a_staged_rename_counts_both_paths(self):
        """改名は新旧どちらも触ったファイルである（元パスからは消えている）。

        `--porcelain -z` の改名だけは 1 件が 2 欄になる。欄を読み飛ばすと元パスが
        観測から落ちる。
        """
        with _Repo() as repo:
            before = toolloop.acceptance_stamps(self.CRITERIA, repo.dir)
            git_before = toolloop.git_snapshot(repo.dir)
            repo.write("out.md", "後\n")
            _git("mv", "other.md", "renamed.md", cwd=repo.dir)
            got = toolloop.acceptance_outcome(self.CRITERIA, cwd=repo.dir,
                                              stamps_before=before, git_before=git_before)
        names = {os.path.basename(f) for f in got["files"]}
        self.assertEqual(names, {"out.md", "other.md", "renamed.md"})


class OutsideGitItFallsBackToFingerprintsTests(unittest.TestCase):
    """受入条件その 3。非 git では現行どおり（後方互換）。"""

    CRITERIA = ["`out.md` が更新されている"]

    def test_the_snapshot_is_none_not_empty(self):
        """None と空辞書は違う。空は「きれい」、None は「差分では見られない」。"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(toolloop.git_snapshot(tmp))
            self.assertEqual(toolloop.git_touched_since(tmp, None), set())

    def test_only_the_declared_paths_are_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "out.md").write_text("前\n", encoding="utf-8")
            before = toolloop.acceptance_stamps(self.CRITERIA, tmp)
            git_before = toolloop.git_snapshot(tmp)
            (Path(tmp) / "out.md").write_text("後\n", encoding="utf-8")
            (Path(tmp) / "other.md").write_text("宣言外\n", encoding="utf-8")
            got = toolloop.acceptance_outcome(self.CRITERIA, cwd=tmp,
                                              stamps_before=before, git_before=git_before)
        self.assertEqual({os.path.basename(f) for f in got["files"]}, {"out.md"})
        self.assertTrue(got["ok"])


class GitStaysOnTheEngineSideTests(unittest.TestCase):
    def test_the_aider_definition_still_refuses_git(self):
        """git を使うのは engine 側で、エージェントには渡さない（設計 §7.3 B）。

        コミットの主体が aider になると、agent-loop の worktree サンドボックスや
        agent-project のブランチ運用と二重になる。
        """
        from agentcore import agentcli
        argv = agentcli.load_cli("aider").get("command") or []
        self.assertIn("--no-git", argv)
        self.assertIn("--no-auto-commits", argv)


if __name__ == "__main__":
    unittest.main()
