"""Aider の実測トークンを共通 usage 契約へ渡す回帰テスト。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import aider_adapter  # noqa: E402


class TestAiderAdapter(unittest.TestCase):
    def test_sums_exact_message_usage(self):
        log_path = None

        def run(argv):
            nonlocal log_path
            log_path = Path(argv[argv.index("--analytics-log") + 1])
            events = [
                {"event": "message_send", "properties": {
                    "prompt_tokens": 12, "completion_tokens": 34}},
                {"event": "other", "properties": {
                    "prompt_tokens": 999, "completion_tokens": 999}},
                {"event": "message_send", "properties": {
                    "prompt_tokens": 5, "completion_tokens": 6}},
            ]
            log_path.write_text("".join(json.dumps(e) + "\n" for e in events))
            return mock.Mock(returncode=0)

        stderr = io.StringIO()
        with mock.patch.object(aider_adapter.subprocess, "run", side_effect=run):
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(aider_adapter.main(["--message", "test"]), 0)

        self.assertEqual(stderr.getvalue(), "@agent-usage tokens_in=17 tokens_out=40\n")
        self.assertFalse(log_path.exists())


if __name__ == "__main__":
    unittest.main()
