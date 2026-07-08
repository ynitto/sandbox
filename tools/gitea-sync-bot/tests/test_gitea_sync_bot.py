#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gitea-sync-bot のテスト。

- 判定コア（decide_action / ref_in_scope）を純粋関数として網羅。
- reconcile_ref を「Gitea/GitLab を模した 2 つのローカル bare repo」で end-to-end 検証
  （fast-forward の双方向 / allowlist 除外 / 分岐時に GitLab へ push しない）。

依存は stdlib と git のみ。実行: python3 -m pytest（または python3 tests/test_gitea_sync_bot.py）。
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gitea_sync_bot as bot  # noqa: E402


# --------------------------------------------------------------------------- #
# 純粋関数
# --------------------------------------------------------------------------- #

class TestDecideAction(unittest.TestCase):
    def test_noop_when_equal(self):
        self.assertEqual(bot.decide_action("a" * 40, "a" * 40, "a" * 40), bot.NOOP)

    def test_gitea_only_creates_on_gitlab(self):
        self.assertEqual(bot.decide_action("a" * 40, None, None), bot.CREATE_ON_GITLAB)

    def test_gitlab_only_creates_on_gitea(self):
        self.assertEqual(bot.decide_action(None, "b" * 40, None), bot.CREATE_ON_GITEA)

    def test_gitea_ahead_ff_to_gitlab(self):
        g, l = "a" * 40, "b" * 40
        # merge-base == gitlab_sha なら GitLab は Gitea の祖先 = Gitea が進行
        self.assertEqual(bot.decide_action(g, l, l), bot.PUSH_FF_TO_GITLAB)

    def test_gitlab_ahead_ff_to_gitea(self):
        g, l = "a" * 40, "b" * 40
        self.assertEqual(bot.decide_action(g, l, g), bot.PUSH_FF_TO_GITEA)

    def test_diverged(self):
        g, l, base = "a" * 40, "b" * 40, "c" * 40
        self.assertEqual(bot.decide_action(g, l, base), bot.DIVERGED)


class TestRefScope(unittest.TestCase):
    def setUp(self):
        self.inc = ["refs/heads/main", "refs/heads/release/*", "refs/tags/*"]
        self.exc = ["refs/heads/feature/*", "refs/heads/sync/*"]

    def test_main_in_scope(self):
        self.assertTrue(bot.ref_in_scope("refs/heads/main", self.inc, self.exc))

    def test_release_in_scope(self):
        self.assertTrue(bot.ref_in_scope("refs/heads/release/1.0", self.inc, self.exc))

    def test_feature_excluded(self):
        # Gitea 発の feature ブランチは対象外（§3.6 の核心）
        self.assertFalse(bot.ref_in_scope("refs/heads/feature/x", self.inc, self.exc))

    def test_integration_branch_excluded(self):
        self.assertFalse(bot.ref_in_scope("refs/heads/sync/integrate-1", self.inc, self.exc))

    def test_exclude_wins_over_include(self):
        inc = ["refs/heads/*"]
        exc = ["refs/heads/feature/*"]
        self.assertTrue(bot.ref_in_scope("refs/heads/main", inc, exc))
        self.assertFalse(bot.ref_in_scope("refs/heads/feature/x", inc, exc))


# --------------------------------------------------------------------------- #
# end-to-end（ローカル git で Gitea/GitLab を模す）
# --------------------------------------------------------------------------- #

def git(args, cwd, check=True, env=None):
    e = dict(os.environ)
    e.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    })
    if env:
        e.update(env)
    return subprocess.run(["git"] + args, cwd=cwd, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e)


def head(bare, ref="refs/heads/main"):
    out = git(["ls-remote", bare, ref], cwd=".", check=False).stdout.strip()
    return out.split()[0] if out else None


class TestReconcileE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Gitea/GitLab を模した bare repo
        self.gitea = os.path.join(self.tmp, "gitea.git")
        self.gitlab = os.path.join(self.tmp, "gitlab.git")
        git(["init", "-q", "--bare", "-b", "main", self.gitea], cwd=self.tmp)
        git(["init", "-q", "--bare", "-b", "main", self.gitlab], cwd=self.tmp)
        # 作業クローンから両方へ同じ初期コミットを入れる（共通祖先）
        self.work = os.path.join(self.tmp, "work")
        git(["clone", "-q", self.gitea, self.work], cwd=self.tmp)
        self._commit("init")
        git(["push", "-q", "origin", "main"], cwd=self.work)
        git(["push", "-q", self.gitlab, "main"], cwd=self.work)

        self.cfg = bot.Config(
            include=["refs/heads/main", "refs/heads/release/*"],
            exclude=["refs/heads/feature/*", "refs/heads/sync/*"],
            state_dir=os.path.join(self.tmp, "state"),
        )
        self.repo = bot.RepoConfig(name="p", workdir=os.path.join(self.tmp, "mirror"),
                                   gitea_url=self.gitea, gitlab_url=self.gitlab)
        self.cfg.repos.append(self.repo)
        self.state = bot.State(self.cfg.state_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _commit(self, msg, branch="main"):
        git(["checkout", "-q", "-B", branch], cwd=self.work)
        with open(os.path.join(self.work, f"{msg}.txt"), "w") as f:
            f.write(msg)
        git(["add", "-A"], cwd=self.work)
        git(["commit", "-q", "-m", msg], cwd=self.work)

    def _mirror(self):
        m = bot.LocalMirror(self.repo)
        m.ensure()
        return m

    def test_gitea_ahead_pushes_to_gitlab(self):
        # Gitea を 1 コミット進める
        self._commit("feat-a")
        git(["push", "-q", self.gitea, "main"], cwd=self.work)
        before = head(self.gitlab)
        action = bot.reconcile_ref(self._mirror(), self.cfg, self.state, "refs/heads/main")
        self.assertEqual(action, bot.PUSH_FF_TO_GITLAB)
        self.assertNotEqual(head(self.gitlab), before)
        self.assertEqual(head(self.gitea), head(self.gitlab))

    def test_gitlab_ahead_pushes_to_gitea(self):
        # GitLab を 1 コミット進める（双方向の逆方向）
        self._commit("feat-b")
        git(["push", "-q", self.gitlab, "main"], cwd=self.work)
        # work を gitea の HEAD に戻しておく（gitea は init のまま）
        action = bot.reconcile_ref(self._mirror(), self.cfg, self.state, "refs/heads/main")
        self.assertEqual(action, bot.PUSH_FF_TO_GITEA)
        self.assertEqual(head(self.gitea), head(self.gitlab))

    def test_diverged_does_not_push_to_gitlab(self):
        # Gitea と GitLab を別々に進めて分岐させる
        self._commit("gitea-side")
        git(["push", "-q", self.gitea, "main"], cwd=self.work)
        gitlab_before = head(self.gitlab)
        # 別コミットを GitLab へ
        git(["reset", "-q", "--hard", "HEAD~1"], cwd=self.work)
        self._commit("gitlab-side")
        git(["push", "-q", "-f", self.gitlab, "main"], cwd=self.work)
        gitlab_before = head(self.gitlab)

        action = bot.reconcile_ref(self._mirror(), self.cfg, self.state, "refs/heads/main")
        self.assertEqual(action, bot.DIVERGED)
        # 分岐時は GitLab の main を絶対に動かさない（--force しない）
        self.assertEqual(head(self.gitlab), gitlab_before)
        # 統合ブランチが Gitea 側に作られている（sync/* は allowlist 外なので GitLab へは伝播しない）
        integ = [l for l in git(["ls-remote", self.gitea], cwd=".").stdout.splitlines()
                 if "refs/heads/sync/integrate" in l]
        self.assertTrue(integ, "統合ブランチが Gitea に作成されていない")

    def test_feature_branch_not_pushed_to_gitlab(self):
        # Gitea で feature ブランチを作成 → GitLab へ push されないこと（§3.6）
        self._commit("wip", branch="feature/x")
        git(["push", "-q", self.gitea, "feature/x"], cwd=self.work)
        bot.sync_repo(self.cfg, self.repo, self.state)
        self.assertIsNone(head(self.gitlab, "refs/heads/feature/x"),
                          "feature ブランチが GitLab へ push されてしまった")


if __name__ == "__main__":
    unittest.main(verbosity=2)
