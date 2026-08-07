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


class TestContextDisplay(unittest.TestCase):
    def test_context_text_shortens_and_omits_unknown_limits(self):
        self.assertEqual(ollama_tui.context_text(4200, 8192, 51.3), "ctx 4.2k/8.2k (51%)")
        self.assertEqual(ollama_tui.context_text(120, 0), "ctx 120",
                         "上限を知らないなら割合を騙らない")

    def test_llm_end_line_shows_context(self):
        line = ollama_tui.event_line({
            "kind": "llm_end", "ts": 1.0, "round": 1, "duration_sec": 3,
            "tokens_in": 4000, "tokens_out": 100, "tokens_per_sec": 7.2,
            "context_used": 4100, "context_limit": 8192, "context_pct": 50.0})
        self.assertIn("ctx 4.1k/8.2k (50%)", line)

    def test_warn_and_exhausted_lines_are_readable(self):
        warn = ollama_tui.event_line({
            "kind": "context_warn", "ts": 1.0, "round": 2, "context_used": 7500,
            "context_limit": 8192, "context_pct": 91.5, "context_source": "measured"})
        self.assertIn("上限に近づいています", warn)
        exhausted = ollama_tui.event_line({
            "kind": "context_exhausted", "ts": 1.0, "round": 3, "context_used": 8100,
            "context_limit": 8192, "context_pct": 98.9})
        self.assertIn("打ち切りました", exhausted)

    def test_status_line_carries_context(self):
        out = _Tty()
        renderer = ollama_tui.Renderer(out=out, use_rich=False)
        renderer.event({"kind": "run_start", "ts": 1.0, "model": "m", "mode": "tools"})
        renderer.event({"kind": "llm_end", "ts": 2.0, "round": 1, "duration_sec": 1,
                        "tokens_in": 4000, "tokens_out": 100, "tokens_per_sec": 7.2,
                        "context_used": 4100, "context_limit": 8192, "context_pct": 50.0})
        renderer.event({"kind": "llm_heartbeat", "ts": 3.0, "round": 2, "phase": "prefill",
                        "waiting_sec": 9.0})
        self.assertIn("ctx 4.1k/8.2k", out.getvalue())


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


class TestCompletions(unittest.TestCase):
    """Tab 補完の候補（ローカルコマンド / スキル名 / on|off）。"""

    def test_local_commands_are_offered_for_a_leading_slash(self):
        self.assertIn("/help", ollama_tui.completions("/he", "/he"))
        offered = ollama_tui.completions("/", "/")
        for name in ("/help", "/keys", "/quit", "/status"):
            self.assertIn(name, offered)

    def test_on_off_is_offered_after_a_toggle_command(self):
        self.assertEqual(ollama_tui.completions("/tools ", ""), ["on", "off"])
        self.assertEqual(ollama_tui.completions("/think o", "o"), ["on", "off"])

    def test_skill_names_join_the_same_candidates(self):
        with mock.patch.object(ollama_tui.ollama_skills, "list_skills",
                               return_value=[("pdf", Path("/s/pdf.md"))]):
            self.assertIn("/pdf", ollama_tui.completions("/p", "/p"))

    def test_a_slash_inside_the_prompt_body_offers_nothing(self):
        """本文中の `/`（パス・日付）で候補を出すと Tab がただの邪魔になる。"""
        self.assertEqual(ollama_tui.completions("要約して docs/", "docs/"), [])
        self.assertEqual(ollama_tui.completions("hello /he", "/he"), [])

    def test_unreadable_skills_do_not_break_completion(self):
        with mock.patch.object(ollama_tui.ollama_skills, "list_skills",
                               side_effect=OSError("boom")):
            self.assertIn("/help", ollama_tui.completions("/h", "/h"))


class TestLineReader(unittest.TestCase):
    def test_non_tty_falls_back_to_plain_reading(self):
        """非 tty で編集用のエスケープを吐くと capture-pane / テストの比較が壊れる。"""
        out, in_ = io.StringIO(), io.StringIO("ひとつめ\n")
        with mock.patch.object(ollama_tui, "_readline", mock.MagicMock()) as rl:
            reader = ollama_tui.LineReader(out, in_)
            self.assertFalse(reader.enabled)
            self.assertEqual(reader.read("> "), "ひとつめ\n")
            self.assertIsNone(reader.read("> "), "読み切ったら EOF")
            reader.close()
        self.assertEqual(rl.set_completer.call_count, 0, "readline を触らない")
        self.assertEqual(out.getvalue(), "> > ", "プロンプトは素で出す")

    def _tty_streams(self):
        stdin, stdout = _Tty(), _Tty()
        return mock.patch.object(ollama_tui.sys, "stdin", stdin), \
            mock.patch.object(ollama_tui.sys, "stdout", stdout), stdin, stdout

    def test_a_real_terminal_enables_editing_and_completion(self):
        in_patch, out_patch, stdin, stdout = self._tty_streams()
        rl = mock.MagicMock(__doc__="GNU readline")
        with in_patch, out_patch, mock.patch.object(ollama_tui, "_readline", rl):
            reader = ollama_tui.LineReader(stdout, stdin)
            self.assertTrue(reader.enabled)
        self.assertEqual(rl.set_completer.call_args_list[0].args[0], reader._complete)
        bound = " ".join(c.args[0] for c in rl.parse_and_bind.call_args_list)
        self.assertIn("tab: complete", bound)
        # 語の区切りが既定のままだと `/he` の補完対象が `he` になり候補が出ない。
        self.assertEqual(rl.set_completer_delims.call_args.args[0], " \t\n")

    def test_libedit_uses_its_own_bind_syntax(self):
        """macOS 標準の readline 互換実装は `tab: complete` を解さない。"""
        in_patch, out_patch, stdin, stdout = self._tty_streams()
        rl = mock.MagicMock(__doc__="libedit wrapper")
        with in_patch, out_patch, mock.patch.object(ollama_tui, "_readline", rl):
            ollama_tui.LineReader(stdout, stdin)
        bound = " ".join(c.args[0] for c in rl.parse_and_bind.call_args_list)
        self.assertIn("bind ^I rl_complete", bound)

    def test_env_switch_turns_editing_off(self):
        in_patch, out_patch, stdin, stdout = self._tty_streams()
        with in_patch, out_patch, mock.patch.object(ollama_tui, "_readline", mock.MagicMock()), \
                mock.patch.dict(ollama_tui.os.environ, {"AGENT_OLLAMA_NO_READLINE": "1"}):
            self.assertFalse(ollama_tui.LineReader(stdout, stdin).enabled)

    def test_setup_failure_degrades_to_plain_reading(self):
        """端末や実装差で構成に失敗しても、入力が読めなくなるよりはよい。"""
        in_patch, out_patch, stdin, stdout = self._tty_streams()
        rl = mock.MagicMock(__doc__="GNU readline")
        rl.set_completer_delims.side_effect = RuntimeError("no")
        with in_patch, out_patch, mock.patch.object(ollama_tui, "_readline", rl):
            self.assertFalse(ollama_tui.LineReader(stdout, stdin).enabled)

    def test_history_is_loaded_and_saved_around_the_session(self):
        in_patch, out_patch, stdin, stdout = self._tty_streams()
        rl = mock.MagicMock(__doc__="GNU readline")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "tui-history"
            with in_patch, out_patch, mock.patch.object(ollama_tui, "_readline", rl), \
                    mock.patch.dict(ollama_tui.os.environ,
                                    {"AGENT_OLLAMA_HISTORY": str(path)}):
                reader = ollama_tui.LineReader(stdout, stdin)
                reader.close()
            self.assertTrue(path.parent.is_dir(), "置き場は書く前に作る")
        self.assertEqual(rl.read_history_file.call_args.args[0], str(path))
        self.assertEqual(rl.write_history_file.call_args.args[0], str(path))

    def test_missing_history_file_is_not_an_error(self):
        in_patch, out_patch, stdin, stdout = self._tty_streams()
        rl = mock.MagicMock(__doc__="GNU readline")
        rl.read_history_file.side_effect = FileNotFoundError
        with in_patch, out_patch, mock.patch.object(ollama_tui, "_readline", rl):
            self.assertTrue(ollama_tui.LineReader(stdout, stdin).enabled, "初回起動でも動く")

    def test_history_path_follows_the_env_override(self):
        with mock.patch.dict(ollama_tui.os.environ, {"AGENT_OLLAMA_HISTORY": "~/hist"}):
            self.assertEqual(ollama_tui.history_path(), Path.home() / "hist")
        with mock.patch.dict(ollama_tui.os.environ, {}, clear=False):
            ollama_tui.os.environ.pop("AGENT_OLLAMA_HISTORY", None)
            # ログ（gc の対象）とは別の場所へ置く: 履歴は人の入力で、証跡ではない。
            self.assertNotIn(ollama_tui.ollama_events.log_dir(),
                             ollama_tui.history_path().parents)


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

    def test_ctx_command_is_local_and_reports_the_last_observation(self):
        with mock.patch.object(ollama_tui.ollama_events, "read_status", return_value={
                "context_used": 4100, "context_limit": 8192, "context_pct": 50.0,
                "context_source": "measured"}):
            _rc, text, calls = self._run("/ctx\n/quit\n")
        self.assertEqual(calls, [], "文脈の確認で LLM を呼ばない")
        self.assertIn("ctx 4.1k/8.2k (50%)", text)

    def test_ctx_command_without_observation_says_so(self):
        with mock.patch.object(ollama_tui.ollama_events, "read_status", return_value={}):
            _rc, text, _calls = self._run("/ctx\n/quit\n")
        self.assertIn("まだ", text)

    def test_skills_listing_is_local_only(self):
        _rc, text, calls = self._run("/skills\n/quit\n")
        self.assertEqual(calls, [])
        self.assertTrue(text.strip(), "何かは表示される（一覧か、無いという案内）")

    def test_keys_command_lists_the_editing_keys(self):
        _rc, text, calls = self._run("/keys\n/quit\n")
        self.assertEqual(calls, [], "キーの確認で LLM を呼ばない")
        for key in ("Ctrl-R", "Tab", "Ctrl-C", "~/.inputrc"):
            self.assertIn(key, text)
        self.assertIn("行編集が無効です", text,
                      "非 tty では効かないことを黙らずに言う")

    def test_ctrl_c_while_typing_cancels_the_line_only(self):
        """入力中の Ctrl-C は行を捨てるだけ（shell と同じ）。終了ではない。"""
        reads = [KeyboardInterrupt(), "やって", "/quit"]

        class _Reader:
            enabled = True

            def read(self, _prompt):
                value = reads.pop(0)
                if isinstance(value, BaseException):
                    raise value
                return value

            def close(self):
                pass

        calls = []
        out = io.StringIO()
        with mock.patch.object(ollama_tui, "LineReader", lambda *_a, **_k: _Reader()):
            rc = ollama_tui.repl(lambda prompt, **_kw: calls.append(prompt) or "本文",
                                 model="m", tools=False, out=out, in_=io.StringIO())
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["やって"], "取り消した行は送られず、次の行は通る")

    def test_reader_is_closed_even_when_the_runner_explodes(self):
        """履歴の保存は終わり方に依らない（落ちたセッションの入力も残す）。"""
        closed = []

        class _Reader:
            enabled = False

            def __init__(self):
                self._lines = iter(["やって", None])

            def read(self, _prompt):
                return next(self._lines)

            def close(self):
                closed.append(True)

        def boom(_prompt, **_kw):
            raise RuntimeError("推論に失敗")

        with mock.patch.object(ollama_tui, "LineReader", lambda *_a, **_k: _Reader()):
            rc = ollama_tui.repl(boom, model="m", tools=False, out=io.StringIO(),
                                 in_=io.StringIO())
        self.assertEqual(rc, 0)
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
