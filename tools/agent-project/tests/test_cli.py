"""agent-project の単体テスト — cli（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from _shared import _MOD, _commit_change, _make_skill_repo, _write_backlog_task  # noqa: E402,F401 — `import *` は _ 始まりを持ってこない


class TestKiroFlowIntegration(unittest.TestCase):
    def test_stub_end_to_end(self):
        kf = Path(__file__).resolve().parents[2] / "agent-flow" / "agent-flow.py"
        if not kf.exists():
            self.skipTest("agent-flow.py が見つからない")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            out = d / "out.txt"
            out.write_text("done")
            mkb(d, "T1", title="何か", verify=f"test -f {out}")
            os.environ["AGENT_FLOW_STUB_SLEEP_MAX"] = "0"
            res = km.run_loop(cfg_for(d, dry_run=False, act_timeout=120, max_cycles=3))
            self.assertEqual(res["counts"]["done"], 1)
            self.assertEqual(res["reason"], km.REASON_DRAINED)


class TestCliEndToEnd(unittest.TestCase):
    """agent-project.py を実プロセスとして argv 起動する黒箱 CLI e2e。

    TestRunLoop が run_loop() を in-process で呼ぶのに対し、こちらは CLI 配線（argparse・パス解決・
    停止理由→exit code・成果物の書き出し）を実バイナリで検証する。act は --dry-run で省略し、
    ループ機構そのもの（優先順位→verify→done/archive/blocked/needs）を確認する。
    パスは絶対（mkdtemp）で渡す: 相対パスは --workdir 基準で解決され picked up されないため。"""

    def _run(self, d: Path, *extra, timeout=60):
        cmd = [sys.executable, str(_MOD), "run", "--no-delivery-review",
               "--workdir", str(d), "--backlog", str(d / "backlog"),
               "--policy", str(d / "policy.md"), "--decisions", str(d / "decisions"),
               "--journal", str(d / "journal.md"), "--needs", str(d / "needs"),
               "--bus", str(d / "bus"), "--planner", "none",
               "--executor", "stub", "--flow-planner", "stub"]
        cmd += list(extra)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def test_revise_feedback_cli_does_not_require_task_node_argument(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            task = km.load_tasks(d / "backlog")[0]
            task.set("node", "pc-assigned")
            km.persist_task(cfg_for(d), task)
            p = subprocess.run(
                [sys.executable, str(_MOD), "revise", "T1", "--root", str(d),
                 "--feedback", "最新 target でやり直す"],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            revised = km.load_tasks(d / "backlog")[0]
            self.assertEqual(revised.norm_status(), "ready")
            self.assertEqual(revised.get("node"), "pc-assigned")

    def test_revise_task_node_cli_reassigns_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="ready", verify="true")
            p = subprocess.run(
                [sys.executable, str(_MOD), "revise", "T1", "--root", str(d),
                 "--task-node", "pc-new"],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual(km.load_tasks(d / "backlog")[0].get("node"), "pc-new")

    def test_drains_and_archives(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write_backlog_task(d / "backlog", "T1", "true")
            _write_backlog_task(d / "backlog", "T2", "true")
            p = self._run(d, "--dry-run", "--max-cycles", "10")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)   # drained → 0
            self.assertIn("drained", p.stdout)
            self.assertIn("done=2", p.stdout)
            self.assertEqual(list((d / "backlog").glob("*.md")), [])  # backlog から消える

    def test_blocked_when_verify_fails(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write_backlog_task(d / "backlog", "T1", "false")        # verify は必ず FAIL
            p = self._run(d, "--dry-run", "--max-retries", "0", "--max-cycles", "10")
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)   # blocked → 1
            self.assertIn("blocked=1", p.stdout)
            self.assertTrue((d / "needs" / "T1.md").exists())        # 人の判断へ委譲
            self.assertTrue((d / "backlog" / "T1.md").exists())      # backlog には残す

    def test_budget_stop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write_backlog_task(d / "backlog", "T1", "false")
            # 無限リトライ相当 + サイクル上限 → drain せず予算で停止
            p = self._run(d, "--dry-run", "--max-retries", "999", "--max-cycles", "3")
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)   # budget → 2
            self.assertIn("budget", p.stdout)

    def test_no_archive_deletes_instead(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write_backlog_task(d / "backlog", "T1", "true")
            p = self._run(d, "--dry-run", "--no-archive", "--max-cycles", "10")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("done=1", p.stdout)
            self.assertIn("archived=0", p.stdout)                    # 退避せず削除
            self.assertEqual(list((d / "backlog").glob("*.md")), [])


class TestCliKiroFlowDelegation(unittest.TestCase):
    """agent-project CLI が act を実際に agent-flow.py へサブプロセス委譲し、完走することを検証する
    クロスツール e2e。委譲の証跡（argv）と委譲先 agent-flow の正常終了をラッパで捕捉して検証する。"""

    def test_cli_delegates_to_real_agent_flow(self):
        kf = Path(__file__).resolve().parents[2] / "agent-flow" / "agent-flow.py"
        if not kf.exists():
            self.skipTest("agent-flow.py が見つからない")
        os.environ["AGENT_FLOW_STUB_SLEEP_MAX"] = "0"   # stub の擬似スリープ無効化（子へ継承）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            log = d / "kf.log"
            # ラッパ: 委譲 argv を記録 → 本物の agent-flow へ転送 → その exit code も記録/伝播
            wrapper = d / "kfwrap.py"
            wrapper.write_text(
                "import sys, subprocess\n"
                "argv = sys.argv[1:]\n"
                f"open(r'{log}', 'a').write('ARGV\\t' + '\\t'.join(argv) + '\\n')\n"
                f"rc = subprocess.run([sys.executable, r'{kf}'] + argv).returncode\n"
                f"open(r'{log}', 'a').write('RC\\t%d\\n' % rc)\n"
                "sys.exit(rc)\n", encoding="utf-8")
            marker = d / "marker"
            marker.write_text("done")   # act は best-effort。verify が真実の源なので事前に通る状態を作る
            _write_backlog_task(d / "backlog", "T1", f"test -f {marker}", title="何かを実装")
            cmd = [sys.executable, str(_MOD), "run", "--no-delivery-review",
                   "--workdir", str(d), "--backlog", str(d / "backlog"),
                   "--policy", str(d / "policy.md"), "--decisions", str(d / "decisions"),
                   "--journal", str(d / "journal.md"), "--needs", str(d / "needs"),
                   "--bus", str(d / "bus"), "--planner", "none",
                   "--executor", "stub", "--flow-planner", "stub",
                   "--agent-flow", str(wrapper),
                   "--act-timeout", "150", "--max-cycles", "3"]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("done=1", p.stdout)
            logtext = log.read_text(encoding="utf-8")
            # 実際に agent-flow が `run --planner stub --executor stub …` で起動された証跡
            self.assertIn("\trun\t", logtext)
            self.assertIn("--planner", logtext)
            self.assertIn("--executor", logtext)
            self.assertIn("stub", logtext)
            # 委譲先 agent-flow（orchestrator/worker まで含む）自身が正常終了した
            self.assertIn("RC\t0", logtext)


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ka-update-"))
        self.state = self.tmp / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self._old = os.environ.get("KIRO_STATE_HOME")
        os.environ["KIRO_STATE_HOME"] = str(self.state)
        km._UPDATE_LAST_CHECK["t"] = 0.0          # モジュール状態を毎テストでリセット
        self.repo = _make_skill_repo(self.tmp)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KIRO_STATE_HOME", None)
        else:
            os.environ["KIRO_STATE_HOME"] = self._old
        km._UPDATE_LAST_CHECK["t"] = 0.0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, **kw):
        base = dict(update_repo=str(self.repo), update_branch="main",
                    update_subdir="tools/agent-project", update_installer="install.sh",
                    update_check_interval=60.0)
        base.update(kw)
        return cfg_for(self.tmp, **base)

    def test_remote_branch_sha(self):
        sha = km.remote_branch_sha(str(self.repo), "main")
        self.assertTrue(sha and len(sha) >= 7)
        self.assertIsNone(km.remote_branch_sha("", "main"))
        self.assertIsNone(km.remote_branch_sha(str(self.repo), "no-such-branch"))

    def test_check_update_baseline_then_latest(self):
        cfg = self._cfg()
        info = km.check_update(cfg)             # 初回: ベースライン
        self.assertTrue(info["enabled"] and info["baseline"])
        self.assertFalse(info["available"])
        self.assertFalse(km.check_update(cfg)["available"])   # 2 回目: 最新

    def test_check_update_detects_new_commit(self):
        cfg = self._cfg()
        km.check_update(cfg)
        _commit_change(self.repo, "tools/agent-project/NEW.txt")
        self.assertTrue(km.check_update(cfg)["available"])

    def test_disabled_when_no_repo(self):
        cfg = self._cfg(update_repo=None)
        self.assertFalse(km.check_update(cfg)["enabled"])
        self.assertFalse(km.maybe_self_update(cfg))

    def test_sparse_checkout_only_subdir(self):
        dest = str(self.tmp / "co" / "repo")
        tool_dir = km.sparse_checkout_tool(str(self.repo), "main",
                                           "tools/agent-project", dest)
        self.assertTrue(os.path.isfile(os.path.join(tool_dir, "install.sh")))
        self.assertFalse(os.path.isdir(os.path.join(dest, "tools", "agent-flow")))

    def test_split_subdirs_accepts_multiple_and_falls_back_split(self):
        self.assertEqual(km.split_subdirs("a/b c/d"), ["a/b", "c/d"])
        self.assertEqual(km.split_subdirs("a/b, c/d"), ["a/b", "c/d"])
        self.assertEqual(km.split_subdirs("a/b"), ["a/b"])
        # 空は既定へ。既定自体が複数パスの文字列なので、包むだけだと空白入りの 1 パスを
        # sparse-checkout へ渡してしまう（必ず外す）。
        fallback = km.split_subdirs("")
        self.assertEqual(fallback, km.TOOL_SUBDIR.split())
        self.assertTrue(all(" " not in p for p in fallback))

    def test_sparse_checkout_includes_dependency_package(self):
        """共有物（実物では tools/agent-tools＝統合インストーラ + agentcore）も取れること。

        cone mode の sparse-checkout は指定ディレクトリの**兄弟を含まない**。本体だけ取ると
        installer が zipapp へ同梱する agentcore が無く `agentcore パッケージが見つかりません`
        で必ず失敗する——自己更新が毎回サイレントに見送られる（実測で確認した既存不具合）。"""
        dest = str(self.tmp / "co2" / "repo")
        tool_dir = km.sparse_checkout_tool(str(self.repo), "main",
                                           "tools/agent-project tools/agent-tools", dest)
        self.assertEqual(tool_dir, os.path.join(dest, "tools", "agent-project"))
        self.assertTrue(os.path.isdir(os.path.join(dest, "tools", "agent-tools", "agentcore")))
        # 統合インストーラ（親ディレクトリのファイル）も cone mode で落ちてくる——
        # 各エンジンの install.sh はそこへ委譲するシムなので、無いと自己更新が動かない。
        self.assertTrue(os.path.isfile(os.path.join(dest, "tools", "agent-tools", "install.sh")))
        self.assertFalse(os.path.isdir(os.path.join(dest, "tools", "agent-flow")))

    def test_default_subdir_carries_dependency(self):
        # 既定が本体だけだと、既定のまま運用している全ノードで自己更新が失敗する。
        self.assertIn("tools/agent-tools", km.split_subdirs(km.TOOL_SUBDIR))

    def test_apply_update_triggers_on_dependency_only_change(self):
        """依存パッケージだけの変更でも適用されること。

        ダイジェストを先頭 subdir だけで取ると agentcore だけの更新を「変更なし」と読んで
        見送り続ける——本体は agentcore と契約バージョンを共有しているので、そこだけ古い
        まま回るのが一番まずい。"""
        cfg = self._cfg(update_subdir="tools/agent-project tools/agent-tools")
        km.check_update(cfg)                    # baseline
        prefix = str(self.tmp / "prefix-dep")

        def runner(c, **k):
            cmd = c + ["--prefix", prefix] if c[:1] == ["bash"] else c
            return subprocess.run(cmd, capture_output=True, text=True, **k)

        _commit_change(self.repo, "tools/agent-tools/agentcore/protocol.py", "# bumped\n")
        info = km.check_update(cfg)
        self.assertTrue(info["available"])
        self.assertTrue(km.apply_update(cfg, info, runner=runner),
                        "依存パッケージだけの更新が見送られている")
        self.assertTrue(os.path.isfile(os.path.join(prefix, "INSTALLED_MARKER")))

    def test_apply_update_records_sha(self):
        cfg = self._cfg()
        km.check_update(cfg)                    # baseline
        _commit_change(self.repo, "tools/agent-project/N2.txt")
        info = km.check_update(cfg)
        self.assertTrue(info["available"])
        prefix = str(self.tmp / "prefix")

        def runner(c, **k):                     # install.sh だけ --prefix を足す
            cmd = c + ["--prefix", prefix] if c[:1] == ["bash"] else c
            return subprocess.run(cmd, capture_output=True, text=True, **k)
        self.assertTrue(km.apply_update(cfg, info, runner=runner))
        self.assertEqual(km.read_update_state()["applied_sha"], info["remote_sha"])
        self.assertTrue(os.path.isfile(os.path.join(prefix, "INSTALLED_MARKER")))
        self.assertFalse(km.check_update(cfg)["available"])   # 適用後は最新

    def test_maybe_self_update_disabled_interval(self):
        cfg = self._cfg(update_check_interval=0.0)   # interval<=0 で無効
        self.assertFalse(km.maybe_self_update(cfg))

    def test_update_enabled_false_disables(self):
        cfg = self._cfg(update_enabled=False, update_check_interval=3600.0)
        self.assertFalse(km.maybe_self_update(cfg))

    def test_apply_update_skips_when_tool_unchanged(self):
        # リポジトリの HEAD は進んだが update_subdir の内容は前回適用と同一 → installer を
        # 実行せずベースラインだけ進める。direct state-git 構成では自分の state sync push が
        # update_repo の新コミットになるため、SHA 比較だけだと「push → 更新検出 → 再起動 →
        # また push」の自己増殖ループになる（2026-07-11 に実発生）。
        cfg = self._cfg()
        km.check_update(cfg)                                   # baseline
        _commit_change(self.repo, "tools/agent-project/N3.txt")
        prefix = str(self.tmp / "prefix3")

        def runner(c, **k):
            cmd = c + ["--prefix", prefix] if c[:1] == ["bash"] else c
            return subprocess.run(cmd, capture_output=True, text=True, **k)
        self.assertTrue(km.apply_update(cfg, km.check_update(cfg), runner=runner))  # 実変更 → 適用
        _commit_change(self.repo, "journal.md")                # subdir 外だけが進む
        info = km.check_update(cfg)
        self.assertTrue(info["available"])                     # SHA 上は更新に見える
        calls = []

        def counting(c, **k):
            calls.append(list(c))
            return runner(c, **k)
        self.assertFalse(km.apply_update(cfg, info, runner=counting))    # 適用スキップ
        self.assertFalse(any(c[:1] == ["bash"] for c in calls))          # installer 不実行
        self.assertEqual(km.read_update_state()["applied_sha"], info["remote_sha"])
        self.assertFalse(km.check_update(cfg)["available"])    # ベースライン前進 → 最新扱い

    def test_apply_update_skips_outside_change_with_multi_path_subdir(self):
        """複数パスの update_subdir でも自己増殖ループの防止が効くこと。

        ダイジェストをチェックアウト全体で取ると、cone mode が落とすリポジトリ直下の
        ファイル（direct state-git 構成では自分の state push がそこを動かす）で毎回
        差分になり、「push → 更新検出 → 再起動 → また push」に戻る。"""
        cfg = self._cfg(update_subdir="tools/agent-project tools/agent-tools")
        km.check_update(cfg)
        prefix = str(self.tmp / "prefix-multi")

        def runner(c, **k):
            cmd = c + ["--prefix", prefix] if c[:1] == ["bash"] else c
            return subprocess.run(cmd, capture_output=True, text=True, **k)

        _commit_change(self.repo, "tools/agent-project/N4.txt")
        self.assertTrue(km.apply_update(cfg, km.check_update(cfg), runner=runner))
        _commit_change(self.repo, "journal.md")     # subdir 外（リポジトリ直下）だけが進む
        info = km.check_update(cfg)
        self.assertTrue(info["available"])          # SHA 上は更新に見える
        calls = []

        def counting(c, **k):
            calls.append(list(c))
            return runner(c, **k)
        self.assertFalse(km.apply_update(cfg, info, runner=counting))
        self.assertFalse(any(c[:1] == ["bash"] for c in calls), "installer が再実行されている")

    def test_update_check_interval_survives_restart(self):
        # チェック間隔は state ファイルへ持続化され、自己更新の再起動（新プロセス＝メモリの
        # 時刻リセット）を跨いで尊重される（再起動直後の即時再チェックを防ぐ）。
        cfg = self._cfg(update_check_interval=3600.0)
        calls = []
        with mock.patch.object(km, "check_update",
                               side_effect=lambda *a, **k: (calls.append(1),
                                                            {"available": False})[1]):
            self.assertFalse(km.maybe_self_update(cfg))
            self.assertEqual(len(calls), 1)                    # 初回はチェックする
            km._UPDATE_LAST_CHECK["t"] = 0.0                   # プロセス再起動を模擬
            self.assertFalse(km.maybe_self_update(cfg))
            self.assertEqual(len(calls), 1)                    # 間隔内 → 再チェックしない

    def test_registry_auto_resolution(self):
        # update_repo 未指定でも skill-registry.json から repo/branch を解決して検出できる
        regdir = self.tmp / "agenthome"
        regdir.mkdir(parents=True, exist_ok=True)
        (regdir / "skill-registry.json").write_text(json.dumps({
            "version": 7, "install_dir": str(self.tmp),
            "repositories": [{"name": "origin", "url": str(self.repo),
                              "branch": "main", "priority": 1}]}))
        old = os.environ.get("KIRO_SKILL_REGISTRY")
        os.environ["KIRO_SKILL_REGISTRY"] = str(regdir)
        try:
            self.assertEqual(km.registry_update_source()[0], str(self.repo))
            cfg = self._cfg(update_repo=None)     # 明示なし → registry から解決
            info = km.check_update(cfg)
            self.assertTrue(info["enabled"])
            self.assertEqual(info["repo"], str(self.repo))
        finally:
            if old is None:
                os.environ.pop("KIRO_SKILL_REGISTRY", None)
            else:
                os.environ["KIRO_SKILL_REGISTRY"] = old

    def test_explicit_repo_overrides_registry(self):
        cfg = self._cfg(update_repo="/explicit/path", update_branch="dev")
        self.assertEqual(km.resolve_update_target(cfg), ("/explicit/path", "dev"))

    def test_run_watch_restarts_on_update(self):
        # アイドルの watch ループで自己更新が成立したら _RestartRequested が送出されること。
        # （idle 配線の検証。更新判定そのものは maybe_self_update を True に差し替える）
        cfg = self._cfg()
        with mock.patch.object(km, "maybe_self_update", return_value=True):
            with self.assertRaises(km._RestartRequested):
                # backlog 空 → run_loop は即 drain → idle ループへ。sleeper は即戻り。
                km.run_watch(cfg, sleeper=lambda _s: None)


class TestGitlabRejectRetry(unittest.TestCase):
    """委譲 executor（gitlab）の却下→通常リトライ連携: 内部再委譲を抑止（--max-retries 0）し、
    却下時の人コメント（[gitlab-reject]）を次 act の feedback に注入する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ka-rej-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_executor_delegates(self):
        self.assertFalse(km.executor_delegates(cfg_for(self.tmp, executor="agent")))
        self.assertTrue(km.executor_delegates(cfg_for(self.tmp, executor="gitlab")))

    def test_build_cmd_sets_max_retries_zero_for_gitlab(self):
        mkb(self.tmp, "t1")
        t = km.load_tasks((self.tmp / "backlog"))[0]
        cmd = km.build_agent_flow_cmd(t, cfg_for(self.tmp, executor="gitlab"))
        self.assertIn("--max-retries", cmd)
        self.assertEqual(cmd[cmd.index("--max-retries") + 1], "0")
        # kiro executor では付けない
        cmd2 = km.build_agent_flow_cmd(t, cfg_for(self.tmp, executor="agent"))
        self.assertNotIn("--max-retries", cmd2)

    def test_read_reject_guidance_extracts_marker(self):
        cfg = cfg_for(self.tmp, executor="gitlab")
        result_json = json.dumps({"final_nodes": [
            {"id": "n1", "output": "実行エラー: [gitlab-reject] 却下されました（u）。"
                                   "やり直し指示: 命名を要件に合わせる"}]})

        def fake_run(cmd, **kw):
            return types.SimpleNamespace(returncode=1, stdout=result_json, stderr="")

        with mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            g = km.read_reject_guidance(cfg, use_git=False)
        self.assertIn("命名を要件に合わせる", g)
        self.assertNotIn("[gitlab-reject]", g)

    def test_read_reject_guidance_prefers_structured_data(self):
        # agent-flow の gitlab executor は却下時に failed result へ構造化 data を残す。
        # 文字列マーカーより data（decision=rejected の guidance）を優先して読む。
        cfg = cfg_for(self.tmp, executor="gitlab")
        result_json = json.dumps({"final_nodes": [
            {"id": "n1",
             "output": "実行エラー: [gitlab-reject] 却下されました（u）。やり直し指示: 古い方の指示",
             "data": {"decision": "rejected", "issue_iid": 9,
                      "guidance": "構造化データ側の指示"}}]})

        def fake_run(cmd, **kw):
            return types.SimpleNamespace(returncode=1, stdout=result_json, stderr="")

        with mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            g = km.read_reject_guidance(cfg, use_git=False)
        self.assertEqual(g, "構造化データ側の指示")

    def test_read_reject_guidance_empty_when_no_marker(self):
        cfg = cfg_for(self.tmp, executor="gitlab")

        def fake_run(cmd, **kw):
            return types.SimpleNamespace(
                returncode=0, stdout='{"final_nodes":[{"output":"ok"}]}', stderr="")

        with mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            self.assertEqual(km.read_reject_guidance(cfg, use_git=False), "")

    def test_settle_failure_injects_reject_comment_as_feedback(self):
        cfg = cfg_for(self.tmp, executor="gitlab", max_retries=2)
        (self.tmp / "backlog").mkdir(parents=True, exist_ok=True)
        t = km.Task(id="t1", title="ログイン", verify="true", status="doing")
        with mock.patch.object(km, "read_reject_guidance", return_value="命名を直す"):
            km._settle_failure(cfg, t, "verify NG", cycle=1, ev="", reasons={}, location="local")
        self.assertEqual(t.norm_status(), "ready")          # 積み直し
        self.assertEqual(t.feedback(), "命名を直す")          # 却下コメントを feedback に注入
