from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from agentcore import agentcli, ollama_adapter, ollama_loop


class TestOllamaAdapter(unittest.TestCase):
    def test_generate_requests_non_streaming_and_returns_usage(self):
        response = io.BytesIO(json.dumps({
            "response": "ok", "prompt_eval_count": 12, "eval_count": 34,
        }).encode())
        response.__enter__ = lambda self: self
        response.__exit__ = lambda *args: None
        with mock.patch.object(ollama_adapter.urllib.request, "urlopen", return_value=response) as call:
            result = ollama_adapter.generate("qwen3", "hello")
        sent = json.loads(call.call_args.args[0].data)
        self.assertEqual(sent, {"model": "qwen3", "prompt": "hello", "stream": False})
        self.assertEqual((result["prompt_eval_count"], result["eval_count"]), (12, 34))

    def test_main_separates_model_text_and_usage(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_loop, "run_plain", return_value={
                "text": "answer", "tokens_in": 12, "tokens_out": 34}), \
                mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("hello")), \
                redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(ollama_adapter.main(["qwen3", "--no-log"]), 0)
        self.assertEqual(out.getvalue(), "answer")
        self.assertEqual(agentcli.parse_usage(err.getvalue()), (12, 34))


class TestParseArgs(unittest.TestCase):
    def test_options_after_the_positional_model(self):
        """契約の argv は `command` 展開後に write_args を足すので権限フラグが後ろに来る。

        （例: `agent-ollama --think off M --tools`）位置に依存しない解釈が要る。
        """
        opts = ollama_adapter.parse_args(["--think", "off", "qwen3", "--tools"])
        self.assertEqual(opts["model"], "qwen3")
        self.assertTrue(opts["tools"])
        self.assertIs(opts["think"], False)

    def test_think_accepts_on_and_off_only(self):
        self.assertIs(ollama_adapter.parse_args(["--think", "on", "m"])["think"], True)
        with self.assertRaises(ollama_adapter.ArgError):
            ollama_adapter.parse_args(["--think", "maybe", "m"])

    def test_equals_form_and_repeated_skill(self):
        opts = ollama_adapter.parse_args(["--skill=pdf", "--skill", "xlsx", "m"])
        self.assertEqual(opts["skills"], ["pdf", "xlsx"])

    def test_optional_valued_status_and_follow(self):
        self.assertEqual(ollama_adapter.parse_args(["--status"])["log_target"], None)
        self.assertEqual(ollama_adapter.parse_args(["--status", "/x.jsonl"])["log_target"],
                         "/x.jsonl")
        # 値を取らずに次のオプションが続く形も壊れない
        opts = ollama_adapter.parse_args(["--follow", "--no-log"])
        self.assertTrue(opts["follow"])
        self.assertIsNone(opts["log_target"])

    def test_unknown_option_and_extra_positional_are_errors(self):
        with self.assertRaises(ollama_adapter.ArgError):
            ollama_adapter.parse_args(["--nope", "m"])
        with self.assertRaises(ollama_adapter.ArgError):
            ollama_adapter.parse_args(["m1", "m2"])
        with self.assertRaises(ollama_adapter.ArgError):
            ollama_adapter.parse_args(["--think"])

    def test_defaults(self):
        opts = ollama_adapter.parse_args(["m"])
        self.assertFalse(opts["tools"])
        self.assertFalse(opts["tui"])
        self.assertIsNone(opts["think"])
        self.assertTrue(opts["skills_enabled"])
        self.assertEqual(opts["max_rounds"], ollama_loop.DEFAULT_MAX_ROUNDS)


class TestMainModes(unittest.TestCase):
    def test_help_and_missing_model(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ollama_adapter.main(["--help"]), 0)
        self.assertIn("使い方", out.getvalue())
        with redirect_stderr(err):
            self.assertEqual(ollama_adapter.main([]), 2)
        self.assertIn("モデルを指定", err.getvalue())

    def test_bad_option_returns_2_with_usage(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(ollama_adapter.main(["--nope"]), 2)
        self.assertIn("知らないオプション", err.getvalue())

    def test_tools_mode_runs_the_loop(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_loop, "run_loop", return_value={
                "text": "できました", "tokens_in": 5, "tokens_out": 6,
                "rounds": 2, "status": "done"}) as loop, \
                mock.patch.object(ollama_adapter.ollama_loop, "run_plain") as plain, \
                mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("やって")), \
                redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(ollama_adapter.main(["qwen3", "--tools", "--no-log"]), 0)
        self.assertEqual(out.getvalue(), "できました")
        self.assertEqual(loop.call_count, 1)
        self.assertEqual(plain.call_count, 0)
        self.assertEqual(agentcli.parse_usage(err.getvalue()), (5, 6))

    def test_stall_is_reported_and_returns_1(self):
        """無進捗の打ち切りは、定義の errors で transient 分類に載る文言で出す。"""
        err = io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_loop, "run_plain",
                               side_effect=ollama_loop.StallError("応答が停止しました: …")), \
                mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("hi")), \
                redirect_stderr(err):
            self.assertEqual(ollama_adapter.main(["qwen3", "--no-log"]), 1)
        message = err.getvalue()
        self.assertIn("応答が停止しました", message)
        spec = agentcli.load_cli("ollama")
        self.assertEqual(agentcli.classify_error(spec, message)[0], "transient")

    def test_connection_failure_classifies_as_env(self):
        err = io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_loop, "run_plain",
                               side_effect=ollama_loop.OllamaError(
                                   "ollama に接続できません: Connection refused")), \
                mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("hi")), \
                redirect_stderr(err):
            self.assertEqual(ollama_adapter.main(["qwen3", "--no-log"]), 1)
        spec = agentcli.load_cli("ollama")
        self.assertEqual(agentcli.classify_error(spec, err.getvalue())[0], "env")

    def test_missing_explicit_skill_is_env_classified(self):
        err = io.StringIO()
        with mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("hi")), \
                redirect_stderr(err):
            self.assertEqual(
                ollama_adapter.main(["qwen3", "--no-log", "--skill", "no-such-skill-xyz"]), 1)
        spec = agentcli.load_cli("ollama")
        self.assertEqual(agentcli.classify_error(spec, err.getvalue())[0], "env")

    def test_status_mode_prints_json_without_calling_the_model(self):
        out = io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_events, "read_status",
                               return_value={"state": "running", "alive": True}) as read, \
                redirect_stdout(out):
            self.assertEqual(ollama_adapter.main(["--status", "/x.jsonl"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"state": "running", "alive": True})
        self.assertEqual(read.call_args.args[0], "/x.jsonl")


class TestContractDefinition(unittest.TestCase):
    """`agents/ollama.json` の宣言と実装の対応（契約が嘘をつかないこと）。"""

    def test_write_mode_gets_tools_and_readonly_does_not(self):
        spec = agentcli.load_cli("ollama")
        write = agentcli.headless_cmd(spec, "M", "P")["argv"]
        readonly = agentcli.headless_cmd(spec, "M", "P", readonly=True)["argv"]
        self.assertIn("--tools", write)
        self.assertNotIn("--tools", readonly, "readonly はツールを持たない = enforced が真")
        self.assertEqual(spec["readonly"], "enforced")

    def test_think_is_declared_off_in_the_definition(self):
        spec = agentcli.load_cli("ollama")
        argv = agentcli.headless_cmd(spec, "M", "P")["argv"]
        self.assertEqual(argv[argv.index("--think") + 1], "off")
        opts = ollama_adapter.parse_args(argv[1:])
        self.assertIs(opts["think"], False, "定義の argv がそのまま解釈できる")

    def test_interactive_launches_the_tui(self):
        spec = agentcli.load_cli("ollama")
        argv = agentcli.interactive_cmd(spec, "M")
        self.assertEqual(argv[:2], ["agent-ollama", "--tui"])
        self.assertNotIn("--tools", argv, "対話は安全側で始め、/tools on で人が開ける")


if __name__ == "__main__":
    unittest.main()
