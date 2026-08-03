#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同期の end-to-end テスト。

ローカルの bare リポジトリを「両側の GitLab」に見立てて、実際の git で
fetch / push まで通す（ネットワークは不要）。検証したいのは次の 4 点:

  - fast-forward できるものだけが双方向に流れること
  - 分岐・タグ付け替え・戦略外の ref では**どちらのリモートも動かない**こと
  - 同じリポジトリが複数のペアに出てきても clone（共有ストア）が 1 つで済むこと
  - 複数ペアが同じ ref を別内容で書こうとしたら、両方止まること
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_repo_sync import planner, runner  # noqa: E402
from gitlab_repo_sync.config import build_config, repo_slug  # noqa: E402
from gitlab_repo_sync.util import Lock, Logger  # noqa: E402

GIT_ENV = dict(os.environ, **{
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
})


def git(args, cwd):
    proc = subprocess.run(["git"] + args, cwd=cwd, env=GIT_ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), proc.stderr))
    return proc.stdout.strip()


class SyncTestCase(unittest.TestCase):
    """bare リポジトリと作業コピーを用意する共通の土台。"""

    remotes = ("a", "b")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="grs-")
        self.remote = {}
        for name in self.remotes:
            path = os.path.join(self.tmp, "%s.git" % name)
            os.makedirs(path)
            git(["init", "--bare", "-q", "-b", "main", path], cwd=self.tmp)
            self.remote[name] = path

        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        git(["init", "-q", "-b", "main", "."], cwd=self.work)
        self.commit("first")
        self.push("a")

        self.log = Logger("", [], quiet=True)
        self.cfg = self.make_config()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 設定 -------------------------------------------------------------- #

    def pair_specs(self):
        return [{"name": "app", "a": self.remote["a"], "b": self.remote["b"]}]

    def make_config(self, **overrides):
        data = {
            "store_dir": os.path.join(self.tmp, "store"),
            "state_dir": os.path.join(self.tmp, "state"),
            "defaults": {"include": ["refs/heads/main", "refs/heads/release/*", "refs/tags/*"],
                         "exclude": ["refs/heads/tmp/*"]},
            "pairs": self.pair_specs(),
        }
        data.update(overrides)
        return build_config(data)

    # -- git 操作 ---------------------------------------------------------- #

    def commit(self, message):
        with open(os.path.join(self.work, "%s.txt" % message), "w", encoding="utf-8") as f:
            f.write(message)
        git(["add", "-A"], cwd=self.work)
        git(["commit", "-q", "-m", message], cwd=self.work)
        return git(["rev-parse", "HEAD"], cwd=self.work)

    def push(self, remote, refspec="main:refs/heads/main", force=False):
        args = ["push", "-q"] + (["--force"] if force else [])
        git(args + [self.remote[remote], refspec], cwd=self.work)

    def head(self, remote, ref="refs/heads/main"):
        out = git(["ls-remote", self.remote[remote], ref], cwd=self.tmp)
        return out.split()[0] if out else None

    def reset_to(self, sha):
        git(["reset", "-q", "--hard", sha], cwd=self.work)

    # -- 実行 -------------------------------------------------------------- #

    def run_sync(self, **kwargs):
        code = runner.run(self.cfg, self.log, **kwargs)
        return code, runner.read_json(
            os.path.join(self.cfg.state_dir, "last-report.json")).get("pairs", [])

    def report_for(self, reports, name):
        for report in reports:
            if report["pair"] == name:
                return report
        raise AssertionError("レポートに %s がありません: %s" % (name, reports))


class BidirectionalTest(SyncTestCase):
    """既定の双方向 fast-forward。"""

    def test_branch_present_on_one_side_is_created_on_the_other(self):
        code, reports = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(self.report_for(reports, "app")["status"], "ok")
        self.assertEqual(self.head("b"), self.head("a"))

    def test_a_ahead_fast_forwards_b(self):
        self.run_sync()
        new_head = self.commit("second")
        self.push("a")
        self.run_sync()
        self.assertEqual(self.head("b"), new_head)

    def test_b_ahead_fast_forwards_a(self):
        self.run_sync()
        new_head = self.commit("second")
        self.push("b")
        self.run_sync()
        self.assertEqual(self.head("a"), new_head)

    def test_second_run_without_changes_transfers_nothing(self):
        self.run_sync()
        _, reports = self.run_sync()
        self.assertEqual(self.report_for(reports, "app")["status"], "unchanged")

    def test_diverged_leaves_both_sides_untouched(self):
        self.run_sync()
        base = self.head("a")

        a_head = self.commit("a-side")
        self.push("a")
        self.reset_to(base)
        b_head = self.commit("b-side")
        self.push("b")

        code, reports = self.run_sync()
        report = self.report_for(reports, "app")
        self.assertEqual(code, 0)                       # 既定では終了コードを立てない
        self.assertEqual(report["status"], "diverged")
        self.assertEqual(report["diverged"], ["refs/heads/main"])
        self.assertEqual(self.head("a"), a_head)
        self.assertEqual(self.head("b"), b_head)

    def test_diverged_is_reported_on_every_run_until_resolved(self):
        self.test_diverged_leaves_both_sides_untouched()
        _, reports = self.run_sync()
        self.assertEqual(self.report_for(reports, "app")["diverged"], ["refs/heads/main"])

    def test_fail_on_diverged_sets_the_exit_code(self):
        self.cfg.fail_on_diverged = True
        self.run_sync()
        base = self.head("a")
        self.commit("a-side")
        self.push("a")
        self.reset_to(base)
        self.commit("b-side")
        self.push("b")
        code, _ = self.run_sync()
        self.assertEqual(code, 2)

    def test_out_of_scope_branch_is_not_propagated(self):
        self.run_sync()
        git(["checkout", "-q", "-b", "tmp/scratch"], cwd=self.work)
        self.commit("scratch")
        self.push("a", "tmp/scratch:refs/heads/tmp/scratch")
        self.run_sync()
        self.assertIsNone(self.head("b", "refs/heads/tmp/scratch"))

    def test_tag_is_synced_once_but_a_moved_tag_is_not(self):
        self.run_sync()
        git(["tag", "v1"], cwd=self.work)
        self.push("a", "refs/tags/v1:refs/tags/v1")
        self.run_sync()
        original = self.head("b", "refs/tags/v1")
        self.assertIsNotNone(original)

        self.commit("after-tag")
        git(["tag", "-f", "v1"], cwd=self.work)
        self.push("a", "refs/tags/v1:refs/tags/v1", force=True)
        _, reports = self.run_sync()
        self.assertIn("refs/tags/v1", self.report_for(reports, "app")["diverged"])
        self.assertEqual(self.head("b", "refs/tags/v1"), original)

    def test_dry_run_changes_nothing_and_does_not_poison_the_state(self):
        code, _ = self.run_sync(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIsNone(self.head("b"))
        # dry-run の観測を確定扱いすると、次回に「変化なし」で飛ばしてしまう
        _, reports = self.run_sync()
        self.assertEqual(self.report_for(reports, "app")["status"], "ok")
        self.assertIsNotNone(self.head("b"))

    def test_unknown_pair_is_an_error(self):
        code, _ = self.run_sync(only="nope")
        self.assertEqual(code, 1)

    def test_unreachable_remote_is_reported_as_failure(self):
        shutil.rmtree(self.remote["b"])
        code, reports = self.run_sync()
        self.assertEqual(code, 1)
        self.assertEqual(self.report_for(reports, "app")["status"], "failed")


class DeletePropagationTest(SyncTestCase):
    def make_config(self, **overrides):
        cfg = super().make_config(**overrides)
        for rule in cfg.rules.values():
            rule.propagate_deletes = True
        for pair in cfg.pairs:
            pair.rule.propagate_deletes = True
        return cfg

    def add_release_branch(self):
        git(["checkout", "-q", "-b", "release/1.0"], cwd=self.work)
        self.commit("rel")
        self.push("a", "release/1.0:refs/heads/release/1.0")
        self.run_sync()
        self.assertIsNotNone(self.head("b", "refs/heads/release/1.0"))

    def test_deletion_is_propagated_when_enabled(self):
        self.add_release_branch()
        git(["push", "-q", self.remote["b"], ":refs/heads/release/1.0"], cwd=self.work)
        self.run_sync()
        self.assertIsNone(self.head("a", "refs/heads/release/1.0"))

    def test_deletion_is_not_propagated_when_the_other_side_moved_on(self):
        self.add_release_branch()
        git(["push", "-q", self.remote["b"], ":refs/heads/release/1.0"], cwd=self.work)
        # 削除より後に a 側で作業が乗った → 消さずに b へ作り直す
        moved = self.commit("more-work")
        self.push("a", "release/1.0:refs/heads/release/1.0")
        self.run_sync()
        self.assertEqual(self.head("a", "refs/heads/release/1.0"), moved)
        self.assertEqual(self.head("b", "refs/heads/release/1.0"), moved)


class DeletionOffTest(SyncTestCase):
    def test_deleted_branch_is_restored_by_default(self):
        git(["checkout", "-q", "-b", "release/1.0"], cwd=self.work)
        self.commit("rel")
        self.push("a", "release/1.0:refs/heads/release/1.0")
        self.run_sync()
        git(["push", "-q", self.remote["b"], ":refs/heads/release/1.0"], cwd=self.work)
        self.run_sync()
        self.assertIsNotNone(self.head("b", "refs/heads/release/1.0"))


class OneWayStrategyTest(SyncTestCase):
    def make_config(self, **overrides):
        return super().make_config(
            rules={"out": {"strategy": planner.A_TO_B}},
            pairs=[dict(self.pair_specs()[0], rule="out")], **overrides)

    def test_source_side_is_never_written(self):
        self.run_sync()
        b_head = self.commit("b-only")
        self.push("b")
        before = self.head("a")

        _, reports = self.run_sync()
        self.assertIn("refs/heads/main", self.report_for(reports, "app")["skipped"])
        self.assertEqual(self.head("a"), before)
        self.assertEqual(self.head("b"), b_head)

    def test_restoring_a_deleted_ref_fetches_the_objects_it_needs(self):
        # 初回は両側が同じ内容 = 何も転送しない。その後 b でブランチが消えると、
        # a-to-b + 削除伝播では「a から復元」になる。素の判定（DELETE_ON_A）だけを
        # 見て取得対象を決めると、送るべき a 側のオブジェクトが手元に無くて push が壊れる。
        for rule in list(self.cfg.rules.values()) + [p.rule for p in self.cfg.pairs]:
            rule.propagate_deletes = True
        git(["checkout", "-q", "-b", "release/1.0"], cwd=self.work)
        head = self.commit("rel")
        self.push("a", "release/1.0:refs/heads/release/1.0")
        self.push("b", "release/1.0:refs/heads/release/1.0")
        self.run_sync()                                   # 両側同一 → 転送なし

        git(["push", "-q", self.remote["b"], ":refs/heads/release/1.0"], cwd=self.work)
        code, reports = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(self.report_for(reports, "app")["failed"], [])
        self.assertEqual(self.head("b", "refs/heads/release/1.0"), head)

    def test_force_overwrites_the_destination(self):
        for rule in list(self.cfg.rules.values()) + [p.rule for p in self.cfg.pairs]:
            rule.allow_force = True
        self.run_sync()
        base = self.head("a")

        a_head = self.commit("a-side")
        self.push("a")
        self.reset_to(base)
        self.commit("b-side")
        self.push("b", force=True)

        self.run_sync()
        self.assertEqual(self.head("b"), a_head)


class SharedStoreTest(SyncTestCase):
    """同じリポジトリを複数のペアで使っても clone を重複させない。"""

    remotes = ("a", "b", "c")

    def pair_specs(self):
        # a を中心に、b（クラウド）と c（バックアップ）へ配る
        return [{"name": "to-b", "a": self.remote["a"], "b": self.remote["b"]},
                {"name": "to-c", "a": self.remote["a"], "b": self.remote["c"]}]

    def stores(self):
        return sorted(os.listdir(self.cfg.store_dir)) if os.path.isdir(self.cfg.store_dir) else []

    def test_two_pairs_sharing_a_repo_use_one_store(self):
        self.run_sync()
        self.assertEqual(len(self.stores()), 1)
        self.assertEqual(self.head("b"), self.head("a"))
        self.assertEqual(self.head("c"), self.head("a"))

    def test_the_shared_repo_is_counted_once(self):
        self.run_sync()
        self.assertEqual(len(self.cfg.repos()), 3)   # ペアは 2 組でもリポジトリは 3 個
        self.assertEqual(len(os.listdir(os.path.join(self.cfg.state_dir, "pairs"))), 2)

    def test_filtering_by_pair_keeps_the_same_store(self):
        self.run_sync(only="to-b")
        self.assertEqual(len(self.stores()), 1)
        first = self.stores()[0]
        self.assertIsNotNone(self.head("b"))
        self.assertIsNone(self.head("c"))           # 絞ったペアだけが動く

        self.run_sync(only="to-c")
        self.assertEqual(self.stores(), [first])    # clone をやり直していない
        self.assertIsNotNone(self.head("c"))

    def test_the_store_refs_are_namespaced_per_repo(self):
        self.run_sync()
        # b 側で先へ進めると、b からも取り込みが発生して両方の名前空間が埋まる
        self.commit("from-b")
        self.push("b")
        self.run_sync()
        store_dir = os.path.join(self.cfg.store_dir, self.stores()[0])
        refs = git(["for-each-ref", "--format=%(refname)"], cwd=store_dir).splitlines()
        for url in (self.remote["a"], self.remote["b"]):
            prefix = "refs/sync/%s/" % repo_slug(url)
            self.assertTrue(any(r.startswith(prefix) for r in refs),
                            "%s で始まる ref がありません: %s" % (prefix, refs))

    def test_disabled_pair_is_skipped(self):
        self.cfg.pairs[1].enabled = False
        self.run_sync()
        self.assertIsNotNone(self.head("b"))
        self.assertIsNone(self.head("c"))


class WriteConflictTest(SyncTestCase):
    """A⇔B と C⇔B のように、1 つのリポジトリへ 2 経路から書き込みが向く構成。"""

    remotes = ("a", "b", "c")

    def pair_specs(self):
        return [{"name": "a-b", "a": self.remote["a"], "b": self.remote["b"]},
                {"name": "c-b", "a": self.remote["c"], "b": self.remote["b"]}]

    def test_conflicting_writes_are_stopped_on_both_pairs(self):
        # a と c にそれぞれ無関係な main がある → b へ 2 通りの書き込みが向く
        a_head = self.head("a")
        git(["checkout", "-q", "--orphan", "other"], cwd=self.work)
        git(["rm", "-rq", "--cached", "."], cwd=self.work)
        c_head = self.commit("c-side")
        git(["push", "-q", self.remote["c"], "other:refs/heads/main"], cwd=self.work)

        code, reports = self.run_sync()
        self.assertEqual(code, 0)
        for name in ("a-b", "c-b"):
            self.assertIn("refs/heads/main", self.report_for(reports, name)["conflicts"])
        self.assertIsNone(self.head("b"))           # b は一切書かれていない
        self.assertEqual(self.head("a"), a_head)
        self.assertEqual(self.head("c"), c_head)

    def test_agreeing_writes_are_not_a_conflict(self):
        # a と c が同じ内容 → b への書き込みは 1 本にまとまる
        git(["push", "-q", self.remote["c"], "main:refs/heads/main"], cwd=self.work)
        code, reports = self.run_sync()
        self.assertEqual(code, 0)
        for name in ("a-b", "c-b"):
            self.assertEqual(self.report_for(reports, name)["conflicts"], [])
        self.assertEqual(self.head("b"), self.head("a"))


class LockTest(SyncTestCase):
    def test_second_process_backs_off(self):
        path = os.path.join(self.cfg.state_dir, "sync.lock")
        first = Lock(path, log=self.log)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(Lock(path, log=self.log).acquire())
        finally:
            first.release()
        self.assertTrue(Lock(path, log=self.log).acquire())

    def test_stale_lock_is_reclaimed(self):
        path = os.path.join(self.cfg.state_dir, "sync.lock")
        held = Lock(path, log=self.log)
        self.assertTrue(held.acquire())
        os.utime(path, (0, 0))                      # 大昔に取られたロックに見せかける
        self.assertTrue(Lock(path, timeout_minutes=1, log=self.log).acquire())


class CliTest(SyncTestCase):
    def test_status_and_list_render(self):
        self.run_sync()
        lines = []
        runner.print_status(self.cfg, out=lines.append)
        runner.print_layout(self.cfg, out=lines.append)
        runner.print_refs(self.cfg, out=lines.append)
        text = "\n".join(lines)
        self.assertIn("app", text)
        self.assertIn("bidirectional-ff", text)
        self.assertIn("refs/heads/main", text)

    def test_tokens_never_reach_the_log(self):
        log = Logger("", ["glpat-supersecret"], quiet=True)
        text = log.redact("push https://oauth2:glpat-supersecret@gitlab.example.com/a/b.git 失敗")
        self.assertNotIn("glpat-supersecret", text)
        self.assertIn("https://***@gitlab.example.com/a/b.git", text)


if __name__ == "__main__":
    unittest.main()
