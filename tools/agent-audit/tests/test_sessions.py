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

    def _payload(self, *argv):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"KIRO_AGENTS_DIR": self.agents_dir}), \
             contextlib.redirect_stdout(buf):
            rc = cli_main(["--audit-dir", self.audit_dir, "sessions", *argv])
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def _run(self, *argv):
        return self._payload(*argv)["sessions"]

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

    def test_reports_whether_cli_declares_a_session_log(self):
        # 0 件の理由を読み手が人へ言えるように、宣言の有無を区別して返す
        self.assertEqual(self._payload("--cli", "fakecli")["cli"],
                         {"name": "fakecli", "declared": True, "supported": True})
        self.assertEqual(self._payload("--cli", "no-such-cli")["cli"],
                         {"name": "no-such-cli", "declared": False, "supported": False})

    def test_cli_block_absent_without_cli_filter(self):
        self.assertNotIn("cli", self._payload())

    def test_json_output_is_scrubbed(self):
        # 読み手はこの本文を画面へ出すだけでなく、要求文・ワークフローの下書き材料として
        # LLM へ渡す（dashboard の「このセッションを種に」）。他の export 系と同じく
        # 資格情報らしいトークンとホーム絶対パスを伏せてから返す。
        home = os.path.expanduser("~")
        path = os.path.join(self.store_dir, "s-secret.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "user", "timestamp": "2026-08-03T13:00:00Z", "sessionId": "s-secret",
                "cwd": f"{home}/repo",
                "message": {"role": "user",
                            "content": "token=ghp_abcdefghijklmnop で直して"},
            }, ensure_ascii=False) + "\n")
        got = self._run("--cli", "fakecli", "--messages", "s-secret")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["cwd"], "~/repo")
        self.assertNotIn("ghp_abcdefghijklmnop", got[0]["messages"][0]["text"])
        self.assertIn("[REDACTED]", got[0]["messages"][0]["text"])


if __name__ == "__main__":
    unittest.main()
