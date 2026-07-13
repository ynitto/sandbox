"""kiro-project の単体テスト（標準ライブラリ unittest）。

案件毎ファイル（backlog/<id>.md）・done でファイル削除・watch 常駐・フィードバック往復・
案件毎の needs/decisions を、kiro-flow を呼ばずに検証する。kiro-flow stub 統合も含む。

    python -m unittest discover -s tools/kiro-project/tests
"""
import contextlib
import dataclasses
import importlib.util
import io
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
from datetime import datetime, timedelta, timezone
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

# 開発者の cwd の設定ファイル（./kiro-project.yaml / ./.kiro/kiro-project.yaml）がテストへ
# 漏れるのを防ぐため、中立な一時 cwd で走らせる。リポジトリ直下で実行すると root=. の設定を
# 拾い、リポジトリ自体が状態リポジトリ（direct state-git）とみなされて **テストが実リポジトリへ
# コミット/push する**事故になる（2026-07-11 に実際に発生）。テストは絶対パスだけを使うので
# cwd に依存しない。
os.chdir(tempfile.mkdtemp(prefix="kp-tests-cwd-"))

_PKG = Path(__file__).resolve().parent.parent / "kiro_project"
_spec = importlib.util.spec_from_file_location(
    "kiro_project", _PKG / "__init__.py", submodule_search_locations=[str(_PKG)])
km = importlib.util.module_from_spec(_spec)
sys.modules["kiro_project"] = km
_spec.loader.exec_module(km)

# 黒箱 CLI e2e が実プロセス起動する薄いエントリポイント（kiro_project/ を起動する shim）。
_MOD = Path(__file__).resolve().parent.parent / "kiro-project.py"


def mkb(d: Path, tid: str, status="ready", verify="true", source="human", title=None, retries=0):
    bd = d / "backlog"
    bd.mkdir(parents=True, exist_ok=True)
    v = f"`{verify}`" if verify else ""
    (bd / f"{tid}.md").write_text(
        f"## {tid}: {title or tid}\n- status: {status}\n- source: {source}\n"
        f"- verify: {v}\n- retries: {retries}\n", encoding="utf-8")


def cfg_for(d: Path, **kw):
    # 既定 plan_review=False / delivery_review=False（従来動作を検証する既存テスト用）。
    # 実行前レビュー（proposed ゲート）の挙動は TestPlanReview が plan_review=True で検証する。
    base = dict(backlog=d / "backlog", policy=d / "policy.md", decisions=d / "decisions",
                journal=d / "journal.md", needs=d / "needs", workdir=d, bus=d / "bus",
                planner="none", flow_planner="stub", executor="stub", dry_run=True,
                plan_review=False, delivery_review=False)
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

    def _dead_pid(self) -> int:
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid                                   # 確実に死んでいる pid

    def test_dead_same_host_owner_is_stolen_without_waiting_ttl(self):
        # kill/クラッシュで死んだ owner のロックは、TTL（既定 41 分）を待たず pid の生死で奪取する。
        # 待たされると、その間そのタスクは誰にも拾われず drained になる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            lock = d / "claims" / "T1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"host": socket.gethostname(), "pid": self._dead_pid(),
                                        "ts": time.time(), "id": "T1"}), encoding="utf-8")  # ts は新鮮
            self.assertTrue(km.claim_task(cfg, t))

    def test_dead_owner_is_stolen_even_when_ttl_is_infinite(self):
        # act_timeout<=0（無制限待ち）は _claim_ttl を inf にする。TTL だけで判定していると
        # 死んだ owner のロックが **永久に** 失効せず、そのタスクは二度と実行されない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.act_timeout = 0
            self.assertEqual(km._claim_ttl(cfg), float("inf"))
            t = self._task(d)
            lock = d / "claims" / "T1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"host": socket.gethostname(), "pid": self._dead_pid(),
                                        "ts": time.time(), "id": "T1"}), encoding="utf-8")
            self.assertTrue(km.claim_task(cfg, t))

    def test_live_owner_is_not_stolen(self):
        # 生きている owner のロックは（ts がいくら古くても）奪わない＝二重実行しない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._task(d)
            lock = d / "claims" / "T1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"host": socket.gethostname(), "pid": os.getpid(),
                                        "ts": 0, "id": "T1"}), encoding="utf-8")   # ts は大昔
            self.assertFalse(km.claim_task(cfg, t))

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
            # kiro-flow/git あり・ディレクトリ作成済み → env/config の致命所見は出ない
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
            mems = list((home / "memory" / "home" / "memories" / "kiro-project").glob("*.md"))
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
            mem = home / "memory" / "home" / "memories" / "kiro-project"
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

    def test_policy_is_not_appended_twice(self):
        # policy は「人の上書き指示」の集合であって履歴ではない。同じ hold を繰り返しても増えない
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.cmd_hold(c, "T1", "止める")
            km.cmd_hold(c, "T1", "もう一度止める")
            self.assertEqual((d / "policy.md").read_text().count("deny: T1"), 1)


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

    def test_watch_ingests_readable_command_immediately(self):
        # 読める指示は watch 中でも即座に取り込む。debounce で先送りすると、has_work が起こした
        # パスで承認が処理されず、そのパスが charter を再評価してマイルストーンを書き直す
        # （承認したのに要対応が復活する）。viewer は .tmp → rename で置くので書きかけは読めない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d, watch=True, debounce=999.0)
            km.ensure_dirs(c)
            f = km.commands_dir(c) / "a.json"
            f.write_text(json.dumps({"command": "approve", "id": "T1"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), ["approve:T1"])
            self.assertFalse(f.exists())                             # 処理したら消える

    def test_watch_debounce_defers_unreadable_command(self):
        # 書きかけ（アトミックに置かれなかった指示）は .err へ飛ばさず静穏化を待つ。
        # 猶予中は has_work も起こさない＝起きたパスは必ずその指示を処理できる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d, watch=True, debounce=999.0)
            km.ensure_dirs(c)
            f = km.commands_dir(c) / "a.json"
            f.write_text('{"command": "appr', encoding="utf-8")      # 書きかけ
            self.assertEqual(km.ingest_commands(c), [])
            self.assertTrue(f.exists())                              # .err にしない（指示を失わない）
            self.assertFalse(km.has_work(c))                         # 読めない指示では起こさない

    def test_approve_drop_does_not_resurrect_milestone(self):
        # 実運用インシデントの再発防止: viewer の「プロジェクトを承認」を押すと commands/ に
        # 指示が落ち、has_work がその場で watch を起こす。かつては ingest_commands が debounce
        # 未経過のその指示を読み飛ばしたため、承認を知らないまま cmd_project が再評価して
        # converged → write_milestone となり、承認直後に「要対応: マイルストーン」が復活していた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("test -f {flag}", 'echo "hellO"'))
            c = cfg_for(d, watch=True, debounce=999.0, max_project_cycles=1)
            km.cmd_project(c, runner=lambda c2: _drained())          # 収束 → milestone が出る
            st = km.load_project_state(c)
            pid = st["id"]
            self.assertEqual(st["status"], km.REASON_PROJECT_CONVERGED)
            self.assertTrue(km.needs_path(c, pid).exists())

            km.commands_dir(c).mkdir(parents=True, exist_ok=True)    # viewer の承認ドロップ
            (km.commands_dir(c) / "viewer-approve.json").write_text(json.dumps(
                {"command": "approve", "id": pid, "reason": "viewer から"}), encoding="utf-8")
            self.assertTrue(km.has_work(c))                          # 置いた直後に watch が起きる

            km.cmd_project(c, runner=lambda c2: _drained())          # その起床パス
            self.assertEqual(km.load_project_state(c)["status"], km.REASON_PROJECT_ACCEPTED)
            self.assertFalse(km.needs_path(c, pid).exists())         # マイルストーンは復活しない


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


class TestAgentCliAndGranularity(unittest.TestCase):
    """agent_cli 切替（kiro-cli / Claude Code）・root 基準のパス解決・バックログ分解粒度。"""

    @staticmethod
    def _resolve(cfg_path=None, **cli):
        ns = types.SimpleNamespace(config=cfg_path, **cli)
        km.resolve_config(ns)
        return ns

    @staticmethod
    def _capture_run():
        calls = {}
        def fake_run(cmd, **kw):
            calls["cmd"] = list(cmd)
            calls["input"] = kw.get("input")
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        return calls, fake_run

    def test_run_kiro_cli_default_kiro_argv(self):
        calls, fake = self._capture_run()
        with mock.patch.object(km, "_AGENT_CLI", "kiro"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake):
            out = km._run_kiro_cli("プロンプト", None)
        self.assertEqual(out, "ok")
        self.assertEqual(calls["cmd"][:4],
                         ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"])
        self.assertEqual(calls["cmd"][-1], "プロンプト")   # 従来どおり argv 渡し
        self.assertIsNone(calls["input"])

    def test_run_kiro_cli_claude_uses_stdin(self):
        calls, fake = self._capture_run()
        with mock.patch.object(km, "_AGENT_CLI", "claude"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake):
            out = km._run_kiro_cli("プロンプト", "claude-sonnet")
        self.assertEqual(out, "ok")
        self.assertEqual(calls["cmd"][0], "claude")
        self.assertIn("-p", calls["cmd"])
        self.assertIn("--model", calls["cmd"])
        self.assertEqual(calls["input"], "プロンプト")     # stdin 渡し
        self.assertNotIn("プロンプト", calls["cmd"])       # argv には載せない

    def test_run_kiro_cli_copilot_uses_prompt_flag(self):
        calls, fake = self._capture_run()
        with mock.patch.object(km, "_AGENT_CLI", "copilot"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake):
            out = km._run_kiro_cli("プロンプト", "gpt-5")
        self.assertEqual(out, "ok")
        self.assertEqual(calls["cmd"][0], "copilot")
        self.assertIn("-s", calls["cmd"])                  # 応答本文のみ
        self.assertIn("--allow-all-tools", calls["cmd"])   # 非対話モードの必須フラグ
        i = calls["cmd"].index("-p")
        self.assertEqual(calls["cmd"][i + 1], "プロンプト")  # -p の引数で渡す
        self.assertIn("--model", calls["cmd"])
        self.assertIsNone(calls["input"])

    def test_run_kiro_cli_codex_uses_exec_and_last_message_file(self):
        calls = {}
        def fake_run(cmd, **kw):
            calls["cmd"] = list(cmd)
            calls["input"] = kw.get("input")
            # codex は最終応答を --output-last-message のファイルへ書く
            i = cmd.index("--output-last-message")
            with open(cmd[i + 1], "w", encoding="utf-8") as f:
                f.write("最終応答")
            return types.SimpleNamespace(returncode=0, stdout="イベントログ...", stderr="")
        with mock.patch.object(km, "_AGENT_CLI", "codex"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            out = km._run_kiro_cli("プロンプト", "gpt-5-codex")
        self.assertEqual(out, "最終応答")                  # stdout のログではなくファイルの中身
        self.assertEqual(calls["cmd"][:2], ["codex", "exec"])
        self.assertIn("--skip-git-repo-check", calls["cmd"])
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", calls["cmd"])
        self.assertIn("--model", calls["cmd"])
        self.assertEqual(calls["cmd"][-1], "-")            # プロンプトは stdin（"-"）
        self.assertEqual(calls["input"], "プロンプト")
        i = calls["cmd"].index("--output-last-message")
        self.assertFalse(os.path.exists(calls["cmd"][i + 1]))  # 一時ファイルは掃除される

    def test_run_kiro_cli_codex_falls_back_to_stdout(self):
        calls, fake = self._capture_run()                  # ファイルへ何も書かない
        with mock.patch.object(km, "_AGENT_CLI", "codex"), \
             mock.patch.object(km.subprocess, "run", side_effect=fake):
            out = km._run_kiro_cli("プロンプト", None)
        self.assertEqual(out, "ok")                        # stdout へフォールバック

    def test_build_config_sets_agent_globals_and_fields(self):
        orig = (km._AGENT_CLI, km._AGENT_TIMEOUT)
        try:
            ns = self._resolve(None, agent_cli="claude", agent_timeout=42.0)
            cfg = km.build_config(ns)
            self.assertEqual((cfg.agent_cli, cfg.agent_timeout), ("claude", 42.0))
            self.assertEqual((km._AGENT_CLI, km._AGENT_TIMEOUT), ("claude", 42.0))
        finally:
            km._AGENT_CLI, km._AGENT_TIMEOUT = orig

    def test_relative_overrides_resolve_under_root(self):
        # 相対パスの上書きは（cwd や workdir ではなく）root 基準で解決される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            orig = (km._AGENT_CLI, km._AGENT_TIMEOUT)
            try:
                ns = self._resolve(None, root=str(d), workdir="wd", backlog="bl", bus="b")
                cfg = km.build_config(ns)
            finally:
                km._AGENT_CLI, km._AGENT_TIMEOUT = orig
            self.assertEqual(cfg.workdir, (d / "wd").resolve())
            self.assertEqual(cfg.backlog, d.resolve() / "bl")
            self.assertEqual(cfg.bus, d.resolve() / "b")

    def test_granularity_default_coarse_and_directives(self):
        ns = self._resolve(None)
        self.assertEqual(ns.granularity, "coarse")
        self.assertIn("ストーリー", km.plan_granularity_directive(None))
        self.assertIn("3〜10", km.plan_granularity_directive("coarse"))
        self.assertIn("最小単位", km.plan_granularity_directive("finest"))
        # 未知値は coarse（既定）に倒す
        self.assertEqual(km.plan_granularity_directive("xxl"),
                         km.plan_granularity_directive("coarse"))

    def test_find_config_prefers_root_level_manifest(self):
        # ルート直下の kiro-project.yaml（マニフェスト）が .kiro/ より優先される
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            try:
                os.chdir(d)
                (Path(d) / ".kiro").mkdir()
                (Path(d) / ".kiro" / "kiro-project.yaml").write_text("root: .\n", encoding="utf-8")
                (Path(d) / "kiro-project.yaml").write_text("root: .\n", encoding="utf-8")
                found = km._find_config(None)
                self.assertEqual(Path(found).resolve(),
                                 (Path(d) / "kiro-project.yaml").resolve())
            finally:
                os.chdir(old)


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
            self.assertFalse((d / ".kiro-projects").exists())

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

class TestInstances(unittest.TestCase):
    """稼働インスタンスのレジストリ（外部操作者がフォルダを発見する口）。"""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._prev = os.environ.get("KIRO_PROJECT_HOME")
        os.environ["KIRO_PROJECT_HOME"] = self._home

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("KIRO_PROJECT_HOME", None)
        else:
            os.environ["KIRO_PROJECT_HOME"] = self._prev

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

    def test_startup_reaps_the_previous_generation_of_kiro_flow(self):
        """起動時に、自分の bus を回している kiro-flow（前世代の残骸）を刈る。

        kiro-project がクラッシュ（kill -9 / 電源断）すると stop を通らないので kiro-flow が残る。
        残った orchestrator はリースを更新し続けるので、次の kiro-project はその run を「実行中」と
        読み、続きから再開せず **新しい run を作り直す** → 同じタスクを二重実行し、同じ作業ブランチへ
        両方が push しあう（17/23 の run を捨てて 1/20 からやり直した）。
        重複起動は別途弾いているので、自分の bus を回している kiro-flow は残骸だけと断定できる。"""
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            # 残骸 run: リースが未来を指したまま（＝そのままなら「実行中」と読まれる）
            p = cfg.bus / "runs" / "run-old"
            p.mkdir(parents=True)
            (p / "meta.json").write_text(json.dumps({
                "status": "running", "orch_lease_until": time.time() + 600}), encoding="utf-8")

            killed = []
            with mock.patch.object(km, "_flow_pids_for_bus", return_value=[4242, 4243]), \
                 mock.patch.object(km.os, "kill", side_effect=lambda pid, sig: killed.append(pid)), \
                 mock.patch.object(km, "_pid_alive", return_value=False):
                n = km.reap_orphan_flow(cfg)

            self.assertEqual(n, 2, "残骸プロセスを止める")
            self.assertEqual(sorted(set(killed)), [4242, 4243])
            meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["orch_lease_until"], 0.0, "リースを失効させる")

            # 失効した run は「停滞」＝続きから再開できる（成果が捨てられない）
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "run-old"))
            self.assertEqual(km.run_id_for(cfg, t), "run-old", "続きから再開する")

    def test_reap_leaves_other_projects_alone(self):
        # 別プロジェクトの bus を回している kiro-flow は自分の残骸ではない（触らない）
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            ps_out = (f"111 python /x/kiro-flow --bus /other/project/bus work\n"
                      f"222 python /x/kiro-flow --bus {cfg.bus.resolve()} work\n"
                      f"333 python /x/unrelated --bus {cfg.bus.resolve()}\n")
            fake = types.SimpleNamespace(returncode=0, stdout=ps_out)
            with mock.patch.object(km.subprocess, "run", return_value=fake):
                pids = km._flow_pids_for_bus(cfg.bus)
            self.assertEqual(pids, [222], "自分の bus の kiro-flow だけを対象にする")

    def test_stop_takes_the_kiro_flow_children_with_it(self):
        """停止は kiro-flow の子孫まで届くこと（本人だけ殺すと残骸が走り続ける）。

        kiro-flow が生き残ると、その orchestrator が run の生存リースを更新し続ける。次に起動した
        kiro-project はそれを「まだ実行中」と読み、続きから再開せず **新しい run を作り直す**
        （実際 17/23 まで進んだ run を捨てて 1/20 からやり直し、同じタスクを二重実行して同じ
        作業ブランチへ両方が push しあった）。"""
        # detached 起動（自分がプロセスグループのリーダー）→ グループごと送る
        with mock.patch.object(km.os, "getpgid", return_value=4242) as _g, \
             mock.patch.object(km.os, "killpg") as killpg, \
             mock.patch.object(km.os, "kill") as kill:
            km._signal_tree(4242, signal.SIGTERM)
            killpg.assert_called_once_with(4242, signal.SIGTERM)
            kill.assert_not_called()

    def test_stop_does_not_kill_the_whole_terminal_group(self):
        # 端末から run --watch を直叩きした場合、プロセスグループには人のシェルや他のジョブが
        # 混ざる。グループへ送ると無関係のプロセスまで殺すので、本人にだけ送る。
        with mock.patch.object(km.os, "getpgid", return_value=999) as _g, \
             mock.patch.object(km.os, "killpg") as killpg, \
             mock.patch.object(km.os, "kill") as kill:
            km._signal_tree(4242, signal.SIGTERM)     # pgid(999) != pid(4242) ＝ リーダーでない
            killpg.assert_not_called()
            kill.assert_called_once_with(4242, signal.SIGTERM)

    def test_watch_refuses_a_duplicate_of_the_same_project(self):
        """同じプロジェクトを 2 つのループに監視させない。

        start は弾いていたが `run --watch` の直叩きは素通りだった。2 つ走ると同じ backlog を
        奪い合い、同じタスクを二重実行して状態ファイルと決定記録を互いに上書きする。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            cfg = cfg_for(root, watch=True)
            cfg.source_root = root
            paths = km.register_instance(cfg)          # 先客（生きている pid で登録）
            self.addCleanup(lambda: [x.unlink() for x in paths if x.exists()])
            self.assertEqual(km.cmd_run(cfg), 1, "重複起動は拒否する")

    def test_watch_duplicate_is_allowed_with_force(self):
        # 人が明示的に許可したなら通す（start --force から伝搬してくる）
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            cfg = cfg_for(root, watch=True)
            cfg.source_root = root
            paths = km.register_instance(cfg)
            self.addCleanup(lambda: [x.unlink() for x in paths if x.exists()])
            cfg.force = True
            with mock.patch.object(km, "ensure_dirs", side_effect=RuntimeError("進んだ")):
                with self.assertRaises(RuntimeError):   # 重複チェックを抜けて先へ進む
                    km.cmd_run(cfg)

    def test_registered_root_is_the_source_root_not_the_worktree(self):
        """登録する root は「素の root」。実書き込み先（state worktree）ではない。

        worktree 側を root として記録すると 2 つ壊れる: start/stop が照合に使う _resolved_root は
        リダイレクトしないので一致せず、重複検出と停止が永久に空振りする（実際そうなっていた）。
        さらにこの root を --root に渡した外部操作者は、worktree をさらに worktree へ逃がす
        二重リダイレクトに落ちる。"""
        with tempfile.TemporaryDirectory() as d:
            src = Path(d).resolve() / ".kiro-project"
            wt = Path(d).resolve() / "elsewhere-kiro-state" / ".kiro-project"
            wt.mkdir(parents=True)
            cfg = cfg_for(wt, watch=True)             # 実書き込み先は worktree
            cfg.source_root = src                     # 設定・CLI で指定された素の root
            rec = km.instance_record(cfg)
            self.assertEqual(rec["root"], str(src), "素の root を登録する（--root に渡せる値）")
            self.assertTrue(rec["backlog"].startswith(str(wt)), "各パスは実体（worktree）のまま")

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

class TestRemoteDiscovery(unittest.TestCase):
    """共有レジストリ越しの別ホスト発見（§11-7）。core はファイル操作のみ・ネットワーク非依存を保つ。"""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._shared = tempfile.mkdtemp()
        self._prev = os.environ.get("KIRO_PROJECT_HOME")
        self._prev_reg = os.environ.get("KIRO_PROJECT_REGISTRY")
        os.environ["KIRO_PROJECT_HOME"] = self._home
        os.environ.pop("KIRO_PROJECT_REGISTRY", None)

    def tearDown(self):
        for k, v in (("KIRO_PROJECT_HOME", self._prev),
                     ("KIRO_PROJECT_REGISTRY", self._prev_reg)):
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
            # ローカル home と共有先の両方へ「ホスト-PID」修飾名で書かれる
            self.assertEqual(len(paths), 2)
            self.assertTrue(any(Path(self._shared) in p.parents for p in paths))
            self.assertTrue(all(p.name == f"{socket.gethostname()}-{os.getpid()}.json"
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

    def test_heartbeat_thread_beats_while_main_thread_is_busy(self):
        # 本体がタスク実行でブロックしている間も心拍が途切れないこと。従来はパス境界でしか
        # 打てず、1 タスクが INSTANCE_TTL（90秒）を超えると停止扱いになり、viewer から
        # 稼働中のプロジェクトが「停止中」「別マシン」に見えていた。
        with tempfile.TemporaryDirectory() as wd:
            cfg = cfg_for(Path(wd), watch=True, project_name="default")
            paths = km.register_instance(cfg)
            self.addCleanup(lambda: [p.unlink() for p in paths if p.exists()])
            before = json.loads(paths[0].read_text())["heartbeat"]
            stop = km._start_heartbeat_thread(cfg, paths, interval=0.02)
            self.addCleanup(stop.set)
            time.sleep(0.2)                       # メインスレッドは「実行中」でブロックしている想定
            after = json.loads(paths[0].read_text())["heartbeat"]
            self.assertGreater(after, before)     # 何もしなくても心拍が進んでいる

    def test_heartbeat_thread_stops_on_event(self):
        with tempfile.TemporaryDirectory() as wd:
            cfg = cfg_for(Path(wd), watch=True, project_name="default")
            paths = km.register_instance(cfg)
            self.addCleanup(lambda: [p.unlink() for p in paths if p.exists()])
            stop = km._start_heartbeat_thread(cfg, paths, interval=0.02)
            time.sleep(0.1)
            stop.set()
            time.sleep(0.1)
            frozen = json.loads(paths[0].read_text())["heartbeat"]
            time.sleep(0.15)
            self.assertEqual(json.loads(paths[0].read_text())["heartbeat"], frozen)  # 以降は打たない

    def test_heartbeat_thread_does_not_touch_status_when_interval_disabled(self):
        # status.json は state_git のコミット対象。既定（status_interval=0）では触らない
        # ＝ idle の git 負荷を増やさない。
        with tempfile.TemporaryDirectory() as wd:
            cfg = cfg_for(Path(wd), watch=True, project_name="default", status_interval=0.0)
            paths = km.register_instance(cfg)
            self.addCleanup(lambda: [p.unlink() for p in paths if p.exists()])
            stop = km._start_heartbeat_thread(cfg, paths, interval=0.02)
            self.addCleanup(stop.set)
            time.sleep(0.15)
            self.assertFalse(km.status_path(cfg).exists())

    def test_split_registry_parses_pathsep_and_list(self):
        joined = os.pathsep.join(["/a", "/b"])
        self.assertEqual(km._split_registry(joined), ["/a", "/b"])
        self.assertEqual(km._split_registry(["/a", joined]), ["/a", "/a", "/b"])
        self.assertEqual(km._split_registry(None), [])

    def test_env_registry_is_read(self):
        self._remote("hostB", 303, hb_age=3)
        os.environ["KIRO_PROJECT_REGISTRY"] = self._shared
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
        self._prev = os.environ.get("KIRO_PROJECT_HOME")
        os.environ["KIRO_PROJECT_HOME"] = self._home

    def tearDown(self):
        km.cmd_stop(want_all=True)            # 取りこぼした daemon を確実に止める
        if self._prev is None:
            os.environ.pop("KIRO_PROJECT_HOME", None)
        else:
            os.environ["KIRO_PROJECT_HOME"] = self._prev

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
        root = "/tmp/wrk/my-project"
        self._write_rec(me, root)
        self.assertEqual([r["pid"] for r in km.select_instances(pid=me)], [me])
        self.assertEqual([r["pid"] for r in km.select_instances(root=root)], [me])  # root 直指定
        self.assertEqual([r["pid"] for r in km.select_instances(want_all=True)], [me])
        self.assertEqual(km.select_instances(root="/no/such"), [])
        self.assertEqual(km.select_instances(root="/tmp/wrk"), [])   # 親ディレクトリでは一致しない

    def test_stop_kills_process_and_cleans_registry(self):
        import subprocess as sp
        child = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self._write_rec(child.pid, "/tmp/x/my-proj")
        rc = km.cmd_stop(pid=child.pid, timeout=5.0)
        self.assertEqual(rc, 0)
        self.assertFalse(km._pid_alive(child.pid))
        self.assertFalse((km.instances_dir() / f"{child.pid}.json").exists())

    def test_stop_without_target_returns_1(self):
        self.assertEqual(km.cmd_stop(root="/nothing/here"), 1)

    def test_start_registers_then_stop(self):
        work = Path(tempfile.mkdtemp())
        (work / "kiro-project.json").write_text(
            '{"executor":"stub","planner":"none","flow_planner":"stub","poll":0.3}', encoding="utf-8")
        cfg = str(work / "kiro-project.json")
        rc = km.cmd_start(root=str(work), config=cfg)
        self.assertEqual(rc, 0)
        # 登録の出現を待つ（最大 ~5s）。記録 root はプロジェクトルートそのもの
        root = str(work.resolve())
        for _ in range(50):
            if km.select_instances(root=root):
                break
            time.sleep(0.1)
        self.assertTrue(km.select_instances(root=root))         # 起動して登録された
        self.assertEqual(km.cmd_start(root=str(work), config=cfg), 1)  # 重複起動は拒否
        self.assertEqual(km.cmd_stop(root=str(work)), 0)
        self.assertEqual(km.select_instances(root=root), [])    # 停止で消える

    def test_root_resolves_from_config(self):
        # start/stop/restart の照合 root は --root 未指定なら設定ファイルの root から
        # 解決する（daemon 子プロセスは resolve_config 経由で設定の root に付くため、ここが
        # cwd 固定だと重複検出が効かず stop も対象を見つけられない）。
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        cfgp = work / "kiro-project.json"
        cfgp.write_text(json.dumps({"root": str(work / "state" / "proj")}), encoding="utf-8")
        self.assertEqual(km._resolved_root(None, config=str(cfgp)),
                         str((work / "state" / "proj").resolve()))
        # 相対 root は cwd 基準（build_config の計算と一致。workdir はアンカーではなく
        # root 配下の作業場所なので、root の解決には影響しない）
        cfgp.write_text(json.dumps({"root": "proj", "workdir": str(work)}), encoding="utf-8")
        self.assertEqual(km._resolved_root(None, config=str(cfgp)),
                         str((Path.cwd() / "proj").resolve()))
        # --root 明示は従来どおり cwd 基準で設定ファイルを読まない
        self.assertEqual(km._resolved_root(str(work / "x"), config=str(cfgp)),
                         str((work / "x").resolve()))

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

    def test_verify_timeout_default_survives_a_full_test_suite(self):
        """verify の既定タイムアウトは、テストスイート全体を回す完了条件に耐えること。

        「テストスイート全体を green にする」類の verify は数分かかる（このリポジトリは 990 件で
        130 秒）。既定 120 秒では **完了しているのに時間切れで NG** と判定され、リトライを積み
        上げた末に人へエスカレーションしていた（retries=6 まで無駄に積み直して blocked）。
        act_timeout より十分短く保ち、ハングの保護は残す。"""
        defaults = {f.name: f.default for f in dataclasses.fields(km.Config)}
        self.assertGreaterEqual(defaults["verify_timeout"], 600.0,
                                "フルスイートを回せる長さがあること")
        self.assertGreaterEqual(km.CONFIG_DEFAULTS["verify_timeout"], 600.0,
                                "設定ファイルの既定も揃っていること")
        self.assertLess(defaults["verify_timeout"], defaults["act_timeout"],
                        "act より短く（ハングの保護を残す）")

    def test_json_config_fills_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-project.json"
            p.write_text('{"executor":"stub","planner":"none","poll":9,"max_cycles":3}',
                         encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual((ns.executor, ns.planner, ns.poll, ns.max_cycles),
                             ("stub", "none", 9, 3))

    def test_cli_overrides_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-project.json"
            p.write_text('{"executor":"stub","planner":"none"}', encoding="utf-8")
            ns = self._resolve(str(p), executor="agent")   # CLI 明示は維持される
            self.assertEqual(ns.executor, "agent")         # CLI 勝ち
            self.assertEqual(ns.planner, "none")           # config 採用

    def test_bus_config_is_honored(self):
        # 設定ファイルの bus: が読まれ、明示バス（絶対パス）として使われること。
        # これが読まれないと既定バスに落ち、外部 kiro-flow daemon が非検知になる。
        with tempfile.TemporaryDirectory() as d:
            shared = str(Path(d) / "shared-bus")
            p = Path(d) / "kiro-project.json"
            p.write_text(json.dumps({"bus": shared}), encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual(ns.bus, shared)               # config の bus が args に載る
            cfg = km.build_config(ns)
            self.assertEqual(str(cfg.bus), shared)          # 実際に使うバス = 明示バス

    def test_bus_absent_defaults_under_root(self):
        # bus 未指定は <root>/bus。
        ns = self._resolve(None)
        self.assertIsNone(ns.bus)
        cfg = km.build_config(ns)
        self.assertEqual(cfg.bus.name, "bus")

    def test_builtin_defaults_when_no_config(self):
        ns = self._resolve(None)
        self.assertEqual((ns.executor, ns.planner, ns.poll, ns.max_cycles, ns.location),
                         ("agent", "agent", 5.0, 20, "auto"))
        self.assertEqual((ns.auto_adjudicate, ns.adjudicate_max), (True, 1))  # 既定 on

    def test_yaml_config_when_pyyaml_available(self):
        if km.yaml is None:
            self.skipTest("PyYAML 未導入（JSON 経路は別テストで担保）")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-project.yaml"
            p.write_text("executor: stub\nmax_retries: 5\ngit_branch: develop\n", encoding="utf-8")
            ns = self._resolve(str(p))
            self.assertEqual((ns.executor, ns.max_retries, ns.git_branch),
                             ("stub", 5, "develop"))

    def test_missing_explicit_config_exits(self):
        with self.assertRaises(SystemExit):
            self._resolve("/no/such/kiro-project.yaml")

    def test_boolean_flags_from_config(self):
        # 真偽フラグ（watch/do_archive/learn/rot/cleanup/once/dry_run/ltm/regression_revert）が
        # 設定ファイルで効く。resolve_config は CLI 未指定（None）のみ config→既定 で埋める。
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kiro-project.json"
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
            p = Path(d) / "kiro-project.json"
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
        def run(prompt, model, purpose=""):
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
            km._run_kiro_cli = lambda prompt, model, purpose="": (
                '[{"title":"lib に型追加","verify":"test -f packages/t.ts"}]')
            try:
                specs = km.plan_via_agent(cfg, ch)
            finally:
                km._run_kiro_cli = orig
            self.assertEqual(specs[0]["workspace"], "lib")  # verify=packages/** → lib（必ず明示される）

    def test_plan_via_stub_enqueues_charter_acceptance(self):
        # executor: stub の既定 planner（plan_via_stub）は _run_kiro_cli を一切呼ばず、charter の
        # acceptance をそのまま初期タスクにする。verify は人が書いた受入条件そのもの。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"                      # 存在しない → acceptance 未達
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)                       # executor="stub"（cfg_for の既定）
            ch = km.load_charter(cfg)
            specs = km.plan_via_stub(cfg, ch)
            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0]["verify"], f"test -f {flag}")
            self.assertIn("受入条件を満たす", specs[0]["title"])

    def test_plan_via_stub_enqueues_even_when_acceptance_already_passes(self):
        # 回帰: 初回から PASS する acceptance（`echo ok` 等）でも起票する。plan は未達判定の場では
        # ない（それは evaluate の役目）。かつては acceptance をその場で実行して未達だけを起票して
        # いたため、こういう charter ではバックログが空のまま converged し、viewer で「バージョンを
        # 足してもバックログが現れない」ように見えていた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("test -f {flag}", 'echo "hellO"'))
            cfg = cfg_for(d)
            ch = km.load_charter(cfg)
            specs = km.plan_via_stub(cfg, ch)      # PASS する条件でも初回は起票する
            self.assertEqual([s["verify"] for s in specs], ['echo "hellO"'])

    def test_stub_plan_is_idempotent_across_cycles(self):
        # 常に起票する planner でも、同じ受入条件が積み直されないこと（_enqueue_specs が backlog と
        # archive のタイトルで冪等に弾く）。これが「初回だけ起票」の担保。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("test -f {flag}", 'echo "hellO"'))
            cfg = cfg_for(d, max_project_cycles=1)
            km.cmd_project(cfg, runner=lambda c: _drained())
            first = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertEqual(len(first), 1)
            km.cmd_project(cfg, runner=lambda c: _drained())   # 2 パス目は積み増さない
            self.assertEqual([t.title for t in km.load_tasks(cfg.backlog)], first)

    def test_cmd_project_stub_executor_never_calls_agent_for_planning(self):
        # 実運用インシデントの再発防止: .kiro/kiro-project.yaml で --planner none / --executor stub
        # を設定しても、charter があると run/watch は自動で cmd_project（charter 駆動）に入り、
        # 従来はその既定 plan_fn が黙って plan_via_agent（実エージェント呼び出し）を使っていた。
        # executor: stub では plan_via_stub に切り替わり、エージェントを一切呼ばないことを保証する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"                      # 存在しない → acceptance 未達のまま
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, max_project_cycles=1)  # executor="stub"（既定）。planner は注入しない
            orig = km._run_kiro_cli

            def _boom(prompt, model):
                raise AssertionError("stub モードなのにエージェント（_run_kiro_cli）が呼ばれた")

            km._run_kiro_cli = _boom
            try:
                km.cmd_project(cfg, runner=lambda c: _drained())
            finally:
                km._run_kiro_cli = orig
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertTrue(any("受入条件を満たす" in t for t in titles))  # 決定的 stub planner の出力

    def test_cmd_project_agent_executor_still_uses_plan_via_agent(self):
        # 対の回帰テスト: executor が stub 以外（既定 agent 等）なら従来どおりエージェント委譲のまま。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, executor="agent", max_project_cycles=1)
            calls = {"n": 0}
            orig = km._run_kiro_cli

            def fake(prompt, model, purpose=""):
                calls["n"] += 1
                return '[{"title":"エージェント生成タスク","verify":"true"}]'

            km._run_kiro_cli = fake
            try:
                km.cmd_project(cfg, runner=lambda c: _drained())
            finally:
                km._run_kiro_cli = orig
            self.assertGreaterEqual(calls["n"], 1)
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertIn("エージェント生成タスク", titles)

    def test_cmd_project_stub_executor_review_via_stub_skips_agent(self):
        # review_project=True（敵対的レビュー opt-in）でも executor: stub では review_via_stub
        # （常に所見なし）に切り替わり、エージェントを呼ばずに収束する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")   # acceptance 全 PASS（敵対的レビューの発火条件）
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, review_project=True, max_project_cycles=1)  # executor="stub"（既定）
            orig = km._run_kiro_cli

            def _boom(prompt, model):
                raise AssertionError("stub モードなのにエージェント（_run_kiro_cli）が呼ばれた")

            km._run_kiro_cli = _boom
            try:
                km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            finally:
                km._run_kiro_cli = orig
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(km.load_tasks(cfg.backlog), [])

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
            proot = d / "demo-proj"
            proot.mkdir(parents=True)
            (proot / "charter.md").write_text(
                "# Charter: demo\n## goal\nやる\n## acceptance\n- `true`\n", encoding="utf-8")
            rc = km.main(["run", "--workdir", str(d), "--root", str(proot),
                          "--planner", "none",
                          "--flow-planner", "stub", "--executor", "stub", "--dry-run",
                          "--max-project-cycles", "1"])
            self.assertEqual(rc, 1)                       # 収束候補→人待ち
            self.assertTrue((proot / "project.json").exists())
            # milestone id はプロジェクト名（ルートのディレクトリ名）が一次（charter 名でなく）
            self.assertTrue((proot / "needs" / "demo-proj.md").exists())

    def test_run_without_charter_is_plain_loop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            proot = d
            (proot / "backlog").mkdir(parents=True, exist_ok=True)
            (proot / "backlog" / "T1.md").write_text(
                "## T1: x\n- status: ready\n- verify: `true`\n", encoding="utf-8")
            rc = km.main(["run", "--no-delivery-review", "--workdir", str(d), "--planner", "none",
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

    def test_charter_plan_signature_is_content_based(self):
        # 署名は「分解に効く内容」のハッシュ。同一内容は一致、goal 変更で変化、acceptance だけの
        # 変更では変化しない（acceptance は done 判定に効くが分解入力ではないため）。
        a = km.parse_charter("# Charter: x\n## goal\nやる\n## constraints\n- c1\n")
        a2 = km.parse_charter("# Charter: x\n## goal\nやる\n## constraints\n- c1\n")
        b = km.parse_charter("# Charter: x\n## goal\n別のことをやる\n## constraints\n- c1\n")
        c = km.parse_charter("# Charter: x\n## goal\nやる\n## constraints\n- c1\n"
                             "## acceptance\n- test -f z\n")
        self.assertEqual(km._charter_plan_signature(a), km._charter_plan_signature(a2))
        self.assertNotEqual(km._charter_plan_signature(a), km._charter_plan_signature(b))
        self.assertEqual(km._charter_plan_signature(a), km._charter_plan_signature(c))

    def test_charter_change_replans_even_with_consumable_tasks(self):
        # viewer 等で charter を編集したら、消化可能タスクが残っていても次 run で再計画され
        # backlog に差分が反映される（編集しても backlog が変わらない問題の修正）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                return [{"title": f"タスク{calls['n']}", "verify": f"test -f {flag}"}]

            def runner(c):                        # blocked で 1 サイクル抜ける（タスクは消化可能のまま残す）
                r = _drained()
                r["counts"]["blocked"] = 1
                return r

            # 1回目: 初回計画（消化可能タスク無し）→ planner 呼ばれ、charter 署名がベースライン記録
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)
            base_sig = km.load_project_state(cfg)["planned_charter_sig"]
            self.assertTrue(any(t.consumable() for t in km.load_tasks(cfg.backlog)))  # 消化可能タスクが残る

            # 2回目: charter 未変更 → 消化可能タスクがあるので再分解しない
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)

            # charter の goal を変更（分解に効く内容 → 署名が変わる）
            write_charter(d, CHARTER.replace("{flag}", str(flag)).replace(
                "CSV を要約する CLI を完成させる。", "JSON を要約する CLI を完成させる。"))

            # 3回目: charter 変更検知 → 消化可能タスクがあっても再計画され、新タスクが入る
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 2)
            self.assertNotEqual(km.load_project_state(cfg)["planned_charter_sig"], base_sig)
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertIn("タスク2", titles)      # charter 差分が生む新規タスクが backlog に反映

    def test_acceptance_only_edit_does_not_replan(self):
        # acceptance だけの変更は分解入力でないので再計画を誘発しない（評価側で反映される）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                return [{"title": f"タスク{calls['n']}", "verify": f"test -f {flag}"}]

            def runner(c):
                r = _drained()
                r["counts"]["blocked"] = 1
                return r

            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)
            # acceptance 行だけ変える（goal/制約/成果物は不変 → 署名は変わらない想定）
            write_charter(d, CHARTER.replace("{flag}", str(flag)).replace(
                f"- `test -f {flag}`", f"- `test -f {flag}`\n- `test -d {d}`"))
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)      # 再計画されない

    def test_replan_request_forces_redecompose_and_recreates_done(self):
        # エラー回復: 人が「charter から再分解」を要求すると、消化可能タスクが残り charter が
        # 無変更でも 1 回だけ plan を強制する。冪等照合は「現行処理中のバックログ」だけと行う:
        # 処理中タスクと類似は二重投入しないが、done/archive と類似はやり直しとして再作成を許す
        # （過去の完了実績が回復のための再分解を丸ごと弾き「押しても何も起きない」のを防ぐ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            mkb(d, "SEED", status="ready", verify="true",
                title="処理中の既存タスク")                        # 消化可能タスクを残す
            adir = cfg.archive_dir()
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "OLD.md").write_text(
                "## OLD: 既存の done タスク\n- status: done\n- verify: `true`\n", encoding="utf-8")

            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                # done と同一タイトル（やり直し＝再作成される）＋処理中と同一タイトル（弾かれる）
                # ＋新規タイトル（取りこぼし＝入る）
                return [{"title": "既存の done タスク", "verify": "true"},
                        {"title": "処理中の既存タスク", "verify": "true"},
                        {"title": "取りこぼした新規タスク", "verify": f"test -f {flag}"}]

            def runner(c):
                r = _drained()
                r["counts"]["blocked"] = 1
                return r

            # baseline: 消化可能タスクあり・charter 無変更 → 再分解しない（署名だけ記録）
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 0)

            # viewer のボタン相当: commands に replan をドロップ → ingest でマーカー化
            cd = km.commands_dir(cfg)
            cd.mkdir(parents=True, exist_ok=True)
            (cd / "replan.json").write_text(json.dumps(
                {"command": "replan", "reason": "取りこぼし回復"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(cfg), ["replan:project"])
            self.assertEqual(list(cd.glob("*.json")), [])          # 処理したら消す
            self.assertTrue(km.replan_request_path(cfg).exists())  # 再分解要求マーカーが立つ
            self.assertTrue(km.has_work(cfg))                      # idle watch を起こす
            self.assertIn("DR-", (cfg.decisions / "demo.md").read_text())  # 決定記録も残る

            # 次パス: 消化可能タスクがあり charter 無変更でも、要求により再分解が走る
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)
            self.assertFalse(km.replan_request_path(cfg).exists())  # one-shot で消化
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertIn("取りこぼした新規タスク", titles)          # 差分（取りこぼし）は入る
            self.assertIn("既存の done タスク", titles)              # done と同種のやり直しは再作成
            self.assertEqual(titles.count("処理中の既存タスク"), 1)   # 処理中とは二重投入しない

            # さらに次パス: 要求は消化済みなので再分解しない（one-shot）
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)

    def test_replan_does_not_resurrect_rejected_tasks(self):
        # replan のやり直しは done の再作成を許すが、却下済み（rejected・人の明示判断）は
        # archive にあっても照合に残し、復活させない（reject → 自動 replan の直後が典型）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            mkb(d, "T1", title="決済APIを追加", verify="true")
            km.cmd_reject(cfg, "T1", "スコープ外")            # archive に rejected として退避
            km.consume_replan_request(cfg)                    # reject が立てた要求は別途消しておく

            km.write_replan_request(cfg, "やり直し")

            def planner(ch):
                return [{"title": "決済APIを追加", "verify": "true"}]

            km.cmd_project(cfg, planner=planner, runner=lambda c: _drained())
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertNotIn("決済APIを追加", titles)          # 却下済みは復活しない

    def test_replan_request_consumed_on_no_acceptance_pass(self):
        # acceptance 未定義で cmd_project が早期 return するパスでも、再分解要求マーカーは
        # 入口で消費される（残すと has_work が永久に True になり idle watch が空振り起床し続ける）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: X\n## goal\nやる\n")   # acceptance 無し
            cfg = cfg_for(d)
            km.write_replan_request(cfg, "回復")
            self.assertTrue(km.has_work(cfg))                   # 要求中は起きる
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, km.project_exit_code("no-acceptance"))
            self.assertFalse(km.replan_request_path(cfg).exists())  # 入口で消費済み＝空振り起床しない

    def test_replan_command_without_charter_is_rejected(self):
        # charter が無い（backlog ループ）プロジェクトでは再分解の対象が無いため、
        # replan 指示は取り込まず .err に退避し、マーカーも立てない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            cd = km.commands_dir(cfg)
            (cd / "r.json").write_text(json.dumps({"command": "replan"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(cfg), [])
            self.assertEqual(len(list(cd.glob("*.json.err"))), 1)   # .err に退避
            self.assertFalse(km.replan_request_path(cfg).exists())  # マーカーは立たない

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

    def test_converged_project_records_best_pass_count(self):
        # 回帰: 一発で全 PASS して収束したプロジェクトの best（過去最高 PASS 数）が 0 のまま
        # 保存され、viewer の概要タブが完了しているのに「0 / 1 達成」と表示していた。
        # best の更新が収束の early return より後ろにあったのが原因。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")     # acceptance は最初から PASS
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            st = km.load_project_state(cfg)
            self.assertEqual(st["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(st["acceptance_total"], 1)
            self.assertEqual(st["history"], [1])
            self.assertEqual(st["best"], 1)             # 1/1 達成として記録される

    def test_stall_still_counts_when_best_not_improved(self):
        # best を評価の先頭で更新するようにしても、停滞判定（PASS 数が過去最高を更新しないと
        # stall を積む）の意味は変わらない。更新前の値と比べていることの確認。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"                            # 存在しない → acceptance は常に FAIL
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, project_stall=2, max_project_cycles=5)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            st = km.load_project_state(cfg)
            self.assertEqual(st["status"], km.REASON_PROJECT_STALL)   # 0 PASS のまま → 停滞で人へ
            self.assertEqual(st["best"], 0)

    def test_approved_project_does_not_resurrect_milestone_on_rerun(self):
        # 実運用インシデントの再発防止: approve 後に charter.md が無変更のまま run/watch が
        # 再度 cmd_project を呼んでも、毎回 acceptance を再収束させて milestone（needs/<pid>.md）を
        # 復活させてはいけない（「承認しても復活してくる」バグ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)
            self.assertFalse((cfg.needs / "demo.md").exists())  # 承認で milestone は消える

            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                return []

            code = km.cmd_project(cfg, planner=planner, runner=lambda c: _drained())
            self.assertEqual(code, 0)                         # accepted のまま＝正常終了
            self.assertEqual(calls["n"], 0)                    # plan すら呼ばれない＝ループ自体が動かない
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_ACCEPTED)
            self.assertFalse((cfg.needs / "demo.md").exists())  # milestone は復活しない

    def test_approved_project_reopens_when_charter_changes(self):
        # 対の回帰テスト: charter.md を編集すれば accepted のガードを抜けて通常どおり再評価される
        # （「続行: charter.md を更新して run を再実行」という既存の案内どおりの挙動）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)

            write_charter(d, CHARTER.replace("{flag}", str(flag)) + "\n<!-- bump -->\n")
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_CONVERGED)
            self.assertNotEqual(code, 0)                       # 収束候補として再び人待ちに戻る

    def test_project_status_not_clobbered_before_execute_stage(self):
        # 実運用インシデントの再発防止: cmd_project は冒頭で state["status"] を無条件に "running" へ
        # 上書き保存していた。② execute（runner=run_loop）は内部で ingest_commands を呼び、その場で
        # 人の approve/hold 指示（commands/ ファイルドロップ）を処理するが、この時点で読む
        # project.json はすでに "running" に潰されており、直前サイクルの "converged" が見えない。
        # watch 中は次サイクルが数秒おきに回るため、承認がほぼ常にこのタイミングとぶつかり、
        # cmd_approve が「converged の milestone が見つからない」として exit 2 で失敗し続け、
        # プロジェクトは承認しても再収束して milestone（needs/<pid>.md）が復活し続けていた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_CONVERGED)

            seen = {}

            def runner(c):
                # execute 段に入った時点（ingest_commands が呼ばれるのと同じタイミング）の status
                seen["mid_cycle_status"] = km.load_project_state(c).get("status")
                return _drained()

            km.cmd_project(cfg, planner=lambda ch: [], runner=runner)
            self.assertEqual(seen["mid_cycle_status"], km.REASON_PROJECT_CONVERGED)

    def test_approve_succeeds_when_ingested_mid_next_cycle(self):
        # 上のバグの実害を、実際の ingest_commands 呼び出しタイミングを模して直接検証する:
        # execute 段（runner の中）で approve を試みても、旧実装のように "running" に潰されておらず
        # 成功すること。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())

            rc = {}

            def runner(c):
                rc["approve"] = km.cmd_approve(c, "demo", "OK")
                return _drained()

            km.cmd_project(cfg, planner=lambda ch: [], runner=runner)
            self.assertEqual(rc["approve"], 0)
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_ACCEPTED)

    def test_replan_request_bypasses_accepted_guard(self):
        # 実運用インシデントの再発防止:「charter から再分解」を押しても何も起きないバグ。
        # replan_req は cmd_project 冒頭で consume_replan_request により一発で消費されるため、
        # その直後の accepted ガードが素通りせず早期 return すると、要求は消えたのに一度も
        # plan_fn に反映されない（人の指示の握り潰し）になっていた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)

            self.assertEqual(km.cmd_replan(cfg, "エラー回復"), 0)
            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                return []

            km.cmd_project(cfg, planner=planner, runner=lambda c: _drained())
            self.assertEqual(calls["n"], 1)   # accepted でも明示の再分解要求は必ず一度処理される

    def test_replan_zero_diff_keeps_accepted_and_no_milestone(self):
        # 実運用インシデントの再発防止: 承認済み（accepted）のプロジェクトに差分ゼロの再分解を
        # かけると、再評価が accepted → converged に降格させて承認済みマイルストーン
        # （needs/<pid>.md）が復活していた（「承認ボタンを押しても再び表示される」の直接原因）。
        # 新しい仕事が何も無い再収束は accepted を維持し、milestone も書かない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)

            self.assertEqual(km.cmd_replan(cfg, "エラー回復"), 0)
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, 0)                                           # accepted のまま
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_ACCEPTED)
            self.assertFalse((cfg.needs / "demo.md").exists())   # milestone は復活しない

    def test_stale_milestone_cleared_while_pass_runs(self):
        # 前パスの milestone（needs/<pid>.md）は次パスの再評価開始時に掃除される。残したままだと
        # run 実行中も「要対応: マイルストーン」カードが出続け、収束前の承認（exit 2）を誘発する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))    # 未達 → converged しない
            cfg = cfg_for(d)

            def runner_fail(c):
                r = _drained()
                r["counts"]["blocked"] = 1
                return r

            km.cmd_project(cfg, planner=lambda ch: [], runner=runner_fail)
            self.assertTrue((cfg.needs / "demo.md").exists())         # blocked milestone が立つ

            seen = {}

            def runner_check(c):
                # execute 段（run 実行中に相当）では前パスの milestone は消えている
                seen["mid_run_needs"] = (cfg.needs / "demo.md").exists()
                r = _drained()
                r["counts"]["blocked"] = 1
                return r

            km.cmd_project(cfg, planner=lambda ch: [], runner=runner_check)
            self.assertFalse(seen["mid_run_needs"])
            self.assertTrue((cfg.needs / "demo.md").exists())         # 停止時に書き直される

    def test_pending_commands_ingested_even_on_accepted_early_return(self):
        # 実運用インシデントの再発防止: accepted ガードの早期 return は execute（run_loop）まで
        # 到達しないため、commands/ に落ちた指示ファイルが何パスも放置され、watch が空振り
        # 起床を繰り返していた。cmd_project は入口で指示を消化する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)    # accepted にする

            cd = km.commands_dir(cfg)
            cd.mkdir(parents=True, exist_ok=True)
            (cd / "approve2.json").write_text(json.dumps(
                {"command": "approve", "id": "demo", "reason": "二度押し"}), encoding="utf-8")
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, 0)
            self.assertEqual(list(cd.glob("*.json")), [])             # 入口で消化される
            self.assertEqual(list(cd.glob("*.json.err")), [])         # 二度押しは .err にしない

    def test_approve_milestone_idempotent_and_clear_error(self):
        # 承認済み milestone への approve は冪等に成功（二度押し・取り込み遅延の再送を .err に
        # しない）。収束前（blocked 等）の approve は原因が分かるエラーで exit 2。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "1回目"), 0)
            self.assertEqual(km.cmd_approve(cfg, "demo", "2回目"), 0)   # 冪等

            st = km.load_project_state(cfg)
            st["status"] = km.REASON_PROJECT_BLOCKED                    # 収束前の状態を模す
            km.save_project_state(cfg, st)
            self.assertEqual(km.cmd_approve(cfg, "demo", "早すぎる承認"), 2)

    def test_master_charter_alone_is_not_decomposed(self):
        # マスター憲章（`## master` 付き charter.md）はプロジェクト全体の普遍的な前提であり、
        # それ自体はバックログへ分解されない。バージョン（charters/<name>.md）が無く、やることも
        # 無ければアイドル（リセット直後などに run_loop を回して無駄なログを増やさない）。
        # acceptance はマスターに書かなくてよい（バージョン側が持つ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- 分解しないマスター\n\n"
                             "## goal\n普遍的な目標\n")
            cfg = cfg_for(d)
            self.assertEqual(km.charter_names(cfg), [])       # 分解対象なし
            self.assertTrue(km._has_master_charter(cfg))

            ran = {"n": 0}
            planned = {"n": 0}

            def runner(c):
                ran["n"] += 1
                return _drained()

            def planner(ch):
                planned["n"] += 1
                return []

            # やることが無ければアイドル（run_loop も走らない）
            km.project_watch(cfg, planner=planner, runner=runner, max_passes=1)
            self.assertEqual(ran["n"], 0)                     # 空なら消化も走らない
            self.assertEqual(planned["n"], 0)                 # 分解（plan）は走らない
            self.assertEqual(list(cfg.needs.glob("*.md")), [])  # milestone も立たない

            # 実 backlog タスクがあるときだけ消化する
            mkb(d, "T1", status="ready", verify="true")
            km.project_watch(cfg, planner=planner, runner=runner, max_passes=1)
            self.assertEqual(ran["n"], 1)                     # backlog があれば消化は回る
            self.assertEqual(planned["n"], 0)                 # それでも分解はしない

    def test_version_inherits_master_charter(self):
        # 計画バージョン（charters/<name>.md）はマスター憲章を継承する:
        # goal はバージョン側が優先、acceptance・制約・前提はバージョンに無ければマスターから補う。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## goal\n普遍的な目標\n\n## constraints\n- 標準ライブラリのみ\n\n"
                             "## assumptions\n- 入力は UTF-8\n\n"
                             f"## acceptance\n- `test -f {flag}`\n")
            cd = d / "charters"
            cd.mkdir()
            (cd / "v1.md").write_text(
                "# Charter: v1\n\n## goal\nCSV 要約機能を作る\n\n"
                "## constraints\n- 追加の制約\n", encoding="utf-8")
            cfg = cfg_for(d)
            self.assertEqual(km.charter_names(cfg), ["v1"])   # バージョンだけが駆動される

            ch = km._load_named_charter(cfg, "v1")
            self.assertEqual(ch.goal, "CSV 要約機能を作る")     # goal はバージョン優先
            self.assertEqual(ch.acceptance, [f"test -f {flag}"])  # acceptance はマスター継承
            self.assertIn("標準ライブラリのみ", ch.constraints)   # 制約は和集合
            self.assertIn("追加の制約", ch.constraints)
            self.assertIn("入力は UTF-8", ch.assumptions)

            # 継承済み acceptance で v1 が通常どおり収束する（マスター側は動かない）
            code = km.cmd_project(cfg, planner=lambda c: [], runner=lambda c: _drained(),
                                  charter_name="v1")
            self.assertNotEqual(code, 0)                       # converged（人待ち）
            st = km.load_charter_state(cfg, "v1")
            self.assertEqual(st["status"], km.REASON_PROJECT_CONVERGED)

    def test_version_target_overrides_shared_registry(self):
        # 共有レジストリ（repos.json）を使っていても、各バージョン charter の ## repos が
        # 明示した『base と異なる target』（バージョン毎のリリース先ブランチ）が効く。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## acceptance\n- `true`\n")
            (d / "repos.json").write_text(__import__("json").dumps(
                {"app": {"url": "git@x:app.git", "desc": "本体", "base": "main",
                         "owns": ["src/**"]}}), encoding="utf-8")   # 手書き＝レジストリが正・全版共有
            cd = d / "charters"; cd.mkdir()
            (cd / "v1.md").write_text(
                "# Charter: v1\n\n## goal\nv1\n\n## repos\n- app = git@x:app.git\n"
                "  - owns: src/**\n  - base: main\n  - target: release/1.x\n", encoding="utf-8")
            (cd / "v2.md").write_text(
                "# Charter: v2\n\n## goal\nv2\n\n## repos\n- app = git@x:app.git\n"
                "  - owns: src/**\n  - base: main\n  - target: release/2.x\n", encoding="utf-8")
            cfg = cfg_for(d)

            ch1 = km._load_named_charter(cfg, "v1")
            ch2 = km._load_named_charter(cfg, "v2")
            s1 = next(s for s in ch1.repo_specs if s["name"] == "app")
            s2 = next(s for s in ch2.repo_specs if s["name"] == "app")
            # url/owns/base はレジストリ由来のまま（同一性・ルーティングは不変）、target だけ版毎に差し替わる
            self.assertEqual(s1["url"], "git@x:app.git")
            self.assertEqual(s1["base"], "main")
            self.assertFalse(s1["readonly"])
            self.assertEqual(s1["target"], "release/1.x")     # v1 → release/1.x
            self.assertEqual(s2["target"], "release/2.x")     # v2 → release/2.x

    def test_version_without_target_keeps_registry_target(self):
        # バージョンが target を明示しない（or ## repos 自体が無い）なら、共有レジストリの
        # target をそのまま尊重する（後方互換＝上書きしない）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## acceptance\n- `true`\n")
            (d / "repos.json").write_text(__import__("json").dumps(
                {"app": {"url": "git@x:app.git", "base": "main", "target": "develop",
                         "owns": ["src/**"]}}), encoding="utf-8")
            cd = d / "charters"; cd.mkdir()
            (cd / "v1.md").write_text("# Charter: v1\n\n## goal\nv1\n", encoding="utf-8")
            cfg = cfg_for(d)
            ch = km._load_named_charter(cfg, "v1")
            s = next(s for s in ch.repo_specs if s["name"] == "app")
            self.assertEqual(s["target"], "develop")          # レジストリの target を尊重

    def test_master_edit_affects_version_signatures(self):
        # マスターを編集すると、継承合成後の署名（plan/full）が変わる＝バージョン側の
        # 再計画・accepted 再開の判定にマスター編集が効く。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## constraints\n- 制約A\n\n## acceptance\n- `true`\n")
            cd = d / "charters"
            cd.mkdir()
            (cd / "v1.md").write_text("# Charter: v1\n\n## goal\nやること\n", encoding="utf-8")
            cfg = cfg_for(d)
            ch1 = km._load_named_charter(cfg, "v1")
            plan1, full1 = km._charter_plan_signature(ch1), km._charter_full_signature(ch1)

            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## constraints\n- 制約A\n- 制約B（追加）\n\n## acceptance\n- `true`\n")
            ch2 = km._load_named_charter(cfg, "v1")
            self.assertNotEqual(plan1, km._charter_plan_signature(ch2))
            self.assertNotEqual(full1, km._charter_full_signature(ch2))

    def test_non_master_charter_keeps_legacy_behavior(self):
        # `## master` の無い従来の charter.md は今までどおり単一 charter として駆動される。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", "x"))
            cfg = cfg_for(d)
            self.assertEqual(km.charter_names(cfg), ["default"])
            self.assertFalse(km._has_master_charter(cfg))

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

    def test_enqueue_specs_rereads_existing_at_enqueue_time(self):
        # plan/review はエージェント委譲で数分かかる。スナップショット取得後に投入された
        # タスク（別インスタンス・前パス・state_git 同期・リセット後に書き戻された残骸）が
        # 照合に無く、類似バックログを二重投入していた。投入直前に現物を読み直して照合する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            snapshot = km._existing_titles(cfg)          # 空バックログ時点のスナップショット
            km.enqueue_task(cfg, {"title": "成果物を作る", "verify": "true"})   # plan 中に投入された体
            created = km._enqueue_specs(
                cfg, [{"title": "成果物を作る", "verify": "true"}], snapshot, 0.5)
            self.assertEqual(created, [])       # 読み直しで重複を検知（二重投入しない）

    def test_enqueue_specs_dedups_against_archive_reread(self):
        # done（archive）も読み直しの対象。リセットを伴わない通常運用で、plan 中に done へ
        # 移ったタスクと類似の spec を再投入しない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            snapshot = km._existing_titles(cfg)
            adir = cfg.archive_dir()
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "T9.md").write_text("## T9: 成果物を作る\n- status: done\n", encoding="utf-8")
            created = km._enqueue_specs(
                cfg, [{"title": "成果物を作る", "verify": "true"}], snapshot, 0.5)
            self.assertEqual(created, [])


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
            km._run_kiro_cli = lambda prompt, model, purpose="": "grep -q '## 概要' README.md"
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

    def test_first_command_line_returns_direct_command(self):
        self.assertEqual(km._first_command_line("\n# comment\npytest -q\n"), "pytest -q")

    def test_first_command_line_skips_unfenced_prose_before_command(self):
        output = "検証コマンドは次のとおりです。\npython3 -m pytest tools/kiro-project/tests -q"
        self.assertEqual(
            km._first_command_line(output),
            "python3 -m pytest tools/kiro-project/tests -q",
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
            km._first_command_line("```sh\npython3 -m pytest tools/kiro-project/tests -q\n```"),
            "python3 -m pytest tools/kiro-project/tests -q",
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
        output = "確認コマンドはこちらです。\n```bash\npython3 -m pytest tools/kiro-project/tests -q\n```"
        self.assertEqual(
            km._first_command_line(output),
            "python3 -m pytest tools/kiro-project/tests -q",
        )

    def test_first_command_line_ignores_colon_terminated_preamble_before_fence(self):
        output = (
            "以下のコマンドで検証できます:\n"
            "```bash\n"
            "python3 -m pytest tools/kiro-project/tests -q -k first_command_line\n"
            "```"
        )
        self.assertEqual(
            km._first_command_line(output),
            "python3 -m pytest tools/kiro-project/tests -q -k first_command_line",
        )

    def test_first_command_line_skips_blank_and_comment_lines_inside_fence(self):
        output = """```bash

# verification notes
   # an indented comment

python3 -m pytest tools/kiro-project/tests -q
echo this-later-command-must-not-be-selected
```"""
        self.assertEqual(
            km._first_command_line(output),
            "python3 -m pytest tools/kiro-project/tests -q",
        )

    def test_first_command_line_skips_language_tag_remnant_inside_fence(self):
        output = "```\nbash\n# verification notes\npython3 -m pytest -q\n```"
        self.assertEqual(km._first_command_line(output), "python3 -m pytest -q")

    def test_first_command_line_strips_leading_shell_prompt_symbol(self):
        self.assertEqual(
            km._first_command_line("$ python3 -m pytest tools/kiro-project/tests -q"),
            "python3 -m pytest tools/kiro-project/tests -q",
        )

    def test_first_command_line_returns_none_without_candidate(self):
        self.assertIsNone(km._first_command_line("\n# comment only\n"))

    def test_first_command_line_returns_none_for_prose_only(self):
        self.assertIsNone(km._first_command_line(
            "Here is how to verify the change\nReview the behavior carefully"
        ))

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
                km.synth_verify(cfg, "x", "曖昧", kiro_run=prose_only, attempts=2),
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
    """kiro-project.py を実プロセスとして argv 起動する黒箱 CLI e2e。

    TestRunLoop が run_loop() を in-process で呼ぶのに対し、こちらは CLI 配線（argparse・パス解決・
    停止理由→exit code・成果物の書き出し）を実バイナリで検証する。act は --dry-run で省略し、
    ループ機構そのもの（優先順位→verify→done/archive/blocked/needs）を確認する。
    パスは絶対（mkdtemp）で渡す: 相対パスは --workdir 基準で解決され picked up されないため。"""

    def _run(self, d: Path, *extra, timeout=60):
        cmd = [sys.executable, str(_MOD), "run", "--no-delivery-review",
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
    """kiro-project CLI が act を実際に kiro-flow.py へサブプロセス委譲し、完走することを検証する
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
            cmd = [sys.executable, str(_MOD), "run", "--no-delivery-review",
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


def _make_skill_repo(root: Path, tool_subdir: str = "tools/kiro-project") -> Path:
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
    (td / "kiro-project.py").write_text("# tool body\n")
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


class TestStateSyncBatching(unittest.TestCase):
    """state sync コミットの集約: 未 push の連続 sync は --amend で 1 つに束ね、同期のたびの
    1 行差分コミットが履歴を埋め尽くさないようにする。push 済み・人のコミットは書き換えない。"""

    @staticmethod
    def _init_repo(d: Path) -> None:
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        subprocess.run(["git", "-C", str(d), "symbolic-ref", "HEAD", "refs/heads/main"],
                       check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.email", "t@test"], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.name", "t"], check=True)

    @staticmethod
    def _log(d: Path) -> "list[str]":
        r = subprocess.run(["git", "-C", str(d), "log", "--format=%s"],
                           capture_output=True, text=True)
        return [ln for ln in r.stdout.splitlines() if ln.strip()]

    def test_direct_consecutive_syncs_amend_into_one(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._init_repo(d)
            sg = km.DirectStateGit(d, interval=0.0)
            (d / "journal.md").write_text("a\n", encoding="utf-8")
            sg.sync()
            (d / "journal.md").write_text("a\nb\n", encoding="utf-8")
            sg.sync()
            msgs = self._log(d)
            self.assertEqual(len(msgs), 1)                     # 2 回目は amend で束ねる
            self.assertTrue(msgs[0].startswith("kiro-project: state sync"))

    def test_direct_does_not_amend_manual_commit(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._init_repo(d)
            sg = km.DirectStateGit(d, interval=0.0)
            (d / "journal.md").write_text("a\n", encoding="utf-8")
            sg.sync()
            (d / "note.md").write_text("human edit\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(d), "commit", "-qm", "manual edit"], check=True)
            (d / "journal.md").write_text("a\nb\n", encoding="utf-8")
            sg.sync()
            msgs = self._log(d)
            self.assertEqual(len(msgs), 3)                     # 人のコミットは書き換えない
            self.assertEqual(msgs[1], "manual edit")

    def test_direct_does_not_amend_pushed_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            d = tmp / "root"
            d.mkdir()
            self._init_repo(d)
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin", str(remote)],
                           check=True)
            sg = km.DirectStateGit(d, interval=0.0)            # interval 0 → 毎 sync で push
            (d / "journal.md").write_text("a\n", encoding="utf-8")
            sg.sync()                                          # commit + push
            (d / "journal.md").write_text("a\nb\n", encoding="utf-8")
            sg.sync()                                          # push 済み HEAD は amend しない
            self.assertEqual(len(self._log(d)), 2)

    @staticmethod
    def _commit_all(d: Path) -> None:
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", "c"], check=True,
                       capture_output=True)

    def test_direct_push_recovers_from_foreign_dirt_in_state_worktree(self):
        """状態 worktree で「同期名前空間の外」が汚れていても push は通り続ける。

        root=<top>/.kiro-project、外（<top>/journal.md）に未コミット変更が残っていると、
        _integrate の rebase が「作業ツリーが汚れている」で必ず失敗 → push は永久に
        non-fast-forward → 分散同期が完全停止する（実際に起きた: 415 件未 push）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            top = tmp / "wt"
            top.mkdir()
            self._init_repo(top)
            subprocess.run(["git", "-C", str(top), "remote", "add", "origin", str(remote)],
                           check=True)
            (top / "journal.md").write_text("legacy\n", encoding="utf-8")   # 名前空間の外
            root = top / ".kiro-project"
            root.mkdir()
            (root / "journal.md").write_text("a\n", encoding="utf-8")
            self._commit_all(top)
            subprocess.run(["git", "-C", str(top), "push", "-q", "-u", "origin", "main"],
                           check=True)

            # 別ホストが origin を 1 コミット進める（= こちらは behind 1 → push は non-FF）
            other = tmp / "other"
            subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.email", "o@test"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
            (other / "other.md").write_text("from another host\n", encoding="utf-8")
            self._commit_all(other)
            subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

            # 名前空間の外に未コミット変更を残す（中断した rebase / 旧レイアウトの残骸を再現）
            (top / "journal.md").write_text("clobbered\n", encoding="utf-8")

            sg = km.DirectStateGit(root, interval=0.0)
            (root / "journal.md").write_text("a\nb\n", encoding="utf-8")     # 自分の名前空間の更新
            sg.sync(force=True)                                              # 例外を投げず push が通る

            r = subprocess.run(["git", "-C", str(top), "rev-list", "--count",
                                "origin/main..HEAD"], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "0")                          # 未 push が残らない
            self.assertTrue((top / "other.md").exists())                     # 相手の更新も取り込めた

    def test_direct_integrate_adjudicates_conflict_instead_of_giving_up(self):
        """rebase が競合しても裁定で決着させて push を通す。

        abort して諦めると push は永久に non-fast-forward のまま＝分散同期が二度と回復しない。
        裁定規則は StateGit と同じ: 人の入力（charter.md）はリモート優先、機械状態はローカル優先。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            top = tmp / "wt"
            top.mkdir()
            self._init_repo(top)
            subprocess.run(["git", "-C", str(top), "remote", "add", "origin", str(remote)],
                           check=True)
            root = top / ".kiro-project"
            root.mkdir()
            (root / "charter.md").write_text("base\n", encoding="utf-8")     # 人の入力
            (root / "status.json").write_text("{}\n", encoding="utf-8")      # 機械状態
            self._commit_all(top)
            subprocess.run(["git", "-C", str(top), "push", "-q", "-u", "origin", "main"],
                           check=True)

            other = tmp / "other"             # 別ホストが同じ 2 ファイルを両方書き換えて push
            subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.email", "o@t"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
            (other / ".kiro-project" / "charter.md").write_text("人の更新\n", encoding="utf-8")
            (other / ".kiro-project" / "status.json").write_text('{"remote":1}\n',
                                                                 encoding="utf-8")
            self._commit_all(other)
            subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

            sg = km.DirectStateGit(root, interval=0.0)
            (root / "charter.md").write_text("こちらの更新\n", encoding="utf-8")
            (root / "status.json").write_text('{"local":1}\n', encoding="utf-8")
            sg.sync(force=True)               # 競合するが例外を投げず push が通る

            r = subprocess.run(["git", "-C", str(top), "rev-list", "--count",
                                "origin/main..HEAD"], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "0")                       # 未 push が残らない
            self.assertFalse(sg._rebasing())                              # rebase を残さない
            self.assertEqual((root / "charter.md").read_text(), "人の更新\n")    # 人＝リモート優先
            self.assertEqual((root / "status.json").read_text(), '{"local":1}\n')  # 機械＝ローカル

    def test_direct_integrate_survives_tracked_excluded_dirt(self):
        """「追跡されてしまった同期除外パス」が dirty でも統合・push が通り続ける（自己修復）。

        旧実装（rebase 統合）の致命傷の再現: 他コミッタ（viewer / 旧 commit_state / kiro-flow の
        管理クローン）が claims/ や bus/.state-git を一度コミットすると、こちらは絶対に commit
        しないため「tracked だが commit されない変更」が永久に残り、rebase が二度と通らず
        push は non-fast-forward のまま状態共有が復旧不能になった（実運用で発生）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            top = tmp / "wt"
            top.mkdir()
            self._init_repo(top)
            subprocess.run(["git", "-C", str(top), "remote", "add", "origin", str(remote)],
                           check=True)
            root = top / ".kiro-project"
            (root / "claims").mkdir(parents=True)
            (root / "bus").mkdir()
            (root / "journal.md").write_text("a\n", encoding="utf-8")
            (root / "claims" / "T1.lock").write_text("owner\n", encoding="utf-8")
            (root / "bus" / ".state-git").write_text("legacy clone marker\n", encoding="utf-8")
            self._commit_all(top)              # 他コミッタが除外パスまで追跡した状態を再現
            subprocess.run(["git", "-C", str(top), "push", "-q", "-u", "origin", "main"],
                           check=True)

            other = tmp / "other"              # リモートの viewer が指示を積む
            subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.email", "o@t"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
            cdir = other / ".kiro-project" / "commands"
            cdir.mkdir(parents=True)
            (cdir / "viewer-approve-T1.json").write_text('{"command":"approve","id":"T1"}',
                                                         encoding="utf-8")
            self._commit_all(other)
            subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

            # 除外パスを dirty にしたまま（旧実装ならここで統合が永久に詰まる）ローカルも進める
            (root / "claims" / "T1.lock").write_text("stolen\n", encoding="utf-8")
            (root / "journal.md").write_text("a\nb\n", encoding="utf-8")
            sg = km.DirectStateGit(root, interval=0.0)
            sg.sync(force=True)                                    # 例外を投げない

            r = subprocess.run(["git", "-C", str(top), "rev-list", "--count",
                                "origin/main..HEAD"], capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "0")                # 未 push が残らない
            self.assertTrue((root / "commands" / "viewer-approve-T1.json").exists())  # 指示を取得
            ls = subprocess.run(["git", "-C", str(top), "ls-files", "--",
                                 ".kiro-project/claims", ".kiro-project/bus/.state-git"],
                                capture_output=True, text=True)
            self.assertEqual(ls.stdout.strip(), "")                # 除外パスは追跡から外れた
            self.assertTrue((root / "claims" / "T1.lock").exists())  # 実ファイルは消さない

    def test_direct_integrate_preserves_both_sides_of_diverged_history(self):
        """多重書き手で分岐した履歴を 1 回の sync で決定的に合流させる（マージ・両方残す）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
            top = tmp / "wt"
            top.mkdir()
            self._init_repo(top)
            subprocess.run(["git", "-C", str(top), "remote", "add", "origin", str(remote)],
                           check=True)
            root = top / ".kiro-project"
            run_dir = root / "bus" / "runs" / "r1"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text('{"status":"running"}', encoding="utf-8")
            (root / "journal.md").write_text("base\n", encoding="utf-8")
            self._commit_all(top)
            subprocess.run(["git", "-C", str(top), "push", "-q", "-u", "origin", "main"],
                           check=True)

            other = tmp / "other"              # 別書き手（viewer）が run の進捗と成果を積む
            subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.email", "o@t"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
            orun = other / ".kiro-project" / "bus" / "runs" / "r1"
            (orun / "results").mkdir(parents=True)
            (orun / "results" / "t1.json").write_text('{"ok":true}', encoding="utf-8")
            self._commit_all(other)
            subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)

            sg = km.DirectStateGit(root, interval=0.0)
            (run_dir / "meta.json").write_text('{"status":"done"}', encoding="utf-8")  # 機械状態
            sg.sync(force=True)                # ローカルコミット＋リモート分岐 → マージで合流

            for c in ("origin/main..HEAD", "HEAD..origin/main"):
                r = subprocess.run(["git", "-C", str(top), "rev-list", "--count", c],
                                   capture_output=True, text=True)
                self.assertEqual(r.stdout.strip(), "0", c)         # 双方向とも乖離ゼロ
            self.assertEqual((run_dir / "meta.json").read_text(), '{"status":"done"}')  # 機械=ローカル
            self.assertTrue((run_dir / "results" / "t1.json").exists())   # リモートの成果も取得

    def test_flow_remote_none_when_bus_inside_root(self):
        """バスが root 配下（既定）なら kiro-flow へ state-git を注入しない＝第二の書き手を作らない。
        kiro-project 自身の state 同期が bus ごと鏡写しする。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._init_repo(d)
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                            "https://example.invalid/r.git"], check=True)
            cfg = cfg_for(d, bus=d / "bus")                      # 既定の <root>/bus 相当
            self.assertIsNone(km.project_flow_remote(cfg))
            cmd = km.flow_daemon_cmd(cfg, budget=2)
            self.assertNotIn("--state-git", cmd)

    def test_state_sync_journals_imports_only(self):
        # journal へ残すのは import（リモート指示の取り込み）のみ。export を記録すると
        # その行自体が次の同期の差分になり「export=1」の空コミットが恒久に続くため。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)

            class _SG:
                def __init__(self, ret):
                    self.ret = ret

                def sync(self, force=False):
                    return self.ret
            with mock.patch.object(km, "state_git_for", return_value=_SG((0, 1))):
                km.state_sync(cfg)
            self.assertFalse(cfg.journal.exists())             # export のみ → 記録しない
            with mock.patch.object(km, "state_git_for", return_value=_SG((2, 0))):
                km.state_sync(cfg)
            self.assertIn("import=2", cfg.journal.read_text(encoding="utf-8"))


class TestResumeRun(unittest.TestCase):
    """resume-run: 停滞・失敗した run を『続きから』やり直す正規の口（viewer の再実行ボタン）。
    従来は viewer が backlog ファイルを直接書き換えており、分散構成では状態リポジトリへの
    第二の書き手＝コミット競合の源だった。"""

    @staticmethod
    def _write_meta(cfg, rid: str, status: str, lease: "float | None" = None) -> None:
        rd = cfg.bus / "runs" / rid
        rd.mkdir(parents=True, exist_ok=True)
        meta = {"status": status, "updated_at": "2026-01-01T00:00:00Z"}
        if lease is not None:
            meta["orch_lease_until"] = lease
        (rd / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_resume_run_pins_last_run_and_requeues(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked")
            cfg = cfg_for(d)
            self._write_meta(cfg, "req-x-T1-r0", "failed")
            rc = km.cmd_resume_run(cfg, "T1", "req-x-T1-r0", "続きから")
            self.assertEqual(rc, 0)
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.norm_status(), "ready")
            self.assertEqual(t.get("last_run"), "req-x-T1-r0")
            # 次の act はこの run を再開する（run_id_for が last_run を採用する）
            self.assertEqual(km.run_id_for(cfg, t), "req-x-T1-r0")

    def test_resume_run_clears_feedback_so_resume_wins(self):
        # feedback / revised は「新しい run を作る」シグナル。人が『続きから』と明示したら外す。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: T1\n- status: blocked\n- verify: `true`\n- retries: 0\n"
                "- feedback: 前回の指示\n", encoding="utf-8")
            cfg = cfg_for(d)
            self._write_meta(cfg, "req-x-T1-r0", "failed")
            self.assertEqual(km.cmd_resume_run(cfg, "T1", "req-x-T1-r0", ""), 0)
            t = km.load_tasks(cfg.backlog)[0]
            self.assertIsNone(t.get("feedback"))
            self.assertEqual(km.run_id_for(cfg, t), "req-x-T1-r0")

    def test_resume_run_rejects_live_run(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked")
            cfg = cfg_for(d)
            self._write_meta(cfg, "req-x-T1-r0", "running", lease=time.time() + 600)
            self.assertEqual(km.cmd_resume_run(cfg, "T1", "req-x-T1-r0", ""), 2)

    def test_resume_run_allows_missing_run(self):
        # bus 掃除後でも kp/<task-id> ブランチから再開できるため、run 不在は拒否しない
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked")
            cfg = cfg_for(d)
            self.assertEqual(km.cmd_resume_run(cfg, "T1", "req-x-T1-r9", ""), 0)

    def test_ingest_commands_resume_run(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked")
            cfg = cfg_for(d)
            self._write_meta(cfg, "req-x-T1-r0", "failed")
            cdir = d / "commands"
            cdir.mkdir()
            (cdir / "viewer-resume.json").write_text(json.dumps(
                {"command": "resume-run", "id": "T1", "run": "req-x-T1-r0",
                 "reason": "実行画面から再実行"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(cfg), ["resume-run:T1"])
            self.assertFalse((cdir / "viewer-resume.json").exists())
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.get("last_run"), "req-x-T1-r0")
            self.assertEqual(t.norm_status(), "ready")


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
                    update_subdir="tools/kiro-project", update_installer="install.sh",
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
        _commit_change(self.repo, "tools/kiro-project/NEW.txt")
        self.assertTrue(km.check_update(cfg)["available"])

    def test_disabled_when_no_repo(self):
        cfg = self._cfg(update_repo=None)
        self.assertFalse(km.check_update(cfg)["enabled"])
        self.assertFalse(km.maybe_self_update(cfg))

    def test_sparse_checkout_only_subdir(self):
        dest = str(self.tmp / "co" / "repo")
        tool_dir = km.sparse_checkout_tool(str(self.repo), "main",
                                           "tools/kiro-project", dest)
        self.assertTrue(os.path.isfile(os.path.join(tool_dir, "install.sh")))
        self.assertFalse(os.path.isdir(os.path.join(dest, "tools", "kiro-flow")))

    def test_apply_update_records_sha(self):
        cfg = self._cfg()
        km.check_update(cfg)                    # baseline
        _commit_change(self.repo, "tools/kiro-project/N2.txt")
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

    def test_apply_update_skips_when_tool_unchanged(self):
        # リポジトリの HEAD は進んだが update_subdir の内容は前回適用と同一 → installer を
        # 実行せずベースラインだけ進める。direct state-git 構成では自分の state sync push が
        # update_repo の新コミットになるため、SHA 比較だけだと「push → 更新検出 → 再起動 →
        # また push」の自己増殖ループになる（2026-07-11 に実発生）。
        cfg = self._cfg()
        km.check_update(cfg)                                   # baseline
        _commit_change(self.repo, "tools/kiro-project/N3.txt")
        prefix = str(self.tmp / "prefix3")

        def runner(c, **k):
            cmd = c + ["--prefix", prefix] if c[:1] == ["bash"] else c
            return subprocess.run(cmd, capture_output=True, text=True, **k)
        self.assertTrue(km.apply_update(cfg, km.check_update(cfg), runner=runner))  # 実変更 → 適用
        _commit_change(self.repo, "journal.md")                # subdir 外だけが進む
        info = km.check_update(cfg)
        self.assertTrue(info["available"])                     # SHA 上は更新に見える
        calls = []

        def counting(c, **k):
            calls.append(list(c))
            return runner(c, **k)
        self.assertFalse(km.apply_update(cfg, info, runner=counting))    # 適用スキップ
        self.assertFalse(any(c[:1] == ["bash"] for c in calls))          # installer 不実行
        self.assertEqual(km.read_update_state()["applied_sha"], info["remote_sha"])
        self.assertFalse(km.check_update(cfg)["available"])    # ベースライン前進 → 最新扱い

    def test_update_check_interval_survives_restart(self):
        # チェック間隔は state ファイルへ持続化され、自己更新の再起動（新プロセス＝メモリの
        # 時刻リセット）を跨いで尊重される（再起動直後の即時再チェックを防ぐ）。
        cfg = self._cfg(update_check_interval=3600.0)
        calls = []
        with mock.patch.object(km, "check_update",
                               side_effect=lambda *a, **k: (calls.append(1),
                                                            {"available": False})[1]):
            self.assertFalse(km.maybe_self_update(cfg))
            self.assertEqual(len(calls), 1)                    # 初回はチェックする
            km._UPDATE_LAST_CHECK["t"] = 0.0                   # プロセス再起動を模擬
            self.assertFalse(km.maybe_self_update(cfg))
            self.assertEqual(len(calls), 1)                    # 間隔内 → 再チェックしない

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
        self.assertFalse(km.executor_delegates(cfg_for(self.tmp, executor="agent")))
        self.assertTrue(km.executor_delegates(cfg_for(self.tmp, executor="gitlab")))

    def test_build_cmd_sets_max_retries_zero_for_gitlab(self):
        mkb(self.tmp, "t1")
        t = km.load_tasks((self.tmp / "backlog"))[0]
        cmd = km.build_kiro_flow_cmd(t, cfg_for(self.tmp, executor="gitlab"))
        self.assertIn("--max-retries", cmd)
        self.assertEqual(cmd[cmd.index("--max-retries") + 1], "0")
        # kiro executor では付けない
        cmd2 = km.build_kiro_flow_cmd(t, cfg_for(self.tmp, executor="agent"))
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
        proot = self.tmp / "proj"
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
        self.assertTrue((got / "kp" / "backlog" / "T1.md").exists())

    def test_import_instruction_drop_and_consumption_propagates(self):
        cfg = self._cfg()
        km.state_sync(cfg, force=True)                       # 初期化（ブランチ作成）
        other = self._other()
        cmd = other / "kp" / "commands" / "ok.json"
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
        rn = other / "kp" / "needs" / "T1.md"
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
        rr = other / "kp" / "repos.json"
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
        rb = other / "kp" / "backlog" / "T1.md"
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
        self.assertTrue((other / "kp" / "backlog" / "T2.md").exists())

    def test_bus_is_synced_but_transient_state_is_excluded(self):
        """bus は同期する（別 PC の viewer が run を見る唯一の経路）。claims / flow-archive は除外。

        kiro-project は WSL、viewer は Windows と別 PC で動くため、ファイルシステムを共有しない。
        bus を除外すると viewer にはバックログしか見えず、実行中の run が一切見えない。
        一方 claims は「同期遅延越しでは排他の意味を持たない」ので載せない（bus/runs/<id>/claims/
        の形でも segment 判定で除外される）。flow-archive は bus の派生で肥大するので載せない。"""
        cfg = self._cfg()
        mkb(cfg.backlog.parent, "T1")
        (cfg.bus / "runs").mkdir(parents=True, exist_ok=True)
        (cfg.bus / "runs" / "r1.json").write_text("{}", encoding="utf-8")
        nested = cfg.bus / "runs" / "r1" / "claims" / "t1"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "worker-1.json").write_text("{}", encoding="utf-8")
        claims = cfg.backlog.parent / "claims"
        claims.mkdir(parents=True, exist_ok=True)
        (claims / "T1.lock").write_text("pid", encoding="utf-8")
        arch = cfg.backlog.parent / "flow-archive"
        arch.mkdir(parents=True, exist_ok=True)
        (arch / "run-1.json").write_text('{"run": {}}', encoding="utf-8")
        km.state_sync(cfg, force=True)
        got = self._other("check")
        proot = got / "kp"
        self.assertTrue((proot / "backlog" / "T1.md").exists())
        self.assertTrue((proot / "bus" / "runs" / "r1.json").exists(), "run は viewer へ届く")
        self.assertFalse((proot / "bus" / "runs" / "r1" / "claims").exists(),
                         "bus 配下の claims は載せない（遅延越しの排他は意味を持たない）")
        self.assertFalse((proot / "claims").exists())
        self.assertFalse((proot / "flow-archive").exists())

    def test_interval_rate_limits_remote_fetch(self):
        cfg = self._cfg(state_git_interval=3600.0)
        km.state_sync(cfg, force=True)                       # 初回は必ず同期（ブランチ作成）
        other = self._other()
        drop = other / "kp" / "inbox" / "task.json"
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
        self.assertTrue((got / "kp" / "journal.md").exists())

    def test_disabled_without_state_git(self):
        cfg = self._cfg(state_git=None)
        km.state_sync(cfg, force=True)                       # 何もしない（クローンも作らない）
        self.assertFalse((self.tmp / "proj" / ".state-git").exists())

    def test_sync_failure_does_not_kill_loop(self):
        cfg = self._cfg(state_git=str(self.tmp / "no-such-remote.git"))
        km.state_sync(cfg, force=True)                       # 不通でも例外を漏らさない
        self.assertIn("state-git 同期失敗", cfg.journal.read_text(encoding="utf-8"))

    def test_dot_prefixed_subdir_works(self):
        # state_git_subdir はドット始まり（.kiro-project 等）でも同期できる（推奨は非ドットだが、
        # 他プロセスの成果物と同居するリポジトリで隠したい構成をサポートする）。
        cfg = self._cfg(state_git_subdir=".kiro-project")
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        got = self._other("check")
        self.assertTrue(
            (got / ".kiro-project" / "backlog" / "T1.md").exists())

    def test_clone_is_reused_across_syncs(self):
        cfg = self._cfg()
        mkb(cfg.backlog.parent, "T1")
        km.state_sync(cfg, force=True)
        clone = self.tmp / "proj" / ".state-git"
        marker = subprocess.run(["git", "-C", str(clone), "config", "--get",
                                 km.STATE_GIT_MARKER], capture_output=True, text=True)
        self.assertEqual(marker.stdout.strip(), "1")
        km._STATE_GITS.clear()                               # プロセス再起動を模擬 → 再クローンせず再利用
        (cfg.backlog / "T2.md").write_text("## T2: y\n- status: ready\n", encoding="utf-8")
        km.state_sync(cfg, force=True)
        got = self._other("check")
        self.assertTrue((got / "kp" / "backlog" / "T2.md").exists())

    def test_project_watch_imports_before_first_plan_on_restart(self):
        # 自己更新の graceful 再起動を模擬（_STATE_GITS クリア）。停止中に viewer が push した
        # charter 更新を、初回 plan より先に取り込むこと（古い charter で計画しない）。
        cfg = self._cfg()
        cfg.charter.write_text("# Charter\n## 目標\nGOAL-A\n## acceptance\n- true\n",
                               encoding="utf-8")
        km.state_sync(cfg, force=True)                       # 初期 export（GOAL-A をリモートへ）
        other = self._other()
        rc = other / "kp" / "charter.md"
        rc.write_text("# Charter\n## 目標\nGOAL-B\n## acceptance\n- true\n", encoding="utf-8")
        self._commit_push(other, "viewer: charter 更新")     # 停止中に GOAL-B を push
        km._STATE_GITS.clear()                               # 再起動を模擬
        seen = []
        km.project_watch(cfg, planner=lambda ch: seen.append(
            "B" if "GOAL-B" in cfg.charter.read_text(encoding="utf-8") else "A") or [],
            reviewer=lambda ch: (True, ""), runner=km.run_loop,
            sleeper=lambda _s: None, max_passes=1)
        self.assertEqual(seen, ["B"])                        # 初回 plan は取り込み後の charter を見る


class TestDirectStateGit(unittest.TestCase):
    """direct モード: プロジェクトルート自体が git クローンなら、管理クローンを介さず
    そのリポジトリへ直接コミット・push する（viewer が git 越しに編集・検収する前提を素直にする）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        km._STATE_GITS.clear()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        subprocess.run(["git", "-C", str(self.remote), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True)
        # ルート = 共有リポジトリの clone（初期コミットを作って main を確立する）
        self.root = self.tmp / "proj"
        seed = self.tmp / "seed"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(seed)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.email", "seed@test"], check=True)
        subprocess.run(["git", "-C", str(seed), "config", "user.name", "seed"], check=True)
        subprocess.run(["git", "-C", str(seed), "checkout", "-qb", "main"], check=True)
        (seed / "charter.md").write_text("# Charter: demo\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(seed), "commit", "-qm", "init"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", "-u", "origin", "main"],
                       check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.root)],
                       check=True, capture_output=True)

    def _cfg(self, **kw):
        base = dict(backlog=self.root / "backlog", policy=self.root / "policy.md",
                    decisions=self.root / "decisions", journal=self.root / "journal.md",
                    needs=self.root / "needs", workdir=self.tmp, bus=self.root / "bus",
                    inbox=self.root / "inbox",
                    planner="none", flow_planner="stub", executor="stub", dry_run=True,
                    state_git_interval=0.0)
        base.update(kw)
        cfg = km.Config(**base)
        km.ensure_dirs(cfg)
        return cfg

    def _other(self, name="other") -> Path:
        d = self.tmp / name
        subprocess.run(["git", "clone", "-q", str(self.remote), str(d)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(d), "config", "user.email", "other@test"], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.name", "other"], check=True)
        return d

    def test_root_clone_selects_direct_mode(self):
        cfg = self._cfg()
        self.assertIsInstance(km.state_git_for(cfg), km.DirectStateGit)   # state_git 未設定でも有効
        self.assertIn("direct モード", km.state_git_status_line(cfg))

    def test_sync_survives_divergence_with_a_dirty_worktree(self):
        """リモートが進んでいて、かつ作業ツリーが汚れていても同期できること。

        DirectStateGit は「人の作業を壊さない」ため作業ツリーに触らない（コミットは detached
        worktree で組み、ブランチは CAS で進める）。その結果、未コミット変更が残ったまま
        pull --rebase へ進み `cannot pull with rebase: You have unstaged changes` で必ず失敗する。
        取り込めないと push も non-fast-forward で永久に通らず、リモートとの乖離が広がり続ける
        （実際 viewer が同じブランチへ push した途端に詰まり、分散構成で状態が共有されなくなった）。
        同期の直前に commit_state でコミットしておけば rebase は素直に通る。"""
        cfg = self._cfg()
        cfg.state_top = self.root          # 状態 worktree 相当（commit_state を効かせる）
        cfg.state_commit = True
        cfg.state_backup_branch = ""       # main へのバックアップはこのテストの関心外
        km._last_state_commit = 0.0

        # 他者（viewer 相当）がリモートを先に進める
        other = self._other("viewer")
        (other / "commands").mkdir(parents=True, exist_ok=True)
        (other / "commands" / "approve-x.json").write_text('{"command": "approve"}', encoding="utf-8")
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        for a in (["add", "-A"], ["commit", "-qm", "viewer: approve"], ["push", "-q", "origin", "HEAD:main"]):
            subprocess.run(["git", "-C", str(other), *a], check=True, capture_output=True, env=env)

        # こちらは作業ツリーが汚れている（backlog を書き換えたが未コミット）
        mkb(self.root, "T1")
        self.assertTrue(subprocess.run(["git", "-C", str(self.root), "status", "--porcelain"],
                                       capture_output=True, text=True).stdout.strip(),
                        "前提: 作業ツリーが汚れている")

        # 修正の要: 同期の前にコミットしてクリーンにする（run_loop がこれを行う）
        km.commit_state(cfg, force=True)
        km.state_sync(cfg, force=True)

        # リモートの変更を取り込めている（rebase が通った）
        self.assertTrue((self.root / "commands" / "approve-x.json").exists(),
                        "他者の指示を取り込める")
        # こちらの変更も push できている（non-fast-forward で詰まらない）
        got = self._other("check")
        self.assertTrue((got / "backlog" / "T1.md").exists(), "自分の状態を push できる")

    def test_direct_sync_pushes_state_and_bus_but_excludes_claims(self):
        cfg = self._cfg()
        mkb(self.root, "T1")
        (cfg.bus / "runs").mkdir(parents=True, exist_ok=True)
        (cfg.bus / "runs" / "r1.json").write_text("{}", encoding="utf-8")
        claims = self.root / "claims"
        claims.mkdir(parents=True, exist_ok=True)
        (claims / "T1.lock").write_text("pid", encoding="utf-8")
        km.state_sync(cfg, force=True)
        got = self._other("check")
        self.assertTrue((got / "backlog" / "T1.md").exists())     # subdir 無し・ルート直下に鏡写し
        self.assertTrue((got / "bus" / "runs" / "r1.json").exists(),
                        "bus は同期する（別 PC の viewer が run を見る唯一の経路）")
        self.assertFalse((got / "claims").exists())               # 遅延越しの排他は意味を持たない
        self.assertFalse((self.root / ".state-git").exists())     # 管理クローンは作らない

    def test_state_worktree_does_not_disable_distributed_sync(self):
        """状態 worktree に逃がしていても direct 同期は有効。

        _git_toplevel は「root がリポジトリのトップレベルか」を見る。状態 worktree では root は
        <repo>-kiro-state/.kiro-project というサブディレクトリになるので False を返し、それだけを
        条件にすると state_git_for も project_flow_remote も None になって **分散同期が丸ごと
        無効化される**（origin に何も push されず、別 PC の viewer が状態と run を読む唯一の経路が
        消える。journal に「state-git: 無効」と出続けていた）。"""
        cfg = self._cfg()
        sub = self.root / "nested" / ".kiro-project"       # トップレベルではない root
        sub.mkdir(parents=True, exist_ok=True)
        cfg.backlog = sub / "backlog"
        cfg.backlog.mkdir(parents=True, exist_ok=True)
        self.assertFalse(km._git_toplevel(sub), "前提: サブディレクトリはトップレベルではない")

        cfg.state_top = None
        self.assertFalse(km._direct_state_git_ok(cfg), "worktree でなければ従来どおり発動しない")

        cfg.state_top = self.root                          # 状態 worktree へ逃がしている
        self.assertTrue(km._direct_state_git_ok(cfg), "worktree なら direct 同期を使う")
        km._STATE_GITS.clear()
        self.assertIsNotNone(km.state_git_for(cfg), "同期オブジェクトが得られる（None にならない）")

    def test_direct_sync_commits_even_while_user_index_locked(self):
        # 人の git 操作中（index.lock 保持）でも export は止まらない: コミットは detached
        # worktree（専用 index）で組み立て、ブランチは update-ref で進めるため index を使わない。
        cfg = self._cfg()
        mkb(self.root, "T1")
        lock = self.root / ".git" / "index.lock"
        lock.write_text("", encoding="utf-8")
        try:
            km.state_sync(cfg, force=True)
        finally:
            lock.unlink()
        r = subprocess.run(["git", "-C", str(self.root), "log", "-1", "--format=%s"],
                           capture_output=True, text=True)
        self.assertTrue(r.stdout.strip().startswith("kiro-project: state sync"))
        got = self._other("locked-check")
        self.assertTrue((got / "backlog" / "T1.md").exists())   # push まで完走する

    def test_direct_sync_records_deletions(self):
        cfg = self._cfg()
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)
        (self.root / "backlog" / "T1.md").unlink()
        km.state_sync(cfg, force=True)
        got = self._other("del-check")
        self.assertFalse((got / "backlog" / "T1.md").exists())  # 削除も worktree 経由で反映

    def test_direct_sync_keeps_working_tree_clean_after_export(self):
        # CAS でブランチを進めた後、対象パスの index を新 HEAD に追随させる
        # （作業ツリー内容＝コミット内容なので status が clean に戻る）。
        cfg = self._cfg()
        mkb(self.root, "T1")
        km.state_sync(cfg, force=True)
        r = subprocess.run(["git", "-C", str(self.root), "status", "--porcelain",
                            "--", "backlog"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "")

    def test_direct_sync_declares_union_merge_for_journal(self):
        cfg = self._cfg()
        km.state_sync(cfg, force=True)
        attrs = self.root / ".git" / "info" / "attributes"
        self.assertTrue(attrs.is_file())
        self.assertIn("journal.md merge=union", attrs.read_text(encoding="utf-8"))

    def test_direct_sync_merges_concurrent_journal_appends_without_conflict(self):
        # 追記専用の journal.md は union マージで、両ホストの追記行が両方残る（EOF 衝突しない）。
        cfg = self._cfg()
        km.append_journal(self.root / "journal.md", "base line")
        km.state_sync(cfg, force=True)                      # base を共有
        other = self._other("journal-writer")
        with (other / "journal.md").open("a", encoding="utf-8") as f:
            f.write("- 2026-07-12 00:00:00 remote line\n")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "remote journal"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        km.append_journal(self.root / "journal.md", "local line")
        km.state_sync(cfg, force=True)                      # 衝突 → rebase + union で合流
        got = self._other("journal-check")
        text = (got / "journal.md").read_text(encoding="utf-8")
        self.assertIn("remote line", text)
        self.assertIn("local line", text)

    def test_direct_sync_imports_remote_instruction(self):
        cfg = self._cfg()
        other = self._other()
        cmd = other / "commands" / "ok.json"
        cmd.parent.mkdir(parents=True, exist_ok=True)
        cmd.write_text('{"command": "pause"}', encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "viewer: pause"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"],
                       check=True, capture_output=True)
        km.state_sync(cfg, force=True)
        self.assertTrue((km.commands_dir(cfg) / "ok.json").exists())


class TestJournalRotation(unittest.TestCase):
    """journal.md のローテーション: 閾値超過で journal-archive/ へ退避し、保持世代を刈り込む。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.journal = self.tmp / "journal.md"

    def test_no_rotation_below_threshold(self):
        with mock.patch.object(km, "_JOURNAL_MAX_BYTES", 10_000):
            km.append_journal(self.journal, "small")
        self.assertFalse((self.tmp / "journal-archive").exists())

    def test_rotation_archives_and_starts_fresh(self):
        with mock.patch.object(km, "_JOURNAL_MAX_BYTES", 200):
            for i in range(30):
                km.append_journal(self.journal, f"line {i} " + "x" * 40)
        arch = sorted((self.tmp / "journal-archive").iterdir())
        self.assertTrue(arch)                                   # 退避が発生している
        self.assertLess(self.journal.stat().st_size, 400)       # アクティブは小さいまま
        text = self.journal.read_text(encoding="utf-8")
        self.assertIn("journal をローテーション", text)          # 継続の目印を残す
        joined = "".join(p.read_text(encoding="utf-8") for p in arch) + text
        for i in range(30):
            self.assertIn(f"line {i} ", joined)                 # 行は失われない

    def test_rotation_prunes_old_archives(self):
        with mock.patch.object(km, "_JOURNAL_MAX_BYTES", 120), \
             mock.patch.object(km, "_JOURNAL_KEEP", 2):
            for i in range(60):
                km.append_journal(self.journal, f"line {i} " + "y" * 40)
        arch = [p for p in (self.tmp / "journal-archive").iterdir() if p.is_file()]
        self.assertLessEqual(len(arch), 2)                      # 保持世代で刈り込む

    def test_rotation_disabled_with_zero(self):
        with mock.patch.object(km, "_JOURNAL_MAX_BYTES", 0):
            for i in range(50):
                km.append_journal(self.journal, "z" * 80)
        self.assertFalse((self.tmp / "journal-archive").exists())

    def test_build_config_sets_journal_globals(self):
        orig = (km._JOURNAL_MAX_BYTES, km._JOURNAL_KEEP)
        try:
            ns = types.SimpleNamespace(root=str(self.tmp), journal_max_bytes=99,
                                       journal_keep=3)
            km.resolve_config(ns)
            km.build_config(ns)
            self.assertEqual((km._JOURNAL_MAX_BYTES, km._JOURNAL_KEEP), (99, 3))
        finally:
            km._JOURNAL_MAX_BYTES, km._JOURNAL_KEEP = orig


class TestPauseResumeStop(unittest.TestCase):
    """commands/ のプロジェクト単位ライフサイクル指示（pause/resume/stop）。
    リモート viewer が git 越しに watch の消化を止め・再開し・プロセスを畳む口。"""

    def test_ingest_pause_then_resume(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            cdir = km.commands_dir(cfg)
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "p.json").write_text('{"command": "pause", "reason": "検収中"}',
                                         encoding="utf-8")
            done = km.ingest_commands(cfg)
            self.assertIn("pause:project", done)
            self.assertTrue(km.is_paused(cfg))
            self.assertFalse((cdir / "p.json").exists())          # 消費済み
            st = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertTrue(st["paused"])                          # 生存信号に paused が載る
            (cdir / "r.json").write_text('{"command": "resume"}', encoding="utf-8")
            km.ingest_commands(cfg)
            self.assertFalse(km.is_paused(cfg))
            st = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertFalse(st["paused"])

    def test_ingest_stop_raises_graceful(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            cdir = km.commands_dir(cfg)
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "s.json").write_text('{"command": "stop"}', encoding="utf-8")
            with self.assertRaises(km._StopRequested):
                km.ingest_commands(cfg)
            self.assertFalse((cdir / "s.json").exists())          # 再起動時に再停止しない

    def test_watch_skips_pass_while_paused(self):
        # paused の間は run_loop を起こさず、resume されたら消化を再開する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, watch=True)
            km.ensure_dirs(cfg)
            mkb(d, "T1")
            km.pause_path(cfg).write_text("{}", encoding="utf-8")

            def sleeper(_s):                                       # idle 1 回目で人が resume した体
                km.pause_path(cfg).unlink(missing_ok=True)

            last = km.run_watch(cfg, sleeper=sleeper, max_passes=1)
            self.assertEqual(last["reason"], km.REASON_DRAINED)    # resume 後に 1 パス回って消化
            self.assertEqual(last["counts"]["done"], 1)
            self.assertIn("一時停止中", cfg.journal.read_text(encoding="utf-8"))


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

    def test_synth_self_repair_retries_on_degenerate(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            calls = {"n": 0, "prompts": []}
            def flaky(prompt, model):
                calls["n"] += 1
                calls["prompts"].append(prompt)
                return "true" if calls["n"] == 1 else "pytest -q tests/login"
            got = km.synth_verify(cfg, "T", "ログインが通る", kiro_run=flaky)
            self.assertEqual(got, "pytest -q tests/login")   # 1回目の恒真式を捨て 2回目を採用
            self.assertEqual(calls["n"], 2)
            self.assertIn("恒真式", calls["prompts"][1])       # 再合成プロンプトに不採用理由

    def test_synth_self_repair_gives_up_after_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            got = km.synth_verify(cfg, "T", "x", kiro_run=lambda p, m: "true", attempts=3)
            self.assertEqual(got, "")                         # 全て恒真式 → 合成失敗（人へ）

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

    def test_verify_reuse_saved_and_recalled(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            done = km.Task(id="A", title="ログイン e2e A", verify="npx playwright test")
            done.extra.append(("verify_source", "synth"))
            km.save_validated_verify(cfg, done)
            # 類似タイトルの新タスクは合成前に検証済み verify を再利用する
            new = km.Task(id="B", title="ログイン e2e B")
            new.extra.append(("accept", "e2e が通る"))
            km.ensure_verify(cfg, new, kiro_run=lambda p, m: self.fail("再合成された"))
            self.assertEqual(new.verify, "npx playwright test")
            self.assertEqual(dict(new.extra).get("verify_source"), "reused")

    def test_verify_reuse_skips_human_and_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            human = km.Task(id="H", title="t", verify="pytest -q")  # verify_source 無し=人が書いた
            km.save_validated_verify(cfg, human)
            self.assertFalse(km.verify_lib_path(cfg).exists())      # 人の verify は保存しない
            auto = km.Task(id="A", title="t", verify="pytest -q")
            auto.extra.append(("verify_source", "template"))
            km.save_validated_verify(cfg, auto)
            km.save_validated_verify(cfg, auto)                     # 二度目は重複保存しない
            self.assertEqual(km.verify_lib_path(cfg).read_text().count("verifycmd"), 1)

    def test_build_request_injects_similar_learn(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True, exist_ok=True)
            (cfg.decisions / "old.md").write_text(
                "## DR1 2026-01-01 actor: gitlab\n- learn: ログイン e2e :: 実サーバ配備で検証すること\n\n",
                encoding="utf-8")
            task = km.Task(id="NEW", title="ログイン e2e を追加", verify="pytest -q")
            req = km.build_request(task, cfg)
            self.assertIn("類似タスクでの学び", req)
            self.assertIn("実サーバ配備で検証すること", req)   # 分解・実装へ届く

    def test_cohort_reflux_propagates_to_siblings(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            # 同 cohort の 3 メンバ（ready）に cohort タグを付与
            for tid in ("M1", "M2", "M3"):
                mkb(d, tid, status="ready", verify="true", title=f"{tid} 移行")
            for tid in ("M1", "M2", "M3"):
                t = [x for x in km.load_tasks(d / "backlog") if x.id == tid][0]
                t.set("cohort", "C1")
                km.persist_task(cfg, t)
            m1 = [x for x in km.load_tasks(d / "backlog") if x.id == "M1"][0]
            n = km.cohort_reflux(cfg, m1, "パスの命名は kebab-case に統一")
            self.assertEqual(n, 2)                              # M2/M3 に波及（M1 自身は除く）
            for tid in ("M2", "M3"):
                t = [x for x in km.load_tasks(d / "backlog") if x.id == tid][0]
                self.assertIn("kebab-case", t.feedback())

    def test_cohort_reflux_noop_for_non_cohort(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "X", status="ready", title="単発")
            x = km.load_tasks(d / "backlog")[0]
            self.assertEqual(km.cohort_reflux(cfg, x, "指摘"), 0)

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




class RunResumeTests(unittest.TestCase):
    """失敗した run は作り直さず再開する（失敗ノードだけやり直し、done は温存）。

    kiro-flow は failed run を --run-id で受けると retry_failed を実行し、失敗ノードだけを
    pending へ戻して done のノードは温存する。ところが kiro-project は --run-id を一切渡して
    いなかったため、リトライのたびにまっさらな run を作っていた。26 ノードのうち 1 つ失敗した
    だけで、成功していた 25 ノード分の LLM 呼び出しを丸ごと捨てて全部やり直していた。"""

    def _cfg(self, d):
        return cfg_for(Path(d))

    def _run(self, cfg, rid, status):
        p = cfg.bus / "runs" / rid
        p.mkdir(parents=True, exist_ok=True)
        (p / "meta.json").write_text(json.dumps({"status": status, "request": "x"}),
                                     encoding="utf-8")

    def test_failed_run_is_resumed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            self._run(cfg, "req-deadbeef-T1-r0", "failed")
            self.assertEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0", "同じ run を続きから")

    def test_stalled_run_is_resumed(self):
        # orchestrator が消えて status=running のまま止まった run（生存リース切れ）。
        # status だけを見ると救えず、失敗ノードも未実行ノードも永久に放置される。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            p = cfg.bus / "runs" / "req-deadbeef-T1-r0"
            p.mkdir(parents=True, exist_ok=True)
            (p / "meta.json").write_text(json.dumps({
                "status": "running", "orch_lease_until": time.time() - 60}), encoding="utf-8")
            self.assertEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0", "停滞 run は続きから")

    def test_live_run_is_not_resumed(self):
        # まだ実行中（リース有効）の run には触らない（走っているものを壊さない）
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            p = cfg.bus / "runs" / "req-deadbeef-T1-r0"
            p.mkdir(parents=True, exist_ok=True)
            (p / "meta.json").write_text(json.dumps({
                "status": "running", "orch_lease_until": time.time() + 600}), encoding="utf-8")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def _lease_less_run(self, cfg, rid, age_sec):
        """生存リースを持たない非終端 run（heartbeat を張る前に死んだ／旧版が残したもの）。"""
        p = cfg.bus / "runs" / rid
        p.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc) - timedelta(seconds=age_sec)
        (p / "meta.json").write_text(json.dumps({
            "status": "running", "request": "x",
            "updated_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")

    def test_lease_less_stalled_run_is_resumed(self):
        # リース不在を「生きている」と読むと、進捗を抱えた run が永久に宙吊りになる。
        # 実際 kiro-flow run（kiro-project の主経路）は heartbeat を張っておらず、9/31 ノード
        # まで進んだ run が status=running のまま固まり、やり直す手段が無かった。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "run-20260712-213419-5922"))
            self._lease_less_run(cfg, "run-20260712-213419-5922", 2 * 3600)
            self.assertEqual(km.run_id_for(cfg, t), "run-20260712-213419-5922",
                             "リース未記録でも古ければ停滞＝続きから")

    def test_lease_less_fresh_run_is_not_resumed(self):
        # 起動直後（heartbeat を張る前）の run を停滞と誤読して奪わない
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "run-20260712-213419-5922"))
            self._lease_less_run(cfg, "run-20260712-213419-5922", 5)
            self.assertNotEqual(km.run_id_for(cfg, t), "run-20260712-213419-5922",
                                "走り出したばかりの run は触らない")

    def test_canceled_run_is_not_resumed(self):
        # 人が中止した＝その計画を続ける意図がない → 作り直す
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            self._run(cfg, "req-deadbeef-T1-r0", "canceled")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_done_run_is_not_resumed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            self._run(cfg, "req-deadbeef-T1-r0", "done")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_human_feedback_forces_a_fresh_run(self):
        # 人が差し戻した＝計画そのものが変わる → 続きからではなく作り直す
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            t.extra.append(("feedback", "方針を変えて"))
            self._run(cfg, "req-deadbeef-T1-r0", "failed")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_revise_forces_a_fresh_run(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            t.extra.append(("revised", "1"))
            self._run(cfg, "req-deadbeef-T1-r0", "failed")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_missing_run_falls_back_to_new(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true")
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))   # bus に実体が無い
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_new_run_id_carries_the_task_and_retry(self):
        # viewer が run ↔ タスクを突き合わせられる形（req-<hash>-<task-id>-r<n>）
        t = km.Task(id="TASK-9", title="x", status="ready", verify="true", retries=2)
        rid = km._new_run_id(t)
        self.assertTrue(rid.startswith("req-"))
        self.assertIn("TASK-9", rid)
        self.assertTrue(rid.endswith("-r2"))

    def test_cmd_passes_run_id_before_the_subcommand(self):
        # --run-id は kiro-flow のグローバル引数（run サブコマンドより前）
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true")
            cmd = km.build_kiro_flow_cmd(t, cfg, run_id="req-x-T1-r0")
            self.assertIn("--run-id", cmd)
            self.assertLess(cmd.index("--run-id"), cmd.index("run"), "run より前に置く")
            self.assertEqual(cmd[cmd.index("--run-id") + 1], "req-x-T1-r0")


class StateWorktreeTests(unittest.TestCase):
    """状態の読み書きを、本体の作業ツリーから切り離した専用 worktree へ逃がす。

    kiro-project は watch 中 5 秒ごとに journal / status.json / run-log / project.json を書き換える。
    本体の中に置くと人の git status が永久に dirty になり、人やツールの git 操作
    （stash / rebase / pull --autostash）が書き込み中の状態ファイルを巻き込んで壊す
    （実際に project.json がコンフリクトマーカーで JSON として読めなくなった）。"""

    def _repo(self):
        # git は toplevel を realpath で返す（macOS の /var → /private/var）。揃えておく。
        top = Path(tempfile.mkdtemp(prefix="kp-state-")).resolve()
        self.addCleanup(shutil.rmtree, top, True)
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        run = lambda *a: subprocess.run(a, cwd=top, capture_output=True, env=env)
        run("git", "init", "-b", "main", ".")
        run("git", "config", "user.email", "t@e.com")
        run("git", "config", "user.name", "t")
        (top / "README.md").write_text("x\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "init")
        self.addCleanup(lambda: shutil.rmtree(top.parent / f"{top.name}-kiro-state", True))
        return top

    def test_root_is_redirected_into_a_worktree(self):
        top = self._repo()
        root, state_top = km._redirect_root_to_state_worktree(
            top / ".kiro-project", "", "kiro-state")
        self.assertEqual(state_top, top)
        self.assertNotIn(str(top / ".kiro-project"), str(root))     # 本体の中ではない
        self.assertTrue((root.parent / ".git").exists(), "worktree の中を指す")
        # ブランチが切られている
        r = subprocess.run(["git", "-C", str(root.parent), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "kiro-state")

    def test_writing_state_does_not_dirty_the_main_worktree(self):
        top = self._repo()
        root, _ = km._redirect_root_to_state_worktree(top / ".kiro-project", "", "kiro-state")
        root.mkdir(parents=True, exist_ok=True)
        (root / "journal.md").write_text("- 稼働中\n")          # 本体が 5 秒ごとに書くもの
        (root / "status.json").write_text('{"watch": true}\n')
        dirty = subprocess.run(["git", "-C", str(top), "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        self.assertEqual(dirty.strip(), "", "本体の作業ツリーは汚れない")

    def test_existing_state_is_migrated_once(self):
        top = self._repo()
        src = top / ".kiro-project"
        (src / "backlog").mkdir(parents=True)
        (src / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        root, _ = km._redirect_root_to_state_worktree(src, "", "kiro-state")
        self.assertTrue((root / "backlog" / "T1.md").is_file(), "既存の状態が引っ越す")
        self.assertFalse(src.exists(), "本体側は残さない（二重管理を作らない）")

    def test_worktree_checks_out_only_the_state_dir(self):
        """状態 worktree は状態ディレクトリだけを sparse checkout する。

        既定ではリポジトリ全体が展開され、tools/ や docs/ の丸ごとコピーが隣に生える。
        ディスクの無駄というより、人が worktree 側の tools/ を本物と思って編集する事故が怖い
        （そこでの変更は kiro-state ブランチに乗るだけで main には決して届かない）。"""
        top = self._repo()
        (top / "tools").mkdir()
        (top / "tools" / "app.py").write_text("x = 1\n")
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        subprocess.run(["git", "-C", str(top), "add", "-A"], capture_output=True, env=env)
        subprocess.run(["git", "-C", str(top), "commit", "-m", "tools"],
                       capture_output=True, env=env)
        # 本体側に既存の状態がある（初回起動＝worktree へ引っ越す形）
        src = top / ".kiro-project"
        (src / "backlog").mkdir(parents=True)
        (src / "backlog" / "T0.md").write_text("## T0\n")

        root, _ = km._redirect_root_to_state_worktree(src, "", "kiro-state")
        wt = root.parent
        self.assertTrue(root.is_dir(), "状態ディレクトリは出ている")
        self.assertTrue((root / "backlog" / "T0.md").is_file(), "既存の状態は引っ越す")
        # ソースのディレクトリは展開しない（人がここの tools/ を本物と思って編集する事故を防ぐ）。
        # cone モードはルート直下の *ファイル* だけは常に置く（README.md 等）。嵩むのはディレクトリ
        # なので、これで目的は足りる。
        self.assertFalse((wt / "tools").exists(), "他のソースのディレクトリは展開しない")

        # sparse は作業ツリーの見え方だけ。ブランチの中身は完全なので状態のコミットは通る
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        r = subprocess.run(["git", "-C", str(root), "add", "-A", "--", "."],
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0)
        c = subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "state", "--", "."],
                           capture_output=True, env=env)
        self.assertEqual(c.returncode, 0, "状態はコミットできる")
        # 展開していない tools/ が「削除された」と誤認されない（skip-worktree）
        tree = subprocess.run(["git", "-C", str(wt), "ls-tree", "-r", "--name-only", "HEAD"],
                              capture_output=True, text=True, env=env).stdout
        self.assertIn("tools/app.py", tree, "ブランチの中身は完全なまま")

    def test_reuses_the_worktree_on_restart(self):
        top = self._repo()
        a, _ = km._redirect_root_to_state_worktree(top / ".kiro-project", "", "kiro-state")
        a.mkdir(parents=True, exist_ok=True)
        (a / "mark.txt").write_text("keep\n")
        b, _ = km._redirect_root_to_state_worktree(top / ".kiro-project", "", "kiro-state")
        self.assertEqual(a, b, "切りっぱなしの worktree を再利用する")
        self.assertTrue((b / "mark.txt").is_file(), "中身を消さない")

    def test_non_git_root_is_left_alone(self):
        d = Path(tempfile.mkdtemp(prefix="kp-nogit-"))
        self.addCleanup(shutil.rmtree, d, True)
        root, state_top = km._redirect_root_to_state_worktree(d / "p", "", "kiro-state")
        self.assertEqual(root, d / "p")
        self.assertIsNone(state_top)


class StateCommitTests(unittest.TestCase):
    """状態のコミット: 人の判断が動いたら即、実行の副産物だけならまとめる。

    watch は 5 秒ごとに journal / status.json を書き換えるので、毎回コミットすると履歴が秒単位で
    埋まって読めない。意味のある変化（backlog / needs / decisions …）と、実行の副産物を分ける。"""

    def _cfg(self):
        top = Path(tempfile.mkdtemp(prefix="kp-sc-")).resolve()
        self.addCleanup(shutil.rmtree, top, True)
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        run = lambda *a: subprocess.run(a, cwd=top, capture_output=True, env=env)
        run("git", "init", "-b", "main", ".")
        run("git", "config", "user.email", "t@e.com")
        run("git", "config", "user.name", "t")
        (top / "README.md").write_text("x\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "init")
        self.addCleanup(lambda: shutil.rmtree(top.parent / f"{top.name}-kiro-state", True))
        root, state_top = km._redirect_root_to_state_worktree(
            top / ".kiro-project", "", "kiro-state")
        root.mkdir(parents=True, exist_ok=True)
        cfg = cfg_for(root)
        cfg.state_top = state_top
        cfg.state_commit = True
        cfg.state_commit_interval = 3600.0        # 副産物はまとめる（テスト中は跨がない）
        km._last_state_commit = 0.0
        return cfg, root

    def _log(self, root):
        return subprocess.run(["git", "-C", str(root), "log", "--oneline"],
                              capture_output=True, text=True).stdout.strip().split("\n")

    def test_meaningful_change_commits_immediately(self):
        cfg, root = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        self.assertTrue(km.commit_state(cfg))
        self.assertIn("状態を更新", self._log(root)[0])

    def test_noise_only_change_is_batched(self):
        cfg, root = self._cfg()
        km._last_state_commit = time.time()             # 直前にコミット済み＝間隔内
        (root / "journal.md").write_text("- 監視中\n")   # 5 秒ごとの副産物
        (root / "status.json").write_text('{"watch": true}\n')
        self.assertFalse(km.commit_state(cfg), "間隔内はまとめる（コミットしない）")

    def test_noise_commits_once_the_interval_passes(self):
        cfg, root = self._cfg()
        cfg.state_commit_interval = 0.0                 # 間隔ゼロ＝毎回
        (root / "journal.md").write_text("- 監視中\n")
        self.assertTrue(km.commit_state(cfg))
        self.assertIn("実行ログを更新", self._log(root)[0])

    def test_main_worktree_is_never_touched(self):
        cfg, root = self._cfg()
        top = cfg.state_top
        (top / "wip.txt").write_text("人の編集中\n")      # 人が本体で作業している
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        km.commit_state(cfg)
        dirty = subprocess.run(["git", "-C", str(top), "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        self.assertIn("wip.txt", dirty, "人の変更はそのまま（コミットも stash もされない）")
        staged = subprocess.run(["git", "-C", str(top), "diff", "--cached", "--name-only"],
                                capture_output=True, text=True).stdout
        self.assertEqual(staged.strip(), "", "本体の index に触らない")


class StateBackupTests(unittest.TestCase):
    """状態を正本ブランチ（既定 main）へバックアップする。

    状態の実体は worktree（kiro-state）にあり、そこが読み書きの正。正本ブランチへ載せるのは
    バックアップであって共有ではない。だから「人の判断が動いたときだけ」「1 同期 1 コミット」
    「本体の作業ツリーには触らない」「失敗しても本業を止めない」を守る。"""

    def _cfg(self, backup="main"):
        top = Path(tempfile.mkdtemp(prefix="kp-bk-")).resolve()
        self.addCleanup(shutil.rmtree, top, True)
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        run = lambda *a: subprocess.run(a, cwd=top, capture_output=True, env=env)
        run("git", "init", "-b", "main", ".")
        run("git", "config", "user.email", "t@e.com")
        run("git", "config", "user.name", "t")
        (top / "README.md").write_text("x\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "init")
        self.addCleanup(lambda: shutil.rmtree(top.parent / f"{top.name}-kiro-state", True))
        root, state_top = km._redirect_root_to_state_worktree(top / ".kiro-project", "", "kiro-state")
        root.mkdir(parents=True, exist_ok=True)
        cfg = cfg_for(root)
        cfg.state_top = state_top
        cfg.state_commit = True
        cfg.state_commit_interval = 3600.0
        cfg.state_backup_branch = backup
        km._last_state_commit = 0.0
        return cfg, root, top

    def _show(self, top, ref):
        r = subprocess.run(["git", "-C", str(top), "show", ref], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    def _count(self, top, branch):
        r = subprocess.run(["git", "-C", str(top), "rev-list", "--count", branch],
                           capture_output=True, text=True)
        return int(r.stdout.strip() or 0)

    def test_meaningful_change_is_backed_up_to_main(self):
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        self.assertTrue(km.commit_state(cfg))
        self.assertIn("status: ready", self._show(top, "main:.kiro-project/backlog/T1.md") or "",
                      "人の判断が動いたら正本へバックアップされる")

    def test_noise_is_not_pushed_to_main(self):
        # journal / status.json は 5 秒ごとに変わる。正本へ流すとコミットが埋まり、本体で
        # 作業している人の git status も落ち着かない。worktree 側の履歴に留める。
        cfg, root, top = self._cfg()
        cfg.state_commit_interval = 0.0
        before = self._count(top, "main")
        (root / "journal.md").write_text("- 監視中\n")
        self.assertTrue(km.commit_state(cfg), "worktree にはコミットされる")
        self.assertEqual(self._count(top, "main"), before, "正本は動かさない")

    def test_backup_resyncs_a_stale_checkout_instead_of_wedging(self):
        """本体側の .kiro-project が古くても、バックアップのたびに HEAD へ揃え直す。

        ここを「差分＝人の編集かもしれない」と見て避けると自己永続的に詰む: 一度ずれた瞬間に
        永久に同期されなくなり、古いスナップショットが index に staged のまま居座る。その状態で
        main に git commit（パス指定なし）を打つと、バックアップが古い状態へ巻き戻る。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        self.assertTrue(km.commit_state(cfg))                      # main へバックアップ

        stale = top / ".kiro-project" / "backlog" / "T1.md"        # 本体側の鏡を古い内容へ汚す
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("## T1\n- status: review\n- retries: 9\n")
        subprocess.run(["git", "-C", str(top), "add", "--", ".kiro-project"],
                       check=True, capture_output=True)            # index に staged のまま残る状況

        (root / "backlog" / "T1.md").write_text("## T1\n- status: done\n")   # 次の意味ある変化
        self.assertTrue(km.commit_state(cfg))

        self.assertIn("status: done", self._show(top, "main:.kiro-project/backlog/T1.md") or "")
        self.assertEqual(stale.read_text(), "## T1\n- status: done\n", "鏡が HEAD へ揃う")
        r = subprocess.run(["git", "-C", str(top), "status", "--porcelain", "--",
                            ".kiro-project"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "", "staged の古いスナップショットが残らない")

    def test_human_edit_in_mirror_is_adopted_not_destroyed(self):
        """本体側 <repo>/.kiro-project への人の編集は、状態へ取り込んでから鏡を揃える。

        人にとって正本は <repo>/.kiro-project なのに、状態の読み書きは worktree へ逃げている。
        取り込まないと編集は **効かないまま黙って消える**（実際 kiro-flow.yaml の
        evaluator を codex へ切り替えた編集が丸ごと無視されていた）。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        (root / "kiro-flow.yaml").write_text("agents:\n  evaluator: claude\n")
        self.assertTrue(km.commit_state(cfg))                    # main へバックアップ（鏡も揃う）

        mirror = top / ".kiro-project" / "kiro-flow.yaml"
        self.assertEqual(mirror.read_text(), "agents:\n  evaluator: claude\n")
        mirror.write_text("agents:\n  evaluator: codex\n")       # 人が本体側を編集

        (root / "backlog" / "T1.md").write_text("## T1\n- status: done\n")   # 次の意味ある変化
        self.assertTrue(km.commit_state(cfg))

        self.assertEqual((root / "kiro-flow.yaml").read_text(),
                         "agents:\n  evaluator: codex\n", "人の編集が状態へ取り込まれる")
        self.assertTrue(km.commit_state(cfg, force=True))        # 取り込んだ内容が正本へ戻る
        self.assertIn("codex", self._show(top, "main:.kiro-project/kiro-flow.yaml") or "")

    def test_stale_mirror_never_rolls_back_machine_state(self):
        """鏡が古くても、機械が書く状態（backlog 等）は絶対に取り込まない。

        鏡は正本ブランチから遅れうる（バックアップは意味のある変化のときしか走らない）ので、
        「差分＝人の編集」と読むと古い内容で live な状態を巻き戻す。実際それをやって doing の
        タスクが proposed へ戻り、削除済みの cancel ファイルが復活した。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: proposed\n")
        (root / "kiro-flow.yaml").write_text("agents:\n  evaluator: claude\n")
        self.assertTrue(km.commit_state(cfg))                      # 鏡＝この時点の内容

        # 状態だけが先へ進む（鏡は古いまま＝バックアップ前）
        (root / "backlog" / "T1.md").write_text("## T1\n- status: doing\n")
        mirror_cfg = top / ".kiro-project" / "kiro-flow.yaml"
        mirror_cfg.write_text("agents:\n  evaluator: codex\n")     # 人は設定だけを触った

        km.sync_mirror_edits(cfg)
        self.assertEqual((root / "backlog" / "T1.md").read_text(),
                         "## T1\n- status: doing\n", "古い鏡で状態を巻き戻さない")
        self.assertEqual((root / "kiro-flow.yaml").read_text(),
                         "agents:\n  evaluator: codex\n", "設定への人の編集だけ取り込む")

    def test_backup_resyncs_mirror_even_when_nothing_to_commit(self):
        """バックアップ済み（＝新しいコミットは不要）でも、鏡がずれていれば揃え直す。

        ここで早期 return すると、詰んだ状態がまさにそこで止まり続ける: 状態が落ち着いている
        限りバックアップは不要と判断され、古いスナップショットは index に staged のまま
        永久に残る。コミットが要らないときこそ揃え直す必要がある。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n- status: ready\n")
        self.assertTrue(km.commit_state(cfg))

        stale = top / ".kiro-project" / "backlog" / "T1.md"      # 鏡だけを汚す（状態そのものは不変）
        stale.write_text("## T1\n- status: review\n- retries: 9\n")
        subprocess.run(["git", "-C", str(top), "add", "--", ".kiro-project"],
                       check=True, capture_output=True)

        km.backup_state(cfg)                                     # 積むものは無いが鏡は揃うはず
        self.assertEqual(stale.read_text(), "## T1\n- status: ready\n")
        r = subprocess.run(["git", "-C", str(top), "status", "--porcelain", "--",
                            ".kiro-project"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "", "staged の古いスナップショットが残らない")

    def test_backup_does_not_pile_up_commits(self):
        """何度同期しても、正本ブランチに積まれるバックアップは 1 コミットに保たれる。

        毎回 old を親にして積むと、同期のたびに 1 コミット増え、正本ブランチが
        「状態をバックアップ（自動）」で埋まる（実際 main に 18 件積み上がり、「main を極力
        汚染しない」という前提が崩れた）。バックアップは履歴ではなく「その時点の状態」なので、
        未 push の間は置き換えてよい。worktree 側には従来どおり全履歴が残る。"""
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        before = self._count(top, "main")
        for i in range(3):
            (root / "backlog" / f"T{i}.md").write_text(f"## T{i}\n")
            km.commit_state(cfg)
        self.assertEqual(self._count(top, "main"), before + 1,
                         "何度同期しても正本には 1 コミットだけ")
        self.assertGreaterEqual(self._count(top, "kiro-state"), 3, "worktree 側には履歴が残る")
        # 最新の状態がちゃんと載っている（置き換えても内容を落とさない）
        self.assertIsNotNone(self._show(top, "main:.kiro-project/backlog/T2.md"))
        self.assertIsNotNone(self._show(top, "main:.kiro-project/backlog/T0.md"))

    def test_backup_never_rewrites_pushed_history(self):
        # push 済みのバックアップコミットは書き換えない（新しいコミットとして積む）
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        km.commit_state(cfg)
        n = self._count(top, "main")

        # 「push 済み」に見せる（origin/main が現在のバックアップコミットを含む）
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        subprocess.run(["git", "-C", str(top), "update-ref", "refs/remotes/origin/main", "main"],
                       capture_output=True, env=env, check=True)
        self.assertTrue(km._is_pushed(top, "main", "main"), "前提: push 済みと判定される")

        (root / "backlog" / "T2.md").write_text("## T2\n")
        km._last_state_commit = 0.0
        km.commit_state(cfg)
        self.assertEqual(self._count(top, "main"), n + 1, "push 済みなら積む（履歴を壊さない）")

    def test_human_on_another_branch_is_not_disturbed(self):
        # 人が別ブランチで作業していても、正本の ref を進めるだけ（作業ツリーに触らない）
        cfg, root, top = self._cfg()
        subprocess.run(["git", "-C", str(top), "checkout", "-q", "-b", "feature"],
                       capture_output=True)
        (top / "wip.txt").write_text("作業中\n")
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        km.commit_state(cfg)
        head = subprocess.run(["git", "-C", str(top), "symbolic-ref", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, "feature", "人のブランチを動かさない")
        self.assertFalse((top / ".kiro-project").exists(), "人の作業ツリーに書き戻さない")
        self.assertIsNotNone(self._show(top, "main:.kiro-project/backlog/T1.md"),
                             "それでも正本にはバックアップされる")
        dirty = subprocess.run(["git", "-C", str(top), "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        self.assertIn("wip.txt", dirty, "人の変更はそのまま")

    def test_identical_state_makes_no_empty_commit(self):
        cfg, root, top = self._cfg()
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        km.commit_state(cfg)
        n = self._count(top, "main")
        self.assertFalse(km.backup_state(cfg), "同じ内容なら何もしない")
        self.assertEqual(self._count(top, "main"), n, "空コミットを作らない")

    def test_missing_backup_branch_is_ignored(self):
        # 正本ブランチが無い運用（別ブランチ名・浅いクローン）でも実行を止めない
        cfg, root, top = self._cfg(backup="nonexistent")
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        self.assertTrue(km.commit_state(cfg), "本業（worktree へのコミット）は成功する")
        self.assertFalse(km.backup_state(cfg), "バックアップは黙って諦める")

    def test_backup_can_be_disabled(self):
        cfg, root, top = self._cfg(backup="")
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "T1.md").write_text("## T1\n")
        before = self._count(top, "main")
        km.commit_state(cfg)
        self.assertEqual(self._count(top, "main"), before, "空設定でバックアップ無効")

    def test_no_change_no_commit(self):
        cfg, _root, _top = self._cfg()
        cfg.state_commit_interval = 0.0
        self.assertFalse(km.commit_state(cfg))

    def test_sibling_project_state_is_not_swallowed(self):
        """同じリポジトリの別プロジェクトの状態を、自分のコミットに巻き込まない。

        <repo>/.kiro-project と <repo>/sub/.kiro-project は state worktree を共有する。
        commit を root 配下に限定しないと index 全体をコミットしてしまい、隣が add した直後に
        自分が commit すると相手の状態を取り込む。取り込まれた側は「ステージに何も乗らない」と
        判断して自分のコミットを作れず、結果として **相手の状態が正本へバックアップされない**。"""
        cfg, root, top = self._cfg(backup="")
        sub = km._redirect_root_to_state_worktree(top / "sub" / ".kiro-project", "", "kiro-state")[0]
        (sub / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog").mkdir(parents=True, exist_ok=True)
        (root / "backlog" / "A1.md").write_text("## A1\n")
        (sub / "backlog" / "B1.md").write_text("## B1\n")
        # 隣（sub）が自分の commit より先にステージへ載せる
        subprocess.run(["git", "-C", str(sub), "add", "-A", "--", "."], capture_output=True)
        self.assertTrue(km.commit_state(cfg))
        wt = km._git_toplevel_of(root)
        files = subprocess.run(["git", "-C", str(wt), "show", "--name-only", "--format=", "HEAD"],
                               capture_output=True, text=True).stdout
        self.assertIn(".kiro-project/backlog/A1.md", files, "自分の状態はコミットする")
        self.assertNotIn("sub/", files, "隣の状態は巻き込まない")


class EnsureNeedsTests(unittest.TestCase):
    """needs は status の投影＝失われたら status から作り直す（自己修復）。

    従来は「状態が変わった瞬間」にしか票を書かず、proposed だけが ensure で守られていた。
    そのため blocked/review の票が失われると二度と作られず、backlog は blocked のままなのに
    viewer の要対応画面には出てこない（viewer の操作ボタンは全て needs カードに紐づくため、
    人は承認も再実行も差し戻しもできない袋小路に入った）。"""

    def _cfg(self, d):
        return cfg_for(Path(d), plan_review=True)

    def test_lost_blocked_card_is_rebuilt_with_its_reason(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T1", title="x", status="blocked", verify="true")
            km._remember_needs_reason(t, "繰り返し NG（retries=3）: exit=1")
            km.persist_task(cfg, t)
            self.assertFalse(km.needs_path(cfg, "T1").exists())   # 票が失われた状態

            made = km.ensure_needs(cfg, [t])
            self.assertEqual(made, ["T1"])
            body = km.needs_path(cfg, "T1").read_text(encoding="utf-8")
            self.assertIn("繰り返し NG（retries=3）", body)        # 理由も復元される
            self.assertIn("kind: blocked", body)

    def test_lost_review_card_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T2", title="x", status="review", verify="true")
            km.persist_task(cfg, t)
            km.ensure_needs(cfg, [t])
            self.assertIn("kind: review", km.needs_path(cfg, "T2").read_text(encoding="utf-8"))

    def test_existing_card_is_never_overwritten(self):
        # 人が記入中の票を消さない
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T3", title="x", status="blocked", verify="true")
            km.persist_task(cfg, t)
            km.ensure_needs(cfg, [t])
            p = km.needs_path(cfg, "T3")
            p.write_text(p.read_text(encoding="utf-8") + "\n人の記入\n", encoding="utf-8")
            self.assertEqual(km.ensure_needs(cfg, [t]), [])       # 再生成しない
            self.assertIn("人の記入", p.read_text(encoding="utf-8"))

    def test_running_states_get_no_card(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = [km.Task(id=f"T{i}", title="x", status=s, verify="true")
                     for i, s in enumerate(("ready", "doing", "done"))]
            self.assertEqual(km.ensure_needs(cfg, tasks), [])

    def test_enqueue_creates_the_review_card_immediately(self):
        # 従来はループのパス頭まで票が作られず、その間「backlog は承認待ち・要対応画面には無い」
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            args = types.SimpleNamespace(
                json=False, file=None, id="T9", title="新規", verify="true", priority=0,
                source="human", status=None, after=None, review=None, note=None, accept=None,
                verify_template=None, repos=None, cohort_items=None)
            self.assertEqual(km.cmd_enqueue(cfg, args), 0)
            self.assertTrue(km.needs_path(cfg, "T9").exists(), "投入したその場で票ができる")


class RecoverStaleDoingTests(unittest.TestCase):
    """実行者が失踪した doing を ready へ戻す（再起動・クラッシュの自己回復）。

    doing は CONSUMABLE（ready/todo）ではないので次のパスでも拾われない。実行していた
    プロセスがいなくなると、claim ロックだけを残して永久に doing のまま止まる
    （viewer には「実行中」と見えるのに何も進まない）。"""

    def _doing(self, cfg, tid="T1"):
        t = km.Task(id=tid, title="x", status="doing", verify="true")
        km.persist_task(cfg, t)
        return t

    def _claim(self, cfg, tid, pid, host=None, ts=None):
        d = km._claims_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{tid}.lock").write_text(json.dumps({
            "host": host or socket.gethostname(), "pid": pid,
            "ts": ts if ts is not None else time.time(), "id": tid}), encoding="utf-8")

    def test_dead_owner_is_recovered(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            km.ensure_dirs(cfg)
            t = self._doing(cfg)
            self._claim(cfg, "T1", pid=999999)          # 存在しない pid＝失踪
            self.assertEqual(km.recover_stale_doing(cfg, [t]), ["T1"])
            self.assertEqual(t.norm_status(), "ready")
            self.assertFalse((km._claims_dir(cfg) / "T1.lock").exists(), "claim を解放する")
            self.assertEqual(t.retries, 0, "retries は据え置き（worker の失敗ではない）")

    def test_live_owner_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            km.ensure_dirs(cfg)
            t = self._doing(cfg)
            self._claim(cfg, "T1", pid=os.getpid())     # 自分＝生きている
            self.assertEqual(km.recover_stale_doing(cfg, [t]), [])
            self.assertEqual(t.norm_status(), "doing")

    def test_missing_claim_is_recovered(self):
        # claim ごと失われた doing（同期事故など）も救う
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            km.ensure_dirs(cfg)
            t = self._doing(cfg)
            self.assertEqual(km.recover_stale_doing(cfg, [t]), ["T1"])

    def test_remote_host_follows_ttl(self):
        # 別ホストは pid の生死を確かめられない → TTL に従う（新鮮なら触らない）
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            km.ensure_dirs(cfg)
            t = self._doing(cfg)
            self._claim(cfg, "T1", pid=1, host="other-host", ts=time.time())
            self.assertEqual(km.recover_stale_doing(cfg, [t]), [])
            self._claim(cfg, "T1", pid=1, host="other-host", ts=0)   # TTL 超過
            self.assertEqual(km.recover_stale_doing(cfg, [t]), ["T1"])


class TestPlanReview(unittest.TestCase):
    """実行前レビュー（plan_review・本番既定 on）: 新規タスクは proposed で入り、
    人の承認（approve）・差し戻し（feedback→kiro-project が修正）・却下（reject）を通る。"""

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
            with mock.patch.object(km, "_run_kiro_cli", return_value=fake):
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
            with mock.patch.object(km, "_run_kiro_cli", side_effect=RuntimeError("kiro-cli 不在")):
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


class TestRejectAndImpact(unittest.TestCase):
    """却下（reject）: 廃止して archive へ退避＋依存先を再審査（proposed）へ＋charter があれば
    再計画を要求。impact: after 逆辺の影響範囲を一覧提示する。"""

    def test_reject_archives_and_reproposes_dependents(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, learn_capture=True)
            mkb(d, "T1", verify="true")
            mkb(d, "T2", verify="true")
            # T2 は T1 に依存・T3 は T2 に依存（推移）
            t2 = km.load_tasks(cfg.backlog)[1]
            t2.extra.append(("after", "T1"))
            km.persist_task(cfg, t2)
            mkb(d, "T3", verify="true")
            t3 = [t for t in km.load_tasks(cfg.backlog) if t.id == "T3"][0]
            t3.extra.append(("after", "T2"))
            km.persist_task(cfg, t3)
            rc = km.cmd_reject(cfg, "T1", "方針転換で不要")
            self.assertEqual(rc, 0)
            # 本体は rejected として archive へ
            self.assertFalse((cfg.backlog / "T1.md").exists())
            arch = (d / "archive" / "T1.md").read_text(encoding="utf-8")
            self.assertIn("rejected", arch)
            self.assertIn("却下記録", arch)
            # 依存先（推移）は proposed に戻り、after から T1 が外れる
            got = {t.id: t for t in km.load_tasks(cfg.backlog)}
            self.assertEqual(got["T2"].norm_status(), "proposed")
            self.assertEqual(got["T3"].norm_status(), "proposed")
            self.assertNotIn("T1", km.task_deps(got["T2"]))
            self.assertTrue((cfg.needs / "T2.md").exists())    # 再審査票
            # avoid（回避知識）が残る
            dec = (cfg.decisions / "T1.md").read_text(encoding="utf-8")
            self.assertIn("- avoid:", dec)
            self.assertIn("reject", dec)

    def test_reject_requests_replan_when_charter_exists(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.charter.write_text("# Charter: demo\n## goal\nx\n## acceptance\n- `true`\n",
                                   encoding="utf-8")
            mkb(d, "T1", verify="true")
            km.cmd_reject(cfg, "T1", "作り直す")
            self.assertTrue(km.replan_request_path(cfg).exists())   # 再計画を要求

    def test_reject_refuses_doing_with_fresh_claim(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1", status="doing", verify="true")
            rc = km.cmd_reject(cfg, "T1", "x")
            self.assertEqual(rc, 2)

    def test_rejected_title_not_replanned(self):
        # rejected は archive に居るため _existing_titles に含まれ、同一タイトルの再提案を冪等排除できる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1", title="決済APIを追加", verify="true")
            km.cmd_reject(cfg, "T1", "スコープ外")
            self.assertIn("決済APIを追加", km._existing_titles(cfg))

    def test_impact_lists_upstream_and_downstream(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "A", verify="true")
            mkb(d, "B", verify="true")
            mkb(d, "C", verify="true")
            tasks = {t.id: t for t in km.load_tasks(cfg.backlog)}
            tasks["B"].extra.append(("after", "A"))
            km.persist_task(cfg, tasks["B"])
            tasks["C"].extra.append(("after", "B"))
            km.persist_task(cfg, tasks["C"])
            all_tasks = km.load_tasks(cfg.backlog)
            downs = [t.id for t in km.dependents_of(all_tasks, "A")]
            self.assertEqual(sorted(downs), ["B", "C"])        # 推移閉包
            ups = km.prerequisites_of(all_tasks, "C")
            self.assertEqual(sorted(ups), ["A", "B"])
            self.assertEqual(km.cmd_impact(cfg, "A"), 0)
            self.assertEqual(km.cmd_impact(cfg, "zzz"), 2)




class TestMultiCharter(unittest.TestCase):
    """複数 charter（charters/<name>.md）: 1 プロジェクトで複数バージョンを並行駆動する。
    タスクは charter タグでスコープされ、plan の冪等照合・drained 判定・milestone/state は
    charter 単位に閉じる（execute の backlog は共有）。"""

    def _mk_charter(self, d, name, goal="やる"):
        cdir = d / "charters"
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{name}.md").write_text(
            f"# Charter: {name}\n## goal\n{goal}\n## acceptance\n- `true`\n", encoding="utf-8")

    def test_charter_names_and_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self.assertEqual(km.charter_names(cfg), [])                 # charter 無し
            cfg.charter.write_text("# Charter: solo\n## goal\nx\n", encoding="utf-8")
            self.assertEqual(km.charter_names(cfg), ["default"])        # 単一 charter.md
            self._mk_charter(d, "v2")
            self._mk_charter(d, "v1")
            self.assertEqual(km.charter_names(cfg), ["v1", "v2"])       # charters/ が優先・名前順
            chs = dict(km.load_charters(cfg))
            self.assertIn("v1", chs)
            self.assertEqual(chs["v2"].name, "v2")

    def test_reconcile_milestones_is_pure_projection_of_status(self):
        # 根本対策:「要対応マイルストーンが何度も復活する」。milestone ファイルは project.json の
        # status の純粋な投影であり、reconcile_milestones が唯一の調整点。承認済み・削除済み
        # バージョン・旧トップレベルの milestone は毎回消え、no-acceptance/converged は残る。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            cfg = cfg_for(d, project_name="proj")
            cdir = d / "charters"; cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "v1.md").write_text(
                f"# Charter: v1\n## goal\nv1\n## acceptance\n- `test -f {flag}`\n", encoding="utf-8")
            (cdir / "v2.md").write_text(          # 完了条件なし → no-acceptance
                "# Charter: v2\n## goal\nv2\n## acceptance\n", encoding="utf-8")
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained(), charter_name="v1")
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained(), charter_name="v2")
            data = km.load_project_state(cfg)
            self.assertEqual(data["charters"]["v1"]["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(data["charters"]["v2"]["status"], km.REASON_PROJECT_NO_ACCEPTANCE)

            # (1) 承認済みの milestone が復活しても GC が毎回消す
            km.finalize_project(cfg, data["charters"]["v1"], "OK",
                                charter=km._load_named_charter(cfg, "v1"), charter_name="v1")
            (cfg.needs / "proj-v1.md").write_text("# マイルストーン: v1\nkind: milestone\n", encoding="utf-8")
            # frontmatter kind を正しく（reconcile は kind=milestone だけ対象）
            (cfg.needs / "proj-v1.md").write_text(
                "---\nkind: milestone\n---\n# マイルストーン: v1\n", encoding="utf-8")
            km.reconcile_milestones(cfg)
            self.assertFalse((cfg.needs / "proj-v1.md").exists())   # accepted → 消える
            self.assertTrue((cfg.needs / "proj-v2.md").exists())    # no-acceptance → 残る

            # (2) 存在しないバージョンの milestone（orphan）も消す
            (cfg.needs / "proj-vX.md").write_text(
                "---\nkind: milestone\n---\n# マイルストーン: vX\n", encoding="utf-8")
            km.reconcile_milestones(cfg)
            self.assertFalse((cfg.needs / "proj-vX.md").exists())

            # (3) タスク級の needs（kind != milestone）は触らない
            (cfg.needs / "T1.md").write_text(
                "---\nkind: review\n---\n# 要対応: T1\n", encoding="utf-8")
            km.reconcile_milestones(cfg)
            self.assertTrue((cfg.needs / "T1.md").exists())

    def test_version_run_clears_stale_toplevel_milestone(self):
        # 実運用インシデントの再発防止:「要対応のマイルストーンが二度出る」。
        # 単一 charter.md で一度 run（トップレベル milestone needs/<project>.md を作る）した後に
        # charters/ を足してバージョン運用へ移行すると、旧トップレベル milestone が残り、
        # <project>.md と <project>-<version>.md の 2 枚が要対応に並んでしまう。
        # バージョン運用の run に入ったら旧トップレベル milestone を掃除する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))       # 単一 charter.md
            cfg = cfg_for(d, project_name="proj")
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertTrue((cfg.needs / "proj.md").exists())            # トップレベル milestone

            self._mk_charter(d, "v1", goal="v1")                        # バージョンへ移行
            (d / "charters" / "v1.md").write_text(
                f"# Charter: v1\n## goal\nv1\n## acceptance\n- `test -f {flag}`\n", encoding="utf-8")
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained(),
                           charter_name="v1")
            self.assertTrue((cfg.needs / "proj-v1.md").exists())         # バージョンの milestone
            self.assertFalse((cfg.needs / "proj.md").exists())          # 旧トップレベルは掃除される
            self.assertEqual(len(list(cfg.needs.glob("*.md"))), 1)      # 要対応は 1 枚だけ

            # v1 を承認（accepted）した後でも、再び現れた旧トップレベル milestone は掃除される
            # （掃除は accepted の早期 return より前で行うため取り残さない）。
            self.assertEqual(km.cmd_approve(cfg, "proj-v1", "OK"), 0)
            (cfg.needs / "proj.md").write_text("# マイルストーン: proj\n", encoding="utf-8")  # 再発を模す
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained(),
                           charter_name="v1")                            # accepted → 早期 return
            self.assertFalse((cfg.needs / "proj.md").exists())          # それでも掃除される

    def test_cmd_project_tags_tasks_and_scopes_state(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            self._mk_charter(d, "v1")
            planner = lambda ch: [{"title": f"{ch.name} のタスク", "verify": "true"}]
            rc = km.cmd_project(cfg, planner=planner, reviewer=lambda ch: [],
                                charter_name="v1")
            self.assertEqual(rc, 1)                                     # 収束候補 → 人待ち
            # タスクに charter タグが付く（アーカイブ済みを含めて確認）
            arch = list((d / "archive").glob("*.md"))
            self.assertTrue(arch)
            t = km.parse_task(arch[0].read_text(encoding="utf-8"), arch[0].stem)
            self.assertEqual(t.get("charter"), "v1")
            # state は project.json の charters マップに閉じる
            data = km.load_project_state(cfg)
            self.assertIn("v1", data.get("charters", {}))
            pid = data["charters"]["v1"]["id"]
            self.assertTrue(pid.endswith("-v1"))                        # milestone id は charter 別
            self.assertTrue((cfg.needs / f"{pid}.md").exists())

    def test_milestone_heading_uses_version_name(self):
        # milestone 票の見出しはバージョン名（ファイル名）を正とする。charter の宣言名が
        # 前バージョンのコピー等でプロジェクト名のまま食い違っても、バージョンで識別できる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            cdir = d / "charters"; cdir.mkdir(parents=True, exist_ok=True)
            # 宣言名は「sandbox」だがファイル名（バージョン）は「v2」
            (cdir / "v2.md").write_text(
                "# Charter: sandbox\n## goal\nやる\n## acceptance\n- `true`\n", encoding="utf-8")
            km.cmd_project(cfg, planner=lambda ch: [], reviewer=lambda ch: [], charter_name="v2")
            pid = km.load_project_state(cfg)["charters"]["v2"]["id"]
            body = (cfg.needs / f"{pid}.md").read_text(encoding="utf-8")
            self.assertIn("# マイルストーン: v2（sandbox）", body)     # バージョン名で識別＋宣言名併記
            self.assertNotIn("# マイルストーン: sandbox\n", body)

    def test_two_charters_plan_independently(self):
        # v1 に消化可能タスクが残っていても v2 の plan は起こる（drained 判定のスコープ）。
        # 同名タスクでも charter が違えば冪等排除しない（existing のスコープ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1, dry_run=True)
            self._mk_charter(d, "v1")
            self._mk_charter(d, "v2")
            planner = lambda ch: [{"title": "共通タイトルの作業", "verify": "true"}]
            km.cmd_project(cfg, planner=planner, reviewer=lambda ch: [], charter_name="v1")
            # v1 のタスクを未消化のまま残す（doing 相当ではなく ready のタスクを積み直す）
            km.enqueue_task(cfg, {"title": "v1 残作業", "verify": "true", "charter": "v1",
                                  "status": "ready"})
            km.cmd_project(cfg, planner=planner, reviewer=lambda ch: [], charter_name="v2")
            # v2 にも同名タスクが plan された（archive/backlog を charter タグで数える）
            tagged = []
            for f in list((d / "archive").glob("*.md")) + list((d / "backlog").glob("*.md")):
                t = km.parse_task(f.read_text(encoding="utf-8"), f.stem)
                if t.title == "共通タイトルの作業":
                    tagged.append(t.get("charter"))
            self.assertIn("v1", tagged)
            self.assertIn("v2", tagged)

    def test_replan_request_scoped_to_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._mk_charter(d, "v1")
            self._mk_charter(d, "v2")
            km.write_replan_request(cfg, "v2 を作り直す", charter="v2")
            self.assertIsNone(km.consume_replan_request(cfg, "v1"))     # 別 charter 宛 → 残す
            self.assertTrue(km.replan_request_path(cfg).exists())
            got = km.consume_replan_request(cfg, "v2")                  # 対象 charter が消化
            self.assertEqual(got.get("charter"), "v2")
            self.assertFalse(km.replan_request_path(cfg).exists())
            # charter 指定の無い要求はどの charter でも消化できる
            km.write_replan_request(cfg, "全体")
            self.assertIsNotNone(km.consume_replan_request(cfg, "v1"))

    def test_run_single_drives_all_charters(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            self._mk_charter(d, "v1")
            self._mk_charter(d, "v2")
            seen = []
            orig = km.cmd_project

            def spy(c, *a, **kw):
                seen.append(kw.get("charter_name"))
                return orig(c, planner=lambda ch: [], reviewer=lambda ch: [],
                            charter_name=kw.get("charter_name"))

            with mock.patch.object(km, "cmd_project", side_effect=spy):
                km._run_single(cfg)
            self.assertEqual(seen, ["v1", "v2"])                        # 全 charter を順に回す

    def test_project_watch_round_robins_charters(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            self._mk_charter(d, "v1")
            self._mk_charter(d, "v2")
            seen = []
            planner = lambda ch: seen.append(ch.name) or []
            km.project_watch(cfg, planner=planner, reviewer=lambda ch: [],
                             runner=km.run_loop, sleeper=lambda _s: None, max_passes=2)
            self.assertEqual(seen, ["v1", "v2"])                        # 両バージョンを 1 パスずつ

    def test_milestone_approve_finalizes_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            self._mk_charter(d, "v1")
            km.cmd_project(cfg, planner=lambda ch: [], reviewer=lambda ch: [],
                           charter_name="v1")
            data = km.load_project_state(cfg)
            pid = data["charters"]["v1"]["id"]
            self.assertEqual(data["charters"]["v1"]["status"], km.REASON_PROJECT_CONVERGED)
            rc = km.cmd_approve(cfg, pid, "受領")
            self.assertEqual(rc, 0)
            data = km.load_project_state(cfg)
            self.assertEqual(data["charters"]["v1"]["status"], km.REASON_PROJECT_ACCEPTED)

    def test_build_request_injects_task_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._mk_charter(d, "v1", goal="V1-GOAL 保守")
            self._mk_charter(d, "v2", goal="V2-GOAL 新機能")
            t = km.enqueue_task(cfg, {"title": "x", "verify": "true", "charter": "v2"})
            req = km.build_request(t, cfg)
            self.assertIn("V2-GOAL", req)                               # タグの charter を注入
            self.assertNotIn("V1-GOAL", req)

    def test_single_charter_md_backward_compatible(self):
        # charter.md 単体は従来どおり（state はトップレベル・milestone id に接尾辞なし）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            cfg.charter.write_text("# Charter: solo\n## goal\nx\n## acceptance\n- `true`\n",
                                   encoding="utf-8")
            km.cmd_project(cfg, planner=lambda ch: [], reviewer=lambda ch: [],
                           charter_name="default")
            data = km.load_project_state(cfg)
            self.assertNotIn("charters", data)                          # 従来のトップレベル形
            self.assertFalse(str(data.get("id", "")).endswith("-default"))




class TestTaskBranchAndDeliveryReview(unittest.TestCase):
    """タスク単位ターゲットブランチ（kp/<task-id>）と成果物レビュー（delivery_review・本番既定 on）。
    review 到達時に MR を用意し、承認で Stage 2 と同一規則の自動決着（クリーンならマージ）を行う。"""

    def test_workspace_spec_injects_task_branch(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True, task_branch_prefix="kp/")
            mkb(d, "T1")
            t = km.load_tasks(cfg.backlog)[0]
            t.extra.append(("workspace", "https://gitlab.example.com/g/app.git"))
            spec = km._workspace_spec_for(cfg, t)
            self.assertEqual(spec["branch"], "kp/T1")          # 全試行を集約するブランチ
            token = km._workspace_token(spec)
            self.assertIn('"branch":"kp/T1"', token)           # kiro-flow へ伝搬
            cfg2 = cfg_for(d, task_branch=False)
            spec2 = km._workspace_spec_for(cfg2, t)
            self.assertNotIn("branch", spec2 or {})            # off なら従来（run 毎 kf/<run-id>）

    def test_delivery_review_gates_done(self):
        # delivery_review=True: verify PASS でも自動 done せず review（検収待ち）へ
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, delivery_review=True, learn=False, auto_adjudicate=False)
            mkb(d, "T1", verify="true")
            result = km.run_loop(cfg)
            self.assertEqual(result["counts"]["review"], 1)    # done でなく検収待ち
            self.assertEqual(result["counts"]["done"], 0)
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.norm_status(), "review")
            self.assertTrue((cfg.needs / "T1.md").exists())
            self.assertEqual(km.exit_code_for(result), 1)      # 人の対応待ち

    def test_gl_parse_repo_forms(self):
        self.assertEqual(km._gl_parse_repo("https://gitlab.com/g/app.git"),
                         ("gitlab.com", "g/app"))
        self.assertEqual(km._gl_parse_repo("https://gl.example.com/team/sub/app"),
                         ("gl.example.com", "team/sub/app"))
        self.assertEqual(km._gl_parse_repo("git@gitlab.com:g/app.git"),
                         ("gitlab.com", "g/app"))
        self.assertIsNone(km._gl_parse_repo("/local/path/repo"))

    def _mr_task(self, cfg, d):
        mkb(d, "T1", verify="true")
        t = km.load_tasks(cfg.backlog)[0]
        t.extra.append(("workspace", "https://gitlab.example.com/g/app.git"))
        return t

    def test_ensure_task_mr_creates_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True)
            t = self._mr_task(cfg, d)
            calls = []

            def api(host, token, method, path, data=None, params=None):
                calls.append((method, path, data, params))
                if method == "GET" and path.endswith("/merge_requests"):
                    return []                                   # 既存 MR 無し
                if method == "POST" and path.endswith("/merge_requests"):
                    return {"iid": 7, "web_url": "https://gitlab.example.com/g/app/-/merge_requests/7"}
                return {}

            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=api):
                url = km.ensure_task_mr(cfg, t)
            self.assertIn("/merge_requests/7", url)
            self.assertEqual(t.get("mr_iid"), "7")
            post = next(c for c in calls if c[0] == "POST")
            self.assertEqual(post[2]["source_branch"], "kp/T1")
            self.assertTrue(post[2]["remove_source_branch"])
            # 冪等: mr_url 記録済みなら API を呼ばない
            with mock.patch.object(km, "_gl_api", side_effect=AssertionError("再作成しない")):
                self.assertEqual(km.ensure_task_mr(cfg, t), url)

    def test_ensure_task_mr_skips_without_gitlab(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True)
            t = self._mr_task(cfg, d)
            with mock.patch.object(km, "_gl_token", return_value=""):
                self.assertEqual(km.ensure_task_mr(cfg, t), "")   # トークン無し＝記録のみで続行

    def _review_task_with_mr(self, cfg, d):
        t = self._mr_task(cfg, d)
        t.status = "review"
        t.extra += [("mr_url", "u7"), ("mr_iid", "7"), ("mr_project", "gitlab.example.com|g/app")]
        km.persist_task(cfg, t)
        return t

    def test_approve_merges_clean_mr_and_finalizes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            calls = []

            def api(host, token, method, path, data=None, params=None):
                calls.append((method, path, data))
                if method == "GET" and path.endswith("/merge_requests/7"):
                    return {"state": "opened", "merge_status": "can_be_merged",
                            "has_conflicts": False}
                if path.endswith("/discussions"):
                    return []
                if path.endswith("/changes"):
                    return {"changes": [{"new_path": "a.py"}]}
                return {}

            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=api):
                rc = km.cmd_approve(cfg, t.id, "検収OK")
            self.assertEqual(rc, 0)
            self.assertTrue(any(m == "PUT" and p.endswith("/merge") for m, p, _ in calls))
            self.assertTrue((d / "archive" / f"{t.id}.md").exists())   # done 確定

    def test_approve_unclean_mr_keeps_review(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            calls = []

            def api(host, token, method, path, data=None, params=None):
                calls.append((method, path, data))
                if method == "GET" and path.endswith("/merge_requests/7"):
                    return {"state": "opened", "merge_status": "cannot_be_merged",
                            "has_conflicts": True}
                if path.endswith("/discussions"):
                    return []
                if path.endswith("/changes"):
                    return {"changes": [{"new_path": "a.py"}]}
                return {}

            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=api):
                rc = km.cmd_approve(cfg, t.id, "検収OK")
            self.assertEqual(rc, 1)                              # done にしない
            got = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(got.norm_status(), "review")        # review のまま
            note = next(c for c in calls if c[0] == "POST" and c[1].endswith("/notes"))
            self.assertIn("差し戻し", note[2]["body"])

    def test_approve_without_mr_is_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1", status="review", verify="true")
            rc = km.cmd_approve(cfg, "T1", "OK")
            self.assertEqual(rc, 0)                              # MR 無しは従来どおり done 確定のみ
            self.assertTrue((d / "archive" / "T1.md").exists())

    def test_reject_closes_mr_and_deletes_branch(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            calls = []

            def api(host, token, method, path, data=None, params=None):
                calls.append((method, path, data))
                return {}

            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=api):
                rc = km.cmd_reject(cfg, t.id, "作り直す")
            self.assertEqual(rc, 0)
            self.assertTrue(any(m == "PUT" and p.endswith("/merge_requests/7")
                                and (dd or {}).get("state_event") == "close"
                                for m, p, dd in calls))          # MR クローズ
            self.assertTrue(any(m == "DELETE" and "/repository/branches/" in p
                                for m, p, _ in calls))           # ソースブランチ削除


class DeliveryEvidenceTests(unittest.TestCase):
    """検収の判断材料は「レビューすべき実体のコード」を示すこと。

    成果物は worker が作業ブランチ kp/<task-id> へコミットする。ところが判断材料は cfg.workdir を
    見ていて、状態 worktree（<repo>-kiro-state/.kiro-project）を指すため、出てくるのは bus/ の
    claims や events の JSON ばかりだった。人は「何をどうレビューすればいいのか分からない」まま
    承認を迫られる（実際そうなっていた: 変更ファイル 23 件がすべて bus の内部ファイル）。"""

    def _repo(self):
        top = Path(tempfile.mkdtemp(prefix="kp-ev-")).resolve()
        self.addCleanup(shutil.rmtree, top, True)
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        run = lambda *a: subprocess.run(a, cwd=top, capture_output=True, env=env)
        run("git", "init", "-b", "main", ".")
        run("git", "config", "user.email", "t@e.com")
        run("git", "config", "user.name", "t")
        (top / "src.py").write_text("x = 1\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "init")
        # worker の作業ブランチ（実体のコード変更）
        run("git", "checkout", "-q", "-b", "kp/T1")
        (top / "src.py").write_text("x = 2\n")
        (top / "test_src.py").write_text("assert True\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "[kiro-flow] t1")
        run("git", "checkout", "-q", "main")
        return top

    def _cfg_with_run(self, top, d):
        """状態 worktree 側に root を置き（＝本番と同じ形）、run メタに作業ブランチを記録する。"""
        cfg = cfg_for(d)
        cfg.state_top = top                      # 成果物のあるリポジトリは本体側
        t = km.Task(id="T1", title="直す", status="doing", verify="true")
        t.extra.append(("last_run", "req-abc-T1-r0"))
        p = cfg.bus / "runs" / "req-abc-T1-r0"
        p.mkdir(parents=True, exist_ok=True)
        (p / "meta.json").write_text(json.dumps({
            "status": "done",
            "workspace": {"base": "main", "branch": "kp/T1"}}), encoding="utf-8")
        return cfg, t

    def test_evidence_lists_the_real_code_files(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg, t = self._cfg_with_run(top, Path(d))
            ev = km.delivery_evidence(cfg, "", None, "local",
                                      verify="true", vmsg="ok", ok=True, task=t)
            self.assertIn("kp/T1", ev, "成果物は作業ブランチ")
            self.assertIn("src.py", ev, "レビューすべき実体ファイルが出る")
            self.assertIn("test_src.py", ev)
            self.assertIn("diff main...", ev, "差分を開くコマンドを添える")
            self.assertNotIn("bus/", ev, "bus の内部ファイルは判断材料ではない")
            self.assertNotIn("claims", ev)

    def test_risk_counts_real_files_not_bus_internals(self):
        # 大差分（>=10）判定も実体ファイルで行う。bus の JSON を数えると無関係に med へ跳ねる。
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg, t = self._cfg_with_run(top, Path(d))
            wb = km._task_work_branch(cfg, t)
            self.assertEqual(wb, ("main", "kp/T1"))
            _ref, files = km.work_branch_changes(cfg, *wb)
            self.assertEqual(sorted(files), ["src.py", "test_src.py"])

    def test_falls_back_when_no_work_branch(self):
        # 単発実行（作業ブランチが無い）では従来どおり workdir を見る
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            t = km.Task(id="T9", title="単発", verify="true")
            ev = km.delivery_evidence(cfg, "", None, "local", task=t)
            self.assertIn("- 成果物:", ev)


class RiskDigestTests(unittest.TestCase):
    """検収（review）前のリスクダイジェスト（決定的な材料のみ・needs の ## リスク節と
    frontmatter risk: low/med/high）。承認フローは変えず情報だけが増えることを検証する。"""

    def test_levels_are_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="安全な作業")
            level, md = km.risk_digest(cfg, t, set(), [])
            self.assertEqual(level, "low")
            self.assertIn("総合: 低", md)
            # protect 接触 → high
            level, md = km.risk_digest(cfg, t, {"auth/x.py"}, [("auth/x.py", "auth/**")])
            self.assertEqual(level, "high")
            self.assertIn("保護パス接触", md)
            # リトライ経験 → med
            t2 = km.Task(id="T2", title="やり直した作業", retries=2)
            level, md = km.risk_digest(cfg, t2, set(), [])
            self.assertEqual(level, "med")
            self.assertIn("リトライ: 2 回", md)
            # 大きな差分（10 ファイル以上）→ med
            level, _ = km.risk_digest(cfg, t, {f"f{i}.py" for i in range(10)}, [])
            self.assertEqual(level, "med")

    def test_avoid_similarity_raises_to_high(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True)
            (cfg.decisions / "OLD.md").write_text(
                "## DR-0001  2026-07-01  actor: human\n"
                "- avoid: deploy prod release :: 本番系は人が見る\n", encoding="utf-8")
            t = km.Task(id="T1", title="deploy prod release v2")
            level, md = km.risk_digest(cfg, t, set(), [])
            self.assertEqual(level, "high")
            self.assertIn("回避判断", md)

    def test_synth_verify_and_assess_raise_to_med(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="合成 verify の作業")
            t.extra.append(("verify_source", "synth"))
            level, md = km.risk_digest(cfg, t, set(), [])
            self.assertEqual(level, "med")
            self.assertIn("自動合成", md)
            # 採点 r=3 も med に引き上げる（P2 の投入時アセスメント連動）
            t2 = km.Task(id="T2", title="採点付き")
            t2.extra.append(("assess", "c=1 r=3 a=1"))
            level, md = km.risk_digest(cfg, t2, set(), [])
            self.assertEqual(level, "med")
            self.assertIn("投入時採点", md)

    def test_review_needs_carries_risk_section(self):
        # delivery_review で review へ遷移した needs 票に ## リスク節と frontmatter risk が載る
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, delivery_review=True, learn=False, auto_adjudicate=False)
            mkb(d, "T1", verify="true")
            result = km.run_loop(cfg)
            self.assertEqual(result["counts"]["review"], 1)
            text = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertIn("risk: low", text.split("---")[1])   # frontmatter（viewer バッジ用）
            self.assertIn("## リスク", text)
            self.assertIn("総合: 低", text)

    def test_blocked_needs_has_no_risk_section(self):
        # リスクダイジェストは検収票（review）のみ。blocked 票は従来のまま
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1")
            t = km.load_tasks(cfg.backlog)[0]
            km.write_needs_file(cfg, t, "判断待ち")
            text = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertNotIn("## リスク", text)
            self.assertNotIn("risk:", text.split("---")[1])


class AssessTests(unittest.TestCase):
    """投入時アセスメント（c=複雑さ r=リスク a=曖昧さ・各1-3）。採点は情報のみで、
    実行可否・done 条件を変えないことを検証する。"""

    def test_heuristic_scoring_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)                            # executor=stub → ヒューリスティック
            t = km.Task(id="T1", title="verify 持ち", verify="true")
            self.assertEqual(km.assess_task(cfg, t), "c=1 r=1 a=1")
            t2 = km.Task(id="T2", title="accept のみ")
            t2.extra.append(("accept", "READMEに使用例"))
            self.assertEqual(km.assess_task(cfg, t2), "c=1 r=1 a=2")
            t3 = km.Task(id="T3", title="完了条件なし")
            self.assertEqual(km.assess_task(cfg, t3), "c=1 r=1 a=3")
            t4 = km.Task(id="T4", title="繰り返し {item}", verify="true")
            t4.extra.append(("cohort_items", "a,b,c"))
            self.assertEqual(km.assess_task(cfg, t4), "c=3 r=1 a=1")

    def test_avoid_similarity_scores_risk(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True)
            (cfg.decisions / "OLD.md").write_text(
                "## DR-0001  2026-07-01  actor: human\n"
                "- avoid: deploy prod release :: 本番系は人が見る\n", encoding="utf-8")
            t = km.Task(id="T1", title="deploy prod release v2", verify="true")
            self.assertEqual(km.assess_task(cfg, t), "c=1 r=3 a=1")

    def test_agent_scoring_clamps_and_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="kiro")           # 非 stub → エージェント採点
            t = km.Task(id="T1", title="x", verify="true")
            val = km.assess_task(cfg, t, kiro_run=lambda p, m: '{"c": 9, "r": 0, "a": 2}')
            self.assertEqual(val, "c=3 r=1 a=2")        # 1-3 にクランプ
            # 非 JSON・例外はヒューリスティックへフォールバック
            t2 = km.Task(id="T2", title="y", verify="true")
            val = km.assess_task(cfg, t2, kiro_run=lambda p, m: "説明文だけ")
            self.assertEqual(val, "c=1 r=1 a=1")
            t3 = km.Task(id="T3", title="z", verify="true")
            val = km.assess_task(cfg, t3,
                                 kiro_run=lambda p, m: (_ for _ in ()).throw(RuntimeError()))
            self.assertEqual(val, "c=1 r=1 a=1")

    def test_assess_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="x", verify="true")
            km.assess_task(cfg, t)
            km.assess_task(cfg, t)                      # 2 回目はスキップ（重複記録しない）
            self.assertEqual(sum(1 for k, _ in t.extra if k == "assess"), 1)

    def test_run_setup_scores_and_plan_review_card_shows_it(self):
        # run で新規タスクが採点され、plan-review 票（needs）に assess が載る
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, plan_review=True, assess=True)
            mkb(d, "T1", status="proposed")
            km.run_loop(cfg)
            body = (cfg.backlog / "T1.md").read_text(encoding="utf-8")
            self.assertIn("- assess: c=1 r=1 a=1", body)
            needs = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertIn("assess: c=1 r=1 a=1", needs)  # レビュー票の判断材料に載る

    def test_assess_off_keeps_tasks_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, assess=False, delivery_review=False)
            mkb(d, "T1")
            km.run_loop(cfg)
            # done → archive でも assess は付かない
            adir = cfg.archive_dir()
            text = "".join(p.read_text(encoding="utf-8") for p in adir.glob("*.md")) \
                if adir.exists() else ""
            text += "".join(p.read_text(encoding="utf-8") for p in cfg.backlog.glob("*.md"))
            self.assertNotIn("- assess:", text)


class SpecTrackTests(unittest.TestCase):
    """spec ルーティング（opt-in spec_track）と spec 前段タスク連鎖。
    ルーティングは「タスクを足す」方向のみで done 条件・予算に触れないこと、
    展開は人の承認（spec タスクの done）後にだけ起きることを検証する。"""

    def _routed(self, d, assess="c=3 r=1 a=1", **kw):
        cfg = cfg_for(d, spec_track=True, **kw)
        (d / "backlog").mkdir(parents=True, exist_ok=True)
        (d / "backlog" / "T1.md").write_text(
            f"## T1: 大きめの機能\n- status: ready\n- source: human\n- verify: `true`\n"
            f"- retries: 0\n- assess: {assess}\n", encoding="utf-8")
        tasks = km.load_tasks(cfg.backlog)
        created = km.route_spec_tasks(cfg, tasks, km.Policy())
        return cfg, tasks, created

    def test_route_creates_spec_task_and_chains_after(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks, created = self._routed(d)
            self.assertEqual(len(created), 1)
            s = created[0]
            self.assertEqual(s.id, "T1-spec")
            self.assertEqual(s.get("spec_for"), "T1")
            self.assertEqual(s.get("review"), "human")       # spec は必ず人が検収
            self.assertIn("specs/T1", s.verify)              # 決定的 verify（3 ファイル存在）
            t = km.load_tasks(cfg.backlog)
            t1 = next(x for x in t if x.id == "T1")
            self.assertEqual(t1.get("route"), "spec")
            self.assertEqual(t1.get("spec_task"), "T1-spec")
            self.assertIn("T1-spec", km.task_deps(t1))       # T1 は spec 完了まで待つ
            # 決定記録が残る
            self.assertIn("spec-route", (cfg.decisions / "T1.md").read_text(encoding="utf-8"))

    def test_route_skips_low_score_and_explicit_direct(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks, created = self._routed(d, assess="c=1 r=1 a=1")
            self.assertEqual(created, [])                    # しきい値未満は素通り
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True)
            (d / "backlog").mkdir(parents=True)
            (d / "backlog" / "T1.md").write_text(
                "## T1: 危ない機能\n- status: ready\n- verify: `true`\n"
                "- assess: c=3 r=3 a=3\n- route: direct\n", encoding="utf-8")
            tasks = km.load_tasks(cfg.backlog)
            self.assertEqual(km.route_spec_tasks(cfg, tasks, km.Policy()), [])  # 人の明示が勝つ

    def test_policy_spec_forces_routing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True)
            mkb(d, "T1", title="deploy prod")
            tasks = km.load_tasks(cfg.backlog)
            created = km.route_spec_tasks(cfg, tasks, km.parse_policy("spec: prod\n"))
            self.assertEqual(len(created), 1)                # 採点に依らず policy が強制

    def test_spec_track_off_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)                                 # spec_track 既定 off
            mkb(d, "T1")
            tasks = km.load_tasks(cfg.backlog)
            self.assertEqual(km.route_spec_tasks(cfg, tasks, km.Policy()), [])
            self.assertEqual(km.expand_spec_tasks(cfg, tasks), [])

    def _expanded_setup(self, d, tasks_md, spec_status="done"):
        cfg = cfg_for(d, spec_track=True)
        (d / "backlog").mkdir(parents=True, exist_ok=True)
        (d / "backlog" / "T1.md").write_text(
            "## T1: 大きめの機能\n- status: ready\n- verify: `true`\n"
            "- route: spec\n- spec_task: T1-spec\n- after: T1-spec\n", encoding="utf-8")
        adir = cfg.archive_dir()
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "T1-spec.md").write_text(
            f"## T1-spec: Spec 作成: 大きめの機能\n- status: {spec_status}\n", encoding="utf-8")
        if tasks_md is not None:
            sdir = km.specs_root(cfg) / "T1"
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "tasks.md").write_text(tasks_md, encoding="utf-8")
        return cfg, km.load_tasks(cfg.backlog)

    def test_expand_after_spec_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks = self._expanded_setup(d, (
                '# 実装タスク\n\n```json\n'
                '[{"title": "モデルを作る", "verify": "test -f m.py"},\n'
                ' {"title": "API を作る", "verify": "test -f api.py", "after": ["モデルを作る"]}]\n'
                '```\n'))
            created = km.expand_spec_tasks(cfg, tasks)
            self.assertEqual(len(created), 2)
            m = next(t for t in created if "モデル" in t.title)
            a = next(t for t in created if "API" in t.title)
            self.assertEqual(a.get("after"), m.id)           # 配列内 after（title）→ id 解決
            self.assertEqual(m.get("spec"), "T1")            # spec 文脈注入のタグ
            t1 = km.load_tasks(cfg.backlog)
            t1 = next(x for x in t1 if x.id == "T1")
            self.assertEqual(set(km.task_deps(t1)), {m.id, a.id})  # 総合検証として最後に走る
            self.assertEqual(t1.get("spec_expanded"), "2")
            # 冪等: 2 回目は展開しない
            tasks2 = km.load_tasks(cfg.backlog)
            self.assertEqual(km.expand_spec_tasks(cfg, tasks2), [])

    def test_expand_skips_rejected_spec(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks = self._expanded_setup(
                d, '[{"title": "x", "verify": "true"}]', spec_status="rejected")
            self.assertEqual(km.expand_spec_tasks(cfg, tasks), [])   # 却下は展開しない

    def test_expand_without_json_marks_none(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks = self._expanded_setup(d, "JSON の無い自由文\n")
            self.assertEqual(km.expand_spec_tasks(cfg, tasks), [])
            t1 = next(x for x in km.load_tasks(cfg.backlog) if x.id == "T1")
            self.assertEqual(t1.get("spec_expanded"), "none")  # 元タスクが spec 文脈で自力実装

    def test_build_request_injects_spec_instructions_and_context(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True)
            # spec 作成タスク → 3 ファイルの作成指示が載る
            s = km.Task(id="T1-spec", title="Spec 作成: 大きめの機能", verify="true")
            s.extra += [("spec_for", "T1"), ("note", "対象タスク T1: 大きめの機能")]
            req = km.build_request(s, cfg)
            self.assertIn("specs/T1/spec.md", req)
            self.assertIn("tasks.md", req)
            self.assertIn("実装はしない", req.replace("コードの実装はしない", "実装はしない"))
            # 実装タスク → spec.md/design.md が文脈注入される
            sdir = km.specs_root(cfg) / "T1"
            sdir.mkdir(parents=True)
            (sdir / "spec.md").write_text("# 要求仕様\nログイン必須\n", encoding="utf-8")
            (sdir / "design.md").write_text("# 設計\nJWT を使う\n", encoding="utf-8")
            impl = km.Task(id="I1", title="API を作る", verify="true")
            impl.extra.append(("spec", "T1"))
            req = km.build_request(impl, cfg)
            self.assertIn("ログイン必須", req)
            self.assertIn("JWT を使う", req)
            # spec の無い通常タスクは従来のまま
            plain = km.Task(id="P1", title="通常", verify="true")
            self.assertNotIn("仕様（spec 前段の成果", km.build_request(plain, cfg))

    def test_spec_task_pins_local_and_swaps_delegating_executor(self):
        # spec 作成タスクは委譲しない: location は常に local、gitlab executor は agent へ差し替え
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True, executor="gitlab",
                          location="remote", git_bus="https://example.com/bus.git")
            s = km.Task(id="T1-spec", title="Spec 作成: x", verify="true")
            s.extra.append(("spec_for", "T1"))
            self.assertEqual(km.decide_location(s, km.Policy(offload=["Spec"]), cfg), "local")
            cmd = km.build_kiro_flow_cmd(s, cfg)
            self.assertEqual(cmd[cmd.index("--executor") + 1], "agent")
            self.assertNotIn("--max-retries", cmd)   # 委譲用の即失敗化（0 固定）は付かない
            # 通常タスクは従来どおり（gitlab のまま・却下は即失敗化）
            t = km.Task(id="T2", title="通常", verify="true")
            cmd2 = km.build_kiro_flow_cmd(t, cfg)
            self.assertEqual(cmd2[cmd2.index("--executor") + 1], "gitlab")
            self.assertIn("--max-retries", cmd2)

    def test_run_setup_routes_in_loop(self):
        # run_loop の S0 でルーティングが効く（spec タスクが前置され T1 は依存待ちで走らない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True, max_cycles=1, learn=False, auto_adjudicate=False)
            (d / "backlog").mkdir(parents=True)
            (d / "backlog" / "T1.md").write_text(
                "## T1: 大きめの機能\n- status: ready\n- verify: `true`\n"
                "- assess: c=3 r=1 a=1\n", encoding="utf-8")
            km.run_loop(cfg)
            ids = {t.id for t in km.load_tasks(cfg.backlog)}
            self.assertIn("T1-spec", ids)
            self.assertIn("T1", ids)                          # T1 は消化されず残る（依存待ち）


class PlanAfterTests(unittest.TestCase):
    """plan 分解が after（先行タスクの title）を出し、enqueue 側で id へ決定的に解決される。
    未知 title は落とす・循環は捨てる（DAG の健全性が優先）。"""

    def _charter(self, d):
        (d / "charter.md").write_text(
            "# Charter: demo\n## goal\nCLI を作る\n## acceptance\n- `true`\n", encoding="utf-8")
        return km.parse_charter((d / "charter.md").read_text(encoding="utf-8"))

    def test_plan_via_agent_captures_after_titles(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            charter = self._charter(d)
            out = ('[{"title": "モデルを作る", "verify": "test -f m.py"},'
                   ' {"title": "API を作る", "verify": "test -f api.py",'
                   '  "after": ["モデルを作る"]}]')
            with mock.patch.object(km, "_run_kiro_cli", return_value=out):
                specs = km.plan_via_agent(cfg, charter)
            self.assertEqual(specs[1]["after_titles"], ["モデルを作る"])
            self.assertNotIn("after_titles", specs[0].get("after_titles") or [])

    def test_enqueue_resolves_titles_to_ids(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            specs = [{"title": "モデルを作る", "verify": "true"},
                     {"title": "API を作る", "verify": "true",
                      "after_titles": ["モデルを作る", "存在しないタスク"]}]
            created = km._enqueue_specs(cfg, specs, [], 0.9)
            self.assertEqual(len(created), 2)
            m, a = created
            self.assertEqual(a.get("after"), m.id)          # title → id・未知 title は落ちる
            self.assertNotIn("after_titles", dict(a.extra))  # 生 title はタスクに書かない
            self.assertIsNone(m.get("after"))

    def test_enqueue_drops_cyclic_after(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            specs = [{"title": "A を作る", "verify": "true", "after_titles": ["B を作る"]},
                     {"title": "B を作る", "verify": "true", "after_titles": ["A を作る"]}]
            created = km._enqueue_specs(cfg, specs, [], 0.9)
            a, b = created
            # 片方（後から解決した方）の after は循環になるため捨てられる
            deps_total = [x for t in (a, b) for x in km.task_deps(t)]
            self.assertEqual(len(deps_total), 1)
            journal = cfg.journal.read_text(encoding="utf-8")
            self.assertIn("循環のため破棄", journal)

    def test_coerce_titles_keeps_spaces(self):
        self.assertEqual(km._coerce_titles(["モデルを作る", " API を作る "]),
                         ["モデルを作る", "API を作る"])
        self.assertEqual(km._coerce_titles("モデルを作る, API を作る"),
                         ["モデルを作る", "API を作る"])   # 文字列はカンマ区切りのみ（空白は保持）
        self.assertEqual(km._coerce_titles(None), [])

    def test_plan_prompt_mentions_after(self):
        with tempfile.TemporaryDirectory() as d:
            charter = self._charter(Path(d))
            self.assertIn('"after"', km._plan_decompose_prompt(charter))


class RepoMapTests(unittest.TestCase):
    """リポジトリ理解の成果物化（context/<repo名>.md・opt-in repo_map）。
    生成は sha キャッシュで律速され、読み出し（注入）は常時効くことを検証する。"""

    def _write_map(self, cfg, name, body, sha="abc123"):
        cdir = km.context_dir(cfg)
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{name}.md").write_text(
            f"<!-- head: {sha} -->\n# リポジトリ理解: {name}\n\n{body}\n", encoding="utf-8")

    def test_repo_map_context_reads_and_filters(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self.assertEqual(km.repo_map_context(cfg), "")        # 無ければ空（従来動作）
            self._write_map(cfg, "app", "src/ 配下が本体。pytest で検証。")
            self._write_map(cfg, "docs", "mkdocs 構成。")
            all_ctx = km.repo_map_context(cfg)
            self.assertIn("pytest", all_ctx)
            self.assertIn("mkdocs", all_ctx)
            self.assertNotIn("<!-- head:", all_ctx)               # 署名マーカーは注入しない
            only = km.repo_map_context(cfg, ["app"])
            self.assertIn("pytest", only)
            self.assertNotIn("mkdocs", only)

    def test_ensure_repo_maps_caches_by_sha(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, repo_map=True, executor="kiro")
            charter = km.parse_charter(
                "# Charter: demo\n## goal\nx\n## repos\n"
                "- app = https://example.com/app.git\n  - owns: src/**\n")
            calls = []

            def gen(c, spec):
                calls.append(spec["url"])
                return "生成された理解"

            with mock.patch.object(km, "_repo_head_sha", return_value="abc123"), \
                 mock.patch.object(km, "_repo_map_generate", side_effect=gen):
                km.ensure_repo_maps(cfg, charter)
                km.ensure_repo_maps(cfg, charter)                 # 同一 sha → 再生成しない
            self.assertEqual(len(calls), 1)
            text = (km.context_dir(cfg) / "app.md").read_text(encoding="utf-8")
            self.assertIn("<!-- head: abc123 -->", text)
            self.assertIn("生成された理解", text)
            # sha が変わったら再生成
            with mock.patch.object(km, "_repo_head_sha", return_value="def456"), \
                 mock.patch.object(km, "_repo_map_generate", side_effect=gen):
                km.ensure_repo_maps(cfg, charter)
            self.assertEqual(len(calls), 2)

    def test_ensure_repo_maps_off_or_stub_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            charter = km.parse_charter(
                "# Charter: demo\n## goal\nx\n## repos\n- app = https://example.com/app.git\n")
            with mock.patch.object(km, "_repo_map_generate",
                                   side_effect=AssertionError("生成してはいけない")):
                km.ensure_repo_maps(cfg_for(d), charter)                      # repo_map 既定 off
                km.ensure_repo_maps(cfg_for(d, repo_map=True), charter)       # executor=stub
            self.assertFalse(km.context_dir(cfg_for(d)).exists())

    def test_build_request_injects_repo_map(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._write_map(cfg, "app", "src/ 配下が本体。pytest で検証。")
            t = km.Task(id="T1", title="機能を足す", verify="true")
            req = km.build_request(t, cfg)
            self.assertIn("リポジトリ理解", req)
            self.assertIn("pytest", req)
            # workspace 指定タスクは該当 repo 分だけ
            t2 = km.Task(id="T2", title="別の作業", verify="true")
            t2.extra.append(("workspace", "docs"))
            self.assertNotIn("pytest", km.build_request(t2, cfg))

    def test_plan_prompt_carries_context(self):
        charter = km.parse_charter("# Charter: demo\n## goal\nx\n")
        p = km._plan_decompose_prompt(charter, context="src/ 配下が本体")
        self.assertIn("リポジトリ理解", p)
        self.assertIn("src/ 配下が本体", p)
        self.assertNotIn("リポジトリ理解", km._plan_decompose_prompt(charter))


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
            self.assertIn("<!-- learn:OLD hits=2", text)            # 出典つき（人が消してよい）
            self.assertIn("- rules-promoted: rules.md",
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

    def test_state_git_remote_wins_includes_rules(self):
        # rules.md は人の入力パス（同時変更はリモート＝人の編集を優先）
        self.assertIn("rules.md", km._STATE_REMOTE_WINS_FILES)


class AgentOverrideTests(unittest.TestCase):
    """処理（purpose）毎のエージェント上書き（設定 agents:・yaml 専用）。
    plan/review/prioritize/route/adjudicate/verify/distill/assess/repo_map/doctor の
    各処理でエージェント CLI とモデルを個別に選べることを検証する。"""

    def setUp(self):
        self._cli, self._ov = km._AGENT_CLI, dict(km._AGENT_OVERRIDES)

    def tearDown(self):
        km._AGENT_CLI, km._AGENT_OVERRIDES = self._cli, self._ov

    def test_normalize_accepts_known_purposes_only(self):
        raw = {"plan": {"agent_cli": "Claude", "model": "opus"},
               "assess": {"model": "haiku"},
               "unknown": {"agent_cli": "x"},       # 未知キーは落とす
               "verify": "not-a-dict",              # 不正な値も落とす
               "doctor": {}}                         # 空も落とす
        out = km._normalize_agent_overrides(raw)
        self.assertEqual(set(out), {"plan", "assess"})
        self.assertEqual(out["plan"], {"agent_cli": "claude", "model": "opus"})
        self.assertEqual(km._normalize_agent_overrides(None), {})

    def test_agent_for_falls_back_to_global(self):
        km._AGENT_CLI = "kiro"
        km._AGENT_OVERRIDES = {"plan": {"agent_cli": "claude", "model": "opus"},
                               "assess": {"model": "haiku"}}
        self.assertEqual(km._agent_for("plan"), ("claude", "opus"))
        self.assertEqual(km._agent_for("assess"), ("kiro", "haiku"))  # model だけ上書き
        self.assertEqual(km._agent_for("verify"), ("kiro", None))     # 未指定 → グローバル
        self.assertEqual(km._agent_for(""), ("kiro", None))

    def test_agent_cmd_builds_per_cli(self):
        cmd, stdin, out_file = km._agent_cmd("claude", "opus", "P")
        self.assertEqual(cmd[0], "claude")
        self.assertIn("opus", cmd)
        self.assertEqual(stdin, "P")                              # claude は stdin 渡し
        self.assertIsNone(out_file)
        cmd, stdin, out_file = km._agent_cmd("copilot", None, "P")
        self.assertEqual(cmd[0], "copilot")
        self.assertEqual(cmd[-2:], ["-p", "P"])
        self.assertIsNone(stdin)
        self.assertIsNone(out_file)
        cmd, stdin, out_file = km._agent_cmd("kiro", "m", "P")
        self.assertEqual(cmd[0], "kiro-cli")
        self.assertEqual(cmd[-1], "P")
        self.assertIsNone(out_file)
        cmd, stdin, out_file = km._agent_cmd("codex", "m", "P")
        try:
            self.assertEqual(cmd[:2], ["codex", "exec"])
            self.assertEqual(cmd[-1], "-")                        # プロンプトは stdin（"-"）
            self.assertEqual(stdin, "P")
            self.assertIn("--output-last-message", cmd)           # 最終応答はファイル経由
            self.assertTrue(out_file and os.path.exists(out_file))
        finally:
            if out_file:
                os.remove(out_file)

    def test_run_kiro_cli_uses_purpose_override(self):
        km._AGENT_CLI = "kiro"
        km._AGENT_OVERRIDES = {"plan": {"agent_cli": "claude", "model": "opus"}}
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(km.subprocess, "run", side_effect=fake_run):
            km._run_kiro_cli("x", "global-model", purpose="plan")
            km._run_kiro_cli("x", "global-model", purpose="assess")
        self.assertEqual(calls[0][0], "claude")
        self.assertIn("opus", calls[0])                           # 上書き model が勝つ
        self.assertEqual(calls[1][0], "kiro-cli")
        self.assertIn("global-model", calls[1])                   # 未指定はグローバル

    def test_resolve_config_reads_agents_map(self):
        # yaml（json 互換）から agents: が読まれ、build_config がモジュールへ確定する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "kiro-project.json").write_text(json.dumps({
                "agents": {"plan": {"agent_cli": "claude", "model": "opus"},
                           "bogus": {"agent_cli": "x"}}}), encoding="utf-8")
            old_cwd = os.getcwd()
            try:
                os.chdir(d)
                args = types.SimpleNamespace(config=None)
                km.resolve_config(args)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(km._normalize_agent_overrides(args.agents),
                             {"plan": {"agent_cli": "claude", "model": "opus"}})


if __name__ == "__main__":
    unittest.main()
