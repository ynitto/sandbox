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
    def run_adapter(self, argv, side_effect=None):
        calls = []
        settings = []

        def run(command):
            calls.append(command)
            if "--model-settings-file" in command:
                path = Path(command[command.index("--model-settings-file") + 1])
                settings.append((path, json.loads(path.read_text(encoding="utf-8"))))
            if side_effect:
                return side_effect(command)
            return mock.Mock(returncode=0)

        stderr = io.StringIO()
        with mock.patch.object(aider_adapter, "load_profile_env", return_value={}), \
                mock.patch.object(aider_adapter.subprocess, "run", side_effect=run):
            with contextlib.redirect_stderr(stderr):
                rc = aider_adapter.main(argv)
        return rc, stderr.getvalue(), calls, settings

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

    def test_applies_policy_and_removes_wrapper_option(self):
        rc, stderr, calls, settings = self.run_adapter([
            "--agent-policy", aider_adapter.POLICY_ID,
            "--model", aider_adapter.POLICY_MODEL, "--message", "edit"])
        self.assertEqual(rc, 0)
        self.assertNotIn("--agent-policy", calls[0])
        self.assertEqual(settings[0][1], [{
            "name": aider_adapter.POLICY_MODEL,
            "system_prompt_prefix": aider_adapter.POLICY_TEXT}])
        self.assertIn(f"@agent-policy id={aider_adapter.POLICY_ID} ", stderr)
        self.assertFalse(settings[0][0].exists())

    def test_no_policy_preserves_forwarded_arguments(self):
        rc, stderr, calls, settings = self.run_adapter(["--message", "edit"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0][3:], ["--message", "edit"])
        self.assertEqual(settings, [])
        self.assertNotIn("@agent-policy", stderr)

    def test_unknown_policy_fails_before_aider_starts(self):
        rc, stderr, calls, _ = self.run_adapter(["--agent-policy", "typo"])
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [])
        self.assertIn("unknown policy 'typo'", stderr)

    def test_policy_requires_exact_model(self):
        for model in ("ollama_chat/gemma4:12b", None):
            argv = ["--agent-policy", aider_adapter.POLICY_ID]
            if model:
                argv += ["--model", model]
            rc, stderr, calls, _ = self.run_adapter(argv)
            self.assertEqual(rc, 2)
            self.assertEqual(calls, [])
            self.assertIn("requires model", stderr)

    def test_managed_settings_reject_external_settings(self):
        rc, stderr, calls, _ = self.run_adapter([
            "--agent-num-ctx", "32768", "--model", aider_adapter.POLICY_MODEL,
            "--model-settings-file", "external.yml"])
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [])
        self.assertIn("conflicts", stderr)

    def test_numeric_options_are_composed_in_one_entry(self):
        rc, _, calls, settings = self.run_adapter([
            "--agent-num-ctx=32768", "--agent-num-predict", "2048",
            "--model", aider_adapter.POLICY_MODEL])
        self.assertEqual(rc, 0)
        self.assertNotIn("--agent-num-predict", calls[0])
        self.assertEqual(settings[0][1][0]["extra_params"],
                         {"num_ctx": 32768, "num_predict": 2048})

    def test_numeric_options_must_be_positive_integers(self):
        for value in ("0", "-1", "many"):
            rc, stderr, calls, _ = self.run_adapter([
                "--agent-num-predict", value, "--model", aider_adapter.POLICY_MODEL])
            self.assertEqual(rc, 2)
            self.assertEqual(calls, [])
            self.assertIn("positive integer", stderr)

    def test_file_not_found_cleans_both_temporary_files(self):
        seen = []
        def missing(command):
            seen.extend(Path(command[i + 1]) for i, value in enumerate(command)
                        if value in ("--analytics-log", "--model-settings-file"))
            raise FileNotFoundError
        rc, stderr, _, _ = self.run_adapter([
            "--agent-policy", aider_adapter.POLICY_ID,
            "--model", aider_adapter.POLICY_MODEL], missing)
        self.assertEqual(rc, 127)
        self.assertIn("command not found", stderr)
        self.assertTrue(seen)
        self.assertTrue(all(not path.exists() for path in seen))

    def test_aider_error_keeps_policy_and_usage_markers_and_cleans_files(self):
        seen = []

        def fail(command):
            analytics = Path(command[command.index("--analytics-log") + 1])
            settings = Path(command[command.index("--model-settings-file") + 1])
            seen.extend((analytics, settings))
            analytics.write_text(json.dumps({
                "event": "message_send",
                "properties": {"prompt_tokens": 7, "completion_tokens": 3},
            }) + "\n", encoding="utf-8")
            return mock.Mock(returncode=1)

        rc, stderr, _, _ = self.run_adapter([
            "--agent-policy", aider_adapter.POLICY_ID,
            "--model", aider_adapter.POLICY_MODEL], fail)
        self.assertEqual(rc, 1)
        self.assertEqual(stderr.count("@agent-policy "), 1)
        self.assertIn("@agent-usage tokens_in=7 tokens_out=3", stderr)
        self.assertTrue(all(not path.exists() for path in seen))


if __name__ == "__main__":
    unittest.main()
