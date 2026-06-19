"""kiro-autonomous の単体テスト（標準ライブラリ unittest）。

案件毎ファイル（backlog/<id>.md）・done でファイル削除・watch 常駐・フィードバック往復・
案件毎の needs/decisions を、kiro-flow を呼ばずに検証する。kiro-flow stub 統合も含む。

    python -m unittest discover -s tools/kiro-autonomous/tests
"""
import importlib.util
import os
import sys
import tempfile
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
            p = km.register_instance(cfg)
            self.addCleanup(lambda: p and p.exists() and p.unlink())
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
