from __future__ import annotations

import json
import os
import threading
import time
import unittest
import urllib.error
from unittest import mock

from agentcore import ollama_loop


class _FakeResponse:
    """行を少しずつ返す偽の HTTP 応答。

    `block_before_first` で prefill（最初のトークンまでの沈黙）を、`block_after` で
    生成が止まる状況を再現する。close() で待ちを解く（本体の打ち切り経路を通すため）。
    """

    def __init__(self, lines, *, block_before_first: float = 0.0,
                 block_after: float = 0.0, delay: float = 0.0) -> None:
        self.lines = list(lines)
        self.block_before_first = block_before_first
        self.block_after = block_after
        self.delay = delay
        self.closed = threading.Event()

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.closed.is_set():
            time.sleep(0.01)

    def __iter__(self):
        self._sleep(self.block_before_first)
        for line in self.lines:
            if self.closed.is_set():
                return
            self._sleep(self.delay)
            yield line if isinstance(line, bytes) else json.dumps(line).encode() + b"\n"
        self._sleep(self.block_after)

    def close(self):
        self.closed.set()


def _patch_urlopen(response):
    return mock.patch.object(ollama_loop.urllib.request, "urlopen",
                             lambda req, timeout=None: response)


def _gen_lines(text: str, tokens_in: int = 11, tokens_out: int = 3):
    chunks = [{"response": ch, "done": False} for ch in text]
    chunks.append({"response": "", "done": True, "done_reason": "stop",
                   "prompt_eval_count": tokens_in, "eval_count": tokens_out})
    return chunks


class TestStreamCall(unittest.TestCase):
    def test_aggregates_text_and_measured_usage(self):
        events = []
        with _patch_urlopen(_FakeResponse(_gen_lines("あい"))):
            result = ollama_loop.run_plain(
                "qwen3", "hello", emit=lambda kind, **f: events.append((kind, f)),
                heartbeat=0.05)
        self.assertEqual(result["text"], "あい")
        self.assertEqual((result["tokens_in"], result["tokens_out"]), (11, 3))
        kinds = [k for k, _f in events]
        self.assertEqual(kinds[0], "llm_start")
        self.assertEqual(kinds[-1], "llm_end")

    def test_slow_prefill_is_not_killed(self):
        """**要件の核**: 最初のトークンまでの沈黙は既定で無制限（遅さは異常ではない）。

        stall_timeout を極端に短くしても、prefill 中は打ち切らない。
        """
        response = _FakeResponse(_gen_lines("あ"), block_before_first=0.4)
        with _patch_urlopen(response):
            result = ollama_loop.run_plain(
                "qwen3", "hello", stall_timeout=0.1, first_token_timeout=0.0, heartbeat=0.05)
        self.assertEqual(result["text"], "あ")

    def test_heartbeats_prove_liveness_during_prefill(self):
        events = []
        response = _FakeResponse(_gen_lines("あ"), block_before_first=0.3)
        with _patch_urlopen(response):
            ollama_loop.run_plain("qwen3", "hello", stall_timeout=0.0,
                                  emit=lambda kind, **f: events.append((kind, f)),
                                  heartbeat=0.05)
        beats = [f for k, f in events if k == "llm_heartbeat"]
        self.assertTrue(beats, "沈黙中も heartbeat が出る")
        self.assertEqual(beats[0]["phase"], "prefill")
        self.assertGreater(beats[-1]["waiting_sec"], 0.0)

    def test_stall_after_first_token_raises(self):
        events = []
        response = _FakeResponse(_gen_lines("あ")[:1], block_after=5.0)
        with _patch_urlopen(response):
            with self.assertRaises(ollama_loop.StallError) as caught:
                ollama_loop.run_plain(
                    "qwen3", "hello", stall_timeout=0.2, heartbeat=0.05,
                    emit=lambda kind, **f: events.append((kind, f)))
        self.assertIn("無進捗", str(caught.exception))
        self.assertTrue(response.closed.is_set(), "打ち切り時に応答を閉じる")
        self.assertIn("stall", [k for k, _f in events])

    def test_prefill_timeout_can_be_enabled(self):
        response = _FakeResponse(_gen_lines("あ"), block_before_first=5.0)
        with _patch_urlopen(response):
            with self.assertRaises(ollama_loop.StallError):
                ollama_loop.run_plain("qwen3", "hello", first_token_timeout=0.2,
                                      heartbeat=0.05)

    def test_connection_failure_is_reported_as_ollama_error(self):
        def boom(_req, timeout=None):
            raise urllib.error.URLError("Connection refused")
        with mock.patch.object(ollama_loop.urllib.request, "urlopen", boom):
            with self.assertRaises(ollama_loop.OllamaError) as caught:
                ollama_loop.run_plain("qwen3", "hello", heartbeat=0.05)
        self.assertIn("接続できません", str(caught.exception))

    def test_error_chunk_is_reported(self):
        with _patch_urlopen(_FakeResponse([{"error": "model not found"}])):
            with self.assertRaises(ollama_loop.OllamaError) as caught:
                ollama_loop.run_plain("qwen3", "hello", heartbeat=0.05)
        self.assertIn("model not found", str(caught.exception))

    def test_llm_end_carries_context_when_a_tracker_is_given(self):
        from agentcore import ollama_context
        events = []
        tracker = ollama_context.ContextTracker(limit=100)
        with _patch_urlopen(_FakeResponse(_gen_lines("あい", tokens_in=11, tokens_out=3))):
            ollama_loop.run_plain("qwen3", "hello", tracker=tracker, heartbeat=0.05,
                                  emit=lambda kind, **f: events.append((kind, f)))
        end = [f for k, f in events if k == "llm_end"][0]
        self.assertEqual(end["context_used"], 14)
        self.assertEqual(end["context_limit"], 100)
        self.assertEqual(end["context_pct"], 14.0)

    def test_progress_reports_thinking_so_long_thought_is_not_read_as_a_hang(self):
        """think 有効時、本文が始まる前の進捗に thinking の量を載せる。

        載せないと `tokens_out=0 / tokens_per_sec=0.0` が延々と並び、**長考と停止が
        見分けられない**——実際に 6500 トークン思考中の実行をハングと誤読した。
        停滞の判定自体は last_progress（thinking も進捗に数える）が持つので変えない。"""
        events = []
        chunks = [{"thinking": "考", "done": False} for _ in range(3)]
        chunks.append({"response": "", "done": True, "done_reason": "stop",
                       "prompt_eval_count": 11, "eval_count": 0})
        with _patch_urlopen(_FakeResponse(chunks)):
            ollama_loop.run_plain("qwen3", "hello", think=True, heartbeat=0.05,
                                  emit=lambda kind, **f: events.append((kind, f)))
        progress = [f for k, f in events if k == "llm_progress"]
        self.assertTrue(progress, "思考だけでも進捗イベントは出る")
        self.assertEqual(progress[-1]["tokens_out"], 0, "本文はまだ 0")
        self.assertGreater(progress[-1]["thinking_chars"], 0, "思考量が見える")
        end = [f for k, f in events if k == "llm_end"][0]
        self.assertEqual(end["thinking_chars"], 3)

    def test_think_and_options_are_sent_only_when_declared(self):
        captured = {}

        def capture(req, timeout=None):
            captured.update(json.loads(req.data))
            return _FakeResponse(_gen_lines("あ"))

        with mock.patch.object(ollama_loop.urllib.request, "urlopen", capture):
            ollama_loop.run_plain("qwen3", "hello", heartbeat=0.05)
        self.assertNotIn("think", captured, "未宣言なら送らない（モデル既定に委ねる）")

        with mock.patch.object(ollama_loop.urllib.request, "urlopen", capture):
            ollama_loop.run_plain("qwen3", "hello", think=False, heartbeat=0.05)
        self.assertIs(captured["think"], False)
        self.assertTrue(captured["stream"])

    def test_format_forces_think_off(self):
        """`format` 指定時は think off を**明示**する。

        併用すると文法制約が thinking から効き、答えが thinking に入って本文が空になる。
        未宣言はモデル既定（思考モデルでは on）に落ちるので、送らないだけでは足りない。
        """
        captured = {}

        def capture(req, timeout=None):
            captured.update(json.loads(req.data))
            return _FakeResponse(_gen_lines("あ"))

        for think in (True, None):
            with mock.patch.object(ollama_loop.urllib.request, "urlopen", capture):
                ollama_loop.run_plain("qwen3", "hello", think=think, fmt="json", heartbeat=0.05)
            self.assertIs(captured["think"], False, f"think={think} でも off を明示する")
            self.assertEqual(captured["format"], "json")


class TestStallReturnsPromptly(unittest.TestCase):
    """実ソケット相手の回帰テスト: 打ち切りが**即座に戻る**こと。

    偽の応答オブジェクトでは踏めない事故が 1 つある。`http.client` の応答を
    `close()` で閉じようとすると `BufferedReader` のロックを取りに行き、そのロックは
    受信でブロックしているリーダースレッドが握っているため、打ち切った側が固まる。
    「無進捗を検知したのにプロセスが終われない」という最悪の形なので、実物の HTTP で守る。
    """

    def setUp(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                self.wfile.write(json.dumps({"response": "あ", "done": False}).encode() + b"\n")
                self.wfile.flush()
                time.sleep(5)           # 1 トークン出したあと黙る

        # ThreadingHTTPServer + daemon_threads にする（単スレッドだと、黙っている
        # ハンドラが終わるまで shutdown() が返らずテストが遅くなる）。
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_returns_within_seconds_after_stall(self):
        started = time.monotonic()
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": self.host}):
            with self.assertRaises(ollama_loop.StallError):
                ollama_loop.run_plain("m", "hi", stall_timeout=0.5, heartbeat=0.1)
        self.assertLess(time.monotonic() - started, 10.0,
                        "打ち切り後に固まっている（close() のロック待ち）")


class TestResolveThink(unittest.TestCase):
    def test_explicit_wins_over_env(self):
        with mock.patch.dict(os.environ, {"AGENT_OLLAMA_THINK": "on"}):
            self.assertIs(ollama_loop.resolve_think(False), False)

    def test_env_is_used_when_not_given(self):
        with mock.patch.dict(os.environ, {"AGENT_OLLAMA_THINK": "off"}):
            self.assertIs(ollama_loop.resolve_think(None), False)
        with mock.patch.dict(os.environ, {"AGENT_OLLAMA_THINK": "on"}):
            self.assertIs(ollama_loop.resolve_think(None), True)

    def test_unset_declares_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_OLLAMA_THINK", None)
            self.assertIsNone(ollama_loop.resolve_think(None))


class TestLoadOptions(unittest.TestCase):
    CAP = ollama_loop.DEFAULT_NUM_PREDICT

    def test_json_is_merged(self):
        with mock.patch.dict(os.environ, {"AGENT_OLLAMA_OPTIONS": '{"num_ctx": 8192}'}):
            self.assertEqual(ollama_loop.load_options(),
                             {"num_ctx": 8192, "num_predict": self.CAP})

    def test_broken_json_is_ignored(self):
        with mock.patch.dict(os.environ, {"AGENT_OLLAMA_OPTIONS": "{壊れ"}):
            self.assertEqual(ollama_loop.load_options(), {"num_predict": self.CAP})

    def test_runaway_generation_is_capped_by_default(self):
        """未指定でも 1 ラウンドの生成に天井を置く。

        停止トークンを出さなくなったモデルは 10 tok/s で書き続け、ラウンド 1 本で
        呼び出し側の予算（agent_timeout）を食い切る。停滞検知は無進捗しか見ないので
        この暴走には反応しない——天井だけが止められる。"""
        with mock.patch.dict(os.environ, {"AGENT_OLLAMA_OPTIONS": ""}):
            self.assertEqual(ollama_loop.load_options()["num_predict"], self.CAP)

    def test_explicit_num_predict_wins(self):
        with mock.patch.dict(os.environ, {"AGENT_OLLAMA_OPTIONS": '{"num_predict": 128}'}):
            self.assertEqual(ollama_loop.load_options()["num_predict"], 128)


class TestCommandExtraction(unittest.TestCase):
    def test_takes_the_last_fenced_block(self):
        text = "まず\n```bash\nls\n```\n次に\n```\necho hi\n```\n"
        self.assertEqual(ollama_loop.extract_command(text), "echo hi")

    def test_no_block_is_empty(self):
        self.assertEqual(ollama_loop.extract_command("完了しました TASK_COMPLETE"), "")

    def test_done_marker_is_stripped_from_the_body(self):
        self.assertEqual(ollama_loop.strip_done_marker("成果です\nTASK_COMPLETE\n"), "成果です")


class TestRunCommand(unittest.TestCase):
    def test_captures_output_and_exit_code(self):
        result = ollama_loop.run_command("echo hi; exit 3", cwd=os.getcwd(),
                                        timeout=30, max_chars=100)
        self.assertEqual(result["exit_code"], 3)
        self.assertIn("hi", result["output"])

    def test_output_is_clipped(self):
        result = ollama_loop.run_command("printf 'x%.0s' $(seq 1 500)", cwd=os.getcwd(),
                                        timeout=30, max_chars=100)
        self.assertIn("中略", result["output"])
        self.assertLess(len(result["output"]), 200)

    def test_timeout_is_reported_not_raised(self):
        result = ollama_loop.run_command("sleep 5", cwd=os.getcwd(), timeout=0.2,
                                        max_chars=100)
        self.assertEqual(result["exit_code"], 124)


class TestRunLoop(unittest.TestCase):
    def _loop(self, replies, **kwargs):
        calls = {"n": 0, "messages": []}

        def fake_chat(model, messages, **_kw):
            calls["messages"].append(list(messages))
            index = min(calls["n"], len(replies) - 1)
            calls["n"] += 1
            return {"text": replies[index], "tokens_in": 10, "tokens_out": 2}

        with mock.patch.object(ollama_loop, "chat_once", fake_chat), \
                mock.patch.object(ollama_loop, "run_command",
                                  lambda command, **_kw: {
                                      "exit_code": 0, "output": f"[{command} の結果]",
                                      "duration_sec": 0.1}):
            result = ollama_loop.run_loop("qwen3", "タスク", **kwargs)
        return result, calls

    def test_executes_command_then_finishes_on_marker(self):
        events = []
        result, calls = self._loop(
            ["```bash\nls\n```", "できました\nTASK_COMPLETE"],
            emit=lambda kind, **f: events.append((kind, f)))
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(result["text"], "できました")
        self.assertEqual(result["tokens_in"], 20, "ラウンドを跨いで実測を積む")
        kinds = [k for k, _f in events]
        self.assertIn("tool_exec", kinds)
        self.assertIn("tool_result", kinds)
        # ツール結果が次ラウンドの入力へ入っている
        self.assertIn("[ls の結果]", calls["messages"][1][-1]["content"])

    def test_conversation_body_is_emitted_as_messages(self):
        # 会話の本文を `message` イベントとして残す（他 CLI のネイティブ記録と揃える）。
        # agent-audit の jsonl-dir リーダは行直下の role / content を会話として読むので、
        # このイベント形がそのまま「あとで読める会話」になる。
        events = []
        _result, _calls = self._loop(
            ["```bash\nls\n```", "できました\nTASK_COMPLETE"],
            emit=lambda kind, **f: events.append((kind, f)))
        said = [(f["role"], f["content"]) for k, f in events if k == "message"]
        self.assertEqual(said[0], ("user", "タスク"), "最初の指示が会話の起点")
        self.assertEqual(said[1][0], "assistant")
        self.assertIn("[ls の結果]", said[2][1], "ツール結果も会話として残る")
        self.assertEqual(said[3], ("assistant", "できました\nTASK_COMPLETE"))
        # system プロンプトは会話ではない（読み手も user/assistant だけを会話とする）
        self.assertNotIn("system", [role for role, _ in said])

    def test_message_events_absent_without_emit(self):
        # emit なしでも会話は成立する（記録は観測手段で、実行の条件ではない）
        result, calls = self._loop(["できました\nTASK_COMPLETE"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(calls["messages"][0][-1]["content"], "タスク")

    def test_off_contract_reply_is_nudged_then_accepted(self):
        result, calls = self._loop(["ただの雑談"] * 5)
        self.assertEqual(result["status"], "no_command")
        # 言い直しは 2 回まで（その後は最後の本文を成果として返す = 止まらない）
        self.assertEqual(result["rounds"], 3)
        self.assertEqual(result["text"], "ただの雑談")

    def test_max_rounds_is_respected(self):
        result, _calls = self._loop(["```bash\nls\n```"], max_rounds=3)
        self.assertEqual(result["status"], "max_rounds")
        self.assertEqual(result["rounds"], 3)

    def test_context_is_tracked_and_warned(self):
        from agentcore import ollama_context
        events = []
        tracker = ollama_context.ContextTracker(limit=1000, warn_pct=50)

        def fake_chat(model, messages, **kw):
            kw["tracker"].observe(600, 50)      # 使用量 650 / 1000 = 65%
            return {"text": "終わりました\nTASK_COMPLETE", "tokens_in": 600, "tokens_out": 50}

        with mock.patch.object(ollama_loop, "chat_once", fake_chat):
            result = ollama_loop.run_loop(
                "m", "タスク", tracker=tracker,
                emit=lambda kind, **f: events.append((kind, f)))
        warns = [f for k, f in events if k == "context_warn"]
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0]["context_used"], 650)
        self.assertEqual(warns[0]["context_pct"], 65.0)
        self.assertEqual(result["context"]["context_used"], 650)

    def test_tool_output_is_clipped_to_the_remaining_context(self):
        """残り容量に合わせて詰める（サーバに黙って切り捨てさせない）。"""
        from agentcore import ollama_context
        seen = {}
        tracker = ollama_context.ContextTracker(limit=1200)

        def fake_chat(model, messages, **kw):
            kw["tracker"].observe(600, 0)       # 残り 600 - reserve(512) = 88 トークン
            return {"text": "```bash\nls\n```", "tokens_in": 600, "tokens_out": 0}

        def fake_run(command, **kw):
            seen["max_chars"] = kw["max_chars"]
            return {"exit_code": 0, "output": "x", "duration_sec": 0.1}

        with mock.patch.object(ollama_loop, "chat_once", fake_chat), \
                mock.patch.object(ollama_loop, "run_command", fake_run):
            ollama_loop.run_loop("m", "タスク", tracker=tracker, max_rounds=1,
                                 max_output_chars=99999)
        self.assertLess(seen["max_chars"], 99999, "残り容量まで絞る")
        self.assertGreater(seen["max_chars"], 0)

    def test_exhausted_context_stops_explicitly(self):
        from agentcore import ollama_context
        events = []
        tracker = ollama_context.ContextTracker(limit=700)

        def fake_chat(model, messages, **kw):
            kw["tracker"].observe(690, 0)       # 残りは reserve にも足りない
            return {"text": "```bash\nls\n```", "tokens_in": 690, "tokens_out": 0}

        with mock.patch.object(ollama_loop, "chat_once", fake_chat), \
                mock.patch.object(ollama_loop, "run_command") as run:
            result = ollama_loop.run_loop(
                "m", "タスク", tracker=tracker, max_rounds=5,
                emit=lambda kind, **f: events.append((kind, f)))
        self.assertEqual(result["status"], "context_exhausted")
        self.assertEqual(run.call_count, 0, "入らないと分かっているなら実行しない")
        kinds = [k for k, _f in events]
        self.assertIn("context_exhausted", kinds)
        self.assertNotIn("tool_exec", kinds,
                         "実行していないコマンドを実行したようにログへ残さない")

    def test_no_tracker_means_no_context_fields(self):
        """トラッカーを渡さなければ従来どおり（上限も警告も関与しない）。"""
        result, _calls = self._loop(["できました\nTASK_COMPLETE"])
        self.assertEqual(result["context"], {})

    def test_system_prompt_states_the_workdir(self):
        self.assertIn("/tmp/work", ollama_loop.system_prompt("/tmp/work"))
        self.assertIn("TASK_COMPLETE", ollama_loop.system_prompt("/tmp/work"))

    def test_read_toolset_prompt_declares_the_limits(self):
        text = ollama_loop.system_prompt("/tmp/work", "read")
        self.assertIn("/tmp/work", text)
        self.assertIn("TASK_COMPLETE", text)
        self.assertIn("grep", text)
        self.assertNotIn("bash", text.lower(), "汎用シェルがあるかのように書かない")


class TestCommandGate(unittest.TestCase):
    """read セットの実行ゲート（ツール開示設計 §4.2・適用拡大設計 §11 の受け入れ基準）。"""

    def test_bash_toolset_allows_everything(self):
        self.assertEqual(ollama_loop.check_command("rm -rf / | tee x", "bash"), "")

    def test_read_commands_pass(self):
        for command in ("ls -la src", "cat README.md", "grep -rn foo .",
                        "git log --oneline -20", "find . -name '*.py'",
                        "/usr/bin/head -5 a.txt"):
            self.assertEqual(ollama_loop.check_command(command, "read"), "",
                             f"許可されるべき: {command}")

    def test_write_commands_are_rejected(self):
        for command in ("rm -rf build", "sed -i s/a/b/ f.py", "python x.py",
                        "tee out.txt", "awk '{print > \"f\"}' a", "bash -c ls"):
            self.assertNotEqual(ollama_loop.check_command(command, "read"), "",
                                f"拒否されるべき: {command}")

    def test_metacharacters_are_rejected(self):
        for command in ("cat a > b", "cat a | sh", "ls; rm x", "cat $(id)",
                        "ls `whoami`", "cat a && rm b", "ls\nrm x", "ls ~"):
            reason = ollama_loop.check_command(command, "read")
            self.assertIn("使えません", reason, f"拒否されるべき: {command}")

    def test_write_capable_git_subcommands_are_rejected(self):
        self.assertNotEqual(ollama_loop.check_command("git push origin main", "read"), "")
        self.assertNotEqual(ollama_loop.check_command("git commit -m x", "read"), "")

    def test_find_write_predicates_are_rejected(self):
        self.assertNotEqual(ollama_loop.check_command("find . -delete", "read"), "")
        self.assertNotEqual(
            ollama_loop.check_command("find . -name x -exec rm {} +", "read"), "")

    def test_restricted_set_runs_without_a_shell(self):
        """メタ文字はゲートで弾くが、実行の形そのものでもシェルを介さない（二段構え）。"""
        result = ollama_loop.run_command("echo a b", cwd=os.getcwd(), timeout=30,
                                         max_chars=100, toolset="read")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["output"], "a b")


class TestToolDenial(unittest.TestCase):
    def _run(self, replies, **kwargs):
        events = []
        index = {"n": 0}

        def fake_chat(model, messages, **_kw):
            i = min(index["n"], len(replies) - 1)
            index["n"] += 1
            return {"text": replies[i], "tokens_in": 1, "tokens_out": 1}

        with mock.patch.object(ollama_loop, "chat_once", fake_chat), \
                mock.patch.object(ollama_loop, "run_command") as run:
            result = ollama_loop.run_loop(
                "m", "タスク", toolset="read", max_rounds=8,
                emit=lambda kind, **f: events.append((kind, f)), **kwargs)
        return result, events, run

    def test_denied_command_is_not_executed_and_is_explained(self):
        result, events, run = self._run(
            ["```bash\nrm -rf build\n```", "調べ直しました\nTASK_COMPLETE"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(run.call_count, 0, "拒否したコマンドは実行しない")
        kinds = [k for k, _f in events]
        self.assertIn("tool_denied", kinds)
        self.assertNotIn("tool_exec", kinds,
                         "実行していないコマンドを実行したようにログへ残さない")

    def test_repeated_denials_stop_the_run(self):
        """権限の探りだけでラウンドを焼き切らせない（拒否にも予算を切る）。"""
        result, events, run = self._run(["```bash\nrm -rf build\n```"])
        self.assertEqual(result["status"], "tool_denied")
        self.assertEqual(run.call_count, 0)
        self.assertEqual(len([k for k, _f in events if k == "tool_denied"]),
                         ollama_loop._MAX_DENIALS + 1)


class TestFormat(unittest.TestCase):
    def test_format_is_sent_as_a_grammar_constraint(self):
        body = ollama_loop._payload("m", think=True, options=None, fmt="json")
        self.assertEqual(body["format"], "json")

    def test_no_format_field_when_unset(self):
        self.assertNotIn("format", ollama_loop._payload("m", think=None, options=None))


if __name__ == "__main__":
    unittest.main()
