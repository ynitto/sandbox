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
        # main() は入口で ~/.profile を読みにいく。テストは実行環境の profile に
        # 依存させない（subprocess も起こさない）。
        with mock.patch.object(aider_adapter, "load_profile_env",
                               return_value={}) as profile, \
                mock.patch.object(aider_adapter.subprocess, "run", side_effect=run):
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(aider_adapter.main(["--message", "test"]), 0)

        self.assertEqual(stderr.getvalue(), "@agent-usage tokens_in=17 tokens_out=40\n")
        self.assertFalse(log_path.exists())
        self.assertEqual(profile.call_count, 1,
                         "aider を起動する前に ~/.profile の補完を通す")

    def test_env_completion_matches_canonical_implementation(self):
        """複製した補完ロジックが正典（ollama_adapter）と同じ振る舞いをすること。"""
        with mock.patch.dict(aider_adapter.os.environ):
            for name in ("OLLAMA_HOST", "OLLAMA_API_BASE", "NO_PROXY", "no_proxy"):
                aider_adapter.os.environ.pop(name, None)
            aider_adapter.os.environ["OLLAMA_HOST"] = "http://10.0.0.5:11434"
            aider_adapter.load_profile_env("/nonexistent")
            self.assertEqual(aider_adapter.os.environ["OLLAMA_API_BASE"],
                             "http://10.0.0.5:11434",
                             "aider（litellm）は OLLAMA_API_BASE を読む")
            for var in ("NO_PROXY", "no_proxy"):
                self.assertEqual(aider_adapter.os.environ[var], "10.0.0.5",
                                 "ollama のホストはプロキシ対象から外れる")


if __name__ == "__main__":
    unittest.main()
