"""agent-project の単体テスト — state_git（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class TestStateSyncBatching(unittest.TestCase):
    """state sync コミットの集約: 未 push の連続 sync は --amend で 1 つに束ね、同期のたびの
    1 行差分コミットが履歴を埋め尽くさないようにする。push 済み・人のコミットは書き換えない。"""

    @staticmethod
    def _init_repo(d: Path) -> None:
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        subprocess.run(["git", "-C", str(d), "symbolic-ref", "HEAD", "refs/heads/main"],
                       check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.email", "t@test"], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.name", "t"], check=True)

    @staticmethod
    def _log(d: Path) -> "list[str]":
        r = subprocess.run(["git", "-C", str(d), "log", "--format=%s"],
                           capture_output=True, text=True)
        return [ln for ln in r.stdout.splitlines() if ln.strip()]

    def test_direct_consecutive_syncs_amend_into_one(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._init_repo(d)
            sg = km.DirectStateGit(d, interval=0.0)
            (d / "journal.md").write_text("a\n", encoding="utf-8")
            sg.sync()
            (d / "journal.md").write_text("a\nb\n", encoding="utf-8")
            sg.sync()
            msgs = self._log(d)
            self.assertEqual(len(msgs), 1)                     # 2 回目は amend で束ねる
            self.assertTrue(msgs[0].startswith("agent-project: state sync"))

    def test_direct_does_not_amend_manual_commit(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._init_repo(d)
            sg = km.DirectStateGit(d, interval=0.0)
            (d / "journal.md").write_text("a\n", encoding="utf-8")
            sg.sync()
            (d / "note.md").write_text("human edit\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(d), "commit", "-qm", "manual edit"], check=True)
            (d / "journal.md").write_text("a\nb\n", encoding="utf-8")
            sg.sync()
            msgs = self._log(d)
            self.assertEqual(len(msgs), 3)                     # 人のコミットは書き換えない
            self.assertEqual(msgs[1], "manual edit")

    def test_direct_does_not_amend_pushed_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            d = tmp / "root"
            d.mkdir()
            self._init_repo(d)
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin", str(remote)],
                           check=True)
            sg = km.DirectStateGit(d, interval=0.0)            # interval 0 → 毎 sync で push
            (d / "journal.md").write_text("a\n", encoding="utf-8")
            sg.sync()                                          # commit + push
            (d / "journal.md").write_text("a\nb\n", encoding="utf-8")
            sg.sync()                                          # push 済み HEAD は amend しない
            self.assertEqual(len(self._log(d)), 2)

    @staticmethod
    def _worktree_names(d: Path) -> "list[str]":
        r = subprocess.run(["git", "-C", str(d), "worktree", "list", "--porcelain"],
                           capture_output=True, text=True)
        return [os.path.basename(ln[len("worktree "):])
                for ln in r.stdout.splitlines() if ln.startswith("worktree ")]

    def _repo_with_head(self, d: Path) -> None:
        self._init_repo(d)
        (d / "seed.md").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", "seed"], check=True,
                       capture_output=True)

    def test_prune_stale_state_worktrees_removes_leftover(self):
        # 前プロセスの強制終了で残った専用 worktree（登録 + /tmp 実体）を掃除する
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = tmp / "root"; d.mkdir()
            self._repo_with_head(d)
            leftover = tmp / (km._STATE_WT_PREFIX + "dead")
            subprocess.run(["git", "-C", str(d), "worktree", "add", "--detach", "--force",
                            str(leftover), "HEAD"], check=True, capture_output=True)
            self.assertIn(leftover.name, self._worktree_names(d))
            km.DirectStateGit(d, interval=0.0)._prune_stale_state_worktrees()
            self.assertNotIn(leftover.name, self._worktree_names(d))
            self.assertFalse(leftover.exists())

    def test_prune_removes_locked_leftover_worktree(self):
        # ロック済み worktree は prune が飛ばす → unlock してから外すことを固定する
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = tmp / "root"; d.mkdir()
            self._repo_with_head(d)
            leftover = tmp / (km._STATE_WT_PREFIX + "locked")
            subprocess.run(["git", "-C", str(d), "worktree", "add", "--detach", "--force",
                            str(leftover), "HEAD"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(d), "worktree", "lock", str(leftover)],
                           check=True, capture_output=True)
            km.DirectStateGit(d, interval=0.0)._prune_stale_state_worktrees()
            self.assertNotIn(leftover.name, self._worktree_names(d))
            self.assertFalse(leftover.exists())

    def test_prune_leaves_foreign_worktrees_untouched(self):
        # prefix に一致しない worktree（人・他ツール）には一切触れない
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = tmp / "root"; d.mkdir()
            self._repo_with_head(d)
            keep = tmp / "human-feature"
            subprocess.run(["git", "-C", str(d), "worktree", "add", "--detach",
                            str(keep), "HEAD"], check=True, capture_output=True)
            km.DirectStateGit(d, interval=0.0)._prune_stale_state_worktrees()
            self.assertIn(keep.name, self._worktree_names(d))
            self.assertTrue(keep.exists())

    def test_worktree_commit_self_heals_leftover_before_creating(self):
        # _worktree_commit の冒頭で残骸を掃除してから新規作成する（同期は正常完了する）
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = tmp / "root"; d.mkdir()
            self._repo_with_head(d)
            leftover = tmp / (km._STATE_WT_PREFIX + "stale")
            subprocess.run(["git", "-C", str(d), "worktree", "add", "--detach", "--force",
                            str(leftover), "HEAD"], check=True, capture_output=True)
            sg = km.DirectStateGit(d, interval=0.0)
            (d / "journal.md").write_text("a\n", encoding="utf-8")
            sg.sync()                                          # 残骸があっても export は通る
            self.assertNotIn(leftover.name, self._worktree_names(d))
            self.assertTrue(any(m.startswith("agent-project: state sync") for m in self._log(d)))

    @staticmethod
    def _commit_all(d: Path) -> None:
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", "c"], check=True,
                       capture_output=True)

    def test_direct_push_recovers_from_foreign_dirt_in_state_worktree(self):
        """状態 worktree で「同期名前空間の外」が汚れていても push は通り続ける。

        root=<top>/.agent-project、外（<top>/journal.md）に未コミット変更が残っていると、
        _integrate の rebase が「作業ツリーが汚れている」で必ず失敗 → push は永久に
        non-fast-forward → 分散同期が完全停止する（実際に起きた: 415 件未 push）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            top = tmp / "wt"
            top.mkdir()
            self._init_repo(top)
            subprocess.run(["git", "-C", str(top), "remote", "add", "origin", str(remote)],
                           check=True)
            (top / "journal.md").write_text("legacy\n", encoding="utf-8")   # 名前空間の外
            root = top / ".agent-project"
            root.mkdir()
            (root / "journal.md").write_text("a\n", encoding="utf-8")
            self._commit_all(top)
            subprocess.run(["git", "-C", str(top), "push", "-q", "-u", "origin", "main"],
                           check=True)

            # 別ホストが origin を 1 コミット進める（= こちらは behind 1 → push は non-FF）
            other = tmp / "other"
            subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.email", "o@test"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
            (other / "other.md").write_text("from another host\n", encoding="utf-8")
            self._commit_all(other)
            subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

            # 名前空間の外に未コミット変更を残す（中断した rebase / 旧レイアウトの残骸を再現）
            (top / "journal.md").write_text("clobbered\n", encoding="utf-8")

            sg = km.DirectStateGit(root, interval=0.0)
            (root / "journal.md").write_text("a\nb\n", encoding="utf-8")     # 自分の名前空間の更新
            sg.sync(force=True)                                              # 例外を投げず push が通る

            r = subprocess.run(["git", "-C", str(top), "rev-list", "--count",
                                "origin/main..HEAD"], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "0")                          # 未 push が残らない
            self.assertTrue((top / "other.md").exists())                     # 相手の更新も取り込めた

    def test_direct_integrate_adjudicates_conflict_instead_of_giving_up(self):
        """rebase が競合しても裁定で決着させて push を通す。

        abort して諦めると push は永久に non-fast-forward のまま＝分散同期が二度と回復しない。
        裁定規則は StateGit と同じ: 人の入力（charter.md）はリモート優先、機械状態はローカル優先。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            top = tmp / "wt"
            top.mkdir()
            self._init_repo(top)
            subprocess.run(["git", "-C", str(top), "remote", "add", "origin", str(remote)],
                           check=True)
            root = top / ".agent-project"
            root.mkdir()
            (root / "charter.md").write_text("base\n", encoding="utf-8")     # 人の入力
            (root / "status.json").write_text("{}\n", encoding="utf-8")      # 機械状態
            self._commit_all(top)
            subprocess.run(["git", "-C", str(top), "push", "-q", "-u", "origin", "main"],
                           check=True)

            other = tmp / "other"             # 別ホストが同じ 2 ファイルを両方書き換えて push
            subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.email", "o@t"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
            (other / ".agent-project" / "charter.md").write_text("人の更新\n", encoding="utf-8")
            (other / ".agent-project" / "status.json").write_text('{"remote":1}\n',
                                                                 encoding="utf-8")
            self._commit_all(other)
            subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

            sg = km.DirectStateGit(root, interval=0.0)
            (root / "charter.md").write_text("こちらの更新\n", encoding="utf-8")
            (root / "status.json").write_text('{"local":1}\n', encoding="utf-8")
            sg.sync(force=True)               # 競合するが例外を投げず push が通る

            r = subprocess.run(["git", "-C", str(top), "rev-list", "--count",
                                "origin/main..HEAD"], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "0")                       # 未 push が残らない
            self.assertFalse(sg._rebasing())                              # rebase を残さない
            self.assertEqual((root / "charter.md").read_text(), "人の更新\n")    # 人＝リモート優先
            self.assertEqual((root / "status.json").read_text(), '{"local":1}\n')  # 機械＝ローカル

    def test_direct_integrate_survives_tracked_excluded_dirt(self):
        """「追跡されてしまった同期除外パス」が dirty でも統合・push が通り続ける（自己修復）。

        旧実装（rebase 統合）の致命傷の再現: 他コミッタ（viewer / 旧 commit_state / agent-flow の
        管理クローン）が claims/ や bus/.state-git を一度コミットすると、こちらは絶対に commit
        しないため「tracked だが commit されない変更」が永久に残り、rebase が二度と通らず
        push は non-fast-forward のまま状態共有が復旧不能になった（実運用で発生）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            top = tmp / "wt"
            top.mkdir()
            self._init_repo(top)
            subprocess.run(["git", "-C", str(top), "remote", "add", "origin", str(remote)],
                           check=True)
            root = top / ".agent-project"
            (root / "claims").mkdir(parents=True)
            (root / "bus").mkdir()
            (root / "journal.md").write_text("a\n", encoding="utf-8")
            (root / "claims" / "T1.lock").write_text("owner\n", encoding="utf-8")
            (root / "bus" / ".state-git").write_text("legacy clone marker\n", encoding="utf-8")
            self._commit_all(top)              # 他コミッタが除外パスまで追跡した状態を再現
            subprocess.run(["git", "-C", str(top), "push", "-q", "-u", "origin", "main"],
                           check=True)

            other = tmp / "other"              # リモートの viewer が指示を積む
            subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.email", "o@t"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
            cdir = other / ".agent-project" / "commands"
            cdir.mkdir(parents=True)
            (cdir / "viewer-approve-T1.json").write_text('{"command":"approve","id":"T1"}',
                                                         encoding="utf-8")
            self._commit_all(other)
            subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

            # 除外パスを dirty にしたまま（旧実装ならここで統合が永久に詰まる）ローカルも進める
            (root / "claims" / "T1.lock").write_text("stolen\n", encoding="utf-8")
            (root / "journal.md").write_text("a\nb\n", encoding="utf-8")
            sg = km.DirectStateGit(root, interval=0.0)
            sg.sync(force=True)                                    # 例外を投げない

            r = subprocess.run(["git", "-C", str(top), "rev-list", "--count",
                                "origin/main..HEAD"], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "0")                # 未 push が残らない
            self.assertTrue((root / "commands" / "viewer-approve-T1.json").exists())  # 指示を取得
            ls = subprocess.run(["git", "-C", str(top), "ls-files", "--",
                                 ".agent-project/claims", ".agent-project/bus/.state-git"],
                                capture_output=True, text=True)
            self.assertEqual(ls.stdout.strip(), "")                # 除外パスは追跡から外れた
            self.assertTrue((root / "claims" / "T1.lock").exists())  # 実ファイルは消さない

    def test_direct_integrate_preserves_both_sides_of_diverged_history(self):
        """多重書き手で分岐した履歴を 1 回の sync で決定的に合流させる（マージ・両方残す）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            top = tmp / "wt"
            top.mkdir()
            self._init_repo(top)
            subprocess.run(["git", "-C", str(top), "remote", "add", "origin", str(remote)],
                           check=True)
            root = top / ".agent-project"
            run_dir = root / "bus" / "runs" / "r1"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text('{"status":"running"}', encoding="utf-8")
            (root / "journal.md").write_text("base\n", encoding="utf-8")
            self._commit_all(top)
            subprocess.run(["git", "-C", str(top), "push", "-q", "-u", "origin", "main"],
                           check=True)

            other = tmp / "other"              # 別書き手（viewer）が run の進捗と成果を積む
            subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.email", "o@t"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
            orun = other / ".agent-project" / "bus" / "runs" / "r1"
            (orun / "results").mkdir(parents=True)
            (orun / "results" / "t1.json").write_text('{"ok":true}', encoding="utf-8")
            self._commit_all(other)
            subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

            sg = km.DirectStateGit(root, interval=0.0)
            (run_dir / "meta.json").write_text('{"status":"done"}', encoding="utf-8")  # 機械状態
            sg.sync(force=True)                # ローカルコミット＋リモート分岐 → マージで合流

            for c in ("origin/main..HEAD", "HEAD..origin/main"):
                r = subprocess.run(["git", "-C", str(top), "rev-list", "--count", c],
                                   capture_output=True, text=True)
                self.assertEqual(r.stdout.strip(), "0", c)         # 双方向とも乖離ゼロ
            self.assertEqual((run_dir / "meta.json").read_text(), '{"status":"done"}')  # 機械=ローカル
            self.assertTrue((run_dir / "results" / "t1.json").exists())   # リモートの成果も取得

    def test_flow_remote_none_when_bus_inside_root(self):
        """バスが root 配下（既定）なら agent-flow へ state-git を注入しない＝第二の書き手を作らない。
        agent-project 自身の state 同期が bus ごと鏡写しする。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._init_repo(d)
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                            "https://example.invalid/r.git"], check=True)
            cfg = cfg_for(d, bus=d / "bus")                      # 既定の <root>/bus 相当
            t = km.Task(id="T1", title="x", verify="true")
            self.assertNotIn("--state-git", km.build_agent_flow_cmd(t, cfg))

    def test_state_sync_journals_imports_only(self):
        # journal へ残すのは import（リモート指示の取り込み）のみ。export を記録すると
        # その行自体が次の同期の差分になり「export=1」の空コミットが恒久に続くため。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)

            class _SG:
                def __init__(self, ret):
                    self.ret = ret

                def sync(self, force=False):
                    return self.ret
            with mock.patch.object(km, "state_git_for", return_value=_SG((0, 1))):
                km.state_sync(cfg)
            self.assertFalse(cfg.journal.exists())             # export のみ → 記録しない
            with mock.patch.object(km, "state_git_for", return_value=_SG((2, 0))):
                km.state_sync(cfg)
            self.assertIn("import=2", cfg.journal.read_text(encoding="utf-8"))


class SharedGitCacheTests(unittest.TestCase):
    """検証用の共有 git キャッシュ + worktree（docs/designs/git-worktree-cache-pattern.md）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ka-cache-"))
        self._prev = os.environ.get("KIRO_GIT_CACHE_DIR")
        os.environ["KIRO_GIT_CACHE_DIR"] = str(self.tmp / "gitcache")

    def tearDown(self):
        km._prune_caches(km._provisioned_urls)
        km._provisioned_urls.clear()
        if self._prev is None:
            os.environ.pop("KIRO_GIT_CACHE_DIR", None)
        else:
            os.environ["KIRO_GIT_CACHE_DIR"] = self._prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_remote(self, name="remote"):
        remote = self.tmp / name
        remote.mkdir(parents=True)
        for cmd in (["git", "init", "-q", "-b", "main", str(remote)],
                    ["git", "-C", str(remote), "config", "user.email", "t@t"],
                    ["git", "-C", str(remote), "config", "user.name", "t"]):
            subprocess.run(cmd, check=True)
        (remote / "f.txt").write_text("init")
        subprocess.run(["git", "-C", str(remote), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(remote), "commit", "-qm", "init"], check=True)
        return str(remote)

    def test_clone_repo_shallow_uses_worktree_and_reflects_latest(self):
        # _clone_repo_shallow は共有 cache 経由で worktree を生やし、毎回 fetch して最新を反映する（INV-1）。
        remote = self._make_remote()
        dest1 = str(self.tmp / "w1")
        km._clone_repo_shallow(remote, "main", dest1)
        self.assertTrue(os.path.exists(os.path.join(dest1, ".git")))   # worktree なら .git はファイル
        self.assertTrue(os.path.exists(os.path.join(dest1, "f.txt")))
        # ミラーが共有 root にできている
        self.assertTrue(any(n.endswith(".git") for n in os.listdir(os.environ["KIRO_GIT_CACHE_DIR"])))
        # リモートに新コミット → 次の取得は最新を反映
        (Path(remote) / "more.txt").write_text("x")
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "more"], check=True)
        dest2 = str(self.tmp / "w2")
        km._clone_repo_shallow(remote, "main", dest2)
        self.assertTrue(os.path.exists(os.path.join(dest2, "more.txt")))

    def test_clone_repo_shallow_falls_back_when_cache_unavailable(self):
        # INV-3: cache が使えなければ従来の浅 clone に倒れる（.git はディレクトリ）。
        remote = self._make_remote(name="fb")
        dest = str(self.tmp / "fb-dest")
        with mock.patch.object(km, "ensure_cache", return_value=None):
            km._clone_repo_shallow(remote, "main", dest)
        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))

    def test_clone_repo_shallow_raises_on_total_failure(self):
        # cache もフォールバック clone も失敗するなら RuntimeError（呼び出し側で全 NG 扱い）。
        with mock.patch.object(km, "ensure_cache", return_value=None):
            with self.assertRaises(RuntimeError):
                km._clone_repo_shallow("/no/such/repo.git", "main", str(self.tmp / "none"))

    def test_missing_target_branch_is_ng_not_silent_default(self):
        # 明示した target ブランチが存在しないなら NG（RuntimeError）。既定ブランチへ無言フォールバック
        # して「成果の無い場所で偽 PASS」しないこと（worktree 化で壊しやすい不変条件の回帰防止）。
        remote = self._make_remote(name="tgt")
        with self.assertRaises(RuntimeError):
            km._clone_repo_shallow(remote, "nonexistent-target", str(self.tmp / "wt"))

    def test_explicit_branch_checks_out_that_branch(self):
        # 実在する非既定ブランチを指定したら、その内容で worktree ができる（target 伝搬が効く）。
        remote = self._make_remote(name="tgt2")
        subprocess.run(["git", "-C", remote, "checkout", "-q", "-b", "feature"], check=True)
        (Path(remote) / "only_on_feature.txt").write_text("x")
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "feat"], check=True)
        subprocess.run(["git", "-C", remote, "checkout", "-q", "main"], check=True)
        dest = str(self.tmp / "wtf" / "repo")
        km._clone_repo_shallow(remote, "feature", dest)
        self.assertTrue(os.path.exists(os.path.join(dest, "only_on_feature.txt")))


class TestStateRepoSeparation(unittest.TestCase):
    """案1: 状態専用リポジトリ。状態を成果物リポジトリの worktree ではなく、専用リポジトリの
    通常 clone に置く（worktree 二重実装・本体 main へのバックアップ＝ドリフト源を回避）。
    未設定なら従来の worktree 方式、clone 失敗時も worktree 方式へフォールバックする。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        km._STATE_GITS.clear()
        self.env = {**os.environ, "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        # 状態専用リポジトリ（bare + main を確立）
        self.state_remote = self.tmp / "state.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.state_remote)], check=True)
        seed = self.tmp / "state-seed"
        subprocess.run(["git", "clone", "-q", str(self.state_remote), str(seed)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.email", "s@t"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.name", "s"], check=True)
        subprocess.run(["git", "-C", str(seed), "checkout", "-qb", "main"], check=True)
        (seed / "charter.md").write_text("# Charter\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(seed), "commit", "-qm", "init"], check=True, env=self.env)
        subprocess.run(["git", "-C", str(seed), "push", "-q", "-u", "origin", "main"],
                       check=True, capture_output=True)
        # 成果物リポジトリ（状態とは別リポジトリ。初期コミットありで worktree フォールバックも効く）
        self.deliverable = self.tmp / "app"
        subprocess.run(["git", "init", "-q", str(self.deliverable)], check=True)
        subprocess.run(["git", "-C", str(self.deliverable), "config", "user.email", "a@t"], check=True)
        subprocess.run(["git", "-C", str(self.deliverable), "config", "user.name", "a"], check=True)
        (self.deliverable / "README.md").write_text("app\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.deliverable), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.deliverable), "commit", "-qm", "init"],
                       check=True, env=self.env)

    def _build(self, **cli):
        ns = types.SimpleNamespace(config=None, **cli)
        km.resolve_config(ns)
        orig = (km._AGENT_CLI, km._AGENT_TIMEOUT)
        try:
            return km.build_config(ns)
        finally:
            km._AGENT_CLI, km._AGENT_TIMEOUT = orig

    def test_state_repo_redirects_to_clone_and_disables_backup(self):
        cfg = self._build(root=str(self.deliverable), state_repo=str(self.state_remote))
        clone = self.deliverable.parent / "app-state"            # 旧 worktree(app-agent-state)と別名
        self.assertTrue((clone / ".git").exists())               # 専用リポジトリを通常 clone した
        self.assertEqual(cfg.backlog, clone.resolve() / "backlog")  # 状態ルートは clone のルート直下
        self.assertEqual(cfg.state_backup_branch, "")            # 本体 main へのミラー無効（ドリフト源を断つ）
        self.assertEqual(cfg.state_top, self.deliverable.resolve())  # 成果物 top（_source_repo 用）
        self.assertIsInstance(km.state_git_for(cfg), km.DirectStateGit)  # direct 同期が効く

    def test_state_repo_clone_dir_is_distinct_from_worktree(self):
        # 既定 clone 先 <repo>-state は旧 worktree <repo>-agent-state と衝突しない
        # （同名だと旧 worktree を掴んで移行が効かなかった。実際に踏んだ落とし穴の回帰防止）。
        cfg = self._build(root=str(self.deliverable), state_repo=str(self.state_remote))
        self.assertEqual(cfg.backlog.parent, (self.deliverable.parent / "app-state").resolve())
        self.assertNotIn("agent-state", cfg.backlog.parent.name)

    def test_state_repo_dir_relative_resolves_under_deliverable_parent(self):
        # state_repo_dir（相対）は成果物top の親を基準に解決する（既定と同じ「成果物の隣」を素直に指定）。
        cfg = self._build(root=str(self.deliverable), state_repo=str(self.state_remote),
                          state_repo_dir="custom-state")
        self.assertEqual(cfg.backlog.parent, (self.deliverable.parent / "custom-state").resolve())

    def test_state_repo_dir_absolute_is_respected(self):
        # 絶対パスはそのまま（pathlib の / 規約: 親 / 絶対パス = 絶対パス）。
        abs_dir = self.tmp / "elsewhere" / "app-state"
        cfg = self._build(root=str(self.deliverable), state_repo=str(self.state_remote),
                          state_repo_dir=str(abs_dir))
        self.assertEqual(cfg.backlog.parent, abs_dir.resolve())

    def test_state_repo_rejects_dir_that_is_not_the_state_clone(self):
        # state_repo_dir が別リポジトリ（旧 worktree 相当）を指していたら再利用せず worktree へ倒す。
        # 別 remote の clone を用意して origin を食い違わせる。
        other_remote = self.tmp / "other.git"
        subprocess.run(["git", "init", "-q", "--bare", str(other_remote)], check=True)
        wrong = self.tmp / "wrong-clone"
        subprocess.run(["git", "clone", "-q", str(other_remote), str(wrong)],
                       check=True, capture_output=True)
        cfg = self._build(root=str(self.deliverable), state_repo=str(self.state_remote),
                          state_repo_dir=str(wrong))
        self.assertNotEqual(cfg.backlog.parent, wrong.resolve())  # 誤ったディレクトリは状態ルートにしない
        self.assertEqual(cfg.state_top, self.deliverable.resolve())  # worktree 方式へフォールバック
        self.assertNotEqual(cfg.state_backup_branch, "")           # フォールバックなのでミラー既定を維持

    def test_state_repo_reuses_existing_clone(self):
        cfg1 = self._build(root=str(self.deliverable), state_repo=str(self.state_remote))
        clone = self.deliverable.parent / "app-state"
        marker = clone / ".git" / "HEAD"
        stamp = marker.stat().st_mtime
        km._STATE_GITS.clear()
        cfg2 = self._build(root=str(self.deliverable), state_repo=str(self.state_remote))
        self.assertEqual(marker.stat().st_mtime, stamp)          # 再 clone せず既存を再利用（origin 一致）
        self.assertEqual(cfg1.backlog, cfg2.backlog)

    def test_state_repo_unset_uses_worktree(self):
        cfg = self._build(root=str(self.deliverable), state_repo="")
        self.assertEqual(cfg.state_repo, "")
        self.assertFalse((self.deliverable.parent / "app-state" / ".git").exists()
                         and bool(cfg.state_repo))               # state_repo clone は作らない

    def test_state_repo_clone_failure_falls_back_to_worktree(self):
        bad = str(self.tmp / "does-not-exist.git")
        cfg = self._build(root=str(self.deliverable), state_repo=bad)
        self.assertEqual(cfg.state_repo, bad)
        self.assertNotEqual(cfg.state_backup_branch, "")         # フォールバック時はミラー既定を維持
        self.assertEqual(cfg.state_top, self.deliverable.resolve())  # worktree 方式（成果物 top）


class TestDirectStateGit(unittest.TestCase):
    """direct モード: プロジェクトルート自体が git クローンなら、管理クローンを介さず
    そのリポジトリへ直接コミット・push する（viewer が git 越しに編集・検収する前提を素直にする）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        km._STATE_GITS.clear()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        subprocess.run(["git", "-C", str(self.remote), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True)
        # ルート = 共有リポジトリの clone（初期コミットを作って main を確立する）
        self.root = self.tmp / "proj"
        seed = self.tmp / "seed"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(seed)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.email", "seed@test"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.name", "seed"], check=True)
        subprocess.run(["git", "-C", str(seed), "checkout", "-qb", "main"], check=True)
        (seed / "charter.md").write_text("# Charter: demo\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(seed), "commit", "-qm", "init"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", "-u", "origin", "main"],
                       check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.root)],
                       check=True, capture_output=True)

    def _cfg(self, **kw):
        base = dict(backlog=self.root / "backlog", policy=self.root / "policy.md",
                    decisions=self.root / "decisions", journal=self.root / "journal.md",
                    needs=self.root / "needs", workdir=self.tmp, bus=self.root / "bus",
                    inbox=self.root / "inbox",
                    planner="none", flow_planner="stub", executor="stub", dry_run=True,
                    state_git_interval=0.0)
        base.update(kw)
        cfg = km.Config(**base)
        km.ensure_dirs(cfg)
        # このクラスは「共有リポジトリを複数 PC が clone している」構成を検証する。CAS は
        # ピアが観測されて初めて有効なので（W1-8）、その前提をフィクスチャで宣言する。
        mk_peer(self.root)
        return cfg

    def _other(self, name="other") -> Path:
        d = self.tmp / name
        subprocess.run(["git", "clone", "-q", str(self.remote), str(d)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(d), "config", "user.email", "other@test"], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.name", "other"], check=True)
        mk_peer(d)
        return d

    def test_root_clone_selects_direct_mode(self):
        cfg = self._cfg()
        self.assertIsInstance(km.state_git_for(cfg), km.DirectStateGit)   # state_git 未設定でも有効
        self.assertIn("direct モード", km.state_git_status_line(cfg))

    def test_controller_lease_has_one_winner_across_clones(self):
        first = self._cfg(node="pc-a", controller_lease_sec=120.0)
        other = self._other("pc-b")
        second = cfg_for(other, node="pc-b", controller_lease_sec=120.0)
        at = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(km.renew_controller_lease(first, at=at))
        self.assertFalse(km.renew_controller_lease(second, at=at + timedelta(seconds=30)))

    def test_controller_lease_moves_after_expiry(self):
        first = self._cfg(node="pc-a", controller_lease_sec=60.0,
                          clock_skew_tolerance_sec=5.0)
        other = self._other("pc-b")
        second = cfg_for(other, node="pc-b", controller_lease_sec=60.0,
                         clock_skew_tolerance_sec=5.0)
        at = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(km.renew_controller_lease(first, at=at))
        self.assertTrue(km.renew_controller_lease(second, at=at + timedelta(seconds=66)))
        lease = json.loads((other / "coordination" / "controller.json").read_text(encoding="utf-8"))
        self.assertEqual((lease["node"], lease["generation"]), ("pc-b", 2))

    def test_controller_lease_tolerates_clock_skew_before_reclaiming(self):
        # 設計 §6「時計ずれ」行: lease は clock_skew_tolerance_sec の許容幅で吸収する。
        # pc-a の時計が pc-b よりわずかに遅れている想定 —— pc-b から見て名目上の
        # lease_until を過ぎていても、許容幅（tolerance）以内ならまだ横取りしない
        # （↑ test_controller_lease_moves_after_expiry は許容幅を過ぎた後の横取りは
        # 確認済みだが、許容幅の内側で横取りしないこと自体は未検証だった）。
        first = self._cfg(node="pc-a", controller_lease_sec=60.0,
                          clock_skew_tolerance_sec=5.0)
        other = self._other("pc-b")
        second = cfg_for(other, node="pc-b", controller_lease_sec=60.0,
                         clock_skew_tolerance_sec=5.0)
        at = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(km.renew_controller_lease(first, at=at))
        # lease_sec(60) は過ぎたが tolerance(5) の内側（+63s）→ pc-b はまだ横取りしない。
        # False 自体が横取りしなかったことの検証（second 側は書き込みを行わないため
        # other のローカルには何も同期されない——読むなら書いた側 self.root を読む）。
        self.assertFalse(km.renew_controller_lease(second, at=at + timedelta(seconds=63)))
        lease = json.loads((self.root / "coordination" / "controller.json").read_text(encoding="utf-8"))
        self.assertEqual(lease["node"], "pc-a")   # pc-a が持ったまま

    def test_worker_does_not_consume_global_inbox(self):
        controller = self._cfg(node="pc-a")
        self.assertTrue(km.renew_controller_lease(controller))
        other = self._other("pc-b-worker")
        worker = cfg_for(other, node="pc-b", inbox=other / "inbox")
        worker.inbox.mkdir(parents=True, exist_ok=True)
        dropped = worker.inbox / "job.json"
        dropped.write_text('{"title":"global job","verify":"true"}', encoding="utf-8")
        km.run_loop(worker)
        self.assertTrue(dropped.exists())
        self.assertEqual(km.load_tasks(worker.backlog), [])

    def test_distributed_claim_has_one_winner_and_persists_fence(self):
        first = self._cfg(node="pc-a")
        mkb(self.root, "T1")
        km.state_sync(first, force=True)
        other = self._other("pc-b-claim")
        second = cfg_for(other, node="pc-b")
        token = km.claim_distributed_task(first, "T1")
        self.assertTrue(token)
        self.assertIsNone(km.claim_distributed_task(second, "T1"))
        claimed = km.load_tasks(first.backlog)[0]
        self.assertEqual((claimed.status, claimed.get("claim_owner")), ("doing", "pc-a"))
        self.assertEqual(claimed.get("claim_token"), token)

    def test_stale_claim_token_cannot_settle(self):
        first = self._cfg(node="pc-a")
        mkb(self.root, "T1")
        km.state_sync(first, force=True)
        token = km.claim_distributed_task(first, "T1")
        stale = km.load_tasks(first.backlog)[0]
        other = self._other("pc-b-fence")
        second = cfg_for(other, node="pc-b")

        def reassign(root):
            path = root / "backlog" / "T1.md"
            task = km.parse_task(path.read_text(encoding="utf-8"), "T1")
            task.set("claim_owner", "pc-b")
            task.set("claim_token", "new-owner-token")
            task.set("claim_generation", "2")
            path.write_text(km.serialize_task(task), encoding="utf-8")
            return True

        self.assertTrue(km.state_transaction(second, reassign, "test reassign"))
        self.assertEqual(stale.get("claim_token"), token)
        self.assertEqual(km.claim_fence_state(first, stale), "lost")   # 取り直された＝真の不一致

    def test_observe_sync_counts_ahead_without_fetching(self):
        # dashboard の同期表示の材料（実装計画 W2-5）。**fetch はしない**——観測のために
        # 毎 tick リモートを叩くと、W2-1 で dashboard から取り除いたリモート負荷を
        # 常駐体側で復活させることになる。
        cfg = self._cfg(node="pc-a")
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)             # 一度 push して origin/<branch> を作る
        git = km.state_git_for(cfg)
        self.assertEqual(git.observe_sync()["ahead"], 0)
        (self.root / "backlog" / "T2.md").write_text("## T2: x\n- status: ready\n",
                                                     encoding="utf-8")
        km.state_sync(cfg, force=True)
        obs = git.observe_sync()
        self.assertEqual((obs["ahead"], obs["behind"]), (0, 0))   # push 済みなら揃う
        self.assertIsNone(obs["last_error"])

    def test_observe_sync_reports_error_when_push_wedged(self):
        # 同期の失敗は best-effort で握り潰される（state_sync）。どこにも残らないと
        # dashboard が緑のままになるので、直近の失敗を観測結果に残す。
        cfg = self._cfg(node="pc-a")
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)
        git = km.state_git_for(cfg)
        subprocess.run(["git", "-C", str(self.root), "remote", "set-url", "origin",
                        "file:///no-such-remote.git"], check=True)
        (self.root / "backlog" / "T3.md").write_text("## T3: x\n- status: ready\n",
                                                     encoding="utf-8")
        km.state_sync(cfg, force=True)             # 失敗は journal 行になって握り潰される
        self.assertTrue(git.observe_sync()["last_error"])

    def test_observe_sync_is_empty_without_remote(self):
        proot = Path(tempfile.mkdtemp(prefix="obs-local-")) / "proj"
        proot.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(proot)], check=True)
        self.assertEqual(km.DirectStateGit(proot, interval=0.0).observe_sync(), {})

    def test_unreachable_remote_is_unknown_not_lost(self):
        # リモートに触れないことを「fence 喪失」と同一視しない。同一視すると、一過性の通信断で
        # 完成した成果が settle 時に破棄される。
        cfg = self._cfg(node="pc-a", coordination_retries=1)
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)
        km.claim_distributed_task(cfg, "T1")
        task = km.load_tasks(cfg.backlog)[0]
        self.assertEqual(km.claim_fence_state(cfg, task), "ok")
        subprocess.run(["git", "-C", str(self.root), "remote", "set-url", "origin",
                        "file:///no-such-remote.git"], check=True)
        # bool の合否に畳むと "lost" と区別が付かず、届かないだけの claim を破棄側へ落とす。
        # 破棄するかどうかは 3 値を見る側の責務であることをここで固定する。
        self.assertEqual(km.claim_fence_state(cfg, task), "unknown")

    def test_settle_with_unreachable_remote_preserves_work_for_human(self):
        # unknown は破棄でも自動採用でもなく、人の判断へ隔離する（実行ノード消失時と同形）。
        cfg = self._cfg(node="pc-a", coordination_retries=1)
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)
        km.claim_distributed_task(cfg, "T1")
        task = km.load_tasks(cfg.backlog)[0]
        subprocess.run(["git", "-C", str(self.root), "remote", "set-url", "origin",
                        "file:///no-such-remote.git"], check=True)
        deltas = km._settle_task(cfg, task, "local", "done", 1, 0, 0.0, None, {},
                                 km.load_policy(cfg.policy), {}, {})
        self.assertEqual(deltas, {"archived": 0, "followups": []})
        settled = km.load_tasks(cfg.backlog)[0]
        self.assertEqual(settled.status, "blocked")             # 自動採用しない
        self.assertTrue((cfg.needs / "T1.md").exists())         # 人の判断へ回す
        self.assertIn("リモート不通", (cfg.needs / "T1.md").read_text(encoding="utf-8"))
        self.assertNotIn("破棄", cfg.journal.read_text(encoding="utf-8"))

    def test_controller_balances_unassigned_ready_tasks_across_active_nodes(self):
        controller = self._cfg(node="pc-a")
        mkb(self.root, "T1")
        mkb(self.root, "T2")
        statuses = self.root / "status"
        statuses.mkdir(exist_ok=True)     # フィクスチャの mk_peer が先に作っている
        now = datetime.now(timezone.utc).isoformat()
        for node in ("pc-a", "pc-b"):
            (statuses / f"{node}.json").write_text(json.dumps({
                "node": node, "availability": "active", "updated_iso": now,
                "fresh_after_sec": 120,
            }), encoding="utf-8")
        km.state_sync(controller, force=True)
        self.assertEqual(km.allocate_distributed_tasks(controller), {"T1": "pc-a", "T2": "pc-b"})
        assigned = {task.id: task.get("node") for task in km.load_tasks(controller.backlog)}
        self.assertEqual(assigned, {"T1": "pc-a", "T2": "pc-b"})

    def test_draining_node_releases_controller_for_another_node(self):
        first = self._cfg(node="pc-a", availability={
            "timezone": "Asia/Tokyo", "daily_stop": "23:00", "drain_before_sec": 1800,
        })
        active = datetime(2026, 7, 22, 13, 29, 50, tzinfo=timezone.utc)
        draining = active + timedelta(seconds=20)
        self.assertTrue(km.renew_controller_lease(first, at=active))
        self.assertFalse(km.renew_controller_lease(first, at=draining))
        other = self._other("pc-b-drain")
        second = cfg_for(other, node="pc-b")
        self.assertTrue(km.renew_controller_lease(second, at=draining))

    def test_planned_shutdown_requeues_owned_doing_without_retry_penalty(self):
        cfg = self._cfg(node="pc-a")
        mkb(self.root, "T1", retries=1)
        km.state_sync(cfg, force=True)
        self.assertTrue(km.claim_distributed_task(cfg, "T1"))
        self.assertEqual(km.requeue_draining_tasks(cfg), ["T1"])
        task = km.load_tasks(cfg.backlog)[0]
        self.assertEqual((task.status, task.retries), ("ready", 1))
        self.assertEqual(task.get("claim_generation"), "2")

    def test_controller_heartbeat_renews_lease_during_long_work(self):
        controller = self._cfg(node="pc-a",
                               controller_lease_sec=0.4, controller_heartbeat_sec=0.05,
                               clock_skew_tolerance_sec=0.0)
        stop = km.start_controller_heartbeat(controller)
        self.addCleanup(stop.set)
        initial = json.loads((self.root / "coordination" / "controller.json").read_text(encoding="utf-8"))
        deadline = time.time() + 2.0
        renewed = initial
        while time.time() < deadline and renewed["lease_until"] <= initial["lease_until"]:
            time.sleep(0.05)
            renewed = json.loads((self.root / "coordination" / "controller.json").read_text(encoding="utf-8"))
        self.assertGreater(renewed["lease_until"], initial["lease_until"])

    def test_sync_survives_divergence_with_a_dirty_worktree(self):
        """リモートが進んでいて、かつ作業ツリーが汚れていても同期できること。

        DirectStateGit は「人の作業を壊さない」ため作業ツリーに触らない（コミットは detached
        worktree で組み、ブランチは CAS で進める）。その結果、未コミット変更が残ったまま
        pull --rebase へ進み `cannot pull with rebase: You have unstaged changes` で必ず失敗する。
        取り込めないと push も non-fast-forward で永久に通らず、リモートとの乖離が広がり続ける
        （実際 viewer が同じブランチへ push した途端に詰まり、分散構成で状態が共有されなくなった）。
        同期の直前に commit_state でコミットしておけば rebase は素直に通る。"""
        cfg = self._cfg()
        cfg.state_top = self.root          # 状態 worktree 相当（commit_state を効かせる）
        cfg.state_commit = True
        cfg.state_backup_branch = ""       # main へのバックアップはこのテストの関心外
        km._last_state_commit = 0.0

        # 他者（viewer 相当）がリモートを先に進める
        other = self._other("viewer")
        (other / "commands").mkdir(parents=True, exist_ok=True)
        (other / "commands" / "approve-x.json").write_text('{"command": "approve"}', encoding="utf-8")
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        for a in (["add", "-A"], ["commit", "-qm", "viewer: approve"], ["push", "-q", "origin", "HEAD:main"]):
            subprocess.run(["git", "-C", str(other), *a], check=True, capture_output=True, env=env)

        # こちらは作業ツリーが汚れている（backlog を書き換えたが未コミット）
        mkb(self.root, "T1")
        self.assertTrue(subprocess.run(["git", "-C", str(self.root), "status", "--porcelain"],
                                       capture_output=True, text=True).stdout.strip(),
                        "前提: 作業ツリーが汚れている")

        # 修正の要: 同期の前にコミットしてクリーンにする（run_loop がこれを行う）
        km.commit_state(cfg, force=True)
        km.state_sync(cfg, force=True)

        # リモートの変更を取り込めている（rebase が通った）
        self.assertTrue((self.root / "commands" / "approve-x.json").exists(),
                        "他者の指示を取り込める")
        # こちらの変更も push できている（non-fast-forward で詰まらない）
        got = self._other("check")
        self.assertTrue((got / "backlog" / "T1.md").exists(), "自分の状態を push できる")

    def test_direct_sync_pushes_state_and_bus_but_excludes_claims(self):
        cfg = self._cfg()
        mkb(self.root, "T1")
        (cfg.bus / "runs").mkdir(parents=True, exist_ok=True)
        (cfg.bus / "runs" / "r1.json").write_text("{}", encoding="utf-8")
        claims = self.root / "claims"
        claims.mkdir(parents=True, exist_ok=True)
        (claims / "T1.lock").write_text("pid", encoding="utf-8")
        km.state_sync(cfg, force=True)
        got = self._other("check")
        self.assertTrue((got / "backlog" / "T1.md").exists())     # subdir 無し・ルート直下に鏡写し
        self.assertTrue((got / "bus" / "runs" / "r1.json").exists(),
                        "bus は同期する（別 PC の viewer が run を見る唯一の経路）")
        self.assertFalse((got / "claims").exists())               # 遅延越しの排他は意味を持たない
        self.assertFalse((self.root / ".state-git").exists())     # 管理クローンは作らない

    def test_state_worktree_does_not_disable_distributed_sync(self):
        """状態 worktree に逃がしていても direct 同期は有効。

        _git_toplevel は「root がリポジトリのトップレベルか」を見る。状態 worktree では root は
        <repo>-agent-state/.agent-project というサブディレクトリになるので False を返し、それだけを
        条件にすると state_git_for も project_flow_remote も None になって **分散同期が丸ごと
        無効化される**（origin に何も push されず、別 PC の viewer が状態と run を読む唯一の経路が
        消える。journal に「state-git: 無効」と出続けていた）。"""
        cfg = self._cfg()
        sub = self.root / "nested" / ".agent-project"       # トップレベルではない root
        sub.mkdir(parents=True, exist_ok=True)
        cfg.backlog = sub / "backlog"
        cfg.backlog.mkdir(parents=True, exist_ok=True)
        self.assertFalse(km._git_toplevel(sub), "前提: サブディレクトリはトップレベルではない")

        cfg.state_top = None
        self.assertFalse(km._direct_state_git_ok(cfg), "worktree でなければ従来どおり発動しない")

        cfg.state_top = self.root                          # 状態 worktree へ逃がしている
        self.assertTrue(km._direct_state_git_ok(cfg), "worktree なら direct 同期を使う")
        km._STATE_GITS.clear()
        self.assertIsNotNone(km.state_git_for(cfg), "同期オブジェクトが得られる（None にならない）")

    def test_direct_sync_commits_even_while_user_index_locked(self):
        # 人の git 操作中（index.lock 保持）でも export は止まらない: コミットは detached
        # worktree（専用 index）で組み立て、ブランチは update-ref で進めるため index を使わない。
        cfg = self._cfg()
        mkb(self.root, "T1")
        lock = self.root / ".git" / "index.lock"
        lock.write_text("", encoding="utf-8")
        try:
            km.state_sync(cfg, force=True)
        finally:
            lock.unlink()
        r = subprocess.run(["git", "-C", str(self.root), "log", "-1", "--format=%s"],
                           capture_output=True, text=True)
        self.assertTrue(r.stdout.strip().startswith("agent-project: state sync"))
        got = self._other("locked-check")
        self.assertTrue((got / "backlog" / "T1.md").exists())   # push まで完走する

    def test_direct_sync_records_deletions(self):
        cfg = self._cfg()
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)
        (self.root / "backlog" / "T1.md").unlink()
        km.state_sync(cfg, force=True)
        got = self._other("del-check")
        self.assertFalse((got / "backlog" / "T1.md").exists())  # 削除も worktree 経由で反映

    def test_direct_merge_remote_needs_deletion_keeps_local_replacement(self):
        """direct モードでも stale な needs 削除より新しい機械票を優先する。"""
        cfg = self._cfg()
        nf = cfg.needs / "T1.md"
        nf.write_text("plan-review\n", encoding="utf-8")
        km.state_sync(cfg, force=True)

        other = self._other("viewer-delete")
        (other / "needs" / "T1.md").unlink()
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "delete stale need"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)

        nf.write_text("blocked with delivery\n", encoding="utf-8")
        km.state_sync(cfg, force=True)
        got = self._other("needs-check")
        self.assertEqual((got / "needs" / "T1.md").read_text(encoding="utf-8"),
                         "blocked with delivery\n")

    def test_direct_sync_keeps_working_tree_clean_after_export(self):
        # CAS でブランチを進めた後、対象パスの index を新 HEAD に追随させる
        # （作業ツリー内容＝コミット内容なので status が clean に戻る）。
        cfg = self._cfg()
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)
        r = subprocess.run(["git", "-C", str(self.root), "status", "--porcelain",
                            "--", "backlog"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "")

    def test_direct_sync_declares_union_merge_for_journal(self):
        cfg = self._cfg()
        km.state_sync(cfg, force=True)
        attrs = self.root / ".git" / "info" / "attributes"
        self.assertTrue(attrs.is_file())
        self.assertIn("journal.md merge=union", attrs.read_text(encoding="utf-8"))

    def test_direct_sync_merges_concurrent_journal_appends_without_conflict(self):
        # 追記専用の journal.md は union マージで、両ホストの追記行が両方残る（EOF 衝突しない）。
        cfg = self._cfg()
        km.append_journal(self.root / "journal.md", "base line")
        km.state_sync(cfg, force=True)                      # base を共有
        other = self._other("journal-writer")
        with (other / "journal.md").open("a", encoding="utf-8") as f:
            f.write("- 2026-07-12 00:00:00 remote line\n")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "remote journal"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        km.append_journal(self.root / "journal.md", "local line")
        km.state_sync(cfg, force=True)                      # 衝突 → rebase + union で合流
        got = self._other("journal-check")
        text = (got / "journal.md").read_text(encoding="utf-8")
        self.assertIn("remote line", text)
        self.assertIn("local line", text)

    def test_direct_sync_imports_remote_instruction(self):
        cfg = self._cfg()
        other = self._other()
        cmd = other / "commands" / "ok.json"
        cmd.parent.mkdir(parents=True, exist_ok=True)
        cmd.write_text('{"command": "pause"}', encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "viewer: pause"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        km.state_sync(cfg, force=True)
        self.assertTrue((km.commands_dir(cfg) / "ok.json").exists())

    # --- 状態共有 direct 一本化（実装計画 W1-7）で管理クローン（旧 TestStateGitSync）から
    # 移植したコントラクトテスト。裏付けるバックエンドが変わっただけで、検証したい規則
    # （人の入力はリモート優先・機械状態はローカル優先・interval 律速・障害耐性・
    # 再起動時の取り込み順序）はそのまま。

    def test_conflict_human_input_prefers_remote(self):
        cfg = self._cfg()
        nf = cfg.needs / "T1.md"
        nf.write_text("machine\n", encoding="utf-8")
        km.state_sync(cfg, force=True)
        other = self._other()
        rn = other / "needs" / "T1.md"
        rn.write_text("human answer\n", encoding="utf-8")    # 人がリモートで記入
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "human feedback"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        nf.write_text("machine rewrite\n", encoding="utf-8")  # 同時にローカルも変更
        km.state_sync(cfg, force=True)
        self.assertEqual(nf.read_text(encoding="utf-8"), "human answer\n")

    def test_conflict_repos_registry_prefers_remote(self):
        # repos.{json,yaml,yml} は人が書くレジストリ（charter ## repos の互換入力）なので
        # policy.md / charter.md と同じくリモート優先（viewer 側の編集を取りこぼさない）。
        cfg = self._cfg()
        rf = self.root / "repos.json"
        rf.write_text('{"app": {"url": "git@h:t/a.git"}}\n', encoding="utf-8")
        km.state_sync(cfg, force=True)
        other = self._other()
        rr = other / "repos.json"
        rr.write_text('{"app": {"url": "git@h:t/a.git", "base": "main"}}\n', encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "viewer: edit repos"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        rf.write_text('{"app": {"url": "git@h:t/a.git", "base": "dev"}}\n', encoding="utf-8")
        km.state_sync(cfg, force=True)
        self.assertIn('"base": "main"', rf.read_text(encoding="utf-8"))

    def test_conflict_machine_state_prefers_local(self):
        cfg = self._cfg()
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)
        other = self._other()
        rb = other / "backlog" / "T1.md"
        rb.write_text("remote edit\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "remote edit"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        local = self.root / "backlog" / "T1.md"
        local.write_text("local truth\n", encoding="utf-8")
        km.state_sync(cfg, force=True)
        self.assertEqual(local.read_text(encoding="utf-8"), "local truth\n")

    def test_concurrent_committer_is_not_clobbered(self):
        # 他プログラムが（我々の pull の後に）同一リポジトリへ push しても、push 競合を
        # rebase + CAS 再試行で吸収して自分の変更を反映し、相手のコミットも壊さない。
        cfg = self._cfg(state_git_interval=3600.0)
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)
        other = self._other()
        (other / "unrelated.txt").write_text("theirs\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "other program commit"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        (self.root / "backlog" / "T2.md").write_text("## T2: x\n- status: ready\n", encoding="utf-8")
        km.state_sync(cfg, force=True)   # interval 内 → fetch せず push → 非 FF → 取り込んで再試行
        got = self._other("check")
        self.assertTrue((got / "unrelated.txt").exists())
        self.assertTrue((got / "backlog" / "T2.md").exists())

    def test_interval_rate_limits_remote_fetch(self):
        cfg = self._cfg(state_git_interval=3600.0)
        km.state_sync(cfg, force=True)                       # 初回は必ず同期
        other = self._other()
        drop = other / "inbox" / "task.json"
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_text('{"title": "x", "verify": "true"}', encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "drop"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        km.state_sync(cfg)                                   # interval 内 → fetch しない（負荷律速）
        self.assertFalse((cfg.inbox / "task.json").exists())
        sg = km.state_git_for(cfg)
        sg._last_remote = 0.0                                # interval 経過を模擬
        km.state_sync(cfg)
        self.assertTrue((cfg.inbox / "task.json").exists())

    def test_run_loop_syncs_state(self):
        # run_loop の入口で指示を取り込み、出口でパスの結果（journal 等）を共有側へ押し出す。
        cfg = self._cfg()
        result = km.run_loop(cfg)
        self.assertEqual(result["reason"], km.REASON_DRAINED)
        got = self._other("check")
        self.assertTrue((got / "journal.md").exists())

    def test_sync_failure_does_not_kill_loop(self):
        # state_sync は「push が反映できませんでした」（DirectStateGit.sync が push リトライ
        # 尽きて raise する唯一の失敗経路 — 詳細は stategit.py の RuntimeError 箇所）を
        # 握り潰さず journal に残して続行する契約を持つ。実際に push リトライ（最大 15s の
        # バックオフ）を待つと遅いので、契約だけを sync() の差し替えで軽く確認する。
        cfg = self._cfg()
        with mock.patch.object(km, "state_git_for") as m:
            sg = mock.Mock()
            sg.sync.side_effect = RuntimeError("push が main へ反映できませんでした")
            m.return_value = sg
            km.state_sync(cfg, force=True)                   # 不通でも例外を漏らさない
        self.assertIn("state-git 同期失敗", cfg.journal.read_text(encoding="utf-8"))

    def test_project_watch_imports_before_first_plan_on_restart(self):
        # 自己更新の graceful 再起動を模擬（_STATE_GITS クリア）。停止中に viewer が push した
        # charter 更新を、初回 plan より先に取り込むこと（古い charter で計画しない）。
        cfg = self._cfg()
        cfg.charter.write_text("# Charter\n## 目標\nGOAL-A\n## acceptance\n- true\n",
                               encoding="utf-8")
        km.state_sync(cfg, force=True)                       # 初期 export（GOAL-A をリモートへ）
        other = self._other()
        rc = other / "charter.md"
        rc.write_text("# Charter\n## 目標\nGOAL-B\n## acceptance\n- true\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "viewer: charter 更新"],
                       check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)       # 停止中に GOAL-B を push
        km._STATE_GITS.clear()                               # 再起動を模擬
        seen = []
        km.project_watch(cfg, planner=lambda ch: seen.append(
            "B" if "GOAL-B" in cfg.charter.read_text(encoding="utf-8") else "A") or [],
            reviewer=lambda ch: (True, ""), runner=km.run_loop,
            sleeper=lambda _s: None, max_passes=1)
        self.assertEqual(seen, ["B"])                        # 初回 plan は取り込み後の charter を見る

    def test_uninitialized_root_auto_git_inits_and_syncs_locally(self):
        # 状態共有は direct 一本（実装計画 W1-7）: root が未初期化でも state_git_for がその場で
        # git init し、remote 未設定ならコミットのみのローカル縮退として動く（例外にならない）。
        proot = Path(tempfile.mkdtemp(prefix="uninit-root-")) / "proj"
        cfg = km.Config(backlog=proot / "backlog", policy=proot / "policy.md",
                        decisions=proot / "decisions", journal=proot / "journal.md",
                        needs=proot / "needs", workdir=self.tmp, bus=proot / "bus",
                        inbox=proot / "inbox",
                        planner="none", flow_planner="stub", executor="stub", dry_run=True,
                        state_git_interval=0.0)
        km.ensure_dirs(cfg)
        self.assertFalse((proot / ".git").exists(), "前提: 未初期化")
        mkb(proot, "T1")
        km.state_sync(cfg, force=True)
        self.assertTrue((proot / ".git").exists(), "state_git_for が git init するはず")
        self.assertIsInstance(km.state_git_for(cfg), km.DirectStateGit)

    def test_state_git_config_bootstraps_origin_when_missing(self):
        # state_git: <url> 設定は「管理クローンの同期先」の意味を失ったが、root に origin が
        # 無ければそれを origin として設定する（設定がサイレントに意味を失わないように）。
        proot = Path(tempfile.mkdtemp(prefix="bootstrap-origin-")) / "proj"
        cfg = km.Config(backlog=proot / "backlog", policy=proot / "policy.md",
                        decisions=proot / "decisions", journal=proot / "journal.md",
                        needs=proot / "needs", workdir=self.tmp, bus=proot / "bus",
                        inbox=proot / "inbox",
                        planner="none", flow_planner="stub", executor="stub", dry_run=True,
                        state_git=str(self.remote), state_git_interval=0.0)
        km.ensure_dirs(cfg)
        km.state_sync(cfg, force=True)
        r = subprocess.run(["git", "-C", str(proot), "remote", "get-url", "origin"],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), str(self.remote))

    def _cfg_under_existing_repo(self):
        """root が既存リポジトリの内側（トップレベルではない）という構成を作る。"""
        outer = Path(tempfile.mkdtemp(prefix="outer-repo-"))
        subprocess.run(["git", "init", "-q", str(outer)], check=True,
                       capture_output=True, text=True)
        proot = outer / "sub" / ".agent-project"
        return outer, proot, km.Config(
            backlog=proot / "backlog", policy=proot / "policy.md",
            decisions=proot / "decisions", journal=proot / "journal.md",
            needs=proot / "needs", workdir=self.tmp, bus=proot / "bus",
            inbox=proot / "inbox",
            planner="none", flow_planner="stub", executor="stub", dry_run=True,
            state_git_interval=0.0)

    def test_root_inside_existing_repo_does_not_create_nested_repo(self):
        # root が無関係な既存リポジトリの内側にあるとき git init してはいけない。nested repo は
        # 外側の `git add -A` を「does not have a commit checked out」で失敗させ、commit を積むと
        # gitlink 化して意図しない submodule 相当のエントリを持ち込む。
        outer, proot, cfg = self._cfg_under_existing_repo()
        km.ensure_dirs(cfg)
        km.state_sync(cfg, force=True)
        self.assertFalse((proot / ".git").exists(), "既存リポジトリの内側に nested repo を作らない")
        self.assertIsNone(km.state_git_for(cfg))         # 同期は諦める（毎周期の失敗を出さない）
        # 外側リポジトリの通常操作が壊れていないこと（nested repo があるとここが fatal になる）
        r = subprocess.run(["git", "-C", str(outer), "add", "-A"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_status_line_explains_inside_existing_repo(self):
        # 起動時の一行は切り分け用。ここで「未初期化 → 次回 init する」と出すと実態と食い違う。
        _outer, _proot, cfg = self._cfg_under_existing_repo()
        km.ensure_dirs(cfg)
        line = km.state_git_status_line(cfg)
        self.assertIn("既存 git リポジトリの内側", line)
        self.assertNotIn("git init する", line)


class StateWorktreeTests(unittest.TestCase):
    """状態の読み書きを、本体の作業ツリーから切り離した専用 worktree へ逃がす。

    agent-project は watch 中 5 秒ごとに journal / status.json / run-log / project.json を書き換える。
    本体の中に置くと人の git status が永久に dirty になり、人やツールの git 操作
    （stash / rebase / pull --autostash）が書き込み中の状態ファイルを巻き込んで壊す
    （実際に project.json がコンフリクトマーカーで JSON として読めなくなった）。"""

    def _repo(self):
        # git は toplevel を realpath で返す（macOS の /var → /private/var）。揃えておく。
        top = Path(tempfile.mkdtemp(prefix="kp-state-")).resolve()
        self.addCleanup(shutil.rmtree, top, True)
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        run = lambda *a: subprocess.run(a, cwd=top, capture_output=True, env=env)
        run("git", "init", "-b", "main", ".")
        run("git", "config", "user.email", "t@e.com")
        run("git", "config", "user.name", "t")
        (top / "README.md").write_text("x\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "init")
        self.addCleanup(lambda: shutil.rmtree(top.parent / f"{top.name}-agent-state", True))
        return top

    def test_root_is_redirected_into_a_worktree(self):
        top = self._repo()
        root, state_top = km._redirect_root_to_state_worktree(
            top / ".agent-project", "", "agent-state")
        self.assertEqual(state_top, top)
        self.assertNotIn(str(top / ".agent-project"), str(root))     # 本体の中ではない
        self.assertTrue((root.parent / ".git").exists(), "worktree の中を指す")
        # ブランチが切られている
        r = subprocess.run(["git", "-C", str(root.parent), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "agent-state")

    def test_writing_state_does_not_dirty_the_main_worktree(self):
        top = self._repo()
        root, _ = km._redirect_root_to_state_worktree(top / ".agent-project", "", "agent-state")
        root.mkdir(parents=True, exist_ok=True)
        (root / "journal.md").write_text("- 稼働中\n")          # 本体が 5 秒ごとに書くもの
        (root / "status.json").write_text('{"watch": true}\n')
        dirty = subprocess.run(["git", "-C", str(top), "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        self.assertEqual(dirty.strip(), "", "本体の作業ツリーは汚れない")

    def test_existing_state_is_migrated_once(self):
        top = self._repo()
        src = top / ".agent-project"
        (src / "backlog").mkdir(parents=True)
        (src / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        root, _ = km._redirect_root_to_state_worktree(src, "", "agent-state")
        self.assertTrue((root / "backlog" / "T1.md").is_file(), "既存の状態が引っ越す")
        self.assertFalse(src.exists(), "本体側は残さない（二重管理を作らない）")

    def test_worktree_checks_out_only_the_state_dir(self):
        """状態 worktree は状態ディレクトリだけを sparse checkout する。

        既定ではリポジトリ全体が展開され、tools/ や docs/ の丸ごとコピーが隣に生える。
        ディスクの無駄というより、人が worktree 側の tools/ を本物と思って編集する事故が怖い
        （そこでの変更は agent-state ブランチに乗るだけで main には決して届かない）。"""
        top = self._repo()
        (top / "tools").mkdir()
        (top / "tools" / "app.py").write_text("x = 1\n")
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        subprocess.run(["git", "-C", str(top), "add", "-A"], capture_output=True, env=env)
        subprocess.run(["git", "-C", str(top), "commit", "-m", "tools"],
                       capture_output=True, env=env)
        # 本体側に既存の状態がある（初回起動＝worktree へ引っ越す形）
        src = top / ".agent-project"
        (src / "backlog").mkdir(parents=True)
        (src / "backlog" / "T0.md").write_text("## T0\n")

        root, _ = km._redirect_root_to_state_worktree(src, "", "agent-state")
        wt = root.parent
        self.assertTrue(root.is_dir(), "状態ディレクトリは出ている")
        self.assertTrue((root / "backlog" / "T0.md").is_file(), "既存の状態は引っ越す")
        # ソースのディレクトリは展開しない（人がここの tools/ を本物と思って編集する事故を防ぐ）。
        # cone モードはルート直下の *ファイル* だけは常に置く（README.md 等）。嵩むのはディレクトリ
        # なので、これで目的は足りる。
        self.assertFalse((wt / "tools").exists(), "他のソースのディレクトリは展開しない")

        # sparse は作業ツリーの見え方だけ。ブランチの中身は完全なので状態のコミットは通る
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        r = subprocess.run(["git", "-C", str(root), "add", "-A", "--", "."],
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0)
        c = subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "state", "--", "."],
                           capture_output=True, env=env)
        self.assertEqual(c.returncode, 0, "状態はコミットできる")
        # 展開していない tools/ が「削除された」と誤認されない（skip-worktree）
        tree = subprocess.run(["git", "-C", str(wt), "ls-tree", "-r", "--name-only", "HEAD"],
                              capture_output=True, text=True, env=env).stdout
        self.assertIn("tools/app.py", tree, "ブランチの中身は完全なまま")

    def test_reuses_the_worktree_on_restart(self):
        top = self._repo()
        a, _ = km._redirect_root_to_state_worktree(top / ".agent-project", "", "agent-state")
        a.mkdir(parents=True, exist_ok=True)
        (a / "mark.txt").write_text("keep\n")
        b, _ = km._redirect_root_to_state_worktree(top / ".agent-project", "", "agent-state")
        self.assertEqual(a, b, "切りっぱなしの worktree を再利用する")
        self.assertTrue((b / "mark.txt").is_file(), "中身を消さない")

    def test_non_git_root_is_left_alone(self):
        d = Path(tempfile.mkdtemp(prefix="kp-nogit-"))
        self.addCleanup(shutil.rmtree, d, True)
        root, state_top = km._redirect_root_to_state_worktree(d / "p", "", "agent-state")
        self.assertEqual(root, d / "p")
        self.assertIsNone(state_top)


class StateCommitTests(unittest.TestCase):
    """状態のコミット: 人の判断が動いたら即、実行の副産物だけならまとめる。

    watch は 5 秒ごとに journal / status.json を書き換えるので、毎回コミットすると履歴が秒単位で
    埋まって読めない。意味のある変化（backlog / needs / decisions …）と、実行の副産物を分ける。"""

    def _cfg(self):
        top = Path(tempfile.mkdtemp(prefix="kp-sc-")).resolve()
        self.addCleanup(shutil.rmtree, top, True)
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        run = lambda *a: subprocess.run(a, cwd=top, capture_output=True, env=env)
        run("git", "init", "-b", "main", ".")
        run("git", "config", "user.email", "t@e.com")
        run("git", "config", "user.name", "t")
        (top / "README.md").write_text("x\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "init")
        self.addCleanup(lambda: shutil.rmtree(top.parent / f"{top.name}-agent-state", True))
        root, state_top = km._redirect_root_to_state_worktree(
            top / ".agent-project", "", "agent-state")
        root.mkdir(parents=True, exist_ok=True)
        cfg = cfg_for(root)
        cfg.state_top = state_top
        cfg.state_commit = True
        cfg.state_commit_interval = 3600.0        # 副産物はまとめる（テスト中は跨がない）
        km._last_state_commit = 0.0
        return cfg, root

    def _log(self, root):
        return subprocess.run(["git", "-C", str(root), "log", "--oneline"],
                              capture_output=True, text=True).stdout.strip().split("\n")

    def test_meaningful_change_commits_immediately(self):
        cfg, root = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        self.assertTrue(km.commit_state(cfg))
        self.assertIn("状態を更新", self._log(root)[0])

    def test_noise_only_change_is_batched(self):
        cfg, root = self._cfg()
        km._last_state_commit = time.time()             # 直前にコミット済み＝間隔内
        (root / "journal.md").write_text("- 監視中\n")   # 5 秒ごとの副産物
        (root / "status.json").write_text('{"watch": true}\n')
        self.assertFalse(km.commit_state(cfg), "間隔内はまとめる（コミットしない）")

    def test_noise_commits_once_the_interval_passes(self):
        cfg, root = self._cfg()
        cfg.state_commit_interval = 0.0                 # 間隔ゼロ＝毎回
        (root / "journal.md").write_text("- 監視中\n")
        self.assertTrue(km.commit_state(cfg))
        self.assertIn("実行ログを更新", self._log(root)[0])

    def test_main_worktree_is_never_touched(self):
        cfg, root = self._cfg()
        top = cfg.state_top
        (top / "wip.txt").write_text("人の編集中\n")      # 人が本体で作業している
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        km.commit_state(cfg)
        dirty = subprocess.run(["git", "-C", str(top), "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        self.assertIn("wip.txt", dirty, "人の変更はそのまま（コミットも stash もされない）")
        staged = subprocess.run(["git", "-C", str(top), "diff", "--cached", "--name-only"],
                                capture_output=True, text=True).stdout
        self.assertEqual(staged.strip(), "", "本体の index に触らない")


class StateBackupTests(unittest.TestCase):
    """状態を正本ブランチ（既定 main）へバックアップする。

    状態の実体は worktree（agent-state）にあり、そこが読み書きの正。正本ブランチへ載せるのは
    バックアップであって共有ではない。だから「人の判断が動いたときだけ」「1 同期 1 コミット」
    「本体の作業ツリーには触らない」「失敗しても本業を止めない」を守る。"""

    def _cfg(self, backup="main"):
        top = Path(tempfile.mkdtemp(prefix="kp-bk-")).resolve()
        self.addCleanup(shutil.rmtree, top, True)
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        run = lambda *a: subprocess.run(a, cwd=top, capture_output=True, env=env)
        run("git", "init", "-b", "main", ".")
        run("git", "config", "user.email", "t@e.com")
        run("git", "config", "user.name", "t")
        (top / "README.md").write_text("x\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "init")
        self.addCleanup(lambda: shutil.rmtree(top.parent / f"{top.name}-agent-state", True))
        root, state_top = km._redirect_root_to_state_worktree(top / ".agent-project", "", "agent-state")
        root.mkdir(parents=True, exist_ok=True)
        cfg = cfg_for(root)
        cfg.state_top = state_top
        cfg.state_commit = True
        cfg.state_commit_interval = 3600.0
        cfg.state_backup_branch = backup
        km._last_state_commit = 0.0
        return cfg, root, top

    def _show(self, top, ref):
        r = subprocess.run(["git", "-C", str(top), "show", ref], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    def _count(self, top, branch):
        r = subprocess.run(["git", "-C", str(top), "rev-list", "--count", branch],
                           capture_output=True, text=True)
        return int(r.stdout.strip() or 0)

    def test_meaningful_change_is_backed_up_to_main(self):
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        self.assertTrue(km.commit_state(cfg))
        self.assertIn("status: ready", self._show(top, "main:.agent-project/backlog/T1.md") or "",
                      "人の判断が動いたら正本へバックアップされる")

    def test_noise_is_not_pushed_to_main(self):
        # journal / status.json は 5 秒ごとに変わる。正本へ流すとコミットが埋まり、本体で
        # 作業している人の git status も落ち着かない。worktree 側の履歴に留める。
        cfg, root, top = self._cfg()
        cfg.state_commit_interval = 0.0
        before = self._count(top, "main")
        (root / "journal.md").write_text("- 監視中\n")
        self.assertTrue(km.commit_state(cfg), "worktree にはコミットされる")
        self.assertEqual(self._count(top, "main"), before, "正本は動かさない")

    def test_backup_resyncs_a_stale_checkout_instead_of_wedging(self):
        """本体側の .agent-project が古くても、バックアップのたびに HEAD へ揃え直す。

        ここを「差分＝人の編集かもしれない」と見て避けると自己永続的に詰む: 一度ずれた瞬間に
        永久に同期されなくなり、古いスナップショットが index に staged のまま居座る。その状態で
        main に git commit（パス指定なし）を打つと、バックアップが古い状態へ巻き戻る。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        self.assertTrue(km.commit_state(cfg))                      # main へバックアップ

        stale = top / ".agent-project" / "backlog" / "T1.md"        # 本体側の鏡を古い内容へ汚す
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("## T1\n- status: review\n- retries: 9\n")
        subprocess.run(["git", "-C", str(top), "add", "--", ".agent-project"],
                       check=True, capture_output=True)            # index に staged のまま残る状況

        (root / "backlog" / "T1.md").write_text("## T1\n- status: done\n")   # 次の意味ある変化
        self.assertTrue(km.commit_state(cfg))

        self.assertIn("status: done", self._show(top, "main:.agent-project/backlog/T1.md") or "")
        self.assertEqual(stale.read_text(), "## T1\n- status: done\n", "鏡が HEAD へ揃う")
        r = subprocess.run(["git", "-C", str(top), "status", "--porcelain", "--",
                            ".agent-project"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "", "staged の古いスナップショットが残らない")

    def test_human_edit_in_mirror_is_adopted_not_destroyed(self):
        """本体側 <repo>/.agent-project への人の編集は、状態へ取り込んでから鏡を揃える。

        人にとって正本は <repo>/.agent-project なのに、状態の読み書きは worktree へ逃げている。
        取り込まないと編集は **効かないまま黙って消える**（実際 agent-flow.yaml の
        evaluator を codex へ切り替えた編集が丸ごと無視されていた）。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        (root / "agent-flow.yaml").write_text("agents:\n  evaluator: claude\n")
        self.assertTrue(km.commit_state(cfg))                    # main へバックアップ（鏡も揃う）

        mirror = top / ".agent-project" / "agent-flow.yaml"
        self.assertEqual(mirror.read_text(), "agents:\n  evaluator: claude\n")
        mirror.write_text("agents:\n  evaluator: codex\n")       # 人が本体側を編集

        (root / "backlog" / "T1.md").write_text("## T1\n- status: done\n")   # 次の意味ある変化
        self.assertTrue(km.commit_state(cfg))

        self.assertEqual((root / "agent-flow.yaml").read_text(),
                         "agents:\n  evaluator: codex\n", "人の編集が状態へ取り込まれる")
        self.assertTrue(km.commit_state(cfg, force=True))        # 取り込んだ内容が正本へ戻る
        self.assertIn("codex", self._show(top, "main:.agent-project/agent-flow.yaml") or "")

    def test_stale_mirror_never_rolls_back_machine_state(self):
        """鏡が古くても、機械が書く状態（backlog 等）は絶対に取り込まない。

        鏡は正本ブランチから遅れうる（バックアップは意味のある変化のときしか走らない）ので、
        「差分＝人の編集」と読むと古い内容で live な状態を巻き戻す。実際それをやって doing の
        タスクが proposed へ戻り、削除済みの cancel ファイルが復活した。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: proposed\n")
        (root / "agent-flow.yaml").write_text("agents:\n  evaluator: claude\n")
        self.assertTrue(km.commit_state(cfg))                      # 鏡＝この時点の内容

        # 状態だけが先へ進む（鏡は古いまま＝バックアップ前）
        (root / "backlog" / "T1.md").write_text("## T1\n- status: doing\n")
        mirror_cfg = top / ".agent-project" / "agent-flow.yaml"
        mirror_cfg.write_text("agents:\n  evaluator: codex\n")     # 人は設定だけを触った

        km.sync_mirror_edits(cfg)
        self.assertEqual((root / "backlog" / "T1.md").read_text(),
                         "## T1\n- status: doing\n", "古い鏡で状態を巻き戻さない")
        self.assertEqual((root / "agent-flow.yaml").read_text(),
                         "agents:\n  evaluator: codex\n", "設定への人の編集だけ取り込む")

    def test_backup_resyncs_mirror_even_when_nothing_to_commit(self):
        """バックアップ済み（＝新しいコミットは不要）でも、鏡がずれていれば揃え直す。

        ここで早期 return すると、詰んだ状態がまさにそこで止まり続ける: 状態が落ち着いている
        限りバックアップは不要と判断され、古いスナップショットは index に staged のまま
        永久に残る。コミットが要らないときこそ揃え直す必要がある。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        self.assertTrue(km.commit_state(cfg))

        stale = top / ".agent-project" / "backlog" / "T1.md"      # 鏡だけを汚す（状態そのものは不変）
        stale.write_text("## T1\n- status: review\n- retries: 9\n")
        subprocess.run(["git", "-C", str(top), "add", "--", ".agent-project"],
                       check=True, capture_output=True)

        km.backup_state(cfg)                                     # 積むものは無いが鏡は揃うはず
        self.assertEqual(stale.read_text(), "## T1\n- status: ready\n")
        r = subprocess.run(["git", "-C", str(top), "status", "--porcelain", "--",
                            ".agent-project"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "", "staged の古いスナップショットが残らない")

    def test_backup_does_not_pile_up_commits(self):
        """何度同期しても、正本ブランチに積まれるバックアップは 1 コミットに保たれる。

        毎回 old を親にして積むと、同期のたびに 1 コミット増え、正本ブランチが
        「状態をバックアップ（自動）」で埋まる（実際 main に 18 件積み上がり、「main を極力
        汚染しない」という前提が崩れた）。バックアップは履歴ではなく「その時点の状態」なので、
        未 push の間は置き換えてよい。worktree 側には従来どおり全履歴が残る。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        before = self._count(top, "main")
        for i in range(3):
            (root / "backlog" / f"T{i}.md").write_text(f"## T{i}\n")
            km.commit_state(cfg)
        self.assertEqual(self._count(top, "main"), before + 1,
                         "何度同期しても正本には 1 コミットだけ")
        self.assertGreaterEqual(self._count(top, "agent-state"), 3, "worktree 側には履歴が残る")
        # 最新の状態がちゃんと載っている（置き換えても内容を落とさない）
        self.assertIsNotNone(self._show(top, "main:.agent-project/backlog/T2.md"))
        self.assertIsNotNone(self._show(top, "main:.agent-project/backlog/T0.md"))

    def test_backup_never_rewrites_pushed_history(self):
        # push 済みのバックアップコミットは書き換えない（新しいコミットとして積む）
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        km.commit_state(cfg)
        n = self._count(top, "main")

        # 「push 済み」に見せる（origin/main が現在のバックアップコミットを含む）
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        subprocess.run(["git", "-C", str(top), "update-ref", "refs/remotes/origin/main", "main"],
                       capture_output=True, env=env, check=True)
        self.assertTrue(km._is_pushed(top, "main", "main"), "前提: push 済みと判定される")

        (root / "backlog" / "T2.md").write_text("## T2\n")
        km._last_state_commit = 0.0
        km.commit_state(cfg)
        self.assertEqual(self._count(top, "main"), n + 1, "push 済みなら積む（履歴を壊さない）")

    def test_human_on_another_branch_is_not_disturbed(self):
        # 人が別ブランチで作業していても、正本の ref を進めるだけ（作業ツリーに触らない）
        cfg, root, top = self._cfg()
        subprocess.run(["git", "-C", str(top), "checkout", "-q", "-b", "feature"],
                       capture_output=True)
        (top / "wip.txt").write_text("作業中\n")
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        km.commit_state(cfg)
        head = subprocess.run(["git", "-C", str(top), "symbolic-ref", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, "feature", "人のブランチを動かさない")
        self.assertFalse((top / ".agent-project").exists(), "人の作業ツリーに書き戻さない")
        self.assertIsNotNone(self._show(top, "main:.agent-project/backlog/T1.md"),
                             "それでも正本にはバックアップされる")
        dirty = subprocess.run(["git", "-C", str(top), "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        self.assertIn("wip.txt", dirty, "人の変更はそのまま")

    def test_identical_state_makes_no_empty_commit(self):
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        km.commit_state(cfg)
        n = self._count(top, "main")
        self.assertFalse(km.backup_state(cfg), "同じ内容なら何もしない")
        self.assertEqual(self._count(top, "main"), n, "空コミットを作らない")

    def test_missing_backup_branch_is_ignored(self):
        # 正本ブランチが無い運用（別ブランチ名・浅いクローン）でも実行を止めない
        cfg, root, top = self._cfg(backup="nonexistent")
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        self.assertTrue(km.commit_state(cfg), "本業（worktree へのコミット）は成功する")
        self.assertFalse(km.backup_state(cfg), "バックアップは黙って諦める")

    def test_backup_can_be_disabled(self):
        cfg, root, top = self._cfg(backup="")
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        before = self._count(top, "main")
        km.commit_state(cfg)
        self.assertEqual(self._count(top, "main"), before, "空設定でバックアップ無効")

    def test_no_change_no_commit(self):
        cfg, _root, _top = self._cfg()
        cfg.state_commit_interval = 0.0
        self.assertFalse(km.commit_state(cfg))

    def test_sibling_project_state_is_not_swallowed(self):
        """同じリポジトリの別プロジェクトの状態を、自分のコミットに巻き込まない。

        <repo>/.agent-project と <repo>/sub/.agent-project は state worktree を共有する。
        commit を root 配下に限定しないと index 全体をコミットしてしまい、隣が add した直後に
        自分が commit すると相手の状態を取り込む。取り込まれた側は「ステージに何も乗らない」と
        判断して自分のコミットを作れず、結果として **相手の状態が正本へバックアップされない**。"""
        cfg, root, top = self._cfg(backup="")
        sub = km._redirect_root_to_state_worktree(top / "sub" / ".agent-project", "", "agent-state")[0]
        (sub / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "A1.md").write_text("## A1\n")
        (sub / "backlog" / "B1.md").write_text("## B1\n")
        # 隣（sub）が自分の commit より先にステージへ載せる
        subprocess.run(["git", "-C", str(sub), "add", "-A", "--", "."], capture_output=True)
        self.assertTrue(km.commit_state(cfg))
        wt = km._git_toplevel_of(root)
        files = subprocess.run(["git", "-C", str(wt), "show", "--name-only", "--format=", "HEAD"],
                               capture_output=True, text=True).stdout
        self.assertIn(".agent-project/backlog/A1.md", files, "自分の状態はコミットする")
        self.assertNotIn("sub/", files, "隣の状態は巻き込まない")
