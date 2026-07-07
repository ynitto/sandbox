"""kiro-projects の単体テスト（標準ライブラリ unittest）。

案件毎ファイル（backlog/<id>.md）・done でファイル削除・watch 常駐・フィードバック往復・
案件毎の needs/decisions を、kiro-flow を呼ばずに検証する。kiro-flow stub 統合も含む。

    python -m unittest discover -s tools/kiro-projects/tests
"""
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock as mock
from pathlib import Path

# テストの git コミットを環境のコミット署名設定（commit.gpgsign）から切り離す。
# 署名が有効な環境では署名処理が間欠的に失敗して `git commit` がコミットを作らず、
# git ベースのテスト（成果参照・差分 verify 等）が偶発的に落ちる。GIT_CONFIG_* で
# この子プロセス（と配下）に commit.gpgsign=false を上乗せして決定的にする（identity は温存）。
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = "commit.gpgsign"
os.environ["GIT_CONFIG_VALUE_0"] = "false"

# 自動アップデートは既定 on のため、テスト中にコントリビューターの実 skill-registry.json から
# 更新元が解決されて実ネットワーク/再起動が走るのを防ぐ。存在しないパスを権威指定して registry
# 解決を無効化する（SelfUpdateTests は必要なテストでだけ KIRO_SKILL_REGISTRY を一時上書きする）。
os.environ["KIRO_SKILL_REGISTRY"] = os.path.join(
    tempfile.gettempdir(), "ka-tests-no-such-registry", "skill-registry.json")

_MOD = Path(__file__).resolve().parent.parent / "kiro-projects.py"
_spec = importlib.util.spec_from_file_location("kiro_projects", _MOD)
km = importlib.util.module_from_spec(_spec)
sys.modules["kiro_projects"] = km
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

    def test_rank_agent_skips_llm_for_zero_or_one(self):
        # 0/1 件は並べ替えの余地が無い＝kiro-cli（LLM）を呼ばずに即返す
        def boom(p, m):
            raise AssertionError("LLM は呼ばれないはず")

        self.assertEqual(km.rank_agent([], None, kiro_run=boom), [])
        one = [km.Task(id="only", title="x")]
        self.assertEqual([t.id for t in km.rank_agent(one, None, kiro_run=boom)], ["only"])

    def test_prioritize_skips_llm_for_single_task(self):
        # prioritize（planner=kiro）でも ready が 1 件なら ranker（LLM）を呼ばない。
        # policy（pin/defer）は 1 件でも後段で効くことも併せて確認する。
        called = {"n": 0}

        def ranker(ready, model):
            called["n"] += 1
            return list(reversed(ready))

        one = [km.Task(id="solo", title="x")]
        order = km.prioritize(one, km.Policy(), planner="kiro", ranker=ranker)
        self.assertEqual([t.id for t in order], ["solo"])
        self.assertEqual(called["n"], 0, "1 件では ranker（LLM）を呼ばない")

        # 2 件になると従来どおり ranker が呼ばれる（回帰防止）
        two = [km.Task(id="a", title="a"), km.Task(id="b", title="b")]
        order2 = km.prioritize(two, km.Policy(), planner="kiro", ranker=ranker)
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
            rc = km.main(["enqueue", "--title", "X", "--verify", "true",
                          "--workdir", str(d), "--root", str(d / ".ka")])
            self.assertEqual(rc, 0)
            # 新レイアウト: <root>/projects/default/backlog（--project 未指定は default）
            files = list((d / ".ka" / "projects" / "default" / "backlog").glob("*.md"))
            self.assertEqual(len(files), 1)
            self.assertEqual(km.parse_task(files[0].read_text(), files[0].stem).norm_status(), "ready")


class TestIntake(unittest.TestCase):
    """取り込みコマンド（intake_cmd）。外部の決定的ゲート/検出器（codd-gate 等）から修復タスクを
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


class TestRepoRegistry(unittest.TestCase):
    """repos レジストリ（schemas/repos.schema.json）。<project>/repos.{yaml,yml,json} があれば
    レジストリの正になり、charter の ## repos は互換入力。repos ファイル単独では charter モード
    （目標駆動）は発動しないが、ワークスペース・ルーティングには使える。"""

    def test_registry_file_overrides_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (d / "charter.md").write_text(
                "# Charter: x\n## goal\ny\n## repos\n- old = git@x:old.git\n"
                "  - desc: 旧\n  - base: main\n  - owns: src/**\n", encoding="utf-8")
            (d / "repos.json").write_text(__import__("json").dumps(
                {"app": {"url": "git@x:app.git", "desc": "新", "base": "main",
                         "owns": ["src/**"], "docs": ["docs/**"]}}), encoding="utf-8")
            before = (d / "repos.json").read_text(encoding="utf-8")
            ch = km.load_charter(cfg)
            self.assertEqual([s["name"] for s in ch.repo_specs], ["app"])   # ファイルが勝つ
            self.assertEqual(ch.repo_specs[0]["target"], "main")            # target 省略 = base
            self.assertFalse(ch.repo_specs[0]["readonly"])                  # owns あり = 書込先
            self.assertEqual((d / "repos.json").read_text(encoding="utf-8"),
                             before)                                        # 手書きは上書きしない

    def test_registry_without_charter_routes_but_no_charter_mode(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (d / "repos.json").write_text(__import__("json").dumps(
                {"app": {"url": "git@x:app.git", "desc": "本体", "base": "main",
                         "owns": ["src/**"]}}), encoding="utf-8")
            self.assertIsNone(km.load_charter(cfg))          # 目標駆動は発動しない（charter.md 無し）
            bd = d / "backlog"
            bd.mkdir(parents=True, exist_ok=True)
            (bd / "T1.md").write_text(
                "## T1: x を直す\n- status: ready\n- verify: `true`\n- paths: src/x.py\n",
                encoding="utf-8")
            t = [x for x in km.load_tasks(cfg.backlog) if x.id == "T1"][0]
            spec, routed = km.resolve_workspace(cfg, t, km.load_policy(cfg.policy))
            self.assertEqual((spec["name"], routed), ("app", "owns"))       # レジストリ単独で解決

    def test_charter_exports_generated_registry(self):
        """repos ファイルが無ければ charter から自動生成して外部ツール（codd-gate --repos）へ渡す。
        生成物には _meta マーカーが付き、正は charter のまま（charter 変更に追従・手書きなら不干渉）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (d / "charter.md").write_text(
                "# Charter: x\n## goal\ny\n## repos\n- app = git@x:app.git\n"
                "  - desc: 本体\n  - base: main\n  - owns: src/**\n"
                "  - docs: docs/**, README.md\n", encoding="utf-8")
            ch = km.load_charter(cfg)
            rp = d / "repos.json"
            self.assertTrue(rp.exists())                       # charter から自動生成
            data = __import__("json").loads(rp.read_text(encoding="utf-8"))
            self.assertIn("generated_from", data["_meta"])     # 生成物マーカー
            self.assertEqual(data["app"]["url"], "git@x:app.git")
            self.assertEqual(data["app"]["docs"], ["docs/**", "README.md"])   # 分類グロブも損失なし
            self.assertEqual([s["name"] for s in ch.repo_specs], ["app"])     # 正は charter のまま
            (d / "charter.md").write_text(                     # charter 更新 → 生成物が追従
                "# Charter: x\n## goal\ny\n## repos\n- app2 = git@x:app2.git\n"
                "  - desc: 本体2\n  - base: main\n  - owns: src/**\n", encoding="utf-8")
            km.load_charter(cfg)
            data = __import__("json").loads(rp.read_text(encoding="utf-8"))
            self.assertIn("app2", data)
            self.assertNotIn("app", data)
            (d / "charter.md").write_text(                     # ## repos が消えたら生成物も消す
                "# Charter: x\n## goal\ny\n", encoding="utf-8")
            km.load_charter(cfg)
            self.assertFalse(rp.exists())

    def test_broken_registry_falls_back_to_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (d / "charter.md").write_text(
                "# Charter: x\n## goal\ny\n## repos\n- old = git@x:old.git\n"
                "  - desc: 旧\n  - base: main\n  - owns: src/**\n", encoding="utf-8")
            (d / "repos.json").write_text("{ 壊れた json", encoding="utf-8")
            ch = km.load_charter(cfg)
            self.assertEqual([s["name"] for s in ch.repo_specs], ["old"])   # 黙って空にしない


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

    def test_approve_clears_stale_claim_lock(self):
        # worker クラッシュ等で残った古い claim ロックは、人手 approve で掃除される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "backlog" / "R1.md").parent.mkdir(parents=True, exist_ok=True)
            (d / "backlog" / "R1.md").write_text(
                "## R1: x\n- status: review\n- verify: `true`\n", encoding="utf-8")
            lock = d / "claims" / "R1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text('{"host":"dead","pid":1,"ts":0,"id":"R1"}', encoding="utf-8")
            km.cmd_approve(cfg_for(d, learn=False), "R1", "ok")
            self.assertFalse(lock.exists())                  # 承認時に古いロックを掃除

    def test_hold_clears_stale_claim_lock(self):
        # hold（blocked 化）でも doing を離れるので claim ロックを残さない
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "H1", verify="true")
            lock = d / "claims" / "H1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text('{"host":"dead","pid":1,"ts":0,"id":"H1"}', encoding="utf-8")
            km.cmd_hold(cfg_for(d, learn=False), "H1", "保留")
            self.assertFalse(lock.exists())

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


class TestDoctor(unittest.TestCase):
    """稼働診断（doctor）: 決定的チェック・kiro-cli 診断・分類・env/config 修正・program 起票。"""

    def _cfg(self, d, **kw):
        kw.setdefault("planner", "none")
        kw.setdefault("executor", "stub")
        kw.setdefault("auto_adjudicate", False)
        return cfg_for(Path(d), **kw)

    def test_env_findings_detect_missing_kiro_cli(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, planner="kiro")            # planner=kiro は kiro-cli を要求
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
            # kiro-flow/git あり・ディレクトリ作成済み → env/config の致命所見は出ない
            self.assertFalse(any(f["severity"] == "critical" for f in fs))
            self.assertFalse(any(f.get("fix_action") == "create-dirs" for f in fs))

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
            self.assertIsNone(km.diagnose_with_agent(cfg, {}, [], kiro_run=boom))

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
                rc = km.cmd_doctor(cfg, fix=True, as_json=True, kiro_run=agent,
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

            rc = km.cmd_doctor(cfg, fix=True, kiro_run=agent,
                               skill_finder=lambda _n: None)   # スキル無し
            self.assertEqual(calls, [])                      # 起票は呼ばない（出力のみ）
            self.assertEqual(rc, 2)                          # 未解決の critical program → 2

    def test_doctor_via_main_without_backlog_diagnoses(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # kiro-cli/kiro-flow を呼ばない構成で main 経由（backlog 無しでも落ちない）
            rc = km.main(["doctor", "--json", "--no-flow", "--workdir", str(d),
                          "--root", str(d / ".ka"), "--planner", "none", "--executor", "stub",
                          "--no-auto-adjudicate"])
            self.assertIn(rc, (0, 1, 2))

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

            # kiro-flow doctor が返す findings（env/config は解消済み・program は起票済み）
            def flow_finder(c, fix):
                return [
                    {"category": "config", "severity": "warn", "title": "バスのルートが未作成",
                     "evidence": "bus=...", "fix": "作成", "source": "kiro-flow",
                     "resolved": "バスのルートを作成しました"},
                    {"category": "program", "severity": "critical", "title": "flow のクラッシュ",
                     "evidence": "run-x", "fix": "例外", "source": "kiro-flow",
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
                    rc = km.cmd_doctor(cfg, fix=True, as_json=True, kiro_run=agent,
                                       skill_finder=lambda n: km.find_skill(n, home=str(home)),
                                       flow_finder=flow_finder)
                captured = json.loads(buf.getvalue())
            # flow 由来の program は本体が再起票しない（kiro-flow が起票済み）
            self.assertEqual(filed, [])
            # flow の critical は解消済みで統合 → 未解決 critical なし（rc は 2 でない）
            self.assertIn(rc, (0, 1))
            self.assertEqual(captured["flow_findings"], 2)
            flow_prog = [f for f in captured["findings"]
                         if f.get("source") == "kiro-flow" and f["category"] == "program"]
            self.assertEqual(len(flow_prog), 1)
            self.assertTrue(flow_prog[0].get("resolved"))     # kiro-flow が起票済みのまま統合

    def test_flow_disabled_skips_flow_finder(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, with_flow=False)        # 既定 off（直接 Config 構築）
            km.ensure_dirs(cfg)
            called = []
            km.cmd_doctor(cfg, fix=False, kiro_run=lambda p, m: "[]",
                          flow_finder=lambda c, fix: called.append(1) or [])
            self.assertEqual(called, [])               # with_flow=False なら呼ばれない

    def test_collect_flow_findings_parses_subprocess_json(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, with_flow=True)

            class P:
                stdout = ('{"tool":"kiro-flow","findings":'
                          '[{"category":"env","severity":"warn","title":"git 無し",'
                          '"evidence":"e","fix":"f"}]}')

            out = km.collect_flow_findings(cfg, fix=False, runner=lambda cmd: P())
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["source"], "kiro-flow")   # 連携由来でタグ付け
            # 不正 JSON は空で無害にスキップ
            self.assertEqual(km.collect_flow_findings(
                cfg, fix=False, runner=lambda cmd: type("P", (), {"stdout": "boom"})()), [])


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

    def test_needs_file_includes_evidence(self):
        # blocked の needs に「判断材料（所在・差分・検証）」が載り、人がレビューせず判断できる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="test -f never_exists")     # 必ず FAIL
            km.run_loop(cfg_for(d, max_retries=0))
            body = (d / "needs" / "T1.md").read_text(encoding="utf-8")
            self.assertIn("## 判断材料", body)
            self.assertIn("- 成果物:", body)
            self.assertIn("- 所在:", body)
            self.assertIn("- 検証:", body)
            self.assertIn("FAIL", body)

    def test_delivery_evidence_reports_location_and_diff(self):
        # delivery_evidence が所在（ブランチ）・差分・検証を含む
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            subprocess.run(["git", "-C", str(d), "init", "-q", "-b", "feat"], check=True,
                           capture_output=True)
            (d / "a.txt").write_text("x")
            for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
                subprocess.run(["git", "-C", str(d)] + c, check=True, capture_output=True)
            base = km.git_change_baseline(d)
            (d / "b.txt").write_text("y")                    # baseline 以降の変更
            ev = km.delivery_evidence(cfg_for(d, workdir=d),
                                      "https://gitlab.com/g/r/merge_requests/7",
                                      base, location="remote", verify="true", vmsg="ok", ok=True)
            self.assertIn("merge_requests/7", ev)            # 成果物 ref（MR URL）
            self.assertIn("ブランチ feat", ev)               # 所在ブランチ
            self.assertIn("b.txt", ev)                       # 差分
            self.assertIn("→ PASS", ev)                      # 検証

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


class TestActSubmitTerminal(unittest.TestCase):
    """daemon/remote submit 待ちが kiro-flow run の終端 status を正しく解釈する。
    failed を success と取り違えず、orchestrator 異常終了（daemon が failed に確定）でも
    execute フェーズが永久待機せず即座に失敗として返ることを検証する。"""

    def _fake_run(self, result_payload, advance=None):
        """submit は run-id を返し、result --json は result_payload を返す擬似 subprocess.run。"""
        def fake(cmd, *a, **kw):
            if "submit" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="run-XYZ\n", stderr="")
            if "result" in cmd:
                if advance is not None:
                    advance()
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps(result_payload), stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return fake

    def _task(self):
        return km.Task(id="T1", title="x", verify="true")

    def test_failed_run_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            with mock.patch.object(km.subprocess, "run",
                                   self._fake_run({"done": True, "status": "failed"})), \
                 mock.patch.object(km.time, "sleep", lambda *_: None):
                ok, msg = km._act_submit(self._task(), cfg, use_git=False)
            self.assertFalse(ok)              # failed を success と取り違えない
            self.assertIn("failed", msg)

    def test_done_run_reported_as_success(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            with mock.patch.object(km.subprocess, "run",
                                   self._fake_run({"done": True, "status": "done"})), \
                 mock.patch.object(km.time, "sleep", lambda *_: None):
                ok, msg = km._act_submit(self._task(), cfg, use_git=False)
            self.assertTrue(ok)
            self.assertIn("done", msg)

    def test_submit_req_id_deterministic_and_passed_to_submit(self):
        # リブート跨ぎの再接続の前提: 同一試行は同じ req_id（決定的）、リトライ・別プロジェクトは別 id
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            t = self._task()
            rid = km._submit_req_id(t, cfg)
            self.assertEqual(rid, km._submit_req_id(t, cfg))                  # 決定的
            self.assertNotEqual(rid, km._submit_req_id(
                km.Task(id="T1", title="x", verify="true", retries=1), cfg))  # リトライは新 run
            cfg2 = cfg_for(Path(d) / "other", dry_run=False)
            self.assertNotEqual(rid, km._submit_req_id(t, cfg2))              # 別 backlog と衝突しない
            self.assertNotIn("/", rid)                                        # run ディレクトリ名に安全

            seen = []

            def fake(cmd, *a, **kw):
                seen.append(list(cmd))
                if "submit" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout=f"{rid}\n", stderr="")
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps({"done": True, "status": "done"}), stderr="")

            with mock.patch.object(km.subprocess, "run", fake), \
                 mock.patch.object(km.time, "sleep", lambda *_: None):
                ok, _ = km._act_submit(t, cfg, use_git=False)
            self.assertTrue(ok)
            sub_cmd = next(c for c in seen if "submit" in c)
            self.assertIn("--run-id", sub_cmd)                                # 再接続の入口
            self.assertEqual(sub_cmd[sub_cmd.index("--run-id") + 1], rid)

    def test_nonterminal_run_times_out_without_hanging(self):
        # done=False のまま（orchestrator 失踪を daemon が終端化できていない最悪ケース）でも、
        # act_timeout を境に必ず返る（永久待機しない）ことを擬似クロックで確認する。
        clock = [1000.0]
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=10.0)
            fake = self._fake_run({"done": False, "status": "running"})
            with mock.patch.object(km.subprocess, "run", fake), \
                 mock.patch.object(km.time, "time", lambda: clock[0]), \
                 mock.patch.object(km.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s)):
                ok, msg = km._act_submit(self._task(), cfg, use_git=False)
            self.assertFalse(ok)
            self.assertIn("タイムアウト", msg)


class TestActTimeoutZeroAndInherit(unittest.TestCase):
    """act_timeout=0（無制限待ち）と、リトライ時の先行 run 引き継ぎ（--inherit-from）の配線。
    gitlab 等の長時間委譲で待ち切れず retry を空増やしする事故を防ぐための変更。"""

    def _task(self, retries=0):
        return km.Task(id="T1", title="x", verify="true", retries=retries)

    def test_claim_ttl_infinite_when_act_timeout_zero(self):
        with tempfile.TemporaryDirectory() as d:
            cfg0 = cfg_for(Path(d), dry_run=False, act_timeout=0.0)
            self.assertEqual(km._claim_ttl(cfg0), float("inf"))   # 委譲中に claim を奪われない
            cfg30 = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            self.assertTrue(km._claim_ttl(cfg30) < float("inf"))

    def test_act_timeout_zero_waits_until_done(self):
        # act_timeout=0 は無制限。擬似クロックが大きく進んでもタイムアウトせず、done で success。
        clock = [1000.0]
        state = {"polls": 0}

        def fake(cmd, *a, **kw):
            if "submit" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="run-XYZ\n", stderr="")
            if "result" in cmd:
                state["polls"] += 1
                done = state["polls"] >= 5
                payload = {"done": done, "status": "done" if done else "running"}
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=0.0)
            with mock.patch.object(km.subprocess, "run", fake), \
                 mock.patch.object(km.time, "time", lambda: clock[0]), \
                 mock.patch.object(km.time, "sleep",
                                   lambda s: clock.__setitem__(0, clock[0] + 100000)):
                ok, msg = km._act_submit(self._task(), cfg, use_git=False)
            self.assertTrue(ok)                          # 巨大なクロック前進でもタイムアウトしない
            self.assertIn("done", msg)
            self.assertGreaterEqual(state["polls"], 5)

    def test_inherit_from_passed_on_retry_only(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            self.assertIsNone(km._prev_req_id(self._task(0), cfg))          # 初回は先行 run なし
            self.assertEqual(km._prev_req_id(self._task(2), cfg),
                             km._req_id_for(self._task(2), cfg, 1))         # retries-1 世代

            def capture(retries):
                seen = []

                def fake(cmd, *a, **kw):
                    seen.append(list(cmd))
                    if "submit" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, stdout="rid\n", stderr="")
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout=json.dumps({"done": True, "status": "done"}), stderr="")

                with mock.patch.object(km.subprocess, "run", fake), \
                     mock.patch.object(km.time, "sleep", lambda *_: None):
                    km._act_submit(self._task(retries), cfg, use_git=False)
                return next(c for c in seen if "submit" in c)

            self.assertNotIn("--inherit-from", capture(0))                  # 初回は引き継ぎなし
            retry = capture(3)
            self.assertIn("--inherit-from", retry)                         # リトライは引き継ぐ
            self.assertEqual(retry[retry.index("--inherit-from") + 1],
                             km._req_id_for(self._task(3), cfg, 2))


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

    def test_needs_is_madr_format(self):
        # needs/<id>.md は MADR 互換（frontmatter + Decision Outcome 欄）で生成され、
        # そのままフィードバック往復が成立する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "NG")
            nf = d / "needs" / "T1.md"
            text = nf.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            self.assertIn("status: proposed", text)
            self.assertIn("kind: blocked", text)
            self.assertIn("## Context and Problem Statement", text)
            self.assertIn(km.DECISION_MARKER, text)
            _submit_feedback(nf, "この方針で")
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(km.ingest_feedback(cfg, tasks), ["T1"])
            self.assertEqual(dict(tasks[0].extra)["feedback"], "この方針で")

    def test_legacy_feedback_marker_still_ingested(self):
        # 旧形式（## フィードバック）の needs ファイルも引き続き取り込める
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            (d / "needs" / "T1.md").write_text(
                "# 要対応: T1\n\n## フィードバック\n- [x] 確定\n旧形式の方針\n",
                encoding="utf-8")
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(km.ingest_feedback(cfg, tasks), ["T1"])
            self.assertEqual(dict(tasks[0].extra)["feedback"], "旧形式の方針")


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
            mems = list((home / "memory" / "home" / "memories" / "kiro-projects").glob("*.md"))
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
            mem = home / "memory" / "home" / "memories" / "kiro-projects"
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


class TestCommandsIngest(unittest.TestCase):
    """指示のファイル取り込み（commands/*.json）。CLI と同一ロジックへの委譲・
    掃除・不正ファイルの退避・watch の起床を検証する。"""

    def test_ingest_commands_runs_cli_logic(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            mkb(d, "T2", verify="true")
            c = cfg_for(d, actor="bob")
            km.ensure_dirs(c)
            cd = km.commands_dir(c)
            (cd / "a.json").write_text(json.dumps(
                {"command": "approve", "id": "T1", "reason": "直した"}), encoding="utf-8")
            (cd / "b.json").write_text(json.dumps(
                {"command": "hold", "id": "T2", "reason": "本番は手動"}), encoding="utf-8")
            (cd / "c.json").write_text(json.dumps(
                {"command": "pin", "id": "T1"}), encoding="utf-8")
            done = km.ingest_commands(c)
            self.assertEqual(sorted(done), ["approve:T1", "hold:T2", "pin:T1"])
            self.assertEqual(list(cd.glob("*.json")), [])            # 処理したら消す
            t1 = next(t for t in km.load_tasks(d / "backlog") if t.id == "T1")
            self.assertEqual(t1.status, "ready")                     # CLI approve と同じ効果
            self.assertIn("deny: T2", (d / "policy.md").read_text())
            self.assertIn("pin: T1", (d / "policy.md").read_text())
            self.assertIn("DR-", (d / "decisions" / "T1.md").read_text())  # 決定記録も同一

    def test_ingest_commands_rejects_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            cd = km.commands_dir(c)
            (cd / "broken.json").write_text("{oops", encoding="utf-8")
            (cd / "unknown.json").write_text(json.dumps(
                {"command": "explode", "id": "T1"}), encoding="utf-8")
            (cd / "missing.json").write_text(json.dumps(
                {"command": "approve", "id": "NOPE"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), [])
            self.assertEqual(list(cd.glob("*.json")), [])            # 再試行ループにしない
            self.assertEqual(len(list(cd.glob("*.json.err"))), 3)    # .err に退避
            self.assertIn("commands 取り込み失敗", (d / "journal.md").read_text())

    def test_has_work_wakes_on_commands(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")            # consumable 無し
            c = cfg_for(d)
            km.ensure_dirs(c)
            self.assertFalse(km.has_work(c))
            (km.commands_dir(c) / "a.json").write_text(json.dumps(
                {"command": "approve", "id": "T1"}), encoding="utf-8")
            self.assertTrue(km.has_work(c))                          # 指示ドロップで起きる

    def test_watch_debounce_defers_fresh_command(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d, watch=True, debounce=999.0)
            km.ensure_dirs(c)
            f = km.commands_dir(c) / "a.json"
            f.write_text(json.dumps({"command": "approve", "id": "T1"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), [])              # 静穏化待ちで保留
            self.assertTrue(f.exists())


class TestStatusHeartbeat(unittest.TestCase):
    """リモート kiro-projects-viewer 向けの生存信号（status.json）。idle 中は既定で
    state_git への追加コミットを一切生まないこと（--status-interval は opt-in）を検証する。"""

    def test_write_status_content(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d, watch=True, level="assisted", state_git_interval=300.0)
            km.write_status(c)
            rec = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertTrue(rec["watch"])
            self.assertEqual(rec["level"], "assisted")
            self.assertIn("updated_iso", rec)
            self.assertEqual(rec["fresh_after_sec"], 600.0)          # 2 * state_git_interval

    def test_fresh_after_sec_floor_and_max(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # 両方 0（未設定）でもフロア 120 秒を下回らない
            c0 = cfg_for(d, state_git_interval=0.0, status_interval=0.0)
            self.assertEqual(km._status_fresh_after_sec(c0), 120.0)
            # 大きい方（status_interval）が勝つ
            c1 = cfg_for(d, state_git_interval=300.0, status_interval=1000.0)
            self.assertEqual(km._status_fresh_after_sec(c1), 2000.0)

    def test_maybe_heartbeat_disabled_by_default_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d, status_interval=0.0)                       # 既定 0 = 無効
            km.maybe_heartbeat_status(c)
            self.assertFalse((d / "status.json").exists())            # idle 中の追加コミット元を作らない

    def test_maybe_heartbeat_enabled_throttles_to_interval(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d, status_interval=100.0)
            km.maybe_heartbeat_status(c)                              # 未作成 → 書く
            self.assertTrue((d / "status.json").exists())
            first_mtime = (d / "status.json").stat().st_mtime
            km.maybe_heartbeat_status(c)                              # 直後の再呼び出しは間隔未満 → 書かない
            self.assertEqual((d / "status.json").stat().st_mtime, first_mtime)
            # 間隔を過ぎたことにする（mtime を過去へ）
            old = time.time() - 101.0
            os.utime(d / "status.json", (old, old))
            km.maybe_heartbeat_status(c)
            self.assertGreater((d / "status.json").stat().st_mtime, old)

    def test_run_loop_piggybacks_status_write(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False, level="assisted")
            km.ensure_dirs(c)
            km.run_loop(c, act=lambda t, cfg, loc: (True, "ok"))
            rec = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(rec["level"], "assisted")
            self.assertTrue(rec["watch"] is False)                    # cfg_for 既定は watch=False

    def test_throttle_demotion_refreshes_status(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False,
                       watch=True, max_tokens=100, throttle=0.5)

            def act(t, cfg, loc):
                t.extra.append(("_cost_marker", "1"))
                return (True, "ok @cost tokens=80")

            km.run_watch(c, act=act, sleeper=lambda s: None, max_passes=1)
            rec = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(rec["level"], "report")                  # 降格後の値で上書きされている


class TestRevise(unittest.TestCase):
    """人の即時フィードバック（revise）。内容・依存 after の修正と feedback 注入、
    実行中タスクの積み直し予約（revised マーカー）、CLI/commands ドロップの同一実装を検証する。"""

    def test_revise_updates_fields_deps_and_feedback(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            mkb(d, "T2", verify="true")
            c = cfg_for(d, actor="alice")
            km.ensure_dirs(c)
            rc = km.cmd_revise(c, "T2", {"title": "実サーバで e2e", "priority": 5, "after": "T1"},
                               "ローカルサーバでなく実サーバに配備して e2e を実施すること", "軌道修正")
            self.assertEqual(rc, 0)
            t2 = next(t for t in km.load_tasks(d / "backlog") if t.id == "T2")
            self.assertEqual(t2.title, "実サーバで e2e")
            self.assertEqual(t2.priority, 5)
            self.assertEqual(km.task_deps(t2), ["T1"])
            self.assertIn("実サーバに配備", t2.feedback())
            self.assertEqual(t2.get("rev"), "1")                     # act 試行の世代番号
            self.assertEqual(t2.status, "ready")                     # 状態は変えない
            drs = (d / "decisions" / "T2.md").read_text(encoding="utf-8")
            self.assertIn("action  : revise", drs)                   # 決定記録
            self.assertIn("- learn:", drs)                           # feedback は学習材料にも
            # 依存が効く: T2 は T1 が残る間は選ばれない
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual([t.id for t in km.ready_after_deps(tasks)], ["T1"])

    def test_revise_validates_input(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            self.assertEqual(km.cmd_revise(c, "NOPE", {"title": "x"}, "", ""), 2)   # 不在
            self.assertEqual(km.cmd_revise(c, "T1", {}, "", ""), 2)                 # 変更なし
            self.assertEqual(km.cmd_revise(c, "T1", {"level": "bogus"}, "", ""), 2)  # level 不正
            self.assertEqual(km.cmd_revise(c, "T1", {"after": "T1"}, "", ""), 2)     # 自己依存
            # 循環（T1 after T2, T2 after T1）は拒否し、ファイルは変えない
            mkb(d, "T2", verify="true")
            self.assertEqual(km.cmd_revise(c, "T2", {"after": "T1"}, "", ""), 0)
            self.assertEqual(km.cmd_revise(c, "T1", {"after": "T2"}, "", ""), 2)
            t1 = next(t for t in km.load_tasks(d / "backlog") if t.id == "T1")
            self.assertEqual(km.task_deps(t1), [])

    def test_revise_clears_fields(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            mkb(d, "T2", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            km.cmd_revise(c, "T2", {"after": "T1", "note": "旧メモ"}, "", "")
            km.cmd_revise(c, "T2", {"after": "none", "note": ""}, "", "")
            t2 = next(t for t in km.load_tasks(d / "backlog") if t.id == "T2")
            self.assertEqual(km.task_deps(t2), [])
            self.assertIsNone(t2.get("note"))

    def test_revise_blocked_requeues_and_clears_needs(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            t = km.load_tasks(d / "backlog")[0]
            km.write_needs_file(c, t, "検証 NG")
            rc = km.cmd_revise(c, "T1", {"verify": "test -f ok.txt"}, "ok.txt を作る方式にする", "")
            self.assertEqual(rc, 0)
            t1 = km.load_tasks(d / "backlog")[0]
            self.assertEqual(t1.status, "ready")                     # 積み直し（needs 記入と同じ復帰）
            self.assertEqual(t1.verify, "test -f ok.txt")
            self.assertFalse((d / "needs" / "T1.md").exists())

    def test_ingest_commands_revise(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            (km.commands_dir(c) / "r.json").write_text(json.dumps(
                {"command": "revise", "id": "T1", "priority": 9,
                 "feedback": "実サーバで e2e", "reason": "軌道修正"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), ["revise:T1"])
            t1 = km.load_tasks(d / "backlog")[0]
            self.assertEqual(t1.priority, 9)
            self.assertIn("実サーバ", t1.feedback())

    def test_claim_adopts_disk_edits(self):
        # パス途中の CLI revise / 直接編集が、doing 永続化で上書き消失しないこと
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            stale = km.load_tasks(d / "backlog")[0]                  # パス開始時点の in-memory 相当
            km.cmd_revise(c, "T1", {"priority": 7}, "最新の指示", "")  # その後の人の修正
            self.assertTrue(km.claim_task(c, stale))
            self.assertEqual(stale.priority, 7)                      # ディスク内容を採用
            self.assertIn("最新の指示", stale.feedback())

    def test_submit_req_id_changes_with_rev(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d)
            t = km.Task(id="T1", title="x")
            base = km._submit_req_id(t, c)
            t.set("rev", "1")
            self.assertNotEqual(base, km._submit_req_id(t, c))       # 世代が上がれば新しい run
            self.assertTrue(km._submit_req_id(t, c).endswith("-v1"))

    def test_revise_during_act_requeues_without_settling(self):
        # 実行中の revise: 現在の試行は verify=PASS 相当でも確定せず、修正内容で再実行される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False, max_cycles=10)
            km.ensure_dirs(c)
            seen = []

            def act(t, cfg, loc):
                seen.append(t.feedback())
                if len(seen) == 1:      # 人が act 中に気づいて revise した想定（別プロセス相当）
                    rc = km.cmd_revise(cfg, "T1", {"title": "実サーバ e2e"},
                                       "ローカルサーバでなく実サーバに配備して実施", "軌道修正")
                    assert rc == 0
                return (True, "ok")

            res = km.run_loop(c, act=act)
            self.assertEqual(res["reason"], km.REASON_DRAINED)
            self.assertEqual(len(seen), 2)                           # 積み直し → 再実行
            self.assertIsNone(seen[0])
            self.assertIn("実サーバに配備", seen[1])                  # 修正が次 act に届いた
            self.assertIn("revise により積み直し", (d / "journal.md").read_text(encoding="utf-8"))
            self.assertEqual(list((d / "backlog").glob("*.md")), []) # 2回目で done

    def test_midpass_command_applies_before_next_task(self):
        # パス途中の commands/ ドロップが、後続タスクの実行前に取り込まれること
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            (d / "backlog" / "T2.md").write_text(
                "## T2: 後続\n- status: ready\n- verify: `true`\n- priority: -1\n",
                encoding="utf-8")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False, max_cycles=10)
            km.ensure_dirs(c)
            seen = {}

            def act(t, cfg, loc):
                if t.id == "T1":        # T1 実行中に人が T2 へ指示を落とした想定
                    (km.commands_dir(cfg) / "r.json").write_text(json.dumps(
                        {"command": "revise", "id": "T2",
                         "feedback": "実サーバで e2e"}), encoding="utf-8")
                seen[t.id] = t.feedback()
                return (True, "ok")

            res = km.run_loop(c, act=act)
            self.assertEqual(res["reason"], km.REASON_DRAINED)
            self.assertIn("実サーバ", seen["T2"] or "")               # 次サイクル開始時に反映済み

    def test_recover_revised_requeues_orphan(self):
        # 実行者不在（stale claim）の revised マーカーは自己回復で ready に戻す
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="doing", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            tasks = km.load_tasks(d / "backlog")
            tasks[0].set("revised", "2026-01-01 00:00:00")
            km.persist_task(c, tasks[0])
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(km.recover_revised(c, tasks), ["T1"])
            t1 = km.load_tasks(d / "backlog")[0]
            self.assertEqual(t1.status, "ready")
            self.assertIsNone(t1.get("revised"))


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
            proj = d / ".ka" / "projects" / "default"
            self._seed_avoid(proj, "OLD", "deploy to production", "本番は手動")
            rc = km.main(["enqueue", "--title", "deploy to production tonight", "--verify", "true",
                          "--workdir", str(d), "--root", str(d / ".ka")])
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
            # 新レイアウト: per-project root は <root>/projects/default/
            proot = d / ".kiro-projects" / "projects" / "default"
            bl = proot / "backlog"
            bl.mkdir(parents=True)
            (bl / "T1.md").write_text(
                "## T1: x\n- status: ready\n- verify: `true`\n- retries: 0\n", encoding="utf-8")
            rc = km.main(["run", "--workdir", str(d), "--planner", "none",
                          "--flow-planner", "stub", "--executor", "stub", "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertTrue((proot / "journal.md").exists())
            self.assertTrue((proot / "archive" / "T1.md").exists())   # done → project/archive
            self.assertFalse((bl / "T1.md").exists())
            # プロジェクト root 以外に散らばっていない
            self.assertFalse((d / "backlog").exists())
            self.assertFalse((d / ".kiro-projects" / "backlog").exists())
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

    def test_state_git_keeps_bus(self):
        # state_git でバスをリモート viewer へ鏡写ししている構成では、local run 後も runs/ を
        # 消さない（消すとフロータブに見せたい run 状態を破壊し、削除がリモートへ伝播する）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, state_git="git@example.com:team/kiro-state.git")
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

    def test_lock_path_canonical_across_symlink(self):
        # symlink 経由で起動した外部 daemon でも、同じ実バスなら同じロックパスになる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            real = d / "real_bus"
            real.mkdir()
            link = d / "link_bus"
            try:
                link.symlink_to(real)
            except (OSError, NotImplementedError):
                self.skipTest("symlink 不可")
            p_real = km.daemon_lock_path(cfg_for(d, bus=real), False)
            p_link = km.daemon_lock_path(cfg_for(d, bus=link), False)
            self.assertEqual(p_real, p_link)

    def test_lock_dir_config_override(self):
        # 設定 lock_dir を起動側・プローブ側で共有すれば TMPDIR 差を吸収できる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            p = km.daemon_lock_path(cfg_for(d, lock_dir=str(d / "locks")), False)
            self.assertEqual(p.parent, d / "locks")

    def test_shared_bus_kept_across_projects(self):
        # 共有バス（明示設定）なら --project all でも全プロジェクトが同じバス＝同じ daemon ロックを使う
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            shared = d / "shared-bus"
            cfg = cfg_for(d, bus=shared, shared_bus=True)
            a = km.project_cfg(cfg, "projectA")
            b = km.project_cfg(cfg, "projectB")
            self.assertEqual(a.bus, shared)
            self.assertEqual(b.bus, shared)
            # 同じバス → 同じ daemon ロックパス（単一 daemon を全プロジェクトから検知できる）
            self.assertEqual(km.daemon_lock_path(a, False), km.daemon_lock_path(b, False))

    def test_per_project_bus_when_not_shared(self):
        # 共有バス未設定なら従来どおりプロジェクト毎の bus（分離）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, shared_bus=False)
            a = km.project_cfg(cfg, "projectA")
            b = km.project_cfg(cfg, "projectB")
            self.assertNotEqual(a.bus, b.bus)
            self.assertEqual(a.bus.name, "bus")

    def test_pid_liveness_fallback_when_flock_unavailable(self):
        # fcntl 無し（Windows 等）でも、daemon が記録した pid の生存で発見できる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            lp = km.daemon_lock_path(cfg, False)
            lp.parent.mkdir(parents=True, exist_ok=True)
            with mock.patch.object(km, "fcntl", None):
                lp.write_text(str(os.getpid()))          # 自分（生存）= daemon 稼働とみなす
                self.assertTrue(km.daemon_running(cfg))
                lp.write_text("999999999")               # 不在 pid = daemon 無し
                self.assertFalse(km.daemon_running(cfg))
                lp.write_text("")                        # pid 不明 = daemon 無し
                self.assertFalse(km.daemon_running(cfg))
            self.addCleanup(lambda: lp.exists() and lp.unlink())


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

    def _route_project(self, argv):
        captured = {}
        orig = km.cmd_run
        km.cmd_run = lambda cfg: (captured.update(project=cfg.project_name), 0)[1]
        try:
            km.main(argv)
        finally:
            km.cmd_run = orig
        return captured.get("project")

    def test_bare_defaults_to_all_project(self):
        # サブコマンド省略は全プロジェクト（--project all）を既定にする
        self.assertEqual(self._route_project([]), "all")
        self.assertEqual(self._route_project(["--poll", "10"]), "all")

    def test_explicit_run_stays_single_default(self):
        # 明示 run は単一 default のまま（all にしない）
        self.assertEqual(self._route_project(["run"]), "default")
        # 省略でも明示 --project があればそちらが勝つ
        self.assertEqual(self._route_project(["--project", "web"]), "web")


class TestInstances(unittest.TestCase):
    """稼働インスタンスのレジストリ（外部操作者がフォルダを発見する口）。"""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._prev = os.environ.get("KIRO_PROJECTS_HOME")
        os.environ["KIRO_PROJECTS_HOME"] = self._home

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("KIRO_PROJECTS_HOME", None)
        else:
            os.environ["KIRO_PROJECTS_HOME"] = self._prev

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

    def test_run_prunes_dead_garbage_on_invocation(self):
        # 前回の異常終了で残った自ホストの死レコードは、run 起動時（register 前）に掃除される
        d = km.instances_dir()
        d.mkdir(parents=True, exist_ok=True)
        garbage = d / f"{km.socket.gethostname()}-999999999-projectA.json"
        garbage.write_text(
            '{"pid": 999999999, "host": "%s", "root": "/x/projects/projectA",'
            ' "project": "projectA", "watch": true}' % km.socket.gethostname(),
            encoding="utf-8")
        with tempfile.TemporaryDirectory() as wd:
            wd = Path(wd)
            mkb(wd, "T1", title="x", verify="true")
            km.main(["run", "--workdir", str(wd), "--root", str(wd / ".ka"),
                     "--planner", "none", "--flow-planner", "stub",
                     "--executor", "stub", "--dry-run"])
        self.assertFalse(garbage.exists())             # 起動時に掃除済み

    def test_all_sentinel_is_marked(self):
        # all-daemon の「all」センチネルは実フォルダ監視と区別する目印（sentinel=True）を持つ
        with tempfile.TemporaryDirectory() as d:
            proot = Path(d) / ".ka" / "projects" / "all"
            rec = km.instance_record(cfg_for(proot, project_name="all"))
            self.assertTrue(rec["sentinel"])
            # 実プロジェクトのレコードはセンチネルではない
            rec2 = km.instance_record(cfg_for(Path(d) / ".ka" / "projects" / "projectA",
                                              project_name="projectA"))
            self.assertFalse(rec2["sentinel"])


class TestRemoteDiscovery(unittest.TestCase):
    """共有レジストリ越しの別ホスト発見（§11-7）。core はファイル操作のみ・ネットワーク非依存を保つ。"""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._shared = tempfile.mkdtemp()
        self._prev = os.environ.get("KIRO_PROJECTS_HOME")
        self._prev_reg = os.environ.get("KIRO_PROJECTS_REGISTRY")
        os.environ["KIRO_PROJECTS_HOME"] = self._home
        os.environ.pop("KIRO_PROJECTS_REGISTRY", None)

    def tearDown(self):
        for k, v in (("KIRO_PROJECTS_HOME", self._prev),
                     ("KIRO_PROJECTS_REGISTRY", self._prev_reg)):
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
            cfg = cfg_for(Path(wd), watch=True, project_name="default")
            paths = km.register_instance(cfg, [self._shared])
            self.addCleanup(lambda: [p.unlink() for p in paths if p.exists()])
            # ローカル home と共有先の両方へ「ホスト-PID-プロジェクト」修飾名で書かれる
            self.assertEqual(len(paths), 2)
            self.assertTrue(any(Path(self._shared) in p.parents for p in paths))
            self.assertTrue(all(p.name == f"{socket.gethostname()}-{os.getpid()}-default.json"
                                for p in paths))
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
        os.environ["KIRO_PROJECTS_REGISTRY"] = self._shared
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
        self._prev = os.environ.get("KIRO_PROJECTS_HOME")
        os.environ["KIRO_PROJECTS_HOME"] = self._home

    def tearDown(self):
        km.cmd_stop(want_all=True)            # 取りこぼした daemon を確実に止める
        if self._prev is None:
            os.environ.pop("KIRO_PROJECTS_HOME", None)
        else:
            os.environ["KIRO_PROJECTS_HOME"] = self._prev

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
        root = "/tmp/wrk/.kiro-projects"
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
        self._write_rec(child.pid, "/tmp/x/.kiro-projects")
        rc = km.cmd_stop(pid=child.pid, timeout=5.0)
        self.assertEqual(rc, 0)
        self.assertFalse(km._pid_alive(child.pid))
        self.assertFalse((km.instances_dir() / f"{child.pid}.json").exists())

    def test_stop_without_target_returns_1(self):
        self.assertEqual(km.cmd_stop(root="/nothing/here"), 1)

    def test_start_registers_then_stop(self):
        work = Path(tempfile.mkdtemp())
        (work / "kiro-projects.json").write_text(
            '{"executor":"stub","planner":"none","flow_planner":"stub","poll":0.3}', encoding="utf-8")
        cfg = str(work / "kiro-projects.json")
        rc = km.cmd_start(root=str(work), config=cfg)
        self.assertEqual(rc, 0)
        # 登録の出現を待つ（最大 ~5s）。記録 root は per-project（projects/default）
        root = str((work / "projects" / "default").resolve())
        for _ in range(50):
            if km.select_instances(root=root):
                break
            time.sleep(0.1)
        self.assertTrue(km.select_instances(root=root))         # 起動して登録された
        self.assertEqual(km.cmd_start(root=str(work), config=cfg), 1)  # 重複起動は拒否
        self.assertEqual(km.cmd_stop(root=str(work), project="default"), 0)
        self.assertEqual(km.select_instances(root=root), [])    # 停止で消える

    def test_start_defaults_to_all_daemon(self):
        # daemon（start）は --project 未指定なら all で起動し、"all" センチネルを登録する
        work = Path(tempfile.mkdtemp())
        (work / "kiro-projects.json").write_text(
            '{"executor":"stub","planner":"none","flow_planner":"stub","poll":0.3}', encoding="utf-8")
        cfgp = str(work / "kiro-projects.json")
        self.assertEqual(km.cmd_start(root=str(work), config=cfgp), 0)   # --project なし → all
        all_root = str((work / "projects" / "all").resolve())
        for _ in range(50):
            if km.select_instances(root=all_root):
                break
            time.sleep(0.1)
        self.assertTrue(km.select_instances(root=all_root))             # all センチネルが登録された
        self.assertEqual(km.cmd_start(root=str(work), config=cfgp), 1)  # 重複起動は拒否
        self.assertEqual(km.cmd_stop(root=str(work), project="all"), 0)  # all daemon を停止
        self.assertEqual(km.select_instances(root=all_root), [])

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
            p = Path(d) / "kiro-projects.json"
            p.write_text('{"executor":"stub","planner":"none","poll":9,"max_cycles":3}',
                         encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual((ns.executor, ns.planner, ns.poll, ns.max_cycles),
                             ("stub", "none", 9, 3))

    def test_cli_overrides_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-projects.json"
            p.write_text('{"executor":"stub","planner":"none"}', encoding="utf-8")
            ns = self._resolve(str(p), executor="kiro")   # CLI 明示は維持される
            self.assertEqual(ns.executor, "kiro")          # CLI 勝ち
            self.assertEqual(ns.planner, "none")           # config 採用

    def test_bus_config_is_honored_and_shared(self):
        # 設定ファイルの bus: が読まれ、共有バス（絶対パス 1 本）として使われること。
        # これが読まれないと per-project バスに落ち、kiro-flow daemon 非検知・state_git で
        # バスが鏡写しされない（status.json は上がるが runs が上がらない）原因になる。
        with tempfile.TemporaryDirectory() as d:
            shared = str(Path(d) / "shared-bus")
            p = Path(d) / "kiro-projects.json"
            p.write_text(json.dumps({"bus": shared}), encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual(ns.bus, shared)               # config の bus が args に載る
            cfg = km.build_config(ns)
            self.assertEqual(str(cfg.bus), shared)          # 実際に使うバス = 共有バス
            self.assertTrue(cfg.shared_bus)                 # --project all でも per-project へ落とさない

    def test_bus_absent_stays_per_project(self):
        # bus 未指定は従来どおり per-project（後方互換）。
        ns = self._resolve(None)
        self.assertIsNone(ns.bus)
        cfg = km.build_config(ns)
        self.assertFalse(cfg.shared_bus)

    def test_builtin_defaults_when_no_config(self):
        ns = self._resolve(None)
        self.assertEqual((ns.executor, ns.planner, ns.poll, ns.max_cycles, ns.location),
                         ("kiro", "kiro", 5.0, 20, "auto"))
        self.assertEqual((ns.auto_adjudicate, ns.adjudicate_max), (True, 1))  # 既定 on

    def test_yaml_config_when_pyyaml_available(self):
        if km.yaml is None:
            self.skipTest("PyYAML 未導入（JSON 経路は別テストで担保）")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-projects.yaml"
            p.write_text("executor: stub\nmax_retries: 5\ngit_branch: develop\n", encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual((ns.executor, ns.max_retries, ns.git_branch),
                             ("stub", 5, "develop"))

    def test_missing_explicit_config_exits(self):
        with self.assertRaises(SystemExit):
            self._resolve("/no/such/kiro-projects.yaml")

    def test_boolean_flags_from_config(self):
        # 真偽フラグ（watch/do_archive/learn/rot/cleanup/once/dry_run/ltm/regression_revert）が
        # 設定ファイルで効く。resolve_config は CLI 未指定（None）のみ config→既定 で埋める。
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-projects.json"
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
            p = Path(d) / "kiro-projects.json"
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


CHARTER = """# Charter: demo

## goal
CSV を要約する CLI を完成させる。

## constraints
- 標準ライブラリのみ

## assumptions
- 入力は UTF-8

## deliverables
- report.py

## acceptance
- `test -f {flag}`
"""


def write_charter(d: Path, body: str) -> None:
    (d / "charter.md").write_text(body, encoding="utf-8")


class TestProjectLayer(unittest.TestCase):
    def test_parse_charter(self):
        ch = km.parse_charter(CHARTER.replace("{flag}", "x"))
        self.assertEqual(ch.name, "demo")
        self.assertIn("CSV", ch.goal)
        self.assertEqual(ch.constraints, ["標準ライブラリのみ"])
        self.assertEqual(ch.deliverables, ["report.py"])
        self.assertEqual(ch.acceptance, ["test -f x"])

    def test_parse_charter_repos(self):
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app = https://git/app.git\n- https://git/lib.git\n")
        self.assertEqual(ch.repos, ["app = https://git/app.git", "https://git/lib.git"])
        rmap = km.charter_repo_map(ch)
        self.assertEqual(rmap["app"], "https://git/app.git")     # name 引き
        self.assertEqual(rmap["lib"], "https://git/lib.git")     # URL 末尾を name に
        self.assertEqual(rmap["https://git/app.git"], "https://git/app.git")  # URL 引き

    def test_parse_charter_repos_structured(self):
        # 構造化 repos: name=url ＋ desc/base/target（target 省略時は base）
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app = https://git/app.git\n"
            "  - desc: アプリ本体（API/UI）\n"
            "  - base: main\n"
            "  - target: develop\n"
            "- lib = https://git/lib.git\n"
            "  - 説明: 共有ライブラリ\n"
            "  - ベース: release\n")
        self.assertEqual(ch.repos, ["app = https://git/app.git", "lib = https://git/lib.git"])
        a, b = ch.repo_specs
        self.assertEqual((a["name"], a["url"], a["desc"], a["base"], a["target"]),
                         ("app", "https://git/app.git", "アプリ本体（API/UI）", "main", "develop"))
        # 日本語キー・target 省略（既定 base）
        self.assertEqual((b["name"], b["desc"], b["base"], b["target"]),
                         ("lib", "共有ライブラリ", "release", "release"))
        # charter_repo_map は従来どおり name/url 解決できる
        self.assertEqual(km.charter_repo_map(ch)["app"], "https://git/app.git")

    def test_validate_charter_requires_desc_and_base(self):
        ok = km.parse_charter("# Charter: r\n## goal\nx\n## repos\n"
                              "- app = u\n  - desc: d\n  - base: main\n")
        self.assertEqual(km.validate_charter(ok), [])
        bad = km.parse_charter("# Charter: r\n## goal\nx\n## repos\n- app = u\n")
        probs = km.validate_charter(bad)
        self.assertEqual(len(probs), 2)                  # desc と base の両方
        self.assertTrue(any("desc" in p or "説明" in p for p in probs))
        self.assertTrue(any("base" in p for p in probs))

    def test_charter_definition_renders_base_target_desc(self):
        ch = km.parse_charter("# Charter: r\n## goal\nやる\n## repos\n"
                              "- app = https://git/app.git\n  - desc: 本体\n  - base: main\n  - target: develop\n"
                              "## links\n- https://wiki/x — 仕様\n  - desc: 仕様メモ\n")
        d = km._charter_definition(ch)
        self.assertIn("base=main", d)
        self.assertIn("target=develop", d)
        self.assertIn("本体", d)
        self.assertIn("仕様メモ", d)

    def test_parse_charter_repos_path(self):
        # path 属性（モノレポ作業フォルダ）。日本語別名・先頭/末尾スラッシュ除去も確認
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/api/\n  - 説明: API\n  - base: main\n"
            "- web = https://git/shop.git\n  - フォルダ: /apps/web\n  - 役割: 画面\n  - base: main\n")
        a, b = ch.repo_specs
        self.assertEqual((a["path"], a["desc"]), ("apps/api", "API"))
        self.assertEqual((b["path"], b["desc"]), ("apps/web", "画面"))   # 役割=desc 別名

    def test_validate_charter_monorepo_requires_distinct_path(self):
        # 同一 URL を役割分割するなら distinct な path で区別できる
        ok = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/api\n  - desc: API\n  - base: main\n"
            "- web = https://git/shop.git\n  - path: apps/web\n  - desc: 画面\n  - base: main\n")
        self.assertEqual(km.validate_charter(ok), [])
        # path も branch も全て一致 → 曖昧な重複として弾く
        dupall = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- api = https://git/shop.git\n  - desc: API\n  - base: main\n"
            "- web = https://git/shop.git\n  - desc: 画面\n  - base: main\n")
        self.assertTrue(any("重複" in p for p in km.validate_charter(dupall)))
        # path 重複（同一フォルダ・同一ブランチ）→ 問題
        dup = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/x\n  - desc: API\n  - base: main\n"
            "- web = https://git/shop.git\n  - path: apps/x\n  - desc: 画面\n  - base: main\n")
        self.assertTrue(any("重複" in p for p in km.validate_charter(dup)))
        # 単独エントリは path 任意（後方互換）
        single = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n- app = u\n  - desc: d\n  - base: main\n")
        self.assertEqual(km.validate_charter(single), [])

    def test_validate_charter_distinguishes_same_url_by_branch(self):
        # 同一 URL・path 無しでも base（ブランチ）が違えば別エントリとして成立する
        bybase = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app-main = https://git/app.git\n  - desc: 本流\n  - base: main\n"
            "- app-rel = https://git/app.git\n  - desc: backport\n  - base: release/1.x\n")
        self.assertEqual(km.validate_charter(bybase), [])
        # 同一 URL・同一 path でも target（PR 先ブランチ）が違えば成立する
        bytarget = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- a = https://git/app.git\n  - path: svc\n  - desc: develop 向け\n"
            "  - base: main\n  - target: develop\n"
            "- b = https://git/app.git\n  - path: svc\n  - desc: main 向け\n  - base: main\n")
        self.assertEqual(km.validate_charter(bytarget), [])

    def test_charter_definition_renders_path(self):
        ch = km.parse_charter(
            "# Charter: r\n## goal\nやる\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/api\n  - desc: API\n  - base: main\n")
        d = km._charter_definition(ch)
        self.assertIn("path=apps/api", d)
        self.assertIn("API", d)

    def test_build_charter_request_lists_path_and_role(self):
        # プランナー提示にフォルダ(path)と役割(desc)が載る
        ch = km.parse_charter(
            "# Charter: r\n## goal\nやる\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/api\n  - desc: APIロジック\n  - base: main\n")
        req = km.build_charter_request(ch)
        self.assertIn("apps/api", req)
        self.assertIn("APIロジック", req)
        self.assertIn("api = https://git/shop.git", req)

    def test_parse_charter_repos_owns_marks_reference(self):
        # owns: があれば書込先候補（readonly False）。owns 未指定は参照リポジトリ（readonly True）。
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- a = u1\n  - owns: apps/api/**\n  - desc: d\n  - base: main\n"
            "- b = u2\n  - desc: d\n  - base: main\n"
            "- c = u3\n  - readonly: true\n  - owns: x/**\n  - desc: d\n  - base: main\n")
        a, b, c = ch.repo_specs
        self.assertEqual(a["owns"], ["apps/api/**"])
        self.assertFalse(a["readonly"])     # owns 有り → 書込先候補
        self.assertEqual(b["owns"], [])
        self.assertTrue(b["readonly"])      # owns 未指定 → 参照リポジトリ
        self.assertTrue(c["readonly"])      # readonly 明示は owns 有りでも参照

    def test_resolve_workspace_explicit_and_owns_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: apps/api/**\n  - path: apps/api\n"
                          "  - base: main\n  - target: develop\n  - desc: API\n"
                          "- lib = https://git/lib.git\n  - owns: packages/**\n  - base: main\n"
                          "- docs = https://git/docs.git\n  - desc: 参照元\n  - base: main\n")
            cfg = cfg_for(d, route_planner="none")
            pol = km.Policy()
            # 1. 明示 - workspace:
            t = km.Task(id="T1", title="x", extra=[("workspace", "lib")])
            spec, by = km.resolve_workspace(cfg, t, pol)
            self.assertEqual((spec["name"], by), ("lib", "explicit"))
            # 2. route: ルール（パターンはタイトル/ID の部分一致）
            pol2 = km.Policy(route=["API -> app"])
            spec, by = km.resolve_workspace(cfg, km.Task(id="T2", title="API 改修"), pol2)
            self.assertEqual((spec["name"], by), ("app", "rule"))
            # 3. owns: パス推定（- paths: ヒント）
            t3 = km.Task(id="T3", title="z", extra=[("paths", "packages/util.py")])
            spec, by = km.resolve_workspace(cfg, t3, pol)
            self.assertEqual((spec["name"], by), ("lib", "owns"))
            # 4. 既定ワークスペース（決まらないとき）
            cfg2 = cfg_for(d, route_planner="none", default_workspace="app")
            spec, by = km.resolve_workspace(cfg2, km.Task(id="T4", title="謎"), km.Policy())
            self.assertEqual((spec["name"], by), ("app", "default"))
            # docs は owns 無し → 参照リポジトリ（書込先候補にならない）
            docs = km.charter_repo_spec_map(km.load_charter(cfg))["docs"]
            self.assertTrue(km._is_reference_repo(docs))

    def test_resolve_workspace_persists_decision(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: **\n  - base: main\n")
            cfg = cfg_for(d, route_planner="none")
            (cfg.backlog).mkdir(parents=True, exist_ok=True)
            t = km.Task(id="T1", title="x", verify="true")
            km.persist_task(cfg, t)
            km.resolve_and_persist_workspace(cfg, t, km.Policy())
            reloaded = km.parse_task((cfg.backlog / "T1.md").read_text(), "T1")
            self.assertEqual(reloaded.get("workspace"), "app")   # 決定を md へ書き戻す
            self.assertEqual(reloaded.get("routed_by"), "sole")

    def test_workspace_token_json(self):
        # url/path/base/target/desc を JSON で構造化（readonly/name は載せない）
        tok = km._workspace_token({"name": "api", "url": "https://git/shop.git", "desc": "API",
                                   "base": "main", "target": "develop", "path": "apps/api"})
        obj = json.loads(tok)
        self.assertEqual((obj["url"], obj["path"], obj["base"], obj["target"]),
                         ("https://git/shop.git", "apps/api", "main", "develop"))
        self.assertNotIn("name", obj)
        self.assertNotIn("readonly", obj)

    def test_workspace_propagated_to_kiro_flow(self):
        # 解決済み - workspace: が --workspace の JSON トークンとして kiro-flow へ伝搬する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- api = https://git/shop.git\n  - owns: apps/api/**\n  - path: apps/api\n"
                          "  - base: main\n  - target: develop\n")
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="x", verify="true", extra=[("workspace", "api")])
            cmd = km.build_kiro_flow_cmd(t, cfg)
            self.assertNotIn("--repo", cmd)
            obj = json.loads(cmd[cmd.index("--workspace") + 1])
            self.assertEqual((obj["path"], obj["base"], obj["target"]), ("apps/api", "main", "develop"))

    def test_charter_renders_readonly(self):
        ch = km.parse_charter("# Charter: r\n## goal\nやる\n## repos\n"
                              "- lib = https://git/lib.git\n  - readonly: true\n  - desc: 参照元\n  - base: main\n")
        self.assertIn("参照のみ", km._charter_definition(ch))
        self.assertIn("参照のみ", km.build_charter_request(ch))

    def test_cmd_project_errors_on_invalid_repos(self):
        # desc/base 欠落の repos を持つ charter は cmd_project がエラー停止（return 2）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: X\n## goal\nやる\n## acceptance\n- true\n"
                             "## repos\n- app = https://git/app.git\n")
            self.assertEqual(km.cmd_project(cfg_for(d)), 2)

    def test_reference_repos_passed_as_structured_args(self):
        # owns 無し（参照リポジトリ）は --reference として構造化伝搬する（分解後の各ノード/gitlab
        # イシューにも届くように。要求本文へは畳まない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: **\n  - base: main\n"
                          "- spec = https://git/spec.git\n  - desc: API 仕様\n  - base: main\n")
            cfg = cfg_for(d)
            refs = km.task_reference_specs(cfg, km.Task(id="T1", title="x"))
            self.assertEqual([s["name"] for s in refs], ["spec"])      # owns 無しだけ参照に
            t = km.Task(id="T1", title="x", verify="true", extra=[("workspace", "app")])
            cmd = km.build_kiro_flow_cmd(t, cfg)
            # --reference の値だけを集める（書込先 app は参照に含めない）
            ref_vals = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--reference"]
            self.assertEqual([json.loads(v)["url"] for v in ref_vals], ["https://git/spec.git"])
            self.assertFalse(any("app.git" in v for v in ref_vals))
            # 要求本文へは畳まない（構造化伝搬に一本化）
            self.assertNotIn("参照用リポジトリ", km.build_request(t, cfg))

    def test_workspace_only_propagated_when_resolved(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: **\n  - base: main\n")
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="x", verify="true", extra=[("workspace", "app")])
            cmd = km.build_kiro_flow_cmd(t, cfg)
            self.assertIn("--workspace", cmd)
            self.assertIn("https://git/app.git", cmd[cmd.index("--workspace") + 1])
            # 未解決（- workspace: 無し）のタスクは --workspace を付けない＝読み取り専用 run
            self.assertNotIn("--workspace", km.build_kiro_flow_cmd(km.Task(id="T2", title="y"), cfg))

    def test_assign_plan_workspace_from_verify_paths(self):
        # plan が生成したタスクは、verify が操作するパスの owns を持つ repo を書込先にする
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app = https://git/app.git\n  - owns: apps/app/**\n  - base: main\n"
            "- lib = https://git/lib.git\n  - owns: packages/**\n  - base: main\n"
            "- spec = https://git/spec.git\n  - desc: 仕様（参照）\n  - base: main\n")
        sp = km.assign_plan_workspace(ch, {"title": "型を追加",
                                           "verify": "test -f packages/types.ts"})
        self.assertEqual(sp["workspace"], "lib")            # owns packages/** に一致 → lib が書込先
        self.assertIn("app", sp["refs"]); self.assertIn("spec", sp["refs"])  # 他は参照
        self.assertNotIn("lib", sp["refs"].split(","))      # 書込先は参照に含めない
        self.assertNotIn("repos", sp)                       # repos は廃止

    def test_assign_plan_workspace_respects_owning_hint(self):
        # プランナーが付けた workspace（owns 持ち）は尊重。owns を持たない指定は無視して推定に倒す
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app = https://git/app.git\n  - owns: apps/app/**\n  - base: main\n"
            "- lib = https://git/lib.git\n  - owns: packages/**\n  - base: main\n")
        sp = km.assign_plan_workspace(ch, {"title": "t", "verify": "test -f packages/x",
                                           "workspace": "app"})
        self.assertEqual(sp["workspace"], "app")            # プランナー指定（owns 持ち）を尊重
        sp2 = km.assign_plan_workspace(ch, {"title": "t", "verify": "test -f packages/x",
                                            "workspace": "spec"})  # owns 無し指定は無効
        self.assertEqual(sp2["workspace"], "lib")           # → verify パスの owns で確定

    def test_plan_via_agent_sets_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: apps/app/**\n  - base: main\n"
                          "- lib = https://git/lib.git\n  - owns: packages/**\n  - base: main\n")
            cfg = cfg_for(d)
            ch = km.load_charter(cfg)
            orig = km._run_kiro_cli
            km._run_kiro_cli = lambda prompt, model: (
                '[{"title":"lib に型追加","verify":"test -f packages/t.ts"}]')
            try:
                specs = km.plan_via_agent(cfg, ch)
            finally:
                km._run_kiro_cli = orig
            self.assertEqual(specs[0]["workspace"], "lib")  # verify=packages/** → lib（必ず明示される）

    def test_plugin_executor_forwarded_to_kiro_flow(self):
        # executor に kiro-flow プラグイン名/パスを指定すると、そのまま kiro-flow run へ委譲される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="gitlab")
            cmd = km.build_kiro_flow_cmd(km.Task(id="T1", title="x", verify="true"), cfg)
            i = cmd.index("--executor")
            self.assertEqual(cmd[i + 1], "gitlab")
            cfg2 = cfg_for(d, executor="/path/to/my_executor.py")
            cmd2 = km.build_kiro_flow_cmd(km.Task(id="T2", title="y"), cfg2)
            self.assertEqual(cmd2[cmd2.index("--executor") + 1], "/path/to/my_executor.py")

    def test_cli_accepts_plugin_executor(self):
        # CLI の --executor は choices で縛らず、プラグイン名をそのまま受理する（dry-run で act はしない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(Path(d), "T1", title="x", verify="true")
            rc = km.main(["run", "--workdir", str(d), "--root", str(Path(d) / ".ka"),
                          "--planner", "none", "--flow-planner", "stub",
                          "--executor", "gitlab", "--dry-run"])
            self.assertEqual(rc, 0)

    def test_repos_spec_roundtrips_to_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.task_from_spec(cfg, {"title": "x", "verify": "true", "repos": ["app", "lib"]})
            self.assertEqual(t.get("repos"), "app,lib")
            t2 = km.parse_task(km.serialize_task(t), t.id)      # 永続化往復で保持
            self.assertEqual(t2.get("repos"), "app,lib")

    def test_run_autodetects_charter(self):
        # run は charter.md があれば自動で目標駆動になる（project サブコマンドは廃止・1プロセス統合）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            proot = d / ".kiro-projects" / "projects" / "default"
            proot.mkdir(parents=True)
            (proot / "charter.md").write_text(
                "# Charter: demo\n## goal\nやる\n## acceptance\n- `true`\n", encoding="utf-8")
            rc = km.main(["run", "--workdir", str(d), "--planner", "none",
                          "--flow-planner", "stub", "--executor", "stub", "--dry-run",
                          "--max-project-cycles", "1"])
            self.assertEqual(rc, 1)                       # 収束候補→人待ち
            self.assertTrue((proot / "project.json").exists())
            # milestone id は --project 名（default）が一次（charter 名でなく）
            self.assertTrue((proot / "needs" / "default.md").exists())

    def test_run_without_charter_is_plain_loop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            proot = d / ".kiro-projects" / "projects" / "default"
            (proot / "backlog").mkdir(parents=True)
            (proot / "backlog" / "T1.md").write_text(
                "## T1: x\n- status: ready\n- verify: `true`\n", encoding="utf-8")
            rc = km.main(["run", "--workdir", str(d), "--planner", "none",
                          "--flow-planner", "stub", "--executor", "stub", "--dry-run"])
            self.assertEqual(rc, 0)                       # charter 無し→従来の backlog ループで drained
            self.assertFalse((proot / "project.json").exists())

    def test_missing_charter_errors(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(km.cmd_project(cfg_for(d)), 2)

    def test_no_acceptance_escalates(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: X\n## goal\nやる\n")
            code = km.cmd_project(cfg_for(d), planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, 1)
            self.assertTrue((d / "needs" / "X.md").exists())

    def test_acceptance_kind_classifies(self):
        self.assertEqual(km._acceptance_kind("pytest -q tests/"), ("command", "pytest -q tests/"))
        self.assertEqual(km._acceptance_kind("test -f x && grep -q y z"),
                         ("command", "test -f x && grep -q y z"))
        # 明示の accept: 接頭辞 → 自然言語（接頭辞を剥がす）
        self.assertEqual(km._acceptance_kind("accept: README に概要がある"),
                         ("accept", "README に概要がある"))
        self.assertEqual(km._acceptance_kind("受入: 画面が表示される"),
                         ("accept", "画面が表示される"))
        # 接頭辞なしの散文（全角句読点）も自然言語に倒す
        self.assertEqual(km._acceptance_kind("レポートに要約が出力される。"),
                         ("accept", "レポートに要約が出力される。"))

    def test_resolve_acceptance_synthesizes_natural_language(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            ch = km.parse_charter("# Charter: x\n## goal\nやる\n## acceptance\n"
                                  "- `test -f keep`\n- accept: README に概要がある\n")
            state = {}
            resolved, unresolved = km.resolve_charter_acceptance(
                cfg, ch, state, kiro_run=lambda p, m: "grep -q 概要 README.md")
            self.assertEqual(resolved, ["test -f keep", "grep -q 概要 README.md"])
            self.assertEqual(unresolved, [])
            # 合成結果は原文キーでキャッシュされ、再実行で安定する（再合成不要）
            self.assertEqual(state["acceptance_synth"]["README に概要がある"],
                             "grep -q 概要 README.md")
            again, _ = km.resolve_charter_acceptance(
                cfg, ch, state, kiro_run=lambda p, m: self.fail("再合成された"))
            self.assertEqual(again, ["test -f keep", "grep -q 概要 README.md"])

    def test_resolve_acceptance_unresolved_when_synth_fails(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            ch = km.parse_charter("# Charter: x\n## goal\nやる\n## acceptance\n"
                                  "- accept: 曖昧で検証できない\n")
            resolved, unresolved = km.resolve_charter_acceptance(
                cfg, ch, {}, kiro_run=lambda p, m: "やはり検証できません。")  # 散文 → 合成失敗
            self.assertEqual(resolved, [])
            self.assertEqual(unresolved, ["曖昧で検証できない"])

    def test_natural_language_acceptance_converges(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, "# Charter: nl\n## goal\nやる\n## acceptance\n"
                             f"- accept: flag ファイルが存在する\n")
            code = km.cmd_project(cfg_for(d), planner=lambda ch: [],
                                  runner=lambda c: (flag.write_text("x"), _drained())[1],
                                  kiro_run=lambda p, m: f"test -f {flag}")
            self.assertEqual(code, 1)            # converged → 人の承認待ち
            self.assertEqual(km.load_project_state(cfg_for(d))["status"],
                             km.REASON_PROJECT_CONVERGED)

    def test_unsynthesizable_acceptance_escalates(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: nl\n## goal\nやる\n## acceptance\n"
                             "- accept: 曖昧な完了条件\n")
            code = km.cmd_project(cfg_for(d), planner=lambda ch: [],
                                  runner=lambda c: _drained(),
                                  kiro_run=lambda p, m: "")   # 合成不能
            self.assertEqual(code, 1)            # done 判定不能 → 人へ
            self.assertTrue((d / "needs" / "nl.md").exists())

    def test_plan_enqueues_then_converges(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            planned = {"n": 0}

            def planner(ch):
                planned["n"] += 1
                return [{"title": "成果物を作る", "verify": f"test -f {flag}"}]

            def runner(c):                      # 実行を模す: acceptance を満たすファイルを作る
                flag.write_text("x")
                return _drained()

            code = km.cmd_project(cfg_for(d), planner=planner, runner=runner)
            self.assertEqual(code, 1)           # converged → 人の承認待ち
            st = km.load_project_state(cfg_for(d))
            self.assertEqual(st["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(planned["n"], 1)   # 1 回だけ plan（消化可能タスクがある間は再分解しない）
            self.assertTrue((d / "needs" / "demo.md").exists())

    def test_unmet_acceptance_generates_improvement(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", str(d / "never")))
            cfg = cfg_for(d, max_project_cycles=1)
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, 2)           # 1サイクルで未達のまま予算到達 → project-budget
            # 未達 acceptance がそれ自体を verify とする改善タスクとして積まれている
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertTrue(any("受入条件を満たす" in t for t in titles))

    def test_resolve_verify_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(km.resolve_verify_cwd(cfg_for(d)), d)        # 既定は workdir
            self.assertEqual(km.resolve_verify_cwd(cfg_for(d, verify_cwd="/abs/clone")),
                             Path("/abs/clone"))                          # 絶対パスはそのまま
            self.assertEqual(km.resolve_verify_cwd(cfg_for(d, verify_cwd="clone")),
                             d / "clone")                                 # 相対は workdir 起点

    def test_verify_cwd_overrides_acceptance_dir(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            clone = d / "clone"; clone.mkdir(); (clone / "M").write_text("x")
            charter = km.parse_charter("# Charter: c\n## goal\nx\n## acceptance\n- test -f M\n")
            # workdir(d) には M が無い → 未指定なら FAIL
            self.assertEqual(km.evaluate_acceptance(cfg_for(d), charter)[0], 0)
            # verify_cwd をクローン先に向けると PASS（成果のある場所で検証）
            passed, total, _ = km.evaluate_acceptance(cfg_for(d, verify_cwd=str(clone)), charter)
            self.assertEqual((passed, total), (1, 1))

    def _make_git_repo(self, path: Path, marker: str = "MARKER.txt") -> None:
        g = ["git", "-C", str(path)]
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        (path / marker).write_text("ok")
        subprocess.run(g + ["add", "-A"], check=True)
        subprocess.run(g + ["-c", "user.email=a@b", "-c", "user.name=x",
                            "commit", "-qm", "init"], check=True)

    def test_acceptance_clones_single_repo_when_workdir_lacks_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote)
            # workdir(d) には MARKER が無いので、clone せず workdir で見ると FAIL になるはず。
            # base/target を省く（branch 非依存で既定ブランチを clone）。url は単一・非 readonly。
            charter = km.parse_charter(
                f"# Charter: c\n## goal\nx\n## acceptance\n- test -f MARKER.txt\n"
                f"## repos\n- app = {remote}\n  - owns: **\n  - desc: 対象\n")
            passed, total, _ = km.evaluate_acceptance(cfg_for(d), charter)
            self.assertEqual((passed, total), (1, 1))   # 一時 clone 先で検証 → PASS

    def test_acceptance_clone_failure_is_all_ng(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            charter = km.parse_charter(
                "# Charter: c\n## goal\nx\n## acceptance\n- true\n"
                f"## repos\n- app = {d / 'does-not-exist'}\n  - owns: **\n  - desc: 対象\n")
            passed, total, results = km.evaluate_acceptance(cfg_for(d), charter)
            self.assertEqual(passed, 0)                 # clone 失敗 → 黙ってフォールバックせず全 NG
            self.assertTrue(any("clone" in m for _, _, m in results))

    def test_acceptance_multi_repo_uses_workdir(self):
        # 対象 repo が複数なら（どれを cwd にするか曖昧）従来どおり workdir で実行する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "M").write_text("x")
            charter = km.parse_charter(
                "# Charter: c\n## goal\nx\n## acceptance\n- test -f M\n## repos\n"
                "- a = https://git/a.git\n  - desc: A\n  - base: main\n"
                "- b = https://git/b.git\n  - desc: B\n  - base: main\n")
            self.assertIsNone(km._charter_single_repo(charter))
            self.assertEqual(km.evaluate_acceptance(cfg_for(d), charter)[0], 1)  # workdir(d) で PASS

    def test_task_verify_cwd_clones_workspace_repo(self):
        # workspace 指定タスクは git-bus ルート(workdir)でなく該当 repo のクローン内で検証する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote, marker="WS.txt")     # workdir(d) には WS.txt が無い
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x", verify="test -f WS.txt")
            task.set("workspace", "app")
            vcwd, tmp = km._task_verify_cwd(cfg_for(d), task)
            try:
                self.assertIsNotNone(tmp)                    # 一時 clone を作った
                self.assertTrue((vcwd / "WS.txt").exists())  # クローン内に成果がある
                self.assertNotEqual(vcwd, d)                 # workdir ではない
            finally:
                if tmp:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_task_verify_cwd_uses_clone_root_not_path(self):
        # path（モノレポのサブフォルダ）があっても cwd はクローンのルート。verify は
        # リポジトリ直下からの相対（例 `cd pkg && …`）で書かれる規約なので path には潜らない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote)
            (remote / "pkg").mkdir()
            (remote / "pkg" / "IN_SUB.txt").write_text("ok")
            subprocess.run(["git", "-C", str(remote), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(remote), "-c", "user.email=a@b",
                            "-c", "user.name=x", "commit", "-qm", "sub"], check=True)
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - path: pkg\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x", verify="test -f pkg/IN_SUB.txt")
            task.set("workspace", "app")
            vcwd, tmp = km._task_verify_cwd(cfg_for(d), task)
            try:
                self.assertNotEqual(vcwd.name, "pkg")        # path には潜らない（クローンのルート）
                self.assertTrue((vcwd / ".git").exists())    # ルートなので $KIRO_BASE_REV を取り直せる
                self.assertTrue((vcwd / "pkg" / "IN_SUB.txt").exists())   # path はルートからの相対で届く
            finally:
                if tmp:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_task_verify_cwd_bad_path_raises(self):
        # path: が clone 内に無い（誤設定）は RuntimeError（黙って workdir に倒さない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote)
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - path: nope\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x", verify="true")
            task.set("workspace", "app")
            with self.assertRaises(RuntimeError):
                km._task_verify_cwd(cfg_for(d), task)

    def test_task_verify_cwd_no_workspace_falls_back_to_workdir(self):
        # workspace 未指定は従来どおり workdir（一時 clone を作らない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            vcwd, tmp = km._task_verify_cwd(cfg_for(d), km.Task(id="T1", title="x"))
            self.assertEqual(vcwd, d)
            self.assertIsNone(tmp)

    def test_task_verify_cwd_explicit_verify_cwd_wins(self):
        # 明示 verify_cwd は workspace 指定より優先（運用の上書き・clone しない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote)
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x")
            task.set("workspace", "app")
            vcwd, tmp = km._task_verify_cwd(cfg_for(d, verify_cwd="/abs/clone"), task)
            self.assertEqual(vcwd, Path("/abs/clone"))
            self.assertIsNone(tmp)

    def test_task_verify_cwd_clone_failure_raises(self):
        # clone 失敗は黙って workdir に倒さず RuntimeError（成果の無い場所で誤判定しない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {d / 'nope'}\n  - owns: **\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x")
            task.set("workspace", "app")
            with self.assertRaises(RuntimeError):
                km._task_verify_cwd(cfg_for(d), task)

    def test_stall_escalates(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", str(d / "never")))
            cfg = cfg_for(d, max_project_cycles=9, project_stall=2)
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            st = km.load_project_state(cfg)
            self.assertEqual(st["status"], km.REASON_PROJECT_STALL)
            self.assertEqual(code, 1)

    def test_approve_finalizes_converged_project(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)
            st = km.load_project_state(cfg)
            self.assertEqual(st["status"], km.REASON_PROJECT_ACCEPTED)
            self.assertIn("project", (d / "DELIVERY.md").read_text(encoding="utf-8"))

    def test_review_project_generates_findings(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, review_project=True, max_project_cycles=1)
            seen = {"n": 0}

            def reviewer(ch):
                seen["n"] += 1
                return [{"title": "テストを追加", "verify": "true"}]

            km.cmd_project(cfg, planner=lambda ch: [], reviewer=reviewer,
                           runner=lambda c: _drained())
            self.assertEqual(seen["n"], 1)      # acceptance 全 PASS でも敵対的レビューが走る
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertIn("テストを追加", titles)

    def test_inner_blocked_stops_project(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", str(d / "f")))

            def runner(c):
                r = _drained(); r["counts"]["blocked"] = 1
                return r

            code = km.cmd_project(cfg_for(d), planner=lambda ch: [], runner=runner)
            self.assertEqual(km.load_project_state(cfg_for(d))["status"],
                             km.REASON_PROJECT_BLOCKED)
            self.assertEqual(code, 1)

    def test_request_injects_charter_and_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", "x"))
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True, exist_ok=True)
            km.append_decision(cfg, "T1", "user", context="前回の判断",
                               action="approve", reason="ライブラリXを使う", affects="T1")
            t = km.Task(id="T1", title="やる", verify="true")
            req = km.build_request(t, cfg)
            self.assertIn("プロジェクト定義", req)       # charter(定義)が注入される
            self.assertIn("CSV", req)                    # goal 本文
            self.assertIn("過去の判断記録", req)         # needs の判断結果(decisions)が注入される
            self.assertIn("ライブラリXを使う", req)

    def test_request_no_charter_is_backward_compatible(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)                              # charter.md 無し（通常運用）
            t = km.Task(id="T1", title="やる", verify="true")
            self.assertNotIn("プロジェクト定義", km.build_request(t, cfg))
            self.assertEqual(km.build_request(t), km.build_request(t, None))  # cfg 無しは従来どおり

    def test_charter_definition_includes_repos_and_links(self):
        # charter の repos（対象リポジトリ）と links（ブランチ等）が定義文に含まれる
        ch = km.parse_charter(
            "# Charter: r\n## goal\nやる\n"
            "## repos\n- app = https://git/app.git\n"
            "## links\n- https://git/app.git@release ブランチで作業\n")
        d = km._charter_definition(ch)
        self.assertIn("対象リポジトリ", d)
        self.assertIn("https://git/app.git", d)
        self.assertIn("関連リンク", d)
        self.assertIn("release ブランチで作業", d)

    def test_request_carries_charter_repos_and_links(self):
        # build_request（→ kiro-flow ワーカー/gitlab イシュー）に repos/links が伝わる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nやる\n"
                             "## repos\n- app = https://git/app.git\n"
                             "## links\n- https://git/app.git@release で作業\n")
            cfg = cfg_for(d)
            req = km.build_request(km.Task(id="T1", title="やる", verify="true"), cfg)
            self.assertIn("https://git/app.git", req)
            self.assertIn("release で作業", req)

    def test_idempotent_plan_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            existing = ["成果物を作る"]
            created = km._enqueue_specs(
                cfg, [{"title": "成果物を作る", "verify": "true"}], existing, 0.5)
            self.assertEqual(created, [])       # 既存と類似は投入しない


def _drained():
    return {"reason": km.REASON_DRAINED, "cycles": 0,
            "counts": {s: 0 for s in km.VALID_STATUS}, "cost": 0.0, "tokens": 0}


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
            # run_loop の S0 で synth_verify（kiro_run を差し替え）により verify が用意される
            orig = km._run_kiro_cli
            km._run_kiro_cli = lambda prompt, model: "grep -q '## 概要' README.md"
            try:
                km.run_loop(cfg_for(d, dry_run=True, max_cycles=1))
            finally:
                km._run_kiro_cli = orig
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
            self.assertFalse(km.ensure_verify(cfg, t, kiro_run=boom))   # 合成不能→verify 空のまま
            self.assertEqual(t.verify, "")

    def test_strip_ansi_removes_escapes(self):
        raw = "\x1b[38;5;141m> \x1b[0mgrep -q foo bar.txt\x1b[0m"
        self.assertEqual(km.strip_ansi(raw), "> grep -q foo bar.txt")
        self.assertEqual(km.strip_ansi(""), "")

    def test_synth_verify_strips_ansi_from_kiro_output(self):
        # kiro-cli の色付き出力に ANSI が混ざっても、合成した verify は素のコマンドになる
        cfg = cfg_for(Path("."))
        ansi_out = "\x1b[2K\x1b[36mgrep -q '## 概要' README.md\x1b[0m"
        cmd = km.synth_verify(cfg, "概要を書く", "README に概要", kiro_run=lambda p, m: ansi_out)
        self.assertEqual(cmd, "grep -q '## 概要' README.md")
        self.assertNotIn("\x1b", cmd)

    def test_synth_verify_rejects_japanese_prose(self):
        # バグ修正: エージェントが自然言語（説明/拒否文）を返しても shell へ流さない
        cfg = cfg_for(Path("."))
        prose = "この完了条件は曖昧なため、決定的な検証コマンドに変換できません。"
        self.assertEqual(km.synth_verify(cfg, "x", "曖昧", kiro_run=lambda p, m: prose), "")

    def test_synth_verify_rejects_malformed_shell_prose(self):
        # 不完全なシェル構文（散文）も弾く（sh -n が syntax error にする）
        cfg = cfg_for(Path("."))
        prose = "Run the tests; if they pass, you are done"
        self.assertEqual(km.synth_verify(cfg, "x", "tests", kiro_run=lambda p, m: prose), "")

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


class TestMultiProject(unittest.TestCase):
    def test_run_all_consumes_every_project(self):
        # 1 プロセス（--project all）でコンテナ配下の全プロジェクトを回す
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            root = d / ".ka"
            for name in ("alpha", "beta"):
                km.main(["enqueue", "--project", name, "--title", f"T-{name}", "--verify", "true",
                         "--workdir", str(d), "--root", str(root)])
            rc = km.main(["run", "--project", "all", "--workdir", str(d), "--root", str(root),
                          "--planner", "none", "--flow-planner", "stub", "--executor", "stub",
                          "--dry-run", "--max-cycles", "3"])
            self.assertEqual(rc, 0)
            # 両プロジェクトとも消化され archive に入る
            self.assertEqual(len(list((root / "projects" / "alpha" / "archive").glob("*.md"))), 1)
            self.assertEqual(len(list((root / "projects" / "beta" / "archive").glob("*.md"))), 1)

    def test_project_cfg_repoints_paths(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = cfg_for(d / ".ka" / "projects" / "default", project_name="default")
            pc = km.project_cfg(base, "payments")
            self.assertEqual(pc.project_name, "payments")
            self.assertTrue(str(pc.backlog).endswith("projects/payments/backlog"))
            self.assertEqual(km.container_dir(pc), d / ".ka")

    def test_run_creates_default_project_folder(self):
        # project 指定なしで起動すると default プロジェクトのフォルダが（無ければ）作られ、その下に集約される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            root = d / ".ka"
            km.main(["run", "--workdir", str(d), "--root", str(root),
                     "--planner", "none", "--flow-planner", "stub", "--executor", "stub", "--dry-run"])
            dflt = root / "projects" / "default"
            self.assertTrue((dflt / "backlog").is_dir())
            self.assertTrue((dflt / "needs").is_dir())
            self.assertTrue((dflt / "decisions").is_dir())
            self.assertFalse((root / "inbox").exists())     # グローバル inbox は作らない

    def test_enqueue_targets_project_dir(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            root = d / ".ka"
            km.main(["enqueue", "--title", "A", "--verify", "true",
                     "--workdir", str(d), "--root", str(root)])                      # default
            km.main(["enqueue", "--project", "payments", "--title", "B", "--verify", "true",
                     "--workdir", str(d), "--root", str(root)])                      # 別プロジェクト
            self.assertEqual(len(list((root / "projects" / "default" / "backlog").glob("*.md"))), 1)
            self.assertEqual(len(list((root / "projects" / "payments" / "backlog").glob("*.md"))), 1)

    def test_needs_decisions_isolated_per_project(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            root = d / ".ka"
            # P1 にだけ判断待ち（verify 無し→inbox ではなく blocked にするため triage 経由）
            km.main(["enqueue", "--project", "p1", "--title", "X",
                     "--workdir", str(d), "--root", str(root)])        # verify 無し → inbox
            # p1 の操作は p1 配下だけを作り、p2 は存在しない（プロジェクト分離）
            self.assertTrue((root / "projects" / "p1" / "backlog").exists())
            self.assertFalse((root / "projects" / "p2").exists())

    def test_instance_record_exposes_project_and_container(self):
        # 外部操作者（スキル）が発見後に `--root <container> --project <name>` を組めること
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            proot = d / ".ka" / "projects" / "payments"
            proot.mkdir(parents=True)
            rec = km.instance_record(cfg_for(proot, project_name="payments"))
            self.assertEqual(rec["project"], "payments")
            self.assertEqual(rec["container"], str((d / ".ka").resolve()))
            self.assertEqual(rec["root"], str(proot.resolve()))

    def test_project_dirname_sanitizes(self):
        self.assertEqual(km._project_dirname("a/b:c"), "a_b_c")
        self.assertEqual(km._project_dirname("  "), "default")
        self.assertEqual(km._project_dirname("案件A"), "案件A")     # unicode は保つ

    def test_project_id_prefers_project_name(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ch = km.parse_charter("# Charter: 表示名\n## goal\nやる\n")
            self.assertEqual(km._project_id(cfg_for(d, project_name="payments"), ch), "payments")
            # project_name 未設定（Config 直接構築）は charter 名スラグ→日本語のみは "project"
            self.assertEqual(km._project_id(cfg_for(d), ch), "project")

    def test_charter_links_resolve_and_inject(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            projects = d / ".ka" / "projects"
            # リンク先プロジェクト shared に定義と learn を置く
            shared = projects / "shared"
            (shared / "decisions").mkdir(parents=True)
            (shared).joinpath("charter.md").write_text(
                "# Charter: shared\n## goal\n共通規約\n## constraints\n- 二段階認証必須\n", encoding="utf-8")
            km.append_decision(cfg_for(shared), "T9", "user", context="c",
                               action="approve", reason="r", affects="a",
                               learn=("認証の作法", "MFA を必ず通す"))
            # 本体プロジェクト main が shared をリンク
            main_p = projects / "main"
            (main_p / "backlog").mkdir(parents=True)
            (main_p).joinpath("charter.md").write_text(
                "# Charter: main\n## goal\n本体\n## acceptance\n- `true`\n## links\n- shared\n",
                encoding="utf-8")
            cfg = cfg_for(main_p)
            ch = km.load_charter(cfg)
            links = km.resolve_linked_projects(cfg, ch)
            self.assertEqual([n for n, _ in links], ["shared"])
            cc = km.charter_context(cfg)
            self.assertIn("二段階認証必須", cc)              # リンク先の定義が取り込まれる
            lc = km.linked_learnings_context(cfg)
            self.assertIn("MFA を必ず通す", lc)              # リンク先の判断(learn)が取り込まれる


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


def _write_backlog_task(backlog: Path, tid: str, verify: str, title: "str | None" = None):
    """CLI e2e 用に backlog/<id>.md を書く（mkb の最小版・絶対パス前提）。"""
    backlog.mkdir(parents=True, exist_ok=True)
    (backlog / f"{tid}.md").write_text(
        f"## {tid}: {title or tid}\n- status: ready\n- verify: `{verify}`\n", encoding="utf-8")


class TestCliEndToEnd(unittest.TestCase):
    """kiro-projects.py を実プロセスとして argv 起動する黒箱 CLI e2e。

    TestRunLoop が run_loop() を in-process で呼ぶのに対し、こちらは CLI 配線（argparse・パス解決・
    停止理由→exit code・成果物の書き出し）を実バイナリで検証する。act は --dry-run で省略し、
    ループ機構そのもの（優先順位→verify→done/archive/blocked/needs）を確認する。
    パスは絶対（mkdtemp）で渡す: 相対パスは --workdir 基準で解決され picked up されないため。"""

    def _run(self, d: Path, *extra, timeout=60):
        cmd = [sys.executable, str(_MOD), "run",
               "--workdir", str(d), "--backlog", str(d / "backlog"),
               "--policy", str(d / "policy.md"), "--decisions", str(d / "decisions"),
               "--journal", str(d / "journal.md"), "--needs", str(d / "needs"),
               "--bus", str(d / "bus"), "--planner", "none",
               "--executor", "stub", "--flow-planner", "stub"]
        cmd += list(extra)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def test_drains_and_archives(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write_backlog_task(d / "backlog", "T1", "true")
            _write_backlog_task(d / "backlog", "T2", "true")
            p = self._run(d, "--dry-run", "--max-cycles", "10")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)   # drained → 0
            self.assertIn("drained", p.stdout)
            self.assertIn("done=2", p.stdout)
            self.assertEqual(list((d / "backlog").glob("*.md")), [])  # backlog から消える

    def test_blocked_when_verify_fails(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write_backlog_task(d / "backlog", "T1", "false")        # verify は必ず FAIL
            p = self._run(d, "--dry-run", "--max-retries", "0", "--max-cycles", "10")
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)   # blocked → 1
            self.assertIn("blocked=1", p.stdout)
            self.assertTrue((d / "needs" / "T1.md").exists())        # 人の判断へ委譲
            self.assertTrue((d / "backlog" / "T1.md").exists())      # backlog には残す

    def test_budget_stop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write_backlog_task(d / "backlog", "T1", "false")
            # 無限リトライ相当 + サイクル上限 → drain せず予算で停止
            p = self._run(d, "--dry-run", "--max-retries", "999", "--max-cycles", "3")
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)   # budget → 2
            self.assertIn("budget", p.stdout)

    def test_no_archive_deletes_instead(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write_backlog_task(d / "backlog", "T1", "true")
            p = self._run(d, "--dry-run", "--no-archive", "--max-cycles", "10")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("done=1", p.stdout)
            self.assertIn("archived=0", p.stdout)                    # 退避せず削除
            self.assertEqual(list((d / "backlog").glob("*.md")), [])


class TestCliKiroFlowDelegation(unittest.TestCase):
    """kiro-projects CLI が act を実際に kiro-flow.py へサブプロセス委譲し、完走することを検証する
    クロスツール e2e。委譲の証跡（argv）と委譲先 kiro-flow の正常終了をラッパで捕捉して検証する。"""

    def test_cli_delegates_to_real_kiro_flow(self):
        kf = Path(__file__).resolve().parents[2] / "kiro-flow" / "kiro-flow.py"
        if not kf.exists():
            self.skipTest("kiro-flow.py が見つからない")
        os.environ["KIRO_FLOW_STUB_SLEEP_MAX"] = "0"   # stub の擬似スリープ無効化（子へ継承）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            log = d / "kf.log"
            # ラッパ: 委譲 argv を記録 → 本物の kiro-flow へ転送 → その exit code も記録/伝播
            wrapper = d / "kfwrap.py"
            wrapper.write_text(
                "import sys, subprocess\n"
                "argv = sys.argv[1:]\n"
                f"open(r'{log}', 'a').write('ARGV\\t' + '\\t'.join(argv) + '\\n')\n"
                f"rc = subprocess.run([sys.executable, r'{kf}'] + argv).returncode\n"
                f"open(r'{log}', 'a').write('RC\\t%d\\n' % rc)\n"
                "sys.exit(rc)\n", encoding="utf-8")
            marker = d / "marker"
            marker.write_text("done")   # act は best-effort。verify が真実の源なので事前に通る状態を作る
            _write_backlog_task(d / "backlog", "T1", f"test -f {marker}", title="何かを実装")
            cmd = [sys.executable, str(_MOD), "run",
                   "--workdir", str(d), "--backlog", str(d / "backlog"),
                   "--policy", str(d / "policy.md"), "--decisions", str(d / "decisions"),
                   "--journal", str(d / "journal.md"), "--needs", str(d / "needs"),
                   "--bus", str(d / "bus"), "--planner", "none",
                   "--executor", "stub", "--flow-planner", "stub",
                   "--kiro-flow", str(wrapper),
                   "--act-timeout", "150", "--max-cycles", "3"]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("done=1", p.stdout)
            logtext = log.read_text(encoding="utf-8")
            # 実際に kiro-flow が `run --planner stub --executor stub …` で起動された証跡
            self.assertIn("\trun\t", logtext)
            self.assertIn("--planner", logtext)
            self.assertIn("--executor", logtext)
            self.assertIn("stub", logtext)
            # 委譲先 kiro-flow（orchestrator/worker まで含む）自身が正常終了した
            self.assertIn("RC\t0", logtext)


def _make_skill_repo(root: Path, tool_subdir: str = "tools/kiro-projects") -> Path:
    """temp に「スキルリポジトリ」を作る: main に tool_subdir/install.sh を持つ git リポジトリ。
    install.sh は --prefix のディレクトリに marker を書くだけの最小実装。リポジトリ path を返す。"""
    repo = root / "skillrepo"
    td = repo / tool_subdir
    td.mkdir(parents=True, exist_ok=True)
    other = repo / "tools" / "kiro-flow"           # sparse 除外の確認用
    other.mkdir(parents=True, exist_ok=True)
    (other / "FILE.txt").write_text("unrelated\n")
    (td / "install.sh").write_text(
        "#!/usr/bin/env bash\nset -e\nPREFIX=\"$HOME/.local/bin\"\n"
        "[ \"$1\" = --prefix ] && PREFIX=\"$2\"\nmkdir -p \"$PREFIX\"\n"
        "echo installed > \"$PREFIX/INSTALLED_MARKER\"\n")
    (td / "kiro-projects.py").write_text("# tool body\n")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
              ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(c, cwd=repo, env=env, check=True, capture_output=True)
    return repo


def _commit_change(repo: Path, relpath: str, content: str = "x\n") -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "update"], cwd=repo, env=env,
                   check=True, capture_output=True)


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ka-update-"))
        self.state = self.tmp / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self._old = os.environ.get("KIRO_STATE_HOME")
        os.environ["KIRO_STATE_HOME"] = str(self.state)
        km._UPDATE_LAST_CHECK["t"] = 0.0          # モジュール状態を毎テストでリセット
        self.repo = _make_skill_repo(self.tmp)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KIRO_STATE_HOME", None)
        else:
            os.environ["KIRO_STATE_HOME"] = self._old
        km._UPDATE_LAST_CHECK["t"] = 0.0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, **kw):
        base = dict(update_repo=str(self.repo), update_branch="main",
                    update_subdir="tools/kiro-projects", update_installer="install.sh",
                    update_check_interval=60.0)
        base.update(kw)
        return cfg_for(self.tmp, **base)

    def test_remote_branch_sha(self):
        sha = km.remote_branch_sha(str(self.repo), "main")
        self.assertTrue(sha and len(sha) >= 7)
        self.assertIsNone(km.remote_branch_sha("", "main"))
        self.assertIsNone(km.remote_branch_sha(str(self.repo), "no-such-branch"))

    def test_check_update_baseline_then_latest(self):
        cfg = self._cfg()
        info = km.check_update(cfg)             # 初回: ベースライン
        self.assertTrue(info["enabled"] and info["baseline"])
        self.assertFalse(info["available"])
        self.assertFalse(km.check_update(cfg)["available"])   # 2 回目: 最新

    def test_check_update_detects_new_commit(self):
        cfg = self._cfg()
        km.check_update(cfg)
        _commit_change(self.repo, "tools/kiro-projects/NEW.txt")
        self.assertTrue(km.check_update(cfg)["available"])

    def test_disabled_when_no_repo(self):
        cfg = self._cfg(update_repo=None)
        self.assertFalse(km.check_update(cfg)["enabled"])
        self.assertFalse(km.maybe_self_update(cfg))

    def test_sparse_checkout_only_subdir(self):
        dest = str(self.tmp / "co" / "repo")
        tool_dir = km.sparse_checkout_tool(str(self.repo), "main",
                                           "tools/kiro-projects", dest)
        self.assertTrue(os.path.isfile(os.path.join(tool_dir, "install.sh")))
        self.assertFalse(os.path.isdir(os.path.join(dest, "tools", "kiro-flow")))

    def test_apply_update_records_sha(self):
        cfg = self._cfg()
        km.check_update(cfg)                    # baseline
        _commit_change(self.repo, "tools/kiro-projects/N2.txt")
        info = km.check_update(cfg)
        self.assertTrue(info["available"])
        prefix = str(self.tmp / "prefix")

        def runner(c, **k):                     # install.sh だけ --prefix を足す
            cmd = c + ["--prefix", prefix] if c[:1] == ["bash"] else c
            return subprocess.run(cmd, capture_output=True, text=True, **k)
        self.assertTrue(km.apply_update(cfg, info, runner=runner))
        self.assertEqual(km.read_update_state()["applied_sha"], info["remote_sha"])
        self.assertTrue(os.path.isfile(os.path.join(prefix, "INSTALLED_MARKER")))
        self.assertFalse(km.check_update(cfg)["available"])   # 適用後は最新

    def test_maybe_self_update_disabled_interval(self):
        cfg = self._cfg(update_check_interval=0.0)   # interval<=0 で無効
        self.assertFalse(km.maybe_self_update(cfg))

    def test_update_enabled_false_disables(self):
        cfg = self._cfg(update_enabled=False, update_check_interval=3600.0)
        self.assertFalse(km.maybe_self_update(cfg))

    def test_registry_auto_resolution(self):
        # update_repo 未指定でも skill-registry.json から repo/branch を解決して検出できる
        regdir = self.tmp / "agenthome"
        regdir.mkdir(parents=True, exist_ok=True)
        (regdir / "skill-registry.json").write_text(json.dumps({
            "version": 7, "install_dir": str(self.tmp),
            "repositories": [{"name": "origin", "url": str(self.repo),
                              "branch": "main", "priority": 1}]}))
        old = os.environ.get("KIRO_SKILL_REGISTRY")
        os.environ["KIRO_SKILL_REGISTRY"] = str(regdir)
        try:
            self.assertEqual(km.registry_update_source()[0], str(self.repo))
            cfg = self._cfg(update_repo=None)     # 明示なし → registry から解決
            info = km.check_update(cfg)
            self.assertTrue(info["enabled"])
            self.assertEqual(info["repo"], str(self.repo))
        finally:
            if old is None:
                os.environ.pop("KIRO_SKILL_REGISTRY", None)
            else:
                os.environ["KIRO_SKILL_REGISTRY"] = old

    def test_explicit_repo_overrides_registry(self):
        cfg = self._cfg(update_repo="/explicit/path", update_branch="dev")
        self.assertEqual(km.resolve_update_target(cfg), ("/explicit/path", "dev"))

    def test_run_watch_restarts_on_update(self):
        # アイドルの watch ループで自己更新が成立したら _RestartRequested が送出されること。
        # （idle 配線の検証。更新判定そのものは maybe_self_update を True に差し替える）
        cfg = self._cfg()
        with mock.patch.object(km, "maybe_self_update", return_value=True):
            with self.assertRaises(km._RestartRequested):
                # backlog 空 → run_loop は即 drain → idle ループへ。sleeper は即戻り。
                km.run_watch(cfg, sleeper=lambda _s: None)


class TestGitlabRejectRetry(unittest.TestCase):
    """委譲 executor（gitlab）の却下→通常リトライ連携: 内部再委譲を抑止（--max-retries 0）し、
    却下時の人コメント（[gitlab-reject]）を次 act の feedback に注入する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ka-rej-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_executor_delegates(self):
        self.assertFalse(km.executor_delegates(cfg_for(self.tmp, executor="kiro")))
        self.assertTrue(km.executor_delegates(cfg_for(self.tmp, executor="gitlab")))

    def test_build_cmd_sets_max_retries_zero_for_gitlab(self):
        mkb(self.tmp, "t1")
        t = km.load_tasks((self.tmp / "backlog"))[0]
        cmd = km.build_kiro_flow_cmd(t, cfg_for(self.tmp, executor="gitlab"))
        self.assertIn("--max-retries", cmd)
        self.assertEqual(cmd[cmd.index("--max-retries") + 1], "0")
        # kiro executor では付けない
        cmd2 = km.build_kiro_flow_cmd(t, cfg_for(self.tmp, executor="kiro"))
        self.assertNotIn("--max-retries", cmd2)

    def test_read_reject_guidance_extracts_marker(self):
        cfg = cfg_for(self.tmp, executor="gitlab")
        result_json = json.dumps({"final_nodes": [
            {"id": "n1", "output": "実行エラー: [gitlab-reject] 却下されました（u）。"
                                   "やり直し指示: 命名を要件に合わせる"}]})

        def fake_run(cmd, **kw):
            return types.SimpleNamespace(returncode=1, stdout=result_json, stderr="")

        with mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            g = km.read_reject_guidance(cfg, use_git=False)
        self.assertIn("命名を要件に合わせる", g)
        self.assertNotIn("[gitlab-reject]", g)

    def test_read_reject_guidance_prefers_structured_data(self):
        # kiro-flow の gitlab executor は却下時に failed result へ構造化 data を残す。
        # 文字列マーカーより data（decision=rejected の guidance）を優先して読む。
        cfg = cfg_for(self.tmp, executor="gitlab")
        result_json = json.dumps({"final_nodes": [
            {"id": "n1",
             "output": "実行エラー: [gitlab-reject] 却下されました（u）。やり直し指示: 古い方の指示",
             "data": {"decision": "rejected", "issue_iid": 9,
                      "guidance": "構造化データ側の指示"}}]})

        def fake_run(cmd, **kw):
            return types.SimpleNamespace(returncode=1, stdout=result_json, stderr="")

        with mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            g = km.read_reject_guidance(cfg, use_git=False)
        self.assertEqual(g, "構造化データ側の指示")

    def test_read_reject_guidance_empty_when_no_marker(self):
        cfg = cfg_for(self.tmp, executor="gitlab")

        def fake_run(cmd, **kw):
            return types.SimpleNamespace(
                returncode=0, stdout='{"final_nodes":[{"output":"ok"}]}', stderr="")

        with mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            self.assertEqual(km.read_reject_guidance(cfg, use_git=False), "")

    def test_settle_failure_injects_reject_comment_as_feedback(self):
        cfg = cfg_for(self.tmp, executor="gitlab", max_retries=2)
        (self.tmp / "backlog").mkdir(parents=True, exist_ok=True)
        t = km.Task(id="t1", title="ログイン", verify="true", status="doing")
        with mock.patch.object(km, "read_reject_guidance", return_value="命名を直す"):
            km._settle_failure(cfg, t, "verify NG", cycle=1, ev="", reasons={}, location="local")
        self.assertEqual(t.norm_status(), "ready")          # 積み直し
        self.assertEqual(t.feedback(), "命名を直す")          # 却下コメントを feedback に注入


class SharedGitCacheTests(unittest.TestCase):
    """検証用の共有 git キャッシュ + worktree（docs/designs/git-worktree-cache-pattern.md）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ka-cache-"))
        self._prev = os.environ.get("KIRO_GIT_CACHE_DIR")
        os.environ["KIRO_GIT_CACHE_DIR"] = str(self.tmp / "gitcache")

    def tearDown(self):
        km._prune_caches(km._provisioned_urls)
        km._provisioned_urls.clear()
        if self._prev is None:
            os.environ.pop("KIRO_GIT_CACHE_DIR", None)
        else:
            os.environ["KIRO_GIT_CACHE_DIR"] = self._prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_remote(self, name="remote"):
        remote = self.tmp / name
        remote.mkdir(parents=True)
        for cmd in (["git", "init", "-q", "-b", "main", str(remote)],
                    ["git", "-C", str(remote), "config", "user.email", "t@t"],
                    ["git", "-C", str(remote), "config", "user.name", "t"]):
            subprocess.run(cmd, check=True)
        (remote / "f.txt").write_text("init")
        subprocess.run(["git", "-C", str(remote), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(remote), "commit", "-qm", "init"], check=True)
        return str(remote)

    def test_clone_repo_shallow_uses_worktree_and_reflects_latest(self):
        # _clone_repo_shallow は共有 cache 経由で worktree を生やし、毎回 fetch して最新を反映する（INV-1）。
        remote = self._make_remote()
        dest1 = str(self.tmp / "w1")
        km._clone_repo_shallow(remote, "main", dest1)
        self.assertTrue(os.path.exists(os.path.join(dest1, ".git")))   # worktree なら .git はファイル
        self.assertTrue(os.path.exists(os.path.join(dest1, "f.txt")))
        # ミラーが共有 root にできている
        self.assertTrue(any(n.endswith(".git") for n in os.listdir(os.environ["KIRO_GIT_CACHE_DIR"])))
        # リモートに新コミット → 次の取得は最新を反映
        (Path(remote) / "more.txt").write_text("x")
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "more"], check=True)
        dest2 = str(self.tmp / "w2")
        km._clone_repo_shallow(remote, "main", dest2)
        self.assertTrue(os.path.exists(os.path.join(dest2, "more.txt")))

    def test_clone_repo_shallow_falls_back_when_cache_unavailable(self):
        # INV-3: cache が使えなければ従来の浅 clone に倒れる（.git はディレクトリ）。
        remote = self._make_remote(name="fb")
        dest = str(self.tmp / "fb-dest")
        with mock.patch.object(km, "ensure_cache", return_value=None):
            km._clone_repo_shallow(remote, "main", dest)
        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))

    def test_clone_repo_shallow_raises_on_total_failure(self):
        # cache もフォールバック clone も失敗するなら RuntimeError（呼び出し側で全 NG 扱い）。
        with mock.patch.object(km, "ensure_cache", return_value=None):
            with self.assertRaises(RuntimeError):
                km._clone_repo_shallow("/no/such/repo.git", "main", str(self.tmp / "none"))

    def test_missing_target_branch_is_ng_not_silent_default(self):
        # 明示した target ブランチが存在しないなら NG（RuntimeError）。既定ブランチへ無言フォールバック
        # して「成果の無い場所で偽 PASS」しないこと（worktree 化で壊しやすい不変条件の回帰防止）。
        remote = self._make_remote(name="tgt")
        with self.assertRaises(RuntimeError):
            km._clone_repo_shallow(remote, "nonexistent-target", str(self.tmp / "wt"))

    def test_explicit_branch_checks_out_that_branch(self):
        # 実在する非既定ブランチを指定したら、その内容で worktree ができる（target 伝搬が効く）。
        remote = self._make_remote(name="tgt2")
        subprocess.run(["git", "-C", remote, "checkout", "-q", "-b", "feature"], check=True)
        (Path(remote) / "only_on_feature.txt").write_text("x")
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "feat"], check=True)
        subprocess.run(["git", "-C", remote, "checkout", "-q", "main"], check=True)
        dest = str(self.tmp / "wtf" / "repo")
        km._clone_repo_shallow(remote, "feature", dest)
        self.assertTrue(os.path.exists(os.path.join(dest, "only_on_feature.txt")))


class TestStateGitSync(unittest.TestCase):
    """状態の git 保存・共有（state_git）: ワーク内容を共有リポジトリへ双方向同期する。
    リモート負荷の律速（interval）・多重コミッタ（他プログラムの同一リポジトリへのコミット）・
    3-way 裁定（人の入力はリモート優先/機械状態はローカル優先）・一時状態の除外を検証する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        km._STATE_GITS.clear()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        # 既定ブランチ名に依存しない: state_git_branch（main）へ HEAD を向けて clone が追従するように
        subprocess.run(["git", "-C", str(self.remote), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True)

    def _cfg(self, **kw):
        proot = self.tmp / "c" / "projects" / "default"
        base = dict(backlog=proot / "backlog", policy=proot / "policy.md",
                    decisions=proot / "decisions", journal=proot / "journal.md",
                    needs=proot / "needs", workdir=self.tmp, bus=proot / "bus",
                    inbox=proot / "inbox",
                    planner="none", flow_planner="stub", executor="stub", dry_run=True,
                    state_git=str(self.remote), state_git_subdir="kp",
                    state_git_interval=0.0)
        base.update(kw)
        cfg = km.Config(**base)
        km.ensure_dirs(cfg)
        return cfg

    def _other(self, name="other") -> Path:
        """「他のプログラム」役: 同一リポジトリを普通に clone して commit/push するクローン。"""
        d = self.tmp / name
        subprocess.run(["git", "clone", "-q", str(self.remote), str(d)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(d), "config", "user.email", "other@test"], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.name", "other"], check=True)
        return d

    @staticmethod
    def _commit_push(d: Path, msg="other"):
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", msg], check=True)
        subprocess.run(["git", "-C", str(d), "push", "-q", "-u", "origin", "main"],
                       check=True, capture_output=True)

    @staticmethod
    def _pull(d: Path):
        subprocess.run(["git", "-C", str(d), "pull", "-q", "--rebase", "origin", "main"],
                       check=True, capture_output=True)

    def test_export_pushes_state_under_subdir(self):
        cfg = self._cfg()
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        got = self._other("check")
        self.assertTrue((got / "kp" / "projects" / "default" / "backlog" / "T1.md").exists())

    def test_import_instruction_drop_and_consumption_propagates(self):
        cfg = self._cfg()
        km.state_sync(cfg, force=True)                       # 初期化（ブランチ作成）
        other = self._other()
        cmd = other / "kp" / "projects" / "default" / "commands" / "ok.json"
        cmd.parent.mkdir(parents=True, exist_ok=True)
        cmd.write_text('{"command": "approve", "id": "T1"}', encoding="utf-8")
        self._commit_push(other, "viewer: approve")
        km.state_sync(cfg, force=True)                       # 指示が取り込まれる
        local_cmd = km.commands_dir(cfg) / "ok.json"
        self.assertTrue(local_cmd.exists())
        local_cmd.unlink()                                   # 本体が消化して消した体
        km.state_sync(cfg, force=True)                       # 消化（削除）がリモートへ伝播
        self._pull(other)
        self.assertFalse(cmd.exists())

    def test_conflict_human_input_prefers_remote(self):
        cfg = self._cfg()
        nf = cfg.needs / "T1.md"
        nf.write_text("machine\n", encoding="utf-8")
        km.state_sync(cfg, force=True)
        other = self._other()
        rn = other / "kp" / "projects" / "default" / "needs" / "T1.md"
        rn.write_text("human answer\n", encoding="utf-8")    # 人がリモートで記入
        self._commit_push(other, "human feedback")
        nf.write_text("machine rewrite\n", encoding="utf-8")  # 同時にローカルも変更
        km.state_sync(cfg, force=True)
        self.assertEqual(nf.read_text(encoding="utf-8"), "human answer\n")

    def test_conflict_repos_registry_prefers_remote(self):
        # repos.{json,yaml,yml} は人が書くレジストリ（charter ## repos の互換入力）なので
        # policy.md / charter.md と同じくリモート優先（viewer 側の編集を取りこぼさない）。
        cfg = self._cfg()
        rf = cfg.backlog.parent / "repos.json"
        rf.write_text('{"app": {"url": "git@h:t/a.git"}}\n', encoding="utf-8")
        km.state_sync(cfg, force=True)
        other = self._other()
        rr = other / "kp" / "projects" / "default" / "repos.json"
        rr.write_text('{"app": {"url": "git@h:t/a.git", "base": "main"}}\n', encoding="utf-8")
        self._commit_push(other, "viewer: edit repos")
        rf.write_text('{"app": {"url": "git@h:t/a.git", "base": "dev"}}\n', encoding="utf-8")
        km.state_sync(cfg, force=True)
        self.assertIn('"base": "main"', rf.read_text(encoding="utf-8"))

    def test_conflict_machine_state_prefers_local(self):
        cfg = self._cfg()
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        other = self._other()
        rb = other / "kp" / "projects" / "default" / "backlog" / "T1.md"
        rb.write_text("remote edit\n", encoding="utf-8")
        self._commit_push(other, "remote edit")
        local = cfg.backlog / "T1.md"
        local.write_text("local truth\n", encoding="utf-8")
        km.state_sync(cfg, force=True)
        self.assertEqual(local.read_text(encoding="utf-8"), "local truth\n")
        self._pull(other)
        self.assertEqual(rb.read_text(encoding="utf-8"), "local truth\n")

    def test_concurrent_committer_is_not_clobbered(self):
        # 他プログラムが（我々の pull の後に）同一リポジトリへ push しても、push 競合を
        # pull --rebase で吸収して自分の変更を反映し、相手のコミットも壊さない。
        cfg = self._cfg(state_git_interval=3600.0)
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        other = self._other()
        (other / "unrelated.txt").write_text("theirs\n", encoding="utf-8")
        self._commit_push(other, "other program commit")
        (cfg.backlog / "T2.md").write_text("## T2: x\n- status: ready\n", encoding="utf-8")
        km.state_sync(cfg, force=True)   # interval 内 → pull せず push → 非 FF → rebase 再試行
        self._pull(other)
        self.assertTrue((other / "unrelated.txt").exists())
        self.assertTrue((other / "kp" / "projects" / "default" / "backlog" / "T2.md").exists())

    def test_transient_state_is_excluded(self):
        cfg = self._cfg()
        mkb(cfg.backlog.parent, "T1")
        (cfg.bus / "runs").mkdir(parents=True, exist_ok=True)
        (cfg.bus / "runs" / "r1.json").write_text("{}", encoding="utf-8")
        claims = cfg.backlog.parent / "claims"
        claims.mkdir(parents=True, exist_ok=True)
        (claims / "T1.lock").write_text("pid", encoding="utf-8")
        km.state_sync(cfg, force=True)
        got = self._other("check")
        proot = got / "kp" / "projects" / "default"
        self.assertTrue((proot / "backlog" / "T1.md").exists())
        self.assertFalse((proot / "bus").exists())
        self.assertFalse((proot / "claims").exists())

    def test_interval_rate_limits_remote_fetch(self):
        cfg = self._cfg(state_git_interval=3600.0)
        km.state_sync(cfg, force=True)                       # 初回は必ず同期（ブランチ作成）
        other = self._other()
        drop = other / "kp" / "projects" / "default" / "inbox" / "task.json"
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_text('{"title": "x", "verify": "true"}', encoding="utf-8")
        self._commit_push(other, "drop")
        km.state_sync(cfg)                                   # interval 内 → fetch しない（負荷律速）
        self.assertFalse((cfg.inbox / "task.json").exists())
        sg = km.state_git_for(cfg)
        sg._last_remote = 0.0                                # interval 経過を模擬
        km.state_sync(cfg)
        self.assertTrue((cfg.inbox / "task.json").exists())

    def test_run_loop_syncs_state(self):
        # run_loop の入口で指示を取り込み、出口でパスの結果（journal 等）を共有側へ押し出す。
        cfg = self._cfg()
        result = km.run_loop(cfg)
        self.assertEqual(result["reason"], km.REASON_DRAINED)
        got = self._other("check")
        self.assertTrue((got / "kp" / "projects" / "default" / "journal.md").exists())

    def test_disabled_without_state_git(self):
        cfg = self._cfg(state_git=None)
        km.state_sync(cfg, force=True)                       # 何もしない（クローンも作らない）
        self.assertFalse((self.tmp / "c" / ".state-git").exists())

    def test_sync_failure_does_not_kill_loop(self):
        cfg = self._cfg(state_git=str(self.tmp / "no-such-remote.git"))
        km.state_sync(cfg, force=True)                       # 不通でも例外を漏らさない
        self.assertIn("state-git 同期失敗", cfg.journal.read_text(encoding="utf-8"))

    def test_dot_prefixed_subdir_works(self):
        # state_git_subdir はドット始まり（.kiro-projects 等）でも同期できる（推奨は非ドットだが、
        # 他プロセスの成果物と同居するリポジトリで隠したい構成をサポートする）。
        cfg = self._cfg(state_git_subdir=".kiro-projects")
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        got = self._other("check")
        self.assertTrue(
            (got / ".kiro-projects" / "projects" / "default" / "backlog" / "T1.md").exists())

    def test_clone_is_reused_across_syncs(self):
        cfg = self._cfg()
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        clone = self.tmp / "c" / ".state-git"
        marker = subprocess.run(["git", "-C", str(clone), "config", "--get",
                                 km.STATE_GIT_MARKER], capture_output=True, text=True)
        self.assertEqual(marker.stdout.strip(), "1")
        km._STATE_GITS.clear()                               # プロセス再起動を模擬 → 再クローンせず再利用
        (cfg.backlog / "T2.md").write_text("## T2: y\n- status: ready\n", encoding="utf-8")
        km.state_sync(cfg, force=True)
        got = self._other("check")
        self.assertTrue((got / "kp" / "projects" / "default" / "backlog" / "T2.md").exists())


class TestStateGitPerProject(unittest.TestCase):
    """プロジェクト単位で保存先リポジトリを分ける（state_git_projects）。default は個人リポジトリ、
    他プロジェクトは固有リポジトリへ、各々そのプロジェクトの subtree だけをスコープして同期する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        km._STATE_GITS.clear()
        self.personal = self._bare("personal.git")   # default（個人）
        self.team = self._bare("team-alpha.git")      # alpha（プロジェクト固有・共有）

    def _bare(self, name: str) -> Path:
        r = self.tmp / name
        subprocess.run(["git", "init", "-q", "--bare", str(r)], check=True)
        subprocess.run(["git", "-C", str(r), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        return r

    def _cfg(self, project: str, **kw):
        proot = self.tmp / "c" / "projects" / project
        base = dict(project_name=project,
                    backlog=proot / "backlog", policy=proot / "policy.md",
                    decisions=proot / "decisions", journal=proot / "journal.md",
                    needs=proot / "needs", workdir=self.tmp, bus=proot / "bus",
                    inbox=proot / "inbox", charter=proot / "charter.md",
                    planner="none", flow_planner="stub", executor="stub", dry_run=True,
                    state_git=str(self.personal), state_git_subdir="kp", state_git_interval=0.0,
                    state_git_projects={"alpha": str(self.team)})
        base.update(kw)
        cfg = km.Config(**base)
        km.ensure_dirs(cfg)
        return cfg

    def _check(self, remote: Path, name: str) -> Path:
        d = self.tmp / f"chk-{name}-{remote.stem}"
        subprocess.run(["git", "clone", "-q", str(remote), str(d)], check=True, capture_output=True)
        return d

    def test_mapped_project_goes_to_its_own_repo(self):
        cfg = self._cfg("alpha")
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        got = self._check(self.team, "alpha")
        self.assertTrue((got / "kp" / "projects" / "alpha" / "backlog" / "T1.md").exists())
        # 個人リポジトリには入らない（プロジェクト固有リポジトリへ分離されている）
        personal = self._check(self.personal, "alpha")
        self.assertFalse((personal / "kp" / "projects" / "alpha").exists())

    def test_unmapped_default_falls_to_personal_repo(self):
        cfg = self._cfg("default")
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        got = self._check(self.personal, "default")
        self.assertTrue((got / "kp" / "projects" / "default" / "backlog" / "T1.md").exists())
        # チームリポジトリには default は入らない
        team = self._check(self.team, "default")
        self.assertFalse((team / "kp" / "projects" / "default").exists())

    def test_scoped_to_own_subtree_only(self):
        # alpha の同期は alpha の subtree だけを見る（兄弟プロジェクト default のファイルを引かない）。
        default_cfg = self._cfg("default")
        mkb(default_cfg.backlog.parent, "D1")         # 兄弟プロジェクトの実体をディスク上に作る
        cfg = self._cfg("alpha")
        mkb(cfg.backlog.parent, "A1")
        km.state_sync(cfg, force=True)
        got = self._check(self.team, "scope")
        self.assertTrue((got / "kp" / "projects" / "alpha" / "backlog" / "A1.md").exists())
        self.assertFalse((got / "kp" / "projects" / "default").exists())

    def test_dict_spec_overrides_branch_and_subdir(self):
        cfg = self._cfg("alpha", state_git_projects={
            "alpha": {"remote": str(self.team), "subdir": "shared"}})
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        got = self._check(self.team, "dict")
        self.assertTrue((got / "shared" / "projects" / "alpha" / "backlog" / "T1.md").exists())

    def test_member_drives_via_remote_input(self):
        # プロジェクトメンバーが viewer 相当でチームリポジトリへ指示（commands）をドロップ →
        # 本体（alpha を回す側）が取り込む。共有リポジトリ越しの「誰でもドライブ」を検証。
        cfg = self._cfg("alpha")
        km.state_sync(cfg, force=True)                # ブランチ作成
        member = self._check(self.team, "member")
        subprocess.run(["git", "-C", str(member), "config", "user.email", "m@t"], check=True)
        subprocess.run(["git", "-C", str(member), "config", "user.name", "m"], check=True)
        cmd = member / "kp" / "projects" / "alpha" / "commands" / "ok.json"
        cmd.parent.mkdir(parents=True, exist_ok=True)
        cmd.write_text('{"command": "approve", "id": "T1"}', encoding="utf-8")
        subprocess.run(["git", "-C", str(member), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(member), "commit", "-qm", "member approve"], check=True)
        subprocess.run(["git", "-C", str(member), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        km.state_sync(cfg, force=True)
        self.assertTrue((km.commands_dir(cfg) / "ok.json").exists())

    def test_disabled_when_unmapped_and_no_personal(self):
        cfg = self._cfg("beta", state_git=None)       # beta は未記載・個人リポジトリも無し
        km.state_sync(cfg, force=True)
        self.assertIsNone(km.state_git_for(cfg))

    def test_project_flow_remote_resolves_repo(self):
        # 各プロジェクトの kiro-flow バスの鏡写し先＝そのプロジェクトのリポジトリ。
        self.assertEqual(km.project_flow_remote(self._cfg("alpha"))[0], str(self.team))
        self.assertEqual(km.project_flow_remote(self._cfg("default"))[0], str(self.personal))

    def test_project_flow_remote_none_for_shared_legacy_or_no_repo(self):
        shared = self.tmp / "shared-bus"
        self.assertIsNone(km.project_flow_remote(self._cfg("alpha", bus=shared, shared_bus=True)))
        self.assertIsNone(km.project_flow_remote(self._cfg("alpha", state_git_projects={})))
        self.assertIsNone(km.project_flow_remote(self._cfg("beta", state_git=None)))

    def test_flow_daemon_cmd_injects_bus_state_git_and_budget(self):
        cfg = self._cfg("alpha", executor="stub")
        cmd = km.flow_daemon_cmd(cfg, 3)
        self.assertIn(str(cfg.bus), cmd)
        self.assertIn("--state-git", cmd)
        self.assertIn(str(self.team), cmd)
        self.assertIn("daemon", cmd)
        self.assertEqual(cmd[cmd.index("--max-workers") + 1], "3")
        self.assertEqual(cmd[cmd.index("--executor") + 1], "stub")

    def test_flow_daemon_cmd_does_not_inject_state_git_subdir(self):
        # state_git サブディレクトリは kiro-flow の設定（flow_config / 既定 kiro-flow）に委ね、
        # kiro-projects は CLI で個別注入しない（kiro-projects 側に kiro-flow 設定を増やさない方針）。
        cfg = self._cfg("alpha", executor="stub")
        cmd = km.flow_daemon_cmd(cfg, 1)
        self.assertNotIn("--state-git-subdir", cmd)

    def test_flow_daemon_cmd_passes_flow_config(self):
        # kiro-flow の設定は flow_config を --config で渡して集約する（個別注入しない）。
        cfg = self._cfg("alpha", executor="stub", flow_config="/etc/kiro-flow.yaml")
        cmd = km.flow_daemon_cmd(cfg, 1)
        self.assertIn("--config", cmd)
        self.assertTrue(cmd[cmd.index("--config") + 1].endswith("kiro-flow.yaml"))

    def test_ensure_flow_daemon_spawns_when_managed_and_absent(self):
        cfg = self._cfg("alpha", manage_flow_daemon=True)
        with mock.patch.object(km, "daemon_running", return_value=False), \
             mock.patch.object(km.subprocess, "Popen") as popen:
            started = km.ensure_flow_daemon(cfg, 2)
        self.assertTrue(started)
        popen.assert_called_once()
        spawned = popen.call_args[0][0]
        self.assertIn("--state-git", spawned)
        self.assertIn(str(self.team), spawned)

    def test_ensure_flow_daemon_idempotent_when_running(self):
        cfg = self._cfg("alpha", manage_flow_daemon=True)
        with mock.patch.object(km, "daemon_running", return_value=True), \
             mock.patch.object(km.subprocess, "Popen") as popen:
            self.assertFalse(km.ensure_flow_daemon(cfg, 2))
        popen.assert_not_called()

    def test_ensure_flow_daemon_noop_when_unmanaged_or_shared(self):
        with mock.patch.object(km.subprocess, "Popen") as popen:
            self.assertFalse(km.ensure_flow_daemon(self._cfg("alpha", manage_flow_daemon=False), 2))
            self.assertFalse(km.ensure_flow_daemon(
                self._cfg("alpha", manage_flow_daemon=True,
                          bus=self.tmp / "shared", shared_bus=True), 2))
        popen.assert_not_called()

    def test_ensure_flow_daemons_divides_budget_by_targets(self):
        a = self._cfg("alpha", manage_flow_daemon=True, flow_max_workers=6)
        cfgs = [km.project_cfg(a, n) for n in ("alpha", "default")]   # 両方が対象 → 6//2=3
        seen = []
        with mock.patch.object(km, "ensure_flow_daemon", side_effect=lambda c, b: seen.append(b)):
            km.ensure_flow_daemons(a, cfgs)
        self.assertEqual(seen, [3, 3])

    def test_doctor_warns_when_daemon_absent(self):
        cfg = self._cfg("alpha")
        with mock.patch.object(km, "daemon_running", return_value=False):
            fs = km.doctor_flow_bus_coverage_findings(cfg)
        self.assertTrue(any("不在" in f["title"] and "alpha" in f["title"] for f in fs))
        self.assertTrue(all(f["severity"] == "warn" for f in fs))

    def test_doctor_coverage_skips_shared_bus_and_legacy(self):
        shared = self.tmp / "shared-bus"
        self.assertEqual(km.doctor_flow_bus_coverage_findings(
            self._cfg("alpha", bus=shared, shared_bus=True)), [])
        self.assertEqual(km.doctor_flow_bus_coverage_findings(
            self._cfg("alpha", state_git_projects={})), [])


class TestAsyncOffload(unittest.TestCase):
    """非ブロッキング委譲（act_async）: daemon/remote への submit で待たず offloaded にし、次パスで
    ポーリングして終端した run だけ settle する（gitlab 等の長期委譲でループを塞がない）。"""

    def _cfg(self, d, **kw):
        return cfg_for(d, dry_run=False, act_async=True, executor="gitlab", **kw)

    def _offloaded(self, d, tid, run_id, verify="true"):
        bd = d / "backlog"
        bd.mkdir(parents=True, exist_ok=True)
        (bd / f"{tid}.md").write_text(
            f"## {tid}: {tid}\n- status: offloaded\n- source: human\n- verify: `{verify}`\n"
            f"- retries: 0\n- flow_run: {run_id}\n- flow_loc: daemon\n", encoding="utf-8")

    def test_pending_act_marks_task_offloaded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = self._cfg(d)
            with mock.patch.object(km, "_flow_result_once", return_value=(False, False, "")):
                km.run_loop(cfg, act=lambda t, c, loc: (km._Pending("run-T1"), "実行中"))
            t = km._load_task_file(cfg, "T1")
            self.assertEqual(t.norm_status(), "offloaded")
            self.assertEqual(t.get("flow_run"), "run-T1")

    def test_reap_settles_terminal_run_to_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded(d, "T1", "run-T1", verify="true")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_flow_result_once", return_value=(True, True, "done")):
                deltas = km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertEqual(deltas["settled"], 1)
            self.assertEqual(deltas["archived"], 1)            # verify PASS → done → archive
            self.assertIsNone(km._load_task_file(cfg, "T1"))   # backlog から消えた（archive 済み）

    def test_reap_leaves_nonterminal_offloaded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded(d, "T1", "run-T1")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_flow_result_once", return_value=(False, False, "")):
                deltas = km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertEqual(deltas["settled"], 0)
            self.assertEqual(km._load_task_file(cfg, "T1").norm_status(), "offloaded")

    def test_reap_failed_run_does_not_mark_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded(d, "T1", "run-T1", verify="false")   # verify も失敗
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = km.load_tasks(cfg.backlog)
            with mock.patch.object(km, "_flow_result_once", return_value=(True, False, "failed")):
                km._reap_offloaded(cfg, tasks, km.Policy(), {}, {}, 0, 20)
            self.assertNotEqual(km._load_task_file(cfg, "T1").norm_status(), "done")

    def test_has_work_true_for_offloaded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._offloaded(d, "T1", "run-T1")
            self.assertTrue(km.has_work(self._cfg(d)))

    def test_act_via_kiro_flow_offloads_when_async(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.load_tasks(cfg.backlog)[0]
            with mock.patch.object(km, "daemon_running", return_value=True), \
                 mock.patch.object(km, "_flow_result_once", return_value=(False, False, "")), \
                 mock.patch.object(km.subprocess, "run", return_value=types.SimpleNamespace(
                     returncode=0, stdout="run-T1\n", stderr="")):
                status, _ = km.act_via_kiro_flow(t, cfg, "daemon")
            self.assertIsInstance(status, km._Pending)

    def test_sync_path_unaffected_when_async_off(self):
        # act_async 未指定なら従来どおり待つ（_act_submit）。daemon_running False → _act_run（同期）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = cfg_for(d, dry_run=False, executor="stub")   # act_async=False（既定）
            res = km.run_loop(cfg, act=lambda t, c, loc: (True, "ok"))
            self.assertEqual(res["reason"], km.REASON_DRAINED)
            self.assertGreaterEqual(res["archived"], 1)        # done → archive（従来どおり同期で確定）
            self.assertIsNone(km._load_task_file(cfg, "T1"))   # backlog から消えた（archive 済み）


class FeedbackReductionTests(unittest.TestCase):
    """ユーザーの決定・指摘を全体へ還元する仕組み（gitlab 却下コメントの learn 化・蒸留）と
    verify 品質改善（恒真式スクリーン・テンプレ拡充）。"""

    def test_distill_learn_generalizes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            got = km.distill_learn(cfg, "ログイン画面の e2e",
                                   "実サーバでなく localhost で検証していてダメ",
                                   kiro_run=lambda p, m: "e2e/統合テスト系 :: 実サーバ配備で実施すること")
            self.assertEqual(got, ("e2e/統合テスト系", "実サーバ配備で実施すること"))

    def test_distill_learn_verbatim_fallback_on_error(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            def boom(p, m): raise RuntimeError("no kiro-cli")
            title, guide = km.distill_learn(cfg, "T", "実サーバで検証", kiro_run=boom)
            self.assertEqual(title, "T")
            self.assertIn("実サーバで検証", guide)

    def test_distill_learn_off_returns_verbatim(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), distill_learn=False)
            got = km.distill_learn(cfg, "T", "生の指摘",
                                   kiro_run=lambda p, m: self.fail("蒸留された"))
            self.assertEqual(got, ("T", "生の指摘"))

    def test_verify_degenerate_screen(self):
        for bad in ("true", ":", "echo done", "test 1 = 1", "exit 0", ""):
            self.assertTrue(km._verify_is_degenerate(bad), bad)
        for good in ("grep -q 概要 README.md", "pytest -q", "test -f x && grep -q y z"):
            self.assertFalse(km._verify_is_degenerate(good), good)

    def test_synth_rejects_degenerate_output(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            self.assertEqual(km.synth_verify(cfg, "T", "何かする",
                                             kiro_run=lambda p, m: "true"), "")
            self.assertEqual(km.synth_verify(cfg, "T", "概要見出し",
                             kiro_run=lambda p, m: "grep -q 概要 README.md"),
                             "grep -q 概要 README.md")

    def test_expand_verify_template_additions(self):
        self.assertEqual(km.expand_verify_template("test-passes :: pytest -q"), "pytest -q")
        self.assertEqual(km.expand_verify_template("builds :: make"), "make")
        self.assertEqual(km.expand_verify_template("exit-zero :: ./run.sh"), "./run.sh")
        cmd = km.expand_verify_template("endpoint-returns :: http://x/health :: 200")
        self.assertIn("http_code", cmd)
        self.assertIn("200", cmd)
        self.assertIn("http://x/health", cmd)

    def test_reject_guidance_captured_as_learn(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="pytest -q", title="ログイン e2e")
            cfg = cfg_for(d, executor="gitlab")
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_reject_guidance",
                                   return_value="実サーバで検証すること"), \
                 mock.patch.object(km, "distill_learn",
                                   return_value=("e2e 系", "実サーバ配備で実施")):
                km._settle_failure(cfg, task, "NG", 1, "ev", {}, location="local")
            dr = (cfg.decisions / "T1.md").read_text(encoding="utf-8")
            self.assertIn("- learn: e2e 系 :: 実サーバ配備で実施", dr)
            self.assertIn("gitlab-reject", dr)

    def test_reject_learn_suppressed_when_capture_off(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T2", verify="pytest -q", title="x")
            cfg = cfg_for(d, executor="gitlab", learn_capture=False)
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_reject_guidance", return_value="直して"):
                km._settle_failure(cfg, task, "NG", 1, "ev", {}, location="local")
            self.assertFalse((cfg.decisions / "T2.md").exists())

    def test_approve_notes_captured_as_learn(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T3", verify="true", title="ログイン e2e")
            cfg = cfg_for(d, executor="gitlab")
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_result_notes",
                                   return_value=[{"body": "実サーバで検証してOK", "note_id": 1}]), \
                 mock.patch.object(km, "distill_learn",
                                   return_value=("e2e 系", "実サーバ配備で実施")):
                km.capture_approve_learn(cfg, task, "local")
            dr = (cfg.decisions / "T3.md").read_text(encoding="utf-8")
            self.assertIn("gitlab-approve", dr)
            self.assertIn("- learn: e2e 系 :: 実サーバ配備で実施", dr)

    def test_detect_repo_context(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "package.json").write_text('{"scripts": {"test": "jest", "build": "tsc"}}')
            (d / "Makefile").write_text("smoke:\n\techo ok\nlint:\n\ttrue\n")
            (d / "tests").mkdir()
            ctx = km.detect_repo_context(d)
            self.assertIn("package.json", ctx)
            self.assertIn("test", ctx)
            self.assertIn("Makefile", ctx)
            self.assertIn("smoke", ctx)
            self.assertIn("pytest", ctx)

    def test_synth_injects_hint_and_repo_context(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "backlog").mkdir()
            (d / "decisions").mkdir()
            # 過去の類似タスクの learn（find_learned_resolution が拾う）
            (d / "decisions" / "old.md").write_text(
                "## DR1  2026-01-01  actor: u\n- learn: ログイン e2e :: 実サーバ配備で検証すること\n\n",
                encoding="utf-8")
            (d / "package.json").write_text('{"scripts": {"e2e": "playwright test"}}')
            mkb(d, "T1", status="ready", verify="", title="ログイン e2e", source="human")
            # accept を付けて合成経路に入れる
            (d / "backlog" / "T1.md").write_text(
                "## T1: ログイン e2e\n- status: ready\n- source: human\n- verify: \n"
                "- accept: ログインの e2e が通る\n", encoding="utf-8")
            cfg = cfg_for(d, workdir=d)
            task = km.load_tasks(d / "backlog")[0]
            seen = {}
            def fake_kiro(prompt, model):
                seen["prompt"] = prompt
                return "npx playwright test"
            km.ensure_verify(cfg, task, kiro_run=fake_kiro)
            self.assertIn("実サーバ配備で検証すること", seen["prompt"])   # learn ヒント注入
            self.assertIn("package.json", seen["prompt"])                # リポジトリ文脈注入
            self.assertEqual(task.verify, "npx playwright test")

    def _seed_reject_decision(self, cfg, tid, title):
        cfg.decisions.mkdir(parents=True, exist_ok=True)
        (cfg.decisions / f"{tid}.md").write_text(
            f"## DR-0001  2026-01-01  actor: gitlab\n"
            f"- context : {tid}（{title}）が gitlab で却下\n- action  : gitlab-reject\n"
            f"- reason  : x\n- affects : {tid}\n- learn: e2e 系 :: 実サーバで\n\n", encoding="utf-8")

    def test_count_gitlab_reject_recur(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._seed_reject_decision(cfg, "A", "ログイン e2e A")
            self._seed_reject_decision(cfg, "B", "無関係な掃除タスク")
            task = km.Task(id="C", title="ログイン e2e C")
            self.assertEqual(km.count_gitlab_reject_recur(cfg, task), 1)  # A のみ類似

    def test_reject_recurrence_escalates_to_human(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "C", verify="pytest -q", title="ログイン e2e C")
            cfg = cfg_for(d, executor="gitlab", reject_recur=2)
            self._seed_reject_decision(cfg, "A", "ログイン e2e A")  # 既に 1 件の同種却下
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_reject_guidance", return_value="また命名が違う"), \
                 mock.patch.object(km, "distill_learn", return_value=("e2e 系", "実サーバで")):
                km._settle_failure(cfg, task, "NG", 1, "ev", {}, location="local")
            self.assertEqual(task.norm_status(), "blocked")            # 系の再考で人へ
            self.assertTrue((d / "needs" / "C.md").exists())

    def test_reject_recurrence_disabled_requeues(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "C", verify="pytest -q", title="ログイン e2e C")
            cfg = cfg_for(d, executor="gitlab", reject_recur=0)     # 無効
            self._seed_reject_decision(cfg, "A", "ログイン e2e A")
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_reject_guidance", return_value="直して"), \
                 mock.patch.object(km, "distill_learn", return_value=("t", "g")):
                km._settle_failure(cfg, task, "NG", 1, "ev", {}, location="local")
            self.assertEqual(task.status, "ready")                    # silent 積み直し

    # --- red-green（変更を弁別しない合成 verify を実行で弾く）---
    def _git_repo(self, d: Path, fname="f", content="old"):
        import subprocess as sp
        sp.run(["git", "init", "-q", str(d)], check=True)
        sp.run(["git", "-C", str(d), "config", "user.email", "t@t"], check=True)
        sp.run(["git", "-C", str(d), "config", "user.name", "t"], check=True)
        (d / fname).write_text(content)
        sp.run(["git", "-C", str(d), "add", "-A"], check=True)
        sp.run(["git", "-C", str(d), "commit", "-qm", "base"], check=True)
        return km._git_out(d, "rev-parse", "HEAD").strip()

    def test_redgreen_passes_for_discriminating_verify(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = self._git_repo(d, content="old")
            (d / "f").write_text("new")                 # act 後の作業ツリー
            cfg = cfg_for(d, workdir=d)
            task = km.Task(id="T", title="x", verify="grep -q new f")
            task.extra.append(("verify_source", "synth"))
            # base では 'new' が無い＝fail、post では pass ⇒ 弁別している＝undiscriminating False
            self.assertFalse(km.verify_undiscriminating(cfg, task, d, False,
                                                        (base, frozenset()), None))

    def test_redgreen_flags_stale_verify(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = self._git_repo(d, content="old")
            (d / "f").write_text("old changed but still has old")
            cfg = cfg_for(d, workdir=d)
            task = km.Task(id="T", title="x", verify="grep -q old f")  # base でも PASS
            task.extra.append(("verify_source", "synth"))
            self.assertTrue(km.verify_undiscriminating(cfg, task, d, False,
                                                       (base, frozenset()), None))

    def test_redgreen_off_and_human_verify_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = self._git_repo(d, content="old")
            cfg_off = cfg_for(d, workdir=d, verify_validate="off")
            task = km.Task(id="T", title="x", verify="grep -q old f")
            task.extra.append(("verify_source", "synth"))
            self.assertFalse(km.verify_undiscriminating(cfg_off, task, d, False,
                                                        (base, frozenset()), None))
            # synth ポリシーは人が書いた verify（source!=synth/template）を検証しない
            cfg = cfg_for(d, workdir=d)
            human = km.Task(id="T2", title="x", verify="grep -q old f")
            self.assertFalse(km.verify_undiscriminating(cfg, human, d, False,
                                                        (base, frozenset()), None))


if __name__ == "__main__":
    unittest.main()
