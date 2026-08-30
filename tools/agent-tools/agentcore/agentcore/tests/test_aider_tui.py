"""共通 TUI の aider バックエンド（設計 2026-08-27 §7.1・§7.5 / 段 12）。

要点は 3 つ。①前面の規約がバックエンドによらず 1 つ（aider.json の interactive が
ollama.json と同じ ready_pattern / turn hook を宣言する）、②1 入力 = aider 1 回
（`--message`）で、会話を積まない、③`/sm` `/edit` は対話で打たれてもハーネスへ回る
（層3 の限定ツール契約が対話からも使える）。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import aider_adapter, herdcli, ollama_skills, ollama_tui  # noqa: E402

BUNDLED = Path(__file__).resolve().parents[5] / "agents"


class TheDefinitionSharesTheFrontTests(unittest.TestCase):
    def test_aider_interactive_is_the_common_tui(self):
        """capture-pane から見た画面の規約が ollama バックエンドと同じであること。"""
        aider = json.loads((BUNDLED / "aider.json").read_text(encoding="utf-8"))["interactive"]
        ollama = json.loads((BUNDLED / "ollama.json").read_text(encoding="utf-8"))["interactive"]
        self.assertEqual(aider["command"][:3], ["agent-herd", "aider", "--tui"])
        self.assertEqual(aider["ready_pattern"], ollama["ready_pattern"],
                         "ready_pattern はバックエンドによらず共有する（受入条件）")
        self.assertEqual(aider["turn_completion"], "ollama",
                         "前面が我々の TUI なので hook 資産は要らない（env だけ）")

    def test_tui_flag_is_adapter_only(self):
        forwarded, managed = aider_adapter._wrapper_args(["--tui", "--model", "m"])
        self.assertTrue(managed["tui"])
        self.assertNotIn("--tui", forwarded, "--tui を aider へ渡さない")


class _CapturedRunner:
    """_tui_repl が repl へ渡す runner を捕まえる。"""

    def __enter__(self):
        self.runner = None

        def fake_repl(runner, **_kw):
            self.runner = runner
            return 0

        self._patch = mock.patch.object(ollama_tui, "repl", fake_repl)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


class AiderBackendRunnerTests(unittest.TestCase):
    MODEL = "ollama_chat/gemma4:e4b"

    def _runner(self, managed=None):
        cap = _CapturedRunner()
        with cap:
            rc = aider_adapter._tui_repl(
                ["--model", self.MODEL, "--no-git"],
                {"policy": None, "num_ctx": None, "num_predict": None, "tui": True,
                 **(managed or {})})
        self.assertEqual(rc, 0)
        return cap.runner

    def test_one_input_is_one_aider_message_run(self):
        runner = self._runner()
        with mock.patch.object(aider_adapter.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="編集した")) as run:
            body = runner("foo.py を直して", model=self.MODEL, tools=False,
                          think=None, renderer=None)
        self.assertEqual(body, "編集した")
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "aider")
        self.assertEqual(argv[-2:], ["--message", "foo.py を直して"])
        self.assertIn("--no-git", argv)
        self.assertEqual(argv[argv.index("--model") + 1], self.MODEL)

    def test_nonzero_exit_is_an_error_not_a_body(self):
        runner = self._runner()
        with mock.patch.object(aider_adapter.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="途中")):
            with self.assertRaises(RuntimeError):
                runner("直して", model=self.MODEL, tools=False, think=None, renderer=None)

    def test_unknown_slash_is_an_explicit_error(self):
        """未知の /x を本文として aider へ流さない（設計 §3.2）。"""
        runner = self._runner()
        with self.assertRaises(RuntimeError):
            runner("/nope なにか", model=self.MODEL, tools=False, think=None, renderer=None)

    def test_shape_commands_do_not_apply_to_this_backend(self):
        runner = self._runner()
        with self.assertRaises(RuntimeError):
            runner("/ask 富士山の高さは?", model=self.MODEL, tools=False,
                   think=None, renderer=None)

    def test_a_real_skill_is_expanded_not_called_unknown(self):
        """/help と /skills と Tab が案内するものを、runner が「未知」と言わないこと。

        表だけを引く（lookup）と宣言もスキルも None になり、実在するスキル名が
        「未知のコマンドです」で弾かれる——広告している面と実際に動く面がずれる。
        """
        runner = self._runner()
        with mock.patch.object(ollama_skills, "skill_exists", lambda name: name == "tidy"), \
             mock.patch.object(ollama_skills, "expand",
                               lambda prompt, **kw: ("スキル本文\n" + kw["plan"].body, [])), \
             mock.patch.object(aider_adapter.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="編集した")) as run:
            body = runner("/tidy\nfoo.py を直して", model=self.MODEL, tools=False,
                          think=None, renderer=None)
        self.assertEqual(body, "編集した")
        self.assertEqual(run.call_args.args[0][-1], "スキル本文\nfoo.py を直して")

    def test_ask_is_refused_even_though_it_asks_for_no_tools(self):
        """`tools` は False も意味を持つ（/ask＝道具なし）。

        真偽で見ると /ask が黙って素通りし、指定が消えたまま aider が走る。
        """
        runner = self._runner()
        with mock.patch.object(aider_adapter.subprocess, "run") as run:
            with self.assertRaises(RuntimeError):
                runner("/ask 富士山の高さは?", model=self.MODEL, tools=False,
                       think=None, renderer=None)
        self.assertFalse(run.called, "断ったのに aider を起こさない")

    def test_this_backend_declares_it_writes_no_log(self):
        """/ctx と /status が別の ollama 実行の数字を出さないための宣言。"""
        seen = {}
        with mock.patch.object(ollama_tui, "repl",
                               side_effect=lambda runner, **kw: seen.update(kw) or 0):
            aider_adapter._tui_repl(["--model", self.MODEL],
                                    {"policy": None, "num_ctx": None, "num_predict": None})
        self.assertIs(seen["event_log"], False)

    def test_model_switch_with_managed_settings_is_refused(self):
        """settings の entry は起動時モデル名で束ねてある——黙って外さない。"""
        runner = self._runner({"policy": aider_adapter.POLICY_ID})
        with self.assertRaises(RuntimeError):
            runner("直して", model="ollama_chat/gemma4:12b", tools=False,
                   think=None, renderer=None)


class HarnessDispatchFromTheTuiTests(unittest.TestCase):
    def _repl(self, script: str, runner=None):
        calls = []

        def default_runner(prompt, **_kw):
            calls.append(prompt)
            return "本文"

        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_LOOP_EXECUTABLE", None)
            rc = ollama_tui.repl(runner or default_runner, model="m", tools=False,
                                 out=out, in_=io.StringIO(script))
        return rc, out.getvalue(), calls

    def test_edit_goes_to_the_headless_harness_not_the_backend(self):
        with mock.patch.object(herdcli, "_run_harness", return_value=0) as harness:
            rc, _text, calls = self._repl("/edit foo.py を直して\n/quit\n")
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [], "バックエンドは呼ばれない（ハーネスへ回る）")
        kind, ns, _cwd = harness.call_args.args
        self.assertEqual(kind, "run")
        self.assertEqual(ns.prompt, ["/edit foo.py を直して"],
                         "コマンド行ごと渡す（/sm の振り分けは cmd_run が持つ）")
        self.assertIsNone(ns.agent_cli, "エンジン選択は宣言と既定に任せる")

    def test_sm_takes_the_same_route(self):
        with mock.patch.object(herdcli, "_run_harness", return_value=0) as harness:
            self._repl("/sm nightly\n/quit\n")
        self.assertEqual(harness.call_args.args[0], "run")

    def test_harness_failure_does_not_kill_the_loop(self):
        with mock.patch.object(herdcli, "_run_harness", side_effect=RuntimeError("boom")):
            rc, text, calls = self._repl("/edit x\nつぎ\n/quit\n")
        self.assertEqual(rc, 0)
        self.assertIn("✖", text)
        self.assertEqual(calls, ["つぎ"], "次の入力は普通に処理される")


if __name__ == "__main__":
    unittest.main()
