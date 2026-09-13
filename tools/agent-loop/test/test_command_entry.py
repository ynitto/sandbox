"""定期プロンプトの `command:` 実行の回帰。

entry が固定コマンドを宣言したら、デーモンは対話ペインにも LLM にも触れず、宣言された
argv を 1 回実行する。成否は**終了コード**で決まり、実行はスロット・実行レコード・
リポジトリ履歴・`RESULT` 行という既存の記録面に乗る。

`{…}` の補完はフック / webhook が返した辞書を材料にする。規則は本文テンプレートと同じ
（`str.format_map`・未定義キーは残す・遅延 lookup を先に解く）。

仕様: docs/specs/agent-loop-spec.md §2.3 / §2.3.2。
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as al  # noqa: E402


class DeclarationTest(unittest.TestCase):
    """宣言の正規化（3 形）と、読み込み時に断る書き方。"""

    def _one(self, **extra):
        return al.validate_entries([{
            "name": "c", "command": "echo hi", "interval_minutes": 10, **extra,
        }])[0]

    def test_a_string_is_split_into_argv(self):
        self.assertEqual(self._one()["command"]["argv"], ["echo", "hi"])

    def test_an_array_is_taken_as_is(self):
        entry = self._one(command=["python3", "-m", "pytest", "-q"])
        self.assertEqual(entry["command"]["argv"], ["python3", "-m", "pytest", "-q"])

    def test_a_map_carries_the_timeout_and_the_environment(self):
        entry = self._one(command={"argv": ["a"], "timeout_sec": 600, "env": {"K": "v"}})
        self.assertEqual(entry["command"]["timeout_sec"], 600)
        self.assertEqual(entry["command"]["env"], {"K": "v"})

    def test_the_default_timeout_matches_the_hooks_it_replaces(self):
        self.assertEqual(self._one()["command"]["timeout_sec"], 300)

    def test_a_leading_tilde_is_expanded_but_a_placeholder_is_not(self):
        entry = self._one(command=["s.py", "~/notes", "{iid}"])
        self.assertEqual(entry["command"]["argv"][1], os.path.expanduser("~/notes"))
        self.assertEqual(entry["command"]["argv"][2], "{iid}")

    def test_a_shell_metacharacter_is_refused_at_load_time(self):
        # シェルを通さないので、書けても効かない。効かない指定は通さない。
        with self.assertRaisesRegex(ValueError, "シェル記号"):
            self._one(command="a | b")

    def test_an_entry_that_declares_nothing_else_is_still_adopted(self):
        entries = al.validate_entries([
            {"name": "c", "command": "echo hi", "interval_minutes": 5}])
        self.assertEqual(len(entries), 1)

    def test_it_cannot_be_combined_with_another_kind_of_work(self):
        for extra in ({"prompt": "本文"}, {"statemachine": "digest"}, {"slash": ["x"]}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "併用"):
                self._one(**extra)

    def test_it_cannot_be_combined_with_an_llm_or_a_session_knob(self):
        for extra in ({"agent_cli": "aider"}, {"model": "m"}, {"session": "keep"},
                      {"acceptance": ["x"]}, {"oneshot": True}, {"target": "t"},
                      {"fresh_context": True}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "併用"):
                self._one(**extra)

    def test_adaptive_needs_a_hook_because_a_command_is_never_idle(self):
        adaptive = {"enabled": True}
        with self.assertRaisesRegex(ValueError, "adaptive"):
            self._one(adaptive=adaptive)
        # フックがあれば `check()` の None が無風なので、従来どおり効く。
        self.assertIsNotNone(self._one(adaptive=adaptive, hooks="h")["adaptive"])


class SubstitutionTest(unittest.TestCase):
    """`{…}` の補完（本文テンプレートと同じ規則）。"""

    def test_a_value_is_placed_into_one_token(self):
        argv = al._loopentry.render_argv(["s.py", "--iid", "{iid}"], {"iid": "42"})
        self.assertEqual(argv, ["s.py", "--iid", "42"])

    def test_a_value_with_spaces_does_not_become_two_arguments(self):
        argv = al._loopentry.render_argv(["s.py", "--title", "{t}"], {"t": "a b c"})
        self.assertEqual(argv, ["s.py", "--title", "a b c"])

    def test_an_unknown_key_is_left_as_written(self):
        argv = al._loopentry.render_argv(["s.py", "{nope}"], {"iid": "42"})
        self.assertEqual(argv, ["s.py", "{nope}"])

    def test_without_material_the_declaration_passes_through(self):
        argv = al._loopentry.render_argv(["s.py", "{iid}"], {})
        self.assertEqual(argv, ["s.py", "{iid}"])


class HeadlessRunTest(unittest.TestCase):
    """実行経路。LLM を起こさず、終了コードで成否を決める。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        Path(self.dir, "ok.py").write_text("print('done')\n", encoding="utf-8")
        Path(self.dir, "ng.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        self.sched = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        self.sched._lock = threading.RLock()
        self.sched._workspace = self.dir
        self.sched._tool_config = {"headless_pane": False}
        self.sched._session_mgr = mock.Mock()
        self.sched._semaphore = None
        self.sched._slot_monitor = None
        self.sched._executions = {}
        self.sched._sessions = {}
        self.sched._command_running = {}
        self.sched._begin_active = mock.Mock()
        self.sched._end_active = mock.Mock()
        self.sched._release_slot = mock.Mock()
        self.sched._fail_execution = mock.Mock()
        self.sched._upsert_execution = mock.Mock()
        self.sched._pop_execution = mock.Mock()

    def _entry(self, command, **extra):
        return al.validate_entries([{
            "id": "e1", "name": "c", "command": command, "interval_minutes": 5, **extra,
        }])[0]

    def _run(self, entry, meta=None):
        req = {"id": "r1", "entry_id": "e1", "prompt": "", "source": "schedule",
               "meta": dict(meta or {})}
        with mock.patch.object(al._harness_toolloop, "_tl_resolve_agent") as resolve, \
             mock.patch.object(al._harness_toolloop, "run_prompt") as run_prompt:
            self.sched._run_headless(req, entry, {"prompt": ""}, None,
                                     self.dir, "r1", None)
        return resolve, run_prompt

    def test_a_successful_command_completes_without_touching_an_llm(self):
        resolve, run_prompt = self._run(self._entry(["python3", "ok.py"]))
        resolve.assert_not_called()         # CLI 定義の解決すらしない
        run_prompt.assert_not_called()
        self.sched._fail_execution.assert_not_called()
        self.sched._end_active.assert_called_once()

    def test_a_non_zero_exit_is_reported_as_a_command_failure(self):
        self._run(self._entry(["python3", "ng.py"]))
        self.sched._fail_execution.assert_called_once()
        self.assertEqual(self.sched._fail_execution.call_args.kwargs["reason"],
                         "command_failed")

    def test_a_missing_executable_fails_without_crashing_the_daemon(self):
        self._run(self._entry(["no-such-executable-xyz"]))
        self.assertEqual(self.sched._fail_execution.call_args.kwargs["reason"],
                         "command_failed")

    def test_hook_material_reaches_the_argv(self):
        Path(self.dir, "echo.py").write_text(
            "import sys\nprint(sys.argv[1])\n", encoding="utf-8")
        seen = {}

        def fake_run(spec, **kwargs):
            seen.update(spec)
            return {"ok": True, "status": 0, "stopReason": "command_exit",
                    "durationSec": 0.1, "argv": spec["argv"]}

        entry = self._entry(["python3", "echo.py", "{iid}"])
        req = {"id": "r1", "entry_id": "e1", "prompt": "", "source": "hook",
               "meta": {"_values": {"iid": "42"}}}
        with mock.patch.object(al._commandrun, "run_command", fake_run):
            self.sched._run_headless(req, entry, {"prompt": ""}, None,
                                     self.dir, "r1", None)
        self.assertEqual(seen["argv"], ["python3", "echo.py", "42"])

    def test_the_running_flag_is_cleared_however_the_run_ends(self):
        # 解除を書き忘れた分岐が 1 つでもあると、その entry は二度と回らなくなる
        # （フラグが残り、以後の dispatch がすべて defer になる）。出口は 1 つ。
        for command in (["python3", "ok.py"], ["python3", "ng.py"],
                        ["no-such-executable-xyz"]):
            with self.subTest(command=command):
                self.sched._command_running["e1"] = "r0"
                self.sched._run_headless_guarded(
                    {"id": "r1", "entry_id": "e1", "prompt": "", "source": "schedule",
                     "meta": {}},
                    self._entry(command), {"prompt": ""}, None, self.dir, "r1", None)
                self.assertNotIn("e1", self.sched._command_running)

    def test_an_unexpected_exception_still_clears_the_running_flag(self):
        self.sched._command_running["e1"] = "r0"
        with mock.patch.object(al._commandrun, "run_command",
                               side_effect=RuntimeError("boom")):
            self.sched._run_headless_guarded(
                {"id": "r1", "entry_id": "e1", "prompt": "", "source": "schedule",
                 "meta": {}},
                self._entry(["python3", "ok.py"]), {"prompt": ""}, None,
                self.dir, "r1", None)
        self.assertNotIn("e1", self.sched._command_running)

    def test_a_result_is_visible_in_repository_history(self):
        # 履歴は entry 名で引く（コマンドはワークフローを持たない）。
        config = Path(self.dir, "agent-loop.yaml")
        config.write_text("prompts:\n  - name: c\n    command: python3 ok.py\n"
                          "    interval_minutes: 5\n", encoding="utf-8")
        with tempfile.TemporaryDirectory() as history_dir, \
             mock.patch.dict(os.environ, {"AGENT_LOOP_RUN_HISTORY_DIR": history_dir}):
            self._run(self._entry(["python3", "ok.py"]))
            snapshot = al.repository_snapshot(self.dir)
        task = next(t for t in snapshot["tasks"] if t["kind"] == "command")
        self.assertEqual(len(task["history"]), 1)
        self.assertEqual(task["history"][0]["entryName"], "c")
        self.assertTrue(task["history"][0]["ok"])
        self.assertEqual(task["history"][0]["source"], "scheduled")


class HookAckTest(unittest.TestCase):
    """headless 実行が成功した回はフックへ `ack()` を返す（既存の穴の修正）。"""

    def setUp(self):
        self.sched = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        self.sched._call_hook_ack = mock.Mock()

    def test_a_hook_driven_run_acknowledges_the_event(self):
        self.sched._ack_headless_hook(
            {"source": "hook", "meta": {"_hook": "h"}}, {"id": "e1"})
        self.sched._call_hook_ack.assert_called_once()

    def test_a_scheduled_run_has_nothing_to_acknowledge(self):
        self.sched._ack_headless_hook({"source": "schedule", "meta": {}}, {"id": "e1"})
        self.sched._call_hook_ack.assert_not_called()


if __name__ == "__main__":
    unittest.main()
