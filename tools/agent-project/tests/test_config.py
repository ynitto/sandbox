"""agent-project の単体テスト — config（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class TestRepoRegistry(unittest.TestCase):
    """repos レジストリ（schemas/repos.schema.json）。<project>/repos.{yaml,yml,json} があれば
    レジストリの正になり、charter の ## repos は互換入力。repos ファイル単独では charter モード
    （目標駆動）は発動しないが、ワークスペース・ルーティングには使える。"""

    def test_registry_file_overrides_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (d / "charter.md").write_text(
                "# Charter: x\n## goal\ny\n## repos\n- old = git@x:old.git\n"
                "  - desc: 旧\n  - base: main\n  - owns: src/**\n", encoding="utf-8")
            (d / "repos.json").write_text(__import__("json").dumps(
                {"app": {"url": "git@x:app.git", "desc": "新", "base": "main",
                         "owns": ["src/**"], "docs": ["docs/**"]}}), encoding="utf-8")
            before = (d / "repos.json").read_text(encoding="utf-8")
            ch = km.load_charter(cfg)
            self.assertEqual([s["name"] for s in ch.repo_specs], ["app"])   # ファイルが勝つ
            self.assertEqual(ch.repo_specs[0]["target"], "main")            # target 省略 = base
            self.assertFalse(ch.repo_specs[0]["readonly"])                  # owns あり = 書込先
            self.assertEqual((d / "repos.json").read_text(encoding="utf-8"),
                             before)                                        # 手書きは上書きしない

    def test_registry_without_charter_routes_but_no_charter_mode(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (d / "repos.json").write_text(__import__("json").dumps(
                {"app": {"url": "git@x:app.git", "desc": "本体", "base": "main",
                         "owns": ["src/**"]}}), encoding="utf-8")
            self.assertIsNone(km.load_charter(cfg))          # 目標駆動は発動しない（charter.md 無し）
            bd = d / "backlog"
            bd.mkdir(parents=True, exist_ok=True)
            (bd / "T1.md").write_text(
                "## T1: x を直す\n- status: ready\n- verify: `true`\n- paths: src/x.py\n",
                encoding="utf-8")
            t = [x for x in km.load_tasks(cfg.backlog) if x.id == "T1"][0]
            spec, routed = km.resolve_workspace(cfg, t, km.load_policy(cfg.policy))
            self.assertEqual((spec["name"], routed), ("app", "owns"))       # レジストリ単独で解決

    def test_charter_exports_generated_registry(self):
        """repos ファイルが無ければ charter から自動生成して外部ツール（codd-gate --repos）へ渡す。
        生成物には _meta マーカーが付き、正は charter のまま（charter 変更に追従・手書きなら不干渉）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (d / "charter.md").write_text(
                "# Charter: x\n## goal\ny\n## repos\n- app = git@x:app.git\n"
                "  - desc: 本体\n  - base: main\n  - owns: src/**\n"
                "  - docs: docs/**, README.md\n", encoding="utf-8")
            ch = km.load_charter(cfg)
            rp = d / "repos.json"
            self.assertTrue(rp.exists())                       # charter から自動生成
            data = __import__("json").loads(rp.read_text(encoding="utf-8"))
            self.assertIn("generated_from", data["_meta"])     # 生成物マーカー
            self.assertEqual(data["app"]["url"], "git@x:app.git")
            self.assertEqual(data["app"]["docs"], ["docs/**", "README.md"])   # 分類グロブも損失なし
            self.assertEqual([s["name"] for s in ch.repo_specs], ["app"])     # 正は charter のまま
            (d / "charter.md").write_text(                     # charter 更新 → 生成物が追従
                "# Charter: x\n## goal\ny\n## repos\n- app2 = git@x:app2.git\n"
                "  - desc: 本体2\n  - base: main\n  - owns: src/**\n", encoding="utf-8")
            km.load_charter(cfg)
            data = __import__("json").loads(rp.read_text(encoding="utf-8"))
            self.assertIn("app2", data)
            self.assertNotIn("app", data)
            (d / "charter.md").write_text(                     # ## repos が消えたら生成物も消す
                "# Charter: x\n## goal\ny\n", encoding="utf-8")
            km.load_charter(cfg)
            self.assertFalse(rp.exists())

    def test_broken_registry_falls_back_to_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (d / "charter.md").write_text(
                "# Charter: x\n## goal\ny\n## repos\n- old = git@x:old.git\n"
                "  - desc: 旧\n  - base: main\n  - owns: src/**\n", encoding="utf-8")
            (d / "repos.json").write_text("{ 壊れた json", encoding="utf-8")
            ch = km.load_charter(cfg)
            self.assertEqual([s["name"] for s in ch.repo_specs], ["old"])   # 黙って空にしない


class TestAgentCliAndGranularity(unittest.TestCase):
    """agent_cli 切替（kiro-cli / Claude Code）・root 基準のパス解決・バックログ分解粒度。"""

    @staticmethod
    def _resolve(cfg_path=None, **cli):
        ns = types.SimpleNamespace(config=cfg_path, **cli)
        km.resolve_config(ns)
        return ns

    @staticmethod
    def _capture_run():
        calls = {}
        def fake_run(cmd, **kw):
            calls["cmd"] = list(cmd)
            calls["input"] = kw.get("input")
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        return calls, fake_run

    def test_run_agent_cli_default_agent_argv(self):
        calls, fake = self._capture_run()
        with mock.patch.object(km, "_AGENT_CLI", "kiro"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake):
            out = km._run_agent_cli("プロンプト", None)
        self.assertEqual(out, "ok")
        self.assertEqual(calls["cmd"][:4],
                         ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"])
        self.assertEqual(calls["cmd"][-1], "プロンプト")   # 従来どおり argv 渡し
        self.assertIsNone(calls["input"])

    def test_run_agent_cli_claude_uses_stdin(self):
        calls, fake = self._capture_run()
        with mock.patch.object(km, "_AGENT_CLI", "claude"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake):
            out = km._run_agent_cli("プロンプト", "claude-sonnet")
        self.assertEqual(out, "ok")
        self.assertEqual(calls["cmd"][0], "claude")
        self.assertIn("-p", calls["cmd"])
        self.assertIn("--model", calls["cmd"])
        self.assertEqual(calls["input"], "プロンプト")     # stdin 渡し
        self.assertNotIn("プロンプト", calls["cmd"])       # argv には載せない

    def test_run_agent_cli_copilot_uses_prompt_flag(self):
        calls, fake = self._capture_run()
        with mock.patch.object(km, "_AGENT_CLI", "copilot"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake):
            out = km._run_agent_cli("プロンプト", "gpt-5")
        self.assertEqual(out, "ok")
        self.assertEqual(calls["cmd"][0], "copilot")
        self.assertIn("-s", calls["cmd"])                  # 応答本文のみ
        self.assertIn("--allow-all-tools", calls["cmd"])   # 非対話モードの必須フラグ
        i = calls["cmd"].index("-p")
        self.assertEqual(calls["cmd"][i + 1], "プロンプト")  # -p の引数で渡す
        self.assertIn("--model", calls["cmd"])
        self.assertIsNone(calls["input"])

    def test_run_agent_cli_codex_uses_exec_and_last_message_file(self):
        calls = {}
        def fake_run(cmd, **kw):
            calls["cmd"] = list(cmd)
            calls["input"] = kw.get("input")
            # codex は最終応答を --output-last-message のファイルへ書く
            i = cmd.index("--output-last-message")
            with open(cmd[i + 1], "w", encoding="utf-8") as f:
                f.write("最終応答")
            return types.SimpleNamespace(returncode=0, stdout="イベントログ...", stderr="")
        with mock.patch.object(km, "_AGENT_CLI", "codex"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            out = km._run_agent_cli("プロンプト", "gpt-5-codex")
        self.assertEqual(out, "最終応答")                  # stdout のログではなくファイルの中身
        self.assertEqual(calls["cmd"][:2], ["codex", "exec"])
        self.assertIn("--skip-git-repo-check", calls["cmd"])
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", calls["cmd"])
        self.assertIn("--model", calls["cmd"])
        self.assertEqual(calls["cmd"][-1], "-")            # プロンプトは stdin（"-"）
        self.assertEqual(calls["input"], "プロンプト")
        i = calls["cmd"].index("--output-last-message")
        self.assertFalse(os.path.exists(calls["cmd"][i + 1]))  # 一時ファイルは掃除される

    def test_run_agent_cli_codex_falls_back_to_stdout(self):
        calls, fake = self._capture_run()                  # ファイルへ何も書かない
        with mock.patch.object(km, "_AGENT_CLI", "codex"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake):
            out = km._run_agent_cli("プロンプト", None)
        self.assertEqual(out, "ok")                        # stdout へフォールバック

    def test_build_config_sets_agent_globals_and_fields(self):
        orig = (km._AGENT_CLI, km._AGENT_TIMEOUT)
        try:
            ns = self._resolve(None, agent_cli="claude", agent_timeout=42.0)
            cfg = km.build_config(ns)
            self.assertEqual((cfg.agent_cli, cfg.agent_timeout), ("claude", 42.0))
            self.assertEqual((km._AGENT_CLI, km._AGENT_TIMEOUT), ("claude", 42.0))
        finally:
            km._AGENT_CLI, km._AGENT_TIMEOUT = orig

    def test_relative_overrides_resolve_under_root(self):
        # 相対パスの上書きは（cwd や workdir ではなく）root 基準で解決される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            orig = (km._AGENT_CLI, km._AGENT_TIMEOUT)
            try:
                ns = self._resolve(None, root=str(d), workdir="wd", backlog="bl", bus="b")
                cfg = km.build_config(ns)
            finally:
                km._AGENT_CLI, km._AGENT_TIMEOUT = orig
            self.assertEqual(cfg.workdir, (d / "wd").resolve())
            self.assertEqual(cfg.backlog, d.resolve() / "bl")
            self.assertEqual(cfg.bus, d.resolve() / "b")

    def test_granularity_default_coarse_and_directives(self):
        ns = self._resolve(None)
        self.assertEqual(ns.granularity, "coarse")
        self.assertIn("ストーリー", km.plan_granularity_directive(None))
        self.assertIn("3〜10", km.plan_granularity_directive("coarse"))
        self.assertIn("最小単位", km.plan_granularity_directive("finest"))
        # 未知値は coarse（既定）に倒す
        self.assertEqual(km.plan_granularity_directive("xxl"),
                         km.plan_granularity_directive("coarse"))

    def test_project_config_is_read_from_the_state_root_only(self):
        """プロジェクト設定は状態ルート直下のみ（S1）。旧探索先は読まずに警告する。

        旧実装は cwd → ./.agents → ./.agent → ~/.agents と辿っていたため、移行時に成果物
        リポジトリ側の古い yaml が黙って優先される事故が起きた。読まないだけだと今度は
        「設定したのに効かない」が無言になるので、見つけた場所を名指しする。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".agent").mkdir()
            (root / ".agent" / "agent-project.yaml").write_text("executor: stub\n", encoding="utf-8")
            (root / "agent-project.json").write_text('{"executor":"stub"}', encoding="utf-8")
            self.assertEqual(Path(km._project_config_path(None, root)).resolve(),
                             (root / "agent-project.json").resolve())
            (root / "agent-project.json").unlink()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._project_config_path(None, root))
            self.assertIn(".agent/agent-project.yaml", err.getvalue())


class TestInstances(unittest.TestCase):
    """稼働インスタンスのレジストリ（外部操作者がフォルダを発見する口）。"""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._prev = os.environ.get("AGENT_PROJECT_HOME")
        os.environ["AGENT_PROJECT_HOME"] = self._home

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AGENT_PROJECT_HOME", None)
        else:
            os.environ["AGENT_PROJECT_HOME"] = self._prev

    def test_watch_lock_is_exclusive_per_project(self):
        # 同一プロジェクトの二重監視は OS の排他で防ぐ（実装計画 W1-9）。以前はレジストリの
        # 心拍鮮度で推定しており、心拍 ttl が長い LLM 実行中に切れる窓で二重起動が通っていた。
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), watch=True)
            first = km.acquire_watch_lock(cfg)
            self.assertIsNotNone(first)
            self.addCleanup(km.release_watch_lock, first)
            self.assertIsNone(km.acquire_watch_lock(cfg), "2 本目が同じ root を掴めてしまった")
            km.release_watch_lock(first)
            again = km.acquire_watch_lock(cfg)      # 解放後は取れる
            self.assertIsNotNone(again)
            km.release_watch_lock(again)

    def test_watch_lock_key_ignores_path_spelling(self):
        # --root の綴り差（末尾スラッシュ・相対・symlink）で別ロックになると二重監視が通る。
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "proj"
            (root / "backlog").mkdir(parents=True)
            a = km.watch_lock_path(cfg_for(root, watch=True))
            link = Path(d) / "link"
            os.symlink(root, link)
            b = km.watch_lock_path(cfg_for(link, watch=True))
            self.assertEqual(a, b)

    def test_watch_lock_is_per_project_not_global(self):
        # 別プロジェクトは互いに邪魔しない（PC で 1 本しか監視できないと困る）。
        with tempfile.TemporaryDirectory() as d:
            one = km.acquire_watch_lock(cfg_for(Path(d) / "p1", watch=True))
            two = km.acquire_watch_lock(cfg_for(Path(d) / "p2", watch=True))
            self.addCleanup(km.release_watch_lock, one)
            self.addCleanup(km.release_watch_lock, two)
            self.assertIsNotNone(one)
            self.assertIsNotNone(two)

    def test_startup_reaps_the_previous_generation_of_agent_flow(self):
        """起動時に、自分の bus を回している agent-flow（前世代の残骸）を刈る。

        agent-project がクラッシュ（kill -9 / 電源断）すると stop を通らないので agent-flow が残る。
        残った orchestrator はリースを更新し続けるので、次の agent-project はその run を「実行中」と
        読み、続きから再開せず **新しい run を作り直す** → 同じタスクを二重実行し、同じ作業ブランチへ
        両方が push しあう（17/23 の run を捨てて 1/20 からやり直した）。
        重複起動は別途弾いているので、自分の bus を回している agent-flow は残骸だけと断定できる。"""
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            # 残骸 run: リースが未来を指したまま（＝そのままなら「実行中」と読まれる）
            p = cfg.bus / "runs" / "run-old"
            p.mkdir(parents=True)
            (p / "meta.json").write_text(json.dumps({
                "status": "running", "orch_lease_until": time.time() + 600}), encoding="utf-8")

            killed = []
            with mock.patch.object(km, "_flow_pids_for_bus", return_value=[4242, 4243]), \
                 mock.patch.object(km.os, "kill", side_effect=lambda pid, sig: killed.append(pid)), \
                 mock.patch.object(km, "_pid_alive", return_value=False):
                n = km.reap_orphan_flow(cfg)

            self.assertEqual(n, 2, "残骸プロセスを止める")
            self.assertEqual(sorted(set(killed)), [4242, 4243])
            meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["orch_lease_until"], 0.0, "リースを失効させる")

            # 失効した run は「停滞」＝続きから再開できる（成果が捨てられない）
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "run-old"))
            self.assertEqual(km.run_id_for(cfg, t), "run-old", "続きから再開する")

    def test_reap_leaves_other_projects_alone(self):
        # 別プロジェクトの bus を回している agent-flow は自分の残骸ではない（触らない）
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            ps_out = (f"111 python /x/agent-flow --bus /other/project/bus work\n"
                      f"222 python /x/agent-flow --bus {cfg.bus.resolve()} work\n"
                      f"333 python /x/unrelated --bus {cfg.bus.resolve()}\n")
            fake = types.SimpleNamespace(returncode=0, stdout=ps_out)
            with mock.patch.object(km.subprocess, "run", return_value=fake):
                pids = km._flow_pids_for_bus(cfg.bus)
            self.assertEqual(pids, [222], "自分の bus の agent-flow だけを対象にする")

    def test_reap_covers_every_flow_process_on_the_bus(self):
        """自分の bus を回している agent-flow は種類を問わず前世代の残骸として刈る
        （同じ bus を回す agent-project は 1 つだけなので、他人のものは混ざらない）。"""
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            bus = str(cfg.bus.resolve())
            ps_out = (
                f"222 python /x/agent-flow --bus {bus} run --run-id R1\n"
                f"333 python /x/agent-flow --bus {bus} orchestrate --run-id R1\n"
                f"444 python /x/agent-flow --bus {bus} work --run-id R1\n"
            )
            fake = types.SimpleNamespace(returncode=0, stdout=ps_out)
            with mock.patch.object(km.subprocess, "run", return_value=fake):
                self.assertEqual(sorted(km._flow_pids_for_bus(cfg.bus)), [222, 333, 444])

            killed = []
            with mock.patch.object(km, "_flow_pids_for_bus", return_value=[222, 333, 444]), \
                 mock.patch.object(km.os, "kill", side_effect=lambda pid, sig: killed.append(pid)), \
                 mock.patch.object(km, "_pid_alive", return_value=False):
                n = km.reap_orphan_flow(cfg)
            self.assertEqual(n, 3)
            self.assertEqual(sorted(set(killed)), [222, 333, 444])

    def test_watch_refuses_a_duplicate_of_the_same_project(self):
        """同じプロジェクトを 2 つのループに監視させない。

        2 つ走ると同じ backlog を奪い合い、同じタスクを二重実行して状態ファイルと
        決定記録を互いに上書きする。先客は**監視ロックの保持**で表す（実装計画 W1-9 —
        以前はレジストリの心拍鮮度による推定で、窓の隙間で二重起動が通っていた）。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            cfg = cfg_for(root, watch=True)
            cfg.source_root = root
            held = km.acquire_watch_lock(cfg)           # 先客がロックを保持している
            self.assertIsNotNone(held)
            self.addCleanup(km.release_watch_lock, held)
            self.assertEqual(km.cmd_run(cfg), 1, "重複起動は拒否する")

    def test_watch_duplicate_is_allowed_with_force(self):
        # 人が明示的に許可したなら通す（--force）
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            cfg = cfg_for(root, watch=True)
            cfg.source_root = root
            held = km.acquire_watch_lock(cfg)
            self.assertIsNotNone(held)
            self.addCleanup(km.release_watch_lock, held)
            cfg.force = True
            with mock.patch.object(km, "ensure_dirs", side_effect=RuntimeError("進んだ")):
                with self.assertRaises(RuntimeError):   # 重複チェックを抜けて先へ進む
                    km.cmd_run(cfg)


class TestConfigFile(unittest.TestCase):
    """設定ファイル（YAML 任意 / JSON フォールバック、CLI > config > 既定）。"""

    @staticmethod
    def _resolve(cfg_path=None, **cli):
        # CLI 未指定キーは None（getattr の既定）。明示したいキーだけ cli に渡す。
        ns = types.SimpleNamespace(config=cfg_path, **cli)
        km.resolve_config(ns)
        return ns

    def test_verify_timeout_default_survives_a_full_test_suite(self):
        """verify の既定タイムアウトは、テストスイート全体を回す完了条件に耐えること。

        「テストスイート全体を green にする」類の verify は数分かかる（このリポジトリは 990 件で
        130 秒）。既定 120 秒では **完了しているのに時間切れで NG** と判定され、リトライを積み
        上げた末に人へエスカレーションしていた（retries=6 まで無駄に積み直して blocked）。
        act_timeout より十分短く保ち、ハングの保護は残す。"""
        defaults = {f.name: f.default for f in dataclasses.fields(km.Config)}
        self.assertGreaterEqual(defaults["verify_timeout"], 600.0,
                                "フルスイートを回せる長さがあること")
        self.assertGreaterEqual(km.CONFIG_DEFAULTS["verify_timeout"], 600.0,
                                "設定ファイルの既定も揃っていること")
        self.assertLess(defaults["verify_timeout"], defaults["act_timeout"],
                        "act より短く（ハングの保護を残す）")

    def test_json_config_fills_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agent-project.json"
            p.write_text('{"executor":"stub","planner":"none","poll":9,"max_cycles":3}',
                         encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual((ns.executor, ns.planner, ns.poll, ns.max_cycles),
                             ("stub", "none", 9, 3))

    def test_cli_overrides_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agent-project.json"
            p.write_text('{"executor":"stub","planner":"none"}', encoding="utf-8")
            ns = self._resolve(str(p), executor="agent")   # CLI 明示は維持される
            self.assertEqual(ns.executor, "agent")         # CLI 勝ち
            self.assertEqual(ns.planner, "none")           # config 採用

    def test_host_yaml_supplies_node_and_root_without_environment(self):
        """host.yaml が「このノードの宣言」の単一ソース（旧 profile の置き換え・S1）。

        node_id は環境変数 AGENT_PROJECT_NODE より優先し（宣言が正）、root は projects[] から
        取る。プロジェクト yaml 側の合意事項（default_node）はそのまま効く。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            state = mk_state_repo(d / "state")
            (state / "agent-project.json").write_text('{"default_node":"pc-b"}', encoding="utf-8")
            use_host_config(self, {
                "node_id": "pc-a",
                "projects": [{"name": "demo", "root": str(state)}],
            }, home=d / "agents-home")
            with mock.patch.dict(os.environ, {"AGENT_PROJECT_NODE": "wrong-env"}):
                ns = self._resolve(None, project="demo")
                cfg = km.build_config(ns)
            self.assertEqual(cfg.node, "pc-a")
            self.assertEqual(cfg.backlog.parent, state.resolve())
            self.assertEqual(cfg.default_node, "pc-b")

    def test_unknown_project_name_stops(self):
        with tempfile.TemporaryDirectory() as d:
            use_host_config(self, {"projects": [{"name": "demo", "root": d}]},
                            home=Path(d) / "agents-home")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                self._resolve(None, project="typo")
            self.assertIn("demo", err.getvalue())        # 宣言済みの名前を示して直せるようにする

    def test_layer_precedence_cli_over_overrides_over_defaults_over_project(self):
        """CLI > projects[].overrides > defaults > プロジェクト yaml > 組み込み既定（S1）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            state = mk_state_repo(d / "state")
            (state / "agent-project.json").write_text(
                '{"agent_cli":"kiro","model":"from-project","location":"local"}', encoding="utf-8")
            use_host_config(self, {
                "defaults": {"agent_cli": "codex", "model": "from-defaults"},
                "projects": [{"name": "demo", "root": str(state),
                              "overrides": {"model": "from-overrides"}}],
            }, home=d / "agents-home")
            ns = self._resolve(None, project="demo", verify_timeout=99.0)
            self.assertEqual(ns.model, "from-overrides")   # overrides が defaults に勝つ
            self.assertEqual(ns.agent_cli, "codex")        # defaults がプロジェクト yaml に勝つ
            self.assertEqual(ns.location, "local")         # 上書きが無ければプロジェクト yaml
            self.assertEqual(ns.verify_timeout, 99.0)      # CLI が最優先
            self.assertEqual(ns.max_retries, km.CONFIG_DEFAULTS["max_retries"])

    def test_host_only_key_in_project_yaml_stops(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            state = mk_state_repo(d / "state")
            (state / "agent-project.json").write_text(
                '{"node_id":"pc-x","update_repo":"https://x/y.git"}', encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                self._resolve(None, root=str(state))
            msg = err.getvalue()
            self.assertIn("node_id", msg)
            self.assertIn("update_repo", msg)
            self.assertIn("host.yaml", msg)

    def test_project_only_key_in_host_overrides_stops(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            state = mk_state_repo(d / "state")
            use_host_config(self, {
                "projects": [{"name": "demo", "root": str(state),
                              "overrides": {"max_retries": 9}}],
            }, home=d / "agents-home")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                self._resolve(None, project="demo")
            self.assertIn("max_retries", err.getvalue())

    def test_removed_worktree_keys_stop_with_migration_hint(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            state = mk_state_repo(d / "state")
            (state / "agent-project.json").write_text(
                '{"state_backup_branch":"main","state_push":true}', encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                self._resolve(None, root=str(state))
            msg = err.getvalue()
            self.assertIn("state_backup_branch", msg)
            self.assertIn("migrate-state-repo.sh", msg)

    def test_inert_and_unknown_project_keys_only_warn(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            state = mk_state_repo(d / "state")
            (state / "agent-project.json").write_text(
                '{"root":".","state_repo":"https://x/y.git","planer":"none","executor":"stub"}',
                encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ns = self._resolve(None, root=str(state))
            msg = err.getvalue()
            self.assertIn("root", msg)
            self.assertIn("planer", msg)                   # 綴り違いを見逃さない
            self.assertEqual(ns.executor, "stub")          # 正しいキーは効いたまま
            self.assertEqual(ns.state_repo, "")            # 無効なので採用しない

    def test_removed_flags_explain_the_migration(self):
        for kwargs, needle in (({"_removed_profile": "x"}, "--profile"),
                               ({"_removed_state_repo_dir": "x"}, "--state-repo-dir")):
            with tempfile.TemporaryDirectory() as d:
                err = io.StringIO()
                with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                    self._resolve(None, root=d, **kwargs)
                self.assertIn(needle, err.getvalue())

    def test_key_classification_partitions_config_defaults(self):
        """CONFIG_DEFAULTS の全キーがちょうど 1 つの分類に属する（契約の CI 固定）。

        分類漏れがあると、そのキーは黙ってプロジェクト共有側に落ちる。差集合で定義した
        PROJECT_ONLY_KEYS がその安全側の受け皿なので、ここでは **重複が無いこと** と
        **分類の語彙が CONFIG_DEFAULTS に実在すること** を固定する。"""
        groups = {"SHARED": km.SHARED_KEYS, "HOST_SOURCED": km.HOST_SOURCED_KEYS,
                  "PROJECT_ONLY": km.PROJECT_ONLY_KEYS}
        seen = {}
        for name, keys in groups.items():
            for k in keys:
                self.assertNotIn(k, seen, f"{k} が {seen.get(k)} と {name} に重複")
                seen[k] = name
        self.assertEqual(set(seen) | set(km._INERT_PROJECT_KEYS), set(km.CONFIG_DEFAULTS)
                         | set(km._INERT_PROJECT_KEYS))
        for k in km._REMOVED_WORKTREE_KEYS:
            self.assertNotIn(k, km.CONFIG_DEFAULTS, "廃止キーは既定表に残さない")

    def test_bus_config_is_honored(self):
        # 設定ファイルの bus: が読まれ、明示バス（絶対パス）として使われること。
        # これが読まれないと既定バスに落ち、agent-flow が別のバスを見てしまう。
        with tempfile.TemporaryDirectory() as d:
            shared = str(Path(d) / "shared-bus")
            p = Path(d) / "agent-project.json"
            p.write_text(json.dumps({"bus": shared}), encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual(ns.bus, shared)               # config の bus が args に載る
            cfg = km.build_config(ns)
            self.assertEqual(str(cfg.bus), shared)          # 実際に使うバス = 明示バス

    def test_bus_absent_defaults_under_root(self):
        # bus 未指定は <root>/bus。
        ns = self._resolve(None)
        self.assertIsNone(ns.bus)
        cfg = km.build_config(ns)
        self.assertEqual(cfg.bus.name, "bus")

    def test_builtin_defaults_when_no_config(self):
        ns = self._resolve(None)
        self.assertEqual((ns.executor, ns.planner, ns.poll, ns.max_cycles, ns.location),
                         ("agent", "agent", 5.0, 20, "auto"))
        self.assertEqual((ns.auto_adjudicate, ns.adjudicate_max), (True, 1))  # 既定 on

    def test_yaml_config_when_pyyaml_available(self):
        if km.yaml is None:
            self.skipTest("PyYAML 未導入（JSON 経路は別テストで担保）")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agent-project.yaml"
            p.write_text("executor: stub\nmax_retries: 5\ngit_branch: develop\n", encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual((ns.executor, ns.max_retries, ns.git_branch),
                             ("stub", 5, "develop"))

    def test_missing_explicit_config_exits(self):
        with self.assertRaises(SystemExit):
            self._resolve("/no/such/agent-project.yaml")

    def test_boolean_flags_from_config(self):
        # 真偽フラグ（watch/do_archive/learn/rot/cleanup/once/dry_run/ltm/regression_revert）が
        # 設定ファイルで効く。resolve_config は CLI 未指定（None）のみ config→既定 で埋める。
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agent-project.json"
            p.write_text('{"watch":true,"do_archive":false,"learn":false,"rot":true}',
                         encoding="utf-8")
            ns = self._resolve(str(p), watch=None, do_archive=None, learn=None, rot=None,
                               once=None, dry_run=None, cleanup=None, ltm=None,
                               regression_revert=None)
            self.assertEqual((ns.watch, ns.do_archive, ns.learn, ns.rot),
                             (True, False, False, True))
            self.assertEqual((ns.cleanup, ns.once, ns.dry_run, ns.ltm), (True, False, False, False))

    def test_cli_overrides_boolean_config(self):
        # CLI 明示（--no-watch / --learn 等で None でない値）が config に勝つ。
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agent-project.json"
            p.write_text('{"watch":true,"learn":false}', encoding="utf-8")
            ns = self._resolve(str(p), watch=False, learn=True)
            self.assertEqual((ns.watch, ns.learn), (False, True))     # CLI 勝ち

    def test_boolean_defaults_when_no_config(self):
        ns = self._resolve(None, watch=None, do_archive=None, learn=None, cleanup=None,
                           rot=None, once=None, dry_run=None, ltm=None, regression_revert=None)
        self.assertEqual((ns.watch, ns.do_archive, ns.learn, ns.cleanup),
                         (False, True, True, True))                    # 組み込み既定


class TestGenericHookConfig(unittest.TestCase):
    """build_config は provider 固有の配線を持たず、汎用フック設定をそのまま渡す。"""

    @staticmethod
    def _build(root, **cli):
        ns = types.SimpleNamespace(root=str(root), config=None, **cli)
        km.resolve_config(ns)
        return km.build_config(ns)

    def test_explicit_hooks_pass_through(self):
        with tempfile.TemporaryDirectory() as d:
            expected = {
                "wiring": "project_wiring",
                "wiring.detect": "custom_detector",
            }
            path = Path(d) / "agent-project.json"
            path.write_text(json.dumps({"root": d, "hooks": expected}), encoding="utf-8")
            ns = types.SimpleNamespace(config=str(path))
            km.resolve_config(ns)
            cfg = km.build_config(ns)
        self.assertEqual(cfg.hooks, expected)

    def test_repos_json_present_does_not_auto_wire(self):
        # repos.json があっても build_config は regression_cmd/intake_cmd を補わない。
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "repos.json").write_text(
                json.dumps({"app": {"url": "git@h:t/a.git"}}), encoding="utf-8")
            cfg = self._build(d)
        self.assertIsNone(cfg.regression_cmd)
        self.assertIsNone(cfg.intake_cmd)

    def test_no_repos_json_leaves_commands_unset(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._build(d)
        self.assertIsNone(cfg.regression_cmd)
        self.assertIsNone(cfg.intake_cmd)

    def test_explicit_commands_pass_through_unchanged(self):
        # 明示設定は build_config を素通りする（自動検出による上書きも補完も起きない）。
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "repos.json").write_text(
                json.dumps({"app": {"url": "git@h:t/a.git"}}), encoding="utf-8")
            cfg = self._build(d, regression_cmd="echo a", intake_cmd="echo b")
        self.assertEqual((cfg.regression_cmd, cfg.intake_cmd), ("echo a", "echo b"))


class TestAgentPlugin(unittest.TestCase):
    """エージェント CLI プラグイン（データ契約: schemas/agent-cli.schema.json）。
    組み込み以外の CLI を agents/<name>.json だけで差し込み、未知の agent_cli は
    黙って kiro-cli に落とさず明示エラーにする。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ka-agents-"))
        self._old = os.environ.get("KIRO_AGENTS_DIR")
        os.environ["KIRO_AGENTS_DIR"] = str(self.tmp)
        km._AGENT_PLUGIN_CACHE.clear()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KIRO_AGENTS_DIR", None)
        else:
            os.environ["KIRO_AGENTS_DIR"] = self._old
        km._AGENT_PLUGIN_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, spec):
        (self.tmp / f"{name}.json").write_text(json.dumps(spec), encoding="utf-8")

    def test_plugin_builds_command_with_model_placeholder(self):
        self._write("myllm", {"command": ["my-cli", "run", "{model}"],
                              "default_model": "base-7b"})
        cmd, stdin_text, out_file = km._agent_cmd("myllm", None, "こんにちは")
        self.assertEqual(cmd, ["my-cli", "run", "base-7b"])   # default_model が {model} に入る
        self.assertEqual(stdin_text, "こんにちは")               # 既定は stdin 渡し
        self.assertIsNone(out_file)
        cmd, _, _ = km._agent_cmd("myllm", "big-70b", "x")
        self.assertEqual(cmd, ["my-cli", "run", "big-70b"])   # 明示モデルが勝つ

    def test_plugin_argv_prompt_and_model_flag(self):
        self._write("argvcli", {"command": ["a-cli"], "prompt_via": "argv",
                                "prompt_flag": "-p", "model_flag": "--model"})
        cmd, stdin_text, _ = km._agent_cmd("argvcli", "m1", "prompt text")
        self.assertEqual(cmd, ["a-cli", "--model", "m1", "-p", "prompt text"])
        self.assertIsNone(stdin_text)

    def test_plugin_output_file_placeholder(self):
        self._write("filecli", {"command": ["f-cli", "--out", "{output_file}"],
                                "output": "file"})
        cmd, _, out_file = km._agent_cmd("filecli", None, "x")
        self.assertIsNotNone(out_file)
        self.assertIn(out_file, cmd)
        os.remove(out_file)

    def test_unknown_agent_cli_is_explicit_error(self):
        # 以前は未知の agent_cli が黙って kiro-cli に落ちていた（設定ミスに気づけない罠）
        with self.assertRaises(RuntimeError) as cm:
            km._agent_cmd("nosuchcli", None, "x")
        self.assertIn("agents/nosuchcli.json", str(cm.exception))

    def test_builtin_definition_can_be_overridden(self):
        # S9: 組み込み名の予約を解除した。「CLI の作法変更が JSON 1 ファイルで完結する」ため
        # には、同梱定義を上位ディレクトリで差し替えられる必要がある（first-wins）。
        cmd, _, _ = km._agent_cmd("claude", None, "x")
        self.assertEqual(cmd[0], "claude")             # 同梱定義（agents/claude.json）
        km._AGENT_PLUGIN_CACHE.clear()
        self._write("claude", {"command": ["my-claude"]})
        cmd, _, _ = km._agent_cmd("claude", None, "x")
        self.assertEqual(cmd[0], "my-claude")          # 上位の定義が勝つ

    def test_builtin_headless_argv_matches_pre_migration(self):
        """組み込み 4 CLI のヘッドレス argv が S9 移行前と一致する（回帰）。"""
        km._AGENT_PLUGIN_CACHE.clear()
        os.environ.pop("KIRO_AGENTS_DIR", None)        # 同梱定義を見る
        try:
            cmd, stdin_text, _ = km._agent_cmd("kiro", "m", "P")
            self.assertEqual(cmd, ["kiro-cli", "chat", "--no-interactive",
                                   "--trust-all-tools", "--model", "m", "P"])
            self.assertIsNone(stdin_text)
            cmd, stdin_text, _ = km._agent_cmd("claude", "m", "P")
            self.assertEqual(cmd, ["claude", "-p", "--output-format", "text",
                                   "--dangerously-skip-permissions", "--model", "m"])
            self.assertEqual(stdin_text, "P")
            cmd, _, _ = km._agent_cmd("copilot", "m", "P")
            self.assertEqual(cmd, ["copilot", "-s", "--allow-all-tools", "--no-color",
                                   "--allow-all-paths", "--model", "m", "-p", "P"])
            cmd, stdin_text, out_file = km._agent_cmd("codex", "m", "P")
            self.assertEqual(stdin_text, "P")
            self.assertIsNotNone(out_file)
            self.assertEqual(cmd[-1], "-")             # 位置引数は末尾のまま
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", cmd)
            os.remove(out_file)
        finally:
            os.environ["KIRO_AGENTS_DIR"] = str(self.tmp)
            km._AGENT_PLUGIN_CACHE.clear()

    def test_agent_cli_binary_comes_from_definition(self):
        """doctor が PATH 確認する実行ファイル名も定義ファイル由来（対応表の二重管理を消した）。"""
        km._AGENT_PLUGIN_CACHE.clear()
        os.environ.pop("KIRO_AGENTS_DIR", None)
        try:
            self.assertEqual(km.agent_cli_binary("kiro"), "kiro-cli")
            self.assertEqual(km.agent_cli_binary("codex"), "codex")
            self.assertEqual(km.agent_cli_binary("nosuchcli"), "nosuchcli")
        finally:
            os.environ["KIRO_AGENTS_DIR"] = str(self.tmp)
            km._AGENT_PLUGIN_CACHE.clear()

    def test_broken_plugin_is_loud(self):
        (self.tmp / "broken.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            km.load_agent_plugin("broken")

    def test_plugin_error_patterns_feed_triage(self):
        self._write("myllm", {"command": ["my-cli"], "errors": [
            {"match": "GPU memory exhausted", "class": "env", "hint": "モデルが大きすぎます"}]})
        km.load_agent_plugin("myllm")
        cls, hint = km.classify_agent_failure("boom: GPU memory exhausted")
        self.assertEqual(cls, "env")
        self.assertIn("モデルが大きすぎます", hint)


class AgentPromptSpillTests(unittest.TestCase):
    """argv 長制限の退避（P1-2）。

    agent-project だけ退避が無く、プロンプトが `ARG_MAX` を超えると `execve` が E2BIG で
    落ちていた（verifier は全基準 unverifiable、plan は空振りで人へ倒れる）。S5/S6 で
    verifier 入力（repo 文脈 + rules + レシピ + feedback）と planner 入力（charter 全文 +
    既存タスク + 墓標）が肥大したので、既定 CLI の kiro（argv 渡し）で現実に当たる。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="spill-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old_limit = km._ARGV_LIMIT
        self.addCleanup(setattr, km, "_ARGV_LIMIT", self._old_limit)
        km._ARGV_LIMIT = 200
        km._AGENT_PLUGIN_CACHE.clear()
        self.addCleanup(km._AGENT_PLUGIN_CACHE.clear)
        self._old_dir = os.environ.pop("KIRO_AGENTS_DIR", None)
        self.addCleanup(lambda: os.environ.__setitem__("KIRO_AGENTS_DIR", self._old_dir)
                        if self._old_dir is not None else None)

    def _run(self, cli: str, prompt: str) -> dict:
        """`_run_agent_cli` を 1 回通し、実際に渡された argv / stdin を捕まえる。"""
        seen: dict = {}

        def fake_run(cmd, **kw):
            seen["argv"], seen["stdin"] = list(cmd), kw.get("input")
            # 退避先は CLI が読む時点で実在している必要がある（掃除は実行後）。
            m = re.search(r"\S*agent-project-prompt-\S*\.txt",
                          " ".join(str(t) for t in cmd) + " " + str(kw.get("input") or ""))
            if m:
                seen["spill"] = m.group(0)
                seen["body"] = Path(m.group(0)).read_text(encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "OK", "")

        with mock.patch.object(km, "_AGENT_CLI", cli), \
                mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            seen["text"] = km._run_agent_cli(prompt, None)
        return seen

    def test_large_prompt_spills_for_argv_cli(self):
        big = "x" * 500
        seen = self._run("kiro", big)
        self.assertNotIn(big, " ".join(seen["argv"]), "本文が argv に載ったまま")
        spill = seen.get("spill")
        self.assertTrue(spill, "退避先が argv に載っていない")
        self.assertEqual(seen["body"], big, "退避先に本文が全部ある")
        self.assertFalse(os.path.exists(spill), "退避先が実行後に残っている")

    def test_spill_keeps_the_write_permission_flags(self):
        """退避しても権限フラグを落とさない（P1-2 の要）。

        定義側の `spill`（`headless_cmd(spill_path=…)`）は退避時に権限フラグを
        `--trust-tools=fs_read` へ**置き換える**。読み取り専用の診断向けの機構なので、
        コマンドを実行して確かめる検証エージェントに掛けると 1 つも実行できなくなる。"""
        seen = self._run("kiro", "y" * 500)
        self.assertIn("--trust-all-tools", seen["argv"])
        self.assertNotIn("--trust-tools=fs_read", seen["argv"])

    def test_stdin_cli_is_not_spilled(self):
        # stdin 渡しは ARG_MAX に当たらない（退避すると本文を無駄に往復させるだけ）。
        big = "z" * 500
        seen = self._run("claude", big)
        self.assertEqual(seen["stdin"], big)

    def test_short_prompt_is_passed_through(self):
        seen = self._run("kiro", "みじかい")
        self.assertIn("みじかい", seen["argv"])

    def test_argv_limit_falls_back_when_unset(self):
        km._ARGV_LIMIT = 0
        self.assertEqual(km._agent_argv_limit(), km._agentcli.DEFAULT_ARGV_LIMIT)

    def test_e2big_is_classified_as_env(self):
        """退避の閾値より OS の上限が小さい環境では E2BIG が残る。内容の問題として扱うと
        タスクのリトライ予算を焼くので env（人が環境を直す）に倒す。"""
        cls, _hint = km.classify_agent_failure(
            "[Errno 7] Argument list too long: 'kiro-cli'")
        self.assertEqual(cls, "env")


class TestAgentHome(unittest.TestCase):
    """エージェント共通ホームの解決（`.agent` → `.agents` 改名の後方互換）。

    判定はサブディレクトリ単位。ホーム単位で見ると `.agents/skills` だけ先に作られた環境で
    「新ホームは在る」と誤判断し、まだ移していない `.agent/control` を見失う。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        patcher = mock.patch.object(km.Path, "home", staticmethod(lambda: self.home))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_to_new_home(self):
        self.assertEqual(km.agent_home_subdir("", "control"), self.home / ".agents" / "control")

    def test_falls_back_to_legacy_when_only_legacy_exists(self):
        (self.home / ".agent" / "control").mkdir(parents=True)
        self.assertEqual(km.agent_home_subdir("", "control"), self.home / ".agent" / "control")

    def test_partial_migration_keeps_unmigrated_items_on_legacy(self):
        # skills だけ先に新ホームへ入った状態（実際にこうなっていた）
        (self.home / ".agent" / "control").mkdir(parents=True)
        (self.home / ".agents" / "skills").mkdir(parents=True)
        self.assertEqual(km.agent_home_subdir("", "skills"), self.home / ".agents" / "skills")
        self.assertEqual(km.agent_home_subdir("", "control"), self.home / ".agent" / "control",
                         "ホーム単位で判定すると、ここで control を見失う")

    def test_uses_new_home_after_migration(self):
        (self.home / ".agent" / "control").mkdir(parents=True)
        (self.home / ".agents" / "control").mkdir(parents=True)
        self.assertEqual(km.agent_home_subdir("", "control"), self.home / ".agents" / "control")

    def test_env_override_wins(self):
        (self.home / ".agent" / "control").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"AGENT_CONTROL_DIR": "/tmp/explicit"}):
            self.assertEqual(km.agent_home_subdir("AGENT_CONTROL_DIR", "control"),
                             Path("/tmp/explicit"))
