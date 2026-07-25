"""agent-project の単体テスト — autonomy（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class TestAutonomyLevels(unittest.TestCase):
    """自律度レベル（report=計画のみ / assisted=実行するが done は人が承認 / unattended=現行）。"""

    def _cfg(self, d, **kw):
        return cfg_for(Path(d), dry_run=False, learn=False, auto_adjudicate=False,
                       max_cycles=10, **kw)

    def test_report_plans_without_acting(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true", title="a"); mkb(d, "T2", verify="true", title="b")
            calls = []
            res = km.run_loop(self._cfg(d, level="report"),
                              act=lambda t, c, loc: calls.append(t.id) or (True, "ok"))
            self.assertEqual(calls, [])                         # act を一切呼ばない
            self.assertEqual(res["reason"], "report")
            self.assertEqual(set(res["plan"]), {"T1", "T2"})    # 計画（順序つき）を返す
            self.assertEqual(res["counts"]["done"], 0)
            self.assertEqual(km.exit_code_for(res), 0)          # 計画報告は正常終了

    def test_assisted_acts_but_routes_done_to_review(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true"); mkb(d, "T2", verify="true")
            calls = []
            res = km.run_loop(self._cfg(d, level="assisted"),
                              act=lambda t, c, loc: calls.append(t.id) or (True, "ok"))
            self.assertEqual(sorted(calls), ["T1", "T2"])       # 実行はする
            self.assertEqual(res["counts"]["done"], 0)          # だが自動 done しない
            self.assertEqual(res["counts"].get("review", 0), 2)  # 全件 検収待ち
            self.assertTrue((d / "needs" / "T1.md").exists())
            self.assertEqual(km.exit_code_for(res), 1)          # 人の対応待ち

    def test_unattended_is_default_auto_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            res = km.run_loop(self._cfg(d), act=lambda t, c, loc: (True, "ok"))  # 既定=unattended
            self.assertEqual(res["level"], "unattended")
            self.assertEqual(res["counts"]["done"], 1)          # 従来どおり自動 done


class TestPerTaskAutonomy(unittest.TestCase):
    """タスク単位の `- level:` 上書き と 実績連動の自動昇格（--auto-level・track）。"""

    def _mk(self, d, tid, level=None, track=None, verify="true"):
        bd = d / "backlog"; bd.mkdir(parents=True, exist_ok=True)
        body = f"## {tid}: {tid}\n- status: ready\n- verify: `{verify}`\n"
        if level:
            body += f"- level: {level}\n"
        if track:
            body += f"- track: {track}\n"
        (bd / f"{tid}.md").write_text(body, encoding="utf-8")

    def _cfg(self, d, **kw):
        return cfg_for(Path(d), dry_run=False, learn=False, auto_adjudicate=False,
                       max_cycles=20, **kw)

    _act = staticmethod(lambda t, c, loc: (True, "ok"))

    def test_resolve_level_precedence(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, level="unattended")
            explicit = km.parse_task("## T: T\n- level: assisted\n", "T")
            self.assertEqual(km.resolve_level(explicit, cfg), "assisted")  # 明示が勝つ
            plain = km.parse_task("## T: T\n", "T")
            self.assertEqual(km.resolve_level(plain, cfg), "unattended")   # 無指定はグローバル

    def test_mixed_levels_in_one_backlog(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "U", level="unattended"); self._mk(d, "A", level="assisted")
            self._mk(d, "R", level="report")
            calls = []
            res = km.run_loop(self._cfg(d, level="unattended"),
                              act=lambda t, c, loc: calls.append(t.id) or (True, "ok"))
            self.assertEqual(res["counts"]["done"], 1)                 # U だけ自動 done
            self.assertEqual(res["counts"].get("review", 0), 1)        # A は検収待ち
            self.assertNotIn("R", calls)                               # report は実行しない
            self.assertIn("R", res["plan"])                            # 計画に保留として載る
            self.assertEqual(km.parse_task((d / "backlog" / "R.md").read_text(), "R")
                             .norm_status(), "ready")                  # 塩漬け（ready のまま）

    def test_global_report_honors_explicit_override(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "P1"); self._mk(d, "P2", level="unattended")
            res = km.run_loop(self._cfg(d, level="report"), act=self._act)
            self.assertEqual(res["counts"]["done"], 1)                 # 明示 unattended は実行
            self.assertEqual(res["reason"], "report")
            self.assertIn("P1", res["plan"])                           # 無指定は report 保留

    def test_auto_promote_assisted_to_unattended(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            conf = dict(level="assisted", auto_level=True, auto_level_max="unattended",
                        level_promote_after=2, level_window=10)
            for i in range(2):                                         # 2 件 clean 承認で昇格
                self._mk(d, f"X{i}", track="docs")
                km.run_loop(self._cfg(d, **conf), act=self._act)
                km.cmd_approve(self._cfg(d, **conf), f"X{i}", "ok")    # review→approve=clean
            rec = km._autonomy_get(self._cfg(d, **conf), "docs")
            self.assertEqual(rec["level"], "unattended")              # 実績で自動昇格
            self._mk(d, "X9", track="docs")
            res = km.run_loop(self._cfg(d, **conf), act=self._act)
            self.assertEqual(res["counts"]["done"], 1)               # 以後は自動 done

    def test_ceiling_default_assisted_blocks_unattended(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            conf = dict(level="assisted", auto_level=True, level_promote_after=1)  # ceiling 既定 assisted
            for i in range(3):
                self._mk(d, f"Y{i}", track="docs")
                km.run_loop(self._cfg(d, **conf), act=self._act)
                km.cmd_approve(self._cfg(d, **conf), f"Y{i}", "ok")
            rec = km._autonomy_get(self._cfg(d, **conf), "docs")
            self.assertEqual(rec["level"], "assisted")               # ceiling で unattended に上がらない

    def test_demote_then_pin_on_rework(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._git_init(d)
            conf = dict(level="unattended", auto_level=True, auto_level_max="unattended",
                        regression_cmd="false")                       # 回帰必ず失敗＝手戻り
            self._mk(d, "R1", track="risky")
            km.run_loop(self._cfg(d, **conf), act=self._act)
            rec = km._autonomy_get(self._cfg(d, **conf), "risky")
            self.assertEqual((rec["level"], rec["demotions"], rec["pinned"]),
                             ("assisted", 1, False))                  # 1 回目 → 降格
            (d / "backlog" / "R1.md").unlink()
            self._mk(d, "R2", track="risky")
            km.run_loop(self._cfg(d, **conf), act=self._act)
            rec = km._autonomy_get(self._cfg(d, **conf), "risky")
            self.assertEqual((rec["level"], rec["pinned"]), ("assisted", True))  # 2 回目 → ピン

    def test_off_by_default_no_store(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "Z", track="docs")
            res = km.run_loop(self._cfg(d, level="unattended"), act=self._act)  # auto_level 既定 off
            self.assertEqual(res["counts"]["done"], 1)
            self.assertFalse((d / "autonomy").exists())              # 既定では一切書かない＝挙動不変

    def _git_init(self, d):
        import subprocess as sp
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        sp.run(["git", "-C", str(d), "init", "-q"], env=env, capture_output=True)


class TestAudit(unittest.TestCase):
    """Loop Readiness セルフ監査（L0–L3・スコア・赤旗・--strict ゲート）。"""

    def _weak(self, d):
        # verify 無し ready・watch・予算/保護なし → 低レベル
        mkb(d, "T1", verify="")
        return cfg_for(d, watch=True)

    def _strong(self, d):
        mkb(d, "T1", verify="true")
        (d / "policy.md").write_text("protect: **/secrets/**\n", encoding="utf-8")
        (d / "needs").mkdir(exist_ok=True)
        (d / "decisions").mkdir(exist_ok=True)
        return cfg_for(d, watch=True, max_cost=5.0, rot=True)

    def test_weak_config_is_l0_with_critical_flag(self):
        with tempfile.TemporaryDirectory() as d:
            a = km.compute_audit(self._weak(Path(d)))
            self.assertEqual(a["level"], 0)
            self.assertLess(a["score"], 60)
            self.assertTrue(any(r["severity"] == "critical" for r in a["red_flags"]))
            ids = {c["id"]: c["ok"] for c in a["checks"]}
            self.assertFalse(ids["verify_coverage"])          # 鉄則違反を検出
            self.assertFalse(ids["safety_denylist"])

    def test_strong_config_is_l3_score_100(self):
        with tempfile.TemporaryDirectory() as d:
            a = km.compute_audit(self._strong(Path(d)))
            self.assertEqual(a["level"], 3)
            self.assertEqual(a["score"], 100)
            self.assertEqual(a["red_flags"], [])

    def test_cost_budget_and_protect_signals_toggle(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            ids = {c["id"]: c["ok"] for c in km.compute_audit(cfg_for(d))["checks"]}
            self.assertFalse(ids["cost_budget"])
            self.assertFalse(ids["safety_denylist"])
            (d / "policy.md").write_text("protect: auth/**\n", encoding="utf-8")
            ids2 = {c["id"]: c["ok"] for c in km.compute_audit(cfg_for(d, max_tokens=1000))["checks"]}
            self.assertTrue(ids2["cost_budget"])
            self.assertTrue(ids2["safety_denylist"])

    def test_strict_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(km.cmd_audit(self._weak(Path(d)), strict=True), 2)   # critical → 2
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(km.cmd_audit(self._strong(Path(d)), strict=True), 0)

    def test_audit_via_main_json_without_backlog(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rc = km.main(["audit", "--json", "--workdir", str(d), "--root", str(d / ".ka")])
            self.assertEqual(rc, 0)                            # backlog 無しでも落ちない


class TestAutoAdjudicate(unittest.TestCase):
    """needs に落とす前の kiro-cli 自律裁定ゲート（既定 off・有限回・人 policy 不介入）。"""

    def setUp(self):
        self._orig = km._run_agent_cli
        self.calls = []

    def tearDown(self):
        km._run_agent_cli = self._orig

    def _stub(self, payload):
        def run(prompt, model, purpose=""):
            self.calls.append(prompt)
            return payload
        km._run_agent_cli = run

    def _cfg(self, d, **kw):
        base = dict(dry_run=False, learn=False, max_retries=0, max_cycles=5)
        base.update(kw)
        return cfg_for(d, **base)

    def test_unit_requeue_and_escalate_and_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            task = km.load_tasks(d / "backlog")[0]
            cfg = cfg_for(d)
            self.assertEqual(
                km.adjudicate_escalation(cfg, task, "ng",
                                         agent_run=lambda p, m: '{"decision":"requeue","guidance":"G"}'),
                ("requeue", "G"))
            self.assertEqual(
                km.adjudicate_escalation(cfg, task, "ng",
                                         agent_run=lambda p, m: '{"decision":"escalate"}')[0],
                "escalate")
            # 不正 JSON・例外は安全側（人へ）にフォールバック
            self.assertEqual(km.adjudicate_escalation(cfg, task, "ng", agent_run=lambda p, m: "??")[0],
                             "escalate")

            def boom(p, m):
                raise RuntimeError("kiro 不在")
            self.assertEqual(km.adjudicate_escalation(cfg, task, "ng", agent_run=boom)[0], "escalate")

    def test_context_gathers_journal_decisions_feedback(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            cfg = cfg_for(d)
            km.append_journal(cfg.journal, "cycle 1: T1 verify NG exit=1")
            km.append_journal(cfg.journal, "cycle 2: T9 無関係")
            km.append_decision(cfg, "T1", "human", "ctx", "hold(deny)", "様子見", "T1")
            t = km.Task(id="T1", title="x", verify="false",
                        extra=[("feedback", "ヒントFB"), ("note", "メモN")])
            ctx = km.adjudication_context(cfg, t)
            self.assertIn("cycle 1: T1 verify NG", ctx)     # journal（当該IDのみ）
            self.assertNotIn("T9 無関係", ctx)               # 無関係行は混ぜない
            self.assertIn("hold(deny)", ctx)                 # decisions
            self.assertIn("ヒントFB", ctx)                    # feedback
            self.assertIn("メモN", ctx)                       # note
            self.assertEqual(km.adjudication_context(cfg, km.Task(id="ZZ", title="none")), "")

    def test_context_is_injected_into_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            cfg = cfg_for(d)
            km.append_journal(cfg.journal, "cycle 1: T1 過去の試行ログ")
            task = km.load_tasks(d / "backlog")[0]
            seen = {}

            def run(prompt, model):
                seen["p"] = prompt
                return '{"decision":"escalate"}'

            km.adjudicate_escalation(cfg, task, "ng", agent_run=run)
            self.assertIn("参考文脈", seen["p"])
            self.assertIn("過去の試行ログ", seen["p"])

    def test_on_requeues_then_blocks_within_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            self._stub('{"decision":"requeue","guidance":"X を追加"}')
            cfg = self._cfg(d, auto_adjudicate=True, adjudicate_max=1)
            res = km.run_loop(cfg, act=lambda t, c, loc: (True, "acted"))
            self.assertEqual(len(self.calls), 1)                 # 裁定は cap=1 回だけ
            self.assertEqual(res["counts"]["blocked"], 1)        # 最終的には人へ
            self.assertTrue((cfg.needs / "T1.md").exists())
            txt = "".join(p.read_text(encoding="utf-8") for p in (d / "decisions").glob("*.md"))
            self.assertIn("auto-adjudicate", txt)                # 決定記録に残る

    def test_escalate_decision_blocks_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            self._stub('{"decision":"escalate"}')
            cfg = self._cfg(d, auto_adjudicate=True, adjudicate_max=2)
            res = km.run_loop(cfg, act=lambda t, c, loc: (True, "acted"))
            self.assertEqual(len(self.calls), 1)                 # 1度諮って escalate
            self.assertEqual(res["counts"]["blocked"], 1)

    def test_off_never_calls_kiro(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            self._stub('{"decision":"requeue"}')
            cfg = self._cfg(d, auto_adjudicate=False)
            res = km.run_loop(cfg, act=lambda t, c, loc: (True, "acted"))
            self.assertEqual(self.calls, [])                     # off は呼ばない
            self.assertEqual(res["counts"]["blocked"], 1)

    def test_verifyless_task_is_not_adjudicated(self):
        # verify を持たない（acceptance 未定義）タスクは裁定対象外＝必ず人へ
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="")
            self._stub('{"decision":"requeue"}')
            cfg = self._cfg(d, auto_adjudicate=True, adjudicate_max=3)
            res = km.run_loop(cfg, act=lambda t, c, loc: (True, "acted"))
            self.assertEqual(self.calls, [])                     # kiro を呼ばずに人へ
            self.assertEqual(res["counts"]["blocked"], 1)


class TestApprovalGate(unittest.TestCase):
    """verify=PASS でも人の承認を要する検収ゲート（- review: human / policy.gate）。"""

    @staticmethod
    def _mk(d, body, policy=None):
        bd = d / "backlog"; bd.mkdir(parents=True, exist_ok=True)
        (bd / "T1.md").write_text(body, encoding="utf-8")
        if policy is not None:
            (d / "policy.md").write_text(policy, encoding="utf-8")

    def test_unit_needs_human_review(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "## T1: x\n- status: ready\n- verify: `true`\n- review: human\n")
            t = km.load_tasks(d / "backlog")[0]
            self.assertTrue(km.needs_human_review(t, km.Policy()))           # タスク単位
            self._mk(d, "## T1: x\n- status: ready\n- verify: `true`\n")
            t = km.load_tasks(d / "backlog")[0]
            self.assertFalse(km.needs_human_review(t, km.Policy()))          # ゲート無し
            self.assertTrue(km.needs_human_review(t, km.Policy(gate=["T1"])))  # policy.gate

    def test_review_gate_holds_then_approve_finalizes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "## T1: deploy\n- status: ready\n- verify: `true`\n- review: human\n- retries: 0\n")
            cfg = cfg_for(d)
            res = km.run_loop(cfg)
            self.assertEqual(res["counts"]["review"], 1)
            self.assertEqual(res["counts"]["done"], 0)
            self.assertTrue((cfg.backlog / "T1.md").exists())            # archive されず残る
            self.assertFalse((cfg.archive_dir() / "T1.md").exists())
            self.assertTrue((cfg.needs / "T1.md").exists())
            self.assertEqual(km.exit_code_for(res), 1)                   # 人の対応待ち
            # 承認 → done 確定（archive・納品書・needs クリア）
            self.assertEqual(km.cmd_approve(cfg, "T1", "本番OK"), 0)
            self.assertTrue((cfg.archive_dir() / "T1.md").exists())
            self.assertFalse((cfg.backlog / "T1.md").exists())
            self.assertFalse((cfg.needs / "T1.md").exists())
            self.assertIn("T1", (d / "DELIVERY.md").read_text(encoding="utf-8"))

    def test_policy_gate_holds(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "## T1: prod-release\n- status: ready\n- verify: `true`\n- retries: 0\n",
                     policy="gate: prod\n")
            res = km.run_loop(cfg_for(d))
            self.assertEqual(res["counts"]["review"], 1)

    def test_no_gate_finalizes_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "## T1: x\n- status: ready\n- verify: `true`\n- retries: 0\n")
            res = km.run_loop(cfg_for(d))
            self.assertEqual(res["counts"]["done"], 1)
            self.assertEqual(res["counts"].get("review", 0), 0)

    def test_reject_via_feedback_reopens_to_ready(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "## T1: y\n- status: ready\n- verify: `true`\n- review: human\n- retries: 0\n")
            cfg = cfg_for(d)
            km.run_loop(cfg)
            nf = cfg.needs / "T1.md"
            nf.write_text(nf.read_text(encoding="utf-8").replace("- [ ] 確定", "- [x] 確定")
                          + "\n## フィードバック\nやり直して\n", encoding="utf-8")
            km.ingest_feedback(cfg, km.load_tasks(cfg.backlog))
            self.assertEqual(km.load_tasks(cfg.backlog)[0].status, "ready")

    def test_review_empty_checkbox_approves_as_done(self):
        # review 票の空 [x]（記入なしのチェックのみ）は承認＝ approve と同じ done 確定経路。
        # 従来は ready に戻って検証済みの成果が最初から再実行されていた（手戻り）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "## T1: deploy\n- status: ready\n- verify: `true`\n- review: human\n- retries: 0\n")
            cfg = cfg_for(d)
            km.run_loop(cfg)                                       # verify PASS → review 待ち
            nf = cfg.needs / "T1.md"
            self.assertTrue(nf.exists())
            nf.write_text(nf.read_text(encoding="utf-8").replace("- [ ] 確定", "- [x] 確定"),
                          encoding="utf-8")
            ingested = km.ingest_feedback(cfg, km.load_tasks(cfg.backlog))
            self.assertEqual(ingested, ["T1"])
            self.assertFalse((cfg.backlog / "T1.md").exists())     # ready に戻らない
            self.assertTrue((cfg.archive_dir() / "T1.md").exists())  # done 確定（archive 済み）
            self.assertFalse(nf.exists())                          # 票はクリアされる
            self.assertIn("T1", (d / "DELIVERY.md").read_text(encoding="utf-8"))


class TestLoopEngineering(unittest.TestCase):
    """Loop Engineering 拡張: 計測・タスク自己生成・依存(DAG)・回帰ゲート。"""

    @staticmethod
    def _mk(d, name, body):
        bd = d / "backlog"; bd.mkdir(parents=True, exist_ok=True)
        (bd / f"{name}.md").write_text(body, encoding="utf-8")

    # --- 計測 ---
    def test_stats_counts(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "T1", "## T1: ok\n- status: ready\n- verify: `true`\n")
            self._mk(d, "T2", "## T2: ng\n- status: ready\n- verify: `false`\n")
            cfg = cfg_for(d, learn=False, max_retries=0, auto_adjudicate=False)
            km.run_loop(cfg)
            s = km.compute_stats(cfg)
            self.assertEqual(s["done_archived"], 1)
            self.assertEqual(s["pending_human"], 1)        # T2 blocked
            self.assertEqual(s["delivery_rows"], 1)
            self.assertEqual(s["first_pass_done"], 1)
            self.assertEqual(km.cmd_stats(cfg, as_json=True), 0)

    # --- タスク自己生成 ---
    def test_followup_spawn_static(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "T1", "## T1: parent\n- status: ready\n- verify: `true`\n"
                              "- followup: 子A :: true\n- followup: 子B\n")
            cfg = cfg_for(d, learn=False, auto_adjudicate=False, max_cycles=10)
            res = km.run_loop(cfg)
            self.assertEqual(res["spawned"], 2)
            self.assertTrue((cfg.archive_dir() / "T1-f1.md").exists())   # 子A: verify有→ready→done
            t = km.load_tasks(cfg.backlog)
            self.assertEqual([x.id for x in t], ["T1-f2"])              # 子B: verify無→inbox 残置
            self.assertEqual(t[0].norm_status(), "inbox")
            self.assertEqual(t[0].source, "followup")

    def test_followup_disabled_by_zero_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "T1", "## T1: p\n- status: ready\n- verify: `true`\n- followup: 子 :: true\n")
            res = km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False, max_spawn=0))
            self.assertEqual(res["spawned"], 0)

    # --- 依存(DAG) ---
    def test_deps_gate_ordering(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "T1", "## T1: first\n- status: ready\n- verify: `true`\n")
            self._mk(d, "T2", "## T2: second\n- status: ready\n- verify: `true`\n- after: T1\n")
            tasks = km.load_tasks(d / "backlog")
            order = km.prioritize(tasks, km.Policy(), "none")
            self.assertEqual([t.id for t in order], ["T1"])            # T2 は依存未達で除外
            res = km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False, max_cycles=10))
            self.assertEqual(res["counts"]["done"], 2)                 # 解けると両方 done

    def test_deps_block_when_dep_unfinished(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "T1", "## T1: dep\n- status: blocked\n- verify: `true`\n")
            self._mk(d, "T2", "## T2: x\n- status: ready\n- verify: `true`\n- after: T1\n")
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(km.unmet_deps(tasks[1] if tasks[1].id == "T2" else tasks[0],
                                           tasks), ["T1"])
            res = km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False))
            self.assertEqual(res["counts"]["done"], 0)                 # T1 未完なので T2 も進まない

    # --- 回帰ゲート ---
    def test_regression_gate_blocks_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "T1", "## T1: x\n- status: ready\n- verify: `true`\n")
            cfg = cfg_for(d, learn=False, auto_adjudicate=False,
                          regression_cmd="external-regression-hook", max_cycles=3)
            with mock.patch.object(km, "run_verify_stable", return_value=(True, False, "ok")), \
                    mock.patch.object(km, "run_verify", return_value=(False, "hook failed")) as hook:
                res = km.run_loop(cfg)
            hook.assert_called_once_with("external-regression-hook", d, cfg.verify_timeout, mock.ANY)
            self.assertEqual(res["counts"]["done"], 0)
            self.assertEqual(res["counts"]["blocked"], 1)

    def test_regression_gate_passes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "T1", "## T1: x\n- status: ready\n- verify: `true`\n")
            cfg = cfg_for(d, learn=False, auto_adjudicate=False,
                          regression_cmd="external-regression-hook")
            with mock.patch.object(km, "run_verify_stable", return_value=(True, False, "ok")), \
                    mock.patch.object(km, "run_verify", return_value=(True, "ok")) as hook:
                res = km.run_loop(cfg)
            hook.assert_called_once_with("external-regression-hook", d, cfg.verify_timeout, mock.ANY)
            self.assertEqual(res["counts"]["done"], 1)

    def test_regression_gate_runs_in_workdir_not_workspace_clone(self):
        # workspace 指定タスクは verify を該当 repo の一時 clone（vcwd）で走らせるが、
        # 外部のグローバル回帰フックはパスも差分基準も git-bus ルート（workdir）前提。
        # clone 内で走らせると `--repos <root>/repos.json` を解決できず壊れる。回帰 cmd の cwd が
        # 常に workdir であることを固定する（vcwd=clone を返しても workdir で走る）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "T1", "## T1: x\n- status: ready\n- verify: `true`\n")
            rec = d / "reg_cwd.txt"

            def fake_verify_cwd(cfg, task):                 # workspace タスクの一時 clone を模す
                parent = Path(tempfile.mkdtemp(prefix="fake-clone-"))
                clone = parent / "repo"; clone.mkdir()
                return clone, str(parent)

            reg = f"python3 -c \"import os; open(r'{rec}','w').write(os.getcwd())\""
            with mock.patch.object(km, "_task_verify_cwd", fake_verify_cwd):
                km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False, regression_cmd=reg))
            self.assertTrue(rec.exists(), "回帰ゲートが走っていない")
            self.assertEqual(Path(rec.read_text()).resolve(), d.resolve())  # clone でなく workdir

    # --- コスト予算 ---
    def test_parse_cost_sums_markers(self):
        self.assertEqual(km.parse_cost("ok\n@cost tokens=1_200 usd=0.03\n@cost tokens=300 cost=0.01"),
                         (1500, 0.04))
        self.assertEqual(km.parse_cost("no markers here"), (0, 0.0))

    @staticmethod
    def _seed_ready(d, n):
        for i in range(n):
            TestLoopEngineering._mk(d, f"T{i}", f"## T{i}: x\n- status: ready\n- verify: `true`\n")

    def test_max_tokens_stops_loop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._seed_ready(d, 5)
            res = km.run_loop(cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False,
                                      max_cycles=99, max_tokens=2500),
                              act=lambda t, c, loc: (True, "done\n@cost tokens=1000 usd=0.02"))
            self.assertEqual(res["reason"], km.REASON_COST)
            self.assertEqual(res["counts"]["done"], 3)        # 3 サイクルで 3000≥2500
            self.assertEqual(res["tokens"], 3000)
            self.assertEqual(km.exit_code_for(res), 2)        # 予算停止は 2

    def test_max_cost_stops_loop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._seed_ready(d, 5)
            res = km.run_loop(cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False,
                                      max_cycles=99, max_cost=0.05),
                              act=lambda t, c, loc: (True, "done\n@cost usd=0.02"))
            self.assertEqual(res["reason"], km.REASON_COST)
            self.assertEqual(res["counts"]["done"], 3)        # 0.06≥0.05 で停止

    def test_stats_aggregates_archived_cost(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._seed_ready(d, 2)
            cfg = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False, max_cycles=99)
            km.run_loop(cfg, act=lambda t, c, loc: (True, "ok\n@cost tokens=500 usd=0.01"))
            s = km.compute_stats(cfg)
            self.assertEqual((s["tokens_archived"], s["cost_archived"], s["done_archived"]),
                             (1000, 0.02, 2))


class TestPlanReview(unittest.TestCase):
    """実行前レビュー（plan_review・本番既定 on）: 新規タスクは proposed で入り、
    人の承認（approve）・差し戻し（feedback→agent-project が修正）・却下（reject）を通る。"""

    def _cfg(self, d, **kw):
        return cfg_for(d, plan_review=True, **kw)

    def test_enqueue_becomes_proposed_and_gets_needs(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            t = km.enqueue_task(cfg, {"title": "x", "verify": "true"})
            self.assertEqual(t.norm_status(), "proposed")     # verify があっても即 ready にしない
            km.ensure_plan_review_needs(cfg, [t])
            nf = cfg.needs / f"{t.id}.md"
            self.assertTrue(nf.exists())
            body = nf.read_text(encoding="utf-8")
            self.assertIn("kind: plan-review", body)
            self.assertIn("実行前レビュー", body)
            self.assertIn("reject", body)                      # 却下の案内が載る

    def test_explicit_status_bypasses_gate(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            t = km.enqueue_task(cfg, {"title": "x", "verify": "true", "status": "ready"})
            self.assertEqual(t.norm_status(), "ready")         # 明示 status は尊重（後方互換の口）

    def test_run_loop_does_not_execute_proposed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, learn=False, auto_adjudicate=False)
            km.enqueue_task(cfg, {"title": "x", "verify": "true"})
            result = km.run_loop(cfg)
            self.assertEqual(result["counts"]["proposed"], 1)  # 実行されず proposed のまま
            self.assertEqual(result["counts"]["done"], 0)
            self.assertEqual(km.exit_code_for(result), 1)      # 人の対応待ち
            # needs（実行前レビュー票）が run_loop 内で用意される
            tasks = km.load_tasks(cfg.backlog)
            self.assertTrue((cfg.needs / f"{tasks[0].id}.md").exists())

    def test_inbox_md_drop_becomes_proposed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, inbox=d / "inbox")
            cfg.inbox.mkdir(parents=True)
            (cfg.inbox / "t.md").write_text(
                "## T9: ドロップ\n- status: ready\n- verify: `true`\n", encoding="utf-8")
            created = km.ingest_inbox(cfg)
            self.assertEqual(created[0].norm_status(), "proposed")

    def test_triage_promotes_inbox_to_proposed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            mkb(d, "T1", status="inbox", verify="true")
            tasks = km.load_tasks(cfg.backlog)
            km.triage(tasks, km.load_policy(cfg.policy), plan_review=True)
            self.assertEqual(tasks[0].norm_status(), "proposed")

    def test_approve_moves_proposed_to_ready(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            t = km.enqueue_task(cfg, {"title": "x", "verify": "true"})
            km.ensure_plan_review_needs(cfg, [t])
            rc = km.cmd_approve(cfg, t.id, "内容OK")
            self.assertEqual(rc, 0)
            got = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(got.norm_status(), "ready")
            self.assertFalse((cfg.needs / f"{t.id}.md").exists())
            dec = (cfg.decisions / f"{t.id}.md").read_text(encoding="utf-8")
            self.assertIn("plan-approve", dec)

    def test_approve_without_verify_goes_inbox(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            t = km.enqueue_task(cfg, {"title": "x"})           # verify 無し
            km.cmd_approve(cfg, t.id, "進めてよいが verify は要定義")
            self.assertEqual(km.load_tasks(cfg.backlog)[0].norm_status(), "inbox")

    def test_feedback_checkbox_only_approves(self):
        # 空のまま [x] = 承認（実行を許可）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            t = km.enqueue_task(cfg, {"title": "x", "verify": "true"})
            km.ensure_plan_review_needs(cfg, [t])
            nf = cfg.needs / f"{t.id}.md"
            nf.write_text(nf.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
                          encoding="utf-8")
            tasks = km.load_tasks(cfg.backlog)
            km.ingest_feedback(cfg, tasks)
            self.assertEqual(tasks[0].norm_status(), "ready")

    def test_feedback_with_text_reworks_via_agent(self):
        # 差し戻し: kiro-cli がタスク定義を修正して再提案（proposed のまま・needs 再生成）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            t = km.enqueue_task(cfg, {"title": "旧タイトル", "verify": "true"})
            km.ensure_plan_review_needs(cfg, [t])
            nf = cfg.needs / f"{t.id}.md"
            body = nf.read_text(encoding="utf-8").replace("- [ ]", "- [x]")
            body = body.replace(km.DECISION_MARKER, km.DECISION_MARKER + "\n\n実サーバ基準の verify にして\n")
            nf.write_text(body, encoding="utf-8")
            fake = '{"title": "新タイトル", "verify": "curl -fsS https://x/health", "after": "", "note": ""}'
            with mock.patch.object(km, "_run_agent_cli", return_value=fake):
                tasks = km.load_tasks(cfg.backlog)
                km.ingest_feedback(cfg, tasks)
            got = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(got.norm_status(), "proposed")    # 再提案（承認まで実行しない）
            self.assertEqual(got.title, "新タイトル")
            self.assertIn("curl", got.verify)
            self.assertTrue(nf.exists())                        # needs 再生成
            dec = (cfg.decisions / f"{t.id}.md").read_text(encoding="utf-8")
            self.assertIn("plan-rework", dec)

    def test_feedback_rework_agent_failure_keeps_note(self):
        # kiro-cli 失敗時は指摘を note に残してそのまま再提案（指摘を失わない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            t = km.enqueue_task(cfg, {"title": "x", "verify": "true"})
            km.ensure_plan_review_needs(cfg, [t])
            nf = cfg.needs / f"{t.id}.md"
            body = nf.read_text(encoding="utf-8").replace("- [ ]", "- [x]")
            body = body.replace(km.DECISION_MARKER, km.DECISION_MARKER + "\n\nもっと細かく分けて\n")
            nf.write_text(body, encoding="utf-8")
            with mock.patch.object(km, "_run_agent_cli", side_effect=RuntimeError("kiro-cli 不在")):
                tasks = km.load_tasks(cfg.backlog)
                km.ingest_feedback(cfg, tasks)
            got = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(got.norm_status(), "proposed")
            self.assertIn("もっと細かく分けて", got.get("note", ""))

    def test_plan_review_off_keeps_legacy_behavior(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))                             # plan_review=False（従来）
            t = km.enqueue_task(cfg, {"title": "x", "verify": "true"})
            self.assertEqual(t.norm_status(), "ready")
