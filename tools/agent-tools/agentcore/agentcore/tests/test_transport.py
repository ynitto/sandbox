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

from agentcore import transport  # noqa: E402
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

    @staticmethod
    def _corrupt_one_loose_object(wd) -> "str | None":
        """**HEAD コミットの** loose object を 0 バイト化する（電源断で生じるサイズ 0
        オブジェクトを模す）。破壊したパスを返す（見つからなければ None）。

        対象を HEAD に固定するのが要点。`_probe_integrity` は
        `git fsck --connectivity-only` なので**到達グラフの走査に必要なオブジェクト**しか
        読まない。以前は os.walk が最初に見つけた任意の loose object を壊していたため、
        それが走査対象外（別途 pack 済み・到達不能）だと fsck が通ってしまい、
        「破損を検知する」という検証そのものが成立していなかった。"""
        head = subprocess.run(["git", "-C", wd, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        if not head:
            return None
        victim = os.path.join(wd, ".git", "objects", head[:2], head[2:])
        if not os.path.isfile(victim):
            return None   # pack 済み（このテストの前提＝直後の push で loose のはず）
        # git は loose object を読み取り専用（0444）で作るため、書き込み権限を与えてから
        # truncate する。付けずに open すると PermissionError でテスト自体が error になる。
        os.chmod(victim, 0o644)
        with open(victim, "wb"):
            pass
        return victim

    def test_corrupted_object_triggers_rebuild(self):
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main")
        t.ensure_clone()
        with open(os.path.join(wd, "keep.txt"), "w") as f:
            f.write("must-survive\n")
        t.sync_push("seed keep.txt")

        victim = self._corrupt_one_loose_object(wd)
        self.assertIsNotNone(victim, "テスト前提: loose object が見つからない")

        self.assertFalse(t._probe_integrity())
        t2 = GitTransport(wd, self.remote, branch="main")
        t2.ensure_clone()  # 破損検知 → 退避 → 再クローン → 復元
        self.assertTrue(t2._probe_integrity())
        # 未 push だった keep.txt はリモートに push 済みなので消えていないはず
        self.assertTrue(os.path.isfile(os.path.join(wd, "keep.txt")))

    def test_sync_push_rebuilds_on_corruption_discovered_mid_operation(self):
        """ensure_clone 後、同一インスタンスで作業中に破損が露見した場合の経路
        （_rebuild_clone 経由。ensure_clone 時点の「回復できず作り直す」経路とは別）。"""
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main")
        t.ensure_clone()
        with open(os.path.join(wd, "keep.txt"), "w") as f:
            f.write("must-survive\n")
        t.sync_push("seed keep.txt")

        self._corrupt_one_loose_object(wd)
        with open(os.path.join(wd, "after.txt"), "w") as f:
            f.write("after corruption\n")
        t.sync_push("after corruption")  # _rebuild_clone を経由して自己回復し、例外を投げない

        self.assertTrue(t._probe_integrity())
        self.assertTrue(os.path.isfile(os.path.join(wd, "keep.txt")))

    def test_sync_pull_rebuilds_on_corruption_discovered_mid_operation(self):
        wd_a = os.path.join(self.tmp.name, "node-a")
        wd_b = os.path.join(self.tmp.name, "node-b")
        a = GitTransport(wd_a, self.remote, branch="main")
        b = GitTransport(wd_b, self.remote, branch="main")
        a.ensure_clone()
        b.ensure_clone()
        with open(os.path.join(wd_a, "keep.txt"), "w") as f:
            f.write("must-survive\n")
        a.sync_push("seed keep.txt")
        with open(os.path.join(wd_b, "b-only.txt"), "w") as f:
            f.write("b\n")
        b.sync_push("seed b-only")

        self._corrupt_one_loose_object(wd_a)
        a.sync_pull(force=True)  # _rebuild_clone を経由して自己回復し、例外を投げない
        self.assertTrue(a._probe_integrity())
        self.assertTrue(os.path.isfile(os.path.join(wd_a, "b-only.txt")))

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


class TestPushSkipsWhenNothingToSend(TransportTestBase):
    """押し出すものが無ければ push しない（バスは毎パス sync_push を呼ぶため、
    変更の無いパスでリモートを叩かない — BoardRepo/BoardMirror が持っていた
    `status --porcelain` の空振り抑止に相当する）。"""

    def test_no_push_attempt_when_nothing_committed(self):
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main")
        t.ensure_clone()
        with open(os.path.join(wd, "a.txt"), "w") as f:
            f.write("one\n")
        t.sync_push("first")
        # リモートを壊す: 押し出すものがあれば必ず失敗（RuntimeError）するはず
        _git(wd, "remote", "set-url", "origin", os.path.join(self.tmp.name, "gone.git"))
        t.sync_push("nothing to do")   # 変更なし → push を試みない＝例外にならない

    def test_unpushed_local_commit_is_pushed_even_without_new_changes(self):
        """commit 済み・push 未達（前回 push が落ちた等）は「変更なし」ではない。"""
        wd = os.path.join(self.tmp.name, "node-a")
        t = GitTransport(wd, self.remote, branch="main")
        t.ensure_clone()
        with open(os.path.join(wd, "a.txt"), "w") as f:
            f.write("one\n")
        t._commit_pending("committed but not pushed")   # commit だけ済ませる
        t.sync_push("push it")
        wd_b = os.path.join(self.tmp.name, "node-b")
        b = GitTransport(wd_b, self.remote, branch="main")
        b.ensure_clone()
        self.assertTrue(os.path.isfile(os.path.join(wd_b, "a.txt")))


class TestHangGuards(unittest.TestCase):
    """常駐体のハング防止（timeout・資格情報プロンプト抑止）。

    スケジューラは tick を専用スレッドで回し、強制打ち切りをサブプロセスの timeout に
    委ねる設計（Python スレッドは kill できない）。ここが緩むと、停止したリモートが
    tick スレッドを永久にブロックして常駐体ごと abort → 再起動ループになる。"""

    def test_network_subcommands_get_the_long_limit(self):
        for args in (["push", "-u", "origin", "main"], ["fetch", "--prune"],
                     ["-C", "/tmp/x", "pull", "--rebase"], ["clone", "url", "dst"]):
            self.assertEqual(transport.git_timeout_for(args),
                             transport.GIT_NET_TIMEOUT_SEC, args)

    def test_local_subcommands_get_the_short_limit(self):
        # `-C <path>` の値を先頭サブコマンドと読み違えないこと（読み違えると push に
        # ローカル上限が掛かり、正当に長い転送を毎回打ち切る）。
        for args in (["-C", "/tmp/x", "rev-parse", "--show-toplevel"],
                     ["config", "--local", "user.email"], ["add", "--", "a.txt"],
                     ["remote", "get-url", "origin"]):
            self.assertEqual(transport.git_timeout_for(args),
                             transport.GIT_LOCAL_TIMEOUT_SEC, args)

    def test_harden_env_blocks_credential_prompts(self):
        env = transport.harden_git_env({})
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_ASKPASS"], "")
        self.assertEqual(env["SSH_ASKPASS"], "")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])
        self.assertEqual(env["LC_ALL"], "C")

    def test_harden_env_keeps_caller_ssh_command(self):
        # 呼び出し側が明示した GIT_SSH_COMMAND は尊重する（鍵指定などを潰さない）。
        env = transport.harden_git_env({"GIT_SSH_COMMAND": "ssh -i /k/id"})
        self.assertEqual(env["GIT_SSH_COMMAND"], "ssh -i /k/id")

    def test_timed_out_result_is_a_failure_not_an_exception(self):
        r = transport.timed_out_result(["git", "fetch"], 600.0)
        self.assertEqual(r.returncode, transport.GIT_TIMEOUT_RC)
        self.assertNotEqual(r.returncode, 0)     # check=True の呼び出しは失敗として扱われる
        self.assertIn("timeout", r.stderr)
        self.assertEqual(r.stdout, "")           # stdout を読む側が None で落ちない

    def test_git_returns_timeout_result_instead_of_hanging(self):
        """timeout を踏んでも例外を投げず、check=False の呼び出しが素通りできること。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = GitTransport(os.path.join(tmp.name, "wd"), os.path.join(tmp.name, "r.git"))
        real = subprocess.run

        def fake(cmd, *a, **kw):
            if cmd[:1] == ["git"]:
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
            return real(cmd, *a, **kw)

        subprocess.run = fake
        try:
            p = t._git(["rev-parse", "--show-toplevel"], check=False)
            self.assertEqual(p.returncode, transport.GIT_TIMEOUT_RC)
            with self.assertRaises(RuntimeError):     # check=True は従来どおり失敗を伝える
                t._git(["rev-parse", "--show-toplevel"], check=True)
        finally:
            subprocess.run = real


if __name__ == "__main__":
    unittest.main()
