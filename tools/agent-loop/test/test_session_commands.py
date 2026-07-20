"""セッション開始コマンド（agent-session-commands 契約）のテスト。

常駐系（tmux ペイン = セッション）の実装を対象にする。kiro-loop の同名実装は
このクローン元と同一ロジックなので、契約の振る舞いはここで担保する。

実行: python3 -m pytest tools/agent-loop/test/ -q
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class SessionCommandsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="al-sess-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.home = tempfile.mkdtemp(prefix="al-sess-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        os.environ["AGENT_SESSION_DIR"] = self.home
        self.addCleanup(os.environ.pop, "AGENT_SESSION_DIR", None)
        al._SESSION_COMMANDS_REV_APPLIED = None
        self.addCleanup(setattr, al, "_SESSION_COMMANDS_REV_APPLIED", None)

    def _write(self, obj):
        with open(os.path.join(self.home, "session.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    # -- 読取のフェイルセーフ ------------------------------------------------

    def test_missing_broken_disabled_are_no_op(self):
        self.assertIsNone(al._load_session_commands())
        with open(os.path.join(self.home, "session.json"), "w", encoding="utf-8") as f:
            f.write("{ 壊れた JSON")
        self.assertIsNone(al._load_session_commands())
        self.assertEqual(al.plan_session_commands(None, {"engine": "agent-loop"}), [])
        self.assertEqual(
            al.plan_session_commands(
                {"enabled": False, "commands": [{"id": "a", "run": "echo hi"}]},
                {"engine": "agent-loop"}),
            [])

    def test_run_returns_true_when_nothing_to_do(self):
        """コマンドが無い / 読めないときは「開始してよい」を返す（ペイン起動を止めない）。"""
        self.assertTrue(al.run_session_commands({"engine": "agent-loop"}))

    # -- 計画の決定性（dashboard の JS plan() と同一結果） --------------------

    def test_placeholders_expand_without_quoting(self):
        ctx = {"cwd": "/w/my repo"}
        self.assertEqual(al.expand_session_placeholders("cd {cwd} && ls", ctx), "cd /w/my repo && ls")
        self.assertEqual(al.expand_session_placeholders("x {node_id} y", ctx), "x  y")
        self.assertEqual(al.expand_session_placeholders("{unknown}", ctx), "{unknown}")

    def test_when_is_and_joined_and_absent_axes_pass(self):
        when = {"engines": ["agent-loop"], "workloads": ["routine"]}
        self.assertTrue(al.session_command_matches(when, {"engine": "agent-loop", "workload": "routine"}))
        self.assertFalse(al.session_command_matches(when, {"engine": "agent-flow", "workload": "routine"}))
        self.assertTrue(al.session_command_matches(None, {"engine": "agent-flow"}))
        self.assertTrue(al.session_command_matches(when, {}))

    def test_chat_is_allowed_on_resident_engine(self):
        data = {"commands": [{"id": "c", "mode": "chat", "run": "docs を読んで"}]}
        self.assertIsNone(al.plan_session_commands(data, {"engine": "agent-loop"})[0]["skip"])
        self.assertEqual(
            al.plan_session_commands(data, {"engine": "agent-flow"})[0]["skip"], "no-session")

    def test_total_budget_truncates_then_skips(self):
        data = {"max_total_timeout": 100, "commands": [
            {"id": "a", "run": "x", "timeout": 60},
            {"id": "b", "run": "y", "timeout": 60},
            {"id": "c", "run": "z", "timeout": 30},
        ]}
        entries = al.plan_session_commands(data, {"engine": "agent-loop"})
        self.assertEqual(entries[0]["timeout"], 60)
        self.assertEqual(entries[1]["timeout"], 40)
        self.assertEqual(entries[2]["skip"], "budget")

    # -- 実行 ----------------------------------------------------------------

    def test_commands_run_in_array_order(self):
        marker = os.path.join(self.dir, "order.txt")
        self._write({"commands": [
            {"id": "first", "run": "echo 1 >> %s" % marker},
            {"id": "second", "run": "echo 2 >> %s" % marker},
        ]})
        self.assertTrue(al.run_session_commands({"engine": "agent-loop"}))
        with open(marker, encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["1", "2"])

    def test_warn_continues_and_fail_blocks_session(self):
        marker = os.path.join(self.dir, "after.txt")
        self._write({"commands": [
            {"id": "bad", "run": "exit 3", "on_error": "warn"},
            {"id": "after", "run": "echo ok > %s" % marker},
        ]})
        self.assertTrue(al.run_session_commands({"engine": "agent-loop"}))
        self.assertTrue(os.path.exists(marker))

        os.remove(marker)
        self._write({"commands": [
            {"id": "bad", "run": "exit 3", "on_error": "fail"},
            {"id": "after", "run": "echo ok > %s" % marker},
        ]})
        self.assertFalse(al.run_session_commands({"engine": "agent-loop"}),
                         "fail はセッションを開始させない")
        self.assertFalse(os.path.exists(marker))

    def test_timeout_is_bounded(self):
        self._write({"commands": [{"id": "slow", "run": "sleep 5", "timeout": 1, "on_error": "fail"}]})
        started = time.time()
        self.assertFalse(al.run_session_commands({"engine": "agent-loop"}))
        self.assertLess(time.time() - started, 4)

    def test_modes_split_process_and_chat(self):
        """常駐系はペイン生成の前後で 2 回に分けて呼ぶ。process だけ / chat だけが走る。"""
        marker = os.path.join(self.dir, "p.txt")
        sent = []
        self._write({"commands": [
            {"id": "p", "mode": "process", "run": "echo x > %s" % marker},
            {"id": "c", "mode": "chat", "run": "はじめの指示"},
        ]})
        al.run_session_commands({"engine": "agent-loop"}, send_chat=sent.append, modes=("process",))
        self.assertTrue(os.path.exists(marker))
        self.assertEqual(sent, [], "process だけの回では chat を送らない")

        os.remove(marker)
        al.run_session_commands({"engine": "agent-loop"}, send_chat=sent.append, modes=("chat",))
        self.assertEqual(sent, ["はじめの指示"])
        self.assertFalse(os.path.exists(marker), "chat だけの回では process を再実行しない")

    def test_chat_send_failure_does_not_block_session(self):
        self._write({"commands": [{"id": "c", "mode": "chat", "run": "指示"}]})

        def boom(_text):
            raise RuntimeError("送信できません")

        self.assertTrue(al.run_session_commands({"engine": "agent-loop"}, send_chat=boom))

    def test_revision_is_recorded_for_status(self):
        self._write({"revision": 7, "commands": [{"id": "a", "run": "true"}]})
        al.run_session_commands({"engine": "agent-loop"})
        self.assertEqual(al._SESSION_COMMANDS_REV_APPLIED, 7)


class StatusHeartbeatTests(unittest.TestCase):
    """agent-control の status ハートビート（kiro-loop から移植した可視化のみの層）。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="al-ctl-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_CONTROL_DIR", None)
        al._CONTROL_CACHE["mtime"] = None
        al._INSTRUCTIONS_REV_APPLIED = None
        al._SESSION_COMMANDS_REV_APPLIED = None
        self.addCleanup(setattr, al, "_INSTRUCTIONS_REV_APPLIED", None)
        self.addCleanup(setattr, al, "_SESSION_COMMANDS_REV_APPLIED", None)

    def _read_status(self):
        status_dir = os.path.join(self.dir, "status")
        files = [n for n in os.listdir(status_dir) if n.endswith(".json")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(status_dir, files[0]), encoding="utf-8") as f:
            return json.load(f)

    def test_status_identifies_agent_loop_as_routine(self):
        al._write_status()
        rec = self._read_status()
        self.assertEqual(rec["tool"], "agent-loop")
        self.assertEqual(rec["workload"], "routine")
        self.assertEqual(rec["pid"], os.getpid())

    def test_applied_revisions_are_omitted_until_applied(self):
        al._write_status()
        rec = self._read_status()
        self.assertNotIn("instructions_revision_applied", rec)
        self.assertNotIn("session_commands_revision_applied", rec)

    def test_applied_revisions_are_reported(self):
        with open(os.path.join(self.dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump({"revision": 12}, f)
        al._CONTROL_CACHE["mtime"] = None
        al._INSTRUCTIONS_REV_APPLIED = 3
        al._SESSION_COMMANDS_REV_APPLIED = 7
        al._write_status()
        rec = self._read_status()
        self.assertEqual(rec["revision_applied"], 12)
        self.assertEqual(rec["instructions_revision_applied"], 3)
        self.assertEqual(rec["session_commands_revision_applied"], 7)

    def test_unwritable_control_dir_does_not_raise(self):
        # 通常ファイルを親に据えると status/ を掘れない（OSError になる経路）
        blocker = os.path.join(self.dir, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("not a directory")
        os.environ["AGENT_CONTROL_DIR"] = blocker
        al._CONTROL_CACHE["mtime"] = None
        al._write_status()  # 例外を投げないことが仕様（定常業務を止めない）


if __name__ == "__main__":
    unittest.main(verbosity=2)
