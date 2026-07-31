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

    def test_sync_realigns_phantom_staged_index_after_interrupted_export(self):
        """CAS 成功と index 追随（_refresh_index）の間で死んだプロセスの残骸を自己修復する。

        export は「detached worktree でコミット → CAS でブランチ前進 → 実 index を追随」の
        順で、最後の追随の前に死ぬ（夜間停止の SIGTERM・watchdog abort・電源断）と、HEAD には
        入ったのに index だけ古いパスが残る。作業ツリー＝HEAD なので次の export は「差分なし」で
        何も積まず、`git status` にはステージ済みの変更が**恒久に**表示され続ける（idle 中は
        journal も動かず自然回復しない）＝「状態リポジトリがステージに乗ったまま同期が
        止まった」ように見える。次の sync の自己修復（_realign_index）で解消すること。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._init_repo(d)
            sg = km.DirectStateGit(d, interval=0.0)
            (d / "journal.md").write_text("a\n", encoding="utf-8")
            (d / "backlog").mkdir()
            (d / "backlog" / "T1.md").write_text("## T1: t\n- status: ready\n", encoding="utf-8")
            sg.sync()
            with open(d / "journal.md", "a", encoding="utf-8") as f:
                f.write("b\n")                                   # 既存ファイルの更新
            (d / "backlog" / "T2.md").write_text("## T2: t\n- status: ready\n",
                                                 encoding="utf-8")   # 新規ファイル
            with mock.patch.object(km.DirectStateGit, "_refresh_index",
                                   lambda self, targets: None):
                sg.sync()                  # クラッシュ窓の再現: CAS 後の index 追随が走らない

            def staged():
                out = subprocess.run(["git", "-C", str(d), "status", "--porcelain"],
                                     capture_output=True, text=True).stdout
                return [ln for ln in out.splitlines() if ln and ln[0] not in " ?"]

            self.assertTrue(staged())      # 幻のステージが残っている（壊れた前提の確認）
            sg.sync()                      # 内容の変更なし＝自己修復だけで clean に戻ること
            self.assertEqual(staged(), [])
            out = subprocess.run(["git", "-C", str(d), "status", "--porcelain"],
                                 capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), "")                    # ?? も残らない
            # 修復が余計なコミットを作っていない（HEAD の内容は既に正しい）
            self.assertEqual(len(self._log(d)), 1)               # 連続 sync は amend で 1 つ

    def test_realign_leaves_real_local_edits_alone(self):
        # 自己修復は「作業ツリー＝HEAD なのに index だけ古い」パスに限る。内容が HEAD と
        # 異なる変更（人のステージ・編集中のファイル）は実差分なので触らず、export に任せる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._init_repo(d)
            sg = km.DirectStateGit(d, interval=0.0)
            (d / "journal.md").write_text("a\n", encoding="utf-8")
            sg.sync()
            (d / "journal.md").write_text("a\nb\n", encoding="utf-8")
            self.assertEqual(sg._realign_index(), 0)             # 実差分は揃えない
            sg.sync()                                            # export が普通に拾う
            r = subprocess.run(["git", "-C", str(d), "status", "--porcelain"],
                               capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "")

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
            (root / "flow-archive").mkdir()
            (root / "journal.md").write_text("a\n", encoding="utf-8")
            (root / "claims" / "T1.lock").write_text("owner\n", encoding="utf-8")
            (root / "bus" / ".state-git").write_text("legacy clone marker\n", encoding="utf-8")
            # 旧 viewer（dashboard の git 書き込み。削除済み）が flow-archive/ を追跡した名残
            (root / "flow-archive" / "run-1.json").write_text('{"id":"run-1"}\n',
                                                              encoding="utf-8")
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
                                 ".agent-project/claims", ".agent-project/bus/.state-git",
                                 ".agent-project/flow-archive"],
                                capture_output=True, text=True)
            self.assertEqual(ls.stdout.strip(), "")                # 除外パスは追跡から外れた
            self.assertTrue((root / "claims" / "T1.lock").exists())  # 実ファイルは消さない
            self.assertTrue((root / "flow-archive" / "run-1.json").exists())

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


class TestStateRootContract(unittest.TestCase):
    """S1: 状態ルート＝状態専用リポジトリの clone、を唯一の方式にする起動契約。

    旧 worktree 方式（state_worktree_dir / state_branch / state_commit / state_push /
    state_backup_branch）と、clone 失敗・origin 不一致での **暗黙フォールバック** は廃止した。
    移行が効いていないことに誰も気付けないまま状態が旧構成へ書かれ続ける事故が起きたため、
    解決できない構成では起動を止める。"""

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
        # 成果物リポジトリ（状態とは別リポジトリ。状態ルートに使ってはいけない側）
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

    def _expect_exit(self, **cli) -> str:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self._build(**cli)
        self.assertEqual(cm.exception.code, 2)
        return err.getvalue()

    def test_state_repo_is_cloned_into_root(self):
        root = self.tmp / "app-state"
        cfg = self._build(root=str(root), state_repo=str(self.state_remote))
        self.assertTrue((root / ".git").exists())            # 状態専用リポジトリを通常 clone した
        self.assertEqual(cfg.backlog.parent, root.resolve())  # root がそのまま状態ルート
        self.assertEqual(cfg.state_repo, str(self.state_remote))

    def test_existing_clone_is_reused(self):
        root = self.tmp / "app-state"
        cfg1 = self._build(root=str(root), state_repo=str(self.state_remote))
        head1 = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout
        cfg2 = self._build(root=str(root), state_repo=str(self.state_remote))
        self.assertEqual(cfg1.backlog, cfg2.backlog)
        self.assertEqual(head1, subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                               capture_output=True, text=True).stdout)

    def test_default_root_for_adhoc_state_repo_is_named_after_the_repo(self):
        cwd = self.tmp / "cwd"
        cwd.mkdir()
        old = os.getcwd()
        os.chdir(cwd)
        try:
            cfg = self._build(root=None, state_repo=str(self.state_remote))
        finally:
            os.chdir(old)
        self.assertEqual(cfg.backlog.parent, (cwd / "state").resolve())

    def test_origin_mismatch_stops_instead_of_falling_back(self):
        # 旧実装はここで黙って worktree 方式へ倒れ、移行が効いていないことに気付けなかった。
        err = self._expect_exit(root=str(self.deliverable), state_repo=str(self.state_remote))
        self.assertIn("clone ではありません", err)
        self.assertIn(str(self.state_remote), err)

    def test_unreachable_state_repo_stops(self):
        bad = str(self.tmp / "does-not-exist.git")
        err = self._expect_exit(root=str(self.tmp / "new-root"), state_repo=bad)
        self.assertIn("clone できませんでした", err)

    def test_deliverable_repo_as_root_is_rejected(self):
        err = self._expect_exit(root=str(self.deliverable), state_repo=None)
        self.assertIn("状態ルートに見えません", err)

    def test_legacy_bootstrap_yaml_is_named_in_the_error(self):
        # 移行前の「成果物リポジトリ直下に state_repo: を置く」構成は、その URL を案内する。
        (self.deliverable / "agent-project.json").write_text(
            json.dumps({"state_repo": str(self.state_remote)}), encoding="utf-8")
        err = self._expect_exit(root=str(self.deliverable), state_repo=None)
        self.assertIn("旧ブートストラップ設定", err)
        self.assertIn("projects[].state_repo", err)

    def test_subdirectory_of_a_repo_is_rejected(self):
        sub = self.deliverable / "sub"
        sub.mkdir()
        (sub / "notes.md").write_text("x\n", encoding="utf-8")
        err = self._expect_exit(root=str(sub), state_repo=None)
        self.assertIn("の内側です", err)

    def test_state_marker_makes_a_plain_clone_acceptable(self):
        # 状態リポジトリを人が手で clone してそこで単発実行する経路（--state-repo 無し）。
        root = self.tmp / "manual"
        subprocess.run(["git", "clone", "-q", str(self.state_remote), str(root)],
                       check=True, capture_output=True)
        (root / "backlog").mkdir()
        cfg = self._build(root=str(root), state_repo=None)
        self.assertEqual(cfg.backlog.parent, root.resolve())

    def test_fresh_directory_is_allowed_as_local_degraded_root(self):
        # 状態リポジトリを持たないローカル縮退（次の同期で git init される）。
        root = self.tmp / "brand-new"
        cfg = self._build(root=str(root), state_repo=None)
        self.assertEqual(cfg.backlog.parent, root.resolve())


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

    def test_transaction_succeeds_while_local_state_is_unpushed(self):
        """ローカルが origin より先行していても CAS トランザクションは成立する。

        state_git_interval の push 間隔の間、ローカルには未 push の state sync コミットが
        あるのが**普通の運用状態**。以前はその間 `state_transaction` が全て失敗し
        （lease 更新・claim・自動割当が全滅）、lease が失効して計画役が PC 間を漂流→
        各 PC が勝手にバックログ分解を走らせる素地になっていた。トランザクションは
        remote HEAD を親に組み立てるのでローカルの未 push とは独立に成立し、ローカルへは
        決定的 3-way で合流する。"""
        first = self._cfg(node="pc-a")
        mkb(self.root, "T1")
        km.state_sync(first, force=True)   # T1 を push（リモート正本に載せる）
        # ローカルだけの未 push コミット（push 間隔の途中を模す）
        (self.root / "journal.md").write_text("local only\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "journal.md"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.email=a@t",
                        "-c", "user.name=a", "commit", "-qm", "local ahead"],
                       check=True, capture_output=True)
        self.assertTrue(km.renew_controller_lease(first))    # ahead でも lease は更新できる
        token = km.claim_distributed_task(first, "T1")
        self.assertTrue(token)                               # ahead でも claim できる
        claimed = km.load_tasks(first.backlog)[0]            # 結果はローカルにも合流している
        self.assertEqual((claimed.norm_status(), claimed.get("claim_owner")),
                         ("doing", "pc-a"))
        self.assertEqual(claimed.get("claim_token"), token)
        # ローカル先行分は失われない（作業ツリーにも履歴にも残る）
        self.assertEqual((self.root / "journal.md").read_text(encoding="utf-8"),
                         "local only\n")
        merged = subprocess.run(["git", "-C", str(self.root), "log", "--format=%s"],
                                capture_output=True, text=True).stdout
        self.assertIn("local ahead", merged)

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

    def test_settle_artifacts_land_in_one_commit(self):
        """W6: settle の archive・backlog 削除・納品書・journal は 1 コミットにまとまり、
        push が通って確定する（archive を導入するコミットが同時に backlog を消す）。"""
        cfg = self._cfg(node="pc-a", delivery_review=False, plan_review=False)
        mkb(self.root, "T1", verify="true")
        result = km.run_loop(cfg)
        self.assertEqual(result["counts"]["done"], 1)
        log = subprocess.run(["git", "-C", str(self.root), "log", "--name-status",
                              "--format=@@%H", "origin/main"],
                             capture_output=True, text=True, check=True).stdout
        commits = [c for c in log.split("@@") if c.strip()]
        with_archive = [c for c in commits if "archive/T1.md" in c]
        self.assertEqual(len(with_archive), 1, "archive/T1.md を導入するコミットは 1 つ")
        c = with_archive[0]
        self.assertIn("D\tbacklog/T1.md", c)      # backlog の削除が同じコミットに入る
        self.assertIn("DELIVERY.md", c)           # 納品書も同じコミットに入る

    def test_partial_settle_heals_forward_on_next_pass(self):
        """W6: settle 途中死（archive 書き込み後・backlog 削除前）は次パスの整合点が
        前へ倒して完成させる。専用リカバリ台帳は持たない。"""
        cfg = self._cfg()
        mkb(self.root, "T1", status="doing", verify="true")
        adir = cfg.archive_dir()
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "T1.md").write_text("## T1: T1\n- status: done\n", encoding="utf-8")
        (cfg.needs / "T1.md").write_text("x", encoding="utf-8")
        tasks = km.load_tasks(cfg.backlog)
        healed = km.heal_partial_settles(cfg, tasks)
        self.assertEqual(healed, ["T1"])
        self.assertFalse((cfg.backlog / "T1.md").exists())
        self.assertFalse((cfg.needs / "T1.md").exists())
        self.assertEqual(tasks, [])
        self.assertIn("T1", (self.root / "DELIVERY.md").read_text(encoding="utf-8"))

    def test_partial_settle_heal_spares_reused_ids(self):
        """id 再利用は倒さない: 別題の doing も、同題で積み直された ready も新しい仕事。"""
        cfg = self._cfg()
        mkb(self.root, "T1", title="新しい別の仕事", status="doing", verify="true")
        mkb(self.root, "T2", title="同じ題で積み直し", verify="true")   # intake の再投入＝ready
        adir = cfg.archive_dir()
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "T1.md").write_text("## T1: 昔の仕事\n- status: done\n", encoding="utf-8")
        (adir / "T2.md").write_text("## T2: 同じ題で積み直し\n- status: done\n", encoding="utf-8")
        tasks = km.load_tasks(cfg.backlog)
        self.assertEqual(km.heal_partial_settles(cfg, tasks), [])
        self.assertTrue((cfg.backlog / "T1.md").exists())
        self.assertTrue((cfg.backlog / "T2.md").exists())
        self.assertTrue((adir / "T1.md").exists())

    def test_unpushed_settle_commit_survives_and_pushes_on_next_pass(self):
        """W6: push 失敗で残るのは未 push のローカルコミット。リモート復帰後の次パスで
        そのまま押し出され、別 clone から見える（remote 正の再突合＝投影の再計算だけ）。"""
        cfg = self._cfg(node="pc-a", delivery_review=False, plan_review=False)
        # 単一ノード視点（ピア無し＝CAS 経路も claim fencing も通らない）で「push だけが失敗」を作る
        (self.root / "status" / "pc-peer.json").unlink()
        mkb(self.root, "T1", verify="true")
        good_url = str(self.remote)
        subprocess.run(["git", "-C", str(self.root), "remote", "set-url", "origin",
                        "file:///no-such-remote.git"], check=True)
        with mock.patch.object(km, "_STATE_PUSH_RETRIES", 1), \
             mock.patch.object(km, "backoff_sleep", lambda *_: None):
            result = km.run_loop(cfg)
        self.assertEqual(result["counts"]["done"], 1)     # push 失敗でもループは続行・done は確定待ち
        subprocess.run(["git", "-C", str(self.root), "remote", "set-url", "origin",
                        good_url], check=True)
        km._STATE_GITS.clear()
        km.state_sync(cfg, force=True)                    # 次パス相当の同期で押し出す
        got = self._other("after-heal")
        self.assertTrue((got / "archive" / "T1.md").exists())
        self.assertFalse((got / "backlog" / "T1.md").exists())

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

        S1 で状態のコミッタは DirectStateGit ただ 1 つになった（旧 `commit_state` による事前
        コミットは廃止）。`sync` 自身が「未コミット変更をコミット → 統合 → push」の順で回すので、
        呼び出し側の下ごしらえ無しにこの状況を抜けられることをここで固定する。"""
        cfg = self._cfg()

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

        km.state_sync(cfg, force=True)   # 事前コミット無しで通ること（書き手は 1 つ）

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

    def test_subdirectory_root_does_not_enable_direct_sync(self):
        """トップレベルでない root では direct 同期を使わない（S1）。

        旧 worktree 方式では root が <repo>-agent-state/.agent-project というサブディレクトリに
        なるため、`cfg.state_top` を見る特例で direct 同期を有効にしていた。S1 で状態ルートは
        常に状態専用リポジトリの clone（＝トップレベル）になったので特例を廃止し、判定は
        「root 自体がトップレベルか」の 1 点に戻した。サブディレクトリで有効にしてしまうと、
        無関係なリポジトリへ自動コミット・push することになる。"""
        cfg = self._cfg()
        sub = self.root / "nested" / ".agent-project"       # トップレベルではない root
        sub.mkdir(parents=True, exist_ok=True)
        cfg.backlog = sub / "backlog"
        cfg.backlog.mkdir(parents=True, exist_ok=True)
        self.assertFalse(km._git_toplevel(sub), "前提: サブディレクトリはトップレベルではない")
        self.assertFalse(km._direct_state_git_ok(cfg))
        km._STATE_GITS.clear()
        self.assertIsNone(km.state_git_for(cfg), "nested repo を作らずに同期を諦める")

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
        km.write_replan_request(cfg, "分解")                  # 分解は明示要求でしか走らない
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

    def test_state_repo_bootstraps_origin_when_missing(self):
        # ローカル縮退（root を git init した）構成で origin が無ければ、host.yaml の
        # projects[].state_repo（cfg.state_repo）を origin として設定する。clone 経由なら
        # origin は既にあるので、これが効くのは init 直後だけ。
        proot = Path(tempfile.mkdtemp(prefix="bootstrap-origin-")) / "proj"
        cfg = km.Config(backlog=proot / "backlog", policy=proot / "policy.md",
                        decisions=proot / "decisions", journal=proot / "journal.md",
                        needs=proot / "needs", workdir=self.tmp, bus=proot / "bus",
                        inbox=proot / "inbox",
                        planner="none", flow_planner="stub", executor="stub", dry_run=True,
                        state_repo=str(self.remote), state_git_interval=0.0)
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
