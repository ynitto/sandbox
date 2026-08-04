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

    def test_unknown_format_returns_empty(self):
        self.assertEqual(readers.read_sessions({"format": "nope", "paths": ["/tmp"]}), [])


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
