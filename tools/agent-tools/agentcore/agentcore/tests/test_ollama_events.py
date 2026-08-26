from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentcore import ollama_events


class TestEventLog(unittest.TestCase):
    def test_emits_jsonl_with_timestamp_and_fans_out_to_sink(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "run.jsonl"   # 親ディレクトリも作る
            with ollama_events.EventLog(path, sink=seen.append) as log:
                log.emit("round_start", round=1)
                log.emit("llm_end", round=1, tokens_in=10, tokens_out=20)
            lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["kind"] for e in lines], ["round_start", "llm_end"])
        self.assertTrue(all(isinstance(e["ts"], float) for e in lines))
        self.assertEqual([e["kind"] for e in seen], ["round_start", "llm_end"])

    def test_sink_failure_does_not_break_the_run(self):
        def boom(_event):
            raise RuntimeError("描画側の事故")
        with ollama_events.EventLog(None, sink=boom) as log:
            log.emit("round_start", round=1)   # 例外が漏れないこと

    def test_no_path_writes_nothing(self):
        with ollama_events.EventLog(None) as log:
            self.assertEqual(log.emit("x")["kind"], "x")


class TestProgressBeacon(unittest.TestCase):
    """外側（ハーネス）の見張り向けの灯台。記録の置き場は動かさない。"""

    def test_beacon_is_marked_on_every_emit_and_carries_the_last_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            beacon = Path(tmp) / "sub" / "beacon"
            path = Path(tmp) / "run.jsonl"
            with ollama_events.EventLog(path, beacon=beacon) as log:
                log.emit("llm_heartbeat", phase="queue", waiting_sec=42.0)
                first = beacon.read_text(encoding="utf-8")
                log.emit("llm_heartbeat", phase="queue", waiting_sec=47.0)
                second = beacon.read_text(encoding="utf-8")
            # 記録はいつもの場所に、そのまま残る（`--status` / `--replay` が見る先）。
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)
            self.assertEqual(json.loads(first)["waiting_sec"], 42.0)
            self.assertEqual(json.loads(second)["waiting_sec"], 47.0)

    def test_env_var_places_the_beacon_when_the_caller_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            beacon = Path(tmp) / "beacon"
            with mock.patch.dict(os.environ,
                                 {ollama_events.PROGRESS_BEACON_ENV: str(beacon)}):
                with ollama_events.EventLog(None) as log:
                    log.emit("run_start", model="qwen3")
            self.assertEqual(json.loads(beacon.read_text(encoding="utf-8"))["kind"],
                             "run_start")

    def test_long_fields_are_left_in_the_log_not_copied_to_the_beacon(self):
        """会話の本文まで毎回書き直すのは灯台の仕事ではない（本文の置き場は JSONL）。"""
        with tempfile.TemporaryDirectory() as tmp:
            beacon = Path(tmp) / "beacon"
            path = Path(tmp) / "run.jsonl"
            with ollama_events.EventLog(path, beacon=beacon) as log:
                log.emit("message", role="user", content="あ" * 5000)
            marked = json.loads(beacon.read_text(encoding="utf-8"))
            logged = path.read_text(encoding="utf-8")
        self.assertEqual(marked["kind"], "message")
        self.assertEqual(marked["role"], "user")       # 短い値は残る
        self.assertNotIn("content", marked)
        self.assertIn("あ" * 5000, logged)

    def test_an_unwritable_beacon_does_not_break_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("", encoding="utf-8")
            with ollama_events.EventLog(None, beacon=blocker / "beacon") as log:
                log.emit("run_start")      # 例外が漏れないこと
                self.assertIsNone(log.beacon)   # 以後は試さない


class TestReadStatus(unittest.TestCase):
    def _write(self, tmp, events):
        path = Path(tmp) / "run.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        return path

    def test_running_and_alive_from_recent_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, [
                {"ts": 100.0, "kind": "run_start", "model": "qwen3", "mode": "tools"},
                {"ts": 101.0, "kind": "round_start", "round": 1, "rounds_max": 12},
                {"ts": 102.0, "kind": "llm_progress", "round": 1, "phase": "decode",
                 "tokens_out": 30, "tokens_per_sec": 7.2},
            ])
            status = ollama_events.read_status(path, now=104.0)
        self.assertEqual(status["state"], "running")
        self.assertTrue(status["alive"])
        self.assertEqual((status["model"], status["mode"], status["round"]),
                         ("qwen3", "tools", 1))
        self.assertEqual(status["phase"], "decode")
        self.assertEqual(status["tokens_per_sec"], 7.2)
        self.assertEqual(status["since_last_progress_sec"], 2.0)
        self.assertEqual(status["elapsed_sec"], 4.0)

    def test_heartbeat_is_liveness_not_progress(self):
        """heartbeat では last_progress を進めない——進めると stall を永遠に検知できない。

        同時に、heartbeat があるので `alive` は真のまま（= 長い沈黙を異常と扱わない証拠）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, [
                {"ts": 100.0, "kind": "run_start", "model": "qwen3", "mode": "plain"},
                {"ts": 101.0, "kind": "llm_progress", "round": 1, "tokens_out": 1},
                {"ts": 130.0, "kind": "llm_heartbeat", "round": 1, "phase": "prefill",
                 "waiting_sec": 29.0},
            ])
            status = ollama_events.read_status(path, now=131.0)
        self.assertTrue(status["alive"], "heartbeat が来ているので生きている")
        self.assertEqual(status["since_last_progress_sec"], 30.0, "進捗は 101.0 のまま")
        self.assertEqual(status["since_last_event_sec"], 1.0)

    def test_silence_beyond_grace_is_not_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, [{"ts": 100.0, "kind": "run_start", "model": "m"}])
            status = ollama_events.read_status(path, now=100.0 + 999)
        self.assertEqual(status["state"], "running")
        self.assertFalse(status["alive"], "heartbeat すら来ていない = 観測できていない")

    def test_terminal_and_stall_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = self._write(tmp, [
                {"ts": 1.0, "kind": "run_start", "model": "m"},
                {"ts": 2.0, "kind": "run_end", "status": "done", "tokens_in": 5, "tokens_out": 6},
            ])
            self.assertEqual(ollama_events.read_status(done, now=3.0)["state"], "done")
            self.assertEqual(ollama_events.read_status(done, now=3.0)["tokens_out"], 6)
        with tempfile.TemporaryDirectory() as tmp:
            stalled = self._write(tmp, [
                {"ts": 1.0, "kind": "run_start", "model": "m"},
                {"ts": 2.0, "kind": "stall", "phase": "decode", "waiting_sec": 180},
            ])
            self.assertEqual(ollama_events.read_status(stalled, now=3.0)["state"], "stalled")

    def test_missing_or_broken_log_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                ollama_events.read_status(Path(tmp) / "nope.jsonl")["state"], "unknown")
            broken = Path(tmp) / "broken.jsonl"
            broken.write_text("これは JSON ではない\n", encoding="utf-8")
            self.assertEqual(ollama_events.read_status(broken)["state"], "unknown")


class TestFollowEvents(unittest.TestCase):
    def test_reads_to_terminal_event_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            path.write_text("".join(json.dumps(e) + "\n" for e in [
                {"ts": 1.0, "kind": "run_start"},
                {"ts": 2.0, "kind": "壊れていない行"},
                {"ts": 3.0, "kind": "run_end", "status": "done"},
            ]), encoding="utf-8")
            got = list(ollama_events.follow_events(path))
        self.assertEqual([e["kind"] for e in got],
                         ["run_start", "壊れていない行", "run_end"])


class TestLogPaths(unittest.TestCase):
    def test_log_dir_honours_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            import os
            os.environ["AGENT_OLLAMA_LOG_DIR"] = tmp
            try:
                self.assertEqual(ollama_events.log_dir(), Path(tmp))
                path = ollama_events.new_log_path("qwen3.5:9b")
                self.assertEqual(path.parent, Path(tmp))
                self.assertIn("qwen3.5_9b", path.name, "パスに使えない文字を落とす")
                self.assertTrue(path.name.endswith(".jsonl"))
                self.assertIsNone(ollama_events.latest_log_path(), "まだ 1 件も無い")
                path.write_text("", encoding="utf-8")
                self.assertEqual(ollama_events.latest_log_path(), path)
            finally:
                del os.environ["AGENT_OLLAMA_LOG_DIR"]


if __name__ == "__main__":
    unittest.main()
