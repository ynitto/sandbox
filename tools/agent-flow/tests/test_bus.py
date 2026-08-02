"""agent-flow の単体テスト — bus（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from _shared import _git_config_get, _zero_loose_objects  # noqa: E402,F401 — `import *` は _ 始まりを持ってこない


class LocalWorktreeTests(unittest.TestCase):
    """手元に同じリポジトリのクローンがあれば、そこから worktree を切ること。

    目の前に同じリポジトリがあるのに、毎回ネットワーク越しに bare ミラーを取り直すのは無駄で、
    オフラインでは動かない。ローカルの作業ツリー・index には触らない（別 worktree なので）。"""

    def _repo(self):
        root = tempfile.mkdtemp(prefix="kf-local-")
        self.addCleanup(shutil.rmtree, root, True)
        origin = os.path.join(root, "origin.git")
        local = os.path.join(root, "local")
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        run = lambda *a, **k: subprocess.run(a, capture_output=True, env=env, **k)
        run("git", "init", "--bare", "-b", "main", origin)
        run("git", "clone", origin, local)
        run("git", "-C", local, "config", "user.email", "t@e.com")
        run("git", "-C", local, "config", "user.name", "t")
        pathlib.Path(local, "app.py").write_text("print(1)\n")
        run("git", "-C", local, "add", "-A")
        run("git", "-C", local, "commit", "-m", "init")
        run("git", "-C", local, "push", "-u", "origin", "main")
        return root, origin, local

    def test_worktree_is_cut_from_the_local_clone(self):
        root, origin, local = self._repo()
        dest = os.path.join(root, "ws")
        wt = kf.provision_from_local(local, origin, ["main"], dest)
        self.addCleanup(kf.cleanup_local_worktrees)
        self.assertTrue(wt, "ローカルから worktree を切れる")
        self.assertTrue(os.path.isfile(os.path.join(wt, "app.py")), "中身が入っている")

    def test_local_working_tree_and_index_are_untouched(self):
        root, origin, local = self._repo()
        # 人がローカルで作業中（未コミット変更 + staged）
        pathlib.Path(local, "wip.txt").write_text("編集中\n")
        subprocess.run(["git", "-C", local, "add", "wip.txt"], capture_output=True)
        before = subprocess.run(["git", "-C", local, "status", "--porcelain"],
                                capture_output=True, text=True).stdout

        wt = kf.provision_from_local(local, origin, ["main"], os.path.join(root, "ws"))
        self.addCleanup(kf.cleanup_local_worktrees)
        self.assertTrue(wt)

        after = subprocess.run(["git", "-C", local, "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        self.assertEqual(before, after, "人の作業ツリー・index を巻き込まない")
        self.assertEqual(pathlib.Path(local, "wip.txt").read_text(), "編集中\n")

    def test_different_repo_is_refused(self):
        # origin URL が違うクローンは使わない（取り違え防止）
        root, origin, local = self._repo()
        wt = kf.provision_from_local(local, "https://example.com/other/repo.git",
                                     ["main"], os.path.join(root, "ws"))
        self.assertIsNone(wt)

    def test_cleanup_removes_the_worktree_registration(self):
        root, origin, local = self._repo()
        kf.provision_from_local(local, origin, ["main"], os.path.join(root, "ws"))
        listed = subprocess.run(["git", "-C", local, "worktree", "list"],
                                capture_output=True, text=True).stdout
        self.assertIn("ws", listed)
        kf.cleanup_local_worktrees()
        listed = subprocess.run(["git", "-C", local, "worktree", "list"],
                                capture_output=True, text=True).stdout
        self.assertNotIn("/ws", listed, "登録を残さない")

    def test_provision_tree_prefers_local_over_network(self):
        # local が使えるなら、共有ミラー（ネットワーク）には触れない
        root, origin, local = self._repo()
        with mock.patch.object(kf, "provision_worktree",
                               side_effect=AssertionError("ネットワークを見に行った")):
            wt = kf.provision_tree(origin, ["main"], os.path.join(root, "ws"), local=local)
        self.addCleanup(kf.cleanup_local_worktrees)
        self.assertTrue(wt)


class ConfigStateGitSubdirTests(unittest.TestCase):
    """回帰: state_git_subdir が --config（agent-project が渡す flow_config）経由で反映されること。
    agent-project が --state-git-subdir を個別 CLI 注入していた頃は config 値が上書きされて効かない
    バグがあった。注入をやめた今、config の値が resolve_config → state_git_for まで通ることを固定する。"""

    def _write_cfg(self, obj):
        d = tempfile.mkdtemp(prefix="kf-cfgsub-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "agent-flow.json")   # JSON でも YAML と同一キー・同一 resolve
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return d, p

    def _resolve(self, argv):
        args = kf.build_parser().parse_args(argv)
        kf.resolve_config(args)
        return args

    def test_subdir_comes_from_config_when_cli_absent(self):
        # agent-project が渡すのと同じ argv（--config あり・--state-git-subdir 無し）で config 値が効く
        d, cfg = self._write_cfg({"state_git_subdir": "custom-flow-ns"})
        args = self._resolve(["--bus", os.path.join(d, "bus"),
                              "--state-git", "git@x:team/s.git", "--state-git-branch", "main",
                              "--state-git-interval", "300", "--config", cfg,
                              "participate", "--executor", "stub"])
        self.assertEqual(args.state_git_subdir, "custom-flow-ns")
        sg = kf.state_git_for(args)                      # 実際に使われる StateGit が同じ subdir を持つ
        self.assertIsNotNone(sg)
        self.assertEqual(sg.subdir, "custom-flow-ns")

    def test_default_subdir_when_unset(self):
        # config にも CLI にも無ければ既定 "agent-flow"（agent-project の FLOW_STATE_SUBDIR と一致）
        d, cfg = self._write_cfg({})
        args = self._resolve(["--bus", os.path.join(d, "bus"),
                              "--state-git", "git@x:team/s.git", "--config", cfg,
                              "participate", "--executor", "stub"])
        self.assertEqual(args.state_git_subdir, "agent-flow")

    def test_cli_still_overrides_config(self):
        # 明示 CLI 指定は従来どおり config より優先（CLI > config > 既定）
        d, cfg = self._write_cfg({"state_git_subdir": "from-config"})
        args = self._resolve(["--bus", os.path.join(d, "bus"),
                              "--state-git", "git@x:team/s.git",
                              "--state-git-subdir", "from-cli", "--config", cfg,
                              "participate", "--executor", "stub"])
        self.assertEqual(args.state_git_subdir, "from-cli")


class GcOrphanInboxTests(unittest.TestCase):
    """gc は run を伴わない孤児 inbox 要求も掃除する（残すと daemon が「新規要求」と誤認して
    不要な run を再起動してしまうため）。フレッシュな未受理要求・claim 中の要求は保護する。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-gc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "_")

    def _args(self, **kw):
        base = dict(bus=self.tmp, git=None, run_id=None, git_branch="main", git_subdir="",
                    keep=0, older_than=7.0, status=None, dry_run=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _old_inbox(self, req_id, days=30):
        self.bus.submit_request(req_id, f"req {req_id}", "submitter")
        p = os.path.join(self.bus.inbox_dir, f"{req_id}.json")
        rec = kf.read_json(p)
        rec["submitted_at"] = "2020-01-01T00:00:00Z"   # 十分古い
        kf.write_json_atomic(p, rec)

    def _run(self, run_id, status="done"):
        b = kf.Bus(self.tmp, run_id)
        b.ensure_run(f"run {run_id}")
        m = kf.read_json(b.meta_path)
        m["status"] = status
        m["created_at"] = "2020-01-01T00:00:00Z"
        kf.write_json_atomic(b.meta_path, m)

    def _gc(self, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kf.cmd_gc(self._args(**kw))
        return buf.getvalue()

    def test_reaps_old_unclaimed_orphan_inbox(self):
        # run を伴わず十分古く claim もされていない孤児 inbox は掃除される
        self._old_inbox("orphanOld")
        out = self._gc()
        self.assertIn("孤児 inbox 掃除: orphanOld", out)
        self.assertNotIn("orphanOld", self.bus.list_inbox())

    def test_protects_fresh_pending_request(self):
        # フレッシュな未受理要求（run がまだ無いだけ）は保護する＝正規の受理待ちを消さない
        self.bus.submit_request("fresh", "just submitted", "submitter")   # submitted_at = 今
        self._gc()
        self.assertIn("fresh", self.bus.list_inbox())

    def test_protects_actively_claimed_orphan(self):
        # lease 内で担当 daemon が claim 中の要求は、run 生成前でも触らない
        self._old_inbox("claimedOld")
        self.bus.claim_request("claimedOld", "daemonA", 3600)
        self._gc()
        self.assertIn("claimedOld", self.bus.list_inbox())

    def test_run_backed_inbox_removed_with_run(self):
        # run を持つ inbox は従来どおり run ごと掃除される（孤児掃除の対象外だが結果的に消える）
        self._old_inbox("reqDone")
        self._run("reqDone", "done")
        out = self._gc()
        self.assertIn("削除: reqDone", out)
        self.assertNotIn("reqDone", self.bus.list_inbox())
        self.assertFalse(self.bus.run_exists("reqDone"))

    def test_status_filter_does_not_reap_orphans(self):
        # --status は「run の status で絞る」意図なので、status を持たない孤児 inbox は触らない
        self._old_inbox("orphanOld")
        out = self._gc(status="done")
        self.assertNotIn("孤児 inbox", out)
        self.assertIn("orphanOld", self.bus.list_inbox())

    def test_dry_run_previews_without_deleting(self):
        # dry-run は掃除対象を表示するだけで実際には消さない
        self._old_inbox("orphanOld")
        out = self._gc(dry_run=True)
        self.assertIn("[dry-run] 孤児 inbox 掃除: orphanOld", out)
        self.assertIn("orphanOld", self.bus.list_inbox())   # 消えていない


class GitDistributedTests(unittest.TestCase):
    """複数 PC 分散の模擬: ローカルのベアリポジトリを共有バスにし、ノードごとに
    独立クローン（= 別 PC 相当）から push/pull させて検証する。git 必須。"""

    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git が無い環境ではスキップ")
        self.root = tempfile.mkdtemp(prefix="kf-git-")
        self.bare = os.path.join(self.root, "bus.git")
        r = subprocess.run(["git", "init", "--bare", "-b", "main", self.bare],
                           capture_output=True, text=True)
        if r.returncode != 0:  # 古い git 向けフォールバック
            subprocess.run(["git", "init", "--bare", self.bare], check=True,
                           capture_output=True)
        self.clones = os.path.join(self.root, "clones")

    def _final_from_bare(self):
        tmp = tempfile.mkdtemp(prefix="kf-read-")
        subprocess.run(["git", "clone", "-q", self.bare, tmp], check=True,
                       capture_output=True)
        runs = os.path.join(tmp, "runs")
        rid = sorted(os.listdir(runs))[0]
        return kf.read_json(os.path.join(runs, rid, "final.json"))

    def test_claim_across_separate_clones_single_winner(self):
        # 別クローン（別 PC 相当）から同じタスクを claim → 勝者は 1 人
        a = kf.GitBus(os.path.join(self.clones, "A"), "run1", remote=self.bare, branch="main")
        b = kf.GitBus(os.path.join(self.clones, "B"), "run1", remote=self.bare, branch="main")
        won_a = a.try_claim("t1", "nodeA", 60)
        won_b = b.try_claim("t1", "nodeB", 60)
        self.assertTrue(won_a)
        self.assertFalse(won_b)
        # 先着の claim が両クローンから見て勝者
        b.sync_pull()
        self.assertEqual(b._winner("t1"), "nodeA")
        # 敗者の claim ファイルは消えている（残すと勝者 release 後に zombie claimed になる）
        a.sync_pull()
        loser_claim = os.path.join(a.claims_dir, "t1", "nodeB.json")
        self.assertFalse(os.path.exists(loser_claim),
                         "敗者が自分の claim を残してはいけない")

    def test_loser_claim_withdrawn_prevents_zombie_after_park(self):
        # 敗者が claim を残すと、勝者の park/release 後に敗者の lease が _winner になり
        # 誰も動かない claimed になる。withdraw でそれを防ぐ。
        a = kf.GitBus(os.path.join(self.clones, "ZA"), "run1", remote=self.bare, branch="main")
        b = kf.GitBus(os.path.join(self.clones, "ZB"), "run1", remote=self.bare, branch="main")
        self.assertTrue(a.try_claim("t1", "nodeA", 60))
        self.assertFalse(b.try_claim("t1", "nodeB", 60))
        a.release_claim("t1", "nodeA")          # park 相当：勝者が slot を解放
        a.sync_push("release")
        b.sync_pull()
        self.assertIsNone(b._winner("t1"))      # 敗者の残骸 claim で zombie にならない

    def test_request_claim_elects_single_daemon(self):
        # 別クローンの 2 デーモンが同じ要求を claim → orchestrate 担当は 1 台
        a = kf.GitBus(os.path.join(self.clones, "dA"), "_", remote=self.bare, branch="main")
        b = kf.GitBus(os.path.join(self.clones, "dB"), "_", remote=self.bare, branch="main")
        a.submit_request("req1", "do it", "submitter")
        a.sync_push("submit req1")
        b.sync_pull()
        ca = a.claim_request("req1", "daemonA", 60)
        cb = b.claim_request("req1", "daemonB", 60)
        self.assertTrue(ca)
        self.assertFalse(cb)

    def test_run_over_git_bus_completes(self):
        # orchestrator + worker が各自の独立クローンから git バスへ push/pull して完走
        cmd = [sys.executable, str(SCRIPT), "--bus", self.clones, "--git", self.bare,
               "run", "x; y; z", "--planner", "stub", "--executor", "stub",
               "--workers", "3", "--poll", "0.2"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stderr[-1000:])
        final = self._final_from_bare()
        self.assertIsNotNone(final)
        res = final["results"]
        self.assertGreaterEqual(len(res), 4)  # fan-out 並列 + synth
        for nid, r in res.items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")
            self.assertTrue(r["who"])  # 誰か（どこかのクローン）が実行した

    def test_sparse_checkout_limits_worktree(self):
        # 既存リポジトリにバスを間借り（--git-subdir）し、sparse で他を展開しない
        seed = tempfile.mkdtemp(prefix="kf-seed-")
        subprocess.run(["git", "clone", "-q", self.bare, seed], check=True, capture_output=True)
        os.makedirs(os.path.join(seed, "unrelated"))
        with open(os.path.join(seed, "unrelated", "x.txt"), "w") as f:
            f.write("hi")
        for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed"],
                  ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", seed] + c, check=True, capture_output=True)
        clone = os.path.join(self.clones, "sub")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main", subdir="flow")
        entries = set(os.listdir(clone))
        self.assertNotIn("unrelated", entries)  # sparse で無関係ディレクトリは未展開
        self.assertIn(".git", entries)

    def test_cleanup_clone_removes_worktree(self):
        # 作業後にクローン（.git を含む作業ツリー）を丸ごと削除できる。
        clone = os.path.join(self.clones, "to-remove")
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
        bus.cleanup_clone()
        self.assertFalse(os.path.exists(clone))  # クローンごと消える
        bus.cleanup_clone()  # 既に無くても安全（冪等）

    def test_clone_retries_on_transient_network_failure(self):
        # 一過性のネットワーク障害でも、起動時クローンはリトライして成功する（即死しない）。
        clone = os.path.join(self.clones, "flaky")
        real_run = subprocess.run
        calls = {"n": 0}

        def flaky_run(cmd, *a, **kw):
            # 1 回目の試行（filtered + fallback の 2 呼び出し）だけネットワーク障害を模して失敗させ、
            # 2 回目の試行で成功させる（_clone_once は 1 試行で git clone を最大 2 回呼ぶ）。
            if isinstance(cmd, list) and cmd[:2] == ["git", "clone"]:
                calls["n"] += 1
                if calls["n"] <= 2:
                    return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access (timeout)")
            return real_run(cmd, *a, **kw)

        slept = []
        # 差し替えるのは **リトライのバックオフ seam** であって素の time.sleep ではない。
        # time は stdlib の共有モジュールなので、差し替えると CPython の subprocess 内部
        # （プロセス終了を 0.001s 倍々でポーリングする）にも効き、CPU 高負荷で git clone が
        # 長引くとその sleep が記録へ混入してこの検証が壊れる（高負荷時だけ落ちていた）。
        with mock.patch.object(kf.subprocess, "run", side_effect=flaky_run), \
             mock.patch.object(kf._transport, "backoff_sleep", side_effect=lambda s: slept.append(s)):
            kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))  # 最終的にクローン成功
        self.assertGreaterEqual(calls["n"], 3)                       # 1 試行目が失敗 → 2 試行目で成功
        self.assertEqual(slept, [1])                                 # 試行の合間に 1 回バックオフ（2^0）

    def test_clone_gives_up_after_retries(self):
        # ずっと失敗するなら CLONE_RETRIES 回試して諦め、明示的な RuntimeError を出す。
        clone = os.path.join(self.clones, "dead")
        real_run = subprocess.run

        def always_fail(cmd, *a, **kw):
            if isinstance(cmd, list) and cmd[:2] == ["git", "clone"]:
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access")
            return real_run(cmd, *a, **kw)

        with mock.patch.object(kf.subprocess, "run", side_effect=always_fail), \
             mock.patch.object(kf._transport, "backoff_sleep", side_effect=lambda s: None):
            with self.assertRaises(RuntimeError) as ctx:
                kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertIn(f"{kf.CLONE_RETRIES} 回失敗", str(ctx.exception))

    def test_clone_inside_parent_repo_does_not_touch_parent(self):
        # クローン先が親リポジトリの作業ツリー配下にあっても、sparse-checkout が親へ波及しない。
        # 親リポジトリを用意し、その配下にバス用クローンを作る。
        parent = tempfile.mkdtemp(prefix="kf-parent-")
        subprocess.run(["git", "-C", parent, "init", "-q"], check=True, capture_output=True)
        for name in ("keepA", "keepB"):
            with open(os.path.join(parent, name), "w") as f:
                f.write("x")
        for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "p"]):
            subprocess.run(["git", "-C", parent] + c, check=True, capture_output=True)
        clone = os.path.join(parent, "bus", "node")     # 親リポジトリの作業ツリー配下
        kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        # クローンは自分自身の .git を持ち、親の作業ツリーは無傷（sparse で隠されない）
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
        self.assertTrue(os.path.exists(os.path.join(parent, "keepA")))
        self.assertTrue(os.path.exists(os.path.join(parent, "keepB")))
        # 親リポジトリに sparse-checkout が設定されていない（cone 化していない）
        cfg = subprocess.run(["git", "-C", parent, "config", "--get", "core.sparseCheckout"],
                             capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(cfg, "true")

    def test_reuse_full_checkout_of_same_remote_is_refused(self):
        # 同一 remote の既存フルチェックアウト（ユーザーの作業リポジトリ等）を --bus に指定しても、
        # sparse-checkout で subdir 以外の追跡ファイルを隠さず、上書きせず中断する。
        seed = tempfile.mkdtemp(prefix="kf-seed-")
        subprocess.run(["git", "clone", "-q", self.bare, seed], check=True, capture_output=True)
        for d in ("flow", "src", "docs"):
            os.makedirs(os.path.join(seed, d))
            with open(os.path.join(seed, d, "f.txt"), "w") as f:
                f.write("x")
        for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "s"],
                  ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", seed] + c, check=True, capture_output=True)
        userwork = tempfile.mkdtemp(prefix="kf-userwork-")
        subprocess.run(["git", "clone", "-q", self.bare, userwork], check=True, capture_output=True)
        before = set(os.listdir(userwork))
        self.assertEqual(before, {".git", "flow", "src", "docs"})
        with self.assertRaises(RuntimeError):
            kf.GitBus(userwork, "run1", remote=self.bare, branch="main", subdir="flow")
        # 追跡ファイルは隠されず、作業ツリーは無傷
        self.assertEqual(set(os.listdir(userwork)), before)
        sparse = subprocess.run(["git", "-C", userwork, "config", "--get", "core.sparseCheckout"],
                                capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(sparse.lower(), "true")

    def test_managed_bus_clone_is_reused(self):
        # 自前で作ったバスクローン（目印つき）は二度目以降そのまま再利用される（中断しない）。
        clone = os.path.join(self.clones, "managed")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main", subdir="flow")
        marker = subprocess.run(["git", "-C", clone, "config", "--get", "agent-flow.busclone"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(marker, "1")
        kf.GitBus(clone, "run2", remote=self.bare, branch="main", subdir="flow")  # 再利用で例外なし

    def test_stale_index_lock_recovered_on_reuse(self):
        # SIGKILL/電源断が残した古い index.lock は、クローン再利用時に残骸として除去され、
        # 以後の add/commit/push（run 作成の sync_push 相当）が失敗し続けない。
        clone = os.path.join(self.clones, "stale-lock")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        lock = os.path.join(clone, ".git", "index.lock")
        open(lock, "w").close()
        old = time.time() - 3600
        os.utime(lock, (old, old))
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")   # 例外なく再利用
        self.assertFalse(os.path.exists(lock))                            # 残骸は除去済み
        bus.submit_request("req1", "do it", "submitter")
        bus.sync_push("submit req1")                                      # add/commit/push が通る
        self.assertEqual(bus._git(["status", "--porcelain"]).stdout.strip(), "")

    def test_corrupt_index_clone_is_rebuilt(self):
        # ロック除去でも回復できないほど壊れたクローン（index 破損等）は作り直して
        # 自己回復する（バスの真実はリモートにあるため使い捨てで安全）。
        clone = os.path.join(self.clones, "corrupt-index")
        first = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        first.submit_request("req0", "seed", "submitter")
        first.sync_push("seed main")                                      # main を実体化させる
        with open(os.path.join(clone, ".git", "index"), "wb") as f:
            f.write(b"broken")                                            # index を破壊
        with mock.patch.object(kf._transport, "backoff_sleep", lambda s: None):         # 競合待ちを高速化
            bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        marker = subprocess.run(["git", "-C", clone, "config", "--get", "agent-flow.busclone"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(marker, "1")                                     # 管理クローンとして再生
        bus.submit_request("req1", "do it", "submitter")
        bus.sync_push("submit req1")                                      # 作り直し後は普通に使える

    def test_lock_going_stale_during_retry_is_removed(self):
        # 実行中に遭遇したロックも、リトライ中に残骸（十分古い）と判明すれば除去して成功する
        # （新しいうちは消さない＝稼働中の他 git を壊さない）。
        clone = os.path.join(self.clones, "aging-lock")
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        lock = os.path.join(clone, ".git", "index.lock")
        open(lock, "w").close()                                           # mtime = 今 → まだ消さない

        def age_lock(_):                                                  # 時間経過の代わりに mtime を過去へ
            old = time.time() - kf.GIT_LOCK_STALE_SEC - 1
            os.utime(lock, (old, old))

        with open(os.path.join(clone, "poke.txt"), "w") as f:
            f.write("x")
        with mock.patch.object(kf._transport, "backoff_sleep", side_effect=age_lock):
            p = bus._git(["add", "-A"])
        self.assertEqual(p.returncode, 0)
        self.assertFalse(os.path.exists(lock))                            # 残骸化した時点で除去された

    def test_interrupted_rebase_recovered_on_reuse(self):
        # 中断された pull --rebase の残骸（rebase-merge/）があると以後の pull が失敗し続けるため、
        # クローン再利用時に破棄して回復する。
        clone = os.path.join(self.clones, "half-rebase")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        os.makedirs(os.path.join(clone, ".git", "rebase-merge"))
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertFalse(os.path.isdir(os.path.join(clone, ".git", "rebase-merge")))
        bus.sync_pull()                                                   # pull --rebase が通る

    def test_git_retries_while_live_lock_is_held(self):
        # 稼働中の他 git が保持する新しいロックは消さず、短いバックオフで解放を待って成功する。
        clone = os.path.join(self.clones, "live-lock")
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        lock = os.path.join(clone, ".git", "index.lock")
        open(lock, "w").close()
        released = []

        def release(_):
            if os.path.exists(lock):                                      # 相手の git が終わった想定
                os.remove(lock)
                released.append(True)

        with open(os.path.join(clone, "poke.txt"), "w") as f:
            f.write("x")
        with mock.patch.object(kf._transport, "backoff_sleep", side_effect=release):
            p = bus._git(["add", "-A"])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(released, [True])                                # 1 回待って解放を拾った

    def test_clone_into_foreign_nonempty_dir_is_refused(self):
        # 別リポジトリの非空ディレクトリを誤ってバスのクローン先に指定したら、上書きせず中断する。
        foreign = tempfile.mkdtemp(prefix="kf-foreign-")
        subprocess.run(["git", "-C", foreign, "init", "-q"], check=True, capture_output=True)
        with open(os.path.join(foreign, "important.txt"), "w") as f:
            f.write("do not touch")
        with self.assertRaises(RuntimeError):
            kf.GitBus(foreign, "run1", remote=self.bare, branch="main")
        # 既存ファイルは無傷
        self.assertTrue(os.path.exists(os.path.join(foreign, "important.txt")))

    def test_durable_write_config_on_clone_and_local_remote(self):
        # 電源断でのサイズ 0 オブジェクトを予防する durable-write 設定（core.fsync/fsyncMethod）を
        # 管理クローンと、ローカルパスの共有リポジトリ本体（受信側 receive-pack）の両方に効かせる。
        clone = os.path.join(self.clones, "durable")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertEqual(_git_config_get(clone, "core.fsync"), "all")
        self.assertEqual(_git_config_get(clone, "core.fsyncMethod"), "batch")
        self.assertEqual(_git_config_get(self.bare, "core.fsync"), "all")
        self.assertEqual(_git_config_get(self.bare, "core.fsyncMethod"), "batch")

    def test_empty_objects_clone_is_rebuilt_on_reuse(self):
        # 電源断でオブジェクトが空になった再利用クローンは、fsck 健全性プローブで検知して捨て、
        # リモート（真実）から作り直す（ロック除去・rebase 中断とは別経路の自己回復）。
        clone = os.path.join(self.clones, "empty-obj")
        first = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        first.submit_request("req0", "seed", "submitter")
        first.sync_push("seed main")                                      # main を実体化
        self.assertGreater(_zero_loose_objects(clone), 0)                 # loose object を空に
        self.assertFalse(first._probe_integrity())                       # 破損を検知できる
        with mock.patch.object(kf._transport, "backoff_sleep", lambda s: None):
            bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")   # 再利用 → 作り直し
        self.assertTrue(bus._probe_integrity())                          # 健全なクローンへ再生
        self.assertEqual(_git_config_get(clone, "agent-flow.busclone"), "1")
        bus.submit_request("req1", "do it", "submitter")
        bus.sync_push("submit req1")                                     # 作り直し後は普通に使える

    def test_sync_push_self_heals_on_object_corruption(self):
        # push 実行中にローカルオブジェクト破損が露見しても、恒久 push 失敗に陥らず作り直して回復する。
        # （作り直しでクローンごと捨てるため in-flight の未 push 書き込みは失われ得るが、それは孤児
        #  reclaim による再実行で回収される設計。ここでは「詰まらず、以後正常に使える」ことを確かめる。）
        clone = os.path.join(self.clones, "push-heal")
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        bus.submit_request("req0", "seed", "submitter")
        bus.sync_push("seed")
        _zero_loose_objects(clone)                                        # 電源断でのサイズ 0 を模擬
        bus.submit_request("req1", "after crash", "submitter")           # 破損クローンへ書き込み
        with mock.patch.object(kf._transport, "backoff_sleep", lambda s: None):
            bus.sync_push("after crash")                                 # 破損を検知 → 作り直し（例外なし）
        self.assertTrue(bus._probe_integrity())                          # クローンは健全化
        # 健全化後の新規投入はリモートへ確実に届き、seed も無傷（共有リポジトリ側から確認）
        bus.submit_request("req2", "recovered", "submitter")
        bus.sync_push("recovered")
        check = tempfile.mkdtemp(prefix="kf-check-")
        subprocess.run(["git", "clone", "-q", self.bare, check], check=True, capture_output=True)
        self.assertTrue(os.path.exists(os.path.join(check, "inbox", "req2.json")))
        self.assertTrue(os.path.exists(os.path.join(check, "inbox", "req0.json")))

    def test_corrupt_remote_gives_clear_diagnostic_not_reclone_loop(self):
        # 共有リポジトリ本体（リモート）が壊れていると clone 自体が失敗する。作り直しでは直らないので、
        # 「リモート破損」を明示した RuntimeError で中断し、無限の再クローンループに陥らない。
        clone = os.path.join(self.clones, "corrupt-remote")
        corrupt = subprocess.CompletedProcess(
            ["git", "clone"], 128, "",
            "error: object file .git/objects/ab/cd is empty\nfatal: loose object abcd is corrupt")
        with mock.patch.object(kf.GitBus, "_clone_with_retry", return_value=corrupt):
            with self.assertRaises(RuntimeError) as ctx:
                kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertIn("共有リポジトリ", str(ctx.exception))
        self.assertIn("破損", str(ctx.exception))

    def test_is_corrupt_error_classifies_power_loss_signatures(self):
        # 電源断由来の破損メッセージは破損と判定し、一過性のネットワーク/権限エラーは判定しない。
        def proc(err):
            return subprocess.CompletedProcess(["git"], 128, "", err)
        for err in ("error: object file .git/objects/ab/cd is empty",
                    "fatal: loose object abcd is corrupt",
                    "error: sha1 mismatch abcd",
                    "fatal: bad object HEAD"):
            self.assertTrue(kf.GitBus._is_corrupt_error(proc(err)), err)
        for err in ("fatal: unable to access 'https://x/': timeout",
                    "fatal: Authentication failed",
                    "error: failed to push some refs"):
            self.assertFalse(kf.GitBus._is_corrupt_error(proc(err)), err)

    def test_make_bus_cleanup_removes_active_clones(self):
        # make_bus で作ったクローンは cleanup_active_clones でまとめて削除される。
        kf._active_clones.clear()
        self.addCleanup(kf._active_clones.clear)
        args = mock.Mock(bus=self.clones, run_id="run1", git=self.bare,
                         git_branch="main", git_subdir="")
        bus = kf.make_bus(args, "node-x")
        self.assertIn(bus, kf._active_clones)
        self.assertTrue(os.path.isdir(bus.workdir))
        kf.cleanup_active_clones()
        self.assertFalse(os.path.exists(bus.workdir))
        self.assertEqual(kf._active_clones, [])


class EnsureBusRootTests(unittest.TestCase):
    """起動初回のバスフォルダ作成（git バスでは .gitkeep も置く）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-ensurebus-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_creates_local_bus_root_without_gitkeep(self):
        # ローカルバスは初回に作成され、.gitkeep は置かない（runs/ 等で埋まるため）。
        bus = os.path.join(self.tmp, "newbus")
        args = mock.Mock(bus=bus, git=None)
        kf.ensure_bus_root(args)
        self.assertTrue(os.path.isdir(bus))
        self.assertFalse(os.path.exists(os.path.join(bus, ".gitkeep")))

    def test_creates_git_bus_root_with_gitkeep(self):
        # git バスはクローンが作業後に消えて空になるため、.gitkeep で空フォルダを残す。
        bus = os.path.join(self.tmp, "gitbus")
        args = mock.Mock(bus=bus, git="/some/remote")
        kf.ensure_bus_root(args)
        self.assertTrue(os.path.isdir(bus))
        self.assertTrue(os.path.isfile(os.path.join(bus, ".gitkeep")))

    def test_idempotent_and_preserves_existing(self):
        # 既存フォルダ/.gitkeep は壊さず冪等（中身を上書きしない）。
        bus = os.path.join(self.tmp, "exists")
        os.makedirs(bus)
        keep = os.path.join(bus, ".gitkeep")
        with open(keep, "w") as f:
            f.write("keep-me")
        args = mock.Mock(bus=bus, git="/some/remote")
        kf.ensure_bus_root(args)
        with open(keep) as f:
            self.assertEqual(f.read(), "keep-me")


class CleanupTests(unittest.TestCase):
    """一時ファイルの自動クリーンアップ（A: ロック / B: 中間 .tmp / C: 孤立クローン）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-cleanup-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _old(self, path, age_sec):
        t = time.time() - age_sec
        os.utime(path, (t, t))

    def test_sweep_tmp_dead_pid(self):
        # 死んだ pid の <path>.tmp.<pid> は消す。生存 pid の新しいものは残す。
        d = os.path.join(self.tmp, "runs", "r1")
        os.makedirs(d)
        dead = os.path.join(d, "meta.json.tmp.999999")
        dead_unique = os.path.join(d, "meta.json.tmp.999999.123456789")
        alive = os.path.join(d, "meta.json.tmp.%d" % os.getpid())
        alive_unique = os.path.join(d, "meta.json.tmp.%d.123456789" % os.getpid())
        normal = os.path.join(d, "meta.json")
        for p in (dead, dead_unique, alive, alive_unique, normal):
            with open(p, "w") as f:
                f.write("{}")
        removed = kf.sweep_tmp_files(self.tmp, min_age_sec=300.0)
        self.assertEqual(removed, 2)
        self.assertFalse(os.path.exists(dead))
        self.assertFalse(os.path.exists(dead_unique))
        self.assertTrue(os.path.exists(alive))   # 生存 pid かつ新しい → 残す
        self.assertTrue(os.path.exists(alive_unique))
        self.assertTrue(os.path.exists(normal))  # 中間でない通常ファイルは対象外

    def test_sweep_tmp_old_alive_pid(self):
        # 生存 pid でも min_age を超えて古ければクラッシュ残骸とみなし消す。
        d = os.path.join(self.tmp, "runs")
        os.makedirs(d)
        old = os.path.join(d, "graph.json.tmp.%d" % os.getpid())
        with open(old, "w") as f:
            f.write("{}")
        self._old(old, 600)
        self.assertEqual(kf.sweep_tmp_files(self.tmp, min_age_sec=300.0), 1)
        self.assertFalse(os.path.exists(old))

    def test_sweep_tmp_skips_git_internals(self):
        # .git 配下は走査しない（git の内部一時ファイルに触れない）。
        g = os.path.join(self.tmp, ".git", "objects")
        os.makedirs(g)
        inside = os.path.join(g, "x.tmp.999999")
        with open(inside, "w") as f:
            f.write("x")
        self.assertEqual(kf.sweep_tmp_files(self.tmp), 0)
        self.assertTrue(os.path.exists(inside))

    def test_sweep_lock_unused_old(self):
        # 古くて誰も保持していないロックは消す。新しいロックは残す。
        d = kf._locks_root()
        os.makedirs(d, exist_ok=True)
        old = os.path.join(d, "kf-test-old.lock")
        fresh = os.path.join(d, "kf-test-fresh.lock")
        for p in (old, fresh):
            with open(p, "w") as f:
                f.write("")
        self.addCleanup(lambda: [os.path.exists(p) and os.remove(p) for p in (old, fresh)])
        self._old(old, 7200)
        removed = kf.sweep_lock_files(min_age_sec=3600.0)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))  # 新しい → 残す
        self.assertGreaterEqual(removed, 1)

    @unittest.skipIf(kf.fcntl is None, "flock 非対応環境")
    def test_sweep_lock_held_is_kept(self):
        # 保持中（flock 中）の古いロックは消さない。
        d = kf._locks_root()
        os.makedirs(d, exist_ok=True)
        held = os.path.join(d, "kf-test-held.lock")
        f = open(held, "w")
        self.addCleanup(lambda: (f.close(), os.path.exists(held) and os.remove(held)))
        kf.fcntl.flock(f, kf.fcntl.LOCK_EX)
        self._old(held, 7200)
        kf.sweep_lock_files(min_age_sec=3600.0)
        self.assertTrue(os.path.exists(held))  # 保持中 → 残す

    def test_sweep_clone_dirs(self):
        # .git を持つ古い孤立クローンは消す。新しいクローンと keep 対象・非クローンは残す。
        parent = self.tmp
        for name in ("orchestrator-r1", "daemon-self", "worker-w1"):
            os.makedirs(os.path.join(parent, name, ".git"))
        plain = os.path.join(parent, "runs")  # .git を持たない → 触らない
        os.makedirs(plain)
        self._old(os.path.join(parent, "orchestrator-r1"), 100000)
        self._old(os.path.join(parent, "orchestrator-r1", ".git"), 100000)
        removed = kf.sweep_clone_dirs(parent, keep_basename="daemon-self",
                                      min_age_sec=3600.0)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(os.path.join(parent, "orchestrator-r1")))
        self.assertTrue(os.path.exists(os.path.join(parent, "daemon-self")))  # keep
        self.assertTrue(os.path.exists(os.path.join(parent, "worker-w1")))    # 新しい
        self.assertTrue(os.path.exists(plain))  # 非クローン

    def test_run_cleanup_local_skips_clones(self):
        # ローカルバス（--git なし）では孤立クローン掃除は走らない（クローンが無い）。
        args = mock.Mock(bus=self.tmp, lease=1800.0, git=None, cleanup_age=24.0)
        bus = kf.Bus(self.tmp, "_")
        res = kf.run_cleanup(args, bus)
        self.assertEqual(res["clones"], 0)
        self.assertIn("locks", res)
        self.assertIn("tmp", res)


class CleanupCommandTests(unittest.TestCase):
    """`agent-flow cleanup`（実装計画 W1-11 残・設計 §4.2 node 層 gc tick の実体）:
    run_cleanup を daemon 常駐なしで単発起動できる入口。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-cleanup-cmd-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _args(self, **kw):
        base = dict(bus=self.tmp, lease=1800.0, git=None, run_id=None, cleanup_age=24.0,
                   json=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_cmd_cleanup_prints_human_summary(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kf.cmd_cleanup(self._args())
        self.assertEqual(rc, 0)
        self.assertIn("cleanup: locks=", buf.getvalue())

    def test_cmd_cleanup_json_output_is_parseable(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kf.cmd_cleanup(self._args(json=True))
        result = json.loads(buf.getvalue())
        self.assertIn("locks", result)
        self.assertIn("tmp", result)
        self.assertIn("clones", result)

    def test_cleanup_subcommand_registered_in_parser(self):
        args = kf.build_parser().parse_args(["--bus", self.tmp, "cleanup", "--json"])
        self.assertIs(args.func, kf.cmd_cleanup)
        self.assertTrue(args.json)


class ArtifactProtocolTests(unittest.TestCase):
    """中間成果物のファイル参照プロトコル（依存タスクの成果物を決定的パスで受け渡す）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-art-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")

    def test_node_artifact_dir_is_deterministic(self):
        # 同じ node-id なら別 Bus ビューでも同じ run 相対パスを指す（後続が発見できる）
        d1 = self.bus.node_artifact_dir("t1")
        d2 = kf.Bus(self.tmp, "run1").node_artifact_dir("t1")
        self.assertEqual(d1, d2)
        self.assertEqual(os.path.relpath(d1, self.bus.run_dir),
                         os.path.join("artifacts", "t1"))

    def test_ensure_and_list_artifacts(self):
        d = self.bus.ensure_artifact_dir("t1")
        self.assertTrue(os.path.isdir(d))
        with open(os.path.join(d, "out.bin"), "w") as f:
            f.write("payload")
        self.assertEqual(self.bus.list_artifacts("t1"), [os.path.join(d, "out.bin")])
        self.assertEqual(self.bus.list_artifacts("missing"), [])  # 無ければ空

    def test_write_result_records_artifacts(self):
        self.bus.write_result("t1", "w", "done", "out", artifacts=["artifacts/t1/out.bin"])
        self.assertEqual(self.bus.read_result("t1")["artifacts"], ["artifacts/t1/out.bin"])
        # artifacts 無しなら後方互換でキーを足さない
        self.bus.write_result("t2", "w", "done", "out")
        self.assertNotIn("artifacts", self.bus.read_result("t2"))

    def test_artifact_instruction_lists_self_and_deps(self):
        dep_dir = self.bus.ensure_artifact_dir("dep1")
        with open(os.path.join(dep_dir, "data.json"), "w") as f:
            f.write("{}")
        self_dir = self.bus.node_artifact_dir("t2")
        note = kf.artifact_instruction(self_dir, {"dep1": dep_dir})
        self.assertIn(self_dir, note)            # 出力先を案内
        self.assertIn(dep_dir, note)             # 依存の成果物パスを案内
        self.assertIn("data.json", note)         # 依存ディレクトリ内のファイル名も
        # 依存ディレクトリが空（成果物なし）なら依存欄は出さない
        empty = kf.artifact_instruction(self_dir, {"dep1": self.bus.node_artifact_dir("nope")})
        self.assertNotIn("依存タスクの成果物", empty)

    def test_artifact_instruction_empty_when_nothing(self):
        self.assertEqual(kf.artifact_instruction(None, None), "")

    def test_execute_agent_prompt_references_dep_artifacts_by_path(self):
        # execute_agent は依存成果物の中身を本文に貼らず、パスを示してファイル参照させる
        dep_dir = self.bus.ensure_artifact_dir("dep1")
        with open(os.path.join(dep_dir, "big.txt"), "w") as f:
            f.write("X" * 100)
        captured = {}

        def fake(prompt, model, purpose=""):
            captured["prompt"] = prompt
            return "ok"

        with mock.patch.object(kf, "run_agent", side_effect=fake):
            kf.execute_agent("work", "後続処理", {}, None,
                            self.bus.node_artifact_dir("t2"), {"dep1": dep_dir})
        self.assertIn("中間成果物プロトコル", captured["prompt"])
        self.assertIn(dep_dir, captured["prompt"])
        self.assertIn("big.txt", captured["prompt"])
        self.assertNotIn("X" * 100, captured["prompt"])  # 中身は貼らない（参照のみ）

    def test_worker_records_artifacts_in_result(self):
        # ワーカーが実行中に書いた成果物を result に記録し、後続が発見できる
        bus = self.bus
        bus.write_graph({"nodes": {"t1": {"goal": "g", "deps": [], "kind": "work"}},
                         "iteration": 0})
        bus.write_task({"id": "t1", "goal": "g", "deps": [], "kind": "work"})
        bus.set_status("running")

        def fake_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None, **kw):
            with open(os.path.join(art_dir, "result.bin"), "w") as f:
                f.write("done")
            return "ok", None

        args = mock.Mock(bus=self.tmp, run_id="run1", git=None, node_id="w1",
                         executor="stub", model=None, lease=60, poll=0,
                         keep_alive=False, idle_exit=True)
        with mock.patch.object(kf, "execute_stub", side_effect=fake_exec), \
             mock.patch.object(kf, "make_bus", return_value=bus):
            kf.cmd_work(args)
        r = bus.read_result("t1")
        self.assertEqual(r["status"], "done")
        self.assertIn(os.path.join("artifacts", "t1", "result.bin"), r["artifacts"])

    def test_worker_records_exception_data_on_failure(self):
        # executor が例外に載せた構造化データ（gitlab 却下の issue_iid / guidance 等）は
        # 承認と対称に failed result の data として残る（消費側の文字列マッチ依存を無くす）
        bus = self.bus
        bus.write_graph({"nodes": {"t1": {"goal": "g", "deps": [], "kind": "work"}},
                         "iteration": 0})
        bus.write_task({"id": "t1", "goal": "g", "deps": [], "kind": "work"})
        bus.set_status("running")

        def fake_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None, **kw):
            err = RuntimeError("[gitlab-reject] 却下されました（未マージクローズ）（u）。やり直し指示: 命名を直す")
            err.data = {"issue_iid": 9, "web_url": "u", "decision": "rejected",
                        "reason": "未マージクローズ", "guidance": "命名を直す", "closed": True}
            raise err

        args = mock.Mock(bus=self.tmp, run_id="run1", git=None, node_id="w1",
                         executor="stub", model=None, lease=60, poll=0,
                         keep_alive=False, idle_exit=True)
        with mock.patch.object(kf, "execute_stub", side_effect=fake_exec), \
             mock.patch.object(kf, "make_bus", return_value=bus):
            kf.cmd_work(args)
        r = bus.read_result("t1")
        self.assertEqual(r["status"], "failed")
        self.assertIn("[gitlab-reject]", r["output"])          # 従来のテキストも維持（後方互換）
        self.assertEqual(r["data"]["decision"], "rejected")
        self.assertEqual(r["data"]["issue_iid"], 9)
        self.assertEqual(r["data"]["guidance"], "命名を直す")

    def test_worker_records_verify_rejection_as_completed_gate(self):
        bus = self.bus
        bus.write_graph({"nodes": {"v1": {"goal": "g", "deps": [], "kind": "verify"}},
                         "iteration": 0})
        bus.write_task({"id": "v1", "goal": "g", "deps": [], "kind": "verify"})
        bus.set_status("running")
        args = mock.Mock(bus=self.tmp, run_id="run1", git=None, node_id="w1",
                         executor="stub", model=None, lease=60, poll=0,
                         keep_alive=False, idle_exit=True)
        with mock.patch.object(kf, "execute_stub", return_value=("verify=fail", {"ok": False})), \
             mock.patch.object(kf, "make_bus", return_value=bus):
            kf.cmd_work(args)
        self.assertEqual(bus.read_result("v1")["status"], "done")

    def test_worker_fails_before_executor_when_workspace_clone_fails(self):
        root = tempfile.mkdtemp(prefix="kf-clone-fail-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        bus = kf.Bus(root, "run-clone")
        bus.ensure_run("req", workspace={"url": "https://invalid.example/repo.git"})
        bus.write_graph({"nodes": {"t1": {"goal": "g", "deps": [], "kind": "work"}},
                         "iteration": 0})
        bus.write_task({"id": "t1", "goal": "g", "deps": [], "kind": "work"})
        bus.set_status("running")
        args = mock.Mock(bus=root, run_id="run-clone", git=None, node_id="w1",
                         executor="stub", model=None, lease=60, poll=0,
                         keep_alive=False, idle_exit=True)
        execute = mock.Mock(return_value=("ok", None))
        with mock.patch.object(kf, "provision_tree", return_value=None), \
             mock.patch.object(kf, "execute_stub", execute), \
             mock.patch.object(kf, "make_bus", return_value=bus):
            kf.cmd_work(args)
        self.assertEqual(bus.read_result("t1")["status"], "failed")
        execute.assert_not_called()

    def test_base_sync_conflict_uses_local_agent_executor(self):
        bus = self.bus
        bus.write_graph({"nodes": {"base-sync": {
            "goal": "sync", "deps": [], "kind": "base-sync"}}, "iteration": 0})
        bus.write_task({"id": "base-sync", "goal": "sync", "deps": [], "kind": "base-sync"})
        bus.set_status("running")
        args = mock.Mock(bus=self.tmp, run_id="run1", git=None, node_id="w1",
                         executor="stub", model=None, lease=60, poll=0,
                         keep_alive=False, idle_exit=True)
        local_agent = mock.Mock(return_value=("resolved", {"ok": True}))
        with mock.patch.object(kf, "sync_workspace_base", return_value={
                 "status": "conflict", "conflict_files": ["f.txt"]}), \
             mock.patch.object(kf, "execute_agent", local_agent), \
             mock.patch.object(kf, "execute_stub") as stub, \
             mock.patch.object(kf, "finalize_workspace", return_value=None), \
             mock.patch.object(kf, "make_bus", return_value=bus):
            kf.cmd_work(args)
        local_agent.assert_called_once()
        stub.assert_not_called()


class StateGitSyncTests(unittest.TestCase):
    """状態の git 保存・共有（state_git）: ローカルバスのワーク内容（runs/・inbox/）を共有
    リポジトリへ双方向同期する。リモート負荷の律速（interval）・多重コミッタ・3-way 裁定
    （inbox はリモート優先/機械状態はローカル優先）・GitBus 時の無効化を検証する。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="kf-sg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        kf._STATE_GITS.clear()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        # 既定ブランチ名に依存しない: state_git_branch（main）へ HEAD を向けて clone が追従するように
        subprocess.run(["git", "-C", str(self.remote), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True)
        self.bus_root = self.tmp / "bus"

    def _args(self, **kw):
        base = dict(bus=str(self.bus_root), git=None, run_id=None,
                    state_git=str(self.remote), state_git_branch="main",
                    state_git_subdir="kf", state_git_interval=0.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _bus(self, run_id="run1"):
        bus = kf.Bus(str(self.bus_root), run_id)
        bus.ensure_run("test request")
        return bus

    def _other(self, name="other") -> pathlib.Path:
        """「他のプログラム」役: 同一リポジトリを普通に clone して commit/push するクローン。"""
        d = self.tmp / name
        subprocess.run(["git", "clone", "-q", str(self.remote), str(d)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(d), "config", "user.email", "other@test"], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.name", "other"], check=True)
        return d

    @staticmethod
    def _commit_push(d: pathlib.Path, msg="other"):
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", msg], check=True)
        subprocess.run(["git", "-C", str(d), "push", "-q", "-u", "origin", "main"],
                       check=True, capture_output=True)

    @staticmethod
    def _pull(d: pathlib.Path):
        subprocess.run(["git", "-C", str(d), "pull", "-q", "--rebase", "origin", "main"],
                       check=True, capture_output=True)

    def test_unpushed_consecutive_syncs_amend_into_one_commit(self):
        # push できない間に同期が続いても、未 push の state sync コミットは --amend で
        # 1 つに束ねられる（1 行差分のコミットが履歴を埋め尽くさない）。
        bus = self._bus()
        args = self._args()
        with mock.patch.object(kf.StateGit, "_push", lambda self: None):
            kf.state_sync(args, force=True)
            bus.write_task({"id": "T9", "goal": "g", "deps": []})
            kf.state_sync(args, force=True)
        sg = kf.state_git_for(args)
        r = subprocess.run(["git", "-C", str(sg.clone), "log", "--format=%s"],
                           capture_output=True, text=True)
        msgs = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].startswith("agent-flow: state sync"))

    def test_export_pushes_run_state_under_subdir(self):
        bus = self._bus()
        bus.write_task({"id": "T1", "goal": "g", "deps": []})
        kf.state_sync(self._args(), force=True)
        got = self._other("check")
        self.assertTrue((got / "kf" / "runs" / "run1" / "meta.json").exists())
        self.assertTrue((got / "kf" / "runs" / "run1" / "tasks" / "T1.json").exists())

    def test_import_inbox_drop(self):
        self._bus()
        kf.state_sync(self._args(), force=True)                  # 初期化（ブランチ作成）
        other = self._other()
        drop = other / "kf" / "inbox" / "req-9.json"
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_text(json.dumps({"request": "from viewer", "submitter": "viewer"}),
                        encoding="utf-8")
        self._commit_push(other, "viewer: submit")
        kf.state_sync(self._args(), force=True)                  # 投入がローカルバスへ届く
        bus = kf.Bus(str(self.bus_root), "_")
        self.assertIn("req-9", bus.list_inbox())
        self.assertEqual((bus.read_inbox("req-9") or {}).get("request"), "from viewer")

    def test_conflict_inbox_prefers_remote_and_runs_prefer_local(self):
        bus = self._bus()
        os.makedirs(self.bus_root / "inbox", exist_ok=True)
        req = self.bus_root / "inbox" / "req-1.json"
        req.write_text('{"request": "local"}', encoding="utf-8")
        meta = pathlib.Path(bus.meta_path)
        kf.state_sync(self._args(), force=True)
        other = self._other()
        (other / "kf" / "inbox" / "req-1.json").write_text('{"request": "remote"}',
                                                           encoding="utf-8")
        rmeta = other / "kf" / "runs" / "run1" / "meta.json"
        rmeta.write_text('{"status": "remote edit"}', encoding="utf-8")
        self._commit_push(other, "both edited")
        req.write_text('{"request": "local2"}', encoding="utf-8")   # 同時変更を作る
        bus.set_status("running")                                   # 機械状態のローカル変更
        kf.state_sync(self._args(), force=True)
        self.assertEqual(json.loads(req.read_text(encoding="utf-8"))["request"], "remote")
        self.assertEqual((kf.read_json(str(meta)) or {}).get("status"), "running")
        self._pull(other)
        self.assertEqual(json.loads(rmeta.read_text(encoding="utf-8")).get("status"), "running")

    def test_concurrent_committer_is_not_clobbered(self):
        # 他プログラムが（我々の pull の後に）同一リポジトリへ push しても、push 競合を
        # pull --rebase → 再 push で吸収して自分の変更を反映し、相手のコミットも壊さない。
        bus = self._bus()
        args = self._args()
        kf.state_sync(args, force=True)
        sg = kf.state_git_for(args)
        other = self._other()
        (other / "unrelated.txt").write_text("theirs\n", encoding="utf-8")
        self._commit_push(other, "other program commit")
        bus.write_task({"id": "T2", "goal": "g", "deps": []})
        real_git = sg._git
        state = {"skipped": False}

        def no_first_pull(*a, **kw):   # sync 冒頭の pull を 1 回落とし「pull 後に push された」競合を再現
            if a[:2] == ("pull", "--rebase") and not state["skipped"]:
                state["skipped"] = True
                return types.SimpleNamespace(returncode=1, stdout="", stderr="skipped")
            return real_git(*a, **kw)
        with mock.patch.object(sg, "_git", side_effect=no_first_pull):
            kf.state_sync(args, force=True)
        self._pull(other)
        self.assertTrue((other / "unrelated.txt").exists())
        self.assertTrue((other / "kf" / "runs" / "run1" / "tasks" / "T2.json").exists())

    def test_interval_rate_limits_sync(self):
        args = self._args(state_git_interval=3600.0)
        self._bus()
        kf.state_sync(args, force=True)                          # 初回は必ず同期
        other = self._other()
        drop = other / "kf" / "inbox" / "req-5.json"
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_text('{"request": "x"}', encoding="utf-8")
        self._commit_push(other, "drop")
        kf.state_sync(args)                                      # interval 内 → 何もしない（負荷律速）
        self.assertFalse((self.bus_root / "inbox" / "req-5.json").exists())
        kf.state_git_for(args)._last_remote = 0.0                # interval 経過を模擬
        kf.state_sync(args)
        self.assertTrue((self.bus_root / "inbox" / "req-5.json").exists())

    def test_disabled_with_git_bus_or_without_state_git(self):
        self.assertIsNone(kf.state_git_for(self._args(git="https://example/bus.git")))
        self.assertIsNone(kf.state_git_for(self._args(state_git=None)))
        kf.state_sync(self._args(git="https://example/bus.git"), force=True)   # 何もしない
        self.assertFalse((self.bus_root / ".state-git").exists())

    def test_tmp_and_dot_files_excluded(self):
        """書きかけ / クラッシュ残骸の一時ファイルは共有状態リポジトリへ送らない。

        実際に生成されるのは原子書き込みの `<name>.tmp.<pid>[.<unique>]`
        （`agentcore.protocol.write_json_atomic` / `agent_flow.util.write_json_atomic`）。
        素の `.tmp` だけを除外していた頃は、torn JSON の残骸が commit・push されて
        全 PC のクローンへ配られていた。"""
        bus = self._bus()
        names = ["meta.json.tmp",                       # `_save_manifest` 形式
                 f"meta.json.tmp.{os.getpid()}",        # 旧・原子書き込み形式
                 f"meta.json.tmp.{os.getpid()}.1234"]   # 現・原子書き込み形式
        for name in names:
            with open(os.path.join(bus.run_dir, name), "w", encoding="utf-8") as f:
                f.write("{")                            # torn JSON（書きかけ）
        kf.state_sync(self._args(), force=True)
        got = self._other("check")
        self.assertTrue((got / "kf" / "runs" / "run1" / "meta.json").exists())
        for name in names:
            self.assertFalse((got / "kf" / "runs" / "run1" / name).exists(), name)
        self.assertFalse((got / "kf" / ".state-git").exists())

    def test_dot_prefixed_subdir_works(self):
        # state_git_subdir はドット始まり（.agent-flow 等）でも同期できる（推奨は非ドット）。
        self._bus()
        kf.state_sync(self._args(state_git_subdir=".agent-flow"), force=True)
        got = self._other("check")
        self.assertTrue((got / ".agent-flow" / "runs" / "run1" / "meta.json").exists())

    def test_sync_failure_does_not_kill_caller(self):
        args = self._args(state_git=str(self.tmp / "no-such-remote.git"))
        self._bus()
        kf.state_sync(args, force=True)                          # 不通でも例外を漏らさない
        self.assertFalse((self.bus_root / ".state-git" / ".git").exists())

    def test_deletion_propagates_like_gc(self):
        bus = self._bus()
        kf.state_sync(self._args(), force=True)
        shutil.rmtree(bus.run_dir)                               # gc / remove_run 相当の掃除
        kf.state_sync(self._args(), force=True)
        got = self._other("check")
        self.assertFalse((got / "kf" / "runs" / "run1").exists())

    def test_durable_write_config_on_state_clone_and_local_remote(self):
        # state_git の管理クローンと、ローカルパスの共有リポジトリ本体にも durable-write を効かせる
        # （電源断でのサイズ 0 オブジェクトを予防）。
        args = self._args()
        self._bus()
        kf.state_sync(args, force=True)
        sg = kf.state_git_for(args)
        self.assertEqual(_git_config_get(sg.clone, "core.fsync"), "all")
        self.assertEqual(_git_config_get(sg.clone, "core.fsyncMethod"), "batch")
        self.assertEqual(_git_config_get(self.remote, "core.fsync"), "all")
        self.assertEqual(_git_config_get(self.remote, "core.fsyncMethod"), "batch")

    def test_empty_objects_state_clone_is_rebuilt_on_reuse(self):
        # 電源断で state_git クローンのオブジェクトが空になったら、次プロセス（新インスタンス）の
        # _ensure_clone が fsck で破損を検知して捨て、作り直す（manifest を失っても 3-way が再収束）。
        args = self._args()
        bus = self._bus()
        bus.write_task({"id": "T1", "goal": "g", "deps": []})
        kf.state_sync(args, force=True)                          # 管理クローン作成 + export
        sg = kf.state_git_for(args)
        clone = sg.clone
        self.assertGreater(_zero_loose_objects(clone), 0)        # loose object を空に
        self.assertFalse(sg._probe_integrity())                 # 破損を検知できる
        kf._STATE_GITS.clear()                                   # 次プロセス相当（新インスタンス）
        kf.state_sync(args, force=True)                          # 再利用 _ensure_clone → 作り直し
        sg2 = kf.state_git_for(args)
        self.assertTrue(sg2._probe_integrity())                 # 健全なクローンへ再生
        self.assertEqual(_git_config_get(sg2.clone, "core.fsync"), "all")  # 予防設定も再適用
        # 作り直し後も状態は共有リポジトリへ届く（export が回復）
        got = self._other("check")
        self.assertTrue((got / "kf" / "runs" / "run1" / "tasks" / "T1.json").exists())

    def test_state_sync_self_heals_on_object_corruption_midflight(self):
        # 稼働中（_ready 済み）に破損が露見しても state_sync は例外を漏らさず、クローンを捨てて
        # 次回作り直す。呼び出し側（daemon ループ）は殺されない。
        args = self._args()
        self._bus()
        kf.state_sync(args, force=True)                          # 初期化（_ready = True）
        sg = kf.state_git_for(args)
        # リモートを 1 コミット進めておき、次の pull --rebase が破損した HEAD を必ず読むようにする
        other = self._other()
        drop = other / "kf" / "inbox" / "req-x.json"
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_text('{"request":"x"}', encoding="utf-8")
        self._commit_push(other, "advance remote")
        _zero_loose_objects(sg.clone)                            # 電源断でのサイズ 0 を模擬
        with mock.patch.object(kf._transport, "backoff_sleep", lambda s: None):
            kf.state_sync(args, force=True)                      # 例外を漏らさず作り直しを予約
        self.assertFalse(sg._ready)                              # クローンは破棄され次回作り直し
        self.assertFalse(os.path.isdir(os.path.join(sg.clone, ".git")))
        kf.state_sync(args, force=True)                          # 次回同期で健全に作り直す
        self.assertTrue(sg._probe_integrity())
