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

            cfg = self._cfg(d)
            cfg.state_top = repo
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

    def test_node_id_cutover_noop_without_board_or_amigos(self):
        self.assertEqual(km.doctor_node_id_cutover_findings(None, "pc-old", "pc-new"), [])

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
