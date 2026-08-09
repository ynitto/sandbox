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


class OpencodeSqliteReaderTests(AuditTestCase):
    """opencode のストアは session / message / part の 3 表に正規化されている。

    形は実機（opencode 1.18.14）の `~/.local/share/opencode/opencode.db` に合わせてある。
    """

    def make_db(self, *, sessions=None, parts=None) -> str:
        db = os.path.join(self.tmp, "opencode.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, title TEXT, "
                     "version TEXT, model TEXT, tokens_input INTEGER, tokens_output INTEGER, "
                     "tokens_cache_read INTEGER, tokens_cache_write INTEGER, "
                     "time_created INTEGER, time_updated INTEGER)")
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, "
                     "time_created INTEGER, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, "
                     "session_id TEXT, time_created INTEGER, data TEXT)")
        for row in sessions or [self.session_row()]:
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
        for row in parts or self.default_parts():
            mid, sid, seq, mdata, pdata = row
            conn.execute("INSERT OR IGNORE INTO message VALUES (?,?,?,?)",
                         (mid, sid, seq, json.dumps(mdata)))
            conn.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                         (f"prt-{mid}-{seq}", mid, sid, seq, json.dumps(pdata)))
        conn.commit()
        conn.close()
        return db

    def session_row(self, sid="ses-1", updated=1785969455093):
        # 時刻は opencode と同じ epoch ミリ秒。model 列は JSON。
        return (sid, "/home/u/repo", "題名", "1.18.14",
                json.dumps({"id": "qwen3", "providerID": "ollama"}),
                100, 20, 3, 0, 1785969453332, updated)

    def default_parts(self, sid="ses-1"):
        # message id はセッションを跨いで一意（実機と同じ）。
        return [
            (f"{sid}-msg-1", sid, 1, {"role": "user"}, {"type": "text", "text": "直して"}),
            (f"{sid}-msg-2", sid, 2, {"role": "assistant"}, {"type": "step-start"}),
            (f"{sid}-msg-2", sid, 3, {"role": "assistant"},
             {"type": "text", "text": "直しました"}),
            (f"{sid}-msg-2", sid, 4, {"role": "assistant"},
             {"type": "step-finish", "tokens": {"input": 100, "output": 20}}),
        ]

    def read(self, db, **over):
        slog = {"format": "opencode-sqlite", "paths": [db], "usage": True}
        slog.update(over)
        return readers.read_sessions(slog, want_messages=True)

    def test_reads_session_with_measured_tokens(self):
        sessions = self.read(self.make_db())
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["native_id"], "ses-1")
        self.assertEqual(s["cwd"], "/home/u/repo")
        self.assertEqual(s["model"], "ollama/qwen3")          # provider/model へ畳む
        self.assertEqual(s["turns"], 2)                        # 本文のあるメッセージだけ
        self.assertEqual(s["messages"], [("User", "直して"), ("Assistant", "直しました")])
        # キャッシュ読みは入力側へ寄せる（jsonl-dir と同じ扱い）
        self.assertEqual((s["tokens_in"], s["tokens_out"]), (103, 20))
        self.assertTrue(s["usage_measured"])
        # epoch ミリ秒 → 秒
        self.assertAlmostEqual(s["updated_at"], 1785969455.093, places=3)

    def test_non_text_parts_are_not_turns(self):
        # step-start / step-finish / tool は本文ではない（turns を水増ししない）。
        db = self.make_db(parts=[
            ("msg-1", "ses-1", 1, {"role": "user"}, {"type": "text", "text": "やって"}),
            ("msg-2", "ses-1", 2, {"role": "assistant"},
             {"type": "tool", "tool": "bash", "state": {"output": "ls"}}),
        ])
        self.assertEqual(self.read(db)[0]["turns"], 1)

    def test_multiple_text_parts_of_one_message_are_joined(self):
        db = self.make_db(parts=[
            ("msg-1", "ses-1", 1, {"role": "user"}, {"type": "text", "text": "前半"}),
            ("msg-1", "ses-1", 2, {"role": "user"}, {"type": "text", "text": "後半"}),
        ])
        s = self.read(db)[0]
        self.assertEqual(s["turns"], 1)
        self.assertEqual(s["messages"], [("User", "前半\n後半")])

    def test_clean_rules_apply_with_version_from_the_session_row(self):
        db = self.make_db(parts=[
            ("msg-1", "ses-1", 1, {"role": "user"},
             {"type": "text", "text": "<system-reminder>雑音</system-reminder>直して"}),
        ])
        s = self.read(db, clean={
            "version_key": "version",
            "rules": [{"rule": "strip-tag", "tags": ["system-reminder"]}],
        })[0]
        self.assertEqual(s["log_version"], "1.18.14")
        self.assertEqual(s["messages"], [("User", "直して")])

    def test_drop_line_sees_the_part_row_with_its_role(self):
        db = self.make_db(parts=[
            ("msg-1", "ses-1", 1, {"role": "user"}, {"type": "text", "text": "残る"}),
            ("msg-2", "ses-1", 2, {"role": "assistant"},
             {"type": "text", "text": "消える", "synthetic": True}),
        ])
        s = self.read(db, clean={"rules": [{"rule": "drop-line", "field": "synthetic",
                                            "equals": True}]})[0]
        self.assertEqual(s["messages"], [("User", "残る")])

    def test_limit_takes_the_newest_sessions(self):
        db = self.make_db(
            sessions=[self.session_row("ses-old", 1000), self.session_row("ses-new", 9000)],
            parts=self.default_parts("ses-old") + self.default_parts("ses-new"))
        sessions = readers.read_sessions(
            {"format": "opencode-sqlite", "paths": [db]}, limit=1)
        self.assertEqual([s["native_id"] for s in sessions], ["ses-new"])

    def test_foreign_sqlite_store_is_read_as_empty(self):
        # 3 表が揃っていないストア（版差・別ツールの db）は「読めない」＝0 件。
        db = os.path.join(self.tmp, "other.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE whatever (id TEXT)")
        conn.commit()
        conn.close()
        self.assertEqual(self.read(db), [])

    def test_missing_path_is_not_an_error(self):
        self.assertEqual(self.read(os.path.join(self.tmp, "nope.db")), [])


if __name__ == "__main__":
    unittest.main()
