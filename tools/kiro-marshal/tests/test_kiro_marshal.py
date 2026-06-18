"""kiro-marshal の単体テスト（標準ライブラリ unittest）。

案件毎ファイル（backlog/<id>.md）・done でファイル削除・watch 常駐・フィードバック往復・
案件毎の needs/decisions を、kiro-flow を呼ばずに検証する。kiro-flow stub 統合も含む。

    python -m unittest discover -s tools/kiro-marshal/tests
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_MOD = Path(__file__).resolve().parent.parent / "kiro-marshal.py"
_spec = importlib.util.spec_from_file_location("kiro_marshal", _MOD)
km = importlib.util.module_from_spec(_spec)
sys.modules["kiro_marshal"] = km
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


class TestFeedback(unittest.TestCase):
    def test_ingest_resumes_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d, actor="alice")
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "繰り返しNG")
            nf = d / "needs" / "T1.md"
            nf.write_text(nf.read_text() + "\nverify を直して再実行して\n", encoding="utf-8")
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
            nf = d / "needs" / "T1.md"
            nf.write_text(nf.read_text() + "\nこう直して\n", encoding="utf-8")
            res = km.run_loop(cfg)
            self.assertEqual(res["ingested"], ["T1"])
            self.assertEqual(res["counts"]["done"], 1)
            self.assertFalse((d / "backlog" / "T1.md").exists())


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
