from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from agentcore import agentcli, ollama_adapter, ollama_context, ollama_loop


class _NoServerMixin:
    """文脈上限の問い合わせを差し替える（テストを推論サーバから切り離す）。"""

    limit = (8192, "server")

    def setUp(self):
        patcher = mock.patch.object(ollama_adapter.ollama_context, "resolve_limit",
                                    return_value=self.limit)
        self.addCleanup(patcher.stop)
        self.resolve_limit = patcher.start()
        # main() は入口で ~/.profile を読みにいく。テストは実行環境の profile に
        # 依存させない（subprocess も起こさない）。
        profile = mock.patch.object(ollama_adapter, "load_profile_env", return_value={})
        self.addCleanup(profile.stop)
        profile.start()


class TestOllamaAdapter(_NoServerMixin, unittest.TestCase):
    def test_generate_requests_non_streaming_and_returns_usage(self):
        response = io.BytesIO(json.dumps({
            "response": "ok", "prompt_eval_count": 12, "eval_count": 34,
        }).encode())
        response.__enter__ = lambda self: self
        response.__exit__ = lambda *args: None
        with mock.patch.object(ollama_adapter.urllib.request, "urlopen", return_value=response) as call:
            result = ollama_adapter.generate("qwen3", "hello")
        sent = json.loads(call.call_args.args[0].data)
        self.assertEqual(sent, {"model": "qwen3", "prompt": "hello", "stream": False,
                                "options": {"num_predict": ollama_loop.DEFAULT_NUM_PREDICT}})
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

    def test_think_accepts_on_off_and_prompt(self):
        self.assertIs(ollama_adapter.parse_args(["--think", "on", "m"])["think"], True)
        with self.assertRaises(ollama_adapter.ArgError):
            ollama_adapter.parse_args(["--think", "maybe", "m"])

    def test_think_prompt_selects_the_system_prompt_route(self):
        opts = ollama_adapter.parse_args(["--think", "prompt", "m"])

        self.assertTrue(opts["think_prompt"])
        self.assertIsNone(opts["think"], "API フィールドは宣言しない（経路が違う）")

    def test_think_on_off_leaves_the_prompt_route_alone(self):
        for value in ("on", "off"):
            opts = ollama_adapter.parse_args(["--think", value, "m"])
            self.assertFalse(opts["think_prompt"], f"--think {value}")

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
        self.assertEqual(opts["toolset"], ollama_loop.DEFAULT_TOOLSET)
        self.assertIsNone(opts["format"])

    def test_toolset_is_optional_and_defaults_to_bash(self):
        """`--tools`（引数なし）は従来どおり bash。セット名は空白でも = でも取れる。"""
        self.assertEqual(ollama_adapter.parse_args(["m", "--tools"])["toolset"], "bash")
        self.assertEqual(ollama_adapter.parse_args(["m", "--tools", "read"])["toolset"], "read")
        self.assertEqual(ollama_adapter.parse_args(["--tools=read", "m"])["toolset"], "read")

    def test_toolset_does_not_swallow_the_positional_model(self):
        opts = ollama_adapter.parse_args(["--tools", "qwen3"])
        self.assertEqual(opts["model"], "qwen3")
        self.assertEqual(opts["toolset"], "bash")

    def test_unimplemented_toolset_is_an_explicit_error(self):
        """未実装セットはモデル名として黙って飲み込まない（原因の分かる失敗にする）。"""
        with self.assertRaises(ollama_adapter.ArgError):
            ollama_adapter.parse_args(["m", "--tools", "edit"])

    def test_format_accepts_json_and_text_only(self):
        self.assertEqual(ollama_adapter.parse_args(["--format", "json", "m"])["format"], "json")
        self.assertIsNone(ollama_adapter.parse_args(["--format", "text", "m"])["format"])
        with self.assertRaises(ollama_adapter.ArgError):
            ollama_adapter.parse_args(["--format", "yaml", "m"])


class TestContextReporting(_NoServerMixin, unittest.TestCase):
    def _run(self, argv, tokens=(4000, 100)):
        out, err = io.StringIO(), io.StringIO()

        def fake_plain(model, prompt, **kw):
            # トラッカーが実際に渡っていることも、ここで一緒に確かめる。
            kw["tracker"].observe(*tokens)
            return {"text": "answer", "tokens_in": tokens[0], "tokens_out": tokens[1]}

        with mock.patch.object(ollama_adapter.ollama_loop, "run_plain", fake_plain), \
                mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("hi")), \
                redirect_stdout(out), redirect_stderr(err):
            rc = ollama_adapter.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_agent_context_line_is_separate_from_usage(self):
        """`@agent-usage`（累計の仕事量）と文脈使用量は意味が違うので行を分ける。"""
        rc, body, err = self._run(["qwen3", "--no-log"])
        self.assertEqual(rc, 0)
        self.assertEqual(body, "answer")
        self.assertEqual(agentcli.parse_usage(err), (4000, 100))
        line = [l for l in err.splitlines() if l.startswith("@agent-context")][0]
        self.assertIn("used=4100", line)
        self.assertIn("limit=8192", line)
        self.assertIn("source=measured", line)

    def test_explicit_limit_is_passed_to_the_resolver(self):
        self._run(["qwen3", "--no-log", "--context-limit", "4096"])
        self.assertEqual(self.resolve_limit.call_args.kwargs["explicit"], 4096)

    def test_warning_is_emitted_when_close_to_the_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "run.jsonl")
            self._run(["qwen3", "--log", log, "--context-warn-pct", "50"],
                      tokens=(8000, 100))
            kinds = [json.loads(l)["kind"] for l in Path(log).read_text().splitlines()]
            status = ollama_adapter.ollama_events.read_status(log)
        self.assertIn("context_warn", kinds)
        self.assertEqual(status["context_used"], 8100)
        self.assertEqual(status["context_limit"], 8192)
        self.assertGreater(status["context_pct"], 98.0)

    def test_run_start_records_effective_options(self):
        """sampling固定が実際に送られたことを、後からセッションログで監査できる。"""
        with tempfile.TemporaryDirectory() as tmp:
            log = str(Path(tmp) / "run.jsonl")
            with mock.patch.dict(os.environ, {"AGENT_OLLAMA_OPTIONS": '{"temperature":0}'}):
                self._run(["qwen3", "--log", log])
            start = json.loads(Path(log).read_text().splitlines()[0])
        self.assertEqual(start["options"]["temperature"], 0)
        self.assertEqual(start["options"]["num_predict"], ollama_loop.DEFAULT_NUM_PREDICT)

    def test_context_query_mode_does_not_call_the_model(self):
        out = io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_loop, "run_plain") as plain, \
                redirect_stdout(out):
            rc = ollama_adapter.main(["qwen3", "--context"])
        self.assertEqual(rc, 0)
        self.assertEqual(plain.call_count, 0)
        self.assertEqual(json.loads(out.getvalue()),
                         {"model": "qwen3", "context_limit": 8192,
                          "context_limit_source": "server"})

    def test_context_query_returns_1_when_unknown(self):
        out = io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_context, "resolve_limit",
                               return_value=(0, "unknown")), redirect_stdout(out):
            self.assertEqual(ollama_adapter.main(["qwen3", "--context"]), 1)
        self.assertEqual(json.loads(out.getvalue())["context_limit"], 0)


class TestMainModes(_NoServerMixin, unittest.TestCase):
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

    def test_queue_peer_vanished_classifies_as_env(self):
        """順番待ちの相手が消えたのは一時障害ではない。

        再投入しても待つ先が居ないので、`transient` として黙って回すと同じ待ちを
        繰り返すだけになる。人がサーバを見に行くべき形＝`env` として出す。
        """
        err = io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_loop, "run_plain",
                               side_effect=ollama_loop.StallError(
                                   "順番待ちの相手が居なくなりました: queue のまま 300 秒…")), \
                mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("hi")), \
                redirect_stderr(err):
            self.assertEqual(ollama_adapter.main(["qwen3", "--no-log"]), 1)
        spec = agentcli.load_cli("ollama")
        self.assertEqual(agentcli.classify_error(spec, err.getvalue())[0], "env")

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

    def _loop_run(self, status, argv=("qwen3", "--tools", "--no-log")):
        """ツールループを status 固定で 1 回回し、(stdout, stderr) を返す。"""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_loop, "run_loop", return_value={
                "text": "途中までの報告", "tokens_in": 1, "tokens_out": 2,
                "rounds": 3, "status": status}), \
                mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("やって")), \
                redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(ollama_adapter.main(list(argv)), 0)
        return out.getvalue(), err.getvalue()

    def test_incomplete_run_appends_a_machine_readable_not_ok_envelope(self):
        """done 以外は本文の末尾に {"ok": false} を足す（途中経過を done にさせない）。

        呼び出し側（agent-flow の worker 契約）はこの封筒だけを見て未完了と判定する。
        no_command を含めるのが要点——一番起きる未完了がここから漏れていた。"""
        for status in ("no_command", "max_rounds", "context_exhausted", "tool_denied"):
            body, err = self._loop_run(status)
            self.assertTrue(body.startswith("途中までの報告"), status)
            envelope = json.loads(body[body.rindex("{"):])
            self.assertIs(envelope["ok"], False, status)
            self.assertIn(status, envelope["issues"][0], status)
            self.assertIn("@agent-note", err, status)
            self.assertIn(status, err, status)

    def test_completed_run_carries_no_envelope(self):
        body, err = self._loop_run("done")
        self.assertEqual(body, "途中までの報告")
        self.assertNotIn("@agent-note", err)

    def test_json_format_keeps_the_body_parsable_when_incomplete(self):
        # 本文全体が JSON 契約なので封筒は足さない（足すと JSON として壊れる）。
        body, err = self._loop_run("max_rounds",
                                   argv=("qwen3", "--tools", "--format", "json", "--no-log"))
        self.assertEqual(body, "途中までの報告")
        self.assertIn("@agent-note", err, "人向けの注記は形式に関係なく出す")

    def test_status_mode_prints_json_without_calling_the_model(self):
        out = io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_events, "read_status",
                               return_value={"state": "running", "alive": True}) as read, \
                redirect_stdout(out):
            self.assertEqual(ollama_adapter.main(["--status", "/x.jsonl"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"state": "running", "alive": True})
        self.assertEqual(read.call_args.args[0], "/x.jsonl")


class TestLoadProfileEnv(unittest.TestCase):
    """エンジンの非ログインシェル起動でも ~/.profile の OLLAMA_HOST / NO_PROXY が効くこと。"""

    _VARS = ("OLLAMA_HOST", "OLLAMA_API_BASE", "AGENT_OLLAMA_THINK",
             "NO_PROXY", "no_proxy", "UNRELATED_VAR")

    def _write_profile(self, tmp, body):
        path = Path(tmp) / "profile"
        path.write_text(body, encoding="utf-8")
        return str(path)

    def _clean_env(self):
        ctx = mock.patch.dict(ollama_adapter.os.environ)
        ctx.start()
        self.addCleanup(ctx.stop)
        for name in self._VARS:
            ollama_adapter.os.environ.pop(name, None)

    def test_imports_ollama_vars_when_host_is_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self._write_profile(tmp, (
                "export OLLAMA_HOST=http://10.0.0.5:11434\n"
                "export AGENT_OLLAMA_THINK=off\n"
                "export UNRELATED_VAR=nope\n"))
            self._clean_env()
            imported = ollama_adapter.load_profile_env(profile)
            self.assertEqual(ollama_adapter.os.environ["OLLAMA_HOST"],
                             "http://10.0.0.5:11434")
            self.assertEqual(ollama_adapter.os.environ["AGENT_OLLAMA_THINK"], "off")
            self.assertNotIn("UNRELATED_VAR", ollama_adapter.os.environ,
                             "OLLAMA_*/AGENT_OLLAMA_*/NO_PROXY 以外は取り込まない")
        self.assertEqual(set(imported), {"OLLAMA_HOST", "AGENT_OLLAMA_THINK"})

    def test_existing_environment_always_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self._write_profile(tmp, (
                "export OLLAMA_HOST=http://profile:11434\n"
                "export AGENT_OLLAMA_THINK=off\n"))
            self._clean_env()
            ollama_adapter.os.environ["AGENT_OLLAMA_THINK"] = "on"
            ollama_adapter.load_profile_env(profile)
            self.assertEqual(ollama_adapter.os.environ["AGENT_OLLAMA_THINK"], "on",
                             "呼び出し側の明示指定を profile で潰さない")

    def test_noop_when_fully_configured(self):
        self._clean_env()
        ollama_adapter.os.environ.update({
            "OLLAMA_HOST": "http://set:11434",
            "OLLAMA_API_BASE": "http://set:11434",
            "NO_PROXY": "set"})
        with mock.patch.object(ollama_adapter.subprocess, "run") as run:
            self.assertEqual(ollama_adapter.load_profile_env("/nonexistent"), {})
        self.assertEqual(run.call_count, 0, "構成済みなら subprocess を起こさない")

    def test_missing_or_broken_profile_is_silent(self):
        self._clean_env()
        self.assertEqual(ollama_adapter.load_profile_env("/no/such/profile"), {})
        with tempfile.TemporaryDirectory() as tmp:
            profile = self._write_profile(tmp, "this-is-not-a-command --boom\n")
            # 壊れた行があっても sh は続行し、環境の取得自体は成功する。
            # 少なくとも例外で推論を止めないことだけを保証する。
            ollama_adapter.load_profile_env(profile)

    def test_no_proxy_is_imported_and_ollama_host_is_appended(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self._write_profile(tmp, (
                "export OLLAMA_HOST=http://10.0.0.5:11434\n"
                "export NO_PROXY=intra.example\n"))
            self._clean_env()
            imported = ollama_adapter.load_profile_env(profile)
            self.assertEqual(set(imported), {"OLLAMA_HOST", "NO_PROXY"})
            for var in ("NO_PROXY", "no_proxy"):
                self.assertEqual(ollama_adapter.os.environ[var],
                                 "intra.example,10.0.0.5",
                                 "ollama のホストは NO_PROXY の両表記へ追記される")

    def test_ollama_host_is_appended_even_when_no_proxy_preexists(self):
        # 親環境が不完全な NO_PROXY を持つ（＝profile の値は取り込まれない）場合でも、
        # ollama のホストだけは確実にプロキシ対象から外れること（504 対策の本丸）。
        self._clean_env()
        ollama_adapter.os.environ.update({
            "OLLAMA_HOST": "http://10.0.0.5:11434",
            "OLLAMA_API_BASE": "http://10.0.0.5:11434",
            "NO_PROXY": "corp.local"})
        ollama_adapter.load_profile_env("/nonexistent")
        for var in ("NO_PROXY", "no_proxy"):
            self.assertEqual(ollama_adapter.os.environ[var], "corp.local,10.0.0.5")

    def test_api_base_and_host_complete_each_other(self):
        self._clean_env()
        ollama_adapter.os.environ["OLLAMA_HOST"] = "10.0.0.5:11434"
        ollama_adapter.load_profile_env("/nonexistent")
        self.assertEqual(ollama_adapter.os.environ["OLLAMA_API_BASE"],
                         "http://10.0.0.5:11434",
                         "スキーム無しの OLLAMA_HOST からでも API base を組める")

        self._clean_env()
        ollama_adapter.os.environ["OLLAMA_API_BASE"] = "http://10.0.0.6:11434"
        ollama_adapter.load_profile_env("/nonexistent")
        self.assertEqual(ollama_adapter.os.environ["OLLAMA_HOST"],
                         "http://10.0.0.6:11434")

    def test_localhost_is_excluded_when_nothing_is_configured(self):
        self._clean_env()
        ollama_adapter.load_profile_env("/nonexistent")
        self.assertEqual(ollama_adapter.os.environ["NO_PROXY"], "localhost,127.0.0.1")


class TestSkillToolsetGuard(_NoServerMixin, unittest.TestCase):
    """同梱スクリプト前提のスキル × 制限セットは黙って続けない（ツール開示設計 §6.1）。"""

    def _run(self, toolset: str, body: str):
        with mock.patch.object(ollama_adapter.ollama_skills, "expand",
                               return_value=(body, [{"name": "tool", "path": "/x/SKILL.md",
                                                     "chars": 10,
                                                     "scripts": "{skill_dir}" in body}])), \
                mock.patch.object(ollama_adapter.ollama_loop, "run_loop", return_value={
                    "text": "ok", "tokens_in": 1, "tokens_out": 1,
                    "rounds": 1, "status": "done"}):
            return ollama_adapter.run_request(
                "依頼", {**ollama_adapter.parse_args(["m", "--tools", toolset]),
                        "no_log": True})

    def test_read_toolset_with_script_bundled_skill_fails_loudly(self):
        with self.assertRaises(ollama_adapter.ollama_skills.SkillToolsetMismatch):
            self._run("read", "実行: {skill_dir}/scripts/go.py")

    def test_bash_toolset_is_unaffected(self):
        self.assertEqual(self._run("bash", "実行: {skill_dir}/scripts/go.py")["text"], "ok")

    def test_prose_skill_passes_on_the_read_toolset(self):
        self.assertEqual(self._run("read", "手順の説明だけ")["text"], "ok")


class TestProgressBeaconReachesTheAdapter(_NoServerMixin, unittest.TestCase):
    """ハーネスが置いた灯台を、ヘッドレス実行が実際に刻むこと（継ぎ目の疎通）。

    見張り（`_tl_run_watched`）と刻む側（`EventLog`）は別々に縛ってあるが、両者を結ぶのは
    環境変数 1 本なので、`run_request` まで通しておかないと「どちらも正しいのに繋がって
    いない」を取り逃がす。ヘッドレスは終わるまで stdout に何も出さないので、この 1 本が
    切れると外側からは無進捗と見分けが付かない。
    """

    def test_headless_run_marks_the_beacon_named_by_the_environment(self):
        import json as _json
        import tempfile as _tempfile
        from pathlib import Path as _Path

        with _tempfile.TemporaryDirectory() as tmp:
            beacon = _Path(tmp) / "beacon"
            with mock.patch.dict(ollama_adapter.os.environ,
                                 {"AGENT_PROGRESS_BEACON": str(beacon)}), \
                    mock.patch.object(ollama_adapter.ollama_loop, "run_plain",
                                      return_value={"text": "ok", "tokens_in": 1,
                                                    "tokens_out": 1}):
                ollama_adapter.run_request(
                    "依頼", {**ollama_adapter.parse_args(["m"]), "no_log": True})
            marked = _json.loads(beacon.read_text(encoding="utf-8"))
        # 最後に刻まれるのは run_end（＝実行の終わりまで刻み続けている）。
        self.assertEqual(marked["kind"], "run_end")


def _adapter_args(argv, expect="ollama"):
    """定義の argv から、入口が adapter へ実際に渡す部分だけを取り出す。

    定義の `command` は `agent-herd ollama …` の綴りなので、素の添字で剥がすと
    綴りが変わるたびにテストが嘘になる。入口（`herdcli.resolve`）と同じ規則で剥がして、
    「エンジンが組んだ argv を adapter がそのまま解釈できる」ことだけを見る。
    """
    from agentcore import herdcli
    sub, rest = herdcli.resolve(argv[0], argv[1:])
    assert sub == expect, f"想定と違うサブコマンドへ落ちている: {sub}"
    return rest


class TestContractDefinition(unittest.TestCase):
    """`agents/ollama*.json` の宣言と実装の対応（契約が嘘をつかないこと）。

    見るのは**このリポジトリの同梱定義**。実ホーム（`~/.agents/agents/`）には配布済みの
    古い定義が残りうるので、そちらを引くと「インストールが古いだけ」でここが落ちる。
    """

    def setUp(self):
        agentcli.clear_cache()
        self._env = {k: os.environ.get(k) for k in
                     ("KIRO_AGENTS_DIR", "AGENT_PROJECT_AGENTS_HOME", "HOME")}
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_PROJECT_AGENTS_HOME"] = str(Path(self._tmp.name) / "none")
        os.environ["HOME"] = str(Path(self._tmp.name) / "none")
        os.environ.pop("KIRO_AGENTS_DIR", None)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        agentcli.clear_cache()
        self._tmp.cleanup()

    def test_write_mode_gets_tools_and_readonly_does_not(self):
        spec = agentcli.load_cli("ollama")
        write = agentcli.headless_cmd(spec, "M", "P")["argv"]
        readonly = agentcli.headless_cmd(spec, "M", "P", readonly=True)["argv"]
        self.assertIn("--tools", write)
        self.assertNotIn("--tools", readonly, "readonly はツールを持たない = enforced が真")
        self.assertEqual(spec["readonly"], "enforced")

    def test_think_is_off_for_every_headless_role(self):
        """think はヘッドレスの全役割で off。人が待てる TUI にだけ残す。

        思考自体は thinking フィールドで本文と分離済みなので成果物は汚れない——問題は
        費用対効果で、実測（2026-08-10・ログ 236 本）では on の 3 経路が全滅した。
        write は 1 ラウンドが思考だけで 7700 トークン・12 分（p90 942 秒に対し
        `agent_timeout` は 600 秒）、readonly は中央値 1000 秒、`--format` 併用は
        文法が thinking から掛かって本文が空（39/39 件）。思考が品質に変換される証拠が
        1 件も取れていないので、on へ戻すならオフライン再生で先に示すこと。"""
        spec = agentcli.load_cli("ollama")
        write = agentcli.headless_cmd(spec, "M", "P")["argv"]
        readonly = agentcli.headless_cmd(spec, "M", "P", readonly=True)["argv"]
        self.assertEqual(write[write.index("--think") + 1], "off")
        self.assertEqual(readonly[readonly.index("--think") + 1], "off")
        opts = ollama_adapter.parse_args(_adapter_args(write))
        self.assertIs(opts["think"], False, "定義の argv がそのまま解釈できる")
        self.assertEqual(opts["toolset"], "bash")
        self.assertEqual(opts["max_rounds"], 12, "write の予算は絞ってある（read は 30）")
        self.assertIs(ollama_adapter.parse_args(_adapter_args(readonly))["think"], False)
        self.assertEqual(agentcli.interactive_cmd(spec, "M")[
            agentcli.interactive_cmd(spec, "M").index("--think") + 1], "on")

    def test_json_variant_forces_the_grammar_and_carries_no_tools(self):
        spec = agentcli.load_cli("ollama-json")
        argv = agentcli.headless_cmd(spec, "M", "P")["argv"]
        opts = ollama_adapter.parse_args(_adapter_args(argv))
        self.assertEqual(opts["format"], "json")
        self.assertFalse(opts["tools"], "JSON しか出せない状態でツールループの規約は成立しない")
        self.assertEqual(spec["readonly"], "enforced")

    def test_list_variant_asks_for_a_top_level_array(self):
        # `--format json` はトップレベルをオブジェクトに固定するので、配列契約（split）は
        # スキーマを渡す起動形で受ける。
        spec = agentcli.load_cli("ollama-list")
        opts = ollama_adapter.parse_args(_adapter_args(agentcli.headless_cmd(spec, "M", "P")["argv"]))
        self.assertEqual(opts["format"], "array")
        self.assertFalse(opts["tools"])
        self.assertEqual(ollama_loop.format_value("array"),
                         {"type": "array", "items": {"type": "string"}})

    def test_thinking_list_variant_leaves_the_grammar_unconstrained(self):
        """Gemma split は意味的な完全被覆を考えられるよう、thinking と grammar を両立させない。"""
        spec = agentcli.load_cli("ollama-list-thinking")
        opts = ollama_adapter.parse_args(_adapter_args(agentcli.headless_cmd(spec, "M", "P")["argv"]))
        self.assertIs(opts["think"], True)
        self.assertIsNone(opts["format"])
        self.assertFalse(opts["tools"])

    def test_read_variant_carries_the_read_toolset(self):
        spec = agentcli.load_cli("ollama-read")
        opts = ollama_adapter.parse_args(_adapter_args(agentcli.headless_cmd(spec, "M", "P")["argv"]))
        self.assertTrue(opts["tools"])
        self.assertEqual(opts["toolset"], "read")
        readonly = agentcli.headless_cmd(spec, "M", "P", readonly=True)["argv"]
        self.assertNotIn("--tools", readonly, "readonly は道具ゼロのまま = enforced が真")

    def test_context_exhausted_is_classified_as_env_not_transient(self):
        """同じ壁に同じ時間を掛けて再試行させない（transient にしないのが要点）。"""
        for name in ("ollama", "ollama-json", "ollama-list", "ollama-list-thinking",
                     "ollama-read"):
            spec = agentcli.load_cli(name)
            triage = agentcli.classify_error(
                spec, "@agent-note 途中で打ち切りました（context_exhausted）。")
            self.assertIsNotNone(triage, name)
            self.assertEqual(triage[0], "env", name)

    def test_interactive_launches_the_tui(self):
        spec = agentcli.load_cli("ollama")
        argv = agentcli.interactive_cmd(spec, "M")
        self.assertEqual(argv[:3], ["agent-herd", "ollama", "--tui"])
        self.assertNotIn("--tools", argv, "対話は安全側で始め、/tools on で人が開ける")


if __name__ == "__main__":
    unittest.main()
