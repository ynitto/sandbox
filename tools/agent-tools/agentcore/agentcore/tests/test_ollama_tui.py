from __future__ import annotations

import io
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from agentcore import ollama_tui


class _Tty(io.StringIO):
    """isatty() が真になる出力先（ステータス行の描画経路を通すため）。"""

    def isatty(self):
        return True


class TestEventLine(unittest.TestCase):
    def test_progress_and_heartbeat_do_not_scroll(self):
        """毎秒の進捗を行として流すと、肝心のラウンド遷移がログに埋もれる。"""
        for kind in ("llm_progress", "llm_heartbeat", "llm_start"):
            self.assertEqual(ollama_tui.event_line({"kind": kind, "ts": 1.0}), "")

    def test_rounds_tools_and_stall_are_readable(self):
        self.assertIn("$ ls", ollama_tui.event_line(
            {"kind": "tool_exec", "ts": 1.0, "round": 2, "command": "ls"}))
        self.assertIn("exit 0", ollama_tui.event_line(
            {"kind": "tool_result", "ts": 1.0, "round": 2, "exit_code": 0,
             "duration_sec": 0.3, "output_chars": 12}))
        self.assertIn("無進捗", ollama_tui.event_line(
            {"kind": "stall", "ts": 1.0, "phase": "decode", "waiting_sec": 180}))
        self.assertIn("tok/s", ollama_tui.event_line(
            {"kind": "llm_end", "ts": 1.0, "round": 1, "duration_sec": 61,
             "tokens_in": 1832, "tokens_out": 210, "tokens_per_sec": 7.2}))

    def test_multiline_command_is_folded_into_one_line(self):
        line = ollama_tui.event_line(
            {"kind": "tool_exec", "ts": 1.0, "round": 1, "command": "a\nb"})
        self.assertNotIn("\n", line)


class TestRenderer(unittest.TestCase):
    def test_never_uses_the_alternate_screen(self):
        """全画面へ切り替えると tmux capture-pane から中身が読めなくなる（設計上の制約）。"""
        out = _Tty()
        renderer = ollama_tui.Renderer(out=out, use_rich=False)
        for event in [
            {"kind": "run_start", "ts": 1.0, "model": "qwen3", "mode": "tools"},
            {"kind": "round_start", "ts": 2.0, "round": 1, "rounds_max": 3},
            {"kind": "llm_heartbeat", "ts": 3.0, "round": 1, "phase": "prefill",
             "waiting_sec": 12.0},
            {"kind": "tool_exec", "ts": 4.0, "round": 1, "command": "ls"},
            {"kind": "run_end", "ts": 5.0, "status": "done", "rounds": 1,
             "tokens_in": 1, "tokens_out": 2, "duration_sec": 3},
        ]:
            renderer.event(event)
        text = out.getvalue()
        self.assertNotIn("\x1b[?1049h", text, "alternate screen へ入らない")
        self.assertNotIn("\x1b[2J", text, "画面全消去もしない")
        self.assertIn("$ ls", text)

    def test_plain_output_has_no_escape_codes(self):
        out = io.StringIO()          # 非 tty（ログへのリダイレクト等）
        renderer = ollama_tui.Renderer(out=out, use_rich=False)
        renderer.event({"kind": "round_start", "ts": 1.0, "round": 1})
        renderer.event({"kind": "llm_heartbeat", "ts": 2.0, "phase": "prefill",
                        "waiting_sec": 1.0})
        self.assertNotIn("\x1b", out.getvalue(), "非 tty ではエスケープを出さない")

    def test_status_line_shows_liveness_while_waiting(self):
        out = _Tty()
        renderer = ollama_tui.Renderer(out=out, use_rich=False)
        renderer.event({"kind": "run_start", "ts": 1.0, "model": "m", "mode": "plain"})
        renderer.event({"kind": "llm_heartbeat", "ts": 2.0, "round": 1, "phase": "prefill",
                        "waiting_sec": 42.0})
        text = out.getvalue()
        self.assertIn("prefill", text)
        self.assertIn("42", text, "沈黙の長さが画面に出る（固まっていないことの提示）")


class TestFollow(unittest.TestCase):
    def test_renders_an_existing_log_until_the_terminal_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            path.write_text("".join(json.dumps(e) + "\n" for e in [
                {"ts": 1.0, "kind": "run_start", "model": "m", "mode": "plain"},
                {"ts": 2.0, "kind": "tool_exec", "round": 1, "command": "ls"},
                {"ts": 3.0, "kind": "run_end", "status": "done", "rounds": 1},
            ]), encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(ollama_tui.sys, "stderr", io.StringIO()):
                rc = ollama_tui.follow(str(path), out=out)
        self.assertEqual(rc, 0)
        self.assertIn("$ ls", out.getvalue())

    def test_no_log_is_an_error_not_a_hang(self):
        with mock.patch.object(ollama_tui.ollama_events, "latest_log_path",
                                        return_value=None), \
                mock.patch.object(ollama_tui.sys, "stderr", io.StringIO()):
            self.assertEqual(ollama_tui.follow(None, out=io.StringIO()), 1)


class TestRepl(unittest.TestCase):
    def _run(self, script: str, runner=None):
        calls: "list[str]" = []

        def default_runner(prompt, **_kw):
            calls.append(prompt)
            return "本文です"

        out = io.StringIO()
        rc = ollama_tui.repl(runner or default_runner, model="qwen3", tools=False,
                             out=out, in_=io.StringIO(script))
        return rc, out.getvalue(), calls

    def test_local_commands_do_not_reach_the_model(self):
        rc, text, calls = self._run("/help\n/tools on\n/think off\n/model x\n/quit\n")
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [], "ローカルコマンドは LLM へ送らない")
        self.assertIn("ローカルコマンド", text)
        self.assertIn("tools=on", text)
        self.assertIn("think=off", text)
        self.assertIn("model=x", text)

    def test_prompt_is_sent_and_body_printed(self):
        rc, text, calls = self._run("直近の変更を要約\n/quit\n")
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["直近の変更を要約"])
        self.assertIn("本文です", text)

    def test_eof_ends_the_session(self):
        rc, _text, _calls = self._run("")
        self.assertEqual(rc, 0)

    def test_runner_failure_is_shown_and_the_loop_continues(self):
        def boom(_prompt, **_kw):
            raise RuntimeError("推論に失敗")
        rc, text, _calls = self._run("やって\n/quit\n", runner=boom)
        self.assertEqual(rc, 0)
        self.assertIn("推論に失敗", text)

    def test_skills_listing_is_local_only(self):
        _rc, text, calls = self._run("/skills\n/quit\n")
        self.assertEqual(calls, [])
        self.assertTrue(text.strip(), "何かは表示される（一覧か、無いという案内）")


if __name__ == "__main__":
    unittest.main()
