#!/usr/bin/env python3
"""kiro-loop のノード予算 v2（トークン一次・rates 推定）と agent-control（lifecycle・
status・停止）の単体テスト。tmux/kiro-cli 不要・標準ライブラリのみ。

    python3 -m unittest test.test_node_budget
    または python3 test/test_node_budget.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
_SCRIPT = HERE.parent / "kiro-loop.py"
_spec = importlib.util.spec_from_file_location("kiroloop", _SCRIPT)
kl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kl)


class NodeBudgetV2Tests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kl-nbv2-")
        os.environ["AGENT_BUDGET_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)

    def _config(self, cfg):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _ledger(self, records):
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        day = time.strftime("%Y%m%d", time.gmtime())
        with open(os.path.join(led, f"{day}.jsonl"), "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

    def test_no_config_unlimited(self):
        self.assertIsNone(kl._node_budget_state())

    def test_token_estimation_and_measured(self):
        self._config({"version": 2, "tokens": 1000, "rates": {"per_cli": {"kiro": 100}}})
        self._ledger([{"workload": "routine", "seconds": 8, "agent_cli": "kiro"}])  # 800 est
        self.assertFalse(kl._node_budget_state()["exceeded"])
        self._ledger([{"workload": "routine", "seconds": 1, "tokens_in": 300}])     # +300
        self.assertTrue(kl._node_budget_state()["exceeded"])

    def test_on_exhausted_default_pause(self):
        self._config({"version": 2, "tokens": 1})
        self._ledger([{"workload": "routine", "seconds": 1, "tokens_in": 5}])
        self.assertEqual(kl._node_budget_state()["on_exhausted"], "pause")

    def test_on_exhausted_stop_flag(self):
        self._config({"version": 2, "tokens": 1,
                      "allocation": {"workloads": {"routine": {"on_exhausted": "stop"}}}})
        self._ledger([{"workload": "routine", "seconds": 1, "tokens_in": 5}])
        st = kl._node_budget_state()
        self.assertTrue(st["exceeded"])
        self.assertEqual(st["on_exhausted"], "stop")

    def test_time_limit_still_enforced_v1_compat(self):
        self._config({"execution_minutes": 1, "period": "day"})   # v1 config
        self._ledger([{"workload": "routine", "seconds": 61}])
        self.assertTrue(kl._node_budget_state()["exceeded"])


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kl-ctl-")
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_CONTROL_DIR", None)
        kl._CONTROL_CACHE["mtime"] = None

    def _control(self, ctl):
        with open(os.path.join(self.dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump(ctl, f)
        kl._CONTROL_CACHE["mtime"] = None

    def test_lifecycle_default_run(self):
        self.assertEqual(kl._control_lifecycle(), "run")

    def test_lifecycle_read(self):
        self._control({"version": 1, "workloads": {"routine": {"lifecycle": "pause"}}})
        self.assertEqual(kl._control_lifecycle(), "pause")

    def test_status_heartbeat(self):
        self._control({"version": 1, "revision": 5, "workloads": {"routine": {}}})
        kl._write_status(lifecycle="run")
        sf = os.path.join(self.dir, "status")
        files = [n for n in os.listdir(sf) if n.endswith(".json")]
        self.assertTrue(files)
        with open(os.path.join(sf, files[0]), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["tool"], "kiro-loop")
        self.assertEqual(rec["workload"], "routine")
        self.assertEqual(rec["revision_applied"], 5)


class GlobalInstructionsTests(unittest.TestCase):
    """グローバル指示（agent-instructions 契約）: 描画・差分注入・status 相乗り。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kl-instr-")
        os.environ["AGENT_INSTRUCTIONS_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_INSTRUCTIONS_DIR", None)
        kl._INSTRUCTIONS_REV_APPLIED = None
        self.addCleanup(setattr, kl, "_INSTRUCTIONS_REV_APPLIED", None)

    def _write(self, obj):
        with open(os.path.join(self.dir, "instructions.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_render_and_disabled(self):
        b = kl.render_instructions_block({
            "revision": 5, "enabled": True, "text": "回答は日本語。",
            "skills": ["karpathy-guidelines", {"name": "self-checking", "note": "提出前に自己評価"}],
            "tools": {"allow": ["fs_read"], "deny_note": "push は人の確認"}})
        self.assertTrue(b.startswith("<!-- agent-instructions rev:5 -->"))
        self.assertIn("- self-checking — 提出前に自己評価", b)
        self.assertIn("ツール（許可）: fs_read", b)
        self.assertEqual(kl.render_instructions_block({"enabled": False, "text": "x", "revision": 1}), "")

    def test_maybe_prepend_only_on_revision_change(self):
        self._write({"version": 1, "revision": 2, "enabled": True, "text": "共通指示Y"})
        # SessionManager を作らず、必要な属性だけ持つ軽量スタブでメソッドを検証する
        stub = types.SimpleNamespace(_instr_rev={}, _lock=threading.Lock())
        first = kl.SessionManager._maybe_prepend_instructions(stub, "p1", "タスク")
        self.assertTrue(first.startswith("<!-- agent-instructions rev:2 -->"))
        self.assertIn("共通指示Y", first)
        self.assertEqual(kl._INSTRUCTIONS_REV_APPLIED, 2)
        # 同 revision の再送では前置しない（差分注入）
        second = kl.SessionManager._maybe_prepend_instructions(stub, "p1", "次のタスク")
        self.assertEqual(second, "次のタスク")
        # revision が上がれば再注入
        self._write({"version": 1, "revision": 3, "enabled": True, "text": "共通指示Z"})
        third = kl.SessionManager._maybe_prepend_instructions(stub, "p1", "また")
        self.assertTrue(third.startswith("<!-- agent-instructions rev:3 -->"))

    def test_status_carries_instructions_revision(self):
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_CONTROL_DIR", None)
        kl._CONTROL_CACHE["mtime"] = None
        kl._INSTRUCTIONS_REV_APPLIED = 4
        kl._write_status(lifecycle="run")
        sf = os.path.join(self.dir, "status")
        rec = json.load(open(os.path.join(sf, os.listdir(sf)[0]), encoding="utf-8"))
        self.assertEqual(rec["instructions_revision_applied"], 4)


class SchedulerStopTests(unittest.TestCase):
    """予算 on_exhausted=stop / control lifecycle=stop でスケジューラが停止要求すること。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kl-stop-")
        os.environ["AGENT_BUDGET_DIR"] = self.dir
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)
        self.addCleanup(os.environ.pop, "AGENT_CONTROL_DIR", None)
        kl._CONTROL_CACHE["mtime"] = None

    def _make_scheduler(self):
        sched = kl.PeriodicScheduler.__new__(kl.PeriodicScheduler)
        sched._stop_event = kl.threading.Event()
        sched._lock = kl.threading.Lock()
        sched._entries = []
        sched._node_budget_warned_at = 0.0
        sched._thread = None
        return sched

    def test_budget_stop_triggers_shutdown(self):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 2, "tokens": 1,
                       "allocation": {"workloads": {"routine": {"on_exhausted": "stop"}}}}, f)
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        day = time.strftime("%Y%m%d", time.gmtime())
        with open(os.path.join(led, f"{day}.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"workload": "routine", "seconds": 1, "tokens_in": 99}) + "\n")
        sched = self._make_scheduler()
        with mock.patch.object(sched, "_request_shutdown") as shutdown:
            # _stop_event.wait(1) を即 False（=継続）にしてループ本体を 1 度だけ通す。
            with mock.patch.object(sched._stop_event, "wait", side_effect=[False, True]):
                sched._run_loop()
        shutdown.assert_called_once()
        # 停止理由マーカが残る
        markers = [n for n in os.listdir(kl._STATE_DIR) if n.startswith("stopped-")]
        self.assertTrue(markers)

    def test_control_stop_triggers_shutdown(self):
        with open(os.path.join(self.dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "workloads": {"routine": {"lifecycle": "stop"}}}, f)
        kl._CONTROL_CACHE["mtime"] = None
        sched = self._make_scheduler()
        with mock.patch.object(sched, "_request_shutdown") as shutdown:
            with mock.patch.object(sched._stop_event, "wait", side_effect=[False, True]):
                sched._run_loop()
        shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
