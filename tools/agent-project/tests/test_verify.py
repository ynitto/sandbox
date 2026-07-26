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
            self._patch_verify([True, False])           # 1回目 PASS / 2回目 FAIL → flake
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

    def test_accept_task_is_ready_and_synthesized_in_loop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            # accept だけ（verify 無し）でも ready になる
            t = km.enqueue_task(cfg, {"title": "概要を書く", "accept": "README に ## 概要 がある"})
            self.assertEqual(t.norm_status(), "ready")
            self.assertEqual(t.verify, "")
            # run_loop の S0 で synth_verify（agent_run を差し替え）により verify が用意される
            orig = km._run_agent_cli
            km._run_agent_cli = lambda prompt, model, purpose="": "grep -q '## 概要' README.md"
            try:
                km.run_loop(cfg_for(d, dry_run=True, max_cycles=1))
            finally:
                km._run_agent_cli = orig
            reloaded = km.parse_task((cfg.backlog / f"{t.id}.md").read_text(), t.id)
            self.assertEqual(reloaded.verify, "grep -q '## 概要' README.md")
            self.assertEqual(dict(reloaded.extra).get("verify_source"), "synth")

    def test_synth_failure_leaves_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="x", verify="", extra=[("accept", "曖昧な条件")])
            def boom(prompt, model):
                raise RuntimeError("no kiro-cli")
            self.assertFalse(km.ensure_verify(cfg, t, agent_run=boom))   # 合成不能→verify 空のまま
            self.assertEqual(t.verify, "")

    def test_strip_ansi_removes_escapes(self):
        raw = "\x1b[38;5;141m> \x1b[0mgrep -q foo bar.txt\x1b[0m"
        self.assertEqual(km.strip_ansi(raw), "> grep -q foo bar.txt")
        self.assertEqual(km.strip_ansi(""), "")

    def test_synth_verify_strips_ansi_from_kiro_output(self):
        # kiro-cli の色付き出力に ANSI が混ざっても、合成した verify は素のコマンドになる
        cfg = cfg_for(Path("."))
        ansi_out = "\x1b[2K\x1b[36mgrep -q '## 概要' README.md\x1b[0m"
        cmd = km.synth_verify(cfg, "概要を書く", "README に概要", agent_run=lambda p, m: ansi_out)
        self.assertEqual(cmd, "grep -q '## 概要' README.md")
        self.assertNotIn("\x1b", cmd)

    def test_is_windows_shell_command_flags_powershell_and_cmd(self):
        for cmd in (
            'powershell.exe -Command "Test-Path foo"',
            "powershell -NoProfile -Command ls",
            "pwsh -Command Get-Item x",
            "cmd.exe /c dir",
            r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command x',
            "/mnt/c/Windows/System32/cmd.exe /c echo x",
        ):
            self.assertTrue(km._is_windows_shell_command(cmd), cmd)
        for cmd in ("pytest -q", "git diff --exit-code", "./powershell-helper.sh", ""):
            self.assertFalse(km._is_windows_shell_command(cmd), cmd)

    def test_synth_verify_rejects_unfenced_powershell_and_retries_to_posix(self):
        # 地の文の powershell.exe は _first_command_line が候補にしない（先頭トークン判定で落ちる）。
        # 合成は諦めず再試行し、POSIX コマンドを採る。⑤ の POSIX 明示が次の候補を誘導する。
        cfg = cfg_for(Path("."))
        outs = iter([
            'powershell.exe -Command "Test-Path README.md"',
            "pytest -q",
        ])
        cmd = km.synth_verify(cfg, "テストを通す", "pytest が通る",
                              agent_run=lambda p, m: next(outs), attempts=2)
        self.assertEqual(cmd, "pytest -q")

    def test_synth_verify_prompt_states_posix_and_forbids_powershell(self):
        p = km._synth_verify_prompt("t", "a")
        self.assertIn("POSIX sh", p)
        self.assertIn("powershell.exe", p)

    def test_synth_verify_rejects_fenced_powershell_with_specific_note(self):
        # フェンス付きは _first_command_line を素通りするが、_is_windows_shell_command が不採用にし、
        # PowerShell 固有の retry_note で再合成 → POSIX コマンドを採る。
        cfg = cfg_for(Path("."))
        outs = iter([
            '```powershell\npowershell.exe -Command "Test-Path x"\n```',
            "git diff --exit-code",
        ])
        notes = []

        def agent_run(prompt, model):
            notes.append(prompt)
            return next(outs)

        cmd = km.synth_verify(cfg, "t", "a", agent_run=agent_run, attempts=2)
        self.assertEqual(cmd, "git diff --exit-code")
        self.assertIn("PowerShell", notes[1])

    def test_first_command_line_returns_direct_command(self):
        self.assertEqual(km._first_command_line("\n# comment\npytest -q\n"), "pytest -q")

    def test_first_command_line_skips_unfenced_prose_before_command(self):
        output = "検証コマンドは次のとおりです。\npython3 -m pytest tools/agent-project/tests -q"
        self.assertEqual(
            km._first_command_line(output),
            "python3 -m pytest tools/agent-project/tests -q",
        )

    def test_first_command_line_skips_unpunctuated_english_prose(self):
        output = "Here is the verification command\npytest -q"
        self.assertEqual(km._first_command_line(output), "pytest -q")

    def test_first_command_line_accepts_path_and_hyphenated_cli(self):
        self.assertEqual(km._first_command_line("Run this next\n./scripts/check.sh --quick"),
                         "./scripts/check.sh --quick")
        self.assertEqual(km._first_command_line("Use the gate\ncustom-check --all"),
                         "custom-check --all")

    def test_first_command_line_extracts_all_fence_lines_in_order(self):
        output = "before\n```\nfirst\n```\nbetween\n```sh\nsecond\n```\nafter"
        self.assertEqual(km._code_fence_lines(output), ["first", "second"])

    def test_first_command_line_extracts_from_untagged_sh_and_console_fences(self):
        self.assertEqual(km._first_command_line("```\npytest -q\n```"), "pytest -q")
        self.assertEqual(
            km._first_command_line("```sh\npython3 -m pytest tools/agent-project/tests -q\n```"),
            "python3 -m pytest tools/agent-project/tests -q",
        )
        self.assertEqual(
            km._first_command_line("```console\n$ pytest -q\n```"),
            "pytest -q",
        )

    def test_first_command_line_treats_unclosed_fence_as_running_to_end(self):
        output = "before\n```zsh\n# note\npytest -q"
        self.assertEqual(km._code_fence_lines(output), ["# note", "pytest -q"])
        self.assertEqual(km._first_command_line(output), "pytest -q")

    def test_first_command_line_returns_command_from_bash_fence_after_prose(self):
        output = "確認コマンドはこちらです。\n```bash\npython3 -m pytest tools/agent-project/tests -q\n```"
        self.assertEqual(
            km._first_command_line(output),
            "python3 -m pytest tools/agent-project/tests -q",
        )

    def test_first_command_line_ignores_colon_terminated_preamble_before_fence(self):
        output = (
            "以下のコマンドで検証できます:\n"
            "```bash\n"
            "python3 -m pytest tools/agent-project/tests -q -k first_command_line\n"
            "```"
        )
        self.assertEqual(
            km._first_command_line(output),
            "python3 -m pytest tools/agent-project/tests -q -k first_command_line",
        )

    def test_first_command_line_skips_blank_and_comment_lines_inside_fence(self):
        output = """```bash

# verification notes
   # an indented comment

python3 -m pytest tools/agent-project/tests -q
echo this-later-command-must-not-be-selected
```"""
        self.assertEqual(
            km._first_command_line(output),
            "python3 -m pytest tools/agent-project/tests -q",
        )

    def test_first_command_line_skips_language_tag_remnant_inside_fence(self):
        output = "```\nbash\n# verification notes\npython3 -m pytest -q\n```"
        self.assertEqual(km._first_command_line(output), "python3 -m pytest -q")

    def test_first_command_line_strips_leading_shell_prompt_symbol(self):
        self.assertEqual(
            km._first_command_line("$ python3 -m pytest tools/agent-project/tests -q"),
            "python3 -m pytest tools/agent-project/tests -q",
        )

    def test_first_command_line_strips_japanese_label_on_command_line(self):
        self.assertEqual(
            km._first_command_line(
                '検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"'
            ),
            'codd-gate verify --base "$KIRO_BASE_REV"',
        )

    def test_first_command_line_strips_japanese_label_with_fullwidth_colon(self):
        self.assertEqual(
            km._first_command_line(
                '検証コマンド：codd-gate verify --base "$KIRO_BASE_REV"'
            ),
            'codd-gate verify --base "$KIRO_BASE_REV"',
        )

    def test_first_command_line_japanese_label_does_not_split_quoted_colon(self):
        self.assertEqual(
            km._first_command_line('git commit -m "note: fix bug"'),
            'git commit -m "note: fix bug"',
        )

    def test_first_command_line_strips_doubled_japanese_label(self):
        self.assertEqual(
            km._first_command_line(
                '検証コマンド: 検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"'
            ),
            'codd-gate verify --base "$KIRO_BASE_REV"',
        )

    def test_first_command_line_strips_japanese_label_after_prose_preamble(self):
        self.assertEqual(
            km._first_command_line(
                '以下を実行してください。検証コマンド: codd-gate verify --base "$KIRO_BASE_REV"'
            ),
            'codd-gate verify --base "$KIRO_BASE_REV"',
        )

    def test_first_command_line_returns_none_without_candidate(self):
        self.assertIsNone(km._first_command_line("\n# comment only\n"))

    def test_first_command_line_returns_none_for_prose_only(self):
        self.assertIsNone(km._first_command_line(
            "Here is how to verify the change\nReview the behavior carefully"
        ))

    def test_first_command_line_joins_continuation_lines(self):
        """回帰: 行末バックスラッシュの継続行は結合してから候補にする。

        結合せずに行単位で選ぶと `pytest -q \\` のような**途中で切れたコマンド**が採用される。
        フェンス内は構文チェックを課さないので素通りし、壊れた verify がそのまま done の唯一の
        根拠になる——実行すれば必ず落ちるので、タスクは永久にリトライと人送りを繰り返す。"""
        self.assertEqual(
            km._first_command_line("```sh\npytest -q \\\n  -k my_test\n```"),
            "pytest -q -k my_test",
        )
        self.assertEqual(
            km._first_command_line("pytest -q \\\n  -k my_test\n"),
            "pytest -q -k my_test",
        )

    def test_join_continuations_merges_backslash_continued_lines(self):
        self.assertEqual(
            km._join_continuations(["pytest -q \\", "  -k first_command_line"]),
            ["pytest -q -k first_command_line"],
        )

    def test_join_continuations_chains_multiple_continuations(self):
        self.assertEqual(
            km._join_continuations(["cmd1 \\", "cmd2 \\", "cmd3"]),
            ["cmd1 cmd2 cmd3"],
        )

    def test_join_continuations_drops_blank_and_comment_lines(self):
        self.assertEqual(
            km._join_continuations(["", "echo hi", "# comment", "echo bye"]),
            ["echo hi", "echo bye"],
        )

    def test_join_continuations_keeps_trailing_unterminated_continuation(self):
        self.assertEqual(km._join_continuations(["cmd1 \\"]), ["cmd1"])

    def test_join_continuations_returns_empty_list_for_no_input(self):
        self.assertEqual(km._join_continuations([]), [])
        self.assertEqual(km._join_continuations(["", "# only comments"]), [])

    def test_first_command_line_prose_only_never_becomes_synth_verify_command(self):
        # コマンドを含まない散文が再試行で返り続けても、verify として誤採用しない。
        cfg = cfg_for(Path("."))
        responses = iter([
            "検証方法を説明します。まず対象の動作を確認してください。",
            "決定的な検証コマンドは提示できません。",
        ])
        calls = []

        def prose_only(prompt, model):
            calls.append((prompt, model))
            return next(responses)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(
                km.synth_verify(cfg, "x", "曖昧", agent_run=prose_only, attempts=2),
                "",
            )
        self.assertEqual(len(calls), 2)
        self.assertIn("verify 合成失敗", stderr.getvalue())
        self.assertIn("実行可能なコマンド行がなかった", stderr.getvalue())
        self.assertIn("task: x", stderr.getvalue())

    def test_synth_verify_rejects_japanese_prose(self):
        # バグ修正: エージェントが自然言語（説明/拒否文）を返しても shell へ流さない
        cfg = cfg_for(Path("."))
        prose = "この完了条件は曖昧なため、決定的な検証コマンドに変換できません。"
        self.assertEqual(km.synth_verify(cfg, "x", "曖昧", agent_run=lambda p, m: prose), "")

    def test_synth_verify_rejects_malformed_shell_prose(self):
        # 不完全なシェル構文（散文）も弾く（sh -n が syntax error にする）
        cfg = cfg_for(Path("."))
        prose = "Run the tests; if they pass, you are done"
        self.assertEqual(km.synth_verify(cfg, "x", "tests", agent_run=lambda p, m: prose), "")

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
