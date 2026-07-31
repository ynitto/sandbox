"""agent-project の単体テスト — verify（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class TestFlakeTolerantVerify(unittest.TestCase):
    """フレーク耐性 verify（--verify-confirm）。揺れる verify を NG churn せず人へ隔離。"""

    def _patch_verify(self, results):
        """km.run_verify を results の順に返すスタブへ差し替え（テスト後に復元）。"""
        seq = list(results)
        i = [0]

        def fake(cmd, wd, to, env=None):
            v = seq[i[0] % len(seq)]
            i[0] += 1
            return (v, f"exit={0 if v else 1}")
        orig = km.run_verify
        km.run_verify = fake
        self.addCleanup(lambda: setattr(km, "run_verify", orig))

    def test_stable_results_not_flaky(self):
        self._patch_verify([True])
        self.assertEqual(km.run_verify_stable("x", Path("."), 1, 3), (True, False, "exit=0"))
        self._patch_verify([False])
        ok, flaky, _ = km.run_verify_stable("x", Path("."), 1, 3)
        self.assertEqual((ok, flaky), (False, False))

    def test_confirm_one_is_legacy_single_run(self):
        self._patch_verify([True, False])              # 交互でも confirm=1 なら1回だけ＝flaky 判定しない
        self.assertEqual(km.run_verify_stable("x", Path("."), 1, 1), (True, False, "exit=0"))

    def test_alternating_is_flaky(self):
        self._patch_verify([True, False, True])
        ok, flaky, msg = km.run_verify_stable("x", Path("."), 1, 2)
        self.assertTrue(flaky)
        self.assertIn("flaky", msg)

    def test_run_loop_quarantines_flaky_to_human(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            # local runner（run_plan_command）が confirm 実行で PASS/FAIL を跨いだ receipt を返す
            orig = km.run_plan_command
            km.run_plan_command = lambda cmd, cwd, to, env=None, confirm=1: {
                "command": cmd, "exit_code": 0, "output_tail": "",
                "flaky": True, "note": "flaky: 実行結果が不安定"}
            self.addCleanup(lambda: setattr(km, "run_plan_command", orig))
            res = km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False,
                                      verify_confirm=2, max_cycles=10))
            self.assertEqual(res["counts"]["done"], 0)          # done にしない
            self.assertEqual(res["counts"]["blocked"], 1)       # 人へ隔離
            self.assertTrue((d / "needs" / "T1.md").exists())
            t = km.parse_task((d / "backlog" / "T1.md").read_text(), "T1")
            self.assertEqual(dict(t.extra).get("flake"), "1")   # flake マーカ
            self.assertEqual(t.retries, 0)                      # NG churn しない（retry 増やさない）

    def test_run_loop_stable_pass_still_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            self._patch_verify([True])                  # 常に PASS（confirm=2 でも一致）
            res = km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False,
                                      verify_confirm=2, max_cycles=10))
            self.assertEqual(res["counts"]["done"], 1)          # 安定 PASS は従来どおり done


class TestVerifyProgress(unittest.TestCase):
    """履歴一致 verify による偽 done の対策（成果参照の真正化・KIRO_BASE_REV・no-progress ガード）。"""

    def _git(self, d, *a):
        import subprocess as sp
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        sp.run(["git", "-C", str(d), *a], env=env, capture_output=True)

    def _repo(self, d, verify="`git log --oneline | grep -q refactor`"):
        (d / "app.py").write_text("x\n", encoding="utf-8")
        self._git(d, "init", "-q"); self._git(d, "add", "-A")
        self._git(d, "commit", "-qm", "refactor: pre-existing helper")   # 過去の修正コミット
        mkbf = d / "backlog"; mkbf.mkdir(exist_ok=True)
        (mkbf / "R1.md").write_text(f"## R1: x\n- status: ready\n- verify: {verify}\n", encoding="utf-8")

    def _cfg(self, d, **kw):
        return cfg_for(Path(d), dry_run=True, learn=False, auto_adjudicate=False,
                       max_cycles=5, **kw)

    def _ref(self, d):
        rows = [l for l in (d / "DELIVERY.md").read_text(encoding="utf-8").splitlines()
                if l.startswith("| R1")]
        return rows[0].split("|")[4].strip() if rows else ""

    def test_delivery_ref_truthful_no_change(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); self._repo(d)
            res = km.run_loop(self._cfg(d))                # 既定: done のまま（挙動不変）
            self.assertEqual(res["counts"]["done"], 1)
            self.assertEqual(self._ref(d), "(変更なし)")    # 既存コミットを成果物と偽らない

    def test_delivery_ref_prefers_act_pr(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); self._repo(d)
            base = km.git_change_baseline(d)
            self.assertIn("/pull/7", km.extract_delivery_ref(
                "done https://github.com/o/r/pull/7", self._cfg(d), base))

    def test_meaningful_changes_excludes_kiro_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); self._repo(d)
            cfg = self._cfg(d)
            base = km.git_change_baseline(d)
            (d / "needs").mkdir(exist_ok=True)
            (d / "needs" / "X.md").write_text("state", encoding="utf-8")   # kiro 状態ファイル
            (d / "journal.md").write_text("log", encoding="utf-8")
            self.assertEqual(km.meaningful_changes(cfg, base), set())      # 成果物ゼロ扱い
            (d / "app.py").write_text("changed\n", encoding="utf-8")        # 本物のコード変更
            self.assertIn("app.py", km.meaningful_changes(cfg, base))

    def test_kiro_base_rev_passed_to_verify(self):
        with tempfile.TemporaryDirectory() as d:
            # 差分スコープ verify: baseline 以降に該当コミットが無ければ正しく未done
            d = Path(d)
            self._repo(d, '`test -n "$(git log $KIRO_BASE_REV..HEAD --grep refactor 2>/dev/null)"`')
            res = km.run_loop(self._cfg(d))
            self.assertEqual(res["counts"]["done"], 0)      # 過去コミットには騙されない

    def test_require_progress_blocks_false_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); self._repo(d)
            res = km.run_loop(self._cfg(d, require_progress=True))
            self.assertEqual(res["counts"]["done"], 0)
            self.assertEqual(res["counts"]["blocked"], 1)
            self.assertTrue((d / "needs" / "R1.md").exists())
            t = km.parse_task((d / "backlog" / "R1.md").read_text(), "R1")
            self.assertEqual(dict(t.extra).get("noprogress"), "1")

    def test_expect_none_opts_out(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._repo(d)
            (d / "backlog" / "R1.md").write_text(
                "## R1: x\n- status: ready\n- verify: `git log|grep -q refactor`\n- expect: none\n",
                encoding="utf-8")
            res = km.run_loop(self._cfg(d, require_progress=True))
            self.assertEqual(res["counts"]["done"], 1)      # 正当な無変更タスクは opt-out で done

    def test_no_diff_opts_out_of_progress_guard(self):
        """W4: 差分ゼロが正の宣言。no-progress ガードは expect: none と同じく外れる。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._repo(d)
            (d / "backlog" / "R1.md").write_text(
                "## R1: x\n- status: ready\n- verify: `git log|grep -q refactor`\n"
                "- no_diff: 調査のみ\n",
                encoding="utf-8")
            res = km.run_loop(self._cfg(d, require_progress=True))
            self.assertEqual(res["counts"]["done"], 1)

    def test_expect_changes_opts_in_without_global(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._repo(d)
            (d / "backlog" / "R1.md").write_text(
                "## R1: x\n- status: ready\n- verify: `git log|grep -q refactor`\n- expect: changes\n",
                encoding="utf-8")
            res = km.run_loop(self._cfg(d))                 # グローバル未指定でもタスク単位で発動
            self.assertEqual(res["counts"]["done"], 0)
            self.assertEqual(res["counts"]["blocked"], 1)


class TestProtectPaths(unittest.TestCase):
    """パス保護ゲート（safety denylist）— act が保護パスを触ったら done せず人の承認(review)へ。"""

    def _git_init(self, d):
        import subprocess as sp
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "commit", "-qm", "init", "--allow-empty"]):
            sp.run(cmd, cwd=str(d), env=env, capture_output=True)

    def _act_writes(self, relpath):
        def _act(t, c, loc):
            f = Path(c.workdir) / relpath
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("changed", encoding="utf-8")
            return (True, "ok")
        return _act

    def test_glob_matcher_denylist(self):
        pats = [".env", ".env.*", "**/secrets/**", "**/*_key*", "auth/**",
                "k8s/production/**", "**/migrations/**"]
        for path in [".env", ".env.local", "app/secrets/db.yaml", "secrets/x",
                     "src/api_key.ts", "auth/login.py", "k8s/production/d.yaml",
                     "db/migrations/001.sql"]:
            self.assertIsNotNone(km.path_protected(path, pats), path)
        for path in ["src/app.py", "README.md", "k8s/staging/d.yaml", "docs/auth-notes.md"]:
            self.assertIsNone(km.path_protected(path, pats), path)

    def test_changed_paths_detects_dirty_and_commits(self):
        with tempfile.TemporaryDirectory() as d:
            import subprocess as sp
            d = Path(d)
            (d / "a.txt").write_text("1", encoding="utf-8")
            self._git_init(d)
            base = km.git_change_baseline(d)
            (d / "a.txt").write_text("2", encoding="utf-8")      # 既存を変更（dirty）
            (d / "sub").mkdir()
            (d / "sub" / "b.txt").write_text("n", encoding="utf-8")  # 新規（untracked）
            changed = km.changed_paths_since(d, base)
            self.assertIn("a.txt", changed)
            self.assertIn("sub/b.txt", changed)
            # コミットしても baseline 以降の差分として検出される
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            sp.run(["git", "add", "-A"], cwd=str(d), env=env, capture_output=True)
            sp.run(["git", "commit", "-qm", "c"], cwd=str(d), env=env, capture_output=True)
            self.assertIn("sub/b.txt", km.changed_paths_since(d, base))

    def _cfg(self, d):
        return cfg_for(Path(d), dry_run=False, learn=False, auto_adjudicate=False, max_cycles=10)

    def test_protected_change_goes_to_review(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._git_init(d)
            mkb(d, "T1", verify="true")
            (d / "policy.md").write_text("protect: secrets/**\n", encoding="utf-8")
            res = km.run_loop(self._cfg(d), act=self._act_writes("secrets/api.yaml"))
            self.assertEqual(res["counts"].get("review", 0), 1)   # done せず検収待ち
            self.assertEqual(res["counts"]["done"], 0)
            self.assertTrue((d / "needs" / "T1.md").exists())
            t = km.parse_task((d / "backlog" / "T1.md").read_text(), "T1")
            self.assertIn("secrets/api.yaml", dict(t.extra).get("gate_protect", ""))

    def test_safe_change_completes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._git_init(d)
            mkb(d, "T1", verify="true")
            (d / "policy.md").write_text("protect: secrets/**\n", encoding="utf-8")
            res = km.run_loop(self._cfg(d), act=self._act_writes("src/app.py"))
            self.assertEqual(res["counts"]["done"], 1)            # 保護外なので通常 done
            self.assertEqual(res["counts"].get("review", 0), 0)

    def test_no_protect_policy_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._git_init(d)
            mkb(d, "T1", verify="true")
            res = km.run_loop(self._cfg(d), act=self._act_writes("secrets/api.yaml"))
            self.assertEqual(res["counts"]["done"], 1)            # protect 未設定なら従来どおり


class TestVerifyAssist(unittest.TestCase):
    def test_template_expands_deterministically(self):
        self.assertEqual(km.expand_verify_template("file-contains :: web/x.html :: 最終更新"),
                         "grep -qF -- '最終更新' 'web/x.html'")
        self.assertEqual(km.expand_verify_template("file-exists :: report.py"),
                         "test -e 'report.py'")
        self.assertEqual(km.expand_verify_template("cmd-succeeds :: pytest -q tests/"),
                         "pytest -q tests/")
        self.assertIn("KIRO_BASE_REV", km.expand_verify_template("diff-contains :: def foo"))
        self.assertIsNone(km.expand_verify_template("unknown-template :: x"))

    def test_enqueue_template_materializes_verify_and_ready(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "X", "verify_template": "file-exists :: out.txt"})
            self.assertEqual(t.verify, "test -e 'out.txt'")
            self.assertEqual(t.norm_status(), "ready")
            self.assertIn(("verify_source", "template"), t.extra)

    def test_accept_becomes_acceptance_criterion_not_a_synthesized_command(self):
        """S5: `accept:` は 1 項目の受入基準として扱い、コマンドへ合成しない。

        自然文を LLM が 1 発でコマンド化する方式は、環境差で大半が失敗して人へ倒れるうえ、
        合成されたコマンドが「たまたま通る劣化した検証」でも人には見抜けなかった。
        """
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "概要を書く", "accept": "README に ## 概要 がある"})
            self.assertEqual(t.norm_status(), "ready")
            self.assertEqual(t.verify, "")
            self.assertEqual(km.task_acceptance(t), ["README に ## 概要 がある"])
            # ensure_verify はもう合成しない（verify_template の決定的展開だけが残る）
            self.assertFalse(km.ensure_verify(cfg, t, agent_run=lambda p, m: self.fail("合成しない")))
            self.assertEqual(t.verify, "")

    def test_acceptance_lines_round_trip_and_take_priority(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (cfg.backlog).mkdir(parents=True, exist_ok=True)
            (cfg.backlog / "T9.md").write_text(
                "## T9: x\n- status: ready\n- acceptance: 候補が並ぶ\n"
                "- acceptance: 理由が出る\n- accept: 旧形式\n", encoding="utf-8")
            t = km.load_tasks(cfg.backlog)[0]
            # Task.extra は (key, value) のリストなので、同名キーの複数行がそのまま往復する
            self.assertEqual(km.task_acceptance(t), ["候補が並ぶ", "理由が出る"])
            km.persist_task(cfg, t)
            self.assertEqual(km.task_acceptance(km.load_tasks(cfg.backlog)[0]),
                             ["候補が並ぶ", "理由が出る"])

    def test_has_verify_plan_counts_acceptance(self):
        mk = lambda **kw: km.Task(id="T", title="t", **kw)
        self.assertTrue(mk(verify="true").__class__ and km.has_verify_plan(mk(verify="true")))
        self.assertTrue(km.has_verify_plan(mk(extra=[("acceptance", "a")])))
        self.assertTrue(km.has_verify_plan(mk(extra=[("accept", "a")])))
        self.assertTrue(km.has_verify_plan(mk(extra=[("verify_template", "file-exists :: x")])))
        self.assertFalse(km.has_verify_plan(mk()))

    # --- S5: 証跡ベースの検証（verifier） ------------------------------------------------

    def _report(self, criteria):
        return "本文\n\n```json\n" + json.dumps({"criteria": criteria}, ensure_ascii=False) + "\n```"

    def test_normalize_fails_closed_without_explicit_pass(self):
        # 明示の pass が無い出力を pass 扱いすると、壊れた検証がゲートを素通りする
        for body in ("LGTM 問題ありません", "", "{}", self._report([{"id": 1, "verdict": "maybe"}])):
            r = km.normalize_verification(body, ["基準A"])
            self.assertEqual(r["criteria"][0]["verdict"], "fail", body[:20])
            self.assertFalse(r["ok"])

    def test_normalize_rejects_pass_without_evidence(self):
        # 「確認しました」だけで pass にできる穴を塞ぐ（verifier の自己欺瞞への防御）
        r = km.normalize_verification(
            self._report([{"id": 1, "verdict": "pass", "note": "確認しました"}]), ["基準A"])
        self.assertEqual(r["criteria"][0]["verdict"], "fail")
        self.assertIn("証跡", r["criteria"][0]["note"])

    def test_normalize_accepts_pass_with_command_or_file_evidence(self):
        for ev in ({"commands": ["pytest -q"]}, {"files": ["src/a.py:12"]}):
            r = km.normalize_verification(
                self._report([{"id": 1, "verdict": "pass", "evidence": ev}]), ["基準A"])
            self.assertTrue(r["ok"], ev)
            self.assertEqual(r["pass"], 1)

    def test_normalize_counts_and_orders_by_criteria(self):
        r = km.normalize_verification(self._report([
            {"id": 2, "verdict": "fail", "note": "落ちた"},
            {"id": 1, "verdict": "pass", "evidence": {"commands": ["c"]}},
        ]), ["A", "B", "C"])
        self.assertEqual([c["text"] for c in r["criteria"]], ["A", "B", "C"])
        self.assertEqual((r["pass"], r["fail"]), (1, 2))   # 応答に無い 3 番目も fail（欠落＝閉じる）

    def test_unverifiable_is_not_a_failure(self):
        r = km.normalize_verification(self._report([
            {"id": 1, "verdict": "unverifiable", "note": "docker が無い"}]), ["A"])
        self.assertEqual((r["fail"], r["unverifiable"], r["ok"]), (0, 1, False))

    def test_verifier_saves_report_and_summary_goes_to_needs(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="x", extra=[("acceptance", "候補が並ぶ")])
            body = self._report([
                {"id": 1, "verdict": "pass", "evidence": {"commands": ["npm test"],
                                                          "output": "24 passing"}},
                {"id": 2, "verdict": "pass", "evidence": {"files": ["a.js:1"]}}])
            result = km.normalize_verification(body, ["候補が並ぶ", km.DIFF_CRITERION])
            self.assertTrue(result["ok"])
            rel = km.save_verification_report(cfg, t, result, "9f3a1c2", body)
            self.assertEqual(rel, "verifications/T1/9f3a1c2.md")
            report = (cfg.backlog.parent / rel).read_text(encoding="utf-8")
            self.assertIn("候補が並ぶ", report)
            self.assertIn("npm test", report, "証跡がレポートに残る（人が読む一次資料）")
            km.write_needs_file(cfg, t, "検収待ち", review=True, verification=result)
            票 = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertIn("verification:", 票)

    def test_settle_treats_unverifiable_without_burning_retries(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, dry_run=True, max_cycles=1)
            (d / "backlog").mkdir(parents=True, exist_ok=True)
            (d / "backlog" / "T1.md").write_text(
                "## T1: T1\n- status: ready\n- source: human\n- verify: \n- retries: 0\n"
                "- acceptance: 何かが動く\n", encoding="utf-8")
            orig = km._run_agent_cli
            km._run_agent_cli = lambda prompt, model, purpose="": self._report(
                [{"id": 1, "verdict": "unverifiable", "note": "docker がありません"},
                 {"id": 2, "verdict": "pass", "evidence": {"files": ["a"]}}])
            try:
                km.run_loop(cfg)
            finally:
                km._run_agent_cli = orig
            t = km.parse_task((cfg.backlog / "T1.md").read_text(encoding="utf-8"), "T1")
            self.assertEqual(t.retries, 0, "検証不能はリトライを消費しない")
            self.assertEqual(t.get("env_resume"), "1", "環境を直して approve すれば続きから")
            self.assertIn("検証不能", (cfg.needs / "T1.md").read_text(encoding="utf-8"))

    def _verification(self, note: str = "docker がありません") -> dict:
        return {"criteria": [{"id": 1, "text": "何かが動く", "verdict": "unverifiable",
                              "evidence": {"commands": [], "output": "", "files": []},
                              "note": note}],
                "pass": 0, "fail": 0, "unverifiable": 1, "ok": False}

    def test_unverifiable_is_published_to_the_board_before_asking_a_human(self):
        # P4-b: 「このノードでは確かめられない」は、まず機械で試せる解決（板への公示）を
        # 試す。人検収へ直行するのは公示できないときだけ（C3・C5）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mk_state_repo(d)
            cfg = cfg_for(d, board=str(d / "board"))
            t = km.Task(id="T1", title="x", status="doing", extra=[("acceptance", "何かが動く")])
            km.persist_task(cfg, t)
            self.assertTrue(km.delegate_verification(cfg, t, self._verification(), "理由", 1))
            self.assertEqual(t.norm_status(), "offloaded")       # 人ではなく板の結果待ち
            self.assertEqual(t.get("flow_loc"), km.VERIFY_DELEGATION_LOC)
            self.assertFalse(t.get("env_resume"))                # approve 待ちではない
            did = t.get("flow_run")
            post = km.BoardRepo(str(d / "board")).read_post(did)
            self.assertEqual(post["workload"], "flow")
            self.assertIn("確かめて報告", post["goal"])
            self.assertIn("成果物を変更しないでください", post["goal"])  # 直すことは頼まない
            self.assertIn("何かが動く", post["goal"])
            self.assertIn("理由", post["goal"])

    def test_verification_is_not_delegated_without_a_board(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mk_state_repo(d)
            cfg = cfg_for(d)                                   # board 未設定
            t = km.Task(id="T1", title="x", extra=[("acceptance", "何かが動く")])
            self.assertFalse(km.delegate_verification(cfg, t, self._verification(), "r", 1))

    def test_verification_is_not_delegated_without_a_revision(self):
        # 成果の版が特定できないと、返ってきた判定を今の成果に結び付けられない → 人へ。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, board=str(d / "board"))            # git ではない workdir
            t = km.Task(id="T1", title="x", extra=[("acceptance", "何かが動く")])
            self.assertFalse(km.delegate_verification(cfg, t, self._verification(), "r", 1))

    def test_external_verdict_is_accepted_for_the_same_revision_only(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            git_init(d)
            (d / "a.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(d), "commit", "-qm", "c"], check=True,
                           capture_output=True)
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="x", extra=[("acceptance", "何かが動く")])
            rev = km.git_change_baseline(d)[0]
            km.save_external_verdict(cfg, t, rev, {"verdict": "pass", "did": "dg-1", "by": "pc-b"})
            ok, flaky, msg, result = km._run_task_verifier(cfg, t, d)
            self.assertTrue(ok)
            self.assertTrue(result["external"])
            self.assertIn("pc-b", msg)                      # 誰が確かめたかを残す
            self.assertIn("pc-b", result["criteria"][0]["note"])
            self.assertIn("external_by", t.get("verification"))
            # 別の版の判定は受理しない（古い版で通った、を今の版の根拠にしない）
            self.assertIsNone(km.read_external_verdict(cfg, t, "0123456"))
            self.assertIsNone(km.read_external_verdict(cfg, t, ""))

    def test_delegated_verification_result_is_ingested(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mk_state_repo(d)
            cfg = cfg_for(d, board=str(d / "board"))
            t = km.Task(id="T1", title="x", status="offloaded", extra=[("acceptance", "A")])
            t.set("verify_rev", "abc1234")
            km.persist_task(cfg, t)
            km._settle_verify_delegation(cfg, t, "dg-1", True, "board delegation dg-1 done", 1, {})
            self.assertEqual(t.norm_status(), "ready")        # 次の巡回で検収へ進む
            rec = json.loads(km.external_verdict_path(cfg, t, "abc1234").read_text(encoding="utf-8"))
            self.assertEqual(rec["verdict"], "pass")
            self.assertEqual(rec["did"], "dg-1")

    def test_undecided_delegation_falls_back_to_the_human(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mk_state_repo(d)
            cfg = cfg_for(d, board=str(d / "board"))
            km.ensure_dirs(cfg)
            t = km.Task(id="T1", title="x", status="offloaded", extra=[("acceptance", "A")])
            t.set("verify_rev", "abc1234")
            km.persist_task(cfg, t)
            km._settle_verify_delegation(cfg, t, "dg-1", False, "board delegation dg-1 failed",
                                         1, {})
            self.assertEqual(t.norm_status(), "blocked")
            self.assertEqual(t.get("env_resume"), "1")        # リトライは焼かない（従来どおり）
            票 = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertIn("板へ検証を回しましたが決着しませんでした", 票)

    def test_strip_ansi_removes_escapes(self):
        raw = "\x1b[38;5;141m> \x1b[0mgrep -q foo bar.txt\x1b[0m"
        self.assertEqual(km.strip_ansi(raw), "> grep -q foo bar.txt")
        self.assertEqual(km.strip_ansi(""), "")

    def test_looks_like_shell_command(self):
        self.assertTrue(km._looks_like_shell_command("grep -q foo bar.txt"))
        self.assertTrue(km._looks_like_shell_command("test -f out && pytest -q"))
        self.assertFalse(km._looks_like_shell_command(""))
        self.assertFalse(km._looks_like_shell_command("検証できません。"))      # 全角句読点
        self.assertFalse(km._looks_like_shell_command("grep -q 'unterminated"))  # 未閉じクォート

    def test_rot_excludes_accept_or_template(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t_acc = km.Task(id="A", title="a", verify="", status="ready", extra=[("accept", "…")])
            t_bare = km.Task(id="B", title="b", verify="", status="ready")
            rot = dict((t.id, why) for t, why in km.detect_rot(cfg, [t_acc, t_bare]))
            self.assertNotIn("A", rot)               # accept ありは unverifiable にしない
            self.assertIn("B", rot)                  # 素の verify 無しは rot

    def test_audit_does_not_flag_accept_task(self):
        # バグ修正: audit は accept/verify_template を持つ ready タスクを「verify 無し（critical）」にしない
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.enqueue_task(cfg, {"title": "X", "accept": "README に概要がある"})
            audit = km.compute_audit(cfg)
            self.assertFalse(any(rf["severity"] == "critical" for rf in audit["red_flags"]))
            verify_check = next(c for c in audit["checks"] if c["id"] == "verify_coverage")
            self.assertTrue(verify_check["ok"])

    def test_inbox_md_accept_stays_ready(self):
        # バグ修正: inbox の .md に accept があれば verify 無しでも inbox 落ちせず ready のまま
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, inbox=d / "inbox")
            cfg.inbox.mkdir(parents=True, exist_ok=True)
            (cfg.inbox / "t.md").write_text(
                "## t1: やる\n- status: ready\n- accept: README に概要がある\n", encoding="utf-8")
            created = km.ingest_inbox(cfg)
            self.assertEqual(created[0].norm_status(), "ready")

    def test_inbox_does_not_revive_a_completed_task(self):
        """done 済み（archive にある）id の再投入は取り込まない。

        明示 id は冪等キー（同じ id = 同じタスク）。done 済みの id が来たら重複投入であって
        「もう一度やれ」ではない。弾かないと完了済みの作業がまるごと再実行され、LLM のコストを
        無駄に払う（実際 archive 済みのタスクが inbox 経由で復活し、新しい run が回り始めた）。"""
        for suffix, body in ((".md", "## T1: やる\n- status: ready\n- verify: `true`\n"),
                             (".json", json.dumps({"id": "T1", "title": "やる", "verify": "true"}))):
            with self.subTest(suffix=suffix):
                with tempfile.TemporaryDirectory() as d:
                    d = Path(d)
                    cfg = cfg_for(d, inbox=d / "inbox")
                    cfg.archive_dir().mkdir(parents=True, exist_ok=True)
                    (cfg.archive_dir() / "T1.md").write_text(
                        "## T1: やる\n- status: done\n", encoding="utf-8")   # 完了済み
                    cfg.inbox.mkdir(parents=True, exist_ok=True)
                    (cfg.inbox / f"T1{suffix}").write_text(body, encoding="utf-8")

                    created = km.ingest_inbox(cfg)
                    self.assertEqual(created, [], "done 済みの id は取り込まない")
                    self.assertFalse((cfg.backlog / "T1.md").exists(), "backlog へ復活させない")
                    self.assertIn("見送り", cfg.journal.read_text(encoding="utf-8"))

    def test_inbox_still_accepts_a_new_id(self):
        # 再発した別件は新しい id で投入されるべき。それは従来どおり取り込む
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, inbox=d / "inbox")
            cfg.archive_dir().mkdir(parents=True, exist_ok=True)
            (cfg.archive_dir() / "T1.md").write_text("## T1: 済\n- status: done\n", encoding="utf-8")
            cfg.inbox.mkdir(parents=True, exist_ok=True)
            (cfg.inbox / "T2.md").write_text(
                "## T2: 別件\n- status: ready\n- verify: `true`\n", encoding="utf-8")
            created = km.ingest_inbox(cfg)
            self.assertEqual([t.id for t in created], ["T2"])


class TestVerifyFailingStep(unittest.TestCase):
    """run_verify の失敗工程の特定: `A && B && C` の途中で沈黙する工程（grep -q 等）が落ちると
    出力は成功した前段のものしか残らず、「exit=1 なのにテストは全部通っている」という読めない
    失敗になる（実際にこの読めなさでリトライが 9 回焼かれた）。set -x のトレースから
    失敗した工程を名指しする。"""

    def test_silent_middle_failure_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg = km.run_verify(
                'echo "29 passed" && grep -q nosuchtoken /dev/null && echo done', Path(d), 30)
        self.assertFalse(ok)
        self.assertIn("失敗した工程: `grep -q nosuchtoken /dev/null`", msg)
        self.assertIn("それより前の工程は成功", msg)
        self.assertIn("29 passed", msg)          # 生の出力も残す（情報を隠さない）

    def test_success_message_keeps_shape_and_hides_trace(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg = km.run_verify("echo hello && true", Path(d), 30)
        self.assertTrue(ok)
        self.assertTrue(msg.startswith("exit=0"))
        self.assertNotIn("+ ", msg)              # set -x のトレースで本文を汚さない

    def test_single_command_failure_still_named(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg = km.run_verify("false", Path(d), 30)
        self.assertFalse(ok)
        self.assertIn("失敗した工程: `false`", msg)


