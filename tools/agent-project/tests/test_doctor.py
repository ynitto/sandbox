"""agent-project の単体テスト — doctor（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class TestDoctor(unittest.TestCase):
    """稼働診断（doctor）: 決定的チェック・kiro-cli 診断・分類・env/config 修正・program 起票。"""

    def _cfg(self, d, **kw):
        kw.setdefault("planner", "none")
        kw.setdefault("executor", "stub")
        kw.setdefault("auto_adjudicate", False)
        return cfg_for(Path(d), **kw)

    def test_stub_with_repo_map_is_a_deterministic_config_finding(self):
        """設定どうしの矛盾（stub × repo_map）は決定層が検出する——モデルに訊かない。

        e4b の実測（2026-08-31・PD4 0/5）: 製品内部の規則（`ensure_repo_maps` は stub で
        生成しない）は材料に無く、モデルには判定しようがない面だった。"""
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, executor="stub", repo_map=True)
            hits = [f for f in km._config_contradiction_findings(cfg)
                    if "repo_map" in f["title"]]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["category"], "config")
            # 矛盾が無ければ無所見（agent executor・repo_map off の両方）
            self.assertEqual(km._config_contradiction_findings(
                self._cfg(d, executor="agent", repo_map=True)), [])
            self.assertEqual(km._config_contradiction_findings(
                self._cfg(d, executor="stub", repo_map=False)), [])

    def test_agent_defs_drift_is_an_env_finding(self):
        """同梱 agents/*.json と配布物 ~/.agents/agents/ のドリフト検査（3 度実害が出た面）。

        検査の実体は agentcore.agentcli.bundled_drift（探索順の持ち主）にあり、doctor は
        env 所見への変換だけを担う。fix は install.sh 再実行の案内——毎回の手動 cp が
        再発源で、個別 cp を案内するとまた 1 ファイルだけ直して残りが漏れる。"""
        with tempfile.TemporaryDirectory() as d:
            bundled = km._agentcli._bundled_dir()
            self.assertIsNotNone(bundled, "リポジトリ直接実行では同梱 dir を解決できる")
            home = Path(d) / "agents-home"
            dist = home / "agents"
            dist.mkdir(parents=True)
            for src in bundled.glob("*.json"):
                shutil.copy(src, dist / src.name)
            stale = dist / "ollama.json"
            stale.write_text(stale.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            env = {"AGENT_PROJECT_AGENTS_HOME": str(home), "HOME": d, "USERPROFILE": d}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("KIRO_AGENTS_DIR", None)   # patch.dict が終了時に戻す
                findings = km.doctor_agent_defs_findings(project_dir=d)
            self.assertEqual([f["title"] for f in findings],
                             ["エージェント定義の配布物が同梱と食い違う: ollama.json"])
            self.assertEqual(findings[0]["category"], "env")
            self.assertEqual(findings[0]["severity"], "warn")
            self.assertIn("install.sh", findings[0]["fix"], "fix は再インストールの案内")
            # 写しが同梱と一致すれば無所見（配り直しで解消したことが観測できる）
            shutil.copy(bundled / "ollama.json", stale)
            with mock.patch.dict(os.environ, env):
                os.environ.pop("KIRO_AGENTS_DIR", None)
                self.assertEqual(km.doctor_agent_defs_findings(project_dir=d), [])

    def test_cmd_doctor_includes_agent_defs_drift(self):
        """`agent-project doctor` から到達できること（呼び出し元の無い検査を作らない）。"""
        with tempfile.TemporaryDirectory() as d:
            bundled = km._agentcli._bundled_dir()
            home = Path(d) / "agents-home"
            dist = home / "agents"
            dist.mkdir(parents=True)
            for src in bundled.glob("*.json"):
                shutil.copy(src, dist / src.name)
            (dist / "ollama.json").write_text('{"command": ["stale"]}', encoding="utf-8")
            cfg = self._cfg(Path(d) / "proj")
            km.ensure_dirs(cfg)
            buf = io.StringIO()
            with mock.patch.dict(os.environ,
                                 {"AGENT_PROJECT_AGENTS_HOME": str(home),
                                  "HOME": d, "USERPROFILE": d}), \
                 contextlib.redirect_stdout(buf):
                os.environ.pop("KIRO_AGENTS_DIR", None)
                km.cmd_doctor(cfg, fix=False, as_json=True, agent_run=lambda p, m: "[]",
                              flow_finder=lambda c, fix: [])
            titles = {f["title"] for f in json.loads(buf.getvalue())["findings"]}
            self.assertIn("エージェント定義の配布物が同梱と食い違う: ollama.json", titles)

    def test_unpushed_commits_are_reported(self):
        """origin へ未 push のコミットを検出する。

        worker と verify は **origin から clone** して実行するので、ローカルにだけあるコミットは
        彼らからは存在しないのと同じ。手元で直した成果は verify に届かず「ローカルでは通るのに
        verify は落ち続ける」という、原因に辿り着きにくい詰まり方をする（実際に起きた: 手元では
        pytest -k codd が 29 件 PASS するのに、クローンでは 0 件収集 → exit=5 → 繰り返し NG）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()
            env = {**os.environ, "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
            remote, repo = d / "remote.git", d / "repo"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True, env=env)
            g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, env=env)
            g("config", "user.email", "t@e.com")
            g("config", "user.name", "t")
            (repo / "a.txt").write_text("x\n")
            g("add", "-A"); g("commit", "-m", "init"); g("push", "-q", "-u", "origin", "HEAD")

            self.assertEqual(km.unpushed_commits(repo)[0], 0, "push 済みなら 0")

            (repo / "b.txt").write_text("y\n")             # 手元で直してコミットしただけ
            g("add", "-A"); g("commit", "-m", "local only")
            n, branch = km.unpushed_commits(repo)
            self.assertEqual(n, 1, "未 push を数える")
            self.assertTrue(branch)

            # 未 push を見る対象は状態ルート自身（S1: 状態リポジトリの clone）。
            cfg = self._cfg(d)
            cfg.backlog = repo / "backlog"
            fs = km.doctor_env_findings(cfg)
            hit = next((f for f in fs if f["category"] == "git"), None)
            self.assertIsNotNone(hit, "doctor が未 push を報告する")
            self.assertIn("未 push", hit["title"])
            self.assertIn("origin から clone", hit["evidence"], "なぜ困るのかを述べる")

    def test_unpushed_commits_on_non_git_is_silent(self):
        # git でない・upstream 無しでは黙る（誤検知しない）
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(km.unpushed_commits(Path(d)), (0, ""))
            self.assertEqual(km.unpushed_commits(None), (0, ""))

    def test_doctor_rejects_unsafe_git_cas_configuration(self):
        # coordination は設定キーでなく観測で決まる（実装計画 W1-8）。doctor_coordination_findings
        # は _coordination_active が True でないと即 [] を返すため、検査対象にするには origin と
        # ピアの両方が要る（origin が無い場合の検査は、それ自体が _coordination_active の判定と
        # 同じことをするだけの到達不能コードだったため削除済み）。
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", str(d)], check=True)
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                            "file:///no-such-remote.git"], check=True)
            mk_peer(Path(d))
            cfg = self._cfg(d, node="",
                            controller_heartbeat_sec=120, controller_lease_sec=60)
            titles = {finding["title"] for finding in km.doctor_coordination_findings(cfg)}
            self.assertIn("git-cas には node が必要", titles)
            self.assertIn("controller heartbeat が lease 以上", titles)

    def test_node_id_cutover_blocks_on_active_board_delegation(self):
        # 実装計画 W1-10: 旧 node_id 名義の委譲がまだ未決着（result.json 無し）なら
        # node_id 切替を止める。
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "board")
            ddir = os.path.join(board, "delegations", "dg-1", "status")
            os.makedirs(ddir, exist_ok=True)
            with open(os.path.join(ddir, "pc-old.json"), "w", encoding="utf-8") as f:
                json.dump({"who": "pc-old", "state": "working"}, f)
            findings = km.doctor_node_id_cutover_findings(board, "pc-old", "pc-new")
            titles = {f["title"] for f in findings}
            self.assertIn("旧 node_id 名義の委譲が実行中", titles)

    def test_node_id_cutover_allows_when_delegation_resolved(self):
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "board")
            ddir = os.path.join(board, "delegations", "dg-1")
            os.makedirs(os.path.join(ddir, "status"), exist_ok=True)
            with open(os.path.join(ddir, "status", "pc-old.json"), "w", encoding="utf-8") as f:
                json.dump({"who": "pc-old", "state": "done"}, f)
            with open(os.path.join(ddir, "result.json"), "w", encoding="utf-8") as f:
                json.dump({"winner": "pc-old", "resolved_at": "2026-01-01T00:00:00Z"}, f)
            findings = km.doctor_node_id_cutover_findings(board, "pc-old", "pc-new")
            self.assertEqual(findings, [])

    def test_node_id_cutover_flags_stale_amigos_role_status(self):
        with tempfile.TemporaryDirectory() as d:
            bus = os.path.join(d, "amigos-bus")
            status_dir = os.path.join(bus, "missions", "m1", "status")
            os.makedirs(status_dir, exist_ok=True)
            with open(os.path.join(status_dir, "pc-old--architect.json"), "w",
                     encoding="utf-8") as f:
                json.dump({"state": "working"}, f)
            findings = km.doctor_node_id_cutover_findings(
                None, "pc-old", "pc-new", amigos_bus_root=bus)
            titles = {f["title"] for f in findings}
            self.assertIn("旧 node_id 名義の amigos ロール状態が残存", titles)

    def test_residency_findings_skip_declared_windows_task(self):
        # doctor が検査できるのは systemd 側だけ。Windows タスクスケジューラ案を正しく
        # 構成した PC に「常駐化が未構成」を出し続けると恒久的な誤警告になる。
        for declared in ("windows-task", "windows", "none", "manual"):
            self.assertEqual(km.doctor_residency_findings(declared), [], declared)

    def test_residency_findings_noop_without_systemd(self):
        # systemd 非対象環境（この開発機の macOS 含む）では所見を出さない。
        if os.path.isdir("/run/systemd/system") and shutil.which("systemctl"):
            self.skipTest("systemd 環境では別経路を通る")
        self.assertEqual(km.doctor_residency_findings("auto"), [])

    def test_node_id_cutover_entry_point_reads_host_yaml(self):
        """`doctor --node-id-cutover <旧>` から検査に到達できること（実装計画 W1-10）。

        以前はこの検査に呼び出し元が無く、手順書は `from agent_project.doctor import …` を
        案内していた。agent_project の断片は共有名前空間へ exec して合成する前提なので、
        単体 import すると即 NameError になる——手順どおり叩いても動かない検査だった。
        板と amigos バスの場所・新名義は host.yaml から引く（人に打ち直させない）。"""
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "board")
            sdir = os.path.join(board, "delegations", "dg-1", "status")
            os.makedirs(sdir, exist_ok=True)
            old = km.normalize_node_id("pc-old")
            with open(os.path.join(sdir, f"{old}.json"), "w", encoding="utf-8") as f:
                json.dump({"who": old, "state": "working"}, f)
            host = types.SimpleNamespace(board=board, board_workdir=None, amigos_bus="",
                                         node_id="pc-new")
            with mock.patch.object(km, "load_host_config", return_value=host):
                cfg = self._cfg(d)
                titles = {f["title"] for f in km.node_id_cutover_findings(cfg, "pc-old")}
                self.assertIn("旧 node_id 名義の委譲が実行中", titles)
                # 旧 node_id を渡さなければ何も検査しない（通常の doctor を重くしない）
                self.assertEqual(km.node_id_cutover_findings(cfg, ""), [])

    def test_node_id_cutover_noop_without_board_or_amigos(self):
        self.assertEqual(km.doctor_node_id_cutover_findings(None, "pc-old", "pc-new"), [])

    def test_node_id_cutover_flags_state_repo_residue(self):
        """プロジェクト状態リポジトリ側の残骸も切替前に出す（P0-3）。

        板と amigos だけ見ていると、同じ PC の状態リポジトリに残るものを見落とす。
        どれも「切替してから固まる」形でしか表に出ない。"""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "state")
            os.makedirs(os.path.join(root, "status"), exist_ok=True)
            os.makedirs(os.path.join(root, "backlog"), exist_ok=True)
            old = km.normalize_node_id("DESKTOP-X")
            with open(os.path.join(root, "status", f"{old}.json"), "w", encoding="utf-8") as f:
                json.dump({"node": "DESKTOP-X", "updated_iso": "2026-07-26T00:00:00+00:00"}, f)
            with open(os.path.join(root, "backlog", "T1.md"), "w", encoding="utf-8") as f:
                f.write("## T1: 実行中\n- status: doing\n- claim_owner: DESKTOP-X\n")
            with open(os.path.join(root, "backlog", "T2.md"), "w", encoding="utf-8") as f:
                f.write("## T2: 手で割当\n- status: ready\n- node: DESKTOP-X\n")
            with open(os.path.join(root, "backlog", "T3.md"), "w", encoding="utf-8") as f:
                f.write("## T3: 自動割当\n- status: ready\n- node: DESKTOP-X\n"
                        "- node_source: auto\n")
            titles = {f["title"] for f in km.doctor_node_id_cutover_findings(
                None, "DESKTOP-X", "desktop-y", state_roots=[root])}
            self.assertIn("旧 node_id 名義の生存信号が残っている", titles)
            self.assertIn("旧 node_id 名義が実行権（claim）を握っている", titles)
            self.assertIn("旧 node_id 名義へ手で割り当てたタスクが残っている", titles)
            # 自動割当（node_source: auto）は allocate_distributed_tasks が振り直すので出さない
            manual = [f for f in km.doctor_node_id_cutover_findings(
                None, "DESKTOP-X", "desktop-y", state_roots=[root])
                if f["title"].startswith("旧 node_id 名義へ手で")]
            self.assertNotIn("T3", manual[0]["evidence"])

    def test_node_id_cutover_state_repo_clean_has_no_findings(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "state")
            os.makedirs(os.path.join(root, "backlog"), exist_ok=True)
            self.assertEqual(km.doctor_node_id_cutover_findings(
                None, "DESKTOP-X", "desktop-y", state_roots=[root]), [])

    def test_node_id_cutover_matches_engine_written_filenames(self):
        # 板の status ファイル名は各エンジンの _safe が決める。doctor が独自に綴り替えると
        # 実行中の委譲を見落として「切替してよい」と誤報告する（所見ゼロを許可条件に
        # している手順書の前提が壊れる）。共通の normalize_node_id で揃っていることを固定。
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "board")
            ddir = os.path.join(board, "delegations", "dg-1", "status")
            os.makedirs(ddir, exist_ok=True)
            written = km.normalize_node_id("My PC")      # エンジンが書くのと同じ綴り
            with open(os.path.join(ddir, f"{written}.json"), "w", encoding="utf-8") as f:
                json.dump({"who": written, "state": "working"}, f)
            findings = km.doctor_node_id_cutover_findings(board, "My PC", "pc-new")
            self.assertIn("旧 node_id 名義の委譲が実行中", {f["title"] for f in findings})

    def test_node_id_cutover_flags_new_id_already_live_on_board(self):
        # W1-10 で既定採番を PC 名にしたため、ホスト名重複（localhost・コンテナ既定名）で
        # 別 PC と名義が衝突しうる。気づかず切り替えると 2 台が同じ名義で入札する。
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "board")
            os.makedirs(os.path.join(board, "nodes"), exist_ok=True)
            with open(os.path.join(board, "nodes", "pc-new.json"), "w", encoding="utf-8") as f:
                json.dump({"node": "pc-new",
                           "heartbeat": datetime.now(timezone.utc).isoformat(),
                           "fresh_after_sec": 120}, f)
            findings = km.doctor_node_id_cutover_findings(board, "pc-old", "pc-new")
            self.assertIn("新 node_id が板で使用中", {f["title"] for f in findings})

    def test_node_id_cutover_ignores_stale_new_id_registration(self):
        # heartbeat が古い登録は「もう居ない PC の残骸」。切替を止める理由にしない。
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "board")
            os.makedirs(os.path.join(board, "nodes"), exist_ok=True)
            old = datetime.now(timezone.utc) - timedelta(seconds=3600)
            with open(os.path.join(board, "nodes", "pc-new.json"), "w", encoding="utf-8") as f:
                json.dump({"node": "pc-new", "heartbeat": old.isoformat(),
                           "fresh_after_sec": 120}, f)
            self.assertEqual(km.doctor_node_id_cutover_findings(board, "pc-old", "pc-new"), [])

    def test_residency_findings_noop_without_systemd(self):
        # systemd 非対象環境（この開発機の macOS 含む）では所見を出さない——ノイズ優先。
        with mock.patch.object(km.os.path, "isdir", return_value=False):
            self.assertEqual(km.doctor_residency_findings(), [])

    def test_residency_findings_flags_missing_unit(self):
        with mock.patch.object(km.os.path, "isdir", side_effect=lambda p: p == "/run/systemd/system"), \
             mock.patch.object(km.shutil, "which", return_value="/usr/bin/systemctl"), \
             mock.patch.object(km.os.path, "isfile", return_value=False):
            findings = km.doctor_residency_findings()
        self.assertEqual([f["title"] for f in findings], ["常駐化が未構成"])
        self.assertEqual(findings[0]["severity"], "warn")

    def test_residency_findings_flags_disabled_unit(self):
        fake_proc = types.SimpleNamespace(stdout="disabled\n")
        with mock.patch.object(km.os.path, "isdir", side_effect=lambda p: p == "/run/systemd/system"), \
             mock.patch.object(km.shutil, "which", return_value="/usr/bin/systemctl"), \
             mock.patch.object(km.os.path, "isfile", return_value=True), \
             mock.patch.object(km.subprocess, "run", return_value=fake_proc):
            findings = km.doctor_residency_findings()
        self.assertEqual([f["title"] for f in findings], ["常駐 unit が未有効化"])

    def test_residency_findings_clean_when_enabled(self):
        fake_proc = types.SimpleNamespace(stdout="enabled\n")
        with mock.patch.object(km.os.path, "isdir", side_effect=lambda p: p == "/run/systemd/system"), \
             mock.patch.object(km.shutil, "which", return_value="/usr/bin/systemctl"), \
             mock.patch.object(km.os.path, "isfile", return_value=True), \
             mock.patch.object(km.subprocess, "run", return_value=fake_proc):
            self.assertEqual(km.doctor_residency_findings(), [])

    def test_env_findings_detect_missing_kiro_cli(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, planner="agent")            # planner=agent はエージェント CLI を要求
            fs = km.doctor_env_findings(cfg, which=lambda _n: None)   # 何も PATH に無い
            titles = [f["title"] for f in fs]
            self.assertTrue(any("kiro-cli" in t for t in titles))
            cli = next(f for f in fs if "kiro-cli" in f["title"])
            self.assertEqual(cli["category"], "env")
            self.assertEqual(cli["severity"], "critical")
            # 必須ディレクトリ未作成は config + create-dirs アクション
            dirf = next(f for f in fs if f["category"] == "config")
            self.assertEqual(dirf["fix_action"], "create-dirs")

    def test_env_findings_clean_when_tools_present(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            fs = km.doctor_env_findings(cfg, which=lambda _n: "/usr/bin/" + _n)
            # agent-flow/git あり・ディレクトリ作成済み → env/config の致命所見は出ない
            self.assertFalse(any(f["severity"] == "critical" for f in fs))
            self.assertFalse(any(f.get("fix_action") == "create-dirs" for f in fs))

    def test_env_findings_check_binary_matching_agent_cli(self):
        # agent_cli=claude のときは kiro-cli ではなく claude の PATH 不在を報告する
        # （executor/planner=agent は agent_cli に委譲するため、必須バイナリも agent_cli 依存）。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, planner="agent", agent_cli="claude")
            fs = km.doctor_env_findings(cfg, which=lambda n: None if n == "claude" else "/usr/bin/" + n)
            titles = [f["title"] for f in fs]
            self.assertTrue(any("claude" in t for t in titles))
            self.assertFalse(any("kiro-cli" in t for t in titles))

    def test_env_findings_check_binary_matching_agent_cli_copilot(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, executor="agent", agent_cli="copilot")
            fs = km.doctor_env_findings(cfg, which=lambda n: None if n == "copilot" else "/usr/bin/" + n)
            titles = [f["title"] for f in fs]
            self.assertTrue(any("copilot" in t for t in titles))

    def test_wiring_findings_preserve_provider_output(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, regression_cmd="verify", intake_cmd="intake")
            finding = {"category": "config", "severity": "info", "title": "配線済み"}
            judgment = object()
            detect = types.SimpleNamespace(detect_wiring=mock.Mock(return_value=judgment))
            render = types.SimpleNamespace(doctor_findings=mock.Mock(return_value=[finding]))
            providers = {"wiring.detect": detect, "wiring.findings": render}
            which, run = mock.Mock(), mock.Mock()

            with mock.patch.object(
                    km, "_hook_provider", side_effect=lambda capability, _cfg: providers[capability]):
                findings = km.doctor_wiring_findings(cfg, which=which, run=run)

            self.assertEqual(findings, [finding])
            detect.detect_wiring.assert_called_once_with(
                regression_cmd="verify", intake_cmd="intake",
                repos_path=km.repo_registry_path(cfg), which=which, run=run)
            render.doctor_findings.assert_called_once_with(judgment)

    def test_wiring_findings_noop_without_provider(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(km, "_hook_provider", return_value=None):
            self.assertEqual(km.doctor_wiring_findings(self._cfg(d)), [])

    def test_parse_findings_filters_unknown_categories(self):
        out = ('説明文… [{"category":"program","severity":"critical","title":"NPE",'
               '"evidence":"journal","fix":"バグ"},'
               '{"category":"bogus","severity":"warn","title":"x"},'
               '{"category":"config","severity":"loud","title":"y"}]')
        fs = km._parse_doctor_findings(out)
        self.assertEqual(len(fs), 2)                       # bogus カテゴリは捨てる
        self.assertEqual(fs[0]["category"], "program")
        self.assertEqual(fs[1]["severity"], "warn")        # 未知 severity は warn へ正規化

    def test_diagnose_returns_none_when_agent_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            boom = lambda p, m: (_ for _ in ()).throw(RuntimeError("no kiro-cli"))
            self.assertIsNone(km.diagnose_with_agent(cfg, {}, [], agent_run=boom))

    def test_doctor_prompt_default_is_compact_json_not_indented(self):
        """案 K-1（docs/plans/2026-08-05-json-prompt-compression-study.md）:
        indent=2 は使わない。内容（キー・値）は不変のまま区切りだけ最小化する。"""
        signals = {"blocked": [{"id": "T-1", "status": "review"}]}
        prompt = km._doctor_prompt(signals, [])
        self.assertNotIn("  ", prompt.split("=== 稼働シグナル", 1)[1].split("\n\n", 1)[0])
        self.assertIn('{"blocked":[{"id":"T-1","status":"review"}]}', prompt)

    def test_doctor_prompt_table_opt_in_folds_homogeneous_arrays(self):
        """案 K-2: table=True のときだけ均質配列を表形式へ畳む。既定は従来どおり。"""
        signals = {"blocked": [{"id": "T-1", "status": "review"},
                               {"id": "T-2", "status": "blocked"}]}
        default_prompt = km._doctor_prompt(signals, [])
        table_prompt = km._doctor_prompt(signals, [], table=True)
        self.assertNotIn("blocked[2]{id,status}:", default_prompt)
        self.assertIn("blocked[2]{id,status}:", table_prompt)

    def test_diagnose_with_agent_honors_prompt_table_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, prompt_table=True)
            captured = {}

            def capture(p, m):
                captured["prompt"] = p
                return "[]"
            signals = {"blocked": [{"id": "T-1", "status": "review"},
                                   {"id": "T-2", "status": "blocked"}]}
            km.diagnose_with_agent(cfg, signals, [], agent_run=capture)
            self.assertIn("blocked[2]{id,status}:", captured["prompt"])

    def test_apply_fix_create_dirs_and_policy_protect(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            self.assertTrue(km.apply_doctor_fix(cfg, {"fix_action": "create-dirs"}))
            self.assertTrue(cfg.needs.exists() and cfg.decisions.exists())
            msg = km.apply_doctor_fix(cfg, {"fix_action": "policy-protect"})
            self.assertIn("protect", msg)
            self.assertTrue(km.load_policy(cfg.policy).protect)
            # 冪等: 既に protect があれば二重追加しない（空文字＝変更なし）
            self.assertEqual(km.apply_doctor_fix(cfg, {"fix_action": "policy-protect"}), "")

    def test_find_skill(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "skills"
            (home / "gitlab-idd").mkdir(parents=True)
            self.assertEqual(km.find_skill("gitlab-idd", home=str(home)),
                             home / "gitlab-idd")
            self.assertIsNone(km.find_skill("does-not-exist", home=str(home)))

    def test_program_findings_routed_to_gitlab_idd_when_skill_present(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            calls = []

            def agent(prompt, model):
                if "稼働診断医" in prompt:                  # 診断パス
                    return ('[{"category":"program","severity":"critical",'
                            '"title":"クラッシュ","evidence":"run-log","fix":"例外"}]')
                calls.append("file")                        # 起票パス
                return "起票しました"

            with tempfile.TemporaryDirectory() as sk:
                home = Path(sk)
                (home / "gitlab-idd").mkdir(parents=True)
                rc = km.cmd_doctor(cfg, fix=True, as_json=True, agent_run=agent,
                                   skill_finder=lambda n: km.find_skill(n, home=str(home)))
            self.assertEqual(calls, ["file"])               # gitlab-idd へ委譲した
            self.assertEqual(rc, 1)                          # critical は起票で解消・残りは warn → 1

    def test_program_output_only_when_skill_missing(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            calls = []

            def agent(prompt, model):
                if "稼働診断医" in prompt:
                    return ('[{"category":"program","severity":"critical",'
                            '"title":"バグ","evidence":"e","fix":"f"}]')
                calls.append("file")
                return "x"

            rc = km.cmd_doctor(cfg, fix=True, agent_run=agent,
                               skill_finder=lambda _n: None)   # スキル無し
            self.assertEqual(calls, [])                      # 起票は呼ばない（出力のみ）
            self.assertEqual(rc, 2)                          # 未解決の critical program → 2

    def test_doctor_via_main_without_backlog_diagnoses(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # kiro-cli/agent-flow を呼ばない構成で main 経由（backlog 無しでも落ちない）
            rc = km.main(["doctor", "--json", "--no-flow", "--workdir", str(d),
                          "--root", str(d / ".ka"), "--planner", "none", "--executor", "stub",
                          "--no-auto-adjudicate"])
            self.assertIn(rc, (0, 1, 2))

    def test_every_emitted_category_is_registered_and_labelled(self):
        # doctor は所見を _DOCTOR_CATEGORIES の順で並べ、label[cat] で見出しを出す。
        # 片方だけに category を足すと、その所見が出た瞬間 doctor 全体が
        # ValueError（.index）/ KeyError（label）で落ちる（実際 "git" 追加時に落ちた）。
        src = (Path(km.__file__).parent / "doctor.py").read_text(encoding="utf-8")
        labelled = set(km.re.findall(r'"(\w+)":\s*"[^"]+"',
                                     km.re.search(r'label = \{([^}]*)\}', src).group(1)))
        self.assertEqual(set(km._DOCTOR_CATEGORIES), labelled)
        # 実際に全カテゴリの所見を持たせても描画が落ちないこと
        findings = [{"category": c, "severity": "warn", "title": f"t-{c}",
                     "evidence": "e", "fix": "f"} for c in km._DOCTOR_CATEGORIES]
        self.assertEqual(len(km._dedupe_findings(findings)), len(km._DOCTOR_CATEGORIES))

    def test_flow_coordination_merges_and_does_not_refile(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, with_flow=True)
            km.ensure_dirs(cfg)
            filed = []

            def agent(prompt, model):
                if "稼働診断医" in prompt:
                    return "[]"                              # 本体側は所見なし
                filed.append("autonomous")
                return "x"

            # agent-flow doctor が返す findings（env/config は解消済み・program は起票済み）
            def flow_finder(c, fix):
                return [
                    {"category": "config", "severity": "warn", "title": "バスのルートが未作成",
                     "evidence": "bus=...", "fix": "作成", "source": "agent-flow",
                     "resolved": "バスのルートを作成しました"},
                    {"category": "program", "severity": "critical", "title": "flow のクラッシュ",
                     "evidence": "run-x", "fix": "例外", "source": "agent-flow",
                     "resolved": "gitlab-idd で起票（gitlab-idd）"},
                ]

            captured = {}
            with tempfile.TemporaryDirectory() as sk:
                home = Path(sk)
                (home / "gitlab-idd").mkdir(parents=True)
                import io
                import contextlib as _ctx
                buf = io.StringIO()
                with _ctx.redirect_stdout(buf):
                    rc = km.cmd_doctor(cfg, fix=True, as_json=True, agent_run=agent,
                                       skill_finder=lambda n: km.find_skill(n, home=str(home)),
                                       flow_finder=flow_finder)
                captured = json.loads(buf.getvalue())
            # flow 由来の program は本体が再起票しない（agent-flow が起票済み）
            self.assertEqual(filed, [])
            # flow の critical は解消済みで統合 → 未解決 critical なし（rc は 2 でない）
            self.assertIn(rc, (0, 1))
            self.assertEqual(captured["flow_findings"], 2)
            flow_prog = [f for f in captured["findings"]
                         if f.get("source") == "agent-flow" and f["category"] == "program"]
            self.assertEqual(len(flow_prog), 1)
            self.assertTrue(flow_prog[0].get("resolved"))     # agent-flow が起票済みのまま統合

    def test_flow_disabled_skips_flow_finder(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, with_flow=False)        # 既定 off（直接 Config 構築）
            km.ensure_dirs(cfg)
            called = []
            km.cmd_doctor(cfg, fix=False, agent_run=lambda p, m: "[]",
                          flow_finder=lambda c, fix: called.append(1) or [])
            self.assertEqual(called, [])               # with_flow=False なら呼ばれない

    def test_collect_flow_findings_parses_subprocess_json(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, with_flow=True)

            class P:
                stdout = ('{"tool":"agent-flow","findings":'
                          '[{"category":"env","severity":"warn","title":"git 無し",'
                          '"evidence":"e","fix":"f"}]}')

            out = km.collect_flow_findings(cfg, fix=False, runner=lambda cmd: P())
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["source"], "agent-flow")   # 連携由来でタグ付け
            # 不正 JSON は空で無害にスキップ
            self.assertEqual(km.collect_flow_findings(
                cfg, fix=False, runner=lambda cmd: type("P", (), {"stdout": "boom"})()), [])


class TestDoctorHostProjects(unittest.TestCase):
    """host.yaml `projects[]` の起動前チェック（S1 設計 §3.6・修正計画 P3-3）。

    ここが無かった間、`root` の綴り間違い・origin の取り違え・層契約違反は
    「子の起動失敗 → 隔離表示」という最も遠い症状でしか観測できなかった。
    """

    def _host(self, projects, defaults=None):
        return km.HostConfig({"schema_version": 1, "node_id": "pc-a",
                              "projects": projects, "defaults": defaults or {}})

    def _git_root(self, path: Path, origin: str = "", branch: str = "main") -> Path:
        path.mkdir(parents=True, exist_ok=True)
        g = lambda *a: subprocess.run(["git", "-C", str(path), *a], capture_output=True)
        subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
        g("config", "user.email", "t@e.com")
        g("config", "user.name", "t")
        if origin:
            g("remote", "add", "origin", origin)
        (path / "backlog").mkdir(exist_ok=True)          # 状態ルートのマーカー
        (path / "charter.md").write_text("# c\n", encoding="utf-8")
        g("add", "-A")
        g("commit", "-q", "-m", "init")
        return path

    def _titles(self, findings):
        return [f["title"] for f in findings]

    def test_worker_node_without_projects_is_silent(self):
        # プロジェクトを 1 つも宣言していない PC（ワーカー構成）では検査対象が無い。
        self.assertEqual(km.doctor_host_projects_findings(self._host([])), [])

    def test_missing_root_without_state_repo_is_critical(self):
        with tempfile.TemporaryDirectory() as d:
            host = self._host([{"name": "p1", "root": os.path.join(d, "typo-root")}])
            findings = km.doctor_host_projects_findings(host)
            self.assertEqual(self._titles(findings), ["宣言された root がありません"])
            self.assertEqual(findings[0]["severity"], "critical")
            self.assertIn("p1", findings[0]["evidence"])

    def test_missing_root_with_state_repo_is_silent(self):
        # state_repo を宣言していれば起動時に clone される＝正常な初回起動。誤警告を出さない。
        with tempfile.TemporaryDirectory() as d:
            host = self._host([{"name": "p1", "root": os.path.join(d, "not-yet"),
                                "state_repo": "git@example.com:t/p1-state.git"}])
            self.assertEqual(km.doctor_host_projects_findings(host), [])

    def test_root_without_declaration_is_reported(self):
        host = self._host([{"name": "p1"}])
        self.assertEqual(self._titles(km.doctor_host_projects_findings(host)),
                         ["projects[].root が未宣言"])

    def test_origin_mismatch_is_critical(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._git_root(Path(d) / "state", origin="git@example.com:t/other.git")
            host = self._host([{"name": "p1", "root": str(root),
                                "state_repo": "git@example.com:t/p1-state.git"}])
            findings = km.doctor_host_projects_findings(host)
            self.assertIn("E3: root の origin が宣言と違う", self._titles(findings))
            hit = next(f for f in findings if f["title"].startswith("E3"))
            self.assertIn("other.git", hit["evidence"])

    def test_origin_match_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            url = "git@example.com:t/p1-state.git"
            root = self._git_root(Path(d) / "state", origin=url)
            host = self._host([{"name": "p1", "root": str(root), "state_repo": url}])
            self.assertEqual(km.doctor_host_projects_findings(host), [])

    def test_branch_mismatch_is_warned(self):
        with tempfile.TemporaryDirectory() as d:
            url = "git@example.com:t/p1-state.git"
            root = self._git_root(Path(d) / "state", origin=url, branch="work")
            host = self._host([{"name": "p1", "root": str(root), "state_repo": url,
                                "branch": "main"}])
            findings = km.doctor_host_projects_findings(host)
            hit = next(f for f in findings if f["title"] == "チェックアウトが宣言ブランチと違う")
            self.assertEqual(hit["severity"], "warn")     # 同期先は宣言ブランチ＝表示で足りる

    def test_root_inside_another_repository_is_critical(self):
        with tempfile.TemporaryDirectory() as d:
            outer = self._git_root(Path(d) / "outer")
            nested = outer / "state"
            nested.mkdir()
            (nested / "backlog").mkdir()
            host = self._host([{"name": "p1", "root": str(nested)}])
            self.assertIn("root が別リポジトリの内側",
                          self._titles(km.doctor_host_projects_findings(host)))

    def test_source_repository_declared_as_root_is_critical(self):
        # E5: 成果物リポジトリを登録すると、そこへ backlog/ や journal.md が生える。
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "app"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "main.py").write_text("print(1)\n", encoding="utf-8")
            host = self._host([{"name": "p1", "root": str(root)}])
            self.assertIn("E5: root が状態ルートに見えない",
                          self._titles(km.doctor_host_projects_findings(host)))

    def test_project_yaml_layer_violation_is_reported_without_exiting(self):
        """E1 をエラー終了ではなく所見として出す（doctor は診断コマンドなので落ちない）。"""
        with tempfile.TemporaryDirectory() as d:
            url = "git@example.com:t/p1-state.git"
            root = self._git_root(Path(d) / "state", origin=url)
            (root / "agent-project.json").write_text(
                json.dumps({"node_id": "pc-a", "planner": "agent"}), encoding="utf-8")
            host = self._host([{"name": "p1", "root": str(root), "state_repo": url}])
            findings = km.doctor_host_projects_findings(host)     # SystemExit しない
            hit = next(f for f in findings if f["title"].startswith("E1"))
            self.assertEqual(hit["severity"], "critical")
            self.assertIn("node_id", hit["evidence"])

    def test_host_defaults_violation_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            url = "git@example.com:t/p1-state.git"
            root = self._git_root(Path(d) / "state", origin=url)
            host = self._host([{"name": "p1", "root": str(root), "state_repo": url}],
                              defaults={"plan_review": False})
            findings = km.doctor_host_projects_findings(host)
            self.assertIn("E2", " ".join(self._titles(findings)))

    def test_unknown_project_key_is_warned(self):
        with tempfile.TemporaryDirectory() as d:
            url = "git@example.com:t/p1-state.git"
            root = self._git_root(Path(d) / "state", origin=url)
            (root / "agent-project.json").write_text(
                json.dumps({"plan_reviw": True}), encoding="utf-8")   # 綴り間違い
            host = self._host([{"name": "p1", "root": str(root), "state_repo": url}])
            findings = km.doctor_host_projects_findings(host)
            hit = next(f for f in findings if f["title"].startswith("W-UNKNOWN"))
            self.assertEqual(hit["severity"], "warn")
            self.assertIn("plan_reviw", hit["evidence"])

    def test_config_key_project_config_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            url = "git@example.com:t/p1-state.git"
            root = self._git_root(Path(d) / "state", origin=url)
            host = self._host([{"name": "p1", "root": str(root), "state_repo": url,
                                "config": "/tmp/other.yaml"}])
            self.assertIn("E6: projects[].config は廃止",
                          self._titles(km.doctor_host_projects_findings(host)))

    def test_missing_host_config_is_silent(self):
        # host.yaml が無い PC（単発 run だけの環境）でも doctor は走り切る。
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"AGENT_PROJECT_AGENTS_HOME": home}):
                self.assertEqual(km.doctor_host_projects_findings(), [])

    def test_host_config_findings_match_the_startup_warning(self):
        # 「doctor は緑なのに起動時は警告」を作らない。判定は起動時と同じ 1 実装を呼ぶ。
        raw = {"schema_version": 1, "node_id": "pc-a", "nodeid": "typo",
               "workloads": ["flow", "nope"], "budget": {"max_concurrent": -1}}
        host = km.HostConfig(raw, path="/tmp/agent-project.host.yaml")
        findings = km.doctor_host_config_findings(host)
        self.assertEqual(len(findings), len(km.host_config_findings(raw)))
        self.assertTrue(all(f["severity"] == "warn" for f in findings))  # 起動と同じ強度
        # 内訳が残る（W→E 昇格の判断材料。題が同じだと doctor の重複畳みで 1 件に潰れる）
        self.assertEqual(len({f["title"] for f in findings}), len(findings))
        self.assertTrue(any("nodeid" in f["evidence"] for f in findings))
        self.assertTrue(any("workloads" in f["evidence"] for f in findings))

    def test_host_config_findings_silent_without_declaration(self):
        self.assertEqual(km.doctor_host_config_findings(km.HostConfig({})), [])

    def test_cmd_doctor_includes_host_config_findings(self):
        """起動時の警告が `agent-project doctor` からも数えられること（§1.2 判断 1 の材料）。"""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "agents-home"
            home.mkdir()
            (home / "agent-project.host.json").write_text(
                json.dumps({"schema_version": 1, "node_id": "pc-a", "nodeid": "typo"}),
                encoding="utf-8")
            cfg = cfg_for(Path(d) / "proj", planner="none", executor="stub")
            km.ensure_dirs(cfg)
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"AGENT_PROJECT_AGENTS_HOME": str(home)}), \
                 contextlib.redirect_stdout(buf):
                km.cmd_doctor(cfg, fix=False, as_json=True, agent_run=lambda p, m: "[]",
                              flow_finder=lambda c, fix: [])
            titles = {f["title"] for f in json.loads(buf.getvalue())["findings"]}
            self.assertIn("host.yaml の宣言が読まれていません: nodeid", titles)

    def test_non_normalized_node_id_declaration_is_reported(self):
        # §6-2 の決着: 明示値は正規化しない代わりに、正規形でないことを doctor が知らせる。
        host = km.HostConfig({"node_id": "DESKTOP-X"}, path="/tmp/agent-project.host.yaml")
        findings = km.doctor_node_id_spelling_findings(host, env={})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "warn")     # 起動は止めない
        self.assertIn("desktop-x", findings[0]["evidence"])   # 正規形を示す
        self.assertIn("--node-id-cutover", findings[0]["fix"])  # 切替は人の明示操作

    def test_normalized_declaration_is_silent(self):
        host = km.HostConfig({"node_id": "desk-a"}, path="/tmp/agent-project.host.yaml")
        self.assertEqual(km.doctor_node_id_spelling_findings(host, env={}), [])
        # 未宣言（ホスト名から導く）経路は常に正規形なので対象外
        self.assertEqual(km.doctor_node_id_spelling_findings(km.HostConfig({}), env={}), [])

    def test_amigos_env_declaration_is_checked(self):
        host = km.HostConfig({}, path="/tmp/agent-project.host.yaml")
        findings = km.doctor_node_id_spelling_findings(host, env={"AGENT_AMIGOS_NODE": "Mac"})
        self.assertEqual([f["title"] for f in findings],
                         ["宣言した node_id が正規形ではない: Mac"])

    def test_amigos_node_json_is_checked(self):
        with tempfile.TemporaryDirectory() as home:
            p = Path(home) / ".agents" / "amigos" / "node.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({"id": "PC_One"}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
                findings = km.doctor_node_id_spelling_findings(km.HostConfig({}), env={})
        self.assertEqual(len(findings), 1)
        self.assertIn("pc_one", findings[0]["evidence"])

    def test_cmd_doctor_includes_host_project_findings(self):
        """`agent-project doctor` から到達できること（呼び出し元の無い検査を作らない）。"""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "agents-home"
            home.mkdir()
            (home / "agent-project.host.json").write_text(
                json.dumps({"schema_version": 1, "node_id": "pc-a",
                            "projects": [{"name": "p1", "root": os.path.join(d, "typo")}]}),
                encoding="utf-8")
            cfg = cfg_for(Path(d) / "proj", planner="none", executor="stub")
            km.ensure_dirs(cfg)
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"AGENT_PROJECT_AGENTS_HOME": str(home)}), \
                 contextlib.redirect_stdout(buf):
                rc = km.cmd_doctor(cfg, fix=False, as_json=True, agent_run=lambda p, m: "[]",
                                   flow_finder=lambda c, fix: [])
            titles = {f["title"] for f in json.loads(buf.getvalue())["findings"]}
            self.assertIn("宣言された root がありません", titles)
            self.assertEqual(rc, 2)                      # 未解決の critical → 2


class TestStructureFindings(unittest.TestCase):
    """W15: 断片合成と CLI 入口の対応を数え、綻びを所見にする（起動は止めない warn）。"""

    def test_repository_has_no_unreachable_fragments_or_unrouted_commands(self):
        """構造テストの本体: 現在のリポジトリは到達不能な断片も宙に浮いた入口も持たない。"""
        c = km.structure_counts()
        self.assertEqual(c["unlisted"], [], "断片は _FRAGMENTS に載せる（載せないと exec されない）")
        self.assertEqual(c["missing"], [])
        self.assertEqual(c["unrouted"], [], "add_parser した名前は _subcommands にも足す")
        self.assertGreater(c["fragments"], 20)
        self.assertIn("doctor", c["commands"])
        self.assertEqual(km.doctor_structure_findings(), [])

    def test_unreachable_fragment_becomes_a_finding(self):
        with mock.patch.object(km, "structure_counts",
                               return_value={"fragments": 31, "unlisted": ["ghost"],
                                             "missing": [], "commands": [], "unrouted": []}):
            f = km.doctor_structure_findings()
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["category"], f[0]["severity"]), ("program", "warn"))
        self.assertIn("ghost", f[0]["title"])

    def test_unrouted_subcommand_becomes_a_finding(self):
        with mock.patch.object(km, "structure_counts",
                               return_value={"fragments": 31, "unlisted": [], "missing": [],
                                             "commands": ["revive"], "unrouted": ["revive"]}):
            f = km.doctor_structure_findings()
        self.assertEqual(len(f), 1)
        self.assertIn("revive", f[0]["title"])
        self.assertIn("serve", f[0]["evidence"])

    def test_findings_reach_the_status_json_pipe(self):
        """所見 → engine/status.json の横断エラー → dashboard のパイプ 1 本に載る（重複は積まない）。"""
        status = km.EngineStatus("pc-a")
        host = km.HostConfig({"schema_version": 1, "node_id": "pc-a", "nodeid": "typo"},
                             path="/tmp/agent-project.host.yaml")
        n = km._record_diagnostic_findings(host, status)
        self.assertGreater(n, 0)
        self.assertTrue(any("nodeid" in e for e in status.recent_errors))
        self.assertTrue(all(e.startswith("doctor[") for e in status.recent_errors))
        self.assertEqual(km._record_diagnostic_findings(host, status), 0)   # 毎 tick 積まない


class TestDoctorCreditKnowledge(unittest.TestCase):
    """Phase5: stale 射影 / 孤立 reservation / 根拠なし active / 未知 rule hash。"""

    def _cfg(self, d, **kw):
        kw.setdefault("planner", "none")
        kw.setdefault("executor", "stub")
        kw.setdefault("auto_adjudicate", False)
        return cfg_for(Path(d), **kw)

    def test_stale_projection_and_orphan_reservation(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            status = d / "status"
            status.mkdir(parents=True, exist_ok=True)
            stale_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            (status / "pc-a.json").write_text(json.dumps({
                "node": "pc-a", "availability": "active",
                "updated_iso": stale_iso, "fresh_after_sec": 60,
                "budget": {
                    "contract_version": 1, "observed_at": stale_iso,
                    "source": "local-ledger",
                    "capacity": {"limit": 100, "used": 10, "reserved": 5},
                    "can_accept": True, "reason_codes": ["ok"], "enforce": False,
                },
            }), encoding="utf-8")
            rdir = d / "reservations"
            rdir.mkdir()
            (rdir / "rsv-1.json").write_text(json.dumps({
                "reservation_id": "rsv-1", "task_id": "MISSING", "node": "pc-a",
                "amount": 5, "status": "live",
            }), encoding="utf-8")
            titles = {f["title"] for f in km.doctor_credit_knowledge_findings(cfg)}
            self.assertTrue(any("stale" in t for t in titles), titles)
            self.assertTrue(any("孤立 reservation" in t for t in titles), titles)

    def test_active_without_evidence_and_unknown_hash(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            rid = "obs-" + ("ab" * 8)
            (cfg.decisions / "R1.md").write_text(
                f"# R1\n\n- rule-lifecycle: {rid} active\n", encoding="utf-8")
            (cfg.backlog / "T1.md").write_text(
                "---\nid: T1\nstatus: ready\n---\n# T1\n\n"
                "- rules_hash: not-a-phase3-hash\n", encoding="utf-8")
            titles = {f["title"] for f in km.doctor_credit_knowledge_findings(cfg)}
            self.assertIn("根拠なし active rule", titles)
            self.assertTrue(any("未知の rule hash" in t for t in titles), titles)

    def test_board_overlay_stale_projection(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            board = d / "board"
            nodes = board / "nodes"
            nodes.mkdir(parents=True)
            old = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            (nodes / "pc-b.json").write_text(json.dumps({
                "node": "pc-b", "heartbeat": old, "fresh_after_sec": 30,
                "budget": {
                    "contract_version": 1, "observed_at": old, "source": "local-ledger",
                    "capacity": {"limit": 1, "used": 0, "reserved": None},
                    "can_accept": True, "reason_codes": ["ok"],
                },
            }), encoding="utf-8")
            titles = {f["title"] for f in km.doctor_credit_knowledge_findings(
                cfg, board_root=str(board))}
            self.assertTrue(any("stale" in t and "board" in t for t in titles), titles)
