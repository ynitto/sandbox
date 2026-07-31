#!/usr/bin/env python3
"""kiro-loop のセッション開始コマンド（agent-session-commands 契約）の単体テスト。

tmux/kiro-cli 不要・標準ライブラリのみ。agent-loop の同名実装（クローン元）と同一の
振る舞いを担保する。計画（展開・when・有界化）は dashboard の JS plan() とも同一結果。

    python3 -m unittest test.test_session_commands
    または python3 test/test_session_commands.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
_SCRIPT = HERE.parent / "kiro-loop.py"
_spec = importlib.util.spec_from_file_location("kiroloop", _SCRIPT)
kl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kl)


class SessionCommandsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kl-sess-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.home = tempfile.mkdtemp(prefix="kl-sess-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        os.environ["AGENT_SESSION_DIR"] = self.home
        self.addCleanup(os.environ.pop, "AGENT_SESSION_DIR", None)
        kl._SESSION_COMMANDS_REV_APPLIED = None
        self.addCleanup(setattr, kl, "_SESSION_COMMANDS_REV_APPLIED", None)

    def _write(self, obj):
        with open(os.path.join(self.home, "session.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def test_missing_broken_disabled_are_no_op(self):
        self.assertIsNone(kl._load_session_commands())
        with open(os.path.join(self.home, "session.json"), "w", encoding="utf-8") as f:
            f.write("{ 壊れた JSON")
        self.assertIsNone(kl._load_session_commands())
        self.assertEqual(kl.plan_session_commands(None, {"engine": "kiro-loop"}), [])
        self.assertEqual(
            kl.plan_session_commands(
                {"enabled": False, "commands": [{"id": "a", "run": "echo hi"}]},
                {"engine": "kiro-loop"}),
            [])

    def test_run_returns_true_when_nothing_to_do(self):
        self.assertTrue(kl.run_session_commands({"engine": "kiro-loop"}))

    def test_placeholders_expand_without_quoting(self):
        ctx = {"cwd": "/w/my repo"}
        self.assertEqual(kl.expand_session_placeholders("cd {cwd} && ls", ctx), "cd /w/my repo && ls")
        self.assertEqual(kl.expand_session_placeholders("x {node_id} y", ctx), "x  y")
        self.assertEqual(kl.expand_session_placeholders("{unknown}", ctx), "{unknown}")

    def test_when_is_and_joined_and_absent_axes_pass(self):
        when = {"engines": ["kiro-loop"], "workloads": ["routine"]}
        self.assertTrue(kl.session_command_matches(when, {"engine": "kiro-loop", "workload": "routine"}))
        self.assertFalse(kl.session_command_matches(when, {"engine": "agent-flow", "workload": "routine"}))
        self.assertTrue(kl.session_command_matches(None, {"engine": "agent-flow"}))
        self.assertTrue(kl.session_command_matches(when, {}))

    def test_chat_is_allowed_on_resident_engine(self):
        data = {"commands": [{"id": "c", "mode": "chat", "run": "docs を読んで"}]}
        self.assertIsNone(kl.plan_session_commands(data, {"engine": "kiro-loop"})[0]["skip"])
        self.assertEqual(
            kl.plan_session_commands(data, {"engine": "agent-flow"})[0]["skip"], "no-session")

    def test_total_budget_truncates_then_skips(self):
        data = {"max_total_timeout": 100, "commands": [
            {"id": "a", "run": "x", "timeout": 60},
            {"id": "b", "run": "y", "timeout": 60},
            {"id": "c", "run": "z", "timeout": 30},
        ]}
        entries = kl.plan_session_commands(data, {"engine": "kiro-loop"})
        self.assertEqual(entries[0]["timeout"], 60)
        self.assertEqual(entries[1]["timeout"], 40)
        self.assertEqual(entries[2]["skip"], "budget")

    def test_commands_run_in_array_order(self):
        marker = os.path.join(self.dir, "order.txt")
        self._write({"commands": [
            {"id": "first", "run": f"echo 1 >> {marker}"},
            {"id": "second", "run": f"echo 2 >> {marker}"},
        ]})
        self.assertTrue(kl.run_session_commands({"engine": "kiro-loop"}))
        with open(marker, encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["1", "2"])

    def test_warn_continues_and_fail_blocks_session(self):
        marker = os.path.join(self.dir, "after.txt")
        self._write({"commands": [
            {"id": "bad", "run": "exit 3", "on_error": "warn"},
            {"id": "after", "run": f"echo ok > {marker}"},
        ]})
        self.assertTrue(kl.run_session_commands({"engine": "kiro-loop"}))
        self.assertTrue(os.path.exists(marker))

        os.remove(marker)
        self._write({"commands": [
            {"id": "bad", "run": "exit 3", "on_error": "fail"},
            {"id": "after", "run": f"echo ok > {marker}"},
        ]})
        self.assertFalse(kl.run_session_commands({"engine": "kiro-loop"}))
        self.assertFalse(os.path.exists(marker))

    def test_timeout_is_bounded(self):
        self._write({"commands": [{"id": "slow", "run": "sleep 5", "timeout": 1, "on_error": "fail"}]})
        started = time.time()
        self.assertFalse(kl.run_session_commands({"engine": "kiro-loop"}))
        self.assertLess(time.time() - started, 4)

    def test_modes_split_process_and_chat(self):
        """ペイン生成の前後で 2 回に分けて呼ぶ。process だけ / chat だけが走る。"""
        marker = os.path.join(self.dir, "p.txt")
        sent = []
        self._write({"commands": [
            {"id": "p", "mode": "process", "run": f"echo x > {marker}"},
            {"id": "c", "mode": "chat", "run": "はじめの指示"},
        ]})
        kl.run_session_commands({"engine": "kiro-loop"}, send_chat=sent.append, modes=("process",))
        self.assertTrue(os.path.exists(marker))
        self.assertEqual(sent, [])

        os.remove(marker)
        kl.run_session_commands({"engine": "kiro-loop"}, send_chat=sent.append, modes=("chat",))
        self.assertEqual(sent, ["はじめの指示"])
        self.assertFalse(os.path.exists(marker))

    def test_revision_is_recorded_for_status(self):
        self._write({"revision": 7, "commands": [{"id": "a", "run": "true"}]})
        kl.run_session_commands({"engine": "kiro-loop"})
        self.assertEqual(kl._SESSION_COMMANDS_REV_APPLIED, 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
