"""agent-project の単体テスト — backlog（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


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
        r = km.rank_agent(ready, None, agent_run=lambda p, m: '["T1","T0"]')
        self.assertEqual([t.id for t in r], ["T1", "T0"])
        self.assertIsNone(km.rank_agent(
            ready, None, agent_run=lambda p, m: (_ for _ in ()).throw(RuntimeError())))

    def test_rank_agent_skips_llm_for_zero_or_one(self):
        # 0/1 件は並べ替えの余地が無い＝kiro-cli（LLM）を呼ばずに即返す
        def boom(p, m):
            raise AssertionError("LLM は呼ばれないはず")

        self.assertEqual(km.rank_agent([], None, agent_run=boom), [])
        one = [km.Task(id="only", title="x")]
        self.assertEqual([t.id for t in km.rank_agent(one, None, agent_run=boom)], ["only"])

    def test_prioritize_skips_llm_for_single_task(self):
        # prioritize（planner=kiro）でも ready が 1 件なら ranker（LLM）を呼ばない。
        # policy（pin/defer）は 1 件でも後段で効くことも併せて確認する。
        called = {"n": 0}

        def ranker(ready, model):
            called["n"] += 1
            return list(reversed(ready))

        one = [km.Task(id="solo", title="x")]
        order = km.prioritize(one, km.Policy(), planner="agent", ranker=ranker)
        self.assertEqual([t.id for t in order], ["solo"])
        self.assertEqual(called["n"], 0, "1 件では ranker（LLM）を呼ばない")

        # 2 件になると従来どおり ranker が呼ばれる（回帰防止）
        two = [km.Task(id="a", title="a"), km.Task(id="b", title="b")]
        order2 = km.prioritize(two, km.Policy(), planner="agent", ranker=ranker)
        self.assertEqual(called["n"], 1)
        self.assertEqual([t.id for t in order2], ["b", "a"])


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
            rc = km.main(["enqueue", "--title", "X", "--verify", "true", "--no-plan-review",
                          "--workdir", str(d), "--root", str(d / ".ka")])
            self.assertEqual(rc, 0)
            # 新レイアウト: <root>/backlog（root = プロジェクトルート）
            files = list((d / ".ka" / "backlog").glob("*.md"))
            self.assertEqual(len(files), 1)
            self.assertEqual(km.parse_task(files[0].read_text(), files[0].stem).norm_status(), "ready")


class TestIntake(unittest.TestCase):
    """取り込みコマンド（intake_cmd）。外部の決定的検出器から修復タスクを
    watch の周期で汲み上げる。冪等（id が現役 backlog に居れば飛ばす）・有限・無害。"""

    def setUp(self):
        km._INTAKE_LAST.clear()

    def _cfg(self, d, cmd, interval=0.0):
        return cfg_for(d, inbox=d / "inbox", learn=False, auto_adjudicate=False,
                       max_cycles=10, intake_cmd=cmd, intake_interval=interval)

    def test_run_intake_enqueues_and_dedups_by_id(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = ("printf '%s' '[{\"id\":\"I1\",\"title\":\"i1\",\"verify\":\"true\"},"
                   "{\"id\":\"I2\",\"title\":\"i2\",\"verify\":\"true\"}]'")
            cfg = self._cfg(Path(d), cmd)
            km.ensure_dirs(cfg)
            got = km.run_intake(cfg)
            self.assertEqual(sorted(t.id for t in got), ["I1", "I2"])
            self.assertEqual(km.run_intake(cfg), [])       # 冪等: 現役 backlog に居る id は再投入しない
            self.assertEqual(sorted(p.stem for p in cfg.backlog.glob("*.md")), ["I1", "I2"])

    def test_run_intake_interval_throttles(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d), "printf '%s' '[{\"id\":\"T1\",\"title\":\"t\",\"verify\":\"true\"}]'",
                            interval=3600.0)
            km.ensure_dirs(cfg)
            self.assertEqual(len(km.run_intake(cfg)), 1)
            (cfg.backlog / "T1.md").unlink()               # backlog から消しても…
            self.assertEqual(km.run_intake(cfg), [])       # …間隔内は実行自体をしない（律速）

    def test_run_intake_tolerates_failures(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for cmd in ("printf not-json", "exit 3", "true"):   # 非JSON / exit≠0 / 空出力
                cfg = self._cfg(d, cmd)
                km.ensure_dirs(cfg)
                self.assertEqual(km.run_intake(cfg), [])
            self.assertEqual(list(cfg.backlog.glob("*.md")), [])

    def test_run_intake_one_bad_record_does_not_block_the_rest(self):
        # 汎用 intake フック: 1件が title 欠落でも、残りは取り込まれる
        # （model 本体同梱の _parse_intake_records によるレコード単位検証。検出器非依存）。
        with tempfile.TemporaryDirectory() as d:
            cmd = ("printf '%s' '[{\"id\":\"OK1\",\"title\":\"ok\",\"verify\":\"true\"},"
                   "{\"id\":\"BAD1\"}]'")
            cfg = self._cfg(Path(d), cmd)
            km.ensure_dirs(cfg)
            got = km.run_intake(cfg)
            self.assertEqual([t.id for t in got], ["OK1"])
            self.assertEqual([p.stem for p in cfg.backlog.glob("*.md")], ["OK1"])
            self.assertIn("title が空/欠落", cfg.journal.read_text(encoding="utf-8"))

    def test_run_loop_intakes_and_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d), "printf '%s' '{\"id\":\"L1\",\"title\":\"l\",\"verify\":\"true\"}'")
            km.ensure_dirs(cfg)
            res = km.run_loop(cfg)
            self.assertEqual(len(res["inboxed"]), 1)       # パス開始時の intake で取り込み
            self.assertEqual(res["counts"]["done"], 1)     # 同じ run で消化

    def test_watch_idle_intake_wakes_pass(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d), "printf '%s' '{\"id\":\"W1\",\"title\":\"w\",\"verify\":\"true\"}'")
            km.ensure_dirs(cfg)
            calls = {"n": 0}

            def slp(_s):
                calls["n"] += 1
                if calls["n"] > 50:                        # idle intake が壊れたらハングでなく失敗させる
                    raise TimeoutError("idle 中の intake がパスを起こさない")

            # pass1: 開始時 intake→W1 消化(archive)。idle: intake が W1 を再投入→has_work→pass2 が起きる
            last = km.run_watch(cfg, sleeper=slp, max_passes=2)
            self.assertEqual(last["counts"]["done"], 1)


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


class TestIntakeRecall(unittest.TestCase):
    """投入/triage 時の予防リコール（shift-left）: 過去の hold（avoid）に類似する新規 ready を、
    実行せず inbox（人の triage）へ寄せる。DR 学習が『失敗してから』人を絞るのに対し先回りで止める。"""

    def _seed_avoid(self, d, src_id, title, reason):
        c = cfg_for(d)
        km.ensure_dirs(c)
        km.append_decision(c, src_id, "human", context=f"{src_id}（{title}）を保留",
                           action="hold(deny)", reason=reason,
                           affects=f"{src_id} → blocked", avoid=(title, reason))

    def test_enqueue_similar_to_hold_routes_to_human(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            proj = d / ".ka"
            self._seed_avoid(proj, "OLD", "deploy to production", "本番は手動")
            rc = km.main(["enqueue", "--title", "deploy to production tonight", "--verify", "true",
                          "--no-plan-review", "--workdir", str(d), "--root", str(d / ".ka")])
            self.assertEqual(rc, 0)
            t = km.load_tasks(proj / "backlog")[0]
            self.assertEqual(t.norm_status(), "blocked")    # ready にせず人の判断へ（verify 持ちでも実行させない）
            self.assertIn("本番は手動", t.get("recall", ""))   # 出典と理由（OLD :: 本番は手動）を残す
            self.assertTrue((proj / "needs" / f"{t.id}.md").exists())   # 人が approve/hold で裁定
            dec = (proj / "decisions" / f"{t.id}.md").read_text()
            self.assertIn("intake-recall", dec)

    def test_unrelated_enqueue_stays_ready(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._seed_avoid(d, "OLD", "deploy to production", "本番は手動")
            t = km.enqueue_task(cfg_for(d), {"title": "update the readme heading", "verify": "true"})
            self.assertIsNone(km.apply_intake_recall(cfg_for(d), t))
            self.assertEqual(t.norm_status(), "ready")

    def test_recall_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._seed_avoid(d, "OLD", "deploy to production", "本番は手動")
            t = km.enqueue_task(cfg_for(d, intake_recall=False),
                                {"title": "deploy to production tonight", "verify": "true"})
            self.assertIsNone(km.apply_intake_recall(cfg_for(d, intake_recall=False), t))
            self.assertEqual(t.norm_status(), "ready")

    def test_triage_diverts_similar_ready(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._seed_avoid(d, "OLD", "delete production database", "破壊的。人の承認必須")
            mkb(d, "T1", title="delete production database backup", verify="true")
            c = cfg_for(d)
            self.assertEqual(km.cmd_triage(c), 0)
            t = km.load_tasks(d / "backlog")[0]
            self.assertEqual(t.norm_status(), "blocked")    # triage の inbox→ready 昇格に呑まれず人へ残る
            self.assertTrue((d / "needs" / "T1.md").exists())


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
            # 新レイアウト: プロジェクトルート = --root（既定 . = cwd）が唯一のアンカー。
            # 全ファイルがこの直下（workdir はアンカーではないので root を明示する）
            proot = d
            bl = proot / "backlog"
            bl.mkdir(parents=True)
            (bl / "T1.md").write_text(
                "## T1: x\n- status: ready\n- verify: `true`\n- retries: 0\n", encoding="utf-8")
            rc = km.main(["run", "--no-delivery-review", "--root", str(d), "--planner", "none",
                          "--flow-planner", "stub", "--executor", "stub", "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertTrue((proot / "journal.md").exists())
            self.assertTrue((proot / "archive" / "T1.md").exists())   # done → <root>/archive
            self.assertFalse((bl / "T1.md").exists())
            # 旧レイアウト（projects/ ネスト）を作らない
            self.assertFalse((d / "projects").exists())
            self.assertFalse((d / ".agent-projects").exists())

    def test_cleanup_bus_keeps_recent_runs(self):
        # 回帰: 直近の run は残す。act のたびに runs/ を丸ごと消していたため、run は完了して
        # いるのに viewer がその最終状態（全ノード done）を観測する前にディレクトリごと消え、
        # フロータブでは最終ノードが実行中のまま固まって見えていた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)                          # bus_keep_runs=20（既定）
            (cfg.bus / "runs" / "r1").mkdir(parents=True)
            (cfg.bus / "inbox").mkdir(parents=True)
            km._cleanup_bus(cfg)
            self.assertTrue((cfg.bus / "runs" / "r1").exists())   # 直近 run は viewer のために残す
            self.assertFalse((cfg.bus / "inbox").exists())        # submit キューは掃除する

    def test_cleanup_bus_drops_old_runs_beyond_keep(self):
        # 掃除は「古い run を捨てる」ためのもの。新しい順に keep 件だけ残す。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, bus_keep_runs=2)
            for i, age in enumerate([300, 200, 100, 0]):          # r0 が最古・r3 が最新
                p = cfg.bus / "runs" / f"r{i}"
                p.mkdir(parents=True)
                os.utime(p, (time.time() - age, time.time() - age))
            km._cleanup_bus(cfg)
            left = sorted(p.name for p in (cfg.bus / "runs").iterdir())
            self.assertEqual(left, ["r2", "r3"])                  # 新しい 2 件だけ残る

    def test_cleanup_bus_keep_zero_removes_all_runs(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, bus_keep_runs=0)
            (cfg.bus / "runs" / "r1").mkdir(parents=True)
            km._cleanup_bus(cfg)
            self.assertEqual(list((cfg.bus / "runs").iterdir()), [])

    def test_no_cleanup_keeps_bus(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, cleanup=False)
            (cfg.bus / "runs" / "r1").mkdir(parents=True)
            km._cleanup_bus(cfg)
            self.assertTrue((cfg.bus / "runs").exists())

    def test_state_git_keeps_bus(self):
        # state_git でバスをリモート viewer へ鏡写ししている構成では、local run 後も runs/ を
        # 消さない（消すとフロータブに見せたい run 状態を破壊し、削除がリモートへ伝播する）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, state_git="git@example.com:team/agent-state.git")
            (cfg.bus / "runs" / "r1").mkdir(parents=True)
            km._cleanup_bus(cfg)
            self.assertTrue((cfg.bus / "runs").exists())


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


class TestCohort(unittest.TestCase):
    """pilot-then-batch: 同様手順の繰り返しは pilot を1件先行→人レビューで指示を固め→残りを生成。"""

    def test_apply_item_placeholder_and_fallback(self):
        self.assertEqual(km._apply_item("Tを{item}に適用", "a"), "Tをaに適用")
        self.assertEqual(km._apply_item("手順を実施", "b"), "手順を実施（対象: b）")  # プレースホルダ無し

    def test_create_cohort_makes_pilot_and_holds_rest(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            pilot = km.enqueue_task(cfg, {"title": "{item} を移行", "verify": "test -f {item}",
                                          "cohort_items": ["a", "b", "c"]})
            self.assertEqual(pilot.title, "a を移行")
            self.assertEqual(pilot.verify, "test -f a")
            self.assertEqual(pilot.get("cohort_role"), "pilot")
            self.assertEqual(pilot.get("review"), "human")          # pilot は人の承認で固める
            self.assertEqual(len(km.load_tasks(cfg.backlog)), 1)    # 残りはまだ作らない
            state = km._read_cohort(cfg, pilot.get("cohort"))
            self.assertEqual(state["items"], ["b", "c"])
            self.assertEqual(state["status"], "pending")

    def test_materialize_rest_after_pilot_approval(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            pilot = km.enqueue_task(cfg, {"title": "{item} を移行", "verify": "true",
                                          "cohort_items": ["a", "b", "c"]})
            # pilot は verify PASS でも review:human で検収待ち（review）になる
            res = km.run_loop(cfg)
            self.assertEqual(res["counts"]["review"], 1)
            self.assertEqual(res["counts"]["done"], 0)
            # pilot 承認 → 残り 2 件が固めた指示（feedback）付きで ready 生成される
            self.assertEqual(km.cmd_approve(cfg, pilot.id, "命名規則に従うこと"), 0)
            members = [t for t in km.load_tasks(cfg.backlog) if t.get("cohort_role") == "member"]
            self.assertEqual(len(members), 2)
            self.assertEqual(sorted(m.title for m in members), ["b を移行", "c を移行"])
            for m in members:
                self.assertEqual(m.norm_status(), "ready")
                self.assertIn("命名規則に従うこと", m.feedback() or "")   # 固めた指示が伝わる
            self.assertEqual(km._read_cohort(cfg, pilot.get("cohort"))["status"], "done")

    def test_materialize_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            pilot = km.enqueue_task(cfg, {"title": "{item}", "verify": "true",
                                          "cohort_items": ["a", "b"]})
            self.assertEqual(len(km.materialize_cohort_rest(cfg, pilot, "ok")), 1)
            self.assertEqual(km.materialize_cohort_rest(cfg, pilot, "ok"), [])  # 二度目は空（done）
