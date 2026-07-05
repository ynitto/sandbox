#!/usr/bin/env python3
"""kiro-flow のプロトコル単体テスト＋障害注入テスト。

kiro-cli 不要（stub のみ）。標準ライブラリの unittest で完結する。
実行: python3 -m unittest discover -s tools/kiro-flow/tests
      または python3 tools/kiro-flow/tests/test_kiro_flow.py
"""
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import types
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "kiro-flow.py"

# stub の擬似実行スリープを無効化してテストを高速化（子プロセスにも継承される）
os.environ["KIRO_FLOW_STUB_SLEEP_MAX"] = "0"

# テストの git コミットを環境のコミット署名設定（commit.gpgsign）から切り離す。
# 署名が有効な環境では署名が間欠的に失敗して `git commit` がコミットを作らず、git バス系の
# テストが偶発的に落ちる。GIT_CONFIG_* で commit.gpgsign=false を上乗せして決定的にする。
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = "commit.gpgsign"
os.environ["GIT_CONFIG_VALUE_0"] = "false"

# 自動アップデートは既定 on のため、テスト中にコントリビューターの実 skill-registry.json から
# 更新元が解決されて実ネットワーク/再起動が走るのを防ぐ。存在しないパスを権威指定して registry
# 解決を無効化する（SelfUpdateTests は必要なテストでだけ KIRO_SKILL_REGISTRY を一時上書きする）。
os.environ["KIRO_SKILL_REGISTRY"] = os.path.join(
    tempfile.gettempdir(), "kf-tests-no-such-registry", "skill-registry.json")


def _load_module():
    spec = importlib.util.spec_from_file_location("kiroflow", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kf = _load_module()


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-test-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("test request")

    def _add_task(self, tid, goal="g", deps=None):
        self.bus.write_task({"id": tid, "goal": goal, "deps": deps or []})
        graph = self.bus.read_graph() or {"nodes": {}, "iteration": 0}
        graph["nodes"][tid] = {"goal": goal, "deps": deps or []}
        self.bus.write_graph(graph)

    def test_pending_then_claimed_then_done(self):
        self._add_task("t1")
        self.assertEqual(self.bus.node_state("t1"), "pending")
        self.assertTrue(self.bus.try_claim("t1", "w1", lease_sec=60))
        self.assertEqual(self.bus.node_state("t1"), "claimed")
        self.bus.write_result("t1", "w1", "done", "out")
        self.assertEqual(self.bus.node_state("t1"), "done")

    def test_deterministic_winner_earliest_ts(self):
        self._add_task("t1")
        # 名前空間付き claim を 2 つ手で書く（ts が古い方が勝つ）
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "wB.json"),
                             {"who": "wB", "ts": 200.0, "lease_until": time.time() + 999})
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "wA.json"),
                             {"who": "wA", "ts": 100.0, "lease_until": time.time() + 999})
        self.assertEqual(self.bus._winner("t1"), "wA")

    def test_expired_lease_is_reclaimable(self):
        """障害注入: ワーカーが死んで claim を残したケース。lease 切れなら再 claim できる。"""
        self._add_task("t1")
        kf.write_json_atomic(os.path.join(self.bus._claim_dir("t1"), "dead.json"),
                             {"who": "dead", "ts": 1.0, "lease_until": time.time() - 10})
        self.assertIsNone(self.bus._winner("t1"))          # 期限切れは無視
        self.assertEqual(self.bus.node_state("t1"), "pending")
        self.assertTrue(self.bus.try_claim("t1", "w2", lease_sec=60))  # 別ノードが回収
        self.assertEqual(self.bus._winner("t1"), "w2")

    def test_concurrent_claim_single_winner(self):
        """障害注入相当: 複数ワーカーが同時に同じタスクを取りに行っても勝者は 1 人。"""
        self._add_task("t1")
        results = {}
        barrier = threading.Barrier(5)

        def worker(name):
            barrier.wait()
            results[name] = self.bus.try_claim("t1", name, lease_sec=60)

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [n for n, ok in results.items() if ok]
        self.assertEqual(len(winners), 1, f"勝者は 1 人のはず: {results}")

    def test_claim_lock_is_off_bus(self):
        # 排他ロックはバス（git に乗る領域）の外＝一時領域に置く
        lp = kf._claim_lock_path(self.bus._claim_dir("t1"))
        self.assertNotIn(self.tmp, lp)
        self.assertIn("kiro-flow-locks", lp)

    def test_all_terminal(self):
        self._add_task("t1")
        self._add_task("t2")
        self.assertFalse(self.bus.all_terminal())
        self.bus.write_result("t1", "w", "done", "o")
        self.bus.write_result("t2", "w", "failed", "o")
        self.assertTrue(self.bus.all_terminal())  # done/failed はどちらも terminal


class InheritTests(unittest.TestCase):
    """リトライ時の引き継ぎ（inherit_from）: 先行 run から確定済みノードを引き継ぎ、
    先行 run を掃除する。タイムアウト/失敗で毎回ゼロからやり直すのを防ぐ。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-inherit-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk_run(self, rid, request="req", workspace=None):
        b = kf.Bus(self.tmp, rid)
        b.ensure_run(request, workspace=workspace)
        return b

    def _add_task(self, bus, tid, deps=None):
        bus.write_task({"id": tid, "goal": "g", "deps": deps or []})
        graph = bus.read_graph() or {"strategy": {}, "nodes": {}, "iteration": 0}
        graph["nodes"][tid] = {"goal": "g", "deps": deps or []}
        bus.write_graph(graph)

    def test_partial_inherit_copies_done_and_removes_old(self):
        old = self._mk_run("req-x-t-r0")
        self._add_task(old, "t1")
        self._add_task(old, "t2")
        old.write_result("t1", "w", "done", "out1", data={"k": 1})
        old.write_result("t2", "w", "failed", "boom")   # 失敗ノードは引き継がない
        old.mark_run_failed("req-x-t-r0", "timed out")   # 終端化（削除の安全条件）

        new = kf.Bus(self.tmp, "req-x-t-r1")
        info = new.inherit_from("req-x-t-r0")

        self.assertEqual(info["seeded_nodes"], 1)
        self.assertTrue(info["inherited"])
        self.assertTrue(info["deleted"])
        # done ノードだけ引き継ぐ＝t1 は done 扱い、t2 は引き継がれず再実行対象
        self.assertEqual(new.node_state("t1"), "done")
        self.assertEqual(new.read_result("t1").get("data"), {"k": 1})
        self.assertIsNone(new.read_result("t2"))
        self.assertIsNotNone(new.read_graph())           # 計画（graph）も引き継ぐ
        # 先行 run は掃除済み
        self.assertNotIn("req-x-t-r0", new.list_runs())
        self.assertEqual(new.run_meta("req-x-t-r1").get("inherited_from"), "req-x-t-r0")

    def test_fully_done_predecessor_seeds_nothing_but_cleans_up(self):
        old = self._mk_run("req-y-t-r0")
        self._add_task(old, "t1")
        old.write_result("t1", "w", "done", "out")
        old.set_status("done")                           # verify=NG 相当（全ノード done で終端）

        new = kf.Bus(self.tmp, "req-y-t-r1")
        info = new.inherit_from("req-y-t-r0")

        self.assertEqual(info["seeded_nodes"], 0)        # 同一出力で即 done の無限ループを避ける
        self.assertTrue(info["deleted"])
        self.assertIsNone(kf.read_json(new.meta_path))   # 新 run は白紙（feedback 付きで再計画）
        self.assertNotIn("req-y-t-r0", new.list_runs())

    def test_live_predecessor_is_untouched(self):
        old = self._mk_run("req-z-t-r0")
        self._add_task(old, "t1")
        old.set_status("running")
        old.touch_run("req-z-t-r0", 9999)                # 生存リースが有効＝実行中

        new = kf.Bus(self.tmp, "req-z-t-r1")
        info = new.inherit_from("req-z-t-r0")

        self.assertFalse(info["deleted"])
        self.assertFalse(info["inherited"])
        self.assertIn("req-z-t-r0", new.list_runs())     # 走っている run は消さない

    def test_missing_predecessor_is_noop(self):
        new = kf.Bus(self.tmp, "req-none-r1")
        info = new.inherit_from("req-none-r0")
        self.assertFalse(info["deleted"])
        self.assertFalse(info["inherited"])

    def test_workspace_branch_is_chained_from_old(self):
        ws = {"url": "https://git.example/g/r", "path": "", "base": "main",
              "target": "main", "desc": ""}
        old = self._mk_run("req-w-t-r0", workspace=ws)
        self._add_task(old, "t1")
        self._add_task(old, "t2")                        # 未完ノードを残す＝部分引き継ぎ
        old.write_result("t1", "w", "done", "out")
        old.mark_run_failed("req-w-t-r0", "timed out")

        new = kf.Bus(self.tmp, "req-w-t-r1")
        new.inherit_from("req-w-t-r0")
        # 確定済みノードの commit を失わないよう、新 run は旧ブランチ kf/<old> から派生する
        self.assertEqual(new.run_workspace().get("base"), kf.run_branch_name("req-w-t-r0"))


class RunFailureTests(unittest.TestCase):
    """orchestrator が done を書く前に異常終了したケースの終端化（失敗終了の検知）。
    これが無いと run が非終端のまま放置され、result/status を待つ消費者
    （kiro-projects の charter 駆動 watch）が execute フェーズで永久待機する。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-test-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("test request")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mark_run_failed_terminalizes_running(self):
        self.bus.set_status("running")
        self.assertTrue(self.bus.mark_run_failed("run1", "orchestrator crash"))
        meta = self.bus.run_meta("run1")
        self.assertEqual(meta["status"], "failed")
        self.assertEqual(meta["failure_reason"], "orchestrator crash")
        # 終端 = result --json の done=True/status=failed として消費者から即検知できる
        self.assertIn(meta["status"], kf.TERMINAL)

    def test_mark_run_failed_noop_when_already_done(self):
        self.bus.set_status("done")
        self.assertFalse(self.bus.mark_run_failed("run1", "late crash"))
        meta = self.bus.run_meta("run1")
        self.assertEqual(meta["status"], "done")            # 正常完了を上書きしない
        self.assertNotIn("failure_reason", meta)

    def test_mark_run_failed_noop_when_already_failed(self):
        self.bus.set_status("failed")
        self.assertFalse(self.bus.mark_run_failed("run1"))  # 冪等: 既に終端

    def test_mark_run_failed_missing_run(self):
        self.assertFalse(self.bus.mark_run_failed("no-such-run"))

    def test_fail_request_without_run_creates_failed_meta(self):
        # orchestrator が run の meta を一度も書けずに死んだ要求は、fail_request が failed run を
        # 新規作成して終端化する（run_exists が真になり、daemon が同じ要求を毎 poll
        # 再 claim → 起動 → 即死 を繰り返す無限ループが止まる）
        self.bus.submit_request("req9", "do it", "submitter",
                                workspace={"url": "https://x/repo.git"})
        self.assertFalse(self.bus.run_exists("req9"))
        self.assertTrue(self.bus.fail_request("req9", "orchestrator died before run creation"))
        self.assertTrue(self.bus.run_exists("req9"))
        meta = self.bus.run_meta("req9")
        self.assertEqual(meta["status"], "failed")
        self.assertIn(meta["status"], kf.TERMINAL)
        self.assertEqual(meta["request"], "do it")                       # 要求内容を引き写す
        self.assertEqual(meta["workspace"], {"url": "https://x/repo.git"})
        self.assertIn("died before run creation", meta["failure_reason"])

    def test_fail_request_delegates_to_mark_run_failed_when_run_exists(self):
        self.bus.set_status("running")
        self.assertTrue(self.bus.fail_request("run1", "orchestrator crash"))
        self.assertEqual(self.bus.run_meta("run1")["status"], "failed")

    def test_fail_request_noop_when_already_terminal(self):
        self.bus.set_status("done")
        self.assertFalse(self.bus.fail_request("run1", "late crash"))
        self.assertEqual(self.bus.run_meta("run1")["status"], "done")   # 正常完了を上書きしない


class OrphanRecoveryTests(unittest.TestCase):
    """owning daemon が消失した非終端 run（孤児）を生存リースで検知して回収する。
    これが無いと、再起動した新プロセスが前プロセスの status:running を見て何もせず、
    remote submit を待つ消費者が act_timeout まで永久待機する。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-test-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("test request")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_meta(self, **kw):
        meta = kf.read_json(self.bus.meta_path) or {}
        meta.update(kw)
        kf.write_json_atomic(self.bus.meta_path, meta)

    def test_touch_run_marks_alive(self):
        self.bus.set_status("running")
        self.bus.touch_run("run1", 120.0)
        meta = self.bus.run_meta("run1")
        self.assertIn("orch_lease_until", meta)
        self.assertIn("heartbeat_at", meta)
        self.assertFalse(self.bus.run_is_orphaned("run1", 120.0))   # 新鮮なリース = 生存

    def test_touch_run_noop_when_terminal(self):
        self.bus.set_status("done")
        self.bus.touch_run("run1", 120.0)
        self.assertNotIn("orch_lease_until", self.bus.run_meta("run1"))

    def test_expired_lease_is_orphan(self):
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        self.assertTrue(self.bus.run_is_orphaned("run1", 120.0))

    def test_no_lease_old_run_is_orphan(self):
        # owner が heartbeat する前に死んだ／本変更前から残る run はリース未記録 → age で判定
        self.bus.set_status("running")
        self._set_meta(created_at="2000-01-01T00:00:00Z", updated_at="2000-01-01T00:00:00Z")
        self.assertTrue(self.bus.run_is_orphaned("run1", 120.0))

    def test_no_lease_fresh_run_not_orphan(self):
        # リース未記録でも作成直後の run は孤児扱いしない（orchestrator spawn 直後の race を守る）
        self.bus.set_status("running")        # created_at は ensure_run の now
        self.assertFalse(self.bus.run_is_orphaned("run1", 120.0))

    def test_terminal_run_never_orphan(self):
        self.bus.set_status("done")
        self._set_meta(orch_lease_until=time.time() - 999.0)
        self.assertFalse(self.bus.run_is_orphaned("run1", 120.0))

    def test_orphan_recovered_via_mark_failed(self):
        # 孤児（非終端＋リース切れ）は mark_run_failed で終端化でき、消費者が失敗を検知して復旧できる
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        self.assertTrue(self.bus.run_is_orphaned("run1", 120.0))
        self.assertTrue(self.bus.mark_run_failed("run1", "orphaned"))
        self.assertEqual(self.bus.run_meta("run1")["status"], "failed")
        self.assertFalse(self.bus.run_is_orphaned("run1", 120.0))    # 終端化後は孤児でない

    def test_run_lease_window_bounds(self):
        self.assertEqual(kf._run_lease_window(types.SimpleNamespace(poll=2.0)), 120.0)
        self.assertEqual(kf._run_lease_window(types.SimpleNamespace(poll=20.0)), 200.0)
        self.assertEqual(kf._run_lease_window(types.SimpleNamespace(poll=None)), 120.0)

    def _args(self, **kw):
        base = dict(max_resumes=3, lease=1800.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _adopt(self, owned=None, spawn=None, **kw):
        spawned = []

        def fake_spawn(base, args, req_id, req):
            spawned.append(req_id)
            return types.SimpleNamespace(poll=lambda: None)   # 生きている子のふり

        adopted, failed = kf._adopt_orphan_runs(
            self.bus, "d2", owned or set(), 120.0, self._args(**kw), [],
            spawn=spawn or fake_spawn)
        return adopted, failed, spawned

    def test_orphan_inbox_run_is_resumed_not_failed(self):
        # PC シャットダウン等で owning daemon が消えた run は failed でなく再開（引き継ぎ）する
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, spawned = self._adopt()
        self.assertEqual(list(adopted), ["run1"])
        self.assertEqual(failed, [])
        self.assertEqual(spawned, ["run1"])
        meta = self.bus.run_meta("run1")
        self.assertEqual(meta["status"], "running")             # failed にしない
        self.assertEqual(meta["resume_count"], 1)
        self.assertGreater(meta.get("orch_lease_until", 0), time.time())  # 生存リースを張り直す

    def test_orphan_failed_after_max_resumes_without_progress(self):
        # 進捗（results/）ゼロのまま連続再開が上限を超えたら従来どおり failed に確定する
        self.bus.submit_request("run1", "req", "submitter")
        for i in range(2):
            self.bus.set_status("running")
            self._set_meta(orch_lease_until=time.time() - 1.0)
            adopted, failed, _ = self._adopt(max_resumes=2)
            self.assertEqual(list(adopted), ["run1"], f"resume #{i+1}")
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, _ = self._adopt(max_resumes=2)
        self.assertEqual(adopted, {})
        self.assertEqual(failed, ["run1"])
        self.assertEqual(self.bus.run_meta("run1")["status"], "failed")
        self.assertIn("orphaned", self.bus.run_meta("run1")["failure_reason"])

    def test_resume_count_resets_on_progress(self):
        # 前回の再開以降に results が増えていれば数え直す＝進捗のある長期 run は何度でも再開できる
        self.bus.submit_request("run1", "req", "submitter")
        for i in range(4):                                       # max_resumes=2 を超える回数
            self.bus.set_status("running")
            self._set_meta(orch_lease_until=time.time() - 1.0)
            adopted, failed, _ = self._adopt(max_resumes=2)
            self.assertEqual(list(adopted), ["run1"], f"resume #{i+1}")
            self.assertEqual(self.bus.run_meta("run1")["resume_count"], 1)  # 進捗ありで毎回リセット
            self.bus.write_result(f"t{i}", "w1", "done", "ok")   # 再開後に進捗が出た
        self.assertEqual(failed, [])

    def test_orphan_failed_when_resume_disabled(self):
        # max_resumes<=0 は従来動作（孤児は即 failed）へのオプトアウト
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, spawned = self._adopt(max_resumes=0)
        self.assertEqual(adopted, {})
        self.assertEqual(failed, ["run1"])
        self.assertEqual(spawned, [])
        self.assertEqual(self.bus.run_meta("run1")["status"], "failed")

    def test_adopt_skips_owned_run(self):
        # 自分が今 orchestrator を回している run は（リースが古くても）引き継がない
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, _ = self._adopt(owned={"run1"})
        self.assertEqual((adopted, failed), ({}, []))
        self.assertEqual(self.bus.run_meta("run1")["status"], "running")

    def test_adopt_skips_live_run(self):
        # 別デーモンが heartbeat 中（リース新鮮）の run は引き継がない（誤って横取りしない）
        self.bus.submit_request("run1", "req", "submitter")
        self.bus.set_status("running")
        self.bus.touch_run("run1", 120.0)
        adopted, failed, _ = self._adopt()
        self.assertEqual((adopted, failed), ({}, []))
        self.assertEqual(self.bus.run_meta("run1")["status"], "running")

    def test_adopt_waits_for_stale_claim_lease(self):
        # 消失した旧 owner の inbox claim がまだ lease 内なら引き継ぎを保留する
        # （lease 失効後の poll で自然に再試行される。failed にはしない）
        self.bus.submit_request("run1", "req", "submitter")
        self.assertTrue(self.bus.reclaim_request("run1", "dead-daemon", 1800.0))
        self.bus.set_status("running")
        self._set_meta(orch_lease_until=time.time() - 1.0)
        adopted, failed, _ = self._adopt()
        self.assertEqual((adopted, failed), ({}, []))
        self.assertEqual(self.bus.run_meta("run1")["status"], "running")

    def test_reclaim_after_owner_lease_expiry(self):
        # 旧 owner の claim が lease 切れなら reclaim できる（run が存在していても）
        self.bus.submit_request("run1", "req", "submitter")
        self.assertTrue(self.bus.reclaim_request("run1", "dead-daemon", 0.01))
        time.sleep(0.05)
        self.assertFalse(self.bus.claim_request("run1", "d2", 120.0))   # 従来 API は run 存在で拒否
        self.assertTrue(self.bus.reclaim_request("run1", "d2", 120.0))  # 引き継ぎ用は claim できる


class PlannerTests(unittest.TestCase):
    def test_parallel_split(self):
        tasks = kf.plan_stub("a; b; c")
        self.assertEqual([t["id"] for t in tasks], ["t1", "t2", "t3"])
        self.assertTrue(all(t["deps"] == [] for t in tasks))

    def test_sequential_chain_deps(self):
        tasks = kf.plan_stub("setup -> build -> test; docs")
        by_id = {t["id"]: t for t in tasks}
        self.assertEqual(by_id["t1"]["deps"], [])         # setup
        self.assertEqual(by_id["t2"]["deps"], ["t1"])     # build after setup
        self.assertEqual(by_id["t3"]["deps"], ["t2"])     # test after build
        self.assertEqual(by_id["t4"]["deps"], [])         # docs independent

    def test_simple_newline_list_still_splits(self):
        # 空行の無いフラットなリストは従来どおり改行を区切りとして扱う
        tasks = kf.plan_stub("task1\ntask2\ntask3")
        self.assertEqual([t["goal"] for t in tasks], ["task1", "task2", "task3"])

    # 回帰: 構造化された複数行の要求（charter 文脈＝対象リポジトリ一覧つき）を、行ごとの
    # 細切れタスクへ分割しないこと。さもないと 1 行 1 行が別イシューになり、gitlab の
    # タイトル/本文が repos 行で埋まる（報告された不具合）。
    _STRUCTURED_REQ = (
        "ログイン画面のバグを修正する\n\n"
        "完了条件: pytest\n\n"
        "対象リポジトリ:\n"
        "- web = https://gitlab.com/acme/web（base=main）\n"
        "    説明: フロントエンド\n"
        "- api = https://gitlab.com/acme/api（base=main）\n"
        "制約:\n- 既存テストを壊さない\n"
    )

    def test_structured_request_not_shredded_per_line(self):
        tasks = kf.plan_stub(self._STRUCTURED_REQ)
        # repos 行や charter 見出しが個別タスクの goal になっていないこと
        for t in tasks:
            self.assertNotIn("gitlab.com", t["goal"])
            self.assertNotIn("対象リポジトリ", t["goal"])
            self.assertNotIn("制約:", t["goal"])
        # 見出しは本来の目的（先頭行）から始まる
        self.assertTrue(all(t["goal"].startswith("ログイン画面のバグを修正する") for t in tasks))

    def test_structured_request_strategy_goals_have_no_repos(self):
        # plan_stub を使う既定パターン（fan-out-and-synthesize）でも repos が goal に出ない
        strat, tasks = kf.plan_strategy_stub(self._STRUCTURED_REQ)
        for t in tasks:
            self.assertNotIn("gitlab.com", t["goal"])
            self.assertNotIn("対象リポジトリ", t["goal"])
        # タイトル相当（先頭行）が本来の目的であること
        heads = [t["goal"] for t in tasks if t["kind"] in ("work", "generate", "synthesize")]
        self.assertTrue(any("ログイン画面のバグを修正する" in g for g in heads))

    def test_first_line_helper(self):
        self.assertEqual(kf._first_line("\n\n  目的の行  \n詳細\n"), "目的の行")
        self.assertEqual(kf._first_line("x" * 60), "x" * 48)   # limit で切る


class StructuredResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-data-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")

    def test_result_data_roundtrip(self):
        self.bus.write_result("t1", "w", "done", "txt", data={"items": [1, 2, 3]})
        r = self.bus.read_result("t1")
        self.assertEqual(r["output"], "txt")
        self.assertEqual(r["data"], {"items": [1, 2, 3]})

    def test_result_without_data_has_no_key(self):
        self.bus.write_result("t1", "w", "done", "txt")
        self.assertNotIn("data", self.bus.read_result("t1"))

    def test_collect_dep_results_sees_through_gate(self):
        # planner が work→gate→synth と直列にしても、集約役は gate が検証した
        # 上流（t2,t3）の成果を受け取れる（gate 経由でも入力が空にならない）
        self.bus.write_graph({"nodes": {
            "t2": {"deps": [], "kind": "work"},
            "t3": {"deps": [], "kind": "work"},
            "gate": {"deps": ["t2", "t3"], "kind": "verify"},
            "synth": {"deps": ["gate"], "kind": "synthesize"},
        }})
        self.bus.write_result("t2", "w", "done", "out2")
        self.bus.write_result("t3", "w", "done", "out3")
        self.bus.write_result("gate", "w", "done", "verify=pass", data={"ok": True})
        node = {"deps": ["gate"], "kind": "synthesize"}
        dep = kf._collect_dep_results(self.bus, node, "synthesize")
        self.assertEqual(set(dep), {"gate", "t2", "t3"})  # 上流が透過された
        self.assertEqual(dep["t2"]["output"], "out2")

    def test_collect_dep_results_no_passthrough_for_work(self):
        # 非集約ノードは透過しない（gate をそのまま受ける）
        self.bus.write_graph({"nodes": {
            "a": {"deps": [], "kind": "work"},
            "gate": {"deps": ["a"], "kind": "verify"},
        }})
        self.bus.write_result("a", "w", "done", "oa")
        self.bus.write_result("gate", "w", "done", "verify=pass", data={"ok": True})
        dep = kf._collect_dep_results(self.bus, {"deps": ["gate"], "kind": "work"}, "work")
        self.assertEqual(set(dep), {"gate"})

    def test_executor_returns_text_and_data(self):
        text, data = kf.execute_stub("classify", "backend のバグ", {}, None)
        self.assertEqual(text, "class=backend")
        self.assertEqual(data, {"label": "backend"})
        text, data = kf.execute_stub("work", "ふつうの仕事", {}, None)
        self.assertIsNone(data)

    def test_reduce_aggregates_dependency_data(self):
        deps = {
            "a": {"output": "oa", "data": ["x", "y"]},
            "b": {"output": "ob", "data": ["z"]},
            "c": {"output": "oc"},  # data 無し → output を要素化
        }
        text, data = kf.execute_stub("reduce", "集約", deps, None)
        self.assertEqual(data["count"], 4)
        self.assertEqual(sorted(str(i) for i in data["items"]), ["oc", "x", "y", "z"])


class OutputSanitizeTests(unittest.TestCase):
    def test_strip_ansi(self):
        raw = "\x1b[38;5;141m> \x1b[0mhello\x1b[1mX\x1b[22m"
        self.assertEqual(kf.strip_ansi(raw), "> helloX")
        self.assertEqual(kf.strip_ansi(""), "")

    def test_reconcile_count_fixes_mismatch(self):
        d = kf._reconcile_count({"primes": [2, 3, 5], "count": 99, "range": {"min": 2}})
        self.assertEqual(d["count"], 3)

    def test_reconcile_count_skips_when_ambiguous(self):
        # count 無し / 複数リスト / 非 dict は変更しない
        self.assertEqual(kf._reconcile_count({"primes": [2, 3]}), {"primes": [2, 3]})
        self.assertEqual(kf._reconcile_count({"a": [1], "b": [1, 2], "count": 5})["count"], 5)
        self.assertEqual(kf._reconcile_count([1, 2, 3]), [1, 2, 3])


class VerifyGateTests(unittest.TestCase):
    def test_normalize_verify_from_json(self):
        d = kf._normalize_verify("verify=fail", {"ok": False, "issues": ["x"]})
        self.assertFalse(d["ok"])
        self.assertEqual(d["issues"], ["x"])

    def test_normalize_verify_from_text(self):
        self.assertFalse(kf._normalize_verify("verify=fail: 件数不一致", None)["ok"])
        self.assertTrue(kf._normalize_verify("verify=pass 問題なし", None)["ok"])

    def test_is_gate_result(self):
        self.assertTrue(kf._is_gate_result({"data": {"ok": True}}))
        self.assertFalse(kf._is_gate_result({"data": [1, 2]}))
        self.assertFalse(kf._is_gate_result({"output": "x"}))


class DataDrivenFanoutTests(unittest.TestCase):
    def test_split_executor_returns_list(self):
        text, data = kf.execute_stub("split", "5 件に分解", {}, None)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 5)

    def test_split_expands_to_map_and_reduce(self):
        nodes = {"split1": {"goal": "分解", "deps": [], "kind": "split"}}
        results = {"split1": {"status": "done", "data": ["x", "y", "z"]}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0, max_fanout=50)
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new],
                         ["split1-m1", "split1-m2", "split1-m3", "split1-reduce"])
        red = next(t for t in new if t["kind"] == "reduce")
        self.assertEqual(red["deps"], ["split1-m1", "split1-m2", "split1-m3"])

    def test_exemplar_first_stages_pilot_then_rest(self):
        # Stage 1: split 完了直後は pilot map 1件＋検証ゲートだけ（残りは出さない）
        nodes = {"s": {"goal": "各件を移行", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["a", "b", "c"]}}
        _, new, _ = kf.continue_stub("各件を移行", nodes, results, 0, exemplar_first=True)
        ids = [t["id"] for t in new]
        self.assertEqual(ids, ["s-m1", "s-pilot"])               # 先行1件＋ゲートのみ
        self.assertEqual(next(t for t in new if t["id"] == "s-pilot")["kind"], "verify")
        self.assertNotIn("s-reduce", ids)

        # pilot ゲート未了の間は残りを展開しない
        nodes.update({"s-m1": {"goal": "", "deps": [], "kind": "map"},
                      "s-pilot": {"goal": "", "deps": ["s-m1"], "kind": "verify"}})
        results.update({"s-m1": {"status": "done"}, "s-pilot": {"status": "running"}})
        _, new2, _ = kf.continue_stub("各件を移行", nodes, results, 1, exemplar_first=True)
        self.assertEqual([t for t in new2 if t["id"].startswith("s-")], [])

        # Stage 2: pilot ゲート done → 残り map（pilot＋ゲートに依存）＋ reduce を展開
        results["s-pilot"] = {"status": "done"}
        _, new3, _ = kf.continue_stub("各件を移行", nodes, results, 2, exemplar_first=True)
        ids3 = [t["id"] for t in new3]
        self.assertEqual(ids3, ["s-m2", "s-m3", "s-reduce"])
        self.assertEqual(next(t for t in new3 if t["id"] == "s-m2")["deps"], ["s-m1", "s-pilot"])
        self.assertEqual(set(next(t for t in new3 if t["id"] == "s-reduce")["deps"]),
                         {"s-m1", "s-m2", "s-m3"})

    def test_default_fanout_unchanged_without_exemplar_first(self):
        # exemplar_first 無し（既定）は従来どおり一括 fan-out
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        _, new, _ = kf.continue_stub("g", nodes, results, 0)
        self.assertEqual([t["id"] for t in new], ["s-m1", "s-m2", "s-reduce"])

    def test_fanout_respects_max(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": list(range(100))}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, max_fanout=5)
        self.assertEqual(len([t for t in new if t["kind"] == "map"]), 5)

    def test_map_goal_carries_request_intent(self):
        # map ゴールに元の要求（intent）が埋め込まれ、各要素に本来のタスクが適用される
        nodes = {"t1": {"id": "t1", "goal": "分解", "deps": [], "kind": "split"}}
        results = {"t1": {"status": "done", "data": ["1-100", "101-200"]}}
        _, new, _ = kf.continue_stub("1-1000まで素数を出して", nodes, results, 0)
        m1 = next(t for t in new if t["id"] == "t1-m1")
        self.assertIn("素数", m1["goal"])
        self.assertIn("1-100", m1["goal"])
        # reduce ゴールも intent を保持（並べ替え・集約条件を失わない）
        red = next(t for t in new if t["id"] == "t1-reduce")
        self.assertIn("素数", red["goal"])

    def test_collapse_static_split_successors(self):
        # planner が split→work→reduce を静的に焼き込んでも fan-out 前に後段を除去
        g = {"t1": {"id": "t1", "goal": "分割", "deps": [], "kind": "split"},
             "t2": {"id": "t2", "goal": "work", "deps": ["t1"], "kind": "work"},
             "t3": {"id": "t3", "goal": "reduce", "deps": ["t2"], "kind": "reduce"}}
        kf._sanitize_graph(g)
        self.assertEqual(sorted(g), ["t1"])

    def test_collapse_skipped_after_fanout(self):
        # 既に fan-out 済み（<split>-reduce 生成済み）なら除去しない
        g = {"t1": {"id": "t1", "goal": "s", "deps": [], "kind": "split"},
             "t1-m1": {"id": "t1-m1", "goal": "m", "deps": [], "kind": "map"},
             "t1-reduce": {"id": "t1-reduce", "goal": "r", "deps": ["t1-m1"], "kind": "reduce"}}
        kf._sanitize_graph(g)
        self.assertEqual(sorted(g), ["t1", "t1-m1", "t1-reduce"])

    def test_split_not_reexpanded(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"},
                 "s-reduce": {"goal": "集約", "deps": ["s-m1"], "kind": "reduce"}}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        # 既に展開済み（s-reduce あり）→ 追加しない
        self.assertFalse(any(t["id"].startswith("s-m") for t in new))

    def test_strategy_map_reduce_starts_with_split(self):
        strat, tasks = kf.plan_strategy_stub("ファイルをそれぞれ処理して集約")
        self.assertIn("map-reduce", strat["patterns"])
        self.assertEqual([t["kind"] for t in tasks], ["split"])
        # 集約パターンは既定（auto）で検証 gate が有効
        self.assertTrue(strat["review"])
        self.assertIn("adversarial-verification", strat["patterns"])


class CoerceTasksTests(unittest.TestCase):
    def test_unknown_kind_coerced_to_work(self):
        out = kf._coerce_tasks([{"id": "a", "goal": "g", "kind": "bogus"}])
        self.assertEqual(out[0]["kind"], "work")

    def test_valid_kinds_preserved(self):
        out = kf._coerce_tasks([{"id": "a", "kind": "split"}, {"id": "b", "kind": "reduce"}])
        self.assertEqual([t["kind"] for t in out], ["split", "reduce"])

    def test_duplicate_and_existing_ids_dropped(self):
        out = kf._coerce_tasks(
            [{"id": "x"}, {"id": "x"}, {"id": "y"}], existing={"y"})
        self.assertEqual([t["id"] for t in out], ["x"])  # 重複 x は 1 つ、既存 y は除外

    def test_deps_stringified(self):
        out = kf._coerce_tasks([{"id": "a", "deps": [1, "b"]}])
        self.assertEqual(out[0]["deps"], ["1", "b"])


class PlannerRobustnessTests(unittest.TestCase):
    """planner（kiro）がオブジェクトでなくベア配列を返しても落ちないこと。"""

    def test_continue_kiro_handles_bare_list(self):
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(
                kf, "run_kiro",
                return_value='[{"id":"n1","goal":"次","deps":[],"kind":"work"}]'):
            decision, new, _ = kf.continue_kiro("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new], ["n1"])

    def test_continue_kiro_handles_scalar(self):
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(kf, "run_kiro", return_value="42"):
            decision, new, _ = kf.continue_kiro("req", nodes, results, 0)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])

    def test_plan_strategy_kiro_handles_bare_list(self):
        with mock.patch.object(
                kf, "run_kiro",
                return_value='[{"id":"t1","goal":"分解","deps":[],"kind":"split"}]'):
            strat, tasks = kf.plan_strategy_kiro("req", None)
        self.assertEqual([t["id"] for t in tasks], ["t1"])


class KiroTimeoutTests(unittest.TestCase):
    """kiro-cli のハングがタイムアウトで失敗化され、run が無限停止しないこと。"""

    def test_run_kiro_timeout_raises_runtimeerror(self):
        import subprocess
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="kiro-cli", timeout=k.get("timeout"))
        with mock.patch.object(kf.subprocess, "run", side_effect=boom):
            with self.assertRaises(RuntimeError) as ctx:
                kf.run_kiro("素数を列挙", None)
        self.assertIn("タイムアウト", str(ctx.exception))

    def test_kiro_timeout_env_override(self):
        with mock.patch.dict(os.environ, {"KIRO_FLOW_KIRO_TIMEOUT": "0"}):
            self.assertIsNone(kf._kiro_timeout())   # 0/負で無効化
        with mock.patch.dict(os.environ, {"KIRO_FLOW_KIRO_TIMEOUT": "120"}):
            self.assertEqual(kf._kiro_timeout(), 120.0)

    def test_kiro_timeout_config_beats_env(self):
        # 設定ファイル（_configure_thresholds 経由）が環境変数より優先される
        with mock.patch.object(kf, "_KIRO_TIMEOUT", 300.0), \
             mock.patch.dict(os.environ, {"KIRO_FLOW_KIRO_TIMEOUT": "120"}):
            self.assertEqual(kf._kiro_timeout(), 300.0)
        with mock.patch.object(kf, "_KIRO_TIMEOUT", 0.0):
            self.assertIsNone(kf._kiro_timeout())   # 設定の 0/負も無効化として尊重

    def test_stub_sleep_max_config_beats_env(self):
        # stub_sleep_max も設定が環境変数より優先される（0 で即時）
        calls = []
        with mock.patch.object(kf, "_STUB_SLEEP_MAX", 0.0), \
             mock.patch.dict(os.environ, {"KIRO_FLOW_STUB_SLEEP_MAX": "5"}), \
             mock.patch.object(kf.time, "sleep", side_effect=lambda s: calls.append(s)):
            kf._stub_sleep()
        self.assertEqual(calls, [])   # 設定 0 → sleep されない

    def test_configure_thresholds_pins_config_values(self):
        # resolve_config 済みの args から kiro_timeout / stub_sleep_max が確定すること
        import argparse
        args = argparse.Namespace(argv_limit=None, executor_dir=None,
                                  kiro_timeout=45.0, stub_sleep_max=0.0)
        with mock.patch.object(kf, "_KIRO_TIMEOUT", None), \
             mock.patch.object(kf, "_STUB_SLEEP_MAX", None):
            kf._configure_thresholds(args)
            self.assertEqual(kf._KIRO_TIMEOUT, 45.0)
            self.assertEqual(kf._STUB_SLEEP_MAX, 0.0)


class StructuredExtractionTests(unittest.TestCase):
    """自由記述 kind の本文に紛れた JSON 風断片を data に誤昇格させないこと。"""

    def test_work_does_not_extract_incidental_json(self):
        # 本文に "issues": [] を含む work 出力でも data は None（誤抽出の事故防止）
        txt = 'verify=pass（修正不要）。t2の検査で問題なし（{"ok": true, "issues": []}）。通過。'
        with mock.patch.object(kf, "run_kiro", return_value=txt):
            _, data = kf.execute_kiro("work", "修正し通過", {}, None)
        self.assertIsNone(data)

    def test_generate_does_not_extract_incidental_json(self):
        with mock.patch.object(kf, "run_kiro", return_value="例: [1, 2] のような配列を返す関数"):
            _, data = kf.execute_kiro("generate", "関数を書く", {}, None)
        self.assertIsNone(data)

    def test_split_still_extracts_list(self):
        with mock.patch.object(kf, "run_kiro", return_value='["1-100", "101-200"]'):
            _, data = kf.execute_kiro("split", "分割", {}, None)
        self.assertEqual(data, ["1-100", "101-200"])

    def test_reduce_still_extracts_and_reconciles(self):
        with mock.patch.object(kf, "run_kiro",
                               return_value='{"primes": [2, 3, 5], "count": 99}'):
            _, data = kf.execute_kiro("reduce", "集約", {}, None)
        self.assertEqual(data["count"], 3)  # 実リスト長へ補正


def _load_executor_plugin(name):
    """executors/<name>.py をテスト用にロードする。"""
    path = HERE.parent / "executors" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"kf_exec_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gl_plugin = _load_executor_plugin("gitlab")


class GitlabExecutorPluginTests(unittest.TestCase):
    """gitlab executor プラグイン（opt-in）: イシュー起票 → **関連 MR の状態**をポーリング →
    全マージ＝承認（イシュークローズして成功）/ 一つでも未マージクローズ＝却下（やり直し）。"""

    def setUp(self):
        # ポーリング待ちを無くす設定を環境変数（KIRO_FLOW_EXECUTOR_CONFIG）で渡す
        self._cfg = {"conn_label": "default", "repo_url": "https://gitlab.com/group/repo",
                     "labels": "status:open,assignee:any", "priority": "priority:normal",
                     "poll_interval": 0.0, "timeout": 0.0,
                     "approved_label": "status:approved", "done_label": "status:done"}
        self._prev_env = os.environ.get("KIRO_FLOW_EXECUTOR_CONFIG")
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(self._cfg)

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None)
        else:
            os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = self._prev_env

    def _run_with(self, api_side, mrs_seq=None, notes=None, token="glpat-x"):
        """gl_api（issue GET/POST/PUT）と gl_api_list（related_merge_requests / notes）を
        モックして execute を回す。mrs_seq は poll 毎の関連 MR リスト列、notes は人コメント。"""
        mrs_seq = list(mrs_seq or [[]])

        def list_side(host, token_, path, params=None):
            if path.endswith("/related_merge_requests"):
                return mrs_seq.pop(0) if len(mrs_seq) > 1 else mrs_seq[0]
            if path.endswith("/notes"):
                return notes or []
            return []

        with mock.patch.object(gl_plugin, "_resolve_token", return_value=token), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api_side) as m, \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            text, data = gl_plugin.execute("work", "ログイン画面を追加", {})
        return text, data, m

    def test_all_mrs_merged_approves_and_closes(self):
        # 関連 MR が全てマージ → 承認。イシューをクローズして成功を返す。
        def api(host, token, method, path, data=None, params=None):
            if method == "POST" and path.endswith("/issues"):
                return {"iid": 42, "web_url": "https://gitlab.com/group/repo/-/issues/42"}
            if method == "GET" and path.endswith("/issues/42"):
                return {"labels": ["status:approved"], "state": "opened"}
            if method == "PUT" and path.endswith("/issues/42"):
                return {"state": "closed"}     # クローズ
            return {}

        mrs = [{"iid": 1, "state": "merged", "web_url": "mr1"},
               {"iid": 2, "state": "merged", "web_url": "mr2"}]
        text, data, m = self._run_with(api, mrs_seq=[mrs])
        self.assertEqual(data["decision"], "approved")
        self.assertTrue(data["closed"])
        self.assertEqual(data["merged_mrs"], [1, 2])
        # イシュークローズの PUT（state_event=close）が出る
        close = next(c for c in m.call_args_list if c.args[2] == "PUT")
        self.assertEqual(close.kwargs["data"]["state_event"], "close")
        # 起票の POST も検証
        post = next(c for c in m.call_args_list if c.args[2] == "POST")
        self.assertIn("priority:normal", post.kwargs["data"]["labels"])
        self.assertIn("gitlab-idd:creator-node-id", post.kwargs["data"]["description"])

    def test_one_mr_closed_unmerged_rejects_with_comments(self):
        # 一つでも未マージでクローズ → 却下。人コメントを [gitlab-reject] に載せて送出。
        closed = {"n": 0}

        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 9, "web_url": "https://gitlab.com/group/repo/-/issues/9"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            if method == "PUT":
                closed["n"] += 1               # イシュークローズが呼ばれた
                return {"state": "closed"}
            return {}

        mrs = [{"iid": 1, "state": "merged"}, {"iid": 2, "state": "closed"}]  # 一つ却下
        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[mrs],
                           notes=[{"body": "命名が要件と違う。修正して。", "system": False}])
        msg = str(ctx.exception)
        self.assertIn("[gitlab-reject]", msg)
        self.assertIn("命名が要件と違う", msg)        # 人コメントをやり直し指示に活かす
        self.assertEqual(closed["n"], 1)              # 元イシューはクローズされる
        # 承認と対称の機械可読な決着: 例外に data が載る（worker が failed result に書く）
        d = ctx.exception.data
        self.assertEqual(d["decision"], "rejected")
        self.assertEqual(d["issue_iid"], 9)
        self.assertIn("命名が要件と違う", d["guidance"])
        self.assertEqual(d["merged_mrs"], [1])        # マージ済みだった MR も分かる
        self.assertTrue(d["closed"])

    def test_reject_without_comments_says_auto(self):
        # 人コメントが無い却下 → 自動判断の指示で送出
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 3, "web_url": "u"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[{"iid": 1, "state": "closed"}]], notes=[])
        self.assertIn("[gitlab-reject]", str(ctx.exception))
        self.assertIn("自動で", str(ctx.exception))

    def test_deleted_issue_treated_as_rejected(self):
        # 決着待ち中にイシューが削除（404）されたら、一般エラーでなく却下（取り下げ）として
        # 決着させる（誤削除でもフィードバックループを壊さない防御）。guidance は空＝自動判断。
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 7, "web_url": "https://gitlab.com/group/repo/-/issues/7"}
            if method == "GET":
                raise RuntimeError("GitLab API GET /projects/x/issues/7 失敗: HTTP 404 Not Found")
            return {}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[]])
        self.assertIn("[gitlab-reject]", str(ctx.exception))
        self.assertIn("削除", str(ctx.exception))
        d = ctx.exception.data
        self.assertEqual(d["decision"], "rejected")
        self.assertIn("削除", d["reason"])
        self.assertEqual(d["guidance"], "")

    def test_non_404_error_still_raises_plain_failure(self):
        # ネットワーク断・権限エラー等（404 以外）は却下でなく従来どおりの失敗として送出
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 7, "web_url": "u"}
            if method == "GET":
                raise RuntimeError("GitLab API GET /projects/x/issues/7 失敗: HTTP 500 Server Error")
            return {}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[]])
        self.assertNotIn("[gitlab-reject]", str(ctx.exception))
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_open_mr_keeps_waiting_until_merged(self):
        # MR が open のうちは待機し、全マージで承認。
        seq = [[{"iid": 1, "state": "opened"}],          # まだ作業中
               [{"iid": 1, "state": "merged"}]]          # マージ完了
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 8, "web_url": "u"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        text, data, _ = self._run_with(api, mrs_seq=seq)
        self.assertEqual(data["decision"], "approved")

    def test_timeout_raises_before_any_mr(self):
        # MR が一つも出ない（レビュー前）まま全体 timeout 超過で失敗する
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(
            dict(self._cfg, timeout=0.01, approved_timeout=0.01, poll_interval=0.0))

        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 1, "web_url": "https://gitlab.com/group/repo/-/issues/1"}
            return {"labels": ["status:open"], "state": "opened"}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[]])
        self.assertIn("レビュー/MR 作成", str(ctx.exception))

    # --- 冪等性: 再 claim 時に同じタスクのイシューを二重起票しない ---------------
    def test_task_token_is_deterministic_from_art_dir(self):
        # art_dir（runs/<run>/artifacts/<node>）から決定的トークンを作る。再 claim で同一になる。
        a = gl_plugin._task_token("/bus/runs/r1/artifacts/n1")
        b = gl_plugin._task_token("/other/runs/r1/artifacts/n1")  # 別バスでも同じ run/node
        self.assertTrue(a.startswith("kf-"))
        self.assertEqual(a, b)
        # run か node が違えば別トークン
        self.assertNotEqual(a, gl_plugin._task_token("/bus/runs/r1/artifacts/n2"))
        self.assertNotEqual(a, gl_plugin._task_token("/bus/runs/r2/artifacts/n1"))
        # 想定外の形（art_dir 無し / artifacts 区切りが無い）は None（＝従来どおり毎回新規起票）
        self.assertIsNone(gl_plugin._task_token(None))
        self.assertIsNone(gl_plugin._task_token(""))
        self.assertIsNone(gl_plugin._task_token("/some/random/path"))

    def test_new_issue_embeds_task_marker(self):
        # art_dir を渡すと本文に隠しマーカーが埋まり、検索（open イシュー）も走る。
        art_dir = "/bus/runs/r1/artifacts/n1"
        token = gl_plugin._task_token(art_dir)

        def api(host, tok, method, path, data=None, params=None):
            if method == "POST" and path.endswith("/issues"):
                return {"iid": 7, "web_url": "u7"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        searched = {"n": 0}

        def list_side(host, tok, path, params=None):
            if path.endswith("/issues"):       # 起票前の重複検索（既存なし）
                searched["n"] += 1
                self.assertEqual(params.get("state"), "opened")
                self.assertEqual(params.get("search"), token)
                return []
            if path.endswith("/related_merge_requests"):
                return [{"iid": 1, "state": "merged"}]
            return []

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api) as m, \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            _text, data = gl_plugin.execute("work", "ログイン画面を追加", {}, art_dir=art_dir)
        self.assertEqual(data["decision"], "approved")
        self.assertEqual(searched["n"], 1)                      # 起票前に検索した
        post = next(c for c in m.call_args_list if c.args[2] == "POST")
        self.assertIn(gl_plugin._task_marker(token), post.kwargs["data"]["description"])

    def test_reattaches_to_existing_open_issue_no_duplicate(self):
        # 再 claim: 同じトークンの open イシューが既にあれば**新規起票せず**再アタッチする。
        art_dir = "/bus/runs/r1/artifacts/n1"
        token = gl_plugin._task_token(art_dir)
        marker = gl_plugin._task_marker(token)

        def api(host, tok, method, path, data=None, params=None):
            if method == "POST":
                raise AssertionError("二重起票してはならない（再アタッチすべき）")
            if method == "GET" and path.endswith("/issues/42"):
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        def list_side(host, tok, path, params=None):
            if path.endswith("/issues"):       # 既存の open イシューがヒット
                return [{"iid": 42, "web_url": "u42", "description": f"本文\n{marker}"}]
            if path.endswith("/related_merge_requests"):
                return [{"iid": 1, "state": "merged"}]
            return []

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            _text, data = gl_plugin.execute("work", "ログイン画面を追加", {}, art_dir=art_dir)
        self.assertEqual(data["decision"], "approved")
        self.assertEqual(data["issue_iid"], 42)                # 既存イシューで決着

    def test_search_hit_without_marker_is_ignored(self):
        # 検索が別タスクのイシューを取りこぼし誤ヒットしても、マーカー不一致なら新規起票する。
        art_dir = "/bus/runs/r1/artifacts/n1"

        def api(host, tok, method, path, data=None, params=None):
            if method == "POST" and path.endswith("/issues"):
                return {"iid": 99, "web_url": "u99"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        def list_side(host, tok, path, params=None):
            if path.endswith("/issues"):       # マーカーを持たない別イシュー（誤ヒット）
                return [{"iid": 5, "web_url": "u5", "description": "無関係なイシュー"}]
            if path.endswith("/related_merge_requests"):
                return [{"iid": 1, "state": "merged"}]
            return []

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api) as m, \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            _text, data = gl_plugin.execute("work", "x", {}, art_dir=art_dir)
        self.assertEqual(data["issue_iid"], 99)                # 新規起票で決着
        self.assertTrue(any(c.args[2] == "POST" for c in m.call_args_list))

    def test_mr_decision_helper(self):
        self.assertEqual(gl_plugin._mr_decision(["merged", "merged"]), "approved")
        self.assertEqual(gl_plugin._mr_decision(["merged", "closed"]), "rejected")
        self.assertEqual(gl_plugin._mr_decision(["opened", "merged"]), "")  # 待機
        self.assertEqual(gl_plugin._mr_decision([]), "")                    # MR 無し＝未決着

    # --- イシューが外部でクローズされたときの承認/却下判定 ----------------------
    def test_closed_issue_approved_by_label(self):
        # MR で決着がつかないまま外部クローズ＋status:approved → 承認
        d, why = gl_plugin._closed_issue_decision(
            "h", "t", "p", 1, {"status:approved"}, "status:approved", "status:done")
        self.assertEqual(d, "approved")
        self.assertIn("status:approved", why)

    def test_closed_issue_approved_by_comment(self):
        # ラベル無しでも、コメントが承認を示唆していれば承認
        with mock.patch.object(gl_plugin, "_get_comments",
                               return_value=[{"body": "確認しました。承認します。", "system": False}]):
            d, why = gl_plugin._closed_issue_decision(
                "h", "t", "p", 1, set(), "status:approved", "status:done")
        self.assertEqual(d, "approved")

    def test_closed_issue_rejected_by_comment(self):
        # コメントが却下を示唆していれば却下（却下語は承認語より優先）
        with mock.patch.object(gl_plugin, "_get_comments",
                               return_value=[{"body": "これは却下。やり直してください。", "system": False}]):
            d, _why = gl_plugin._closed_issue_decision(
                "h", "t", "p", 1, set(), "status:approved", "status:done")
        self.assertEqual(d, "rejected")

    def test_closed_issue_no_hint_is_withdrawn_reject(self):
        # 手掛かりが無い外部クローズは取り下げ＝却下扱い
        with mock.patch.object(gl_plugin, "_get_comments", return_value=[]):
            d, why = gl_plugin._closed_issue_decision(
                "h", "t", "p", 1, set(), "status:approved", "status:done")
        self.assertEqual(d, "rejected")
        self.assertIn("取り下げ", why)

    def test_externally_closed_with_approve_comment_returns_done(self):
        # execute 全体: MR 無し・外部クローズ・承認コメント → done（成果を返す）でグラフへ反映
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 5, "web_url": "u5"}
            if method == "GET":
                return {"labels": [], "state": "closed"}   # 外部クローズ
            return {"state": "closed"}

        text, data, _ = self._run_with(
            api, mrs_seq=[[]],
            notes=[{"body": "対応ありがとう。承認します。", "system": False}])
        self.assertEqual(data["decision"], "approved")
        self.assertIn("承認", data["reason"])

    def test_externally_closed_with_reject_comment_raises(self):
        # execute 全体: MR 無し・外部クローズ・却下コメント → [gitlab-reject] で送出（failed→やり直し）
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 6, "web_url": "u6"}
            if method == "GET":
                return {"labels": [], "state": "closed"}
            return {"state": "closed"}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[]],
                           notes=[{"body": "要件と違うため却下。", "system": False}])
        self.assertIn("[gitlab-reject]", str(ctx.exception))

    def test_missing_repo_url_raises(self):
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(dict(self._cfg, repo_url=""))
        with self.assertRaises(RuntimeError) as ctx:
            with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"):
                gl_plugin.execute("work", "x", {})
        self.assertIn("repo_url", str(ctx.exception))

    def test_missing_token_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            with mock.patch.object(gl_plugin, "_resolve_token", return_value=""):
                gl_plugin.execute("work", "x", {})
        self.assertIn("トークン", str(ctx.exception))

    def test_config_zero_poll_interval_respected(self):
        # 0.0 が `x or default` で 30 に潰れないこと（プラグイン側 _as_float の確認）
        self.assertEqual(gl_plugin._as_float(0.0, 30.0), 0.0)
        self.assertEqual(gl_plugin._as_float(None, 30.0), 30.0)
        self.assertEqual(gl_plugin._as_float("bad", 30.0), 30.0)

    def test_repo_url_default_empty(self):
        os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None)
        self.assertEqual(gl_plugin._config()["repo_url"], "")

    def test_env_override_beats_config_block(self):
        # 個別環境変数 KIRO_FLOW_GITLAB_* が KIRO_FLOW_EXECUTOR_CONFIG より優先される
        os.environ["KIRO_FLOW_GITLAB_CONN_LABEL"] = "work"
        try:
            self.assertEqual(gl_plugin._config()["conn_label"], "work")
        finally:
            os.environ.pop("KIRO_FLOW_GITLAB_CONN_LABEL", None)

    def test_issue_body_has_acceptance_criteria_and_deps(self):
        deps = {"t1": {"output": "上流の成果", "data": {"n": 3}}}
        body = gl_plugin._issue_body("synthesize", "統合する", deps)
        self.assertIn("## 受け入れ条件", body)
        self.assertIn("統合する", body)
        self.assertIn("t1", body)
        self.assertIn("kiro-flow", body)


class GitlabNativeApiTests(unittest.TestCase):
    """native レイヤ（URL 解析・REST 組立・トークン解決）の単体検証。"""

    _TOKEN_ENV = ("GITLAB_TOKEN", "GL_TOKEN")

    def setUp(self):
        self._prev_tok = {k: os.environ.pop(k, None) for k in self._TOKEN_ENV}

    def tearDown(self):
        for k, v in self._prev_tok.items():
            if v is not None:
                os.environ[k] = v

    def test_parse_project_url_variants(self):
        self.assertEqual(gl_plugin._parse_project_url("https://gitlab.com/g/r.git"),
                         ("gitlab.com", "g/r"))
        self.assertEqual(gl_plugin._parse_project_url("https://gl.example.com/a/b/c/"),
                         ("gl.example.com", "a/b/c"))
        self.assertEqual(gl_plugin._parse_project_url("not-a-url"), (None, None))

    def test_resolve_project_requires_repo_url(self):
        with self.assertRaises(RuntimeError) as ctx:
            gl_plugin._resolve_project({"repo_url": ""})
        self.assertIn("repo_url", str(ctx.exception))

    def test_resolve_project_parses_ssh_url(self):
        # SSH 形のワークスペース URL も起票先として解釈できる（パス・末尾 .git を剥がす）
        host, project, _ = gl_plugin._resolve_project(
            {}, workspace_url="git@gitlab.com:group/repo.git")
        self.assertEqual((host, project), ("gitlab.com", "group/repo"))

    def test_resolve_project_parses_repo_url(self):
        host, project, repo_url = gl_plugin._resolve_project(
            {"repo_url": "https://gitlab.com/group/sub/repo"})
        self.assertEqual((host, project), ("gitlab.com", "group/sub/repo"))

    def test_resolve_project_prefers_workspace_over_config(self):
        # ワークスペース URL が config の repo_url より優先される（その run の唯一の書込先へ起票）
        host, project, used = gl_plugin._resolve_project(
            {"repo_url": "https://gitlab.com/fallback/repo"},
            workspace_url="https://gitlab.com/team/app")
        self.assertEqual((host, project), ("gitlab.com", "team/app"))
        self.assertEqual(used, "https://gitlab.com/team/app")

    def test_gl_api_builds_v4_request(self):
        captured = {}

        class _Resp:
            headers = {}

            def read(self):
                return b'{"iid": 1}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["token"] = req.headers.get("Private-token")
            return _Resp()

        with mock.patch.object(gl_plugin.urllib.request, "urlopen", side_effect=fake_urlopen):
            out = gl_plugin.gl_api("gitlab.com", "glpat-x", "GET", "/projects/1/issues/2")
        self.assertEqual(out, {"iid": 1})
        self.assertEqual(captured["url"], "https://gitlab.com/api/v4/projects/1/issues/2")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["token"], "glpat-x")

    def test_gl_api_http_error_raises_runtimeerror(self):
        def fake_urlopen(req, timeout=None):
            raise gl_plugin.urllib.error.HTTPError(
                req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message":"404"}'))

        with mock.patch.object(gl_plugin.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(RuntimeError) as ctx:
                gl_plugin.gl_api("gitlab.com", "t", "GET", "/projects/1")
        self.assertIn("404", str(ctx.exception))

    def test_token_prefers_connections_yaml(self):
        # gl.py と同じく connections.yaml（接続ラベル）を最優先で読む
        with mock.patch.object(gl_plugin, "_token_from_connections", return_value="tok-conn"), \
             mock.patch.object(gl_plugin, "_token_from_shell_files", return_value="tok-shell"):
            os.environ["GITLAB_TOKEN"] = "tok-env"
            self.assertEqual(gl_plugin._resolve_token({"conn_label": "default"}), "tok-conn")

    def test_token_env_fallback(self):
        with mock.patch.object(gl_plugin, "_token_from_connections", return_value=""), \
             mock.patch.object(gl_plugin, "_token_from_shell_files", return_value="tok-shell"):
            os.environ["GITLAB_TOKEN"] = "tok-env"
            self.assertEqual(gl_plugin._resolve_token({"conn_label": "default"}), "tok-env")

    def test_token_shell_fallback(self):
        with mock.patch.object(gl_plugin, "_token_from_connections", return_value=""), \
             mock.patch.object(gl_plugin, "_token_from_shell_files", return_value="tok-shell"):
            self.assertEqual(gl_plugin._resolve_token({"conn_label": "default"}), "tok-shell")

    def test_token_not_read_from_kiro_flow_yaml(self):
        # kiro-flow.yaml 由来の cfg に token を置いても無視される（gl.py の場所だけを読む）
        with mock.patch.object(gl_plugin, "_token_from_connections", return_value=""), \
             mock.patch.object(gl_plugin, "_token_from_shell_files", return_value=""):
            self.assertEqual(
                gl_plugin._resolve_token({"conn_label": "default", "token": "glpat-yaml"}), "")

    def test_token_from_connections_no_scripts_dir(self):
        with mock.patch.object(gl_plugin, "_find_gitlab_idd_scripts_dir", return_value=None):
            self.assertEqual(gl_plugin._token_from_connections("default"), "")


class CallExecutorDispatchTests(unittest.TestCase):
    """call_executor: clone 指示（repo_instruction）を goal に結合せず別引数で渡す。
    受け取れない旧 executor には従来どおり goal 先頭へ結合する（後方互換）。"""

    INSTR = "【成果物リポジトリ】… /tmp/clone/x"

    def test_accepts_detection(self):
        def new_exec(kind, goal, dep_results, model, art_dir, dep_arts, repo_instruction=""):
            return "ok", None

        def legacy_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None):
            return "ok", None

        def kwargs_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None, **kw):
            return "ok", None

        self.assertTrue(kf._executor_accepts(new_exec, "repo_instruction"))
        self.assertTrue(kf._executor_accepts(kwargs_exec, "repo_instruction"))
        self.assertFalse(kf._executor_accepts(legacy_exec, "repo_instruction"))

    def test_new_executor_gets_clean_goal_and_instruction(self):
        seen = {}

        def new_exec(kind, goal, dep_results, model, art_dir, dep_arts, repo_instruction=""):
            seen["goal"] = goal
            seen["instr"] = repo_instruction
            return "ok", None

        kf.call_executor(new_exec, "work", "本来のゴール", {}, None, None, None, self.INSTR)
        self.assertEqual(seen["goal"], "本来のゴール")        # goal は汚れない
        self.assertEqual(seen["instr"], self.INSTR)

    def test_legacy_executor_gets_prepended_goal(self):
        seen = {}

        def legacy_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None):
            seen["goal"] = goal
            return "ok", None

        kf.call_executor(legacy_exec, "work", "本来のゴール", {}, None, None, None, self.INSTR)
        self.assertTrue(seen["goal"].startswith(self.INSTR))   # 旧契約は従来どおり結合
        self.assertIn("本来のゴール", seen["goal"])

    def test_no_instruction_passes_goal_unchanged(self):
        seen = {}

        def new_exec(kind, goal, dep_results, model, art_dir, dep_arts, repo_instruction=""):
            seen["goal"], seen["instr"] = goal, repo_instruction
            return "ok", None

        kf.call_executor(new_exec, "work", "ゴール", {}, None, None, None, "")
        self.assertEqual(seen["goal"], "ゴール")
        self.assertEqual(seen["instr"], "")

    def test_execute_kiro_puts_instruction_in_prompt_not_polluting_goal(self):
        captured = {}

        def fake_run_kiro(prompt, model):
            captured["prompt"] = prompt
            return "成果"

        with mock.patch.object(kf, "run_kiro", side_effect=fake_run_kiro):
            kf.call_executor(kf.execute_kiro, "work", "ログイン追加", {}, None, None, None,
                             self.INSTR)
        # タスク行の goal は本来のゴールのまま、clone 指示はプロンプト内に別途含まれる
        self.assertIn("タスク(work): ログイン追加", captured["prompt"])
        self.assertIn(self.INSTR, captured["prompt"])


class GitlabRepoInstructionTests(unittest.TestCase):
    """gitlab: clone 指示はイシュー本文の独立節に載せ、タイトル/目的は本来の goal を保つ。"""

    def test_issue_body_renders_workspace_as_markdown(self):
        ws = {"url": "https://git/app.git", "path": "apps/api", "base": "main",
              "target": "develop", "desc": "API", "branch": "kf/run-1",
              "clone": "/tmp/kiro-flow-ws-123/app"}
        body = gl_plugin._issue_body("work", "ログイン画面を追加", {}, ws)
        self.assertIn("## 目的", body)
        self.assertIn("## 対象リポジトリ", body)
        # 構造化 Markdown 箇条書き（レイアウトが崩れない）
        self.assertIn("- **リポジトリ**: https://git/app.git", body)
        self.assertIn("- **変更対象フォルダ**: `apps/api` 配下のみ", body)
        self.assertIn("`main` から分岐", body)
        self.assertIn("`develop` へ MR", body)
        # ローカルの作業ディレクトリ（clone パス）はリモートには無いので載せない
        self.assertNotIn("作業ディレクトリ", body)
        self.assertNotIn("/tmp/kiro-flow-ws-123/app", body)
        # 目的節は本来の goal のまま
        purpose = body.split("## 目的", 1)[1].split("##", 1)[0]
        self.assertIn("ログイン画面を追加", purpose)

    def test_issue_body_omits_section_when_no_workspace(self):
        body = gl_plugin._issue_body("work", "ゴール", {})
        self.assertNotIn("## 対象リポジトリ", body)
        self.assertNotIn("## 参照リポジトリ", body)

    def test_issue_body_renders_reference_section(self):
        # 参照リポジトリ（読むだけ）がイシュー本文に独立節として載る
        refs = [{"url": "https://git/spec.git", "path": "openapi", "base": "main", "desc": "API 仕様"},
                {"url": "https://git/lib.git"}]
        body = gl_plugin._issue_body("work", "実装する", {}, None, refs)
        self.assertIn("## 参照リポジトリ", body)
        self.assertIn("- **https://git/spec.git**", body)
        self.assertIn("フォルダ `openapi`", body)
        self.assertIn("API 仕様", body)
        self.assertIn("- **https://git/lib.git**", body)
        self.assertIn("読み取り専用", body)

    def test_execute_renders_workspace_section_without_clone_path(self):
        ws = {"url": "https://gitlab.com/group/repo.git", "base": "main", "target": "main",
              "clone": "/tmp/kiro-flow-ws-9/repo"}
        calls = []

        def api(host, token, method, path, data=None, params=None):
            calls.append((method, data))
            if method == "POST":
                return {"iid": 3, "web_url": "https://gitlab.com/group/repo/-/issues/3"}
            if method == "PUT":
                return {"state": "closed"}
            return {"labels": ["status:approved"], "state": "opened"}

        def list_side(host, token, path, params=None):
            return [{"iid": 1, "state": "merged"}] if path.endswith(
                "/related_merge_requests") else []   # 全マージ＝承認

        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(
            {"repo_url": "https://gitlab.com/group/repo", "poll_interval": 0.0, "timeout": 0.0})
        try:
            with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
                 mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
                 mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
                # 全 MR マージ＝承認でクローズして完了。workspace 節がイシュー本文に出る。
                gl_plugin.execute("work", "ログイン画面を追加", {}, workspace=ws,
                                  repo_instruction="【ワークスペース】… /tmp/kiro-flow-ws-9/repo")
        finally:
            os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None)
        post = next(c for c in calls if c[0] == "POST")
        # タイトルは本来の goal
        self.assertIn("ログイン画面を追加", post[1]["title"])
        # 本文は構造化された対象リポジトリ節（ローカル clone パスは載らない）
        self.assertIn("## 対象リポジトリ", post[1]["description"])
        self.assertNotIn("/tmp/kiro-flow-ws-9/repo", post[1]["description"])
        self.assertNotIn("作業ディレクトリ", post[1]["description"])
        purpose = post[1]["description"].split("## 目的", 1)[1].split("##", 1)[0]
        self.assertIn("ログイン画面を追加", purpose)


class ExecutorResolutionTests(unittest.TestCase):
    """executor のプラグイン解決（kiro-loop の event_hook 流のローダ）。"""

    def _args(self, **kw):
        import types
        return types.SimpleNamespace(**kw)

    def test_builtin_kiro_and_stub(self):
        self.assertIs(kf.make_executor(self._args(executor="kiro")), kf.execute_kiro)
        self.assertIs(kf.make_executor(self._args(executor="stub")), kf.execute_stub)

    def test_default_is_kiro(self):
        self.assertIs(kf.make_executor(self._args(executor=None)), kf.execute_kiro)

    def test_resolves_bundled_gitlab_plugin(self):
        fn = kf.make_executor(self._args(executor="gitlab", gitlab={"poll_interval": 1}))
        self.assertTrue(callable(fn))
        # プラグイン設定が環境変数で渡されていること
        self.assertEqual(json.loads(os.environ["KIRO_FLOW_EXECUTOR_CONFIG"]),
                         {"poll_interval": 1})

    def test_explicit_path_plugin(self):
        path = str(HERE.parent / "executors" / "gitlab.py")
        fn = kf.make_executor(self._args(executor=path))
        self.assertTrue(callable(fn))

    def test_resolve_executor_config_json(self):
        # 組み込み executor は設定ブロック無し → None
        self.assertIsNone(kf.resolve_executor_config_json(self._args(executor="kiro")))
        self.assertIsNone(kf.resolve_executor_config_json(self._args(executor=None)))
        # プラグイン executor は同名ブロックを JSON 化
        js = kf.resolve_executor_config_json(
            self._args(executor="gitlab", gitlab={"repo_url": "u", "conn_label": "c"}))
        self.assertEqual(json.loads(js), {"repo_url": "u", "conn_label": "c"})
        # ブロックが無い/空なら None（親の値を上書きしない判断に使う）
        self.assertIsNone(kf.resolve_executor_config_json(self._args(executor="gitlab", gitlab=None)))
        self.assertIsNone(kf.resolve_executor_config_json(self._args(executor="gitlab", gitlab={})))

    def test_spawn_worker_passes_executor_config_env(self):
        # daemon が解決した gitlab ブロックが worker 起動 env に KIRO_FLOW_EXECUTOR_CONFIG として載る
        args = self._args(executor="gitlab", model=None, poll=1.0,
                          gitlab={"repo_url": "https://gitlab.example/group/repo"})
        captured = {}

        def fake_popen(cmd, *a, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env")
            return object()

        with mock.patch.object(kf.subprocess, "Popen", side_effect=fake_popen):
            kf._spawn_worker(["kiro-flow", "--bus", "b"], args, "run-1", "worker-1")
        self.assertEqual(json.loads(captured["env"]["KIRO_FLOW_EXECUTOR_CONFIG"]),
                         {"repo_url": "https://gitlab.example/group/repo"})
        self.assertIn("work", captured["cmd"])

    def test_spawn_worker_builtin_executor_no_config_env(self):
        # 組み込み executor では設定 env を上書きしない（既存 env をそのまま継承）
        args = self._args(executor="kiro", model=None, poll=1.0)
        captured = {}

        def fake_popen(cmd, *a, **kw):
            captured["env"] = kw.get("env")
            return object()

        prev = os.environ.get("KIRO_FLOW_EXECUTOR_CONFIG")
        os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None)
        self.addCleanup(lambda: os.environ.__setitem__("KIRO_FLOW_EXECUTOR_CONFIG", prev)
                        if prev is not None else os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None))
        with mock.patch.object(kf.subprocess, "Popen", side_effect=fake_popen):
            kf._spawn_worker(["kiro-flow"], args, "run-1", "worker-1")
        self.assertNotIn("KIRO_FLOW_EXECUTOR_CONFIG", captured["env"])

    def test_unresolvable_executor_exits(self):
        with self.assertRaises(SystemExit):
            kf.make_executor(self._args(executor="does-not-exist-xyz"))

    def test_plugin_missing_execute_exits(self):
        # execute() を持たないダミープラグインを一時生成して読み込ませる
        d = tempfile.mkdtemp(prefix="kf-exec-")
        try:
            p = pathlib.Path(d) / "noexec.py"
            p.write_text("X = 1\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                kf.make_executor(self._args(executor=str(p)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_module_cache_reloads_on_mtime_change(self):
        d = tempfile.mkdtemp(prefix="kf-exec-")
        try:
            p = pathlib.Path(d) / "p.py"
            p.write_text("VALUE = 1\ndef execute(*a, **k):\n    return ('', None)\n",
                         encoding="utf-8")
            m1 = kf._load_executor_module(str(p))
            self.assertEqual(m1.VALUE, 1)
            # mtime を進めて内容を変える → 再ロードされること
            time.sleep(0.01)
            p.write_text("VALUE = 2\ndef execute(*a, **k):\n    return ('', None)\n",
                         encoding="utf-8")
            os.utime(p, (time.time() + 5, time.time() + 5))
            m2 = kf._load_executor_module(str(p))
            self.assertEqual(m2.VALUE, 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class GraphHealthTests(unittest.TestCase):
    def test_unknown_deps_dropped(self):
        nodes = {"a": {"id": "a", "goal": "", "deps": ["ghost"], "kind": "work"},
                 "b": {"id": "b", "goal": "", "deps": ["a"], "kind": "work"}}
        kf._sanitize_graph(nodes)
        self.assertEqual(nodes["a"]["deps"], [])      # 未知 ghost を除去
        self.assertEqual(nodes["b"]["deps"], ["a"])   # 正当な依存は保持

    def test_cycle_broken(self):
        nodes = {"a": {"id": "a", "goal": "", "deps": ["b"], "kind": "work"},
                 "b": {"id": "b", "goal": "", "deps": ["a"], "kind": "work"}}
        kf._sanitize_graph(nodes)
        # 循環が断ち切られ、トポロジカル順が成立する（少なくとも片方の deps が空）
        self.assertTrue(nodes["a"]["deps"] == [] or nodes["b"]["deps"] == [])

    def test_self_loop_dropped(self):
        nodes = {"a": {"id": "a", "goal": "", "deps": ["a"], "kind": "work"}}
        kf._sanitize_graph(nodes)
        self.assertEqual(nodes["a"]["deps"], [])


class ReviewGateTests(unittest.TestCase):
    def test_fanout_inserts_gate_before_synthesize(self):
        strat, tasks = kf.plan_strategy_stub("A; B; C", review=True)
        by = {t["id"]: t for t in tasks}
        self.assertIn("gate", by)
        self.assertEqual(by["gate"]["kind"], "verify")
        self.assertIn("gate", by["synth"]["deps"])        # 統合は gate を待つ
        self.assertIn("t1", by["synth"]["deps"])          # 統合は成果も集約する
        self.assertIn("adversarial-verification", strat["patterns"])

    def test_map_reduce_gate_between_map_and_reduce(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["x", "y"]}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, max_fanout=50, review=True)
        by = {t["id"]: t for t in new}
        self.assertIn("s-gate", by)
        self.assertEqual(by["s-gate"]["kind"], "verify")
        self.assertIn("s-gate", by["s-reduce"]["deps"])    # reduce は gate を待つ
        self.assertIn("s-m1", by["s-reduce"]["deps"])      # reduce は map 成果を集約
        self.assertEqual(by["s-gate"]["deps"], ["s-m1", "s-m2"])


class ContinuationTests(unittest.TestCase):
    def test_replan_retries_failed_once(self):
        nodes = {"t1": {"goal": "ok", "deps": [], "kind": "work"},
                 "t2": {"goal": "FAIL bad", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done"}, "t2": {"status": "failed"}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new], ["t2r"])
        self.assertNotIn("FAIL", new[0]["goal"])  # retry のゴールは修正済み

    def test_no_replan_when_all_done(self):
        nodes = {"t1": {"goal": "ok", "deps": [], "kind": "work"}}
        decision, new, _ = kf.continue_stub("req", nodes, {"t1": {"status": "done"}}, 0)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])

    def test_classify_routes_to_specialist(self):
        nodes = {"classify": {"goal": "分類: backend のバグ", "deps": [], "kind": "classify"}}
        results = {"classify": {"status": "done", "output": "class=backend"}}
        decision, new, _ = kf.continue_stub("backend のバグ", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual(new[0]["id"], "classify-act")
        self.assertIn("backend", new[0]["goal"])
        self.assertEqual(new[0]["deps"], ["classify"])

    def test_verify_fail_triggers_regen_and_recheck(self):
        nodes = {"gen1": {"goal": "FLAKY work", "deps": [], "kind": "generate"},
                 "verify1": {"goal": "検証", "deps": ["gen1"], "kind": "verify"}}
        results = {"gen1": {"status": "done", "output": "[stub] 未完(issue)"},
                   "verify1": {"status": "done", "output": "verify=fail"}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        ids = [t["id"] for t in new]
        self.assertIn("gen1-r1", ids)     # 作り直し
        self.assertIn("verify1-r1", ids)  # 再検証
        self.assertNotIn("FLAKY", next(t for t in new if t["id"] == "gen1-r1")["goal"])


class PatternStrategyTests(unittest.TestCase):
    def test_pattern_detection(self):
        cases = {
            "バグを分類して振り分けて": "classify-and-act",
            "3案を比較して最良を選ぶ tournament": "tournament",
            "候補を出してフィルタ": "generate-and-filter",
            "成果をレビューして検証": "adversarial-verification",
            "テストが通るまで繰り返す": "loop-until-done",
            "資料を3観点でまとめる": "fan-out-and-synthesize",
        }
        for req, want in cases.items():
            self.assertEqual(kf._detect_pattern(req), want, req)

    def test_parallelism_extraction(self):
        self.assertEqual(kf._parallelism("候補を x4 出す", 2), 4)
        self.assertEqual(kf._parallelism("並列5で", 2), 5)
        self.assertEqual(kf._parallelism("ふつうの要求", 3), 3)

    def test_fanout_graph_has_synthesize_over_parallel(self):
        # 既定（auto）では集約パターンに検証 gate が入るため、純粋な構造は --no-review で確認
        strat, tasks = kf.plan_strategy_stub("A; B; C", review=False)
        self.assertEqual(strat["patterns"], ["fan-out-and-synthesize"])
        self.assertFalse(strat["review"])
        synth = [t for t in tasks if t["kind"] == "synthesize"]
        self.assertEqual(len(synth), 1)
        # 統合ノードは全並列ノードに依存
        gens = [t["id"] for t in tasks if t["kind"] != "synthesize"]
        self.assertEqual(sorted(synth[0]["deps"]), sorted(gens))

    def test_aggregating_pattern_auto_enables_review(self):
        # 公式準拠: 集約パターンは既定で検証 gate を自動挿入する
        strat, tasks = kf.plan_strategy_stub("A; B; C")  # fan-out-and-synthesize
        self.assertTrue(strat["review"])
        self.assertIn("verify", [t["kind"] for t in tasks])

    def test_non_aggregating_pattern_no_auto_review(self):
        # 集約点を持たない（または内包する）パターンは auto では gate を足さない
        strat, _ = kf.plan_strategy_stub("バグを分類して振り分けて")  # classify-and-act
        self.assertFalse(strat["review"])

    def test_explicit_no_review_overrides_auto(self):
        strat, _ = kf.plan_strategy_stub("ファイルをそれぞれ処理して集約", review=False)
        self.assertFalse(strat["review"])

    def test_tournament_graph_has_judge(self):
        strat, tasks = kf.plan_strategy_stub("最良案を選ぶ tournament x3")
        self.assertEqual(strat["patterns"], ["tournament"])
        self.assertEqual(strat["parallelism"], 3)
        self.assertEqual(len([t for t in tasks if t["kind"] == "generate"]), 3)
        self.assertEqual(len([t for t in tasks if t["kind"] == "judge"]), 1)


class GranularityTests(unittest.TestCase):
    def test_factor_levels(self):
        self.assertEqual(kf.granularity_factor("coarse"), 1)
        self.assertEqual(kf.granularity_factor("fine"), 2)
        self.assertEqual(kf.granularity_factor("finest"), 3)
        self.assertEqual(kf.granularity_factor(None), 3)        # 既定は最も細かい
        self.assertEqual(kf.granularity_factor("unknown"), 3)

    def test_directive_empty_for_coarse(self):
        self.assertEqual(kf.granularity_directive("coarse"), "")
        self.assertIn("細か", kf.granularity_directive("fine"))
        self.assertIn("細か", kf.granularity_directive("finest"))

    def test_stub_scales_node_count_by_granularity(self):
        # 同じ要求でも粒度が細かいほど並列ノードが増える（明示並列が無い場合）。
        # plan_stub は単一セグメントで乱数を使うため、同一 base になるよう seed を固定する
        import random
        req = "最良案を選ぶ tournament"            # generate ノード数 = parallelism

        def plan(g):
            random.seed(0)
            return kf.plan_strategy_stub(req, granularity=g)

        coarse, ctasks = plan("coarse")
        fine, _ = plan("fine")
        finest, ftasks = plan("finest")
        self.assertLess(coarse["parallelism"], fine["parallelism"])
        self.assertLess(fine["parallelism"], finest["parallelism"])
        self.assertEqual(fine["parallelism"], coarse["parallelism"] * 2)
        self.assertEqual(finest["parallelism"], coarse["parallelism"] * 3)
        gens = lambda ts: len([t for t in ts if t["kind"] == "generate"])
        self.assertGreater(gens(ftasks), gens(ctasks))           # 細かいほどノードが多い

    def test_explicit_parallelism_not_scaled(self):
        # 要求に "x3" 等の明示があれば粒度倍率は効かせない（ユーザ指定を尊重）
        strat, _ = kf.plan_strategy_stub("案を出して選ぶ tournament x3", granularity="finest")
        self.assertEqual(strat["parallelism"], 3)

    def test_scale_parallelism_caps_at_16(self):
        self.assertEqual(kf.scale_parallelism(6, "finest"), 16)   # 6*3=18 → 16 にクランプ


class DaemonPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-daemon-")
        self.bus = kf.Bus(self.tmp, "_")

    def test_submit_and_inbox(self):
        self.bus.submit_request("req1", "do things", "tester")
        self.assertIn("req1", self.bus.list_inbox())
        self.assertEqual(self.bus.read_inbox("req1")["request"], "do things")

    def test_claim_request_single_winner(self):
        self.bus.submit_request("req1", "x", "t")
        a = self.bus.claim_request("req1", "daemonA", 60)
        b = self.bus.claim_request("req1", "daemonB", 60)
        self.assertNotEqual(a, b)            # ちょうど 1 台が勝つ
        self.assertTrue(a and not b)         # 先に claim した A が勝者

    def test_claim_request_false_if_run_exists(self):
        self.bus.submit_request("req1", "x", "t")
        kf.Bus(self.tmp, "req1").ensure_run("x")  # 既に run が作られている
        self.assertFalse(self.bus.claim_request("req1", "daemonC", 60))

    def test_workspace_roundtrip_via_meta(self):
        # ワークスペース（唯一の書込先）は run meta に載り、submit→inbox でも伝搬する
        ws = {"url": "https://x/a.git", "path": "apps/api", "base": "main", "target": "develop"}
        b = kf.Bus(self.tmp, "runR")
        b.ensure_run("goal", workspace=ws)
        self.assertEqual(b.run_workspace(), ws)
        self.bus.submit_request("reqR", "do", "t", workspace={"url": "https://x/c.git"})
        self.assertEqual(self.bus.read_inbox("reqR")["workspace"], {"url": "https://x/c.git"})
        # ワークスペース無し → 読み取り専用 run（None）
        b2 = kf.Bus(self.tmp, "runRO")
        b2.ensure_run("just investigate")
        self.assertIsNone(b2.run_workspace())

    def test_references_roundtrip_via_meta(self):
        # 参照リポジトリ（読むだけ）は run meta に載り、submit→inbox でも伝搬する
        refs = kf.parse_references(["https://x/spec.git",
                                    '{"url":"https://x/lib.git","path":"src","desc":"lib"}'])
        self.assertEqual([r["url"] for r in refs], ["https://x/spec.git", "https://x/lib.git"])
        b = kf.Bus(self.tmp, "runRef")
        b.ensure_run("goal", workspace={"url": "https://x/app.git"}, references=refs)
        self.assertEqual(b.run_references(), refs)
        self.bus.submit_request("reqRef", "do", "t", references=refs)
        self.assertEqual(self.bus.read_inbox("reqRef")["references"], refs)
        # 参照節の指示文（エージェント向け）に url が載る
        self.assertIn("https://x/lib.git", kf.reference_instruction(refs))
        self.assertEqual(kf.reference_instruction([]), "")

    def _make_remote(self, name="remote_repo", base="main", subfile=None):
        """ローカルの『リモート』を git init で用意する（push 先になる非 bare リポジトリ）。"""
        remote = os.path.join(self.tmp, name)
        os.makedirs(remote)
        for cmd in (["git", "init", "-q", "-b", base, remote],
                    ["git", "-C", remote, "config", "user.email", "t@t"],
                    ["git", "-C", remote, "config", "user.name", "t"]):
            subprocess.run(cmd, check=True)
        target = os.path.join(remote, subfile) if subfile else os.path.join(remote, "f.txt")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w").close()
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "init"], check=True)
        return remote

    def test_clone_repo_retries_then_succeeds(self):
        # 委譲される側（実作業ノード）のワークスペース clone も一過性障害でリトライして成功する。
        remote = self._make_remote()
        dest = os.path.join(self.dir if hasattr(self, "dir") else tempfile.mkdtemp(), "ws-clone")
        real_run = subprocess.run
        calls = {"n": 0}

        def flaky(cmd, *a, **kw):
            # 1 パス目（`-b main` とフォールバックの 2 呼び出し）を両方失敗させ、2 パス目で成功させる
            if isinstance(cmd, list) and cmd[:2] == ["git", "clone"]:
                calls["n"] += 1
                if calls["n"] <= 2:
                    return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access")
            return real_run(cmd, *a, **kw)

        slept = []
        with mock.patch.object(kf.subprocess, "run", side_effect=flaky), \
             mock.patch.object(kf.time, "sleep", side_effect=lambda s: slept.append(s)):
            out = kf._clone_repo(remote, "main", dest)
        self.assertEqual(out, dest)                       # 最終的に成功
        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))
        self.assertEqual(slept, [1])                      # 1 パス目失敗 → バックオフ → 2 パス目で成功

    def test_clone_repo_gives_up_after_retries(self):
        # ずっと失敗するなら CLONE_RETRIES 回試して "" を返す（呼び出し側が clone 失敗を検知できる）。
        def always_fail(cmd, *a, **kw):
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access")

        with mock.patch.object(kf.subprocess, "run", side_effect=always_fail), \
             mock.patch.object(kf.time, "sleep", side_effect=lambda s: None):
            out = kf._clone_repo("https://x/y.git", "main", os.path.join(tempfile.mkdtemp(), "d"))
        self.assertEqual(out, "")

    def test_ensure_workspace_clone_creates_run_branch(self):
        # 作業ツリーが用意され、作業ブランチ名 kf/<run-id> がエージェントへ渡る。
        # 共有 cache 経由では detached worktree（.git はファイル）で、実ブランチは push 時に作る。
        remote = self._make_remote()
        try:
            ws = kf.ensure_workspace_clone({"url": remote, "base": "main"}, "run-1")
            path = ws["clone"]
            self.assertTrue(path and os.path.exists(os.path.join(path, ".git")))  # worktree=ファイル/clone=dir
            self.assertEqual(ws["branch"], "kf/run-1")
            inside = subprocess.run(["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
                                    capture_output=True, text=True).stdout.strip()
            self.assertEqual(inside, "true")               # git 作業ツリーである
            head = subprocess.run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            self.assertEqual(head, "HEAD")                 # detached（ブランチ二重 checkout 制約を受けない）
            self.assertIn(path, kf.workspace_instruction(ws))
        finally:
            kf.cleanup_workspace()
        self.assertFalse(path and os.path.exists(path))    # 作業後に消える（クリーン必須）

    def test_finalize_workspace_commits_and_pushes_changes(self):
        # エージェントが編集 → finalize が作業ブランチへ commit して push する
        remote = self._make_remote(name="ws_push")
        try:
            ws = kf.ensure_workspace_clone({"url": remote, "base": "main", "target": "main"},
                                           "run-2")
            with open(os.path.join(ws["clone"], "new.txt"), "w") as fh:
                fh.write("change")
            delivery = kf.finalize_workspace(ws, "run-2", "t1")
            self.assertIsNotNone(delivery)
            self.assertEqual(delivery["branch"], "kf/run-2")
            self.assertTrue(delivery["commit"])
            # リモートに作業ブランチが反映され、変更が含まれる
            ls = subprocess.run(["git", "-C", remote, "rev-parse", "--verify", "kf/run-2"],
                                capture_output=True, text=True)
            self.assertEqual(ls.returncode, 0)
            files = subprocess.run(["git", "-C", remote, "ls-tree", "-r", "--name-only", "kf/run-2"],
                                   capture_output=True, text=True).stdout
            self.assertIn("new.txt", files)
        finally:
            kf.cleanup_workspace()

    def test_finalize_workspace_noop_when_no_changes(self):
        # 調査タスク等（変更ゼロ）はブランチを push しない＝読み取り専用グラフでは何もしない
        remote = self._make_remote(name="ws_noop")
        try:
            ws = kf.ensure_workspace_clone({"url": remote, "base": "main"}, "run-3")
            self.assertIsNone(kf.finalize_workspace(ws, "run-3", "t1"))  # 変更なし → None
            ls = subprocess.run(["git", "-C", remote, "rev-parse", "--verify", "kf/run-3"],
                                capture_output=True, text=True)
            self.assertNotEqual(ls.returncode, 0)         # 作業ブランチは push されない
        finally:
            kf.cleanup_workspace()

    def test_ensure_workspace_clone_checks_out_base_content(self):
        # base 指定があればそのブランチ内容から作業ブランチを作る（base の成果物が見える）
        remote = self._make_remote(name="branched_repo", base="main")
        subprocess.run(["git", "-C", remote, "checkout", "-q", "-b", "develop"], check=True)
        open(os.path.join(remote, "dev.txt"), "w").close()
        subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
        subprocess.run(["git", "-C", remote, "commit", "-qm", "dev"], check=True)
        ws_spec = {"url": remote, "base": "develop", "path": "apps/api",
                   "target": "main", "desc": "API"}
        try:
            ws = kf.ensure_workspace_clone(ws_spec, "run-4")
            path = ws["clone"]
            self.assertTrue(os.path.exists(os.path.join(path, "dev.txt")))   # develop の内容
            instr = kf.workspace_instruction(ws)
            self.assertIn("apps/api", instr)               # path（モノレポのフォルダ）
            self.assertIn("kf/run-4", instr)               # 作業ブランチ
            self.assertIn("main", instr)                   # target（MR/PR ターゲット）
        finally:
            kf.cleanup_workspace()

    def test_provision_tree_reuses_shared_mirror(self):
        # 共有 cache: 2 回 provision してもミラー clone は 1 回きり（再 clone しない＝負荷削減の本体）。
        remote = self._make_remote(name="cache_reuse")
        cache_dir = os.path.join(self.tmp, "gitcache")
        prev = os.environ.get("KIRO_GIT_CACHE_DIR")
        os.environ["KIRO_GIT_CACHE_DIR"] = cache_dir
        mirror_calls = {"n": 0}
        real_clone = kf._mirror_clone

        def counting(url, cache):
            mirror_calls["n"] += 1
            return real_clone(url, cache)

        try:
            with mock.patch.object(kf, "_mirror_clone", side_effect=counting):
                d1 = os.path.join(self.tmp, "wt1")
                d2 = os.path.join(self.tmp, "wt2")
                p1 = kf.provision_tree(remote, ["main"], d1)
                p2 = kf.provision_tree(remote, ["main"], d2)
            self.assertEqual(p1, d1)
            self.assertEqual(p2, d2)
            self.assertEqual(mirror_calls["n"], 1)         # ミラーは初回 1 回のみ
            # 2 つの worktree は同じ共有ミラーに登録される
            self.assertTrue(os.path.exists(os.path.join(d1, ".git")))
            self.assertTrue(os.path.exists(os.path.join(d2, ".git")))
        finally:
            kf._prune_caches(kf._provisioned_urls)
            kf._provisioned_urls.clear()
            if prev is None:
                os.environ.pop("KIRO_GIT_CACHE_DIR", None)
            else:
                os.environ["KIRO_GIT_CACHE_DIR"] = prev

    def test_provision_tree_reflects_latest_after_fetch(self):
        # INV-1 鮮度: ミラーは再利用しつつ、provision のたびに fetch して最新コミットで worktree を作る。
        remote = self._make_remote(name="cache_fresh")
        cache_dir = os.path.join(self.tmp, "gitcache2")
        prev = os.environ.get("KIRO_GIT_CACHE_DIR")
        os.environ["KIRO_GIT_CACHE_DIR"] = cache_dir
        try:
            d1 = os.path.join(self.tmp, "f1")
            self.assertEqual(kf.provision_tree(remote, ["main"], d1), d1)
            self.assertFalse(os.path.exists(os.path.join(d1, "added.txt")))
            # リモートに新コミットを追加 → 次の provision はそれを反映する
            open(os.path.join(remote, "added.txt"), "w").close()
            subprocess.run(["git", "-C", remote, "add", "-A"], check=True)
            subprocess.run(["git", "-C", remote, "commit", "-qm", "add"], check=True)
            d2 = os.path.join(self.tmp, "f2")
            self.assertEqual(kf.provision_tree(remote, ["main"], d2), d2)
            self.assertTrue(os.path.exists(os.path.join(d2, "added.txt")))   # 最新が見える
        finally:
            kf._prune_caches(kf._provisioned_urls)
            kf._provisioned_urls.clear()
            if prev is None:
                os.environ.pop("KIRO_GIT_CACHE_DIR", None)
            else:
                os.environ["KIRO_GIT_CACHE_DIR"] = prev

    def test_provision_tree_falls_back_to_direct_clone(self):
        # INV-3: cache が使えない（ミラー作成不可）ときは従来の direct clone に倒れて作業を継続する。
        remote = self._make_remote(name="cache_fallback")
        cache_dir = os.path.join(self.tmp, "gitcache3")
        prev = os.environ.get("KIRO_GIT_CACHE_DIR")
        os.environ["KIRO_GIT_CACHE_DIR"] = cache_dir
        try:
            dest = os.path.join(self.tmp, "fb")
            with mock.patch.object(kf, "ensure_cache", return_value=None):
                out = kf.provision_tree(remote, ["main"], dest)
            self.assertEqual(out, dest)
            self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))   # direct clone（.git はディレクトリ）
        finally:
            kf._prune_caches(kf._provisioned_urls)
            kf._provisioned_urls.clear()
            if prev is None:
                os.environ.pop("KIRO_GIT_CACHE_DIR", None)
            else:
                os.environ["KIRO_GIT_CACHE_DIR"] = prev

    def test_parse_workspace_url_or_json(self):
        # 素の URL は url だけの spec。JSON は構造化メタを受ける。空は None（読み取り専用）
        u = kf.parse_workspace("https://git/app.git")
        self.assertEqual((u["url"], u["path"], u["base"]), ("https://git/app.git", "", ""))
        j = kf.parse_workspace(
            '{"url":"https://git/shop.git","path":"apps/api","base":"main",'
            '"target":"develop","desc":"API"}')
        self.assertEqual((j["path"], j["base"], j["target"], j["desc"]),
                         ("apps/api", "main", "develop", "API"))
        self.assertIsNone(kf.parse_workspace(None))
        self.assertIsNone(kf.parse_workspace(""))

    def test_workspace_id_includes_path_and_base(self):
        # 同 URL でも path（モノレポのフォルダ）や base（作業ブランチ）が違えば別ワークスペース
        a = {"url": "https://git/shop.git", "path": "apps/api", "base": "main"}
        b = {"url": "https://git/shop.git", "path": "apps/web", "base": "main"}
        c = {"url": "https://git/shop.git", "path": "apps/api", "base": "develop"}
        self.assertNotEqual(kf.workspace_id(a), kf.workspace_id(b))   # path 違い
        self.assertNotEqual(kf.workspace_id(a), kf.workspace_id(c))   # base 違い
        self.assertEqual(kf.workspace_id(a), kf.workspace_id(dict(a)))

    def test_sweep_work_repo_dirs_reaps_dead_pid_only(self):
        # SIGKILL リーク回収: 死んだ pid の孤立 clone は消し、生存 pid（自分）のものは残す
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        dead_pid = 2147480000          # 存在しない（はずの）pid
        live = os.path.join(tmp, f"kiro-flow-ws-{os.getpid()}-aaa")
        dead = os.path.join(tmp, f"kiro-flow-ws-{dead_pid}-bbb")
        other = os.path.join(tmp, "unrelated-dir")
        for d in (live, dead, other):
            os.makedirs(d)
        with mock.patch("tempfile.gettempdir", return_value=tmp):
            removed = kf.sweep_work_repo_dirs(min_age_sec=0.0)
        self.assertEqual(removed, 1)
        self.assertTrue(os.path.isdir(live))      # 生存 pid → 残す
        self.assertFalse(os.path.exists(dead))    # 死亡 pid → 回収
        self.assertTrue(os.path.isdir(other))     # 無関係ディレクトリ → 触らない

    def test_sweep_work_repo_dirs_keeps_recent_even_if_pid_unknown(self):
        # min_age 未満かつ pid 生存中は残す（稼働中の clone を誤削除しない）
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        recent = os.path.join(tmp, f"kiro-flow-ws-{os.getpid()}-ccc")
        os.makedirs(recent)
        with mock.patch("tempfile.gettempdir", return_value=tmp):
            self.assertEqual(kf.sweep_work_repo_dirs(min_age_sec=3600.0), 0)
        self.assertTrue(os.path.isdir(recent))

    def test_node_entry_drops_repos(self):
        # ワークスペースは run 単位なので、ノードに repo は持たせない
        e = kf._node_entry({"goal": "g", "deps": [], "kind": "work", "repos": ["a", "b"]})
        self.assertNotIn("repos", e)
        self.assertEqual((e["goal"], e["kind"]), ("g", "work"))

    def test_workspace_instruction_describes_single_writable(self):
        ws = {"url": "https://git/app.git", "path": "apps/api", "base": "develop",
              "target": "main", "desc": "API", "branch": "kf/run-9", "clone": "/tmp/app"}
        instr = kf.workspace_instruction(ws)
        self.assertIn("唯一の書込先", instr)
        self.assertIn("apps/api", instr)
        self.assertIn("kf/run-9", instr)
        self.assertIn("commit と push は kiro-flow", instr)   # エージェントは編集のみ
        # clone 失敗時は書き込めない旨を明示
        self.assertIn("clone に失敗", kf.workspace_instruction({"url": "https://x/y.git"}))
        self.assertEqual(kf.workspace_instruction(None), "")

    def test_remove_run_also_purges_inbox(self):
        # gc（remove_run）は対応する inbox 要求と claim も消す。残すと run_exists が
        # 再び False になり、デーモンが完了済み要求を再実行してしまう（resurrection 防止）。
        self.bus.submit_request("req1", "x", "t")
        self.bus.claim_request("req1", "daemonA", 60)  # inbox/claims/req1 を作る
        kf.Bus(self.tmp, "req1").ensure_run("x")
        self.assertIn("req1", self.bus.list_inbox())
        self.bus.remove_run("req1")
        self.assertNotIn("req1", self.bus.list_inbox())          # inbox 要求が消えた
        self.assertFalse(self.bus.run_exists("req1"))            # run も消えた
        import os
        self.assertFalse(os.path.exists(
            os.path.join(self.bus.inbox_claims_dir, "req1")))   # claim も消えた
        # 消えた後は再 claim 可能にならない（要求自体が無い）
        self.assertEqual(self.bus.list_inbox(), [])

    def test_lock_path_canonical_and_config_dir(self):
        import argparse
        # local キーは realpath で canonical 化 → symlink 経由でも同一ロックパス
        real = os.path.join(self.tmp, "real_bus")
        os.makedirs(real)
        link = os.path.join(self.tmp, "link_bus")
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink 不可")
        a_real = argparse.Namespace(bus=real, git=None, git_branch="main", git_subdir=None, lock_dir=None)
        a_link = argparse.Namespace(bus=link, git=None, git_branch="main", git_subdir=None, lock_dir=None)
        self.assertEqual(kf._daemon_lock_path(a_real), kf._daemon_lock_path(a_link))
        # 設定 lock_dir でロック置き場を共有できる（TMPDIR 差の吸収）
        lockdir = os.path.join(self.tmp, "locks")
        a_cfg = argparse.Namespace(bus=real, git=None, git_branch="main", git_subdir=None, lock_dir=lockdir)
        self.assertEqual(os.path.dirname(kf._daemon_lock_path(a_cfg)), lockdir)

    def test_active_runs_and_claimable_count(self):
        v = kf.Bus(self.tmp, "runA")
        v.ensure_run("req")
        v.write_graph({"nodes": {"t1": {"goal": "a", "deps": []},
                                 "t2": {"goal": "b", "deps": ["t1"]}}, "iteration": 0})
        v.write_task({"id": "t1", "goal": "a", "deps": []})
        v.write_task({"id": "t2", "goal": "b", "deps": ["t1"]})
        v.set_status("running")
        # t1 は claim 可能、t2 は依存未充足なので不可 → count == 1
        self.assertIn("runA", self.bus.active_runs())
        self.assertEqual(self.bus.run_claimable_count("runA"), 1)
        # t1 完了で t2 が解放される
        v.write_result("t1", "w", "done", "o")
        self.assertEqual(self.bus.run_claimable_count("runA"), 1)
        # 終端した run は active_runs から外れる
        v.write_result("t2", "w", "done", "o")
        v.set_status("done")
        self.assertNotIn("runA", self.bus.active_runs())


class EndToEndTests(unittest.TestCase):
    def _run_up(self, bus, request, extra=None, timeout=90):
        cmd = [sys.executable, str(SCRIPT), "--bus", bus, "run", request,
               "--workers", "3", "--planner", "stub", "--executor", "stub", "--poll", "0.2"]
        cmd += extra or []
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p

    def _final(self, bus):
        run_id = sorted(os.listdir(os.path.join(bus, "runs")))[0]
        return kf.read_json(os.path.join(bus, "runs", run_id, "final.json"))

    def test_up_completes_all_tasks_once(self):
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "x; y; z")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        results = final["results"]
        # fan-out-and-synthesize: 並列ノード + 統合ノード（合計 >= 4）がすべて done
        self.assertGreaterEqual(len(results), 4)
        self.assertIn("synth", results)
        for nid, r in results.items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")
            self.assertTrue(r["who"])  # 誰かが実行した
        self.assertIn("fan-out-and-synthesize", final["strategy"]["patterns"])
        # 集約パターンは既定で検証 gate が自動挿入される
        self.assertTrue(final["strategy"]["review"])
        self.assertIn("gate", results)

    def test_up_replan_recovers_failure(self):
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "good; FAIL bad")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        self.assertGreaterEqual(final["iterations"], 1)        # 再計画が回った
        self.assertEqual(final["results"]["t2r"]["status"], "done")  # retry 成功

    def test_up_map_reduce_with_review(self):
        # データ駆動 fan-out（split→map）＋統合前 gate（--review）の複合を end-to-end で
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "ファイルをそれぞれ処理して集約 3件", extra=["--review"])
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        res = final["results"]
        # split → 3 map → gate(verify) → reduce がすべて done
        self.assertEqual(sum(1 for k in res if k.startswith("split1-m")), 3)
        self.assertIn("split1-gate", res)
        self.assertEqual(res["split1-reduce"]["status"], "done")
        self.assertEqual(res["split1-reduce"]["data"]["count"], 3)

    def test_result_command_presents_final_output(self):
        # 完了した run に対し result が最終成果（集約ノード synth）を返す
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "x; y; z")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        run_id = sorted(os.listdir(os.path.join(bus, "runs")))[0]
        rp = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "--run-id", run_id,
             "result", "--json"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(rp.returncode, 0, rp.stderr[-800:])
        out = json.loads(rp.stdout)
        self.assertTrue(out["done"])
        self.assertEqual(out["status"], "done")
        self.assertEqual([n["id"] for n in out["final_nodes"]], ["synth"])
        self.assertTrue(out["final_nodes"][0]["output"])


class DaemonE2ETests(unittest.TestCase):
    """実 daemon プロセスが submit を拾い、orchestrator/worker をオンデマンド起動して run を完走させる黒箱 e2e。

    DaemonPrimitiveTests が bus プリミティブ（submit/claim/inbox）を in-process で検証するのに対し、
    こちらは `daemon` を実プロセスとして常駐させ、`submit` 投入 → final.json 生成まで通す。"""

    def setUp(self):
        self.bus = tempfile.mkdtemp(prefix="kf-daemon-e2e-")
        self.daemon = None

    def tearDown(self):
        if self.daemon and self.daemon.poll() is None:
            self.daemon.terminate()
            try:
                self.daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.daemon.kill()
                self.daemon.wait(timeout=5)
        if self.daemon:
            for s in (self.daemon.stdout, self.daemon.stderr):
                if s:
                    s.close()
        shutil.rmtree(self.bus, ignore_errors=True)

    def _start_daemon(self):
        self.daemon = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--bus", self.bus, "daemon",
             "--max-workers", "3", "--planner", "stub", "--executor", "stub",
             "--poll", "0.2", "--no-cleanup"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _submit(self, request):
        p = subprocess.run([sys.executable, str(SCRIPT), "--bus", self.bus, "submit", request],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return p.stdout.strip().splitlines()[0]   # submit は run-id を標準出力の先頭に出す

    def _wait_final(self, run_id, timeout=90):
        final = os.path.join(self.bus, "runs", run_id, "final.json")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.daemon.poll() is not None:    # daemon が落ちたら即座に失敗（無駄に待たない）
                _, err = self.daemon.communicate()
                self.fail(f"daemon が早期終了 rc={self.daemon.returncode}\n{(err or b'').decode()[-800:]}")
            data = kf.read_json(final) if os.path.exists(final) else None
            if data:                              # final.json は atomic write なので存在＝完成
                return data
            time.sleep(0.3)
        self.fail(f"final.json がタイムアウト({timeout}s)内に現れず: {run_id}")

    def test_daemon_picks_up_submit_and_completes(self):
        self._start_daemon()
        run_id = self._submit("x; y; z")
        final = self._wait_final(run_id)
        results = final["results"]
        # daemon → orchestrator → worker で fan-out-and-synthesize が完走（並列ノード + 統合）
        self.assertGreaterEqual(len(results), 4)
        self.assertIn("synth", results)
        for nid, r in results.items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")
            self.assertTrue(r["who"])             # worker が実行した

    def test_daemon_completes_multiple_submits(self):
        # 1 デーモンが複数要求を並行に受理し、それぞれ独立 run として完走させる
        self._start_daemon()
        r1 = self._submit("a; b")
        r2 = self._submit("c; d")
        for run_id in (r1, r2):
            final = self._wait_final(run_id)
            self.assertIn("synth", final["results"])
            for nid, r in final["results"].items():
                self.assertEqual(r["status"], "done", f"{run_id}/{nid}: {r}")

    def test_daemon_writes_status_json_on_startup(self):
        # cmd_daemon の起動直後の write_daemon_status 呼び出しが実際に配線されていることを、
        # サブプロセスとして起動した実 daemon で確認する（state_git 無しでもローカルに書く）。
        self._start_daemon()
        status = os.path.join(self.bus, "status.json")
        deadline = time.time() + 15
        rec = None
        while time.time() < deadline and rec is None:
            rec = kf.read_json(status) if os.path.exists(status) else None
            if rec is None:
                time.sleep(0.2)
        self.assertIsNotNone(rec, "status.json が起動後に現れませんでした")
        self.assertIn("pid", rec)
        self.assertIn("updated_iso", rec)


class FinalResultNodeTests(unittest.TestCase):
    def test_prefers_aggregation_sink(self):
        nodes = {
            "t1": {"kind": "work", "deps": []},
            "t2": {"kind": "work", "deps": []},
            "synth": {"kind": "synthesize", "deps": ["t1", "t2"]},
        }
        results = {k: {"status": "done"} for k in nodes}
        self.assertEqual(kf._final_result_nodes(nodes, results), ["synth"])

    def test_falls_back_to_sinks_without_agg_kind(self):
        # 末端が work のみ → 集約 kind が無いので末端ノードを返す
        nodes = {
            "a": {"kind": "work", "deps": []},
            "b": {"kind": "work", "deps": ["a"]},
        }
        results = {k: {"status": "done"} for k in nodes}
        self.assertEqual(kf._final_result_nodes(nodes, results), ["b"])

    def test_falls_back_when_agg_node_not_done(self):
        # 集約ノードが未完了なら done の末端へフォールバック
        nodes = {
            "t1": {"kind": "work", "deps": []},
            "synth": {"kind": "synthesize", "deps": ["t1"]},
        }
        results = {"t1": {"status": "done"}, "synth": {"status": "pending"}}
        self.assertEqual(kf._final_result_nodes(nodes, results), ["t1"])

    def test_empty_when_nothing_done(self):
        self.assertEqual(
            kf._final_result_nodes({"t1": {"kind": "work", "deps": []}},
                                   {"t1": {"status": "pending"}}), [])
        self.assertEqual(kf._final_result_nodes({}, {}), [])


class GitDistributedTests(unittest.TestCase):
    """複数 PC 分散の模擬: ローカルのベアリポジトリを共有バスにし、ノードごとに
    独立クローン（= 別 PC 相当）から push/pull させて検証する。git 必須。"""

    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git が無い環境ではスキップ")
        self.root = tempfile.mkdtemp(prefix="kf-git-")
        self.bare = os.path.join(self.root, "bus.git")
        r = subprocess.run(["git", "init", "--bare", "-b", "main", self.bare],
                           capture_output=True, text=True)
        if r.returncode != 0:  # 古い git 向けフォールバック
            subprocess.run(["git", "init", "--bare", self.bare], check=True,
                           capture_output=True)
        self.clones = os.path.join(self.root, "clones")

    def _final_from_bare(self):
        tmp = tempfile.mkdtemp(prefix="kf-read-")
        subprocess.run(["git", "clone", "-q", self.bare, tmp], check=True,
                       capture_output=True)
        runs = os.path.join(tmp, "runs")
        rid = sorted(os.listdir(runs))[0]
        return kf.read_json(os.path.join(runs, rid, "final.json"))

    def test_claim_across_separate_clones_single_winner(self):
        # 別クローン（別 PC 相当）から同じタスクを claim → 勝者は 1 人
        a = kf.GitBus(os.path.join(self.clones, "A"), "run1", remote=self.bare, branch="main")
        b = kf.GitBus(os.path.join(self.clones, "B"), "run1", remote=self.bare, branch="main")
        won_a = a.try_claim("t1", "nodeA", 60)
        won_b = b.try_claim("t1", "nodeB", 60)
        self.assertTrue(won_a)
        self.assertFalse(won_b)
        # 先着の claim が両クローンから見て勝者
        b.sync_pull()
        self.assertEqual(b._winner("t1"), "nodeA")

    def test_request_claim_elects_single_daemon(self):
        # 別クローンの 2 デーモンが同じ要求を claim → orchestrate 担当は 1 台
        a = kf.GitBus(os.path.join(self.clones, "dA"), "_", remote=self.bare, branch="main")
        b = kf.GitBus(os.path.join(self.clones, "dB"), "_", remote=self.bare, branch="main")
        a.submit_request("req1", "do it", "submitter")
        a.sync_push("submit req1")
        b.sync_pull()
        ca = a.claim_request("req1", "daemonA", 60)
        cb = b.claim_request("req1", "daemonB", 60)
        self.assertTrue(ca)
        self.assertFalse(cb)

    def test_run_over_git_bus_completes(self):
        # orchestrator + worker が各自の独立クローンから git バスへ push/pull して完走
        cmd = [sys.executable, str(SCRIPT), "--bus", self.clones, "--git", self.bare,
               "run", "x; y; z", "--planner", "stub", "--executor", "stub",
               "--workers", "3", "--poll", "0.2"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stderr[-1000:])
        final = self._final_from_bare()
        self.assertIsNotNone(final)
        res = final["results"]
        self.assertGreaterEqual(len(res), 4)  # fan-out 並列 + synth
        for nid, r in res.items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")
            self.assertTrue(r["who"])  # 誰か（どこかのクローン）が実行した

    def test_sparse_checkout_limits_worktree(self):
        # 既存リポジトリにバスを間借り（--git-subdir）し、sparse で他を展開しない
        seed = tempfile.mkdtemp(prefix="kf-seed-")
        subprocess.run(["git", "clone", "-q", self.bare, seed], check=True, capture_output=True)
        os.makedirs(os.path.join(seed, "unrelated"))
        with open(os.path.join(seed, "unrelated", "x.txt"), "w") as f:
            f.write("hi")
        for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed"],
                  ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", seed] + c, check=True, capture_output=True)
        clone = os.path.join(self.clones, "sub")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main", subdir="flow")
        entries = set(os.listdir(clone))
        self.assertNotIn("unrelated", entries)  # sparse で無関係ディレクトリは未展開
        self.assertIn(".git", entries)

    def test_cleanup_clone_removes_worktree(self):
        # 作業後にクローン（.git を含む作業ツリー）を丸ごと削除できる。
        clone = os.path.join(self.clones, "to-remove")
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
        bus.cleanup_clone()
        self.assertFalse(os.path.exists(clone))  # クローンごと消える
        bus.cleanup_clone()  # 既に無くても安全（冪等）

    def test_clone_retries_on_transient_network_failure(self):
        # 一過性のネットワーク障害でも、起動時クローンはリトライして成功する（即死しない）。
        clone = os.path.join(self.clones, "flaky")
        real_run = subprocess.run
        calls = {"n": 0}

        def flaky_run(cmd, *a, **kw):
            # 1 回目の試行（filtered + fallback の 2 呼び出し）だけネットワーク障害を模して失敗させ、
            # 2 回目の試行で成功させる（_clone_once は 1 試行で git clone を最大 2 回呼ぶ）。
            if isinstance(cmd, list) and cmd[:2] == ["git", "clone"]:
                calls["n"] += 1
                if calls["n"] <= 2:
                    return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access (timeout)")
            return real_run(cmd, *a, **kw)

        slept = []
        with mock.patch.object(kf.subprocess, "run", side_effect=flaky_run), \
             mock.patch.object(kf.time, "sleep", side_effect=lambda s: slept.append(s)):
            kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))  # 最終的にクローン成功
        self.assertGreaterEqual(calls["n"], 3)                       # 1 試行目が失敗 → 2 試行目で成功
        self.assertEqual(slept, [1])                                 # 試行の合間に 1 回バックオフ（2^0）

    def test_clone_gives_up_after_retries(self):
        # ずっと失敗するなら CLONE_RETRIES 回試して諦め、明示的な RuntimeError を出す。
        clone = os.path.join(self.clones, "dead")
        real_run = subprocess.run

        def always_fail(cmd, *a, **kw):
            if isinstance(cmd, list) and cmd[:2] == ["git", "clone"]:
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access")
            return real_run(cmd, *a, **kw)

        with mock.patch.object(kf.subprocess, "run", side_effect=always_fail), \
             mock.patch.object(kf.time, "sleep", side_effect=lambda s: None):
            with self.assertRaises(RuntimeError) as ctx:
                kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertIn(f"{kf.CLONE_RETRIES} 回失敗", str(ctx.exception))

    def test_clone_inside_parent_repo_does_not_touch_parent(self):
        # クローン先が親リポジトリの作業ツリー配下にあっても、sparse-checkout が親へ波及しない。
        # 親リポジトリを用意し、その配下にバス用クローンを作る。
        parent = tempfile.mkdtemp(prefix="kf-parent-")
        subprocess.run(["git", "-C", parent, "init", "-q"], check=True, capture_output=True)
        for name in ("keepA", "keepB"):
            with open(os.path.join(parent, name), "w") as f:
                f.write("x")
        for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "p"]):
            subprocess.run(["git", "-C", parent] + c, check=True, capture_output=True)
        clone = os.path.join(parent, "bus", "node")     # 親リポジトリの作業ツリー配下
        kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        # クローンは自分自身の .git を持ち、親の作業ツリーは無傷（sparse で隠されない）
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
        self.assertTrue(os.path.exists(os.path.join(parent, "keepA")))
        self.assertTrue(os.path.exists(os.path.join(parent, "keepB")))
        # 親リポジトリに sparse-checkout が設定されていない（cone 化していない）
        cfg = subprocess.run(["git", "-C", parent, "config", "--get", "core.sparseCheckout"],
                             capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(cfg, "true")

    def test_reuse_full_checkout_of_same_remote_is_refused(self):
        # 同一 remote の既存フルチェックアウト（ユーザーの作業リポジトリ等）を --bus に指定しても、
        # sparse-checkout で subdir 以外の追跡ファイルを隠さず、上書きせず中断する。
        seed = tempfile.mkdtemp(prefix="kf-seed-")
        subprocess.run(["git", "clone", "-q", self.bare, seed], check=True, capture_output=True)
        for d in ("flow", "src", "docs"):
            os.makedirs(os.path.join(seed, d))
            with open(os.path.join(seed, d, "f.txt"), "w") as f:
                f.write("x")
        for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "s"],
                  ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", seed] + c, check=True, capture_output=True)
        userwork = tempfile.mkdtemp(prefix="kf-userwork-")
        subprocess.run(["git", "clone", "-q", self.bare, userwork], check=True, capture_output=True)
        before = set(os.listdir(userwork))
        self.assertEqual(before, {".git", "flow", "src", "docs"})
        with self.assertRaises(RuntimeError):
            kf.GitBus(userwork, "run1", remote=self.bare, branch="main", subdir="flow")
        # 追跡ファイルは隠されず、作業ツリーは無傷
        self.assertEqual(set(os.listdir(userwork)), before)
        sparse = subprocess.run(["git", "-C", userwork, "config", "--get", "core.sparseCheckout"],
                                capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(sparse.lower(), "true")

    def test_managed_bus_clone_is_reused(self):
        # 自前で作ったバスクローン（目印つき）は二度目以降そのまま再利用される（中断しない）。
        clone = os.path.join(self.clones, "managed")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main", subdir="flow")
        marker = subprocess.run(["git", "-C", clone, "config", "--get", "kiro-flow.busclone"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(marker, "1")
        kf.GitBus(clone, "run2", remote=self.bare, branch="main", subdir="flow")  # 再利用で例外なし

    def test_stale_index_lock_recovered_on_reuse(self):
        # SIGKILL/電源断が残した古い index.lock は、クローン再利用時に残骸として除去され、
        # 以後の add/commit/push（run 作成の sync_push 相当）が失敗し続けない。
        clone = os.path.join(self.clones, "stale-lock")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        lock = os.path.join(clone, ".git", "index.lock")
        open(lock, "w").close()
        old = time.time() - 3600
        os.utime(lock, (old, old))
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")   # 例外なく再利用
        self.assertFalse(os.path.exists(lock))                            # 残骸は除去済み
        bus.submit_request("req1", "do it", "submitter")
        bus.sync_push("submit req1")                                      # add/commit/push が通る
        self.assertEqual(bus._git(["status", "--porcelain"]).stdout.strip(), "")

    def test_corrupt_index_clone_is_rebuilt(self):
        # ロック除去でも回復できないほど壊れたクローン（index 破損等）は作り直して
        # 自己回復する（バスの真実はリモートにあるため使い捨てで安全）。
        clone = os.path.join(self.clones, "corrupt-index")
        first = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        first.submit_request("req0", "seed", "submitter")
        first.sync_push("seed main")                                      # main を実体化させる
        with open(os.path.join(clone, ".git", "index"), "wb") as f:
            f.write(b"broken")                                            # index を破壊
        with mock.patch.object(kf.time, "sleep", lambda s: None):         # 競合待ちを高速化
            bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        marker = subprocess.run(["git", "-C", clone, "config", "--get", "kiro-flow.busclone"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(marker, "1")                                     # 管理クローンとして再生
        bus.submit_request("req1", "do it", "submitter")
        bus.sync_push("submit req1")                                      # 作り直し後は普通に使える

    def test_lock_going_stale_during_retry_is_removed(self):
        # 実行中に遭遇したロックも、リトライ中に残骸（十分古い）と判明すれば除去して成功する
        # （新しいうちは消さない＝稼働中の他 git を壊さない）。
        clone = os.path.join(self.clones, "aging-lock")
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        lock = os.path.join(clone, ".git", "index.lock")
        open(lock, "w").close()                                           # mtime = 今 → まだ消さない

        def age_lock(_):                                                  # 時間経過の代わりに mtime を過去へ
            old = time.time() - kf.GIT_LOCK_STALE_SEC - 1
            os.utime(lock, (old, old))

        with open(os.path.join(clone, "poke.txt"), "w") as f:
            f.write("x")
        with mock.patch.object(kf.time, "sleep", side_effect=age_lock):
            p = bus._git(["add", "-A"])
        self.assertEqual(p.returncode, 0)
        self.assertFalse(os.path.exists(lock))                            # 残骸化した時点で除去された

    def test_interrupted_rebase_recovered_on_reuse(self):
        # 中断された pull --rebase の残骸（rebase-merge/）があると以後の pull が失敗し続けるため、
        # クローン再利用時に破棄して回復する。
        clone = os.path.join(self.clones, "half-rebase")
        kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        os.makedirs(os.path.join(clone, ".git", "rebase-merge"))
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        self.assertFalse(os.path.isdir(os.path.join(clone, ".git", "rebase-merge")))
        bus.sync_pull()                                                   # pull --rebase が通る

    def test_git_retries_while_live_lock_is_held(self):
        # 稼働中の他 git が保持する新しいロックは消さず、短いバックオフで解放を待って成功する。
        clone = os.path.join(self.clones, "live-lock")
        bus = kf.GitBus(clone, "run1", remote=self.bare, branch="main")
        lock = os.path.join(clone, ".git", "index.lock")
        open(lock, "w").close()
        released = []

        def release(_):
            if os.path.exists(lock):                                      # 相手の git が終わった想定
                os.remove(lock)
                released.append(True)

        with open(os.path.join(clone, "poke.txt"), "w") as f:
            f.write("x")
        with mock.patch.object(kf.time, "sleep", side_effect=release):
            p = bus._git(["add", "-A"])
        self.assertEqual(p.returncode, 0)
        self.assertEqual(released, [True])                                # 1 回待って解放を拾った

    def test_clone_into_foreign_nonempty_dir_is_refused(self):
        # 別リポジトリの非空ディレクトリを誤ってバスのクローン先に指定したら、上書きせず中断する。
        foreign = tempfile.mkdtemp(prefix="kf-foreign-")
        subprocess.run(["git", "-C", foreign, "init", "-q"], check=True, capture_output=True)
        with open(os.path.join(foreign, "important.txt"), "w") as f:
            f.write("do not touch")
        with self.assertRaises(RuntimeError):
            kf.GitBus(foreign, "run1", remote=self.bare, branch="main")
        # 既存ファイルは無傷
        self.assertTrue(os.path.exists(os.path.join(foreign, "important.txt")))

    def test_make_bus_cleanup_removes_active_clones(self):
        # make_bus で作ったクローンは cleanup_active_clones でまとめて削除される。
        kf._active_clones.clear()
        self.addCleanup(kf._active_clones.clear)
        args = mock.Mock(bus=self.clones, run_id="run1", git=self.bare,
                         git_branch="main", git_subdir="")
        bus = kf.make_bus(args, "node-x")
        self.assertIn(bus, kf._active_clones)
        self.assertTrue(os.path.isdir(bus.workdir))
        kf.cleanup_active_clones()
        self.assertFalse(os.path.exists(bus.workdir))
        self.assertEqual(kf._active_clones, [])


class EnsureBusRootTests(unittest.TestCase):
    """起動初回のバスフォルダ作成（git バスでは .gitkeep も置く）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-ensurebus-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_creates_local_bus_root_without_gitkeep(self):
        # ローカルバスは初回に作成され、.gitkeep は置かない（runs/ 等で埋まるため）。
        bus = os.path.join(self.tmp, "newbus")
        args = mock.Mock(bus=bus, git=None)
        kf.ensure_bus_root(args)
        self.assertTrue(os.path.isdir(bus))
        self.assertFalse(os.path.exists(os.path.join(bus, ".gitkeep")))

    def test_creates_git_bus_root_with_gitkeep(self):
        # git バスはクローンが作業後に消えて空になるため、.gitkeep で空フォルダを残す。
        bus = os.path.join(self.tmp, "gitbus")
        args = mock.Mock(bus=bus, git="/some/remote")
        kf.ensure_bus_root(args)
        self.assertTrue(os.path.isdir(bus))
        self.assertTrue(os.path.isfile(os.path.join(bus, ".gitkeep")))

    def test_idempotent_and_preserves_existing(self):
        # 既存フォルダ/.gitkeep は壊さず冪等（中身を上書きしない）。
        bus = os.path.join(self.tmp, "exists")
        os.makedirs(bus)
        keep = os.path.join(bus, ".gitkeep")
        with open(keep, "w") as f:
            f.write("keep-me")
        args = mock.Mock(bus=bus, git="/some/remote")
        kf.ensure_bus_root(args)
        with open(keep) as f:
            self.assertEqual(f.read(), "keep-me")


class CleanupTests(unittest.TestCase):
    """一時ファイルの自動クリーンアップ（A: ロック / B: 中間 .tmp / C: 孤立クローン）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-cleanup-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _old(self, path, age_sec):
        t = time.time() - age_sec
        os.utime(path, (t, t))

    def test_sweep_tmp_dead_pid(self):
        # 死んだ pid の <path>.tmp.<pid> は消す。生存 pid の新しいものは残す。
        d = os.path.join(self.tmp, "runs", "r1")
        os.makedirs(d)
        dead = os.path.join(d, "meta.json.tmp.999999")
        alive = os.path.join(d, "meta.json.tmp.%d" % os.getpid())
        normal = os.path.join(d, "meta.json")
        for p in (dead, alive, normal):
            with open(p, "w") as f:
                f.write("{}")
        removed = kf.sweep_tmp_files(self.tmp, min_age_sec=300.0)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(dead))
        self.assertTrue(os.path.exists(alive))   # 生存 pid かつ新しい → 残す
        self.assertTrue(os.path.exists(normal))  # 中間でない通常ファイルは対象外

    def test_sweep_tmp_old_alive_pid(self):
        # 生存 pid でも min_age を超えて古ければクラッシュ残骸とみなし消す。
        d = os.path.join(self.tmp, "runs")
        os.makedirs(d)
        old = os.path.join(d, "graph.json.tmp.%d" % os.getpid())
        with open(old, "w") as f:
            f.write("{}")
        self._old(old, 600)
        self.assertEqual(kf.sweep_tmp_files(self.tmp, min_age_sec=300.0), 1)
        self.assertFalse(os.path.exists(old))

    def test_sweep_tmp_skips_git_internals(self):
        # .git 配下は走査しない（git の内部一時ファイルに触れない）。
        g = os.path.join(self.tmp, ".git", "objects")
        os.makedirs(g)
        inside = os.path.join(g, "x.tmp.999999")
        with open(inside, "w") as f:
            f.write("x")
        self.assertEqual(kf.sweep_tmp_files(self.tmp), 0)
        self.assertTrue(os.path.exists(inside))

    def test_sweep_lock_unused_old(self):
        # 古くて誰も保持していないロックは消す。新しいロックは残す。
        d = kf._locks_root()
        os.makedirs(d, exist_ok=True)
        old = os.path.join(d, "kf-test-old.lock")
        fresh = os.path.join(d, "kf-test-fresh.lock")
        for p in (old, fresh):
            with open(p, "w") as f:
                f.write("")
        self.addCleanup(lambda: [os.path.exists(p) and os.remove(p) for p in (old, fresh)])
        self._old(old, 7200)
        removed = kf.sweep_lock_files(min_age_sec=3600.0)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))  # 新しい → 残す
        self.assertGreaterEqual(removed, 1)

    @unittest.skipIf(kf.fcntl is None, "flock 非対応環境")
    def test_sweep_lock_held_is_kept(self):
        # 保持中（flock 中）の古いロックは消さない。
        d = kf._locks_root()
        os.makedirs(d, exist_ok=True)
        held = os.path.join(d, "kf-test-held.lock")
        f = open(held, "w")
        self.addCleanup(lambda: (f.close(), os.path.exists(held) and os.remove(held)))
        kf.fcntl.flock(f, kf.fcntl.LOCK_EX)
        self._old(held, 7200)
        kf.sweep_lock_files(min_age_sec=3600.0)
        self.assertTrue(os.path.exists(held))  # 保持中 → 残す

    def test_sweep_clone_dirs(self):
        # .git を持つ古い孤立クローンは消す。新しいクローンと keep 対象・非クローンは残す。
        parent = self.tmp
        for name in ("orchestrator-r1", "daemon-self", "worker-w1"):
            os.makedirs(os.path.join(parent, name, ".git"))
        plain = os.path.join(parent, "runs")  # .git を持たない → 触らない
        os.makedirs(plain)
        self._old(os.path.join(parent, "orchestrator-r1"), 100000)
        self._old(os.path.join(parent, "orchestrator-r1", ".git"), 100000)
        removed = kf.sweep_clone_dirs(parent, keep_basename="daemon-self",
                                      min_age_sec=3600.0)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(os.path.join(parent, "orchestrator-r1")))
        self.assertTrue(os.path.exists(os.path.join(parent, "daemon-self")))  # keep
        self.assertTrue(os.path.exists(os.path.join(parent, "worker-w1")))    # 新しい
        self.assertTrue(os.path.exists(plain))  # 非クローン

    def test_run_cleanup_local_skips_clones(self):
        # ローカルバス（--git なし）では孤立クローン掃除は走らない（クローンが無い）。
        args = mock.Mock(bus=self.tmp, lease=1800.0, git=None, cleanup_age=24.0)
        bus = kf.Bus(self.tmp, "_")
        res = kf.run_cleanup(args, bus)
        self.assertEqual(res["clones"], 0)
        self.assertIn("locks", res)
        self.assertIn("tmp", res)


class ArtifactProtocolTests(unittest.TestCase):
    """中間成果物のファイル参照プロトコル（依存タスクの成果物を決定的パスで受け渡す）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-art-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")

    def test_node_artifact_dir_is_deterministic(self):
        # 同じ node-id なら別 Bus ビューでも同じ run 相対パスを指す（後続が発見できる）
        d1 = self.bus.node_artifact_dir("t1")
        d2 = kf.Bus(self.tmp, "run1").node_artifact_dir("t1")
        self.assertEqual(d1, d2)
        self.assertEqual(os.path.relpath(d1, self.bus.run_dir),
                         os.path.join("artifacts", "t1"))

    def test_ensure_and_list_artifacts(self):
        d = self.bus.ensure_artifact_dir("t1")
        self.assertTrue(os.path.isdir(d))
        with open(os.path.join(d, "out.bin"), "w") as f:
            f.write("payload")
        self.assertEqual(self.bus.list_artifacts("t1"), [os.path.join(d, "out.bin")])
        self.assertEqual(self.bus.list_artifacts("missing"), [])  # 無ければ空

    def test_write_result_records_artifacts(self):
        self.bus.write_result("t1", "w", "done", "out", artifacts=["artifacts/t1/out.bin"])
        self.assertEqual(self.bus.read_result("t1")["artifacts"], ["artifacts/t1/out.bin"])
        # artifacts 無しなら後方互換でキーを足さない
        self.bus.write_result("t2", "w", "done", "out")
        self.assertNotIn("artifacts", self.bus.read_result("t2"))

    def test_artifact_instruction_lists_self_and_deps(self):
        dep_dir = self.bus.ensure_artifact_dir("dep1")
        with open(os.path.join(dep_dir, "data.json"), "w") as f:
            f.write("{}")
        self_dir = self.bus.node_artifact_dir("t2")
        note = kf.artifact_instruction(self_dir, {"dep1": dep_dir})
        self.assertIn(self_dir, note)            # 出力先を案内
        self.assertIn(dep_dir, note)             # 依存の成果物パスを案内
        self.assertIn("data.json", note)         # 依存ディレクトリ内のファイル名も
        # 依存ディレクトリが空（成果物なし）なら依存欄は出さない
        empty = kf.artifact_instruction(self_dir, {"dep1": self.bus.node_artifact_dir("nope")})
        self.assertNotIn("依存タスクの成果物", empty)

    def test_artifact_instruction_empty_when_nothing(self):
        self.assertEqual(kf.artifact_instruction(None, None), "")

    def test_execute_kiro_prompt_references_dep_artifacts_by_path(self):
        # execute_kiro は依存成果物の中身を本文に貼らず、パスを示してファイル参照させる
        dep_dir = self.bus.ensure_artifact_dir("dep1")
        with open(os.path.join(dep_dir, "big.txt"), "w") as f:
            f.write("X" * 100)
        captured = {}

        def fake(prompt, model):
            captured["prompt"] = prompt
            return "ok"

        with mock.patch.object(kf, "run_kiro", side_effect=fake):
            kf.execute_kiro("work", "後続処理", {}, None,
                            self.bus.node_artifact_dir("t2"), {"dep1": dep_dir})
        self.assertIn("中間成果物プロトコル", captured["prompt"])
        self.assertIn(dep_dir, captured["prompt"])
        self.assertIn("big.txt", captured["prompt"])
        self.assertNotIn("X" * 100, captured["prompt"])  # 中身は貼らない（参照のみ）

    def test_worker_records_artifacts_in_result(self):
        # ワーカーが実行中に書いた成果物を result に記録し、後続が発見できる
        bus = self.bus
        bus.write_graph({"nodes": {"t1": {"goal": "g", "deps": [], "kind": "work"}},
                         "iteration": 0})
        bus.write_task({"id": "t1", "goal": "g", "deps": [], "kind": "work"})
        bus.set_status("running")

        def fake_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None):
            with open(os.path.join(art_dir, "result.bin"), "w") as f:
                f.write("done")
            return "ok", None

        args = mock.Mock(bus=self.tmp, run_id="run1", git=None, node_id="w1",
                         executor="stub", model=None, lease=60, poll=0,
                         keep_alive=False, idle_exit=True)
        with mock.patch.object(kf, "execute_stub", side_effect=fake_exec), \
             mock.patch.object(kf, "make_bus", return_value=bus):
            kf.cmd_work(args)
        r = bus.read_result("t1")
        self.assertEqual(r["status"], "done")
        self.assertIn(os.path.join("artifacts", "t1", "result.bin"), r["artifacts"])

    def test_worker_records_exception_data_on_failure(self):
        # executor が例外に載せた構造化データ（gitlab 却下の issue_iid / guidance 等）は
        # 承認と対称に failed result の data として残る（消費側の文字列マッチ依存を無くす）
        bus = self.bus
        bus.write_graph({"nodes": {"t1": {"goal": "g", "deps": [], "kind": "work"}},
                         "iteration": 0})
        bus.write_task({"id": "t1", "goal": "g", "deps": [], "kind": "work"})
        bus.set_status("running")

        def fake_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None):
            err = RuntimeError("[gitlab-reject] 却下されました（未マージクローズ）（u）。やり直し指示: 命名を直す")
            err.data = {"issue_iid": 9, "web_url": "u", "decision": "rejected",
                        "reason": "未マージクローズ", "guidance": "命名を直す", "closed": True}
            raise err

        args = mock.Mock(bus=self.tmp, run_id="run1", git=None, node_id="w1",
                         executor="stub", model=None, lease=60, poll=0,
                         keep_alive=False, idle_exit=True)
        with mock.patch.object(kf, "execute_stub", side_effect=fake_exec), \
             mock.patch.object(kf, "make_bus", return_value=bus):
            kf.cmd_work(args)
        r = bus.read_result("t1")
        self.assertEqual(r["status"], "failed")
        self.assertIn("[gitlab-reject]", r["output"])          # 従来のテキストも維持（後方互換）
        self.assertEqual(r["data"]["decision"], "rejected")
        self.assertEqual(r["data"]["issue_iid"], 9)
        self.assertEqual(r["data"]["guidance"], "命名を直す")


class ArgvLimitTests(unittest.TestCase):
    """大きなプロンプトをコマンドライン長制限で落とさず、一時ファイル参照に切り替える。"""

    def test_argv_limit_from_config(self):
        import argparse
        # 解決済み設定値（argv_limit）はモジュール変数へ確定し、free 関数が参照する
        orig = kf._ARGV_LIMIT
        self.addCleanup(setattr, kf, "_ARGV_LIMIT", orig)
        kf._configure_thresholds(argparse.Namespace(argv_limit=123))
        self.assertEqual(kf._kiro_argv_limit(), 123)
        kf._configure_thresholds(argparse.Namespace(argv_limit=None))  # 未指定は据え置き
        self.assertEqual(kf._kiro_argv_limit(), 123)
        kf._ARGV_LIMIT = 0  # 0/不正は組み込み既定へフォールバック
        self.assertEqual(kf._kiro_argv_limit(), kf.CONFIG_DEFAULTS["argv_limit"])

    def test_argv_limit_resolved_from_config_file(self):
        # 設定ファイルの argv_limit が resolve_config 経由で args に載る（env 非依存）
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "kiro-flow.json")
        with open(cfg, "w") as f:
            json.dump({"argv_limit": 4096}, f)
        args = argparse.Namespace(config=cfg, argv_limit=None)
        kf.resolve_config(args)
        self.assertEqual(args.argv_limit, 4096)

    def test_gitlab_block_resolved_from_config_file(self):
        # 設定ファイルの gitlab: ブロック（repo_url 含む）が args.gitlab に載り、_config_path も確定する。
        # これで --config を渡された worker が repo_url を gl.py へ伝えられる（GL_PROJECT_URL）。
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "kiro-flow.json")
        with open(cfg, "w") as f:
            json.dump({"gitlab": {"repo_url": "https://gitlab.com/grp/repo"}}, f)
        args = argparse.Namespace(config=cfg, gitlab=None)
        kf.resolve_config(args)
        self.assertEqual(args.gitlab.get("repo_url"), "https://gitlab.com/grp/repo")
        self.assertEqual(args._config_path, cfg)
        # make_executor がこの gitlab ブロックを KIRO_FLOW_EXECUTOR_CONFIG へ載せる
        prev = os.environ.get("KIRO_FLOW_EXECUTOR_CONFIG")
        self.addCleanup(lambda: os.environ.__setitem__("KIRO_FLOW_EXECUTOR_CONFIG", prev)
                        if prev is not None else os.environ.pop("KIRO_FLOW_EXECUTOR_CONFIG", None))
        kf.make_executor(argparse.Namespace(executor="gitlab", gitlab=args.gitlab))
        self.assertEqual(json.loads(os.environ["KIRO_FLOW_EXECUTOR_CONFIG"]).get("repo_url"),
                         "https://gitlab.com/grp/repo")

    def test_child_spawn_propagates_config(self):
        # run/daemon が子（orchestrator/worker）へ --config を引き継ぐ。これが無いと worker は設定を
        # 再解決できず gitlab.repo_url が既定（空）になり、起票先が git origin にフォールバックする。
        import argparse
        cfg_dir = tempfile.mkdtemp(prefix="kf-cfg-")
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        cfg = os.path.join(cfg_dir, "kiro-flow.json")
        with open(cfg, "w") as f:
            json.dump({"executor": "gitlab", "gitlab": {"repo_url": "https://gitlab.com/grp/repo"}}, f)
        bus = tempfile.mkdtemp(prefix="kf-bus-")
        self.addCleanup(shutil.rmtree, bus, ignore_errors=True)
        spawned = []

        class _FakePopen:
            def __init__(self, cmd, *a, **k):
                spawned.append(cmd)
            def poll(self): return 0
            def wait(self, *a, **k): return 0
            def terminate(self): pass

        base_args = dict(config=cfg, bus=bus, git=None, git_branch="main", git_subdir=None,
                         lease=30.0, run_id="run-x", workers=1, request="x", planner="stub",
                         executor=None, model=None, poll=0.01, max_iterations=1, max_fanout=4,
                         max_retries=1, review=None, granularity="finest", exemplar_first=False,
                         cleanup_clone=True, repos=None, keep_clone=False)
        args = argparse.Namespace(**base_args)
        kf.resolve_config(args)   # executor=gitlab / gitlab block / _config_path を確定
        with mock.patch.object(kf.subprocess, "Popen", _FakePopen), \
             mock.patch.object(kf, "make_bus", lambda *a, **k: mock.Mock()):
            try:
                kf.cmd_run(args)
            except Exception:
                pass  # bus/poll をモックしているので途中で抜けてよい（spawn コマンドだけ検証）
        self.assertTrue(spawned, "子プロセスが起動されていない")
        for cmd in spawned:
            self.assertIn("--config", cmd)
            self.assertEqual(cmd[cmd.index("--config") + 1], os.path.abspath(cfg))

    def test_small_prompt_passed_inline(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_kiro("短いプロンプト", None)
        self.assertIn("短いプロンプト", seen["cmd"])  # そのまま argv に乗る

    def test_large_prompt_spilled_to_tempfile(self):
        big = "依存成果物" + "X" * 200000  # argv 長制限を超える巨大プロンプト
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            # 退避ファイルへのパスが argv 末尾に入り、実行中はその中身が読めること
            path = cmd[-1].split(": ")[-1]
            seen["spill_path"] = path
            with open(path, encoding="utf-8") as f:
                seen["spill_body"] = f.read()
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_kiro(big, None)
        # 巨大プロンプト本体は argv に乗らない（コマンドライン長制限を回避）
        self.assertNotIn(big, seen["cmd"])
        self.assertLess(len(seen["cmd"][-1]), 500)
        self.assertEqual(seen["spill_body"], big)          # ファイルには全文がある
        self.assertFalse(os.path.exists(seen["spill_path"]))  # 実行後に掃除される


class CircuitBreakerTests(unittest.TestCase):
    """judge/評価役のサーキットブレーカー: 達成不可能な完了条件で無限に再タスクを積まない。"""

    def test_node_entry_preserves_retries(self):
        e = kf._node_entry({"id": "x", "goal": "g", "deps": [], "kind": "verify", "retries": 2})
        self.assertEqual(e["retries"], 2)
        e0 = kf._node_entry({"id": "y", "goal": "g", "deps": [], "kind": "work"})
        self.assertNotIn("retries", e0)  # 0/未指定は持たない（ノイズを足さない）

    def test_verify_fail_increments_retries(self):
        nodes = {"gen1": {"goal": "FLAKY", "deps": [], "kind": "generate"},
                 "v1": {"goal": "検証", "deps": ["gen1"], "kind": "verify"}}
        results = {"gen1": {"status": "done", "output": "issue"},
                   "v1": {"status": "done", "output": "verify=fail"}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, max_retries=3)
        by = {t["id"]: t for t in new}
        self.assertEqual(by["v1-r1"]["retries"], 1)
        self.assertEqual(by["gen1-r1"]["retries"], 1)

    def test_circuit_breaker_stops_verify_retries_at_cap(self):
        # retries が上限に達した verify-fail は作り直しを生成せず done で打ち切る
        nodes = {"gen1": {"goal": "g", "deps": [], "kind": "generate", "retries": 3},
                 "v1": {"goal": "検証", "deps": ["gen1"], "kind": "verify", "retries": 3}}
        results = {"gen1": {"status": "done", "output": "issue"},
                   "v1": {"status": "done", "output": "verify=fail"}}
        decision, new, reason = kf.continue_stub("req", nodes, results, 5, max_retries=3)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])
        self.assertIn("サーキットブレーカー", reason)

    def test_circuit_breaker_stops_failed_task_retries_at_cap(self):
        nodes = {"t2": {"goal": "FAIL", "deps": [], "kind": "work", "retries": 3}}
        results = {"t2": {"status": "failed"}}
        decision, new, reason = kf.continue_stub("req", nodes, results, 5, max_retries=3)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])
        self.assertIn("サーキットブレーカー", reason)

    def test_failed_retry_below_cap_still_retries(self):
        nodes = {"t2": {"goal": "FAIL", "deps": [], "kind": "work", "retries": 1}}
        results = {"t2": {"status": "failed"}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0, max_retries=3)
        self.assertEqual(decision, "replan")
        self.assertEqual(new[0]["id"], "t2r")
        self.assertEqual(new[0]["retries"], 2)

    def test_retry_depth_from_id_chain(self):
        self.assertEqual(kf._retry_depth("gen1", {}), 0)
        self.assertEqual(kf._retry_depth("gen1-r1", {}), 1)
        self.assertEqual(kf._retry_depth("gen1-r1-r2", {}), 2)
        self.assertEqual(kf._retry_depth("x", {"retries": 4}), 4)  # 明示カウンタ優先

    def test_continue_kiro_circuit_breaker_short_circuits(self):
        # 評価役 LLM を呼ぶ前に、上限到達の系統を検知して done で打ち切る（LLM 不要）
        nodes = {"v1-r1-r2-r3": {"goal": "検証", "deps": [], "kind": "verify"}}
        results = {"v1-r1-r2-r3": {"status": "done", "output": "verify=fail"}}
        with mock.patch.object(kf, "run_kiro",
                               side_effect=AssertionError("LLM を呼んではいけない")):
            decision, new, reason = kf.continue_kiro("req", nodes, results, 9, max_retries=3)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])
        self.assertIn("サーキットブレーカー", reason)


class _Args:
    """doctor の決定的チェック/シグナル/cmd が読む args の最小スタブ。"""
    def __init__(self, bus, **kw):
        self.bus = bus
        self.run_id = None
        self.git = None
        self.git_branch = "main"
        self.git_subdir = ""
        self.model = None
        self.executor = "stub"     # 既定は stub（kiro-cli を要求しない）
        self.planner = "stub"
        self.max_iterations = 3
        self.max_retries = 3
        self.lease = 1800.0
        self.argv_limit = 100000
        self.fix = False
        self.json = False
        self.cleanup_clone = True
        for k, v in kw.items():
            setattr(self, k, v)


class DoctorTests(unittest.TestCase):
    """kiro-flow の稼働診断（doctor）: env/config チェック・シグナル・修正・分類・連携用 JSON。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-doc-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_findings_kiro_cli_and_finite_stop(self):
        args = _Args(os.path.join(self.tmp, "bus"), executor="kiro",
                     max_iterations=0, lease=0)
        fs = kf.doctor_env_findings(args, which=lambda _n: None)
        ids = {f["title"]: f for f in fs}
        self.assertTrue(any("kiro-cli" in t for t in ids))          # executor=kiro → env critical
        self.assertTrue(any("max_iterations" in t for t in ids))    # ≤0 → config critical
        self.assertTrue(any("リース" in t for t in ids))             # lease≤0 → config warn
        # バス未作成は ensure-bus アクション付き
        bus = next(f for f in fs if f.get("fix_action") == "ensure-bus")
        self.assertEqual(bus["category"], "config")

    def test_env_findings_clean_when_ready(self):
        bus = os.path.join(self.tmp, "bus")
        os.makedirs(bus)
        args = _Args(bus)                                            # executor=stub・正の閾値
        fs = kf.doctor_env_findings(args, which=lambda n: "/usr/bin/" + n)
        self.assertEqual(fs, [])                                     # 所見なし

    def test_apply_fix_ensure_bus(self):
        bus = os.path.join(self.tmp, "bus")
        args = _Args(bus)
        msg = kf.apply_doctor_fix(args, {"fix_action": "ensure-bus"})
        self.assertTrue(os.path.isdir(bus))
        self.assertIn("作成", msg)

    def test_signals_flag_stuck_and_failed_runs(self):
        bus_root = os.path.join(self.tmp, "bus")
        b = kf.Bus(bus_root, "runOld")
        b.ensure_run("古い要求")
        b.write_graph({"nodes": {"t1": {"goal": "g", "deps": []}}, "iteration": 4})
        b.write_task({"id": "t1", "goal": "g", "deps": []})
        b.write_result("t1", "w1", "failed", "例外: Traceback ...")
        # 滞留判定のため updated_at を十分過去にする
        meta = kf.read_json(b.meta_path)
        meta["status"] = "running"
        meta["updated_at"] = "2000-01-01T00:00:00Z"
        meta["created_at"] = "2000-01-01T00:00:00Z"
        kf.write_json_atomic(b.meta_path, meta)

        sig = kf.collect_doctor_signals(_Args(bus_root))
        self.assertEqual(sig["runs_total"], 1)
        self.assertTrue(any(s["run"] == "runOld" for s in sig["stuck"]))   # 非終端＋高齢→滞留
        self.assertTrue(any(fl["run"] == "runOld" for fl in sig["failed"]))
        self.assertTrue(any("runOld" == e.get("run") for e in sig["errors"]))

    def test_cmd_doctor_json_and_program_routing(self):
        bus_root = os.path.join(self.tmp, "bus")
        os.makedirs(bus_root)
        args = _Args(bus_root, json=True, fix=True)
        filed = []

        def agent(prompt, model):
            if "稼働診断医" in prompt:
                return ('[{"category":"program","severity":"critical",'
                        '"title":"グラフ生成バグ","evidence":"run","fix":"例外"}]')
            filed.append("file")
            return "起票"

        import io
        import contextlib as _ctx
        buf = io.StringIO()
        with _ctx.redirect_stdout(buf):
            rc = kf.cmd_doctor(args, kiro_run=agent, skill_finder=lambda _n: None)
        # スキルが見つからない → 出力のみ（起票しない）
        self.assertEqual(filed, [])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["tool"], "kiro-flow")
        self.assertTrue(any(f["category"] == "program" for f in out["findings"]))
        self.assertEqual(rc, 2)                                     # 未解決 critical program

    def test_cmd_doctor_files_via_gitlab_idd_when_present(self):
        bus_root = os.path.join(self.tmp, "bus")
        os.makedirs(bus_root)
        args = _Args(bus_root, json=True, fix=True)
        skill = os.path.join(self.tmp, "skills", "gitlab-idd")
        os.makedirs(skill)
        filed = []

        def agent(prompt, model):
            if "稼働診断医" in prompt:
                return ('[{"category":"program","severity":"critical",'
                        '"title":"バグ","evidence":"e","fix":"f"}]')
            filed.append("file")
            return "起票しました"

        import io
        import contextlib as _ctx
        with _ctx.redirect_stdout(io.StringIO()):
            rc = kf.cmd_doctor(args, kiro_run=agent, skill_finder=lambda _n: skill)
        self.assertEqual(filed, ["file"])                          # gitlab-idd へ委譲
        self.assertEqual(rc, 0)                                    # 唯一の所見が起票で解消 → healthy


def _make_skill_repo(root: str, tool_subdir: str = "tools/kiro-flow",
                     installer_body: str = None) -> str:
    """temp に「スキルリポジトリ」を作る: main ブランチに tool_subdir/install.sh を持つ git リポジトリ。
    install.sh は --prefix で渡されたディレクトリに marker を書くだけの最小実装。リポジトリ path を返す。"""
    repo = os.path.join(root, "skillrepo")
    td = os.path.join(repo, tool_subdir)
    os.makedirs(td, exist_ok=True)
    other = os.path.join(repo, "tools", "kiro-projects")   # sparse 除外の確認用
    os.makedirs(other, exist_ok=True)
    pathlib.Path(other, "FILE.txt").write_text("unrelated\n")
    body = installer_body or (
        "#!/usr/bin/env bash\nset -e\nPREFIX=\"$HOME/.local/bin\"\n"
        "[ \"$1\" = --prefix ] && PREFIX=\"$2\"\nmkdir -p \"$PREFIX\"\n"
        "echo installed > \"$PREFIX/INSTALLED_MARKER\"\n")
    pathlib.Path(td, "install.sh").write_text(body)
    pathlib.Path(td, "kiro-flow.py").write_text("# tool body\n")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
              ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(c, cwd=repo, env=env, check=True, capture_output=True)
    return repo


def _commit_change(repo: str, relpath: str, content: str = "x\n") -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    p = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    pathlib.Path(p).write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "update"], cwd=repo, env=env,
                   check=True, capture_output=True)


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-update-")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state, exist_ok=True)
        self._old = os.environ.get("KIRO_STATE_HOME")
        os.environ["KIRO_STATE_HOME"] = self.state
        self.repo = _make_skill_repo(self.tmp)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KIRO_STATE_HOME", None)
        else:
            os.environ["KIRO_STATE_HOME"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _args(self, **kw):
        base = dict(update_repo=self.repo, update_branch="main",
                    update_subdir="tools/kiro-flow", update_installer="install.sh",
                    update_check_interval=60.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_remote_branch_sha(self):
        sha = kf.remote_branch_sha(self.repo, "main")
        self.assertTrue(sha and len(sha) >= 7)
        self.assertIsNone(kf.remote_branch_sha("", "main"))
        self.assertIsNone(kf.remote_branch_sha(self.repo, "no-such-branch"))

    def test_check_update_baseline_then_latest(self):
        a = self._args()
        info = kf.check_update(a)              # 初回: ベースライン記録・更新なし
        self.assertTrue(info["enabled"])
        self.assertTrue(info["baseline"])
        self.assertFalse(info["available"])
        info2 = kf.check_update(a)             # 2 回目: 最新
        self.assertFalse(info2["baseline"])
        self.assertFalse(info2["available"])

    def test_check_update_detects_new_commit(self):
        a = self._args()
        kf.check_update(a)                     # ベースライン
        _commit_change(self.repo, "tools/kiro-flow/NEW.txt")
        self.assertTrue(kf.check_update(a)["available"])

    def test_disabled_when_no_repo(self):
        a = self._args(update_repo="")
        self.assertFalse(kf.check_update(a)["enabled"])
        self.assertFalse(kf.maybe_self_update(a, idle=True, state={"last": 0.0}))

    def test_sparse_checkout_only_subdir(self):
        dest = os.path.join(self.tmp, "co", "repo")
        tool_dir = kf.sparse_checkout_tool(self.repo, "main", "tools/kiro-flow", dest)
        self.assertTrue(os.path.isfile(os.path.join(tool_dir, "install.sh")))
        # sparse: 無関係な tools/kiro-projects は作業ツリーに展開されない
        self.assertFalse(os.path.isdir(os.path.join(dest, "tools", "kiro-projects")))

    def test_run_installer(self):
        dest = os.path.join(self.tmp, "co2", "repo")
        tool_dir = kf.sparse_checkout_tool(self.repo, "main", "tools/kiro-flow", dest)
        prefix = os.path.join(self.tmp, "prefix")
        ok, out = kf.run_installer(tool_dir, "install.sh",
                                   runner=lambda c, **k: subprocess.run(
                                       c + ["--prefix", prefix], capture_output=True,
                                       text=True, **k))
        self.assertTrue(ok, out)
        self.assertTrue(os.path.isfile(os.path.join(prefix, "INSTALLED_MARKER")))

    def test_apply_update_records_sha(self):
        a = self._args()
        kf.check_update(a)                     # baseline
        _commit_change(self.repo, "tools/kiro-flow/N2.txt")
        info = kf.check_update(a)
        self.assertTrue(info["available"])
        prefix = os.path.join(self.tmp, "prefix2")

        def runner(c, **k):                    # install.sh だけ --prefix を足す
            cmd = c + ["--prefix", prefix] if c[:1] == ["bash"] else c
            return subprocess.run(cmd, capture_output=True, text=True, **k)
        self.assertTrue(kf.apply_update(a, info, runner=runner))
        self.assertEqual(kf.read_update_state()["applied_sha"], info["remote_sha"])
        self.assertFalse(kf.check_update(a)["available"])   # 適用後は最新

    def test_maybe_self_update_interval_gate(self):
        a = self._args(update_check_interval=3600.0, update_enabled=True)
        st = {"last": time.time()}            # 直近にチェック済み → interval 内は何もしない
        self.assertFalse(kf.maybe_self_update(a, idle=True, state=st))
        # idle でなければチェックしない
        self.assertFalse(kf.maybe_self_update(a, idle=False, state={"last": 0.0}))

    def test_update_enabled_false_disables(self):
        a = self._args(update_enabled=False, update_check_interval=3600.0)
        self.assertFalse(kf.maybe_self_update(a, idle=True, state={"last": 0.0}))

    def test_registry_auto_resolution(self):
        # update_repo 未指定でも skill-registry.json から repo/branch を解決して検出できる
        regdir = os.path.join(self.tmp, "agenthome")
        os.makedirs(regdir, exist_ok=True)
        pathlib.Path(regdir, "skill-registry.json").write_text(json.dumps({
            "version": 7, "install_dir": self.tmp,
            "repositories": [{"name": "origin", "url": self.repo,
                              "branch": "main", "priority": 1}]}))
        old = os.environ.get("KIRO_SKILL_REGISTRY")
        os.environ["KIRO_SKILL_REGISTRY"] = regdir
        try:
            self.assertEqual(kf.registry_update_source()[0], self.repo)
            a = self._args(update_repo="")     # 明示なし → registry から解決
            info = kf.check_update(a)
            self.assertTrue(info["enabled"])
            self.assertEqual(info["repo"], self.repo)
        finally:
            if old is None:
                os.environ.pop("KIRO_SKILL_REGISTRY", None)
            else:
                os.environ["KIRO_SKILL_REGISTRY"] = old

    def test_explicit_repo_overrides_registry(self):
        a = self._args(update_repo="/explicit/path", update_branch="dev")
        self.assertEqual(kf.resolve_update_target(a), ("/explicit/path", "dev"))


class StateGitSyncTests(unittest.TestCase):
    """状態の git 保存・共有（state_git）: ローカルバスのワーク内容（runs/・inbox/）を共有
    リポジトリへ双方向同期する。リモート負荷の律速（interval）・多重コミッタ・3-way 裁定
    （inbox はリモート優先/機械状態はローカル優先）・GitBus 時の無効化を検証する。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="kf-sg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        kf._STATE_GITS.clear()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        # 既定ブランチ名に依存しない: state_git_branch（main）へ HEAD を向けて clone が追従するように
        subprocess.run(["git", "-C", str(self.remote), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True)
        self.bus_root = self.tmp / "bus"

    def _args(self, **kw):
        base = dict(bus=str(self.bus_root), git=None, run_id=None,
                    state_git=str(self.remote), state_git_branch="main",
                    state_git_subdir="kf", state_git_interval=0.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _bus(self, run_id="run1"):
        bus = kf.Bus(str(self.bus_root), run_id)
        bus.ensure_run("test request")
        return bus

    def _other(self, name="other") -> pathlib.Path:
        """「他のプログラム」役: 同一リポジトリを普通に clone して commit/push するクローン。"""
        d = self.tmp / name
        subprocess.run(["git", "clone", "-q", str(self.remote), str(d)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(d), "config", "user.email", "other@test"], check=True)
        subprocess.run(["git", "-C", str(d), "config", "user.name", "other"], check=True)
        return d

    @staticmethod
    def _commit_push(d: pathlib.Path, msg="other"):
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", msg], check=True)
        subprocess.run(["git", "-C", str(d), "push", "-q", "-u", "origin", "main"],
                       check=True, capture_output=True)

    @staticmethod
    def _pull(d: pathlib.Path):
        subprocess.run(["git", "-C", str(d), "pull", "-q", "--rebase", "origin", "main"],
                       check=True, capture_output=True)

    def test_export_pushes_run_state_under_subdir(self):
        bus = self._bus()
        bus.write_task({"id": "T1", "goal": "g", "deps": []})
        kf.state_sync(self._args(), force=True)
        got = self._other("check")
        self.assertTrue((got / "kf" / "runs" / "run1" / "meta.json").exists())
        self.assertTrue((got / "kf" / "runs" / "run1" / "tasks" / "T1.json").exists())

    def test_import_inbox_drop(self):
        self._bus()
        kf.state_sync(self._args(), force=True)                  # 初期化（ブランチ作成）
        other = self._other()
        drop = other / "kf" / "inbox" / "req-9.json"
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_text(json.dumps({"request": "from viewer", "submitter": "viewer"}),
                        encoding="utf-8")
        self._commit_push(other, "viewer: submit")
        kf.state_sync(self._args(), force=True)                  # 投入がローカルバスへ届く
        bus = kf.Bus(str(self.bus_root), "_")
        self.assertIn("req-9", bus.list_inbox())
        self.assertEqual((bus.read_inbox("req-9") or {}).get("request"), "from viewer")

    def test_conflict_inbox_prefers_remote_and_runs_prefer_local(self):
        bus = self._bus()
        os.makedirs(self.bus_root / "inbox", exist_ok=True)
        req = self.bus_root / "inbox" / "req-1.json"
        req.write_text('{"request": "local"}', encoding="utf-8")
        meta = pathlib.Path(bus.meta_path)
        kf.state_sync(self._args(), force=True)
        other = self._other()
        (other / "kf" / "inbox" / "req-1.json").write_text('{"request": "remote"}',
                                                           encoding="utf-8")
        rmeta = other / "kf" / "runs" / "run1" / "meta.json"
        rmeta.write_text('{"status": "remote edit"}', encoding="utf-8")
        self._commit_push(other, "both edited")
        req.write_text('{"request": "local2"}', encoding="utf-8")   # 同時変更を作る
        bus.set_status("running")                                   # 機械状態のローカル変更
        kf.state_sync(self._args(), force=True)
        self.assertEqual(json.loads(req.read_text(encoding="utf-8"))["request"], "remote")
        self.assertEqual((kf.read_json(str(meta)) or {}).get("status"), "running")
        self._pull(other)
        self.assertEqual(json.loads(rmeta.read_text(encoding="utf-8")).get("status"), "running")

    def test_concurrent_committer_is_not_clobbered(self):
        # 他プログラムが（我々の pull の後に）同一リポジトリへ push しても、push 競合を
        # pull --rebase → 再 push で吸収して自分の変更を反映し、相手のコミットも壊さない。
        bus = self._bus()
        args = self._args()
        kf.state_sync(args, force=True)
        sg = kf.state_git_for(args)
        other = self._other()
        (other / "unrelated.txt").write_text("theirs\n", encoding="utf-8")
        self._commit_push(other, "other program commit")
        bus.write_task({"id": "T2", "goal": "g", "deps": []})
        real_git = sg._git
        state = {"skipped": False}

        def no_first_pull(*a, **kw):   # sync 冒頭の pull を 1 回落とし「pull 後に push された」競合を再現
            if a[:2] == ("pull", "--rebase") and not state["skipped"]:
                state["skipped"] = True
                return types.SimpleNamespace(returncode=1, stdout="", stderr="skipped")
            return real_git(*a, **kw)
        with mock.patch.object(sg, "_git", side_effect=no_first_pull):
            kf.state_sync(args, force=True)
        self._pull(other)
        self.assertTrue((other / "unrelated.txt").exists())
        self.assertTrue((other / "kf" / "runs" / "run1" / "tasks" / "T2.json").exists())

    def test_interval_rate_limits_sync(self):
        args = self._args(state_git_interval=3600.0)
        self._bus()
        kf.state_sync(args, force=True)                          # 初回は必ず同期
        other = self._other()
        drop = other / "kf" / "inbox" / "req-5.json"
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_text('{"request": "x"}', encoding="utf-8")
        self._commit_push(other, "drop")
        kf.state_sync(args)                                      # interval 内 → 何もしない（負荷律速）
        self.assertFalse((self.bus_root / "inbox" / "req-5.json").exists())
        kf.state_git_for(args)._last_remote = 0.0                # interval 経過を模擬
        kf.state_sync(args)
        self.assertTrue((self.bus_root / "inbox" / "req-5.json").exists())

    def test_disabled_with_git_bus_or_without_state_git(self):
        self.assertIsNone(kf.state_git_for(self._args(git="https://example/bus.git")))
        self.assertIsNone(kf.state_git_for(self._args(state_git=None)))
        kf.state_sync(self._args(git="https://example/bus.git"), force=True)   # 何もしない
        self.assertFalse((self.bus_root / ".state-git").exists())

    def test_tmp_and_dot_files_excluded(self):
        bus = self._bus()
        with open(os.path.join(bus.run_dir, "meta.json.tmp"), "w", encoding="utf-8") as f:
            f.write("{}")                                        # 書きかけの中間ファイル
        kf.state_sync(self._args(), force=True)
        got = self._other("check")
        self.assertTrue((got / "kf" / "runs" / "run1" / "meta.json").exists())
        self.assertFalse((got / "kf" / "runs" / "run1" / "meta.json.tmp").exists())
        self.assertFalse((got / "kf" / ".state-git").exists())

    def test_dot_prefixed_subdir_works(self):
        # state_git_subdir はドット始まり（.kiro-flow 等）でも同期できる（推奨は非ドット）。
        self._bus()
        kf.state_sync(self._args(state_git_subdir=".kiro-flow"), force=True)
        got = self._other("check")
        self.assertTrue((got / ".kiro-flow" / "runs" / "run1" / "meta.json").exists())

    def test_sync_failure_does_not_kill_caller(self):
        args = self._args(state_git=str(self.tmp / "no-such-remote.git"))
        self._bus()
        kf.state_sync(args, force=True)                          # 不通でも例外を漏らさない
        self.assertFalse((self.bus_root / ".state-git" / ".git").exists())

    def test_deletion_propagates_like_gc(self):
        bus = self._bus()
        kf.state_sync(self._args(), force=True)
        shutil.rmtree(bus.run_dir)                               # gc / remove_run 相当の掃除
        kf.state_sync(self._args(), force=True)
        got = self._other("check")
        self.assertFalse((got / "kf" / "runs" / "run1").exists())


class DaemonStatusHeartbeatTests(unittest.TestCase):
    """daemon の生存信号（status.json）。kiro-projects の write_status/--status-interval と
    同じ考え方: 実イベント（run 終端・生存リース push）時は既存の state_sync/push に相乗り
    （追加 push 無し）、アイドル中の更新は --status-interval（既定 0=無効）が opt-in。
    GitBus（--git）モードでは書かない（sparse-checkout が対象外パスのため）。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="kf-status-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus_root = self.tmp / "bus"

    def _args(self, **kw):
        base = dict(bus=str(self.bus_root), git=None, state_git_interval=300.0,
                    status_interval=0.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _bus(self):
        return kf.Bus(str(self.bus_root), "_")

    def _status_path(self):
        return self.bus_root / "status.json"

    def test_write_daemon_status_content(self):
        bus = self._bus()
        kf.write_daemon_status(self._args(status_interval=60.0), bus, "host-1", {"r1": None}, [1, 2])
        rec = json.loads(self._status_path().read_text(encoding="utf-8"))
        self.assertEqual(rec["node_id"], "host-1")
        self.assertEqual(rec["orchestrators"], 1)
        self.assertEqual(rec["workers"], 2)
        self.assertIn("updated_iso", rec)
        self.assertEqual(rec["fresh_after_sec"], 600.0)   # 2 * state_git_interval

    def test_fresh_after_sec_floor_and_larger_wins(self):
        self.assertEqual(kf._daemon_status_fresh_after_sec(
            self._args(state_git_interval=0.0, status_interval=0.0)), 120.0)   # フロア
        self.assertEqual(kf._daemon_status_fresh_after_sec(
            self._args(state_git_interval=300.0, status_interval=1000.0)), 2000.0)  # 大きい方

    def test_write_daemon_status_noop_in_gitbus_mode(self):
        bus = self._bus()
        kf.write_daemon_status(self._args(git="https://example/bus.git"), bus, "host-1", {}, [])
        self.assertFalse(self._status_path().exists())

    def test_maybe_heartbeat_disabled_by_default_touches_nothing(self):
        bus = self._bus()
        kf.maybe_heartbeat_daemon_status(self._args(status_interval=0.0), bus, "host-1", {}, [])
        self.assertFalse(self._status_path().exists())

    def test_maybe_heartbeat_enabled_throttles_to_interval(self):
        bus = self._bus()
        args = self._args(status_interval=100.0)
        kf.maybe_heartbeat_daemon_status(args, bus, "host-1", {}, [])   # 未作成 → 書く
        self.assertTrue(self._status_path().exists())
        first_mtime = self._status_path().stat().st_mtime
        kf.maybe_heartbeat_daemon_status(args, bus, "host-1", {}, [])   # 直後の再呼び出しは間隔未満
        self.assertEqual(self._status_path().stat().st_mtime, first_mtime)
        old = time.time() - 101.0
        os.utime(self._status_path(), (old, old))                       # 間隔経過を模擬
        kf.maybe_heartbeat_daemon_status(args, bus, "host-1", {}, [])
        self.assertGreater(self._status_path().stat().st_mtime, old)

    def test_maybe_heartbeat_noop_in_gitbus_mode(self):
        bus = self._bus()
        kf.maybe_heartbeat_daemon_status(
            self._args(git="https://example/bus.git", status_interval=1.0), bus, "host-1", {}, [])
        self.assertFalse(self._status_path().exists())


class DaemonStatusStateGitSyncTests(unittest.TestCase):
    """status.json は StateGit._scan() がバス全体を走査するため、既存の state_git 機構へ
    追加設定なしで乗る（GitBus 側のような sparse-checkout の拡張は不要）ことを確認する。
    StateGitSyncTests と同じ道具立て（bare remote + 別クローンで検証）。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="kf-status-sg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        kf._STATE_GITS.clear()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        subprocess.run(["git", "-C", str(self.remote), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True)
        self.bus_root = self.tmp / "bus"

    def _args(self, **kw):
        base = dict(bus=str(self.bus_root), git=None, run_id=None,
                    state_git=str(self.remote), state_git_branch="main",
                    state_git_subdir="kf", state_git_interval=0.0)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def _other(self, name="other") -> pathlib.Path:
        d = self.tmp / name
        subprocess.run(["git", "clone", "-q", str(self.remote), str(d)],
                       check=True, capture_output=True)
        return d

    def test_status_json_mirrors_via_existing_state_sync(self):
        bus = kf.Bus(str(self.bus_root), "_")
        kf.write_daemon_status(self._args(), bus, "host-1", {}, [])
        kf.state_sync(self._args(), force=True)
        got = self._other()
        rec = json.loads((got / "kf" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["node_id"], "host-1")

    def test_idle_heartbeat_disabled_produces_no_extra_commit_beyond_status_write(self):
        # --status-interval 無効時、アイドル中に status.json を書き直さなければ、2 回目の
        # sync（interval 経過を模擬）は新しいコミットを作らない（＝追加の push が無い）。
        bus = kf.Bus(str(self.bus_root), "_")
        args = self._args(state_git_interval=3600.0)
        kf.write_daemon_status(args, bus, "host-1", {}, [])
        kf.state_sync(args, force=True)
        sg = kf.state_git_for(args)
        before = subprocess.run(["git", "-C", str(sg.clone), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        sg._last_remote = 0.0                     # interval 経過を模擬（次回は "due" になる）
        kf.state_sync(args)                        # status.json を書き直していないので差分なし
        after = subprocess.run(["git", "-C", str(sg.clone), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)