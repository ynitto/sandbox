from __future__ import annotations

import json
import os
import unittest

from _shared import AuditTestCase, collect

from agent_audit import reclean as reclean_mod


class RecleanTests(AuditTestCase):
    def _declare_claude(self, agents_dir, session_log):
        with open(os.path.join(agents_dir, "claude.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "claude", "command": ["claude"], "session_log": session_log}, f)

    def test_reclean_rewrites_only_existing_transcripts_without_touching_state(self):
        agents_dir = os.path.join(self.tmp, "agents")
        os.makedirs(agents_dir, exist_ok=True)
        sess_root = os.path.join(self.tmp, "claude-projects")
        path = os.path.join(sess_root, "p", "sess-1.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = [
            {"type": "user", "timestamp": "2026-08-04T09:00:00Z", "sessionId": "sess-1",
             "cwd": "/home/u/repo",
             "message": {"role": "user",
                         "content": "<system-reminder>hidden</system-reminder>直して"}},
            {"type": "assistant", "timestamp": "2026-08-04T09:00:10Z", "sessionId": "sess-1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "直しました"}],
                         "usage": {"input_tokens": 10, "output_tokens": 5}}},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

        os.environ["KIRO_AGENTS_DIR"] = agents_dir
        try:
            self._declare_claude(agents_dir, {"format": "jsonl-dir", "paths": [sess_root],
                                              "usage": True})
            st = self.make_store()
            args = self.make_args()
            n = collect.collect_cli_native(args, st, with_transcripts=True)
            self.assertEqual(n, 1)
            st.save_state()   # cmd_collect が普段行う永続化（reclean は別プロセス相当で読む）
            rec = next(iter(st.iter_records()))
            transcript_path = os.path.join(st.root, rec["excerpt_ref"])
            with open(transcript_path, encoding="utf-8") as f:
                before = f.read()
            self.assertIn("<system-reminder>hidden</system-reminder>", before)

            state_before = json.dumps(self.make_store().state, sort_keys=True)
            records_before = list(st.iter_records())

            # ルール改訂: system-reminder を除去するよう宣言を更新
            self._declare_claude(agents_dir, {
                "format": "jsonl-dir", "paths": [sess_root], "usage": True,
                "clean": {"rules": [{"rule": "strip-tag", "tags": ["system-reminder"]}]},
            })
            rc_args = self.make_args(only_agent_cli=None, dry_run=False)
            self.assertEqual(reclean_mod.cmd_reclean(rc_args), 0)

            with open(transcript_path, encoding="utf-8") as f:
                after = f.read()
            self.assertNotIn("<system-reminder>", after)
            self.assertIn("直して", after)

            # records・state（カーソル・seen・extracted）は不変
            st2 = self.make_store()
            self.assertEqual(json.dumps(st2.state, sort_keys=True), state_before)
            self.assertEqual([r["id"] for r in st2.iter_records()],
                             [r["id"] for r in records_before])
        finally:
            os.environ["KIRO_AGENTS_DIR"] = os.path.join(self.tmp, "no-agents")

    def test_reclean_skips_sessions_without_existing_transcript(self):
        agents_dir = os.path.join(self.tmp, "agents")
        os.makedirs(agents_dir, exist_ok=True)
        sess_root = os.path.join(self.tmp, "claude-projects")
        os.makedirs(sess_root, exist_ok=True)
        os.environ["KIRO_AGENTS_DIR"] = agents_dir
        try:
            self._declare_claude(agents_dir, {"format": "jsonl-dir", "paths": [sess_root],
                                              "usage": True})
            args = self.make_args()
            self.assertEqual(reclean_mod.cmd_reclean(args), 0)   # transcripts/ が無くても落ちない
        finally:
            os.environ["KIRO_AGENTS_DIR"] = os.path.join(self.tmp, "no-agents")


if __name__ == "__main__":
    unittest.main()
