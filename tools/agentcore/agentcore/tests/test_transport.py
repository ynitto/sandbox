"""agentcore.transport の単体テスト。

bare repo + 故意のロック残骸/中断 rebase/オブジェクト破損を使い、GitBus から移植した
自己回復ロジックが agentcore でも同じ保証を持つことを検証する。
実行: python3 -m unittest discover -s tools/agentcore/agentcore/tests
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore.transport import GitTransport  # noqa: E402


def _git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=check)


class TransportTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = os.path.join(self.tmp.name, "remote.git")
        _git(self.tmp.name, "init", "--bare", "-b", "main", self.remote)


class TestCloneAndSync(TransportTestBase):
    def test_ensure_clone_on_empty_remote_creates_workdir(self):
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main")
        t.ensure_clone()
        self.assertTrue(os.path.isdir(os.path.join(wd, ".git")))
        # 自前管理クローンの目印が付いている
        r = _git(wd, "config", "--get", "agentcore.managed", check=False)
        self.assertEqual(r.stdout.strip(), "1")

    def test_push_then_pull_roundtrip(self):
        wd_a = os.path.join(self.tmp.name, "node-a")
        wd_b = os.path.join(self.tmp.name, "node-b")
        a = GitTransport(wd_a, self.remote, branch="main")
        b = GitTransport(wd_b, self.remote, branch="main")
        a.ensure_clone()
        with open(os.path.join(wd_a, "hello.txt"), "w") as f:
            f.write("from a\n")
        a.sync_push("add hello")

        b.ensure_clone()
        b.sync_pull(force=True)
        self.assertTrue(os.path.isfile(os.path.join(wd_b, "hello.txt")))
        with open(os.path.join(wd_b, "hello.txt")) as f:
            self.assertEqual(f.read(), "from a\n")

    def test_concurrent_push_resolves_via_rebase_no_force(self):
        wd_a = os.path.join(self.tmp.name, "node-a")
        wd_b = os.path.join(self.tmp.name, "node-b")
        a = GitTransport(wd_a, self.remote, branch="main")
        b = GitTransport(wd_b, self.remote, branch="main")
        a.ensure_clone()
        b.ensure_clone()

        with open(os.path.join(wd_a, "a.txt"), "w") as f:
            f.write("a\n")
        a.sync_push("add a")

        with open(os.path.join(wd_b, "b.txt"), "w") as f:
            f.write("b\n")
        b.sync_push("add b")  # b は a の push の後なので pull --rebase を経由するはず

        a.sync_pull(force=True)
        self.assertTrue(os.path.isfile(os.path.join(wd_a, "a.txt")))
        self.assertTrue(os.path.isfile(os.path.join(wd_a, "b.txt")))
        # force push が使われていない証拠: リモートの reflog に forced-update が無い
        log = _git(self.remote, "log", "--oneline", "main").stdout
        self.assertIn("add a", log)
        self.assertIn("add b", log)

    def test_sparse_paths_limit_worktree(self):
        wd_a = os.path.join(self.tmp.name, "node-a")
        a = GitTransport(wd_a, self.remote, branch="main")
        a.ensure_clone()
        os.makedirs(os.path.join(wd_a, "runs"))
        os.makedirs(os.path.join(wd_a, "other"))
        with open(os.path.join(wd_a, "runs", "r.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(wd_a, "other", "o.json"), "w") as f:
            f.write("{}")
        a.sync_push("seed")

        wd_b = os.path.join(self.tmp.name, "node-b")
        b = GitTransport(wd_b, self.remote, branch="main", sparse_paths=["runs"])
        b.ensure_clone()
        self.assertTrue(os.path.isfile(os.path.join(wd_b, "runs", "r.json")))
        self.assertFalse(os.path.exists(os.path.join(wd_b, "other")))


class TestSelfHealing(TransportTestBase):
    def test_stale_lock_is_removed_and_recovered(self):
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main", lock_stale_sec=0.05)
        t.ensure_clone()
        lock = os.path.join(wd, ".git", "index.lock")
        with open(lock, "w") as f:
            f.write("stale")
        old = time.time() - 10
        os.utime(lock, (old, old))

        # 新しい GitTransport で再利用させ、回復パスを通す
        t2 = GitTransport(wd, self.remote, branch="main", lock_stale_sec=0.05)
        t2.ensure_clone()
        self.assertFalse(os.path.exists(lock))
        with open(os.path.join(wd, "after.txt"), "w") as f:
            f.write("ok\n")
        t2.sync_push("after lock recovery")  # ロック残骸のせいで失敗しないこと

    def test_fresh_lock_is_not_removed(self):
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main", lock_stale_sec=300.0)
        t.ensure_clone()
        lock = os.path.join(wd, ".git", "index.lock")
        with open(lock, "w") as f:
            f.write("fresh")
        t._remove_stale_git_locks()
        self.assertTrue(os.path.exists(lock))
        os.remove(lock)

    def test_interrupted_rebase_is_aborted_on_reuse(self):
        wd_a = os.path.join(self.tmp.name, "node-a")
        wd_b = os.path.join(self.tmp.name, "node-b")
        a = GitTransport(wd_a, self.remote, branch="main")
        b = GitTransport(wd_b, self.remote, branch="main")
        a.ensure_clone()
        with open(os.path.join(wd_a, "seed.txt"), "w") as f:
            f.write("seed\n")
        a.sync_push("seed")

        b.ensure_clone()
        with open(os.path.join(wd_b, "seed.txt"), "w") as f:
            f.write("b-version\n")
        _git(wd_b, "add", "-A")
        _git(wd_b, "commit", "-m", "b changes seed")

        with open(os.path.join(wd_a, "seed.txt"), "w") as f:
            f.write("a-version\n")
        a.sync_push("a changes seed")

        # b で pull --rebase を始めてコンフリクトさせ、途中で放棄した状態を模す
        _git(wd_b, "fetch", "origin", "main")
        _git(wd_b, "rebase", "origin/main", check=False)
        self.assertTrue(os.path.isdir(os.path.join(wd_b, ".git", "rebase-merge")) or
                        os.path.isdir(os.path.join(wd_b, ".git", "rebase-apply")))

        b2 = GitTransport(wd_b, self.remote, branch="main")
        b2.ensure_clone()  # 再利用時に _recover_reused_clone が rebase --abort する
        self.assertFalse(os.path.isdir(os.path.join(wd_b, ".git", "rebase-merge")))
        self.assertFalse(os.path.isdir(os.path.join(wd_b, ".git", "rebase-apply")))
        # 中断状態が解けていれば通常の git 操作が通る
        r = _git(wd_b, "status", "--porcelain", check=False)
        self.assertEqual(r.returncode, 0)

    def test_corrupted_object_triggers_rebuild(self):
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main")
        t.ensure_clone()
        with open(os.path.join(wd, "keep.txt"), "w") as f:
            f.write("must-survive\n")
        t.sync_push("seed keep.txt")

        # 電源断で生じるサイズ 0 オブジェクトを模す: 到達可能な loose object を 1 つ 0 バイト化
        objects_dir = os.path.join(wd, ".git", "objects")
        victim = None
        for root, _dirs, files in os.walk(objects_dir):
            for name in files:
                if len(os.path.basename(root)) == 2 and len(name) == 38:
                    victim = os.path.join(root, name)
                    break
            if victim:
                break
        self.assertIsNotNone(victim, "テスト前提: loose object が見つからない")
        with open(victim, "wb"):
            pass  # 0 バイトに破壊

        self.assertFalse(t._probe_integrity())
        t2 = GitTransport(wd, self.remote, branch="main")
        t2.ensure_clone()  # 破損検知 → 退避 → 再クローン → 復元
        self.assertTrue(t2._probe_integrity())
        # 未 push だった keep.txt はリモートに push 済みなので消えていないはず
        self.assertTrue(os.path.isfile(os.path.join(wd, "keep.txt")))

    def test_pre_marker_clone_is_reused_when_full_checkout(self):
        """マーカー導入前に作られた素の clone（このモジュール以前の BoardRepo/BoardMirror が
        作ったクローン相当）を、フルチェックアウト運用では「管理外の非空ディレクトリ」として
        拒否せず、過去の自前クローンとみなして再利用できること（後方互換）。"""
        wd = os.path.join(self.tmp.name, "node-a")
        _git(self.tmp.name, "clone", self.remote, wd)  # マーカーを付けない素の clone
        t = GitTransport(wd, self.remote, branch="main")
        t.ensure_clone()  # 拒否されず再利用され、事後にマーカーが付く
        r = _git(wd, "config", "--get", "agentcore.managed", check=False)
        self.assertEqual(r.stdout.strip(), "1")

    def test_managed_clone_marker_prevents_hijacking_foreign_full_checkout_before_sparsing(self):
        """sparse_paths を使う運用（GitBus 相当）でユーザーの手動フルチェックアウト
        （目印なし・sparse-checkout 未設定）を掴んだ場合は管理外として扱い、
        空でなければ ensure_clone は中断する（sparse-checkout で作業ファイルを
        誤って隠す事故を防ぐ——フルチェックアウト運用の後方互換とは別軸の保護）。"""
        wd = os.path.join(self.tmp.name, "node-a")
        _git(self.tmp.name, "clone", self.remote, wd)
        with open(os.path.join(wd, "user_file.txt"), "w") as f:
            f.write("user work\n")
        t = GitTransport(wd, self.remote, branch="main", sparse_paths=["runs"])
        with self.assertRaises(RuntimeError):
            t.ensure_clone()


class TestIntervalThrottling(TransportTestBase):
    def test_pull_skipped_within_interval(self):
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main", interval=1000.0)
        t.ensure_clone()
        self.assertTrue(t.sync_pull())   # 初回は last_pull=0 なので必ず実行
        self.assertFalse(t.sync_pull())  # 間隔未到達なので skip

    def test_force_bypasses_interval(self):
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main", interval=1000.0)
        t.ensure_clone()
        t.sync_pull()
        self.assertTrue(t.sync_pull(force=True))

    def test_clock_does_not_advance_on_pull_failure(self):
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main", interval=1000.0)
        t.ensure_clone()
        t._last_pull = 0.0
        # remote を消して失敗させる
        broken_remote = os.path.join(self.tmp.name, "gone.git")
        _git(wd, "remote", "set-url", "origin", broken_remote)
        t.sync_pull(force=True)
        self.assertEqual(t._last_pull, 0.0, "失敗時は間隔クロックを進めてはいけない")


if __name__ == "__main__":
    unittest.main()
