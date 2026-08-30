"""agent-project の単体テスト — resident（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class AgentOverrideTests(unittest.TestCase):
    """処理（purpose）毎のエージェント上書き（設定 agents:・yaml 専用）。
    plan/review/prioritize/route/adjudicate/verify/distill/assess/repo_map/doctor の
    各処理でエージェント CLI とモデルを個別に選べることを検証する。"""

    def setUp(self):
        self._runtime = km._RUNTIME_CONFIG
        km._RUNTIME_CONFIG = types.SimpleNamespace(
            agent_cli="kiro", agent_timeout=300.0, agents={}, argv_limit=0)

    def tearDown(self):
        km._RUNTIME_CONFIG = self._runtime

    def test_normalize_accepts_known_purposes_only(self):
        raw = {"plan": {"agent_cli": "Claude", "model": "opus"},
               "assess": {"model": "haiku"},
               "unknown": {"agent_cli": "x"},       # 未知キーは落とす
               "verify": "not-a-dict",              # 不正な値も落とす
               "doctor": {}}                         # 空も落とす
        out = km._normalize_agent_overrides(raw)
        self.assertEqual(set(out), {"plan", "assess"})
        self.assertEqual(out["plan"], {"agent_cli": "claude", "model": "opus"})
        self.assertEqual(km._normalize_agent_overrides(None), {})

    def test_agent_for_falls_back_to_global(self):
        km._RUNTIME_CONFIG.agents = {
            "plan": {"agent_cli": "claude", "model": "opus"},
            "assess": {"model": "haiku"}}
        self.assertEqual(km._agent_for("plan"), ("claude", "opus"))
        self.assertEqual(km._agent_for("assess"), ("kiro", "haiku"))  # model だけ上書き
        self.assertEqual(km._agent_for("verify"), ("kiro", None))     # 未指定 → グローバル
        self.assertEqual(km._agent_for(""), ("kiro", None))

    def test_configured_model_survives_the_variant_default(self):
        """人が設定に書いたモデルは、変種の既定で上書きしない（設計 2026-08-27 G4）。

        以前はここが無条件に上書きしていて、`agents.plan.model` を書いても
        `variants.plan → ollama-json` の既定へ黙って戻っていた。起動形（argv）は変種の
        ものを使い、モデルだけは人の明示を残す——agent-flow が既に持っていた規則。
        """
        km._RUNTIME_CONFIG.agents = {"plan": {"agent_cli": "ollama", "model": "qwen3:8b"},
                                     "review": {"agent_cli": "ollama"}}
        self.assertEqual(km._agent_for("plan"), ("ollama-json", "qwen3:8b"))
        # 明示が無ければ従来どおり変種の用途専用チューニングへ寄せる。
        self.assertEqual(km._agent_for("review"), ("ollama-json", "gemma4:e4b"))

    def test_variant_routing_has_no_engine_allow_list(self):
        """振り替えの可否は定義側の申告だけが決める（設計 2026-08-27 §3.3 / G2）。

        以前は JSON_CONTRACT_PURPOSES という許可リストがここにあり、`ollama.json` が
        宣言していても引かない用途があった。申告が無い用途は元のままなので、許可リストを
        消しても「宣言していないのに振り替わる」は起きない。
        """
        km._RUNTIME_CONFIG.agents = {"doctor": {"agent_cli": "ollama"},
                                     "repo_map": {"agent_cli": "ollama"}}
        self.assertEqual(km._agent_for("doctor")[0], "ollama")
        self.assertEqual(km._agent_for("repo_map")[0], "ollama")

    def test_readonly_is_declared_per_purpose(self):
        """権限は役割の性質で決まる。既定は現状のまま write（黙って挙動を変えない）。"""
        km._RUNTIME_CONFIG.agents = km._normalize_agent_overrides({
            "adjudicate": {"agent_cli": "ollama", "readonly": True},
            "plan": {"readonly": "yes"},            # bool 以外は落とす
            "repo_map": {"agent_cli": "kiro"}})
        self.assertTrue(km._agent_readonly("adjudicate"))
        self.assertFalse(km._agent_readonly("plan"))
        self.assertFalse(km._agent_readonly("repo_map"))
        self.assertFalse(km._agent_readonly("verify"))   # 未指定 → 既定の write

    def test_readonly_purpose_drops_the_write_args(self):
        """受け入れ基準: readonly 宣言した purpose の argv に write_args が乗らない。"""
        write = km._agent_cmd("claude", None, "P")[0]
        readonly = km._agent_cmd("claude", None, "P", readonly=True)[0]
        self.assertIn("--dangerously-skip-permissions", write)
        self.assertNotIn("--dangerously-skip-permissions", readonly)

    def test_agent_cmd_builds_per_cli(self):
        cmd, stdin, out_file = km._agent_cmd("claude", "opus", "P")
        self.assertEqual(cmd[0], "claude")
        self.assertIn("opus", cmd)
        self.assertEqual(stdin, "P")                              # claude は stdin 渡し
        self.assertIsNone(out_file)
        cmd, stdin, out_file = km._agent_cmd("copilot", None, "P")
        self.assertEqual(cmd[0], "copilot")
        self.assertEqual(cmd[-2:], ["-p", "P"])
        self.assertIsNone(stdin)
        self.assertIsNone(out_file)
        cmd, stdin, out_file = km._agent_cmd("kiro", "m", "P")
        self.assertEqual(cmd[0], "kiro-cli")
        self.assertEqual(cmd[-1], "P")
        self.assertIsNone(out_file)
        cmd, stdin, out_file = km._agent_cmd("codex", "m", "P")
        try:
            self.assertEqual(cmd[:2], ["codex", "exec"])
            self.assertEqual(cmd[-1], "-")                        # プロンプトは stdin（"-"）
            self.assertEqual(stdin, "P")
            self.assertIn("--output-last-message", cmd)           # 最終応答はファイル経由
            self.assertTrue(out_file and os.path.exists(out_file))
        finally:
            if out_file:
                os.remove(out_file)

    def test_run_agent_cli_uses_purpose_override(self):
        km._RUNTIME_CONFIG.agents = {
            "plan": {"agent_cli": "claude", "model": "opus"}}
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            km._run_agent_cli("x", "global-model", purpose="plan")
            km._run_agent_cli("x", "global-model", purpose="assess")
        self.assertEqual(calls[0][0], "claude")
        self.assertIn("opus", calls[0])                           # 上書き model が勝つ
        self.assertEqual(calls[1][0], "kiro-cli")
        self.assertIn("global-model", calls[1])                   # 未指定はグローバル

    def test_content_failure_retries_once_on_costlier_fallback(self):
        km._RUNTIME_CONFIG.agents = km._normalize_agent_overrides({
            "plan": {"agent_cli": "ollama", "fallbacks": [{"agent_cli": "claude"}]}})
        calls = []

        def once(prompt, model, purpose="", agent=None):
            calls.append(agent)
            if agent is None:
                raise RuntimeError("content failed")
            return "ok"

        with mock.patch.object(km, "_run_agent_cli_once", side_effect=once), \
                mock.patch.object(km, "_node_budget_record"):
            self.assertEqual(km._run_agent_cli("x", None, purpose="plan"), "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["agent_cli"], "claude")

    def test_resolve_config_reads_agents_map(self):
        # yaml（json 互換）から agents: が読まれ、build_config がモジュールへ確定する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "agent-project.json").write_text(json.dumps({
                "agents": {"plan": {"agent_cli": "claude", "model": "opus"},
                           "bogus": {"agent_cli": "x"}}}), encoding="utf-8")
            old_cwd = os.getcwd()
            try:
                os.chdir(d)
                args = types.SimpleNamespace(config=None)
                km.resolve_config(args)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(km._normalize_agent_overrides(args.agents),
                             {"plan": {"agent_cli": "claude", "model": "opus"}})


class NodeBudgetTests(unittest.TestCase):
    """ノード予算（node-budget 契約）の記帳・抑制。共有台帳の合計が上限を超えたら
    _run_agent_cli が [agent-error:quota] タグ付きで即失敗し、既存の環境要因フロー
    （リトライ・裁定を焼かず needs へ）に乗る。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kp-node-budget-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.environ["AGENT_BUDGET_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)

    def _config(self, minutes, period="day", workloads=None):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"execution_minutes": minutes, "period": period,
                       "workloads": workloads or {}}, f)

    def _ledger(self, records):
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        day = time.strftime("%Y%m%d", time.gmtime())
        with open(os.path.join(led, f"{day}.jsonl"), "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

    def test_no_config_is_unlimited(self):
        self.assertIsNone(km._node_budget_state())

    def test_exceeded_raises_quota_before_cli(self):
        self._config(1)
        self._ledger([{"workload": "flow", "seconds": 40},
                      {"workload": "project", "seconds": 30}])  # 他ワークロード合算で超過
        with self.assertRaises(RuntimeError) as ctx:
            km._run_agent_cli("x", None, purpose="prioritize")
        msg = str(ctx.exception)
        self.assertIn("[agent-error:quota]", msg)
        self.assertIn("[node-budget]", msg)
        self.assertEqual(km.classify_agent_failure(msg)[0], "quota")

    def test_record_appends_project_workload(self):
        km._node_budget_record(2.0, ref="adjudicate")
        led = os.path.join(self.dir, "ledger")
        files = [n for n in os.listdir(led) if n.endswith(".jsonl")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(led, files[0]), encoding="utf-8") as f:
            rec = json.loads(f.read().strip())
        self.assertEqual(rec["workload"], "project")
        self.assertEqual(rec["tool"], "agent-project")
        self.assertEqual(rec["seconds"], 2.0)
        self.assertEqual(rec["purpose"], "adjudicate", "仕事種別は ref の言い換えではなく明示する")

    def test_model_escalation_is_bounded_per_process(self):
        """内容系の失敗を上位モデルで拾う回数はプロセス単位で有界（C7: 必ず止まる）。

        ここは分解・優先順位・裁定・ルーティングの全 LLM 呼び出しが通る single choke point
        なので、「失敗するたび 1 段上へ」を無制限に許すと壊れた入力で倍額を払い続ける。
        """
        km._ESCALATIONS_USED = 0
        self.addCleanup(setattr, km, "_ESCALATIONS_USED", 0)
        calls = []

        def boom(prompt, model, purpose="", agent=None):
            calls.append(agent)
            raise RuntimeError("なにか内容の問題")   # 分類タグ無し = 内容系

        cfg = types.SimpleNamespace(agents={"prioritize": {"fallbacks": [{"agent_cli": "claude"}]}},
                                    agent_cli="ollama", agent_escalation_max=1)
        with mock.patch.object(km, "_run_agent_cli_once", side_effect=boom), \
             mock.patch.object(km, "_RUNTIME_CONFIG", cfg), \
             mock.patch.object(km, "_agent_for", return_value=("ollama", None)):
            with self.assertRaises(RuntimeError):
                km._run_agent_cli("p", None, purpose="prioritize")
            self.assertEqual(len(calls), 2, "1 回目は昇格して拾い直す")
            calls.clear()
            with self.assertRaises(RuntimeError) as ctx:
                km._run_agent_cli("p", None, purpose="prioritize")
        self.assertEqual(len(calls), 1, "上限に達したら昇格しない")
        self.assertIn("上限", str(ctx.exception), "止まった理由を失敗文言に残す")

    def test_quota_failure_is_recorded_as_an_observation(self):
        """quota で落ちた CLI を台帳へ観測として残す（消費 0 行）。

        これが無いと細分した quota は失敗メッセージの中で消え、管理面は「枠に当たった」ことを
        知れない＝降格も自動復帰も起きない。agent-flow の同名テストと対。
        """
        km._record_quota_observation(
            "claude", "you've hit your limit for the day")
        led = os.path.join(self.dir, "ledger")
        with open(os.path.join(led, os.listdir(led)[0]), encoding="utf-8") as f:
            rec = json.loads(f.read().strip())
        self.assertEqual((rec["agent_cli"], rec["quota_kind"], rec["seconds"]),
                         ("claude", "exhausted", 0.0))
        self.assertNotIn("reset_at", rec, "恒久枯渇に復帰予定は付けない")


class NodeBudgetV2AndControlTests(unittest.TestCase):
    """ノード予算 v2（トークン一次・rates 推定）と agent-control（上書き・lifecycle・status）。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kp-nbv2-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        _prev_control = os.environ["AGENT_CONTROL_DIR"]
        os.environ["AGENT_BUDGET_DIR"] = self.dir
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)
        # pop すると**モジュール既定の隔離先ごと消え**、以降のテストが開発者の実
        # `~/.agents/control` を読む（テスト順で agent_cli 系が落ちる原因だった）。
        self.addCleanup(os.environ.__setitem__, "AGENT_CONTROL_DIR", _prev_control)
        km._CONTROL_CACHE["mtime"] = None

    def _budget(self, cfg):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _control(self, ctl):
        with open(os.path.join(self.dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump(ctl, f)
        km._CONTROL_CACHE["mtime"] = None

    def _ledger(self, records):
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        day = time.strftime("%Y%m%d", time.gmtime())
        with open(os.path.join(led, f"{day}.jsonl"), "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

    def test_token_limit_with_estimation(self):
        self._budget({"version": 2, "tokens": 1000,
                      "rates": {"per_cli": {"kiro": 100}}})
        self._ledger([{"workload": "project", "seconds": 8, "agent_cli": "kiro"}])   # 800 (est)
        self.assertFalse(km._node_budget_state()["exceeded"])
        self._ledger([{"workload": "project", "seconds": 1, "tokens_in": 150,
                       "tokens_out": 100}])                                          # +250 = 1050
        self.assertTrue(km._node_budget_state()["exceeded"])

    def test_a_by_purpose_policy_never_reaches_project(self):
        """用途別の順位表（`selection_policy.by_purpose`）は project へ届かない。

        調停規則（設計 2026-08-27 G4）は「用途別の実測で選ばれたモデルを変種の既定で
        上書きしない」だが、それを守れるのは**その決定を受け取る経路だけ**である。
        project は version 2 の `selection_policy` を読まず、legacy の
        `workloads[wl].agents` しか見ない——だから by_purpose のモデルが「明示ではない
        モデル」として紛れ込み、変種の既定へ黙って戻ることが起きない。

        ここが縛るのは、将来 project に `selection_policy` を教えるとき
        **`by_purpose=` を一緒に渡さないと静かに壊れる**という一点である。
        """
        km._RUNTIME_CONFIG.agents = {}
        self._control({
            "version": 2,
            "workloads": {"project": {"selection_policy": {
                "strategy": "balanced", "retry_limit": 1, "no_candidate": "park",
                "candidates": [{"agent_cli": "ollama", "model": "gemma4:e4b", "rank": 1}],
                "by_purpose": {"plan": {"operations": ["bounded-review"],
                                        "candidates": [{"agent_cli": "ollama",
                                                        "model": "gemma4:12b",
                                                        "rank": 1}]}},
            }}},
        })
        # legacy の `agents:` が無いので上書きは 1 つも効かず、設定の既定のまま。
        self.assertEqual(km._agent_for("plan"), ("kiro", None))

    def test_a_control_selected_model_still_defers_to_the_variant_default(self):
        """control が選んだモデルは「用途を問わずそのまま使う」という明示ではない。

        by_purpose の実測と違い、こちらは用途を見ていないので変種の用途専用チューニング
        のほうが良い推定である（agent-flow の flat candidates と同じ扱い）。
        """
        km._RUNTIME_CONFIG.agents = {}
        self._control({"version": 1, "workloads": {"project": {"agents": {
            "plan": {"agent_cli": "ollama", "model": "qwen3:8b"}}}}})
        self.assertEqual(km._agent_for("plan"), ("ollama-json", "gemma4:e4b"))

    def test_control_override_and_lifecycle(self):
        self._control({"version": 1, "revision": 3,
                       "workloads": {"project": {"agents": {"plan": {"model": "opus"}}}}})
        self.assertEqual(km._agent_for("plan")[1], "opus")
        self._control({"version": 1, "workloads": {"project": {"lifecycle": "stop"}}})
        with self.assertRaises(RuntimeError) as ctx:
            km._run_agent_cli("x", None, purpose="plan")
        self.assertIn("[agent-control]", str(ctx.exception))
        self.assertIn("[agent-error:control]", str(ctx.exception))
        self.assertEqual(km.classify_agent_failure(str(ctx.exception))[0], "control")


class TestNodeAutoConfig(unittest.TestCase):
    def test_auto_node_name_sanitizes_hostname(self):
        with mock.patch.object(km.socket, 'gethostname', return_value='my pc.local!'):
            self.assertEqual(km._auto_node_name(), 'my-pc.local')

    def test_auto_node_name_falls_back_when_empty_after_sanitize(self):
        with mock.patch.object(km.socket, 'gethostname', return_value='!!!'):
            self.assertEqual(km._auto_node_name(), 'node')


class ResidentCliTests(unittest.TestCase):
    """常駐体 CLI: serve / status / worker init / worker（実装計画 W1-11）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="resident-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.agents_home = self.tmp / "agents-home"
        os.environ["AGENT_PROJECT_AGENTS_HOME"] = str(self.agents_home)
        self.addCleanup(os.environ.pop, "AGENT_PROJECT_AGENTS_HOME", None)
        # amigos の手番マーカー（ツール横断のデータ契約）も実ホームを汚さない場所へ寄せる
        self.turns_home = self.tmp / "amigos-turns"
        os.environ["AGENT_AMIGOS_TURNS_DIR"] = str(self.turns_home)
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_TURNS_DIR", None)

    def test_node_commands_dir_follows_the_agents_home(self):
        # 画面（dashboard）はこの規則と同じ場所へ投函する。両者がずれると、押しても
        # 何も起きない（`.err` すら出ない）——P0-2 で実際に踏んだ形。旧ホーム（~/.agent）へは
        # 落とさない（画面側にだけフォールバックがあると、書けるのに誰も読まない場所ができる）。
        os.environ.pop("AGENT_COMMANDS_DIR", None)
        self.assertEqual(km.node_commands_dir(), km._agents_home() / "commands")

    def test_load_host_config_defaults_to_hostname_worker_profile(self):
        host = km.load_host_config(str(self.tmp / "no-such-file.yaml"))
        self.assertTrue(host.is_worker)
        self.assertIsNone(host.path)
        self.assertTrue(host.node_id)  # 正規化済みホスト名

    def test_load_host_config_reads_declared_projects(self):
        proj_root = self.tmp / "proj-a"
        proj_root.mkdir()
        host_path = self.tmp / "agent-project.host.json"
        host_path.write_text(json.dumps({
            "schema_version": 1, "node_id": "PC-A", "projects": [{"root": str(proj_root)}],
            "tags": ["gpu"], "budget": {"max_concurrent": 2},
        }), encoding="utf-8")
        host = km.load_host_config(str(host_path))
        self.assertEqual(host.node_id, "pc-a")   # normalize_node_id で正規化
        self.assertFalse(host.is_worker)
        self.assertEqual(len(host.projects), 1)
        self.assertEqual(host.tags, ["gpu"])
        self.assertEqual(host.max_concurrent, 2)

    def test_project_children_skips_entries_without_root(self):
        host = km.HostConfig({"projects": [{"name": "no-root"}, {"root": str(self.tmp)}]})
        specs = km._project_children(host)
        self.assertEqual(len(specs), 1)
        self.assertIn("run", specs[0].argv)
        self.assertIn("--watch", specs[0].argv)
        # 子へ渡すのは名前だけ（S1）。root / state_repo / overrides の解釈は子の
        # resolve_config が host.yaml を読み直して行う（宣言の解釈を 2 実装にしない）。
        self.assertEqual(specs[0].argv[-2:], ["--project", specs[0].name])
        self.assertEqual(specs[0].name, km._project_name({"root": str(self.tmp)}))

    def test_project_children_drops_duplicate_names(self):
        # 重複名を specs に残すと Supervisor.add が同名キーを差し替えて 1 本目の Popen
        # ハンドルを失い、続く start が 2 本目を起こす。1 本目は監視されず
        # graceful_shutdown でも止まらない孤児になる。
        other = self.tmp / "other"
        other.mkdir(exist_ok=True)
        host = km.HostConfig({"projects": [
            {"name": "dup", "root": str(self.tmp)},
            {"name": "dup", "root": str(other)},
        ]})
        specs = km._project_children(host)
        self.assertEqual([s.name for s in specs], ["dup"])
        self.assertEqual(specs[0].argv[-2:], ["--project", "dup"])   # 先勝ち（後続を無視）

    def test_gc_tick_declares_timeout_so_watchdog_does_not_abort(self):
        # gc はプロジェクトごとに外部コマンドを逐次起動するので正当に長くなる。
        # Tick.timeout を宣言しないと self-watchdog（既定 300s）の猶予を超え、
        # 健全な常駐体を abort する。猶予に織り込まれていることを固定する。
        host = km.HostConfig({"node_id": "pc-test", "projects": [
            {"name": f"p{i}", "root": str(self.tmp / f"p{i}")} for i in range(3)]})
        for i in range(3):
            (self.tmp / f"p{i}").mkdir(exist_ok=True)
        _sup, sched, _st, _ws, _pool = km._build_resident(host, start_children=False)
        gc_tick = next(t for t in sched._ticks if t.name == "gc")
        self.assertEqual(gc_tick.timeout, km._GC_PROJECT_TIMEOUT_SEC * 3)
        # 猶予 = period + timeout + watchdog_timeout。3 プロジェクトが全て
        # タイムアウトしても（360s）猶予内に収まる。
        self.assertGreater(sched._grace["gc"], gc_tick.period + km._GC_PROJECT_TIMEOUT_SEC * 3 - 1)

    def test_build_resident_starts_children_and_writes_status(self):
        # 実プロジェクト起動の代わりに、確実にすぐ終了する軽いコマンドを子として登録する
        # （Supervisor 自体は実プロセスを見る——テストは argv を差し替えるだけ）。
        host = km.HostConfig({"node_id": "pc-test"})
        sup, sched, status, write_status, pool = km._build_resident(host, start_children=False)
        sup.add(km.ChildSpec("fake", [sys.executable, "-c", "import time; time.sleep(0.05)"]))
        sup.start("fake")
        write_status()
        path = self.agents_home / "engine" / "status.json"
        self.assertTrue(path.is_file())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["node"], "pc-test")
        self.assertEqual(len(data["children"]), 1)
        self.assertEqual(data["children"][0]["name"], "fake")
        sup.stop_all()

    def _board_delegation(self, did: str, *, terminal: str = "result.json",
                          age_days: float = 0.0) -> Path:
        board = self.tmp / "board"
        d = board / "delegations" / did
        d.mkdir(parents=True, exist_ok=True)
        (d / "post.json").write_text(json.dumps({"id": did}), encoding="utf-8")
        if terminal:
            mark = d / terminal
            mark.write_text(json.dumps({"winner": "pc-x", "status": "done"}), encoding="utf-8")
            if age_days:
                old = time.time() - age_days * 86400.0
                os.utime(mark, (old, old))
        return board

    def test_board_sweep_removes_only_long_terminal_orphans(self):
        # 通常の削除は依頼側が settle 直後に行う（_reap_offloaded）。ここが拾うのは
        # 依頼側ごと消えた孤児だけ——終端したばかりの公示を消すと、まだ結果を読んでいない
        # 依頼側の offloaded タスクが未終端のまま永久に固まる（タイムアウトが無い）。
        board = self._board_delegation("dg-old", age_days=60)
        self._board_delegation("dg-fresh", age_days=0)
        self._board_delegation("dg-running", terminal="")
        host = km.HostConfig({"node_id": "pc-test", "board": str(board)})
        self.assertEqual(km._sweep_terminal_delegations(host), {"delegations": 1})
        left = sorted(p.name for p in (board / "delegations").iterdir())
        self.assertEqual(left, ["dg-fresh", "dg-running"])

    def test_board_sweep_noop_without_board(self):
        self.assertEqual(km._sweep_terminal_delegations(km.HostConfig({})), {})

    def test_board_sweep_counts_cancelled_marker_too(self):
        board = self._board_delegation("dg-cancel", terminal="cancelled.json", age_days=60)
        host = km.HostConfig({"node_id": "pc-test", "board": str(board)})
        self.assertEqual(km._sweep_terminal_delegations(host), {"delegations": 1})

    def test_board_sweep_reaches_git_board_clone(self):
        """git+ 板でも掃除が効く。`Path(host.board)` を直接見に行く実装だと `git+ssh://…` は
        ディレクトリでないので黙って no-op になり、孤児が無限に積む。"""
        remote = self.tmp / "board-remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        workdir = self.tmp / "board-clone"
        board = km.BoardRepo(f"git+file://{remote}", workdir=str(workdir))
        d = Path(board.dir) / "delegations" / "dg-git"
        with board._locked():
            board._ensure()
        d.mkdir(parents=True, exist_ok=True)
        mark = d / "result.json"
        mark.write_text(json.dumps({"status": "done"}), encoding="utf-8")
        old = time.time() - 60 * 86400.0
        os.utime(mark, (old, old))
        host = km.HostConfig({"node_id": "pc-test", "board": f"git+file://{remote}",
                              "board_workdir": str(workdir)})
        self.assertEqual(km._sweep_terminal_delegations(host), {"delegations": 1})
        self.assertFalse(d.exists())

    def _amigos_turn_mark(self, mission: str, role: str, pid: int) -> Path:
        """agent-amigos（`turnmark`）が手番の実行中だけ置く印。ツール横断のデータ契約なので
        テストも実装を import せずファイル形式で書く。"""
        self.turns_home.mkdir(parents=True, exist_ok=True)
        path = self.turns_home / f"{mission}--{role}.json"
        path.write_text(json.dumps({"pid": pid, "mission": mission, "role": role,
                                    "started": "2026-07-25T00:00:00Z"}), encoding="utf-8")
        return path

    def test_external_inflight_counts_skill_launched_turns(self):
        # 実装計画 W1-5「計数は status/run ファイルから導出」。常駐体はノード唯一の実行主体
        # ではない（C14 併走）ので、プロセス内カウンタだけだと人が直接叩いた手番が
        # max_concurrent を素通りする。
        self._amigos_turn_mark("m1", "architect", os.getpid())
        host = km.HostConfig({"node_id": "pc-test"})
        self.assertEqual(km._external_amigos_inflight(host), {"amigos/m1/architect"})

    def test_external_inflight_ignores_dead_process_marks(self):
        # 落ちたプロセスの書き残しでスロットを永久に埋めない（生存は pid で判定する）。
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()
        self._amigos_turn_mark("m1", "stale", dead.pid)
        self.assertEqual(km._external_amigos_inflight(km.HostConfig({"node_id": "pc-test"})),
                         set())

    def test_external_inflight_ignores_role_state_in_bus(self):
        """在籍状態（バスの `status/<node>--<role>.json`）を走行中と読まないこと。

        `state` は**ロールの在籍**で、手番が終わっても `working` のまま残る
        （`runner._turn_once` の末尾がそう書く）。ここを走行中と誤読すると、常駐体が回した
        ロールの次の手番を `submit` の二重実行回避が永久に弾き、amigos が進まなくなる。"""
        bus = self.tmp / "amigos-bus"
        sdir = bus / "missions" / "m1" / "status"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "pc-test--architect.json").write_text(json.dumps({
            "state": "working",
            "heartbeat": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}),
            encoding="utf-8")
        host = km.HostConfig({"node_id": "pc-test", "amigos_bus": str(bus)})
        self.assertEqual(km._external_amigos_inflight(host), set(),
                         "在籍状態を走行中と読んでいる（自分の次の手番を自分で弾く）")

    def test_pool_reserves_slots_for_externally_running_work(self):
        # 外部で 1 件走っていれば、max_concurrent=1 のプールは新規投入をキューへ回す。
        external = {"amigos/m1/architect"}
        pool = km.NodeWorkerPool(1, external_inflight=lambda: set(external))
        self.assertFalse(pool.submit(km.WorkItem(id="amigos/m2/impl", run=lambda: None)))
        self.assertEqual(pool.status()["queued"], 1)
        external.clear()                       # 外部の仕事が終われば空く
        self.assertEqual(pool.drain(), 1)

    def test_pool_max_concurrent_can_be_raised_without_restart(self):
        # agent-control は pull 型（dashboard の宣言は再起動を挟まず変わる）。枠を広げた分は
        # 次の drain が拾い、板へ宣言した値と手元の枠が食い違ったままにならない。
        hold = threading.Event()
        self.addCleanup(hold.set)
        pool = km.NodeWorkerPool(1)
        self.assertTrue(pool.submit(km.WorkItem(id="flow/a", run=hold.wait)))
        self.assertFalse(pool.submit(km.WorkItem(id="flow/b", run=hold.wait)))
        self.assertEqual(pool.status()["queued"], 1)
        pool.set_max_concurrent(0)             # 0 = 無制限（板の語彙）
        self.assertEqual(pool.drain(), 1)

    def test_pool_skips_work_already_running_elsewhere(self):
        # 同じ (mission, role) を別プロセスが既に走らせているなら投入しない。積むと
        # 外部の完了後に二重実行になる（次の tick で外部が終わっていれば改めて投入される）。
        pool = km.NodeWorkerPool(4, external_inflight=lambda: {"amigos/m1/architect"})
        self.assertFalse(pool.submit(km.WorkItem(id="amigos/m1/architect", run=lambda: None)))
        self.assertEqual(pool.status()["queued"], 0)   # キューにも積まない

    def test_pool_does_not_double_count_its_own_items(self):
        # 自分が起こした手番も同じ手番マーカーを置くので、外部観測にも現れる。
        # 和集合で数えるため二重計上しない（id 空間を投入側と揃えてあることの検証）。
        release = threading.Event()
        self.addCleanup(release.set)
        external: set = set()
        pool = km.NodeWorkerPool(2, external_inflight=lambda: set(external))
        self.assertTrue(pool.submit(km.WorkItem(id="amigos/m1/architect",
                                                run=lambda: release.wait(5))))
        external.add("amigos/m1/architect")            # 走り出した自分の分が観測に現れる
        self.assertEqual(pool.status()["used"], 1)     # 2 ではない

    @staticmethod
    def _availability(stop_hhmm: str, drain_before_sec: int = 0,
                      shutdown_grace_sec: int = 0) -> dict:
        # grace は明示 0 が既定。既定値（300）のままだと daily_stop=00:00 のテストが
        # 00:00〜00:05 UTC の 5 分間だけ「まだ猶予中」になって落ちる。
        return {"timezone": "UTC", "daily_stop": stop_hhmm,
                "drain_before_sec": drain_before_sec,
                "shutdown_grace_sec": shutdown_grace_sec}

    def _sup_with_fake_child(self, host):
        sup, _sched, status, _ws, _pool = km._build_resident(host, start_children=False)
        sup.add(km.ChildSpec("p1", [sys.executable, "-c", "import time; time.sleep(30)"]))
        sup.start("p1")
        self.addCleanup(sup.stop_all)
        return sup, status

    def test_planned_stop_pauses_child_instead_of_counting_deaths(self):
        # 実装計画 W1-4: 停止を決めるのは常に親。子が自分に SIGTERM を送る旧経路では
        # Supervisor がクラッシュと読んで再起動し、繰り返しで隔離に達していた
        # （夜間停止のたびにプロジェクトが止まる）。pause は死亡に数えない。
        host = km.HostConfig({"node_id": "pc-test",
                              "availability": self._availability("00:00")})  # 常に停止時間帯
        sup, status = self._sup_with_fake_child(host)
        for _ in range(10):                      # 何巡しても隔離に達しない
            km._availability_tick(host, sup, status)
            sup.check_health()
        info = sup.status()["p1"]
        self.assertTrue(info["paused"])
        self.assertFalse(info["alive"])
        self.assertFalse(info["quarantined"])
        self.assertEqual(info["deaths"], 0)      # 計画停止は失敗ではない

    def test_child_resumes_when_window_reopens(self):
        host_off = km.HostConfig({"node_id": "pc-test",
                                  "availability": self._availability("00:00")})
        sup, status = self._sup_with_fake_child(host_off)
        km._availability_tick(host_off, sup, status)
        sup.check_health()
        self.assertTrue(sup.status()["p1"]["paused"])
        # 稼働時間帯へ戻す（daily_stop 未設定＝常時 active）
        host_on = km.HostConfig({"node_id": "pc-test"})
        host_on.availability = {}
        sup.resume("p1")                         # tick は availability 未宣言だと何もしない
        sup.check_health()
        self.assertFalse(sup.status()["p1"]["paused"])
        self.assertTrue(sup.status()["p1"]["alive"])

    def test_drain_window_does_not_kill_the_child_yet(self):
        """drain 中（daily_stop 前）はまだ止めない——drain は「新規 claim を止めて走っている
        ものを終わらせる」時間帯で、そこで SIGTERM すると `drain_before_sec` も
        `shutdown_grace_sec` も死に設定になる。新規 claim の停止は子側の
        `start_availability_monitor` が担う。"""
        # 判定時刻を固定する（実時計に依らない）: 12:00 に停止・drain 幅 2 時間で、
        # いま 11:00 → drain 中（stopped ではない）。相対時刻で書くと、日付を跨ぐ
        # 時間帯に走ったとき daily_stop が「その日の早い時刻」になって stopped と読まれる。
        now = datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc)
        host = km.HostConfig({"node_id": "pc-test",
                              "availability": self._availability("12:00", drain_before_sec=7200)})
        self.assertEqual(km.availability_state(types.SimpleNamespace(
            availability=host.availability), now), "draining")   # 前提の確認
        sup, status = self._sup_with_fake_child(host)
        km._availability_tick(host, sup, status, now)
        sup.check_health()
        info = sup.status()["p1"]
        self.assertFalse(info["paused"], "drain 開始で子を殺している（猶予が効かない）")
        self.assertTrue(info["alive"])

    def test_invalid_availability_pauses_and_reports_once(self):
        # 時間帯が判定できないなら止める側へ倒す（止めたい時間帯に動く方が害が大きい）。
        # 同じ文言を tick ごとに積むと recent_errors が埋まって他のエラーを押し出す。
        host = km.HostConfig({"node_id": "pc-test",
                              "availability": {"daily_stop": "ここは時刻ではない"}})
        sup, status = self._sup_with_fake_child(host)
        for _ in range(5):
            km._availability_tick(host, sup, status)
            sup.check_health()
        self.assertTrue(sup.status()["p1"]["paused"])
        self.assertEqual(sum("availability" in e for e in status.recent_errors), 1)

    def test_paused_state_is_reported_to_dashboard(self):
        # dashboard は「時間が来れば戻る（paused）」と「人が直すまで戻らない（quarantined）」で
        # 文言を変える必要があるため、両方を別フィールドで載せる。
        host = km.HostConfig({"node_id": "pc-test",
                              "availability": self._availability("00:00")})
        sup, _sched, _st, write_status, _pool = km._build_resident(host, start_children=False)
        sup.add(km.ChildSpec("p1", [sys.executable, "-c", "import time; time.sleep(30)"]))
        sup.start("p1")
        self.addCleanup(sup.stop_all)
        sup.pause("p1")
        write_status()
        data = json.loads((self.agents_home / "engine" / "status.json").read_text(encoding="utf-8"))
        self.assertTrue(data["children"][0]["paused"])
        self.assertFalse(data["children"][0]["quarantined"])

    def _project_repo(self, name: str, *, with_remote: bool = True) -> Path:
        """host.yaml に載せる体裁のプロジェクトルート（git 済み・任意で origin 付き）。"""
        root = self.tmp / name
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
        (root / "seed.md").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)
        if with_remote:
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin",
                            "file:///no-such-remote.git"], check=True)
        return root

    def test_status_reports_sync_health_so_dashboard_is_not_falsely_green(self):
        # dashboard の同期表示は sync_health だけが根拠（engine.summarize）。空のまま出すと
        # リモートが落ちていても未 push が溜まっていても「共有先と揃っています」と緑になる
        # ——W2-1 で dashboard 側の fetch を廃止した以上、ここが唯一の異常検知経路。
        root = self._project_repo("p-remote")
        host = km.HostConfig({"node_id": "pc-test",
                              "projects": [{"name": "p1", "root": str(root)}]})
        _sup, _sched, _st, write_status, _pool = km._build_resident(host, start_children=False)
        write_status()
        data = json.loads((self.agents_home / "engine" / "status.json").read_text(encoding="utf-8"))
        entries = {s["name"]: s for s in data["sync_health"]}
        self.assertIn("p1", entries)
        # origin を一度も取り込めていない＝「揃っている」と誤って緑にしない
        self.assertTrue(entries["p1"]["last_error"])

    def test_sync_health_omits_projects_without_remote(self):
        # remote 未設定はローカル完結の縮退で同期の概念が無い。載せると dashboard が
        # 「共有の途中です」と言い出す（共有先が無いのに）。
        root = self._project_repo("p-local", with_remote=False)
        host = km.HostConfig({"node_id": "pc-test",
                              "projects": [{"name": "p1", "root": str(root)}]})
        _sup, _sched, _st, write_status, _pool = km._build_resident(host, start_children=False)
        write_status()
        data = json.loads((self.agents_home / "engine" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(data["sync_health"], [])

    def test_status_reports_running_runs_from_worker_pool(self):
        # dashboard の実行中件数（renderer/sections/flow.js）はこれを数える。
        host = km.HostConfig({"node_id": "pc-test"})
        _sup, _sched, _st, write_status, pool = km._build_resident(host, start_children=False)
        started = threading.Event()
        release = threading.Event()
        pool.submit(km.WorkItem(id="amigos/m1/architect",
                                run=lambda: (started.set(), release.wait(5))))
        self.assertTrue(started.wait(5), "ワーカーが起動しなかった")
        try:
            write_status()
            data = json.loads(
                (self.agents_home / "engine" / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(data["running_runs"], ["amigos/m1/architect"])
        finally:
            release.set()

    def test_status_children_carry_declared_root_for_project_discovery(self):
        # dashboard のプロジェクト発見は children[].root だけを辿る（実装計画 W2-4）。
        # ここが欠けると「常駐体は動いているのにプロジェクトが 0 件」の画面になる。
        proj = self.tmp / "proj-a"
        proj.mkdir()
        host = km.HostConfig({"node_id": "pc-test",
                              "projects": [{"name": "alpha", "root": str(proj)}]})
        _sup, _sched, _st, write_status, _pool = km._build_resident(host, start_children=False)
        write_status()
        data = json.loads((self.agents_home / "engine" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual([c["name"] for c in data["children"]], ["alpha"])
        self.assertEqual(data["children"][0]["root"], str(proj))

    def test_cmd_status_reports_missing_when_never_served(self):
        rc = km.cmd_status(types.SimpleNamespace(json=False))
        self.assertEqual(rc, 1)

    def test_cmd_status_json_reflects_written_status(self):
        host = km.HostConfig({"node_id": "pc-test"})
        sup, sched, status, write_status, pool = km._build_resident(host)
        write_status()
        sup.stop_all()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = km.cmd_status(types.SimpleNamespace(json=True))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["node"], "pc-test")

    def test_cmd_worker_init_writes_host_yaml(self):
        out = self.tmp / "worker-host.yaml"
        args = types.SimpleNamespace(node_id="worker-1", tags="gpu,docker", agent_cli="claude",
                                     board=str(self.tmp / "board"), max_concurrent=3,
                                     out=str(out), force=False)
        rc = km.cmd_worker_init(args)
        self.assertEqual(rc, 0)
        self.assertTrue(out.is_file())
        host = km.load_host_config(str(out))
        self.assertEqual(host.node_id, "worker-1")
        self.assertTrue(host.is_worker)
        self.assertEqual(host.tags, ["gpu", "docker"])
        self.assertEqual(host.max_concurrent, 3)

    def test_cmd_worker_init_omits_max_concurrent_when_unspecified(self):
        """CLI で --max-concurrent を指定しなかった場合、host.yaml に budget.max_concurrent
        を書かない（板の契約: 0/省略=無制限。0 を既定値として書くと「明示的に 0 を宣言」に
        化けて契約上は無制限を意味してしまうバグの回帰テスト）。"""
        out = self.tmp / "worker-host.yaml"
        args = types.SimpleNamespace(node_id="worker-2", tags=None, agent_cli=None,
                                     board=None, max_concurrent=None, out=str(out), force=False)
        rc = km.cmd_worker_init(args)
        self.assertEqual(rc, 0)
        raw = km.yaml.safe_load(out.read_text(encoding="utf-8"))
        self.assertNotIn("max_concurrent", raw.get("budget", {}) or {})
        host = km.load_host_config(str(out))
        self.assertIsNone(host.max_concurrent)

    def test_cmd_worker_init_refuses_overwrite_without_force(self):
        out = self.tmp / "worker-host.yaml"
        out.write_text("existing", encoding="utf-8")
        args = types.SimpleNamespace(node_id=None, tags=None, agent_cli=None, board=None,
                                     max_concurrent=0, out=str(out), force=False)
        rc = km.cmd_worker_init(args)
        self.assertEqual(rc, 1)
        self.assertEqual(out.read_text(encoding="utf-8"), "existing")

    def test_parent_kill_leaves_child_orphaned_and_next_start_recovers(self):
        """カオス（親 kill・実装計画 W1-12）: 常駐体を SIGKILL すると graceful 停止は
        走らないので、子は**孤児として生き残る**。これは仕様——親は自分の死に際に
        何もできない。回復は「次に上がった常駐体が新しい子を起こす」で成立するが、
        そのとき孤児が残っていると同一プロジェクトにループが 2 本並ぶ。

        現状の Supervisor は前世代の子を知らない（PID を永続化していない）ため、
        **孤児の掃除は起動系（systemd の KillMode= 等）に委ねている**。この前提が崩れると
        二重実行になるので、事実として固定しておく。"""
        marker = self.tmp / "child-started"
        child_code = (f"import pathlib,time; pathlib.Path({str(marker)!r}).write_text('1'); "
                      "time.sleep(300)")
        sup = km.Supervisor()
        sup.add(km.ChildSpec("p1", [sys.executable, "-c", child_code]))
        sup.start("p1")
        self.addCleanup(sup.stop_all)
        deadline = time.time() + 15
        while time.time() < deadline and not marker.exists():
            time.sleep(0.1)
        self.assertTrue(marker.exists(), "子が起動しなかった")
        child_pid = sup._children["p1"].proc.pid
        # 親（このテストプロセスの Supervisor）が突然消えた状況を模す: 監督だけを捨てる
        sup._children.clear()
        self.assertTrue(km._pid_alive(child_pid), "親の監督を失っても子は生き続ける（＝孤児）")
        # 新しい常駐体は前世代を知らないまま自分の子を起こす（＝二重実行になりうる）
        sup2 = km.Supervisor()
        sup2.add(km.ChildSpec("p1", [sys.executable, "-c", "import time; time.sleep(1)"]))
        sup2.start("p1")
        self.addCleanup(sup2.stop_all)
        self.assertTrue(km._pid_alive(child_pid))
        os.kill(child_pid, signal.SIGKILL)      # 後始末（起動系が担う部分をテストが代行）

    def test_serve_exits_cleanly_on_sigterm(self):
        # systemd の stop/restart は SIGTERM。既定ハンドラは即死（終了コード -15）なので
        # finally の graceful_shutdown が走らず、`run --watch` の子が監督者不在で生き残る
        # （次の起動で Supervisor が新しい子を起こし、同一プロジェクトにループが 2 本並ぶ）。
        # ハンドラが効いていれば finally を通って 0 で終わる——この差が決定的。
        root = Path(__file__).resolve().parents[2]
        code = ("import sys, types;"
                f"sys.path.insert(0, {str(root / 'agent-project')!r});"
                f"sys.path.insert(0, {str(root / 'agentcore')!r});"
                "import agent_project as km;"
                "sys.exit(km.cmd_serve(types.SimpleNamespace(host_config=None)))")
        serve = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=str(self.tmp),   # host.yaml を置いていない＝projects 空（ワーカー profile）
            env={**os.environ, "AGENT_PROJECT_AGENTS_HOME": str(self.tmp / "agents")},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            status_path = self.tmp / "agents" / "engine" / "status.json"
            deadline = time.time() + 30
            while time.time() < deadline and not status_path.exists():
                if serve.poll() is not None:
                    self.fail(f"serve が早期終了した rc={serve.returncode}")
                time.sleep(0.2)
            self.assertTrue(status_path.exists(), "serve が status.json を書かなかった")
            serve.send_signal(signal.SIGTERM)
            rc = serve.wait(timeout=30)
            self.assertEqual(rc, 0, f"SIGTERM で graceful 終了しなかった rc={rc}"
                                    "（-15 は既定ハンドラによる即死＝finally が走っていない）")
        finally:
            if serve.poll() is None:
                serve.kill()
                serve.wait(timeout=10)

    def _spawn_serve(self):
        """`cmd_serve` を子プロセスで起こす（stdout をパイプで受ける）。"""
        root = Path(__file__).resolve().parents[2]
        code = ("import sys, types;"
                f"sys.path.insert(0, {str(root / 'agent-project')!r});"
                f"sys.path.insert(0, {str(root / 'agentcore')!r});"
                "import agent_project as km;"
                "sys.exit(km.cmd_serve(types.SimpleNamespace(host_config=None)))")
        serve = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=str(self.tmp),   # host.yaml を置いていない＝projects 空（ワーカー profile）
            env={**os.environ, "AGENT_PROJECT_AGENTS_HOME": str(self.tmp / "agents")},
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

        def _cleanup():
            if serve.poll() is None:
                serve.kill()
                serve.wait(timeout=10)
            if serve.stdout is not None:
                serve.stdout.close()

        self.addCleanup(_cleanup)
        return serve

    def test_serve_exits_cleanly_on_sigterm_right_after_banner(self):
        """起動バナー直後の SIGTERM で graceful 終了する（P0-1）。

        バナーは `cmd_serve` の契約上「ハンドラ設置後の最初の出力」なので、これを待って
        撃てば**必ず**旧実装の窓（子の起動〜tick 開始）に当たる。時間に依存せず決定的で、
        `status.json` の出現を待つ既存テスト（窓の後ろ側だけを撃つ＝間欠的にしか当たらない）
        より強い。"""
        serve = self._spawn_serve()
        line = serve.stdout.readline()
        self.assertIn(km.SERVE_BANNER, line, f"起動バナーが最初に出ていない: {line!r}")
        serve.send_signal(signal.SIGTERM)
        rc = serve.wait(timeout=60)
        self.assertEqual(rc, 0, f"バナー直後の SIGTERM で graceful 終了しなかった rc={rc}"
                                "（-15 は既定ハンドラによる即死＝ハンドラ設置が遅い）")

    def test_serve_stops_children_when_signalled_during_startup(self):
        """子の起動中に停止要求が入ったら、tick を始めず子を畳んで 0 で返る（P0-1）。

        ハンドラを差し替えて捕まえ、`_build_resident` の**中から**呼ぶ——実プロセスに
        シグナルを撃つ代わりに窓のど真ん中を狙い撃ちできるので、順序の契約そのものを
        時間に依存せず固定できる。"""
        handlers = {}
        order = []

        def fake_signal(sig, handler):
            handlers[sig] = handler
            order.append(("signal", sig))
            return signal.SIG_DFL

        stopped = {"all": 0}
        sup = types.SimpleNamespace(names=lambda: [], stop_all=lambda: stopped.__setitem__(
            "all", stopped["all"] + 1))
        sched = types.SimpleNamespace(start=lambda: order.append(("sched.start", None)),
                                      stop=lambda: None, join=lambda timeout=None: None)
        wrote = {"n": 0}

        def fake_build(host, **kw):
            order.append(("build", None))
            # 子を起こしている最中に SIGTERM が届いた状況（旧実装が即死していた窓）
            self.assertIn(signal.SIGTERM, handlers, "子を起こす前にハンドラが設置されていない")
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return sup, sched, None, lambda: wrote.__setitem__("n", wrote["n"] + 1), None

        with mock.patch.object(km.signal, "signal", side_effect=fake_signal), \
             mock.patch.object(km, "_build_resident", side_effect=fake_build), \
             mock.patch.object(km, "graceful_shutdown", side_effect=lambda s, **kw: s.stop_all()):
            rc = km.cmd_serve(types.SimpleNamespace(host_config=None))

        self.assertEqual(rc, 0)
        self.assertEqual([k for k, _ in order][:2], ["signal", "signal"],
                         f"ハンドラ設置が先ではない: {order}")
        self.assertNotIn(("sched.start", None), order, "停止要求後に tick を始めている")
        self.assertEqual(wrote["n"], 0, "停止要求後に git 観測込みの write_status を呼んでいる")
        self.assertEqual(stopped["all"], 1, "子を畳んでいない")

    def test_stop_signal_handler_escalates_on_second_signal(self):
        """2 度目のシグナルは握らず既定ハンドラへ戻す（停止が詰まったときの逃げ道）。"""
        handlers, restored, killed = {}, [], []

        def fake_signal(sig, handler):
            if handler is signal.SIG_DFL:
                restored.append(sig)
            else:
                handlers[sig] = handler
            return signal.SIG_DFL

        with mock.patch.object(km.signal, "signal", side_effect=fake_signal), \
             mock.patch.object(km.os, "kill", side_effect=lambda pid, sig: killed.append(sig)):
            stopping = km._install_stop_signals()
            self.assertFalse(stopping.is_set())
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            self.assertTrue(stopping.is_set())
            self.assertEqual((restored, killed), ([], []))
            handlers[signal.SIGTERM](signal.SIGTERM, None)      # 2 度目
        self.assertEqual(restored, [signal.SIGTERM])
        self.assertEqual(killed, [signal.SIGTERM])

    def test_cmd_worker_delegates_to_serve_for_worker_profile(self):
        # worker は serve と同一実装（C12）。projects 無しなら警告を出さず serve と同じ結果になる
        # ことだけを確認する（無限ループを避けるため _build_resident 経路を直接検証済みなので
        # ここは cmd_worker が cmd_serve を呼ぶ配線だけを軽く確認する）。
        called = {"n": 0}
        with mock.patch.object(km, "cmd_serve", side_effect=lambda a: called.__setitem__("n", called["n"] + 1) or 0):
            rc = km.cmd_worker(types.SimpleNamespace(host_config=str(self.tmp / "missing.yaml")))
        self.assertEqual(rc, 0)
        self.assertEqual(called["n"], 1)

    def test_project_gc_sweeper_skips_missing_root(self):
        self.assertIsNone(km._project_gc_sweeper({"name": "no-root"}))

    def test_project_gc_sweeper_runs_real_gc_subcommand(self):
        # フェイク agent-flow を project 設定（agent_flow:）経由で差し込み、実サブプロセスで
        # `agent-project gc` → `agent-flow cleanup` の委譲まで通しで検証する。
        root = self.tmp / "proj"
        root.mkdir()
        fake_flow = self.tmp / "fake_flow.py"
        fake_flow.write_text(
            "import sys, json\n"
            "if 'cleanup' in sys.argv:\n"
            "    print(json.dumps({'locks': 0, 'tmp': 0, 'clones': 0, 'work_repos': 0, 'cache': 0}))\n",
            encoding="utf-8")
        # 設定は状態ルート直下が唯一の置き場（S1）。sweeper は --project 名義で起動するので
        # host.yaml にその宣言が要る。
        (root / "agent-project.json").write_text(
            json.dumps({"agent_flow": str(fake_flow)}), encoding="utf-8")
        entry = {"name": "gc-proj", "root": str(root)}
        use_host_config(self, {"projects": [entry]}, home=self.agents_home)
        name, sweep = km._project_gc_sweeper(entry)
        self.assertEqual(name, "gc-proj")
        result = sweep()
        self.assertEqual(result.get("cleanup.locks"), 0)

    def test_project_gc_sweepers_wired_into_run_gc(self):
        root = self.tmp / "proj2"
        root.mkdir()
        host = km.HostConfig({"projects": [{"name": "p2", "root": str(root)}]})
        sweepers = km._project_gc_sweepers(host)
        self.assertEqual([n for n, _ in sweepers], ["p2"])
        # run_gc は sweeper 例外を隔離するだけの orchestration（実行結果まで検証済みなのは
        # 上のテスト）。ここでは配線（host.projects → sweepers）だけを確認する。

    def test_amigos_participate_tick_dispatches_owned_roles_to_pool(self):
        log = self.tmp / "amigos-argv.log"
        fake_amigos = self.tmp / "fake_amigos.py"
        fake_amigos.write_text(
            "import sys\n"
            f"open(r'{log}', 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
            "if 'participate' in sys.argv:\n"
            "    print('[{\"mission_id\": \"m1\", \"role_id\": \"architect\"}]')\n",
            encoding="utf-8")
        host = km.HostConfig({"node_id": "pc-a", "amigos_bus": str(self.tmp / "amigos-bus")})
        status = km.EngineStatus("pc-a")
        pool = km.NodeWorkerPool(4)
        with mock.patch.object(km, "_resolve_agent_amigos",
                               return_value=[sys.executable, str(fake_amigos)]):
            km._amigos_participate_tick(host, pool, status)
            for _ in range(50):        # プールの投入スレッドが argv を書くまで軽く待つ
                if log.exists() and len(log.read_text().splitlines()) >= 2:
                    break
                time.sleep(0.02)
        calls = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 2)
        self.assertIn("participate", calls[0])
        self.assertTrue(any("run" in c and "m1" in c and "architect" in c for c in calls[1:]))
        self.assertEqual(status.recent_errors, [])

    def test_amigos_participate_tick_skips_when_bus_unset(self):
        # host.yaml に amigos_bus が無ければ tick 自体を skip する（board 未設定時と同じ流儀）。
        host = km.HostConfig({"node_id": "pc-a"})
        self.assertEqual(host.amigos_bus, "")
        sup, sched, status, write_status, pool = km._build_resident(host, start_children=False)
        with mock.patch.object(km, "_amigos_participate_tick") as spy:
            [t for t in sched._ticks if t.name == "amigos"][0].fn()
        spy.assert_not_called()

    def test_amigos_tick_isolates_participate_subprocess_failure(self):
        # 設計 §4.2 の実行規約「例外は tick 内に隔離しループを殺さない」——amigos 側が
        # 存在しない/クラッシュしても常駐体本体は止めず、status のエラーリングバッファに
        # 記録するだけに留める（障害と回復表, 実装計画 W1-12 のカオステスト観点）。
        host = km.HostConfig({"node_id": "pc-a", "amigos_bus": str(self.tmp / "amigos-bus")})
        sup, sched, status, write_status, pool = km._build_resident(host, start_children=False)
        missing = str(self.tmp / "no-such-agent-amigos")
        with mock.patch.object(km, "_resolve_agent_amigos", return_value=[missing]):
            [t for t in sched._ticks if t.name == "amigos"][0].fn()   # 例外を投げないこと自体が検証
        self.assertTrue(status.recent_errors)
        self.assertIn("amigos participate", status.recent_errors[-1])

    def test_flow_participate_tick_dispatches_accepted_runs_to_pool(self):
        # 受理（participate）と実行（run）を分ける配線: tick は受理された run-id を
        # NodeWorkerPool へ投入するだけで、run 自体は tick 内で実行しない。
        log = self.tmp / "flow-argv.log"
        fake_ap = self.tmp / "fake_agent_project.py"
        fake_ap.write_text(
            "import sys\n"
            f"open(r'{log}', 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
            "if 'flow-participate' in sys.argv:\n"
            "    print('[{\"run_id\": \"req-1\"}]')\n",
            encoding="utf-8")
        root = self.tmp / "flowproj"
        root.mkdir()
        host = km.HostConfig({"node_id": "pc-a",
                              "projects": [{"name": "fp", "root": str(root)}]})
        status = km.EngineStatus("pc-a")
        pool = km.NodeWorkerPool(4)
        with mock.patch.object(km, "_self_script", return_value=str(fake_ap)):
            km._flow_participate_tick(host, pool, status)
            for _ in range(50):        # プールの投入スレッドが argv を書くまで軽く待つ
                if log.exists() and len(log.read_text().splitlines()) >= 2:
                    break
                time.sleep(0.02)
        calls = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 2)
        self.assertIn("flow-participate", calls[0])
        self.assertIn("flow-run", calls[1])
        self.assertIn("req-1", calls[1])
        self.assertEqual(status.recent_errors, [])

    def test_flow_participate_tick_reports_runs_it_already_drives(self):
        """走っている / 起動待ちの run は `--running` で申告する。申告しないと participate 側が
        毎周それを『駆動者が居ない孤児』と読んで再開回数を焼き、上限で failed に確定する。"""
        log = self.tmp / "flow-running.log"
        fake_ap = self.tmp / "fake_agent_project2.py"
        fake_ap.write_text(
            "import sys\n"
            f"open(r'{log}', 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
            "print('[]')\n",
            encoding="utf-8")
        root = self.tmp / "flowproj2"
        root.mkdir()
        host = km.HostConfig({"node_id": "pc-a",
                              "projects": [{"name": "fp", "root": str(root)}]})
        status = km.EngineStatus("pc-a")
        pool = km.NodeWorkerPool(1)
        started = threading.Event()
        release = threading.Event()
        pool.submit(km.WorkItem(id="flow/fp/req-A",
                                run=lambda: (started.set(), release.wait(5))))
        pool.submit(km.WorkItem(id="flow/fp/req-B", run=lambda: None))   # 枠が無いのでキュー
        self.assertTrue(started.wait(5))
        try:
            with mock.patch.object(km, "_self_script", return_value=str(fake_ap)):
                km._flow_participate_tick(host, pool, status)
        finally:
            release.set()
        argv = log.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("--running", argv)
        # 実行中（req-A）だけでなく起動待ち（req-B）も申告する
        self.assertIn("req-A,req-B", argv)
        # 投入 id の接頭辞（flow/<project>/）は剥がして run-id だけを渡す
        self.assertNotIn("flow/fp/", argv)

    def test_flow_tick_isolates_participate_subprocess_failure(self):
        # 設計 §4.2 の実行規約「例外は tick 内に隔離しループを殺さない」。
        root = self.tmp / "flowproj3"
        root.mkdir()
        host = km.HostConfig({"node_id": "pc-a",
                              "projects": [{"name": "fp3", "root": str(root)}]})
        sup, sched, status, write_status, pool = km._build_resident(host, start_children=False)
        missing = str(self.tmp / "no-such-agent-project")
        with mock.patch.object(km, "_self_script", return_value=missing):
            [t for t in sched._ticks if t.name == "flow"][0].fn()   # 例外を投げないこと自体が検証
        self.assertTrue(status.recent_errors)
        self.assertIn("flow participate", status.recent_errors[-1])

    def _fake_agent_flow(self, log: Path, name: str, emit: str = "[]") -> Path:
        """`agent-flow` の代わりに argv を記録する偽物（participate は run-id を返す）。"""
        p = self.tmp / name
        p.write_text(
            "import sys\n"
            f"open(r'{log}', 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
            "if 'participate' in sys.argv:\n"
            f"    print({emit!r})\n",
            encoding="utf-8")
        return p

    def test_node_direct_tick_takes_board_work_on_a_worker_node(self):
        # R2b: プロジェクトを 1 つも持たない端末が、板の仕事を落札してノードのバスで実行する。
        # 受理（participate）と実行（run）を分ける配線はプロジェクト経路と同じ。
        log = self.tmp / "node-flow.log"
        fake = self._fake_agent_flow(log, "fake_flow.py", emit='[{"run_id": "dg-1"}]')
        host = km.HostConfig({"node_id": "pc-a", "board": str(self.tmp / "board"),
                              "agent_cli": ["kiro"]})
        self.assertTrue(host.is_worker)
        status = km.EngineStatus("pc-a")
        pool = km.NodeWorkerPool(4)
        with mock.patch.object(km, "resolve_agent_flow", return_value=[sys.executable, str(fake)]):
            km._node_direct_flow_tick(host, pool, status)
            for _ in range(50):
                if log.exists() and len(log.read_text().splitlines()) >= 2:
                    break
                time.sleep(0.02)
        calls = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 2)
        self.assertIn("participate", calls[0])
        self.assertIn("--board", calls[0])                 # 板の場所は host.yaml が正典
        self.assertIn("--node-id pc-a", calls[0])
        self.assertIn(km.node_flow_bus_dir(), calls[0])    # 取り込み先はノードのバス
        self.assertIn("--agent-cli kiro", calls[0])        # 実行 CLI はノードの宣言から
        self.assertIn("run --from-inbox", calls[1])        # 実行は落札した run を続きから
        self.assertIn("dg-1", calls[1])
        self.assertEqual(status.recent_errors, [])

    def test_node_direct_tick_is_silent_on_a_full_node(self):
        # ロール分岐は「プロジェクト子を起動しない」の 1 点に保つ（設計 §9 C12）。
        # フルノードにはプロジェクト経由の取り込み経路があるので、2 つ目の主体を置かない。
        log = self.tmp / "node-flow-full.log"
        fake = self._fake_agent_flow(log, "fake_flow_full.py")
        root = self.tmp / "fullproj"
        root.mkdir()
        host = km.HostConfig({"node_id": "pc-a", "board": str(self.tmp / "board"),
                              "projects": [{"name": "fp", "root": str(root)}]})
        status = km.EngineStatus("pc-a")
        with mock.patch.object(km, "resolve_agent_flow", return_value=[sys.executable, str(fake)]):
            km._node_direct_flow_tick(host, km.NodeWorkerPool(4), status)
        self.assertFalse(log.exists())

    def test_node_direct_tick_needs_a_board(self):
        log = self.tmp / "node-flow-noboard.log"
        fake = self._fake_agent_flow(log, "fake_flow_nb.py")
        host = km.HostConfig({"node_id": "pc-a"})
        with mock.patch.object(km, "resolve_agent_flow", return_value=[sys.executable, str(fake)]):
            km._node_direct_flow_tick(host, km.NodeWorkerPool(4), km.EngineStatus("pc-a"))
        self.assertFalse(log.exists())

    def test_node_direct_tick_reports_runs_it_already_drives(self):
        # 走っている run を申告しないと、participate が毎周それを孤児と読んで再開回数を焼く。
        log = self.tmp / "node-flow-running.log"
        fake = self._fake_agent_flow(log, "fake_flow_run.py")
        host = km.HostConfig({"node_id": "pc-a", "board": str(self.tmp / "board")})
        pool = km.NodeWorkerPool(1)
        started, release = threading.Event(), threading.Event()
        pool.submit(km.WorkItem(id="node/dg-A", run=lambda: (started.set(), release.wait(5))))
        pool.submit(km.WorkItem(id="node/dg-B", run=lambda: None))     # 枠が無いのでキュー
        self.assertTrue(started.wait(5))
        try:
            with mock.patch.object(km, "resolve_agent_flow",
                                   return_value=[sys.executable, str(fake)]):
                km._node_direct_flow_tick(host, pool, km.EngineStatus("pc-a"))
        finally:
            release.set()
        argv = log.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("--running dg-A,dg-B", argv)     # 実行中も起動待ちも申告する
        self.assertNotIn("node/dg-A", argv)            # 投入 id の接頭辞は剥がす

    def test_node_direct_tick_isolates_participate_failure(self):
        host = km.HostConfig({"node_id": "pc-a", "board": str(self.tmp / "board")})
        status = km.EngineStatus("pc-a")
        missing = str(self.tmp / "no-such-agent-flow")
        with mock.patch.object(km, "resolve_agent_flow", return_value=[missing]):
            km._node_direct_flow_tick(host, km.NodeWorkerPool(4), status)
        self.assertTrue(status.recent_errors)
        self.assertIn("node flow participate", status.recent_errors[-1])

    def test_board_status_declares_node_direct_execution(self):
        # dashboard の手動入札ボタンはこの旗（か intake_projects）を根拠にする。
        worker = km.HostConfig({"node_id": "pc-a", "board": str(self.tmp / "b1")})
        status = km.EngineStatus("pc-a")
        km._board_participate_tick(worker, status)
        self.assertTrue(status.board["node_direct"])
        self.assertEqual(status.board["intake_projects"], [])
        root = self.tmp / "fullproj2"
        root.mkdir()
        full = km.HostConfig({"node_id": "pc-a", "board": str(self.tmp / "b2"),
                              "projects": [{"name": "fp", "root": str(root)}]})
        status2 = km.EngineStatus("pc-a")
        km._board_participate_tick(full, status2)
        self.assertFalse(status2.board["node_direct"])     # フルノードは従来の経路
        self.assertEqual(status2.board["intake_projects"], ["fp"])

    def test_gc_tick_isolates_project_sweeper_failure(self):
        # 1 プロジェクトの gc 失敗が他プロジェクトの gc・常駐体本体を止めないこと
        # （run_gc の隔離契約・実装計画 W1-12 のカオステスト観点）。
        broken = self.tmp / "broken-proj"
        broken.mkdir()
        cfg_path = broken / "agent-project.yaml"
        cfg_path.write_text(json.dumps({"agent_flow": str(self.tmp / "no-such-agent-flow")}),
                            encoding="utf-8")
        host = km.HostConfig({"node_id": "pc-a",
                             "projects": [{"name": "broken", "root": str(broken),
                                          "config": str(cfg_path)}]})
        sup, sched, status, write_status, pool = km._build_resident(host, start_children=False)
        [t for t in sched._ticks if t.name == "gc"][0].fn()   # 例外を投げないこと自体が検証
        self.assertTrue(status.recent_errors)
        self.assertIn("gc broken", status.recent_errors[-1])


class GcCommandTests(unittest.TestCase):
    """`agent-project gc`（実装計画 W1-11 残・設計 §4.2 node 層 gc tick の実体）: 掃除の実装は
    持たず、`agent-flow cleanup`/`agent-flow gc` を単発起動するだけ（R1）。委譲先の実引数は
    フェイク agent-flow（argv を記録するだけのスタブ）で検証する——実バイナリは要らない。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "argv.log"

    def _fake_flow(self, gc_marker_count: int = 2) -> str:
        # cleanup --json には掃除件数の JSON を、gc には "削除: " 行を marker 件数だけ返す。
        wrapper = self.tmp / "fake_agent_flow.py"
        wrapper.write_text(
            "import sys, json\n"
            "argv = sys.argv[1:]\n"
            f"open(r'{self.log}', 'a').write(' '.join(argv) + chr(10))\n"
            "if 'cleanup' in argv:\n"
            "    print(json.dumps({'locks': 1, 'tmp': 2, 'clones': 0, 'work_repos': 0, 'cache': 0}))\n"
            "elif 'gc' in argv:\n"
            f"    print('削除: r1\\n' * {gc_marker_count}, end='')\n",
            encoding="utf-8")
        return str(wrapper)

    def _cfg(self, **kw):
        return cfg_for(self.tmp, agent_flow=self._fake_flow(), **kw)

    def test_local_bus_runs_cleanup_only(self):
        # git_bus 無し = _cleanup_bus（loop.py）が自前で archive を掃除する構成 →
        # gc は cleanup（ロック/tmp/クローン）だけ呼び、archive gc は呼ばない（二重掃除しない）。
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = km.cmd_gc(self._cfg(), json_out=True)
        self.assertEqual(rc, 0)
        totals = json.loads(buf.getvalue())
        self.assertEqual(totals["cleanup.locks"], 1)
        self.assertNotIn("gc.deleted", totals)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 1)
        self.assertIn("cleanup", calls[0])

    def test_git_bus_also_runs_archive_gc(self):
        # git_bus 構成では _cleanup_bus が触らない分、archive gc もここで担う。
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = km.cmd_gc(self._cfg(git_bus="https://example.invalid/bus.git"), json_out=True)
        self.assertEqual(rc, 0)
        totals = json.loads(buf.getvalue())
        self.assertEqual(totals["cleanup.locks"], 1)
        self.assertEqual(totals["gc.deleted"], 2)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("cleanup" in c for c in calls))
        self.assertTrue(any(" gc " in f" {c} " for c in calls))

    def test_state_repo_alone_does_not_run_archive_gc(self):
        # 旧 `state_git` 構成の素通り（→ ここで archive gc を担う）は S1 で廃止した。
        # 状態ルートは常に状態リポジトリの clone なので、その条件は「常に真」＝ _cleanup_bus が
        # 永久に走らなくなる。掃除は _cleanup_bus が keep 件数を守って行い、gc は二重に消さない。
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = km.cmd_gc(self._cfg(state_repo="https://example.invalid/state.git"), json_out=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("gc.deleted", json.loads(buf.getvalue()))

    def test_cli_gc_subcommand_dispatches(self):
        wrapper = self._fake_flow()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = km.main(["gc", "--root", str(self.tmp), "--agent-flow", wrapper, "--json"])
        self.assertEqual(rc, 0)
        self.assertIn("cleanup.locks", buf.getvalue())


class ResidentBoardTickTests(unittest.TestCase):
    """板 tick（R2a・S8）: 板の同期・ノード能力宣言の書き出し・ノード宛て指示の取り込み。

    **入札の自動判断はここでは行わない**（自動入札は各ツールの participate が担う）。
    ここが書く入札は「人が押した」分だけ——同じノードに 2 つ目の入札主体を置くと
    二重落札を防ぐ規則が 2 実装になる。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="resident-board-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.agents_home = self.tmp / "agents-home"
        os.environ["AGENT_PROJECT_AGENTS_HOME"] = str(self.agents_home)
        self.addCleanup(os.environ.pop, "AGENT_PROJECT_AGENTS_HOME", None)
        self.commands = self.tmp / "node-commands"
        os.environ["AGENT_COMMANDS_DIR"] = str(self.commands)
        self.addCleanup(os.environ.pop, "AGENT_COMMANDS_DIR", None)
        os.environ["AGENT_BUDGET_DIR"] = str(self.tmp / "budget")
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)
        self.board = self.tmp / "board"
        (self.board / "delegations").mkdir(parents=True)

    def _host(self, **kw):
        data = {"node_id": "pc-a", "board": str(self.board), "tags": ["python"],
                "agent_cli": ["codex"],
                "repos": [{"url": "https://git.example.com/team/app.git",
                           "local": "/home/me/mirrors/app"}]}
        data.update(kw)
        return km.HostConfig(data)

    def _post(self, did, **kw):
        d = self.board / "delegations" / did
        d.mkdir(parents=True, exist_ok=True)
        (d / "post.json").write_text(json.dumps(
            {"op": "post", "version": 1, "id": did, "workload": "flow", "goal": "実装", **kw}),
            encoding="utf-8")
        return d

    def _drop(self, name, rec):
        self.commands.mkdir(parents=True, exist_ok=True)
        (self.commands / name).write_text(json.dumps(rec), encoding="utf-8")
        return self.commands / name

    def _tick(self, host=None):
        status = km.EngineStatus("pc-a")
        km._board_participate_tick(host or self._host(), status)
        return status

    # --- ノード能力宣言（P1-a / S3-5） ---------------------------------------

    def test_writes_node_capability_with_repo_urls(self):
        self._tick()
        rec = json.loads((self.board / "nodes" / "pc-a.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["node"], "pc-a")
        self.assertEqual(rec["tags"], ["python"])
        self.assertEqual(rec["agent_cli"], ["codex"])
        self.assertEqual(rec["repos"], [{"url": "https://git.example.com/team/app.git"}])
        self.assertEqual(rec["contract_version"], km.CONTRACT_VERSION)
        self.assertTrue(rec["heartbeat"])
        self.assertGreater(rec["fresh_after_sec"], 0)
        # Phase1: status と同形の budget ミラー。台帳が読めるのに unavailable へ
        # 倒れていたら退行（builder へ渡す cfg スタブの属性欠け）。
        budget = rec["budget"]
        self.assertEqual(budget["contract_version"], 1)
        self.assertEqual(budget["source"], "local-ledger")
        self.assertIn("can_accept", budget)
        self.assertIs(budget.get("enforce"), False)
        # reservation はプロジェクト状態リポジトリ単位。板ミラーには文脈が無いので未知(null)。
        self.assertIsNone(budget["capacity"]["reserved"])

    def test_board_absent_tick_does_not_require_budget_mirror(self):
        """board 未設定経路は nodes を書かず、status.board.configured=False で終わる。"""
        status = self._tick(self._host(board=""))
        self.assertEqual(status.board, {"configured": False})
        self.assertFalse((self.board / "nodes" / "pc-a.json").exists())

    def test_local_clone_paths_are_not_published_to_the_board(self):
        """板は共有リポジトリ＝置いた値は全 PC へ配られる。`local` はホスト固有の絶対パスで、
        `repos.schema.json` が「共有レジストリには置けない」と宣言しているもの（P2-2）。

        注: budget.source=`local-ledger` は別語彙（射影の出所）。全体文字列の `local` 部分一致は見ない。
        """
        self._tick()
        raw = (self.board / "nodes" / "pc-a.json").read_text(encoding="utf-8")
        self.assertNotIn("/home/me/mirrors/app", raw)
        rec = json.loads(raw)
        for entry in rec.get("repos") or []:
            self.assertNotIn("local", entry)

    def test_local_declaration_still_reaches_the_workspace(self):
        """板から落としても速度最適化は効く（＝落としてよい根拠）。請負ノードは**自分の**
        host.yaml から解決する（`agentcore.repolocal.merge_local`）ので、板は経路に無い。"""
        host = self._host()
        merged = km._repolocal.merge_local({"url": "https://git.example.com/team/app.git"},
                                           host.repos)
        self.assertEqual(merged.get("local", ""), "")   # 実体が無いので解決はしない
        # 宣言そのものは残っている（require_dir=False なら宣言だけを引ける）
        self.assertEqual(
            km._repolocal.resolve_local("https://git.example.com/team/app.git", host.repos,
                                        require_dir=False),
            "/home/me/mirrors/app")

    def test_repo_url_declaration_still_matches_a_post(self):
        """`local` を落としても入札の照合は変わらない（照合は url ベース）。"""
        cap = km._node_capability(self._host())
        post = {"workspace": {"url": "https://git.example.com/team/app"}}
        self.assertTrue(km._boardrules.eligible(post, repos=cap["repos"], tags=cap["tags"],
                                                agent_cli=cap["agent_cli"]))

    # --- 引き受けるエンジンと枠（P2-3） --------------------------------------

    def test_workloads_are_not_published_when_undeclared(self):
        """宣言していないものを宣言として配らない。板の語彙では「空 = 全部」で、
        キーが無いことと同義（導出値を載せると owner-picks の判断が嘘を読む）。"""
        self._tick()
        rec = json.loads((self.board / "nodes" / "pc-a.json").read_text(encoding="utf-8"))
        self.assertNotIn("workloads", rec)

    def test_declared_workloads_are_published(self):
        self._tick(self._host(workloads=["flow"]))
        rec = json.loads((self.board / "nodes" / "pc-a.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["workloads"], ["flow"])

    def test_max_concurrent_declaration_reaches_the_board(self):
        """未宣言 = 既定 4 / 明示 0 = 無制限（スキーマの語彙）/ n = n（P2-3）。"""
        self.assertEqual(km._effective_max_concurrent(self._host()), 4)
        self.assertEqual(
            km._effective_max_concurrent(self._host(budget={"max_concurrent": 0})), 0)
        self.assertEqual(
            km._effective_max_concurrent(self._host(budget={"max_concurrent": 2})), 2)
        self._tick(self._host(budget={"max_concurrent": 0}))
        rec = json.loads((self.board / "nodes" / "pc-a.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["max_concurrent"], 0, "0 は無制限としてそのまま宣言する")

    def _control(self, ctl):
        """agent-control の宣言（dashboard が書く control.json）をこのテスト用に置く。"""
        prev = os.environ["AGENT_CONTROL_DIR"]
        os.environ["AGENT_CONTROL_DIR"] = str(self.tmp)
        self.addCleanup(os.environ.__setitem__, "AGENT_CONTROL_DIR", prev)
        (self.tmp / "control.json").write_text(json.dumps(ctl), encoding="utf-8")
        km._CONTROL_CACHE["mtime"] = None
        self.addCleanup(km._CONTROL_CACHE.__setitem__, "mtime", None)

    def test_control_declaration_overrides_host_yaml(self):
        """dashboard の「同時に動かす数」が agent-project 経由の agent-flow にも効く。
        常駐一本化で agent-flow daemon が消えた後、本数を律速しているのはこの枠。"""
        self._control({"workloads": {"flow": {"concurrency": {"max_runs": 2}}}})
        self.assertEqual(km._effective_max_concurrent(self._host()), 2)
        self.assertEqual(
            km._effective_max_concurrent(self._host(budget={"max_concurrent": 8})), 2)
        # 板へ宣言する値も同じ関数から出る（「板には 8 と言って手元は 2」を作らない）
        self._tick(self._host(budget={"max_concurrent": 8}))
        rec = json.loads((self.board / "nodes" / "pc-a.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["max_concurrent"], 2)

    def test_control_broken_value_falls_back_to_host_yaml(self):
        """GUI の入力ミス（負数・数値でない）で枠が壊れるより、上書きが効かない方が安い。"""
        self._control({"workloads": {"flow": {"concurrency": {"max_runs": -1}}}})
        self.assertEqual(km._effective_max_concurrent(self._host(budget={"max_concurrent": 3})), 3)
        self._control({"workloads": {"flow": {"concurrency": {"max_runs": "たくさん"}}}})
        self.assertEqual(km._effective_max_concurrent(self._host()), 4)

    def test_heartbeat_only_updates_are_rate_limited(self):
        # 30 秒 tick のたびに心拍を書き換えると板に無意味なコミットが積む。
        self._tick()
        path = self.board / "nodes" / "pc-a.json"
        first = path.read_text(encoding="utf-8")
        for _ in range(5):
            self._tick()
        self.assertEqual(path.read_text(encoding="utf-8"), first)

    def test_rate_limit_survives_a_second_boundary(self):
        """秒をまたいでも間引きが効くこと。

        上の tick 越しの検査は、5 回が同じ秒に収まると素通りする（実際 CI で秒をまたいだ
        ときだけ落ちた）。ここは時刻を作って決定的に押さえる。**heartbeat を外すだけでは
        足りない**——budget.observed_at が同じ時刻の写しで、外し忘れると秒をまたぐたび
        「内容が変わった」と判定され、tick は 30 秒間隔なので間引きが一度も効かない。
        """
        now = datetime.now(timezone.utc)

        def cap(offset):
            stamp = (now + timedelta(seconds=offset)).isoformat(
                timespec="seconds").replace("+00:00", "Z")
            return {"node": "pc-a", "tags": ["python"], "heartbeat": stamp,
                    "fresh_after_sec": 1200.0,
                    "budget": {"contract_version": 1, "observed_at": stamp,
                               "source": "local-ledger"}}

        board = km.BoardRepo(str(self.board))
        self.assertTrue(board.write_node(cap(0), heartbeat_interval=300.0), "初回は書く")
        self.assertFalse(board.write_node(cap(1), heartbeat_interval=300.0),
                         "秒をまたいだだけでは書かない")
        self.assertFalse(board.write_node(cap(30), heartbeat_interval=300.0),
                         "30 秒後の tick でも書かない")

    def test_declaration_change_is_written_immediately(self):
        # 内容が変わったときは間隔に関わらず書く（宣言の変更が反映されない方が害が大きい）。
        self._tick()
        self._tick(self._host(tags=["python", "gpu"]))
        rec = json.loads((self.board / "nodes" / "pc-a.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["tags"], ["python", "gpu"])

    def test_worker_node_declares_no_intake_projects(self):
        status = self._tick()
        self.assertEqual(status.board["intake_projects"], [],
                         "プロジェクトを持たないノードは落札しても行き先が無い")
        self.assertTrue(status.board["configured"])

    def test_full_node_declares_intake_projects(self):
        root = self.tmp / "proj"
        root.mkdir()
        status = self._tick(self._host(projects=[{"name": "proj-a", "root": str(root)}]))
        self.assertEqual(status.board["intake_projects"], ["proj-a"])

    def test_status_declares_board_workdir(self):
        """板の**作業ディレクトリ**も状況に載せる（P0-2）。

        dashboard が設定で持っているのは作業フォルダで、指示ドロップの `board:` に要るのは
        所在（`host.board`）。両方を載せておかないと、画面は作業フォルダを載せるしかなく、
        `_ingest_node_commands` の完全一致検査に必ず引っかかって全指示が .err へ落ちる。"""
        status = self._tick()
        self.assertEqual(status.board["location"], str(self.board))
        self.assertEqual(status.board["workdir"], os.path.abspath(str(self.board)))

    def test_no_board_configured_is_reported_not_crashed(self):
        status = km.EngineStatus("pc-a")
        km._board_participate_tick(km.HostConfig({"node_id": "pc-a"}), status)
        self.assertEqual(status.board, {"configured": False})

    def test_status_counts_open_delegations_and_own_bids(self):
        self._post("dg-1")
        self._post("dg-2")
        self._drop("01-bid.json", {"command": "board-bid", "id": "dg-2"})
        status = self._tick()
        self.assertEqual(status.board["open_delegations"], 2)
        self.assertEqual(status.board["my_bids"], ["dg-2"])

    # --- ノード宛て指示の取り込み（S8-2 / S8-3） -----------------------------

    def test_board_bid_writes_claim_and_receipt(self):
        self._post("dg-1")
        f = self._drop("01-bid.json", {"command": "board-bid", "id": "dg-1",
                                       "issued_by": "agent-dashboard"})
        self._tick()
        bid = json.loads((self.board / "delegations" / "dg-1" / "bids" / "pc-a.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(bid["who"], "pc-a")
        self.assertGreater(bid["lease_until"], time.time())
        self.assertFalse(f.exists(), "取り込んだ指示は消える")
        receipt = json.loads((self.commands / "processed" / "01-bid.json")
                             .read_text(encoding="utf-8"))
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["action"], "board-bid")
        self.assertEqual(receipt["id"], "dg-1")

    def test_board_cancel_writes_marker_with_author(self):
        self._post("dg-1")
        self._drop("01-cancel.json", {"command": "board-cancel", "id": "dg-1",
                                      "reason": "不要になった"})
        self._tick()
        rec = json.loads((self.board / "delegations" / "dg-1" / "cancelled.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(rec["reason"], "不要になった")
        self.assertEqual(rec["cancelled_by"], "pc-a")

    def test_board_award_requires_node(self):
        self._post("dg-1")
        self._drop("01-award.json", {"command": "board-award", "id": "dg-1"})
        self._tick()
        err = json.loads((self.commands / "01-award.json.err").read_text(encoding="utf-8"))
        self.assertIn("node", err["error"])

    def test_board_award_writes_award(self):
        self._post("dg-1")
        self._drop("01-award.json", {"command": "board-award", "id": "dg-1", "node": "pc-b"})
        self._tick()
        rec = json.loads((self.board / "delegations" / "dg-1" / "award.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(rec["node"], "pc-b")

    def test_terminal_delegation_is_rejected_with_reason(self):
        d = self._post("dg-1")
        (d / "result.json").write_text(json.dumps({"winner": "pc-b"}), encoding="utf-8")
        self._drop("01-bid.json", {"command": "board-bid", "id": "dg-1"})
        self._tick()
        err = json.loads((self.commands / "01-bid.json.err").read_text(encoding="utf-8"))
        self.assertIn("終端", err["error"])
        self.assertEqual(err["command"]["id"], "dg-1")

    def test_unknown_delegation_is_rejected(self):
        self._drop("01-bid.json", {"command": "board-bid", "id": "dg-nope"})
        self._tick()
        err = json.loads((self.commands / "01-bid.json.err").read_text(encoding="utf-8"))
        self.assertIn("公示がありません", err["error"])

    def test_unknown_command_is_rejected(self):
        self._drop("01-x.json", {"command": "board-explode", "id": "dg-1"})
        self._tick()
        self.assertTrue((self.commands / "01-x.json.err").exists())

    def test_command_for_another_board_is_not_applied_here(self):
        # 別の板宛ての指示を、このノードが宣言している板へ黙って適用しない。
        self._post("dg-1")
        self._drop("01-bid.json", {"command": "board-bid", "id": "dg-1",
                                   "board": "git+ssh://elsewhere/board.git"})
        self._tick()
        self.assertFalse((self.board / "delegations" / "dg-1" / "bids").exists())
        err = json.loads((self.commands / "01-bid.json.err").read_text(encoding="utf-8"))
        self.assertIn("一致しません", err["error"])

    def _age(self, name: str, seconds: float) -> None:
        """ファイルの mtime を過去へずらす（書きかけ猶予を過ぎた状態を作る）。"""
        p = self.commands / name
        old = time.time() - seconds
        os.utime(p, (old, old))

    def test_broken_command_file_is_quarantined_not_retried_forever(self):
        self.commands.mkdir(parents=True, exist_ok=True)
        (self.commands / "01-broken.json").write_text('{"command": "board-', encoding="utf-8")
        # 書きかけかもしれないので、猶予のあいだは .err にしない（P1-4）。
        self._tick()
        self.assertFalse((self.commands / "01-broken.json.err").exists(),
                         "書きかけ猶予の内側で .err へ飛ばしている")
        self._age("01-broken.json", km._NODE_COMMAND_DEBOUNCE_SEC + 1)
        status = self._tick()
        self.assertTrue((self.commands / "01-broken.json.err").exists())
        self.assertTrue(any("node command" in e for e in status.recent_errors))

    def test_deferred_command_holds_back_the_ones_behind_it(self):
        """書きかけを飛ばして後続を処理しない（P1-4）。

        指示はファイル名の時刻順＝処理順が規約。同じ公示への「入札 → 中止」を飛び越えると
        中止済みの板へ入札を書くことになる（順序が壊れると板の状態が巻き戻って見える）。"""
        self._post("dg-1")
        self.commands.mkdir(parents=True, exist_ok=True)
        (self.commands / "01-bid.json").write_text('{"command": "board-', encoding="utf-8")
        self._drop("02-cancel.json", {"command": "board-cancel", "id": "dg-1"})
        self._tick()
        self.assertFalse((self.board / "delegations" / "dg-1" / "cancelled.json").exists(),
                         "前の指示を待たずに後続を実行している")
        self._age("01-bid.json", km._NODE_COMMAND_DEBOUNCE_SEC + 1)
        self._tick()
        self.assertTrue((self.commands / "01-bid.json.err").exists())
        self.assertTrue((self.board / "delegations" / "dg-1" / "cancelled.json").exists())

    def test_rejects_are_recorded_in_engine_status(self):
        """`.err` に落ちた指示は必ず状況にも残る（P1-4）。

        `engine/status.json` の recent_errors は画面の唯一の横断ビュー。ここに出ないと、
        板不一致・未知指示・公示不在で落ちた指示は .err を直接開くまで誰にも見えない。"""
        for name, rec in (("01-x.json", {"command": "board-explode", "id": "dg-1"}),
                          ("02-bid.json", {"command": "board-bid", "id": "dg-nope"}),
                          ("03-bid.json", {"command": "board-bid", "id": "dg-1",
                                           "board": "git+ssh://elsewhere/board.git"})):
            self._drop(name, rec)
        status = self._tick()
        self.assertEqual(sum(1 for e in status.recent_errors if "node command" in e), 3)

    def test_success_clears_the_previous_error_for_the_same_delegation(self):
        """同じ委譲 id で通ったら古い `.err` を消す（P1-4）。

        画面の失敗バナーは id 単位なので、残すと「直ったのに失敗表示」が出続ける。"""
        self._drop("01-bid.json", {"command": "board-bid", "id": "dg-1"})   # 公示がまだ無い
        self._tick()
        self.assertTrue((self.commands / "01-bid.json.err").exists())
        self._post("dg-1")
        self._drop("02-bid.json", {"command": "board-bid", "id": "dg-1"})
        self._tick()
        self.assertFalse((self.commands / "01-bid.json.err").exists())

    def test_other_delegations_errors_survive(self):
        # 掃除は id 単位。別の公示の失敗まで消すと、失敗が誰にも見えない元の不具合に戻る。
        self._drop("01-bid.json", {"command": "board-bid", "id": "dg-other"})
        self._tick()
        self._post("dg-1")
        self._drop("02-bid.json", {"command": "board-bid", "id": "dg-1"})
        self._tick()
        self.assertTrue((self.commands / "01-bid.json.err").exists())

    def test_gc_prunes_stale_error_files(self):
        """`.err` の期限切れ掃除（P1-4）。公示が終端すると同じ id の指示は二度と来ない＝
        `clear_rejected` では消えないので、gc が期限で掃く。"""
        self._drop("01-bid.json", {"command": "board-bid", "id": "dg-nope"})
        self._tick()
        err = self.commands / "01-bid.json.err"
        self.assertTrue(err.exists())
        old = time.time() - (km._cmddrop.REJECTED_TTL_SEC + 60)
        os.utime(err, (old, old))
        with mock.patch.object(km, "node_commands_dir", return_value=self.commands):
            swept = km._sweep_node_commands()
        self.assertEqual(swept.get("commands.err"), 1)
        self.assertFalse(err.exists())

    def test_commands_are_processed_in_name_order(self):
        # 同じ公示への「入札 → 中止」が入れ替わると、中止済みの板へ入札を書くことになる。
        self._post("dg-1")
        self._drop("01-bid.json", {"command": "board-bid", "id": "dg-1"})
        self._drop("02-cancel.json", {"command": "board-cancel", "id": "dg-1"})
        self._tick()
        self.assertTrue((self.board / "delegations" / "dg-1" / "bids" / "pc-a.json").exists())
        self.assertTrue((self.board / "delegations" / "dg-1" / "cancelled.json").exists())

    def test_board_failure_is_recorded_not_raised(self):
        # 板が壊れていても tick を落とさない（Scheduler が隔離すると復帰しても治らない）。
        host = self._host(board=str(self.tmp / "nope") + "/x")
        status = km.EngineStatus("pc-a")
        with mock.patch.object(km, "BoardRepo", side_effect=OSError("板に到達できません")):
            km._board_participate_tick(host, status)
        self.assertTrue(status.board["configured"])
        self.assertIn("板に到達できません", status.board["last_error"])

    def test_engine_status_carries_board_block(self):
        status = self._tick()
        self.assertIn("board", status.to_dict())
        self.assertEqual(status.to_dict()["board"]["location"], str(self.board))


class HostConfigValidationTests(unittest.TestCase):
    """host.yaml トップレベルの検査（P1-3）。

    プロジェクト yaml 側には層検査（S1 の E1/E2）と未知キー警告があるのに、host.yaml の
    トップレベルは誰も見ていなかった——`plan_review: false` を書いても `node_id` を
    `nodeid` と綴り間違えても、警告ゼロで黙って無視される。「設定したのに効かないことに
    気付けない」という S1 の設計動機が、host.yaml 側だけ抜けていた。

    **警告どまりで起動は止めない**（既存の host.yaml でフリート全台を落とさない）。
    """

    def _findings(self, data: dict) -> "list[str]":
        return km.host_config_findings(data)

    def test_clean_example_has_no_findings(self):
        """同梱の host.yaml.example の全キーで所見ゼロ（既知キー表の取りこぼし検出）。"""
        data = {"schema_version": 1, "node_id": "pc-a", "defaults": {"agent_cli": "codex"},
                "projects": [{"name": "p", "state_repo": "https://x/y.git", "branch": "main",
                              "root": "/tmp/p", "overrides": {"model": "m"}}],
                "repos": [{"url": "https://x/app.git", "local": "/tmp/app"}],
                "tags": [], "agent_cli": [], "board": "", "board_workdir": None,
                "amigos_bus": "", "amigos_config": None, "budget": {"max_concurrent": 0},
                "update": {"enabled": True}, "availability": {"daily_stop": "23:30"},
                "residency": "auto"}
        self.assertEqual(self._findings(data), [])

    def test_typo_is_reported_with_a_suggestion(self):
        got = self._findings({"nodeid": "pc-a"})
        self.assertEqual(len(got), 1)
        self.assertIn("nodeid", got[0])
        self.assertIn("node_id", got[0], "近い既知キーを示さないと直しようがない")

    def test_project_key_at_the_top_level_points_to_its_home(self):
        # 「未知のキー」だけだと、正しい置き場が分からないまま消すか放置するかになる。
        got = self._findings({"plan_review": False})
        self.assertIn("agent-project.yaml", got[0])
        got = self._findings({"model": "opus"})
        self.assertIn("defaults", got[0], "共有キーは defaults: の下")
        got = self._findings({"update_enabled": False})
        self.assertIn("update", got[0])
        got = self._findings({"state_repo": "https://x/y.git"})
        self.assertIn("projects[]", got[0])

    def test_removed_worktree_key_is_named(self):
        got = self._findings({"state_worktree_dir": "/tmp/x"})
        self.assertIn("廃止", got[0])

    def test_scalar_agent_cli_is_folded_not_exploded(self):
        """`agent_cli: codex`（スカラ）が 1 文字ずつの配列にならないこと（P1-3）。

        素朴な `[str(a) for a in raw]` は文字列を文字へ分解する。板の `nodes/<id>.json` に
        `["c","o","d","e","x"]` が publish され、`requires.agent_cli` を持つ公示に永久に
        入札しない——入札選別は fail-close なので、誤動作ではなく無言の不参加になる。"""
        host = km.HostConfig({"agent_cli": "codex", "tags": "urgent"})
        self.assertEqual(host.agent_cli, ["codex"])
        self.assertEqual(host.tags, ["urgent"])
        got = self._findings({"agent_cli": "codex", "tags": "urgent"})
        self.assertEqual(len(got), 2, "黙って直すと「配列で書かなくても動く」と思い込ませる")

    def test_scalar_agent_cli_still_matches_a_post(self):
        # 板への波及（回帰）: 修正前は eligible が False になっていた。
        host = km.HostConfig({"node_id": "pc-a", "agent_cli": "codex", "tags": "urgent"})
        cap = km._node_capability(host)
        self.assertEqual(cap["agent_cli"], ["codex"])
        post = {"requires": {"agent_cli": ["codex"], "tags": ["urgent"]}}
        self.assertTrue(km._boardrules.eligible(post, repos=None, tags=cap["tags"],
                                                agent_cli=cap["agent_cli"]))

    def test_wrong_shapes_are_reported(self):
        self.assertTrue(any("defaults" in f for f in self._findings({"defaults": ["a"]})))
        self.assertTrue(any("projects" in f for f in self._findings({"projects": {"a": 1}})))

    def test_unknown_project_entry_key(self):
        got = self._findings({"projects": [{"root": "/tmp/p", "config": "x.yaml"}]})
        self.assertEqual(len(got), 1)
        self.assertIn("agent-project.yaml", got[0], "S1 の E6（設定は状態リポジトリ直下）")

    def test_warning_is_emitted_once_per_path(self):
        tmp = Path(tempfile.mkdtemp(prefix="host-warn-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "agent-project.host.json"
        path.write_text(json.dumps({"nodeid": "pc-a"}), encoding="utf-8")
        km._HOST_WARNED.discard(str(path))
        self.addCleanup(km._HOST_WARNED.discard, str(path))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km.load_host_config(str(path))
            km.load_host_config(str(path))
        self.assertEqual(err.getvalue().count("nodeid"), 1,
                         "CLI 実行と子プロセスのたびに出るとログが埋まる")
