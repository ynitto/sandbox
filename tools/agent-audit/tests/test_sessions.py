"""sessions サブコマンド — CLI ネイティブセッションの検索・本文取得（dashboard の
「ノードの会話を見る」導線）。絞り込み（CLI 名・時間窓・cwd）と JSON 整形だけを検証する
——ストアの読み自体は readers のテスト（test_readers.py）が正。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from _shared import AuditTestCase, claude_session_jsonl, cli_main


def _agents_dir_with_fakecli(store_dir: str) -> str:
    d = tempfile.mkdtemp(prefix="agent-audit-sessions-agents-")
    spec = {"command": "fakecli", "prompt_via": "arg",
            "session_log": {"format": "jsonl-dir", "paths": [store_dir]}}
    with open(os.path.join(d, "fakecli.json"), "w", encoding="utf-8") as f:
        json.dump(spec, f)
    return d


class SessionsCmdTests(AuditTestCase):
    def setUp(self):
        super().setUp()
        self.store_dir = os.path.join(self.tmp, "store")
        claude_session_jsonl(os.path.join(self.store_dir, "s-early.jsonl"),
                             sid="s-early", cwd="/tmp/ws-run-a",
                             t0="2026-08-03T09:59:30Z", t1="2026-08-03T10:00:30Z")
        claude_session_jsonl(os.path.join(self.store_dir, "s-late.jsonl"),
                             sid="s-late", cwd="/tmp/ws-run-b",
                             t0="2026-08-03T12:00:00Z", t1="2026-08-03T12:01:00Z")
        self.agents_dir = _agents_dir_with_fakecli(self.store_dir)

    def _run(self, *argv):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"KIRO_AGENTS_DIR": self.agents_dir}), \
             contextlib.redirect_stdout(buf):
            rc = cli_main(["--audit-dir", self.audit_dir, "sessions", *argv])
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())["sessions"]

    def test_lists_sessions_for_cli_newest_first(self):
        got = self._run("--cli", "fakecli")
        self.assertEqual([s["native_id"] for s in got], ["s-late", "s-early"])
        self.assertEqual(got[0]["agent_cli"], "fakecli")
        self.assertNotIn("messages", got[0])   # 一覧は本文なし（--messages で取得）

    def test_time_window_filters_by_overlap(self):
        self.assertEqual([s["native_id"] for s in
                          self._run("--cli", "fakecli", "--since", "2026-08-03T11:00:00Z")],
                         ["s-late"])
        self.assertEqual([s["native_id"] for s in
                          self._run("--cli", "fakecli", "--until", "2026-08-03T11:00:00Z")],
                         ["s-early"])

    def test_cwd_contains_filter(self):
        got = self._run("--cli", "fakecli", "--cwd-contains", "ws-run-a")
        self.assertEqual([s["native_id"] for s in got], ["s-early"])

    def test_messages_returns_transcript_for_one_session(self):
        got = self._run("--cli", "fakecli", "--messages", "s-early")
        self.assertEqual(len(got), 1)
        msgs = got[0]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["User", "Assistant"])
        self.assertEqual(msgs[0]["text"], "直して")

    def test_unknown_cli_returns_empty(self):
        self.assertEqual(self._run("--cli", "no-such-cli"), [])


if __name__ == "__main__":
    unittest.main()
