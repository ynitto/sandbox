"""kiro-autonomous の単体テスト（標準ライブラリ unittest）。

案件毎ファイル（backlog/<id>.md）・done でファイル削除・watch 常駐・フィードバック往復・
案件毎の needs/decisions を、kiro-flow を呼ばずに検証する。kiro-flow stub 統合も含む。

    python -m unittest discover -s tools/kiro-autonomous/tests
"""
import importlib.util
import os
import signal
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

_MOD = Path(__file__).resolve().parent.parent / "kiro-autonomous.py"
_spec = importlib.util.spec_from_file_location("kiro_autonomous", _MOD)
km = importlib.util.module_from_spec(_spec)
sys.modules["kiro_autonomous"] = km
_spec.loader.exec_module(km)


def mkb(d: Path, tid: str, status="ready", verify="true", source="human", title=None, retries=0):
    bd = d / "backlog"
    bd.mkdir(parents=True, exist_ok=True)
    v = f"`{verify}`" if verify else ""
    (bd / f"{tid}.md").write_text(
        f"## {tid}: {title or tid}\n- status: {status}\n- source: {source}\n"
        f"- verify: {v}\n- retries: {retries}\n", encoding="utf-8")


def cfg_for(d: Path, **kw):
    base = dict(backlog=d / "backlog", policy=d / "policy.md", decisions=d / "decisions",
                journal=d / "journal.md", needs=d / "needs", workdir=d, bus=d / "bus",
                planner="none", flow_planner="stub", executor="stub", dry_run=True)
    base.update(kw)
    return km.Config(**base)


class TestTaskFile(unittest.TestCase):
    def test_parse_serialize_roundtrip(self):
        t = km.parse_task("## T1: 見出し\n- status: ready\n- source: triage\n"
                          "- verify: `grep x f`\n- retries: 2\n- note: メモ\n", "T1")
        self.assertEqual((t.id, t.title, t.source, t.verify, t.retries),
                         ("T1", "見出し", "triage", "grep x f", 2))
        self.assertEqual(t.extra, [("note", "メモ")])
        t2 = km.parse_task(km.serialize_task(t), "T1")
        self.assertEqual(t2.verify, "grep x f")
        self.assertEqual(t2.extra, [("note", "メモ")])

    def test_load_tasks_oldest_first(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1"); mkb(d, "T2")
            ids = [t.id for t in km.load_tasks(d / "backlog")]
            self.assertEqual(set(ids), {"T1", "T2"})


class TestPolicy(unittest.TestCase):
    def test_parse_and_match(self):
        pol = km.parse_policy("deny: prod\npin: T3\noffload: heavy\n")
        self.assertEqual(pol.deny, ["prod"])
        self.assertEqual(pol.offload, ["heavy"])
        self.assertTrue(km.Task(id="T9", title="deploy prod").matches("prod"))


class TestPrioritize(unittest.TestCase):
    def test_none_age_and_policy(self):
        tasks = [km.Task(id="T0", title="a"), km.Task(id="T1", title="cleanup logs"),
                 km.Task(id="T2", title="urgent")]
        order = km.prioritize(tasks, km.Policy(pin=["T2"], defer=["cleanup"]), planner="none")
        self.assertEqual([t.id for t in order], ["T2", "T0", "T1"])

    def test_none_priority_then_age(self):
        # mtime 順 A,B,C で渡るが priority 降順が勝ち、同値は古さ
        tasks = [km.Task(id="A", title="a", priority=1),
                 km.Task(id="B", title="b", priority=5),
                 km.Task(id="C", title="c", priority=5)]
        order = km.prioritize(tasks, km.Policy(), planner="none")
        self.assertEqual([t.id for t in order], ["B", "C", "A"])

    def test_agent_fallback(self):
        ready = [km.Task(id="T0", title="a"), km.Task(id="T1", title="b")]
        r = km.rank_agent(ready, None, kiro_run=lambda p, m: '["T1","T0"]')
        self.assertEqual([t.id for t in r], ["T1", "T0"])
        self.assertIsNone(km.rank_agent(
            ready, None, kiro_run=lambda p, m: (_ for _ in ()).throw(RuntimeError())))


class TestTriage(unittest.TestCase):
    def test_promote_and_deny(self):
        tasks = [km.Task(id="T1", title="a", status="inbox", verify="true"),
                 km.Task(id="T2", title="b", status="inbox", verify=""),
                 km.Task(id="T3", title="deploy prod", status="ready", verify="true")]
        km.triage(tasks, km.Policy(deny=["prod"]))
        self.assertEqual(tasks[0].status, "ready")
        self.assertEqual(tasks[1].status, "inbox")
        self.assertEqual(tasks[2].status, "blocked")


class TestEnqueue(unittest.TestCase):
    """汎用の取り込み口（enqueue コマンド・inbox/ ドロップ）。外部ソースの共通入口。"""

    def _cfg(self, d):
        return cfg_for(d, inbox=d / "inbox", learn=False, auto_adjudicate=False, max_cycles=10)

    def test_spec_required_title_and_status_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            with self.assertRaises(ValueError):
                km.task_from_spec(cfg, {"verify": "true"})           # title 必須
            t = km.task_from_spec(cfg, {"title": "A", "verify": "`pytest -q`"})
            self.assertEqual((t.norm_status(), t.verify, t.source), ("ready", "pytest -q", "enqueue"))
            t2 = km.task_from_spec(cfg, {"title": "B"})
            self.assertEqual(t2.norm_status(), "inbox")              # verify 無し→人の triage へ

    def test_spec_fields_and_unknown_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            t = km.task_from_spec(cfg, {"title": "C", "verify": "true", "priority": "7",
                                        "after": ["T1", "T2"], "review": "human",
                                        "note": "メモ", "custom": "保持"})
            ex = dict(t.extra)
            self.assertEqual(t.priority, 7)
            self.assertEqual(ex["after"], "T1,T2")
            self.assertEqual((ex["review"], ex["note"], ex["custom"]), ("human", "メモ", "保持"))

    def test_enqueue_task_persists_unique_ids(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            a = km.enqueue_task(cfg, {"id": "dup", "title": "x", "verify": "true"})
            b = km.enqueue_task(cfg, {"id": "dup", "title": "y", "verify": "true"})
            self.assertEqual(a.id, "dup")
            self.assertEqual(b.id, "dup-2")                          # 衝突回避
            self.assertTrue((cfg.backlog / "dup.md").exists())
            self.assertTrue((cfg.backlog / "dup-2.md").exists())

    def test_ingest_inbox_json_and_md(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            km.ensure_dirs(cfg)
            (cfg.inbox / "a.json").write_text(
                __import__("json").dumps([{"id": "J1", "title": "j1", "verify": "true"},
                                          {"id": "J2", "title": "j2"}]), encoding="utf-8")
            (cfg.inbox / "b.md").write_text(
                "## ignore: mdタスク\n- status: ready\n- verify: ``\n", encoding="utf-8")
            got = km.ingest_inbox(cfg)
            ids = sorted(t.id for t in got)
            self.assertEqual(ids, ["J1", "J2", "b"])
            self.assertEqual(list(cfg.inbox.glob("*")), [])          # 取り込んだら消す
            self.assertEqual(km.parse_task((cfg.backlog / "J2.md").read_text(), "J2").norm_status(),
                             "inbox")                                # verify 無し→inbox
            self.assertEqual(km.parse_task((cfg.backlog / "b.md").read_text(), "b").norm_status(),
                             "inbox")                                # md も verify 無し→inbox

    def test_run_loop_ingests_inbox_and_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            km.ensure_dirs(cfg)
            (cfg.inbox / "t.json").write_text(
                __import__("json").dumps({"title": "外部から", "verify": "true"}), encoding="utf-8")
            self.assertTrue(km.has_work(cfg))                        # watch が起きる
            res = km.run_loop(cfg)
            self.assertEqual(len(res["inboxed"]), 1)
            self.assertEqual(res["counts"]["done"], 1)              # 同じ run で消化

    def test_cmd_enqueue_via_main(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rc = km.main(["enqueue", "--title", "X", "--verify", "true",
                          "--workdir", str(d), "--root", str(d / ".ka")])
            self.assertEqual(rc, 0)
            files = list((d / ".ka" / "backlog").glob("*.md"))
            self.assertEqual(len(files), 1)
            self.assertEqual(km.parse_task(files[0].read_text(), files[0].stem).norm_status(), "ready")


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


class TestRunlogAndThrottle(unittest.TestCase):
    """構造化 run-log（JSONL）と自動スロットル（ソフト予算→打ち切り・watch は report 降格）。"""

    def _cfg(self, d, **kw):
        return cfg_for(Path(d), dry_run=False, learn=False, auto_adjudicate=False,
                       max_cycles=50, do_archive=True, **kw)

    def _cost_act(self, usd=0.03):
        return lambda t, c, loc: (True, f"done\n@cost tokens=100 usd={usd}")

    def test_runlog_written_per_pass(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true"); mkb(d, "T2", verify="true")
            res = km.run_loop(self._cfg(d), act=lambda t, c, loc: (True, "ok"))
            lines = (d / "run-log.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            rec = __import__("json").loads(lines[0])
            self.assertEqual(rec["done"], 2)
            self.assertEqual(rec["reason"], res["reason"])
            for k in ("ts", "reason", "cycles", "escalations", "tokens", "cost", "duration_s"):
                self.assertIn(k, rec)

    def test_throttle_stops_before_hard_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(6):
                mkb(d, f"T{i}", verify="true")
            res = km.run_loop(self._cfg(d, max_cost=0.10, throttle=0.8),
                              act=self._cost_act(0.03))
            self.assertEqual(res["reason"], "throttle")        # 0.8*0.10=0.08 で打ち切り
            self.assertLess(res["cost"], 0.10)                 # ハード上限の手前
            self.assertEqual(km.exit_code_for(res), 2)

    def test_throttle_off_uses_hard_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(6):
                mkb(d, f"T{i}", verify="true")
            res = km.run_loop(self._cfg(d, max_cost=0.10, throttle=0.0),
                              act=self._cost_act(0.03))
            self.assertEqual(res["reason"], "cost")            # throttle off → ハード上限で停止

    def test_watch_degrades_to_report_on_throttle(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(6):
                mkb(d, f"T{i}", verify="true")
            cfg = self._cfg(d, max_cost=0.10, throttle=0.8)
            km.run_watch(cfg, act=self._cost_act(0.03), sleeper=lambda s: None, max_passes=2)
            self.assertEqual(cfg.level, "report")              # throttle 後は report 降格

    def test_cmd_runlog(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(km.cmd_runlog(self._cfg(d)), 0)   # 空でも落ちない
            mkb(d, "T1", verify="true")
            km.run_loop(self._cfg(d), act=lambda t, c, loc: (True, "ok"))
            self.assertEqual(km.cmd_runlog(self._cfg(d), as_json=True, tail=5), 0)


class TestAtomicClaim(unittest.TestCase):
    """原子的クレーム（共有 backlog／並列での二重実行防止）。"""

    def _task(self, d, tid="T1"):
        mkb(d, tid, verify="true")
        return km.Task(id=tid, title="x", status="ready", verify="true")

    def test_claim_excludes_second_then_release_reopens(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            self.assertTrue(km.claim_task(cfg, t))        # 1人目は取得
            self.assertFalse(km.claim_task(cfg, t))       # 2人目は弾かれる（新鮮なクレーム）
            km.release_claim(cfg, t)
            self.assertTrue(km.claim_task(cfg, t))         # 解放後は再取得できる

    def test_stale_claim_is_stolen(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            lock = d / "claims" / "T1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text('{"host":"old","pid":1,"ts":0,"id":"T1"}', encoding="utf-8")  # 大昔
            self.assertTrue(km.claim_task(cfg, t))         # owner 失踪とみなし奪取

    def test_claim_revalidates_against_disk(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            (d / "backlog" / "T1.md").unlink()             # 別インスタンスが消化(archive)した想定
            self.assertFalse(km.claim_task(cfg, t))        # 取得後の再検証で弾く（二重実行防止）
            self.assertFalse((d / "claims" / "T1.lock").exists())  # ロックも残さない
            # 状態が consumable でない（review）なら同様に弾く
            t2 = self._task(d, "T2")
            (d / "backlog" / "T2.md").write_text(
                "## T2: x\n- status: review\n- verify: `true`\n", encoding="utf-8")
            self.assertFalse(km.claim_task(cfg, t2))

    def test_run_loop_releases_all_claims(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true"); mkb(d, "T2", verify="true")
            res = km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False, max_cycles=10))
            self.assertEqual(res["counts"]["done"], 2)
            claims = d / "claims"
            self.assertEqual(list(claims.glob("*.lock")) if claims.exists() else [], [])

    def test_held_claim_makes_task_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true"); mkb(d, "T2", verify="true")
            (d / "claims").mkdir(parents=True, exist_ok=True)
            (d / "claims" / "T1.lock").write_text(           # 他インスタンスが保持中（新鮮）
                f'{{"host":"other","pid":99999,"ts":{time.time()},"id":"T1"}}', encoding="utf-8")
            calls = []
            res = km.run_loop(cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False,
                                      max_cycles=10),
                              act=lambda t, c, loc: calls.append(t.id) or (True, "ok"))
            self.assertEqual(calls, ["T2"])                  # T1 は他者保持で飛ばす
            self.assertEqual(res["counts"]["done"], 1)
            t1 = km.parse_task((d / "backlog" / "T1.md").read_text(), "T1")
            self.assertEqual(t1.norm_status(), "ready")      # T1 は手つかずのまま


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


class TestParallelConsumption(unittest.TestCase):
    """並列消費（§11）— daemon/remote へ独立タスクを並行 submit。worker 並列へ寄せる。"""

    def _tasks(self, n):
        return [km.Task(id=f"T{i}", title=f"t{i}", status="ready", verify="true")
                for i in range(n)]

    def _cfg(self, d, **kw):
        base = dict(location="remote", git_bus="bus", concurrency=3, dry_run=False,
                    learn=False, auto_adjudicate=False, max_cycles=50)
        base.update(kw)
        return cfg_for(Path(d), **base)

    def test_submit_bound(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            self.assertTrue(km._submit_bound("remote", cfg))
            self.assertFalse(km._submit_bound("local", cfg))
            # daemon は実際に稼働中のときだけ並行対象（テスト環境では未稼働）
            self.assertEqual(km._submit_bound("daemon", cfg), km.daemon_running(cfg, use_git=False))

    def test_select_batch_width_and_caps(self):
        with tempfile.TemporaryDirectory() as d:
            pol = km.parse_policy("")
            order = self._tasks(4)
            self.assertEqual(len(km._select_batch(order, self._cfg(d), pol, 10)), 3)  # concurrency=3
            self.assertEqual(len(km._select_batch(order, self._cfg(d), pol, 2)), 2)   # 残予算で制限
            self.assertEqual(len(km._select_batch(order, self._cfg(d, concurrency=1), pol, 10)), 1)
            self.assertEqual(len(km._select_batch(order, self._cfg(d, once=True), pol, 10)), 1)
            # 先頭が local 実行なら逐次（1件）に落とす
            local = self._cfg(d, location="local", git_bus=None)
            self.assertEqual(len(km._select_batch(order, local, pol, 10)), 1)

    def test_acts_run_concurrently(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(3):
                mkb(d, f"T{i}", verify="true")
            active = {"n": 0, "max": 0}
            lock = threading.Lock()

            def act(t, c, loc):
                with lock:
                    active["n"] += 1
                    active["max"] = max(active["max"], active["n"])
                time.sleep(0.05)
                with lock:
                    active["n"] -= 1
                return (True, "ok")

            res = km.run_loop(self._cfg(d), act=act)
            self.assertEqual(active["max"], 3)               # 3件が同時に走った
            self.assertEqual(res["counts"]["done"], 3)
            self.assertEqual(res["cycles"], 3)               # 1タスク=1サイクルを維持

    def test_location_passed_to_act(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(3):
                mkb(d, f"T{i}", verify="true")
            seen = []
            lock = threading.Lock()

            def act(t, c, loc):
                with lock:
                    seen.append(loc)
                return (True, "ok")

            km.run_loop(self._cfg(d), act=act)
            self.assertEqual(set(seen), {"remote"})          # remote へ submit された

    def test_dry_run_parallel_skips_act(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(3):
                mkb(d, f"T{i}", verify="true")
            calls = []
            res = km.run_loop(self._cfg(d, dry_run=True),
                              act=lambda t, c, loc: calls.append(t.id) or (True, "x"))
            self.assertEqual(calls, [])                       # dry-run は act を呼ばない
            self.assertEqual(res["counts"]["done"], 3)        # verify=true で done

    def test_once_processes_single_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(3):
                mkb(d, f"T{i}", verify="true")
            res = km.run_loop(self._cfg(d, once=True), act=lambda t, c, loc: (True, "ok"))
            self.assertEqual(res["cycles"], 1)                # once は 1 件だけ
            self.assertEqual(res["reason"], "once")


class TestRunLoop(unittest.TestCase):
    def test_drains_and_archives_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true"); mkb(d, "T2", verify="true")
            res = km.run_loop(cfg_for(d))
            self.assertEqual(res["reason"], km.REASON_DRAINED)
            self.assertEqual(res["counts"]["done"], 2)
            self.assertEqual(res["archived"], 2)
            self.assertEqual(km.exit_code_for(res), 0)
            # backlog からは消え、archive/ へ移動（退避ファイルに archived 行）
            self.assertEqual(list((d / "backlog").glob("*.md")), [])
            self.assertTrue((d / "archive" / "T1.md").exists())
            self.assertIn("archived:", (d / "archive" / "T1.md").read_text())

    def test_no_archive_deletes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            res = km.run_loop(cfg_for(d, do_archive=False))
            self.assertEqual(res["archived"], 0)
            self.assertEqual(list((d / "backlog").glob("*.md")), [])
            self.assertFalse((d / "archive").exists())

    def test_ng_restacks_then_blocks_with_needs_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            res = km.run_loop(cfg_for(d, max_retries=2))
            self.assertEqual(res["counts"]["blocked"], 1)
            self.assertEqual(km.exit_code_for(res), 1)
            self.assertTrue((d / "backlog" / "T1.md").exists())
            self.assertTrue((d / "needs" / "T1.md").exists())

    def test_budget_stop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            res = km.run_loop(cfg_for(d, max_retries=999, max_cycles=4))
            self.assertEqual(res["reason"], km.REASON_BUDGET)
            self.assertEqual(res["cycles"], 4)
            self.assertEqual(km.exit_code_for(res), 2)

    def test_no_verify_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="")
            res = km.run_loop(cfg_for(d))
            self.assertEqual(res["counts"]["blocked"], 1)
            self.assertTrue((d / "needs" / "T1.md").exists())

    def test_act_injection_local(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            marker = d / "acted"
            mkb(d, "T1", verify=f"test -f {marker}")
            calls = []

            def fake_act(task, cfg, location="local"):
                calls.append((task.id, location))
                marker.write_text("x")
                return True, "ok"

            res = km.run_loop(cfg_for(d, dry_run=False), act=fake_act)
            self.assertEqual(calls, [("T1", "local")])
            self.assertEqual(res["counts"]["done"], 1)


class TestLocation(unittest.TestCase):
    def test_decide_and_cmd(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            t = km.Task(id="T1", title="heavy batch", verify="true")
            pol = km.Policy(offload=["heavy"])
            # auto: git-bus 無し → local
            self.assertEqual(km.decide_location(t, pol, cfg_for(d)), "local")
            # auto: offload 一致＋git-bus → remote
            c = cfg_for(d, git_bus="git@x:team/bus.git")
            self.assertEqual(km.decide_location(t, pol, c), "remote")
            # 明示 location
            self.assertEqual(km.decide_location(t, km.Policy(), cfg_for(d, location="daemon")), "daemon")
            # remote 指定だが git-bus 無し → local
            self.assertEqual(km.decide_location(t, km.Policy(), cfg_for(d, location="remote")), "local")
            self.assertIn("--git", km.build_kiro_flow_cmd(t, c, use_git=True))
            self.assertNotIn("--git", km.build_kiro_flow_cmd(t, c, use_git=False))

    def test_run_offloads(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "policy.md").write_text("offload: heavy\n")
            mkb(d, "T1", title="heavy job", verify="true")
            mkb(d, "T2", title="light job", verify="true")
            seen = {}

            def fake_act(task, cfg, location="local"):
                seen[task.id] = location
                return True, "ok"

            km.run_loop(cfg_for(d, dry_run=False, git_bus="git@x:team/bus.git"), act=fake_act)
            self.assertEqual(seen["T1"], "remote")
            self.assertEqual(seen["T2"], "local")


class TestPace(unittest.TestCase):
    def test_decide_pace(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertAlmostEqual(km.decide_pace(cfg_for(d, pace=5.0), 2.0), 3.0)
            self.assertEqual(km.decide_pace(cfg_for(d, pace=5.0), 9.0), 0.0)
            self.assertAlmostEqual(
                km.decide_pace(cfg_for(d, max_seconds=20.0, max_cycles=10), 0.5), 1.5)

    def test_run_calls_sleeper(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1"); mkb(d, "T2")
            slept = []
            km.run_loop(cfg_for(d, pace=3.0), sleeper=lambda s: slept.append(s))
            self.assertTrue(slept and all(s > 0 for s in slept))


def _submit_feedback(nf: Path, text: str):
    """needs ファイルにフィードバックを書き、確定チェックボックスを [x] にする。"""
    s = nf.read_text(encoding="utf-8").replace("- [ ] 確定", "- [x] 確定")
    nf.write_text(s + f"\n{text}\n", encoding="utf-8")


class TestFeedback(unittest.TestCase):
    def test_requires_checkbox(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "NG")
            nf = d / "needs" / "T1.md"
            # 未チェックで本文だけ書いた（＝書きかけ）→ 取り込まれない
            nf.write_text(nf.read_text() + "\n書きかけのメモ\n", encoding="utf-8")
            self.assertEqual(km.ingest_feedback(cfg, km.load_tasks(d / "backlog")), [])

    def test_ingest_resumes_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d, actor="alice")
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "繰り返しNG")
            nf = d / "needs" / "T1.md"
            _submit_feedback(nf, "verify を直して再実行して")
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(km.ingest_feedback(cfg, tasks), ["T1"])
            self.assertEqual(tasks[0].status, "ready")
            self.assertIn("feedback", dict(tasks[0].extra))
            self.assertFalse(nf.exists())
            self.assertTrue((d / "decisions" / "T1.md").exists())

    def test_run_loop_ingests_then_completes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "NG")
            _submit_feedback(d / "needs" / "T1.md", "こう直して")
            res = km.run_loop(cfg)
            self.assertEqual(res["ingested"], ["T1"])
            self.assertEqual(res["counts"]["done"], 1)
            self.assertFalse((d / "backlog" / "T1.md").exists())


    def test_debounce_in_watch(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d, watch=True, debounce=999)   # 直近編集は静穏化待ちで取り込まない
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "NG")
            _submit_feedback(d / "needs" / "T1.md", "急いで保存した")
            self.assertEqual(km.ingest_feedback(cfg, km.load_tasks(d / "backlog")), [])


class TestDraft(unittest.TestCase):
    def test_draft_not_consumed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="draft", verify="true")   # 書きかけ＝消化対象外
            mkb(d, "T2", status="ready", verify="true")
            res = km.run_loop(cfg_for(d))
            self.assertEqual(res["counts"]["done"], 1)     # T2 のみ
            self.assertEqual(res["counts"]["draft"], 1)    # T1 は残る
            self.assertTrue((d / "backlog" / "T1.md").exists())
            self.assertFalse(km.has_work(cfg_for(d)))      # draft だけなら watch を起こさない


class TestDelivery(unittest.TestCase):
    def test_extract_ref(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            self.assertIn("/pull/42", km.extract_delivery_ref("done https://github.com/o/r/pull/42 ok", cfg))
            self.assertIn("commit", km.extract_delivery_ref("created abcdef1 done", cfg))

    def test_delivery_note_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", title="納品物A", verify="true")

            def fake_act(task, cfg, location="local"):
                return True, "pushed https://github.com/o/r/pull/7"

            res = km.run_loop(cfg_for(d, dry_run=False), act=fake_act)
            self.assertEqual(res["counts"]["done"], 1)
            note = (d / "archive" / "T1.md").read_text()
            self.assertIn("## 納品書", note)
            self.assertIn("/pull/7", note)
            manifest = (d / "DELIVERY.md").read_text()
            self.assertIn("納品物A", manifest)
            self.assertIn("/pull/7", manifest)


def _seed_learn(d: Path, src: str, title: str, guide: str):
    """decisions/<src>.md に learn ルールを置く。"""
    (d / "decisions").mkdir(parents=True, exist_ok=True)
    (d / "decisions" / f"{src}.md").write_text(
        f"## DR-1  2026-06-18  actor: alice\n- action  : feedback-resume\n"
        f"- learn: {title} :: {guide}\n", encoding="utf-8")


def _seed_hits(d: Path, src: str, n: int):
    """auto-resolve が src を n 回参照した決定記録を作る（昇格の根拠）。"""
    (d / "decisions").mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / "decisions" / f"H{i}.md").write_text(
            f"## DR-1  2026-06-18  actor: auto\n- action  : auto-resolve\n"
            f"- reason  : learned from {src}: なおせ\n", encoding="utf-8")


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
            mems = list((home / "memory" / "home" / "memories" / "kiro-autonomous").glob("*.md"))
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
            mem = home / "memory" / "home" / "memories" / "kiro-autonomous"
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


class TestWatch(unittest.TestCase):
    def test_watch_picks_up_new_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            cfg = cfg_for(d)

            def slp(_):
                mkb(d, "T2", verify="true")  # idle 中に人が新タスク投入した想定

            last = km.run_watch(cfg, sleeper=slp, max_passes=2)
            self.assertEqual(last["reason"], km.REASON_DRAINED)
            self.assertEqual(list((d / "backlog").glob("*.md")), [])


class TestDecisionRecords(unittest.TestCase):
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


class TestRot(unittest.TestCase):
    def test_detect_unverifiable_and_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", title="同じ作業", verify="true")
            mkb(d, "T2", title="同じ作業", verify="true")   # duplicate
            mkb(d, "T3", title="no verify", verify="")       # unverifiable
            rot = {t.id: r for t, r in km.detect_rot(cfg_for(d), km.load_tasks(d / "backlog"))}
            self.assertIn("duplicate", rot.get("T2", ""))
            self.assertIn("unverifiable", rot.get("T3", ""))
            self.assertNotIn("T1", rot)

    def test_stale_by_age(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", title="old task", verify="true")
            old = time.time() - 30 * 86400
            os.utime(d / "backlog" / "T1.md", (old, old))
            rot = km.detect_rot(cfg_for(d, rot_age_days=14), km.load_tasks(d / "backlog"))
            self.assertTrue(any(t.id == "T1" and "stale" in r for t, r in rot))

    def test_run_with_rot_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "A", title="dup", verify="true")
            mkb(d, "B", title="dup", verify="true")
            res = km.run_loop(cfg_for(d, rot=True))
            self.assertTrue((d / "needs" / "B.md").exists())   # duplicate → 人の判断
            self.assertEqual(res["counts"]["blocked"], 1)

    def test_cmd_rot_fix(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", title="x", verify="")  # unverifiable
            self.assertEqual(km.cmd_rot(cfg_for(d), fix=True), 1)
            self.assertEqual(km.load_tasks(d / "backlog")[0].status, "blocked")
            self.assertTrue((d / "needs" / "T1.md").exists())


class TestLayout(unittest.TestCase):
    def test_files_consolidated_under_root(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bl = d / ".kiro-autonomous" / "backlog"
            bl.mkdir(parents=True)
            (bl / "T1.md").write_text(
                "## T1: x\n- status: ready\n- verify: `true`\n- retries: 0\n", encoding="utf-8")
            rc = km.main(["run", "--workdir", str(d), "--planner", "none",
                          "--flow-planner", "stub", "--executor", "stub", "--dry-run"])
            self.assertEqual(rc, 0)
            root = d / ".kiro-autonomous"
            self.assertTrue((root / "journal.md").exists())
            self.assertTrue((root / "archive" / "T1.md").exists())   # done → root/archive
            self.assertFalse((bl / "T1.md").exists())
            # ルート以外に散らばっていない
            self.assertFalse((d / "backlog").exists())
            self.assertFalse((d / "journal.md").exists())

    def test_cleanup_bus_removes_run_state(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (cfg.bus / "runs" / "r1").mkdir(parents=True)
            (cfg.bus / "inbox").mkdir(parents=True)
            km._cleanup_bus(cfg)
            self.assertFalse((cfg.bus / "runs").exists())
            self.assertFalse((cfg.bus / "inbox").exists())

    def test_no_cleanup_keeps_bus(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, cleanup=False)
            (cfg.bus / "runs" / "r1").mkdir(parents=True)
            km._cleanup_bus(cfg)
            self.assertTrue((cfg.bus / "runs").exists())


class TestDaemonRouting(unittest.TestCase):
    def test_kf_base_git_flag(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d, git_bus="git@x:bus.git")
            self.assertNotIn("--git", km._kf_base(c, False))
            self.assertIn("--git", km._kf_base(c, True))

    def test_daemon_detection(self):
        if km.fcntl is None:
            self.skipTest("fcntl 無し")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            lp = km.daemon_lock_path(cfg, False)
            self.addCleanup(lambda: lp.exists() and lp.unlink())
            self.assertFalse(km.daemon_running(cfg))      # ロックファイル無し
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text("")
            self.assertFalse(km.daemon_running(cfg))      # 在るが保持されていない
            f = open(lp, "r+")
            km.fcntl.flock(f, km.fcntl.LOCK_EX | km.fcntl.LOCK_NB)
            try:
                self.assertTrue(km.daemon_running(cfg))   # 保持中 = daemon 稼働
            finally:
                km.fcntl.flock(f, km.fcntl.LOCK_UN)
                f.close()


class TestBareDefault(unittest.TestCase):
    """サブコマンド省略時は常駐監視（run --watch）を既定にする。"""

    def _route(self, argv):
        captured = {}
        orig = km.cmd_run
        km.cmd_run = lambda cfg: (captured.update(cmd="run", watch=cfg.watch), 0)[1]
        try:
            rc = km.main(argv)
        finally:
            km.cmd_run = orig
        return rc, captured

    def test_no_args_defaults_to_run_watch(self):
        rc, cap = self._route([])
        self.assertEqual(rc, 0)
        self.assertEqual(cap, {"cmd": "run", "watch": True})

    def test_bare_flags_route_to_run_watch(self):
        # サブコマンド無しで run 用フラグだけ渡しても watch 常駐になる
        _, cap = self._route(["--poll", "10"])
        self.assertEqual(cap, {"cmd": "run", "watch": True})

    def test_explicit_run_does_not_force_watch(self):
        # 明示 run はこれまで通り（--watch を勝手に付けない）
        _, cap = self._route(["run"])
        self.assertEqual(cap, {"cmd": "run", "watch": False})

    def test_other_subcommands_unaffected(self):
        # needs はバックログ未作成なら従来通り 2 を返す（run にすり替えない）
        with tempfile.TemporaryDirectory() as d:
            rc = km.main(["needs", "--workdir", d, "--root", str(Path(d) / ".ka")])
            self.assertEqual(rc, 2)


class TestInstances(unittest.TestCase):
    """稼働インスタンスのレジストリ（外部操作者がフォルダを発見する口）。"""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._prev = os.environ.get("KIRO_AUTONOMOUS_HOME")
        os.environ["KIRO_AUTONOMOUS_HOME"] = self._home

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("KIRO_AUTONOMOUS_HOME", None)
        else:
            os.environ["KIRO_AUTONOMOUS_HOME"] = self._prev

    def test_register_then_discover(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), watch=True)
            paths = km.register_instance(cfg)
            self.addCleanup(lambda: [x.unlink() for x in paths if x.exists()])
            recs = km.list_instances()
            self.assertEqual(len(recs), 1)
            r = recs[0]
            self.assertEqual(r["pid"], os.getpid())
            self.assertTrue(r["watch"])
            self.assertEqual(r["root"], str(Path(d).resolve()))
            # 主要パスが揃っていて、外部から各ファイルへ直接到達できる
            for k in ("backlog", "needs", "archive", "policy", "delivery", "journal"):
                self.assertIn(k, r)
            self.assertIn(r["runtime"], ("linux", "wsl", "windows", "darwin"))

    def test_dead_pid_is_pruned(self):
        d = km.instances_dir()
        d.mkdir(parents=True, exist_ok=True)
        dead = d / "999999999.json"
        dead.write_text('{"pid": 999999999, "root": "/x"}', encoding="utf-8")
        self.assertEqual(km.list_instances(), [])      # 死んだ PID は出ない
        self.assertFalse(dead.exists())                # かつ掃除される

    def test_run_registers_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", title="x", verify="true")
            rc = km.main(["run", "--workdir", str(d), "--root", str(d / ".ka"),
                          "--planner", "none", "--flow-planner", "stub",
                          "--executor", "stub", "--dry-run"])
            self.assertEqual(rc, 0)
            # run 終了後はレジストリから自分が消えている（finally で unlink）
            self.assertNotIn(os.getpid(), [r["pid"] for r in km.list_instances()])

    def test_cmd_instances_json_smoke(self):
        self.assertEqual(km.cmd_instances(as_json=True), 0)
        self.assertEqual(km.cmd_instances(as_json=False), 0)


class TestRemoteDiscovery(unittest.TestCase):
    """共有レジストリ越しの別ホスト発見（§11-7）。core はファイル操作のみ・ネットワーク非依存を保つ。"""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._shared = tempfile.mkdtemp()
        self._prev = os.environ.get("KIRO_AUTONOMOUS_HOME")
        self._prev_reg = os.environ.get("KIRO_AUTONOMOUS_REGISTRY")
        os.environ["KIRO_AUTONOMOUS_HOME"] = self._home
        os.environ.pop("KIRO_AUTONOMOUS_REGISTRY", None)

    def tearDown(self):
        for k, v in (("KIRO_AUTONOMOUS_HOME", self._prev),
                     ("KIRO_AUTONOMOUS_REGISTRY", self._prev_reg)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _remote(self, host, pid, hb_age, root="/srv/p"):
        d = Path(self._shared); d.mkdir(parents=True, exist_ok=True)
        rec = {"pid": pid, "root": root, "host": host, "watch": True, "runtime": "linux",
               "heartbeat": time.time() - hb_age, "ttl": 90, "started_at": time.time() - hb_age}
        (d / f"{host}-{pid}.json").write_text(__import__("json").dumps(rec), encoding="utf-8")
        return rec

    def test_record_has_heartbeat_and_ttl(self):
        with tempfile.TemporaryDirectory() as wd:
            cfg = cfg_for(Path(wd), watch=True, poll=40.0)
            rec = km.instance_record(cfg)
            self.assertIn("heartbeat", rec)
            self.assertEqual(rec["host"], socket.gethostname())
            self.assertGreaterEqual(rec["ttl"], km.INSTANCE_TTL)
            self.assertGreaterEqual(rec["ttl"], cfg.poll * 3)      # poll より十分長い

    def test_register_writes_to_shared_and_refresh_bumps_heartbeat(self):
        with tempfile.TemporaryDirectory() as wd:
            cfg = cfg_for(Path(wd), watch=True)
            paths = km.register_instance(cfg, [self._shared])
            self.addCleanup(lambda: [p.unlink() for p in paths if p.exists()])
            # ローカル home と共有先の両方へホスト修飾名で書かれる
            self.assertEqual(len(paths), 2)
            self.assertTrue(any(Path(self._shared) in p.parents for p in paths))
            self.assertTrue(all(p.name == f"{socket.gethostname()}-{os.getpid()}.json" for p in paths))
            before = __import__("json").loads(paths[0].read_text())["heartbeat"]
            time.sleep(0.01)
            km.refresh_instance(paths)
            after = __import__("json").loads(paths[0].read_text())["heartbeat"]
            self.assertGreater(after, before)

    def test_live_remote_discovered_stale_hidden(self):
        self._remote("hostB", 101, hb_age=5)              # 生存
        self._remote("hostC", 202, hb_age=9999)           # 古い → 停止扱い
        recs = km.list_instances(extra=[self._shared])
        seen = {(r["host"], r["pid"]) for r in recs}
        self.assertIn(("hostB", 101), seen)
        self.assertNotIn(("hostC", 202), seen)

    def test_select_instances_excludes_remote(self):
        self._remote("hostB", 101, hb_age=1)
        # 停止対象は自ホストのみ（別ホストの PID へシグナルは送れない）
        self.assertEqual(km.select_instances(want_all=True, extra=[self._shared]), [])

    def test_aggregate_dedup_keeps_freshest(self):
        # 同一インスタンスがローカルと共有の両方にある → 1件に集約し heartbeat の新しい方を採用
        km.instances_dir().mkdir(parents=True, exist_ok=True)
        old = {"pid": 101, "root": "/srv/p", "host": "hostB", "watch": True,
               "heartbeat": time.time() - 50, "ttl": 90}
        (km.instances_dir() / "hostB-101.json").write_text(__import__("json").dumps(old),
                                                           encoding="utf-8")
        self._remote("hostB", 101, hb_age=2)              # 共有側はより新しい
        recs = [r for r in km.list_instances(extra=[self._shared])
                if (r["host"], r["pid"]) == ("hostB", 101)]
        self.assertEqual(len(recs), 1)
        self.assertGreater(recs[0]["heartbeat"], time.time() - 10)

    def test_split_registry_parses_pathsep_and_list(self):
        joined = os.pathsep.join(["/a", "/b"])
        self.assertEqual(km._split_registry(joined), ["/a", "/b"])
        self.assertEqual(km._split_registry(["/a", joined]), ["/a", "/a", "/b"])
        self.assertEqual(km._split_registry(None), [])

    def test_env_registry_is_read(self):
        self._remote("hostB", 303, hb_age=3)
        os.environ["KIRO_AUTONOMOUS_REGISTRY"] = self._shared
        seen = {(r["host"], r["pid"]) for r in km.list_instances()}
        self.assertIn(("hostB", 303), seen)               # env でも共有先を読む

    def test_cmd_instances_shows_remote_json(self):
        self._remote("hostB", 404, hb_age=2, root="/srv/q")
        self.assertEqual(km.cmd_instances(as_json=True, extra=[self._shared]), 0)
        recs = km.list_instances(extra=[self._shared])
        self.assertIn("hostB", {r["host"] for r in recs})


class TestLifecycle(unittest.TestCase):
    """常駐ライフサイクル（start / stop / restart）。レジストリの上に起動・停止操作を載せる。"""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._prev = os.environ.get("KIRO_AUTONOMOUS_HOME")
        os.environ["KIRO_AUTONOMOUS_HOME"] = self._home

    def tearDown(self):
        km.cmd_stop(want_all=True)            # 取りこぼした daemon を確実に止める
        if self._prev is None:
            os.environ.pop("KIRO_AUTONOMOUS_HOME", None)
        else:
            os.environ["KIRO_AUTONOMOUS_HOME"] = self._prev

    def _write_rec(self, pid, root):
        import socket
        d = km.instances_dir(); d.mkdir(parents=True, exist_ok=True)
        # 本番（instance_record）は root を resolve して保存するのでフィクスチャも揃える
        # （macOS では /tmp→/private/tmp のため生パスだと select の照合に外れる）
        (d / f"{pid}.json").write_text(
            __import__("json").dumps({"pid": pid, "root": km._norm_root(str(root)), "watch": True,
                                      "host": socket.gethostname()}),
            encoding="utf-8")

    def test_select_by_pid_root_and_all(self):
        me = os.getpid()
        root = "/tmp/wrk/.kiro-autonomous"
        self._write_rec(me, root)
        self.assertEqual([r["pid"] for r in km.select_instances(pid=me)], [me])
        self.assertEqual([r["pid"] for r in km.select_instances(root=root)], [me])  # root 直指定
        self.assertEqual([r["pid"] for r in km.select_instances(root="/tmp/wrk")], [me])  # 作業ルート
        self.assertEqual([r["pid"] for r in km.select_instances(want_all=True)], [me])
        self.assertEqual(km.select_instances(root="/no/such"), [])

    def test_stop_kills_process_and_cleans_registry(self):
        import subprocess as sp
        child = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self._write_rec(child.pid, "/tmp/x/.kiro-autonomous")
        rc = km.cmd_stop(pid=child.pid, timeout=5.0)
        self.assertEqual(rc, 0)
        self.assertFalse(km._pid_alive(child.pid))
        self.assertFalse((km.instances_dir() / f"{child.pid}.json").exists())

    def test_stop_without_target_returns_1(self):
        self.assertEqual(km.cmd_stop(root="/nothing/here"), 1)

    def test_start_registers_then_stop(self):
        work = Path(tempfile.mkdtemp())
        (work / "kiro-autonomous.json").write_text(
            '{"executor":"stub","planner":"none","flow_planner":"stub","poll":0.3}', encoding="utf-8")
        cfg = str(work / "kiro-autonomous.json")
        rc = km.cmd_start(root=str(work), config=cfg)
        self.assertEqual(rc, 0)
        # 登録の出現を待つ（最大 ~5s）
        root = str((work).resolve())
        for _ in range(50):
            if km.select_instances(root=root):
                break
            time.sleep(0.1)
        self.assertTrue(km.select_instances(root=root))         # 起動して登録された
        self.assertEqual(km.cmd_start(root=str(work), config=cfg), 1)  # 重複起動は拒否
        self.assertEqual(km.cmd_stop(root=str(work)), 0)
        self.assertEqual(km.select_instances(root=root), [])    # 停止で消える

    def test_watch_sigterm_graceful_exit(self):
        # SIGTERM 化された KeyboardInterrupt は graceful 停止: traceback を出さず 0 で終え、
        # finally で登録を掃除する（README の「stop は graceful…終了」を担保）。
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), watch=True)
            saved = signal.getsignal(signal.SIGTERM)
            try:
                with mock.patch.object(km, "run_watch", side_effect=KeyboardInterrupt):
                    rc = km.cmd_run(cfg)        # 例外は伝播せず捕捉される
            finally:
                signal.signal(signal.SIGTERM, saved)   # ハンドラを元へ戻す
            self.assertEqual(rc, 0)
            self.assertEqual(km.select_instances(want_all=True), [])  # 登録は掃除済み


class TestConfigFile(unittest.TestCase):
    """設定ファイル（YAML 任意 / JSON フォールバック、CLI > config > 既定）。"""

    @staticmethod
    def _resolve(cfg_path=None, **cli):
        # CLI 未指定キーは None（getattr の既定）。明示したいキーだけ cli に渡す。
        ns = types.SimpleNamespace(config=cfg_path, **cli)
        km.resolve_config(ns)
        return ns

    def test_json_config_fills_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-autonomous.json"
            p.write_text('{"executor":"stub","planner":"none","poll":9,"max_cycles":3}',
                         encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual((ns.executor, ns.planner, ns.poll, ns.max_cycles),
                             ("stub", "none", 9, 3))

    def test_cli_overrides_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-autonomous.json"
            p.write_text('{"executor":"stub","planner":"none"}', encoding="utf-8")
            ns = self._resolve(str(p), executor="kiro")   # CLI 明示は維持される
            self.assertEqual(ns.executor, "kiro")          # CLI 勝ち
            self.assertEqual(ns.planner, "none")           # config 採用

    def test_builtin_defaults_when_no_config(self):
        ns = self._resolve(None)
        self.assertEqual((ns.executor, ns.planner, ns.poll, ns.max_cycles, ns.location),
                         ("kiro", "kiro", 5.0, 20, "auto"))
        self.assertEqual((ns.auto_adjudicate, ns.adjudicate_max), (True, 1))  # 既定 on

    def test_yaml_config_when_pyyaml_available(self):
        if km.yaml is None:
            self.skipTest("PyYAML 未導入（JSON 経路は別テストで担保）")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-autonomous.yaml"
            p.write_text("executor: stub\nmax_retries: 5\ngit_branch: develop\n", encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual((ns.executor, ns.max_retries, ns.git_branch),
                             ("stub", 5, "develop"))

    def test_missing_explicit_config_exits(self):
        with self.assertRaises(SystemExit):
            self._resolve("/no/such/kiro-autonomous.yaml")

    def test_boolean_flags_from_config(self):
        # 真偽フラグ（watch/do_archive/learn/rot/cleanup/once/dry_run/ltm/regression_revert）が
        # 設定ファイルで効く。resolve_config は CLI 未指定（None）のみ config→既定 で埋める。
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-autonomous.json"
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
            p = Path(d) / "kiro-autonomous.json"
            p.write_text('{"watch":true,"learn":false}', encoding="utf-8")
            ns = self._resolve(str(p), watch=False, learn=True)
            self.assertEqual((ns.watch, ns.learn), (False, True))     # CLI 勝ち

    def test_boolean_defaults_when_no_config(self):
        ns = self._resolve(None, watch=None, do_archive=None, learn=None, cleanup=None,
                           rot=None, once=None, dry_run=None, ltm=None, regression_revert=None)
        self.assertEqual((ns.watch, ns.do_archive, ns.learn, ns.cleanup),
                         (False, True, True, True))                    # 組み込み既定


class TestAutoAdjudicate(unittest.TestCase):
    """needs に落とす前の kiro-cli 自律裁定ゲート（既定 off・有限回・人 policy 不介入）。"""

    def setUp(self):
        self._orig = km._run_kiro_cli
        self.calls = []

    def tearDown(self):
        km._run_kiro_cli = self._orig

    def _stub(self, payload):
        def run(prompt, model):
            self.calls.append(prompt)
            return payload
        km._run_kiro_cli = run

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
                                         kiro_run=lambda p, m: '{"decision":"requeue","guidance":"G"}'),
                ("requeue", "G"))
            self.assertEqual(
                km.adjudicate_escalation(cfg, task, "ng",
                                         kiro_run=lambda p, m: '{"decision":"escalate"}')[0],
                "escalate")
            # 不正 JSON・例外は安全側（人へ）にフォールバック
            self.assertEqual(km.adjudicate_escalation(cfg, task, "ng", kiro_run=lambda p, m: "??")[0],
                             "escalate")

            def boom(p, m):
                raise RuntimeError("kiro 不在")
            self.assertEqual(km.adjudicate_escalation(cfg, task, "ng", kiro_run=boom)[0], "escalate")

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

            km.adjudicate_escalation(cfg, task, "ng", kiro_run=run)
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
            res = km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False,
                                      regression_cmd="false", max_cycles=3))
            self.assertEqual(res["counts"]["done"], 0)
            self.assertEqual(res["counts"]["blocked"], 1)

    def test_regression_gate_passes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._mk(d, "T1", "## T1: x\n- status: ready\n- verify: `true`\n")
            res = km.run_loop(cfg_for(d, learn=False, auto_adjudicate=False, regression_cmd="true"))
            self.assertEqual(res["counts"]["done"], 1)

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


class TestKiroFlowIntegration(unittest.TestCase):
    def test_stub_end_to_end(self):
        kf = Path(__file__).resolve().parents[2] / "kiro-flow" / "kiro-flow.py"
        if not kf.exists():
            self.skipTest("kiro-flow.py が見つからない")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            out = d / "out.txt"
            out.write_text("done")
            mkb(d, "T1", title="何か", verify=f"test -f {out}")
            os.environ["KIRO_FLOW_STUB_SLEEP_MAX"] = "0"
            res = km.run_loop(cfg_for(d, dry_run=False, act_timeout=120, max_cycles=3))
            self.assertEqual(res["counts"]["done"], 1)
            self.assertEqual(res["reason"], km.REASON_DRAINED)


if __name__ == "__main__":
    unittest.main()
