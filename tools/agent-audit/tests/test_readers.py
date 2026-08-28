from __future__ import annotations

import json
import os
import sqlite3
import unittest

from _shared import AuditTestCase, claude_session_jsonl, readers


class JsonlDirReaderTests(AuditTestCase):
    def test_claude_style_session(self):
        root = os.path.join(self.tmp, "projects")
        claude_session_jsonl(os.path.join(root, "p", "sess-1.jsonl"))
        sessions = readers.read_sessions({"format": "jsonl-dir", "paths": [root]},
                                         want_messages=True)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["native_id"], "sess-1")
        self.assertEqual(s["cwd"], "/home/u/repo")
        self.assertEqual((s["tokens_in"], s["tokens_out"]), (1000, 200))
        self.assertTrue(s["usage_measured"])
        self.assertEqual(s["turns"], 2)
        self.assertEqual(s["messages"][0], ("User", "直して"))

    def test_same_message_usage_is_counted_once(self):
        """Claude は thinking/text を同じ message.id で分け、usage を各行へ再掲する。"""
        root = os.path.join(self.tmp, "projects-usage")
        path = os.path.join(root, "p", "sess-usage.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        usage = {"input_tokens": 2, "cache_creation_input_tokens": 30,
                 "cache_read_input_tokens": 10, "output_tokens": 5}
        lines = [
            {"type": "assistant", "timestamp": "2026-08-09T05:34:46Z",
             "sessionId": "sess-usage",
             "message": {"id": "msg-1", "role": "assistant",
                         "content": [{"type": "thinking", "thinking": ""}],
                         "usage": usage}},
            {"type": "assistant", "timestamp": "2026-08-09T05:34:47Z",
             "sessionId": "sess-usage",
             "message": {"id": "msg-1", "role": "assistant",
                         "content": [{"type": "text", "text": "完了"}],
                         "usage": usage}},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        session = readers.read_sessions({"format": "jsonl-dir", "paths": [root]})[0]
        self.assertEqual((session["tokens_in"], session["tokens_out"]), (42, 5))

    def test_codex_style_total_usage_wins(self):
        root = os.path.join(self.tmp, "sessions")
        path = os.path.join(root, "2026", "08", "03", "rollout-x.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = [
            {"timestamp": "2026-08-03T10:00:00Z",
             "payload": {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}},
            {"timestamp": "2026-08-03T10:01:00Z",
             "payload": {"type": "token_count",
                         "info": {"total_token_usage": {"input_tokens": 5000,
                                                        "output_tokens": 700}}}},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        sessions = readers.read_sessions({"format": "jsonl-dir", "paths": [root]})
        self.assertEqual(len(sessions), 1)
        self.assertEqual((sessions[0]["tokens_in"], sessions[0]["tokens_out"]), (5000, 700))

    def test_ollama_style_progress_log_reads_the_flat_usage(self):
        """agent-ollama は 1 ラウンドの実測を `llm_end` の**トップレベル**へ書く。

        ここが入れ子の `usage` しか見ていなかったので、**書いている側と読んでいる側が
        食い違っていた**——`session_log.usage` を true にしても 0 トークンで「実測済み」と
        記帳され、秒からの推定より悪くなる（設計 2026-08-27 §7.3 C / 実装計画 段 8）。

        見るのは `llm_end` だけである。`llm_progress` は同じ綴りで**途中経過**の
        `tokens_out` を載せるので、行を選ばずに足すと 1 ラウンドを何度も数える。
        """
        root = os.path.join(self.tmp, "ollama-usage")
        os.makedirs(root, exist_ok=True)
        lines = [
            {"ts": 1786163370.2, "kind": "message", "role": "user", "content": "直して"},
            {"ts": 1786163375.0, "kind": "llm_progress", "round": 1, "tokens_out": 100},
            {"ts": 1786163380.0, "kind": "llm_end", "round": 1, "phase": "done",
             "tokens_in": 1234, "tokens_out": 567, "duration_sec": 10.0},
            {"ts": 1786163390.0, "kind": "llm_end", "round": 2, "phase": "done",
             "tokens_in": 2000, "tokens_out": 33, "duration_sec": 10.0},
            {"ts": 1786163400.0, "kind": "message", "role": "assistant", "content": "直しました"},
        ]
        with open(os.path.join(root, "s1.jsonl"), "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        got = readers.read_sessions({"format": "jsonl-dir", "paths": [root]})[0]
        self.assertEqual((got["tokens_in"], got["tokens_out"]), (3234, 600))
        self.assertTrue(got["usage_measured"])

    def test_a_round_without_usage_is_not_counted_as_measured(self):
        root = os.path.join(self.tmp, "ollama-nousage")
        os.makedirs(root, exist_ok=True)
        lines = [
            {"ts": 1786163370.2, "kind": "message", "role": "user", "content": "直して"},
            {"ts": 1786163380.0, "kind": "llm_end", "round": 1, "phase": "done",
             "duration_sec": 10.0},
        ]
        with open(os.path.join(root, "s1.jsonl"), "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        got = readers.read_sessions({"format": "jsonl-dir", "paths": [root]})[0]
        self.assertFalse(got["usage_measured"])
        self.assertIsNone(got["tokens_in"])

    def test_ollama_style_progress_log_reads_message_events(self):
        # agent-ollama の進捗ログ（`~/.agents/logs/ollama/*.jsonl`）は 1 行 1 イベントで、
        # 会話の本文だけが行直下の role / content に載る（kind="message"）。ts は epoch 秒、
        # cwd は run_start 行が持つ。会話ではない進捗イベントは本文として拾わない。
        root = os.path.join(self.tmp, "ollama-logs")
        path = os.path.join(root, "20260808T132930-72642-qwen3_9b.jsonl")
        os.makedirs(root, exist_ok=True)
        lines = [
            {"ts": 1786163370.188, "kind": "run_start", "model": "qwen3:9b",
             "mode": "tools", "cwd": "/tmp/ws-run-a", "prompt_chars": 2637},
            {"ts": 1786163370.2, "kind": "message", "role": "user", "content": "直して"},
            {"ts": 1786163380.0, "kind": "llm_heartbeat", "phase": "prefill", "round": 1},
            {"ts": 1786163400.0, "kind": "message", "role": "assistant",
             "content": "直しました\nTASK_COMPLETE"},
            {"ts": 1786163401.0, "kind": "run_end", "status": "done", "rounds": 1},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        sessions = readers.read_sessions({"format": "jsonl-dir", "paths": [root]},
                                         want_messages=True)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["native_id"], "20260808T132930-72642-qwen3_9b",
                         "セッション id が無いログはファイル名を名前にする")
        self.assertEqual(s["cwd"], "/tmp/ws-run-a")
        self.assertEqual(s["turns"], 2, "進捗イベントは会話に数えない")
        self.assertEqual(s["messages"],
                         [("User", "直して"), ("Assistant", "直しました\nTASK_COMPLETE")])
        self.assertEqual(s["created_at"], 1786163370.188, "ts（epoch 秒）を時刻として読む")

    def test_unknown_format_returns_empty(self):
        self.assertEqual(readers.read_sessions({"format": "nope", "paths": ["/tmp"]}), [])

    def test_clean_drops_sidechain_lines_and_strips_tags(self):
        root = os.path.join(self.tmp, "projects")
        path = os.path.join(root, "p", "sess-clean.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = [
            {"type": "user", "timestamp": "2026-08-04T09:00:00Z", "sessionId": "s1",
             "version": "2.1.0", "cwd": "/home/u/repo",
             "message": {"role": "user",
                         "content": "<system-reminder>hidden</system-reminder>直して"}},
            {"type": "user", "timestamp": "2026-08-04T09:00:05Z", "sessionId": "s1",
             "isSidechain": True,
             "message": {"role": "user", "content": "サブエージェント向けの内部会話"}},
            {"type": "assistant", "timestamp": "2026-08-04T09:00:10Z", "sessionId": "s1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "直しました"}],
                         "usage": {"input_tokens": 10, "output_tokens": 5}}},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        session_log = {
            "format": "jsonl-dir", "paths": [root],
            "clean": {
                "version_key": "version",
                "rules": [
                    {"rule": "drop-line", "field": "isSidechain", "equals": True},
                    {"rule": "strip-tag", "tags": ["system-reminder"]},
                ],
            },
        }
        sessions = readers.read_sessions(session_log, want_messages=True)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["log_version"], "2.1.0")
        self.assertEqual(s["turns"], 2)   # サイドチェーン行は数えない
        self.assertEqual(s["messages"][0], ("User", "直して"))

    def test_clean_versions_first_match_replaces_default(self):
        root = os.path.join(self.tmp, "projects2")
        path = os.path.join(root, "p", "sess-v.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = [
            {"type": "user", "timestamp": "2026-08-04T09:00:00Z", "sessionId": "s2",
             "version": "3.0.0",
             "message": {"role": "user", "content": "Caveat: システムからの注記\n本題です"}},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        session_log = {
            "format": "jsonl-dir", "paths": [root],
            "clean": {
                "version_key": "version",
                "rules": [{"rule": "drop-message", "prefixes": ["Caveat: "]}],
                "versions": [{"when": ">=3.0", "rules": []}],   # v3 系はノイズ除去なし
            },
        }
        sessions = readers.read_sessions(session_log, want_messages=True)
        self.assertEqual(sessions[0]["turns"], 1)
        self.assertTrue(sessions[0]["messages"][0][1].startswith("Caveat: "))

    def test_clean_absent_leaves_messages_untouched(self):
        root = os.path.join(self.tmp, "projects3")
        claude_session_jsonl(os.path.join(root, "p", "sess-plain.jsonl"))
        sessions = readers.read_sessions({"format": "jsonl-dir", "paths": [root]},
                                         want_messages=True)
        self.assertEqual(sessions[0]["log_version"], "")
        self.assertEqual(sessions[0]["messages"][0], ("User", "直して"))


class KiroSqliteReaderTests(AuditTestCase):
    def test_reads_sessions_table(self):
        db = os.path.join(self.tmp, "store.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE sessions (id TEXT, directory TEXT, created_at REAL, "
                     "updated_at REAL, messages TEXT)")
        msgs = json.dumps([{"role": "user", "content": "やって"},
                           {"role": "assistant", "content": "やりました"}])
        conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                     ("k1", "/home/u/w", 1754200000.0, 1754200600.0, msgs))
        conn.commit()
        conn.close()
        sessions = readers.read_sessions({"format": "kiro-sqlite", "paths": [db]},
                                         want_messages=True)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["native_id"], "k1")
        self.assertEqual(s["turns"], 2)
        self.assertFalse(s["usage_measured"])
        self.assertEqual(s["updated_at"], 1754200600.0)


if __name__ == "__main__":
    unittest.main()
