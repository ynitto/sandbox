"""agent-project の単体テスト — decisions（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from _shared import _seed_hits, _seed_learn  # noqa: E402,F401 — `import *` は _ 始まりを持ってこない


class TestPromotion(unittest.TestCase):
    def test_promote_writes_memory_when_proven(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            home = d / "ltmhome"
            _seed_learn(d, "T1", "build を直す", "make を使え")
            _seed_hits(d, "T1", 2)                      # 2 回効いた → 昇格
            cfg = cfg_for(d, ltm=True, ltm_home=home, promote_threshold=2)
            promoted = km.promote_learnings(cfg)
            self.assertEqual([s for s, _ in promoted], ["T1"])
            mems = list((home / "memory" / "home" / "memories" / "agent-project").glob("*.md"))
            self.assertEqual(len(mems), 1)
            txt = mems[0].read_text()
            self.assertIn("- learn: build を直す :: make を使え", txt)
            self.assertIn("promoted_from: \"decisions/T1.md\"", txt)
            # 出典に昇格マーカ → 再実行は冪等（重複しない）
            self.assertIn("- promoted:", (d / "decisions" / "T1.md").read_text())
            self.assertEqual(km.promote_learnings(cfg), [])

    def test_below_threshold_not_promoted(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            home = d / "ltmhome"
            _seed_learn(d, "T1", "build を直す", "make を使え")
            _seed_hits(d, "T1", 1)                      # 1 回だけ → まだ昇格しない
            cfg = cfg_for(d, ltm=True, ltm_home=home, promote_threshold=2)
            self.assertEqual(km.promote_learnings(cfg), [])
            self.assertFalse((home / "memory").exists())

    def test_noop_when_ltm_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _seed_learn(d, "T1", "x", "y"); _seed_hits(d, "T1", 5)
            cfg = cfg_for(d, ltm=False, ltm_home=d / "ltmhome")
            self.assertEqual(km.promote_learnings(cfg), [])

    def test_recall_falls_back_to_ltm(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            home = d / "ltmhome"
            mem = home / "memory" / "home" / "memories" / "agent-project"
            mem.mkdir(parents=True)
            (mem / "m.md").write_text(
                "---\nid: mem-1\n---\n## 学び・結論\n- learn: build を直す :: make を使え\n",
                encoding="utf-8")
            cfg = cfg_for(d, ltm=True, ltm_home=home)   # ローカル decisions 無し
            task = km.Task(id="T9", title="build を直す")
            res = km.find_learned_resolution(cfg, task)
            self.assertIsNotNone(res)
            self.assertEqual(res[1], "make を使え")
            self.assertTrue(res[0].startswith("ltm:"))


class TestDecisionRecords(unittest.TestCase):
    def test_human_interaction_resolution_is_projected_once_without_bypassing_task_approval(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            interaction = cfg.bus / "runs" / "run1" / "interactions" / "ix-1234567890abcdef"
            interaction.mkdir(parents=True)
            (interaction / "request.json").write_text(json.dumps({
                "interaction_id": "ix-1234567890abcdef", "mode": "approval",
            }), encoding="utf-8")
            resolution = {
                "interaction_id": "ix-1234567890abcdef", "outcome": "approved",
                "actor": "dashboard-user", "answer": {"decision": "approved"},
                "resolved_at": "2026-08-10T00:00:00Z",
            }
            (interaction / "resolution.json").write_text(json.dumps(resolution), encoding="utf-8")

            self.assertEqual(km.project_interaction_decisions(cfg, "T1", "run1"), 1)
            self.assertEqual(km.project_interaction_decisions(cfg, "T1", "run1"), 0)
            text = (cfg.decisions / "T1.md").read_text(encoding="utf-8")
            self.assertIn("interaction:ix-1234567890abcdef", text)
            self.assertIn("action  : human-interaction-approved", text)
            self.assertIsNone(km.last_human_decision(cfg, "T1"),
                              "工程内の承認はタスク全体の検収承認ではない")

    def test_approve_hold_reprioritize_per_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d, actor="bob")
            self.assertEqual(km.cmd_approve(c, "T1", "直した"), 0)
            self.assertEqual(km.load_tasks(d / "backlog")[0].status, "ready")
            self.assertIn("DR-0001", (d / "decisions" / "T1.md").read_text())

            mkb(d, "T2", verify="true")
            km.cmd_hold(c, "T2", "本番は手動")
            self.assertIn("deny: T2", (d / "policy.md").read_text())
            self.assertTrue((d / "needs" / "T2.md").exists())

            km.cmd_reprioritize(c, "T1", "pin", "急ぎ")
            self.assertIn("pin: T1", (d / "policy.md").read_text())
            self.assertIn("DR-0002", (d / "decisions" / "T1.md").read_text())

    def test_approve_releases_the_hold(self):
        """hold（deny）したタスクを approve したら、policy の deny も解ける。

        解けないと承認が一方通行で無効になる: status は ready に戻るが policy の deny が残り、
        次の triage が policy:deny を見て即 blocked へ引き戻す。人が何度承認しても実行されない
        （実際そうなっていた: 承認した 3 タスクが起動直後に全部 blocked へ戻った）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.cmd_hold(c, "T1", "いったん止める")
            self.assertIn("deny: T1", (d / "policy.md").read_text())
            self.assertEqual(km.cmd_approve(c, "T1", "やっぱり進める"), 0)
            self.assertEqual(km.load_tasks(d / "backlog")[0].status, "ready")
            self.assertNotIn("deny: T1", (d / "policy.md").read_text(), "deny が解ける")
            # triage を通しても blocked へ引き戻されない（＝承認が実際に効く）
            tasks = km.load_tasks(d / "backlog")
            moved = km.triage(tasks, km.load_policy(d / "policy.md"))
            self.assertNotIn("policy:deny", " ".join(why for _t, why in moved))
            self.assertNotEqual(tasks[0].norm_status(), "blocked")

    def test_approve_completes_blocked_task_without_verify(self):
        """verify 未定義で人の確認待ち（blocked）になったタスクは、承認で done 確定する。

        従来は approve-and-fix（ready 積み直し）に落ちて同じ工程が再実行され、また
        verify 未定義で blocked に戻る無限往復だった（承認しても完了できない不具合）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 成果確認待ち\n- status: blocked\n- source: human\n"
                "- verify: \n- retries: 1\n"
                "- needs_reason: verify 未定義（工程は完了しています。完了条件が無いため"
                "自動では done にできません。成果を確認し、問題なければ approve してください）\n",
                encoding="utf-8")
            c = cfg_for(d)
            self.assertEqual(km.cmd_approve(c, "T1", "成果を確認した"), 0)
            self.assertEqual(km.load_tasks(d / "backlog"), [])       # backlog から消える
            self.assertTrue((d / "archive" / "T1.md").exists())      # done として退避（納品書つき）
            self.assertIn("action  : approve-done", (d / "decisions" / "T1.md").read_text())

    def test_approve_requeues_blocked_env_failure_even_without_verify(self):
        # 環境要因（env_resume）の blocked は verify が無くても done にしない —
        # 「環境を直してから approve すると続きから再開」の契約（ready 積み直し）を守る。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 環境エラー\n- status: blocked\n- source: human\n"
                "- verify: \n- retries: 1\n- env_resume: 1\n"
                "- needs_reason: [agent-error:auth] 環境の問題（認証切れ）… verify 未定義\n",
                encoding="utf-8")
            c = cfg_for(d)
            self.assertEqual(
                km.cmd_approve(c, "T1", "検証失敗を確認・受容して完了"), 0
            )
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].status, "ready")

    def test_approve_completes_blocked_verification_failure_after_done_run(self):
        """成果生成runがdoneなら、最終検証NGを人が受領してdone確定できる。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 成果は完成・最終検証NG\n- status: blocked\n- source: human\n"
                "- verify: npm test\n- retries: 3\n- last_run: run-done\n"
                "- needs_reason: 繰り返し NG（exit=2）\n",
                encoding="utf-8",
            )
            c = cfg_for(d)
            run_dir = c.bus / "runs" / "run-done"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text(
                json.dumps({"status": "done"}), encoding="utf-8"
            )

            self.assertEqual(km.cmd_approve(c, "T1", "成果を確認し、この検証差異を受容する"), 0)
            self.assertEqual(km.load_tasks(d / "backlog"), [])
            self.assertTrue((d / "archive" / "T1.md").exists())
            self.assertIn("action  : approve-done", (d / "decisions" / "T1.md").read_text())

    def test_approve_completes_explicitly_accepted_verification_failure_with_env_resume(self):
        """env_resumeが残っていても、完了runの検証差異を明示受容すればdone確定できる。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 成果は完成・回帰検証NG\n- status: blocked\n- source: human\n"
                "- verify: codd-gate verify\n- retries: 3\n- last_run: run-done\n"
                "- env_resume: 1\n"
                "- needs_reason: 回帰検知: codd-gate verify 失敗（exit=1）\n",
                encoding="utf-8",
            )
            c = cfg_for(d)
            run_dir = c.bus / "runs" / "run-done"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text(
                json.dumps({"status": "done"}), encoding="utf-8"
            )

            self.assertEqual(
                km.cmd_approve(c, "T1", "検証失敗を確認・受容して完了"), 0
            )
            self.assertEqual(km.load_tasks(d / "backlog"), [])
            self.assertTrue((d / "archive" / "T1.md").exists())

    def test_approve_does_not_complete_verification_failure_before_run_done(self):
        """run未完了なら従来どおりreadyへ戻し、途中成果を完了扱いしない。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 実行途中\n- status: blocked\n- source: human\n"
                "- verify: npm test\n- retries: 1\n- last_run: run-failed\n"
                "- needs_reason: 検証コマンドが失敗しました（exit=2）\n",
                encoding="utf-8",
            )
            c = cfg_for(d)
            run_dir = c.bus / "runs" / "run-failed"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text(
                json.dumps({"status": "failed"}), encoding="utf-8"
            )

            self.assertEqual(
                km.cmd_approve(c, "T1", "検証失敗を確認・受容して完了"), 0
            )
            self.assertEqual(km.load_tasks(d / "backlog")[0].status, "ready")

    def test_approve_complete_flag_completes_regardless_of_reason_wording(self):
        """complete=True は承認理由の文面に依存せず done 確定する。

        以前は理由に「検証」「受容」等のキーワードが揃ったときだけ完了し、外れると
        黙って ready へ積み直していた。推定が外れると同じ工程を再実行してまた要対応へ
        戻る往復になり、「承認して完了にできない」と繰り返し報告された。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 成果は完成・最終検証NG\n- status: blocked\n- source: human\n"
                "- verify: npm test\n- retries: 3\n- last_run: run-done\n"
                "- needs_reason: 繰り返し NG（exit=2）\n",
                encoding="utf-8",
            )
            c = cfg_for(d)
            run_dir = c.bus / "runs" / "run-done"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text(
                json.dumps({"status": "done"}), encoding="utf-8")

            # キーワードを 1 つも含まない理由でも完了する
            self.assertEqual(km.cmd_approve(c, "T1", "これでよい", complete=True), 0)
            self.assertEqual(km.load_tasks(d / "backlog"), [])
            self.assertTrue((d / "archive" / "T1.md").exists())
            self.assertIn("action  : approve-done",
                          (d / "decisions" / "T1.md").read_text())

    def test_approve_complete_flag_completes_without_run_metadata(self):
        """run の meta が読めなくても、人が完了を選べば完了する。

        画面は「検収物があるか」で承認を出す。本体が別の材料（run meta）で再判定して
        食い違うと、承認したのに完了しない状態が生まれる。判断の主は人に一本化する。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 成果あり・run メタ欠落\n- status: blocked\n- source: human\n"
                "- verify: npm test\n- retries: 2\n- last_run: run-missing\n"
                "- needs_reason: 検証コマンドが失敗しました（exit=1）\n",
                encoding="utf-8",
            )
            c = cfg_for(d)
            self.assertEqual(km.cmd_approve(c, "T1", "成果を確認して完了を承認",
                                            complete=True), 0)
            self.assertEqual(km.load_tasks(d / "backlog"), [])
            self.assertTrue((d / "archive" / "T1.md").exists())

    def test_approve_without_complete_still_requeues(self):
        """complete を渡さない従来の承認は、これまでどおり積み直し。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 直して再実行\n- status: blocked\n- source: human\n"
                "- verify: npm test\n- retries: 1\n"
                "- needs_reason: 検証コマンドが失敗しました（exit=2）\n",
                encoding="utf-8",
            )
            c = cfg_for(d)
            self.assertEqual(km.cmd_approve(c, "T1", "直したので進めて"), 0)
            self.assertEqual(km.load_tasks(d / "backlog")[0].status, "ready")

    def test_approve_complete_via_commands_drop(self):
        """agent-dashboard の投函（commands/*.json）で complete が本体まで届く。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 検収待ち\n- status: blocked\n- source: human\n"
                "- verify: npm test\n- retries: 2\n- last_run: run-done\n"
                "- needs_reason: 検証コマンドが失敗しました（exit=2）\n",
                encoding="utf-8",
            )
            c = cfg_for(d)
            cdir = d / "commands"
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "viewer-approve-t1.json").write_text(
                json.dumps({"command": "approve", "id": "T1", "complete": True,
                            "reason": "成果を確認して完了を承認",
                            "actor": "agent-dashboard"}),
                encoding="utf-8")
            km.ingest_commands(c)
            self.assertEqual(km.load_tasks(d / "backlog"), [])
            self.assertTrue((d / "archive" / "T1.md").exists())

    def test_missing_delivery_branch_requires_confirmation_only_once(self):
        """外部マージ後に成果ブランチが消えていても、再承認なら done にできる。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="review", verify="true")
            c = cfg_for(d)
            missing = "作業ブランチ ap/T1 を解決できないため、main へマージできません"
            with mock.patch.object(km, "finalize_task_delivery", return_value=(False, missing)):
                self.assertEqual(km.cmd_approve(c, "T1", "1回目", complete=True), 1)
                task = km.load_tasks(d / "backlog")[0]
                self.assertEqual(task.get("delivery_missing_branch_ack"), "ap/T1")
                self.assertIn("もう一度承認", (d / "needs" / "T1.md").read_text())

                self.assertEqual(km.cmd_approve(c, "T1", "2回目", complete=True), 0)

            self.assertEqual(km.load_tasks(d / "backlog"), [])
            self.assertTrue((d / "archive" / "T1.md").exists())

    def test_approve_from_legacy_dashboard_completion_button_is_done(self):
        """起動中の旧 dashboard が complete を送らなくても、完了ボタンの正規文言は done にする。

        Electron を更新後に再起動しないと、旧 renderer が「成果を確認して完了を承認」
        という明示的な完了意図だけを送る。これを通常 approve として積み直すと、
        同じ環境失敗 run を再開し、再び要確認へ戻る。
        """
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: 成果あり・環境失敗\n- status: blocked\n- source: human\n"
                "- verify: npm test\n- retries: 2\n- last_run: run-failed\n- env_resume: 1\n"
                "- needs_reason: [agent-error:quota] 環境の問題\n",
                encoding="utf-8",
            )
            c = cfg_for(d)
            cdir = d / "commands"
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "viewer-approve-t1.json").write_text(
                json.dumps({"command": "approve", "id": "T1",
                            "reason": "成果を確認して完了を承認",
                            "actor": "agent-dashboard"}),
                encoding="utf-8")

            km.ingest_commands(c)

            self.assertEqual(km.load_tasks(d / "backlog"), [])
            self.assertTrue((d / "archive" / "T1.md").exists())
            self.assertFalse(list(cdir.glob("*.err")))

    def test_policy_is_not_appended_twice(self):
        # policy は「人の上書き指示」の集合であって履歴ではない。同じ hold を繰り返しても増えない
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.cmd_hold(c, "T1", "止める")
            km.cmd_hold(c, "T1", "もう一度止める")
            self.assertEqual((d / "policy.md").read_text().count("deny: T1"), 1)


class TestLearning(unittest.TestCase):
    def _seed_learn(self, d, src_id, title, guide):
        cfg = cfg_for(d)
        km.ensure_dirs(cfg)
        km.append_decision(cfg, src_id, "alice", context=f"{src_id}（{title}）",
                           action="feedback-resume", reason=guide, affects="→ ready",
                           learn=(title, guide))

    def test_find_learned_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._seed_learn(d, "OLD", "fix slugify util", "lower-case と置換を直す")
            cfg = cfg_for(d)
            hit = km.find_learned_resolution(cfg, km.Task(id="NEW", title="fix slugify util again"))
            self.assertIsNotNone(hit)
            self.assertEqual(hit[0], "OLD")
            miss = km.find_learned_resolution(cfg, km.Task(id="NEW", title="完全に無関係な作業"))
            self.assertIsNone(miss)
            # 自分の履歴は学習源にしない
            self.assertIsNone(km.find_learned_resolution(cfg, km.Task(id="OLD", title="fix slugify util")))

    def test_run_auto_resolves_then_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._seed_learn(d, "OLD", "build the report file", "出力先を作ってから書く")
            mkb(d, "T1", title="build the report file", verify="false")
            res = km.run_loop(cfg_for(d, max_retries=0, max_cycles=5))
            dec = (d / "decisions" / "T1.md").read_text()
            self.assertIn("auto-resolve", dec)               # 学習で自動解決した記録
            t = res["tasks"][0]
            self.assertIn("autolearned", dict(t.extra))      # 1回だけ自動適用
            self.assertEqual(res["counts"]["blocked"], 1)    # 解決せず最終的に人の判断

    def test_no_learn_disables(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._seed_learn(d, "OLD", "build the report file", "ヒント")
            mkb(d, "T1", title="build the report file", verify="false")
            res = km.run_loop(cfg_for(d, max_retries=0, learn=False))
            self.assertFalse((d / "decisions" / "T1.md").exists())  # 自動解決せず即 block
            self.assertEqual(res["counts"]["blocked"], 1)


class TestLearnScopeAndExpiry(unittest.TestCase):
    """W10: learn のスコープ（charter/repo/全体）と失効（連続不発・人の無効化）。"""

    def _seed(self, d, src_id, title, guide):
        cfg = cfg_for(d)
        km.ensure_dirs(cfg)
        km.append_decision(cfg, src_id, "alice", context=src_id, action="feedback-resume",
                           reason=guide, affects="→ ready", learn=(title, guide))
        return cfg

    def test_charter_scoped_learn_applies_only_to_that_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util",
                             "置換を直す :: scope=charter:alpha")
            t = km.Task(id="NEW", title="fix slugify util again")
            self.assertIsNone(km.find_learned_resolution(cfg, t))    # タグ無し＝default は対象外
            t.set("charter", "alpha")
            hit = km.find_learned_resolution(cfg, t)
            self.assertEqual(hit, ("OLD", "置換を直す"))              # guide はスコープタグを外して返す

    def test_repo_scoped_learn_matches_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util", "直す :: scope=repo:tools-x")
            t = km.Task(id="NEW", title="fix slugify util again")
            self.assertIsNone(km.find_learned_resolution(cfg, t))
            t.set("workspace", "git@example.com:me/tools-x.git")
            self.assertEqual(km.find_learned_resolution(cfg, t), ("OLD", "直す"))

    def test_consecutive_misfires_expire_the_learn(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util", "直す")
            cfg = cfg_for(d, learn_misfire_limit=2)
            for _ in range(2):
                km.append_decision(cfg, "OLD", "auto", context="x", action="learn-misfire",
                                   reason="不発: T9", affects="OLD")
            t = km.Task(id="NEW", title="fix slugify util again")
            self.assertIsNone(km.find_learned_resolution(cfg, t))
            # worked が挟まれば連続が切れて復活する
            km.append_decision(cfg, "OLD", "auto", context="x", action="learn-worked",
                               reason="成功: T10", affects="OLD")
            self.assertIsNotNone(km.find_learned_resolution(cfg, t))

    def test_human_disable_via_decision_record(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util", "直す")
            km.append_decision(cfg, "OLD", "alice", context="この learn は誤り",
                               action="learn-disable", reason="前提が変わった", affects="OLD")
            self.assertIsNone(km.find_learned_resolution(
                cfg, km.Task(id="NEW", title="fix slugify util again")))

    def test_outcome_is_recorded_to_the_source(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util", "直す")
            t = km.Task(id="T1", title="x")
            t.set("autolearned", "OLD")
            km.record_learn_outcome(cfg, t, worked=False, why="また落ちた")
            km.record_learn_outcome(cfg, t, worked=False)     # 2 回目は書かない（1 タスク 1 回）
            src = (d / "decisions" / "OLD.md").read_text(encoding="utf-8")
            self.assertEqual(src.count("learn-misfire"), 1)
            self.assertIn("不発: T1", src)
            # ltm 出典・旧形式 "1" は対象外
            t2 = km.Task(id="T2", title="x")
            t2.set("autolearned", "ltm:mem-1")
            km.record_learn_outcome(cfg, t2, worked=True)
            self.assertFalse((d / "decisions" / "ltm:mem-1.md").exists())

    def test_rule_outcome_generalizes_w10_and_suspends(self):
        """Phase4 結合点: worked/misfire を rule 単位へ集計し、悪化で fail-close suspended。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util", "直す")
            cfg = cfg_for(d, learn_misfire_limit=2)
            rid = km.rule_id_for_guide("直す", "OLD")
            km.append_rule_lifecycle(cfg, "OLD", rid, "trial")
            for i, worked in enumerate((False, False)):
                t = km.Task(id=f"T{i}", title="x")
                t.set("autolearned", "OLD")
                t.set("rule_id", rid)
                t.set("feedback", "直す")
                km.record_learn_outcome(cfg, t, worked=worked, why="ng")
            src = (d / "decisions" / "OLD.md").read_text(encoding="utf-8")
            self.assertEqual(src.count("rule-outcome:"), 2)
            self.assertIn(f"rule-outcome: {rid} misfire", src)
            self.assertEqual(km.rule_lifecycle_state(cfg, rid), "suspended")
            self.assertTrue(km.rule_excluded_from_requests(cfg, rid))
            # suspended は新規 learn 照合に出ない
            self.assertIsNone(km.find_learned_resolution(
                cfg, km.Task(id="NEW", title="fix slugify util again")))

    def test_rules_hash_mismatch_is_suppressed_not_worked(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util", "直す")
            rid = km.rule_id_for_guide("直す", "OLD")
            t = km.Task(id="T1", title="x")
            t.set("autolearned", "OLD")
            t.set("rule_id", rid)
            t.set("rules_hash", "not-a-phase3-hash")  # Phase3 形式外＝成功扱いしない
            km.record_learn_outcome(cfg, t, worked=True, why="見た目は成功")
            src = (d / "decisions" / "OLD.md").read_text(encoding="utf-8")
            self.assertIn(f"rule-outcome: {rid} suppressed", src)
            self.assertIn("learn-misfire", src)  # 成功扱いしない
            self.assertNotIn("learn-worked", src)

    def test_phase3_rules_hash_stamp_allows_worked(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util", "直す")
            rid = km.rule_id_for_guide("直す", "OLD")
            km.append_rule_lifecycle(cfg, "OLD", rid, "trial")
            t = km.Task(id="T1", title="x")
            t.set("autolearned", "OLD")
            t.set("rule_id", rid)
            t.set("rules_hash", "sha256:" + ("ab" * 32))
            km.record_learn_outcome(cfg, t, worked=True, why="ok")
            src = (d / "decisions" / "OLD.md").read_text(encoding="utf-8")
            self.assertIn(f"rule-outcome: {rid} worked", src)
            self.assertIn("learn-worked", src)
            # hits 未達のため trial 維持（active には promote_threshold+outcome が要る）
            self.assertEqual(km.rule_lifecycle_state(cfg, rid), "trial")

    def test_apply_rule_command_promote_suspend_deprecate_revise(self):
        """Phase5: 人の裁定は decisions append-only（dashboard 第二 writer 禁止）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util", "直す")
            rid = km.rule_id_for_guide("直す", "OLD")
            km.append_rule_lifecycle(cfg, "OLD", rid, "trial")
            rc, detail = km.apply_rule_command(cfg, "rule-promote", rid, "人手昇格")
            self.assertEqual(rc, 0, detail)
            self.assertEqual(km.rule_lifecycle_state(cfg, rid), "active")
            # active は「全該当タスクへ注入」——hit 閾値前でも rules.md に行ができる
            self.assertIn(rid, km.rules_path(cfg).read_text(encoding="utf-8"))
            # 人手昇格は outcome を捏造しない（evidence は実測のみ。doctor が観測できる）
            src_text = (d / "decisions" / "OLD.md").read_text(encoding="utf-8")
            self.assertNotIn(f"rule-outcome: {rid} worked", src_text)
            # 未知 rid（learn / lifecycle に出典なし）は幽霊ルールを作らず拒否
            rc2, _ = km.apply_rule_command(cfg, "rule-promote", "obs-" + "0" * 16, "typo")
            self.assertEqual(rc2, 2)
            rc, detail = km.apply_rule_command(cfg, "rule-suspend", rid, "悪化")
            self.assertEqual(rc, 0, detail)
            self.assertEqual(km.rule_lifecycle_state(cfg, rid), "suspended")
            rc, detail = km.apply_rule_command(cfg, "rule-revise", rid, "文言直し")
            self.assertEqual(rc, 0, detail)
            self.assertEqual(km.rule_lifecycle_state(cfg, rid), "trial")
            self.assertTrue(any(p.name.startswith("rule-revise-") for p in cfg.needs.glob("*.md")))
            rc, detail = km.apply_rule_command(cfg, "rule-deprecate", rid, "退役")
            self.assertEqual(rc, 0, detail)
            self.assertEqual(km.rule_lifecycle_state(cfg, rid), "deprecated")

    def test_ingest_rule_command_via_commands_drop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._seed(d, "OLD", "fix slugify util", "直す")
            rid = km.rule_id_for_guide("直す", "OLD")
            km.append_rule_lifecycle(cfg, "OLD", rid, "trial")
            cdir = km.commands_dir(cfg)
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "viewer-rule-promote.json").write_text(json.dumps({
                "command": "rule-promote", "rule_id": rid, "reason": "ui",
            }), encoding="utf-8")
            done = km.ingest_commands(cfg)
            self.assertTrue(any(x.startswith("rule-promote:") for x in done), done)
            self.assertEqual(km.rule_lifecycle_state(cfg, rid), "active")


class TestDecisionCapture(unittest.TestCase):
    """人の判断（approve 理由・hold 理由）から learn/avoid を自動抽出して蓄積する（learn_capture）。"""

    def test_approve_done_emits_learn(self):
        # 検収ゲート承認（review→done）でも承認理由が learn 化され、類似案件の判断材料になる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="review", title="deploy the payments service", verify="true")
            c = cfg_for(d, actor="bob")
            self.assertEqual(km.cmd_approve(c, "T1", "本番相当の設定でのみ許可"), 0)
            dec = (d / "decisions" / "T1.md").read_text()
            self.assertIn("action  : approve-done", dec)
            self.assertIn("- learn: deploy the payments service :: 本番相当の設定でのみ許可", dec)
            # learn として横断照合に載る
            hit = km.find_learned_resolution(c, km.Task(id="NEW", title="deploy the payments service now"))
            self.assertIsNotNone(hit)
            self.assertEqual(hit[0], "T1")

    def test_hold_emits_avoid_but_not_learn(self):
        # hold は avoid（予防知識）を残す。auto-resolve 用の learn には混ぜない（意味が逆のため）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", title="deploy to production", verify="true")
            c = cfg_for(d)
            km.cmd_hold(c, "T1", "本番は手動でのみ行う")
            dec = (d / "decisions" / "T1.md").read_text()
            self.assertIn("- avoid: deploy to production :: 本番は手動でのみ行う", dec)
            self.assertNotIn("- learn:", dec)
            av = km.find_avoidance(c, km.Task(id="NEW", title="deploy to production again"))
            self.assertIsNotNone(av)
            self.assertEqual(av[0], "T1")

    def test_capture_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="review", title="deploy x", verify="true")
            c = cfg_for(d, learn_capture=False)
            km.cmd_approve(c, "T1", "ok")
            self.assertNotIn("- learn:", (d / "decisions" / "T1.md").read_text())
            mkb(d, "T2", title="hold y", verify="true")
            km.cmd_hold(c, "T2", "手動")
            self.assertNotIn("- avoid:", (d / "decisions" / "T2.md").read_text())


class ProjectRulesTests(unittest.TestCase):
    """プロジェクトルール（rules.md）: 人が書く恒常ルール＋効いた learn の自動昇格。
    learn の recall（類似タスク限定）と違い全タスクへ常時注入されることを検証する。"""

    def test_rules_context_reads_bounded_and_strips_comments(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self.assertEqual(km.project_rules_context(cfg), "")     # 無ければ空（後方互換）
            km.rules_path(cfg).write_text(
                "# プロジェクトルール\n\n- テストは pytest -q で回す\n"
                "<!-- learn:T1 hits=2 -->\n- コミットメッセージは日本語\n", encoding="utf-8")
            ctx = km.project_rules_context(cfg)
            self.assertIn("pytest -q", ctx)
            self.assertIn("日本語", ctx)
            self.assertNotIn("<!--", ctx)                           # 出典コメントは注入しない
            self.assertLessEqual(len(km.project_rules_context(cfg, limit=10)), 10)

    def test_build_request_injects_rules_for_every_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.rules_path(cfg).write_text("- テストは pytest -q で回す\n", encoding="utf-8")
            # タイトルが全く似ていないタスクにも届く（learn の Jaccard recall との違い）
            t = km.Task(id="T1", title="全然関係ない別の作業", verify="true")
            req = km.build_request(t, cfg)
            self.assertIn("プロジェクトルール", req)
            self.assertIn("pytest -q", req)

    def _learn_setup(self, d, hits=2):
        cfg = cfg_for(d)
        cfg.decisions.mkdir(parents=True, exist_ok=True)
        (cfg.decisions / "OLD.md").write_text(
            "## DR-0001  2026-07-01  actor: human\n"
            "- context : OLD の判断\n- action  : feedback-resume\n"
            "- reason  : x\n- affects : OLD\n"
            "- learn: テストの回し方 :: テストは必ず pytest -q で実行する\n", encoding="utf-8")
        body = ""
        for i in range(hits):
            body += (f"## DR-{i+1:04d}  2026-07-0{i+2}  actor: auto\n"
                     f"- context : T{i+2} を学習で自動解決\n- action  : auto-resolve\n"
                     f"- reason  : learned from OLD: テストは必ず pytest -q で実行する\n"
                     f"- affects : T{i+2} → ready\n")
        (cfg.decisions / "T2.md").write_text(body, encoding="utf-8")
        return cfg

    def test_promote_rules_appends_once_with_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._learn_setup(d, hits=2)                      # promote_threshold 既定 2
            promoted = km.promote_rules(cfg)
            self.assertEqual(promoted, ["OLD"])
            text = km.rules_path(cfg).read_text(encoding="utf-8")
            self.assertIn("pytest -q で実行する", text)
            self.assertIn("learn:OLD hits=2", text)                 # 出典つき（人が消してよい）
            self.assertIn("state:trial", text)                      # 自動は trial から
            self.assertIn("rule:obs-", text)
            self.assertIn("- rules-promoted: rules.md",
                          (cfg.decisions / "OLD.md").read_text(encoding="utf-8"))
            self.assertIn("- rule-lifecycle:",
                          (cfg.decisions / "OLD.md").read_text(encoding="utf-8"))
            # 冪等: 2 回目は追記しない
            self.assertEqual(km.promote_rules(cfg), [])
            self.assertEqual(text, km.rules_path(cfg).read_text(encoding="utf-8"))

    def test_promote_rules_respects_threshold_and_flag(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._learn_setup(d, hits=1)                      # しきい値未満
            self.assertEqual(km.promote_rules(cfg), [])
            self.assertFalse(km.rules_path(cfg).exists())
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._learn_setup(d, hits=2)
            cfg.rules_capture = False                               # opt-out
            self.assertEqual(km.promote_rules(cfg), [])
            self.assertFalse(km.rules_path(cfg).exists())

    def test_promote_rules_keeps_human_text(self):
        # 人が書いた本文は温存し、自動昇格節にだけ追記する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._learn_setup(d, hits=2)
            km.rules_path(cfg).write_text(
                "# プロジェクトルール\n\n- コミットメッセージは日本語\n", encoding="utf-8")
            km.promote_rules(cfg)
            text = km.rules_path(cfg).read_text(encoding="utf-8")
            self.assertIn("コミットメッセージは日本語", text)
            self.assertIn(km.RULES_AUTO_SECTION, text)
            self.assertIn("pytest -q で実行する", text)

    def test_suspended_auto_rule_excluded_from_context_human_kept(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._learn_setup(d, hits=2)
            km.rules_path(cfg).write_text(
                "# プロジェクトルール\n\n- 人手ルールは残す\n", encoding="utf-8")
            km.promote_rules(cfg)
            rid = km.rule_id_for_guide("テストは必ず pytest -q で実行する", "OLD")
            km.append_rule_lifecycle(cfg, "OLD", rid, "suspended", why="test")
            ctx = km.project_rules_context(cfg)
            self.assertIn("人手ルールは残す", ctx)
            self.assertNotIn("pytest -q", ctx)

    def test_rule_conflict_goes_to_needs_no_merge(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._learn_setup(d, hits=2)
            km.promote_rules(cfg)
            # 別出典で同一 guide を昇格しようとする → needs、rules.md は増やさない
            (cfg.decisions / "OTHER.md").write_text(
                "## DR-0001  2026-07-01  actor: human\n"
                "- learn: テストの回し方 :: テストは必ず pytest -q で実行する\n",
                encoding="utf-8")
            body = ""
            for i in range(2):
                body += (f"## DR-{i+10:04d}  2026-07-0{i+2}  actor: auto\n"
                         f"- context : X\n- action  : auto-resolve\n"
                         f"- reason  : learned from OTHER: テストは必ず pytest -q で実行する\n"
                         f"- affects : X → ready\n")
            (cfg.decisions / "HX.md").write_text(body, encoding="utf-8")
            before = km.rules_path(cfg).read_text(encoding="utf-8")
            km.promote_rules(cfg)
            after = km.rules_path(cfg).read_text(encoding="utf-8")
            self.assertEqual(before.count("pytest -q"), after.count("pytest -q"))
            needs = list((d / "needs").glob("rule-*.md"))
            self.assertTrue(needs)
            self.assertIn("rule-conflict", (cfg.decisions / "OLD.md").read_text(encoding="utf-8")
                          + (cfg.decisions / "OTHER.md").read_text(encoding="utf-8"))

    def test_state_git_remote_wins_includes_rules(self):
        # rules.md は人の入力パス（同時変更はリモート＝人の編集を優先）
        self.assertIn("rules.md", km._STATE_REMOTE_WINS_FILES)

    def test_state_git_remote_wins_includes_assignments(self):
        # assignments.json（dashboard の監視担当メタ）も人が書くサイドカー。
        # 複数メンバーの同時編集はリモート＝人の割り当てを優先し取りこぼさない。
        self.assertIn("assignments.json", km._STATE_REMOTE_WINS_FILES)
        self.assertTrue(km._remote_wins("assignments.json"))
        # 同時競合ではローカルを勝たせない（リモートの人の編集を優先）
        self.assertFalse(
            km._take_local_on_conflict("assignments.json", True, True))
