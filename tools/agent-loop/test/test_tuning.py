#!/usr/bin/env python3
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import agent_loop as al  # noqa: E402


def _data():
    return {
        "version": 1,
        "revision": 3,
        "injections": [
            {"id": "start", "trigger": "session_start",
             "source": {"type": "inline", "text": "start rules"}},
            {"id": "compact", "trigger": "every_prompt",
             "source": {"type": "inline", "text": "compact rules"}},
        ],
        "env": [{"id": "shim", "path_prepend": ["~/bin"], "vars": {"FLAG": "1"}}],
        "profiles": {
            "default": {"injections": ["start", "compact"], "env": ["shim"]},
            "external-facing": {"injections": [], "env": []},
        },
    }


class TuningTests(unittest.TestCase):
    def test_external_facing_disables_style_injection(self):
        self.assertIn("compact rules", al.render_tuning_blocks(
            _data(), "default", "kiro", include_session_start=False))
        self.assertEqual(al.render_tuning_blocks(
            _data(), "external-facing", "kiro", include_session_start=True), "")

    def test_env_and_session_start_are_applied_deterministically(self):
        env = al.tuning_launch_env(_data(), "default", "kiro", "/usr/bin")
        self.assertEqual(env["FLAG"], "1")
        self.assertTrue(env["PATH"].endswith(os.pathsep + "/usr/bin"))

        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._lock = threading.Lock()
        mgr._tuning_profiles = {"p": "default"}
        mgr._tuning_rev = {}
        with mock.patch.object(al, "_load_tuning", return_value=_data()):
            first = mgr._maybe_prepend_tuning("p", "task")
            second = mgr._maybe_prepend_tuning("p", "task")
            mgr.reset_tuning("p")
            after_clear = mgr._maybe_prepend_tuning("p", "task")
        self.assertIn("start rules", first)
        self.assertNotIn("start rules", second)
        self.assertIn("compact rules", second)
        self.assertIn("start rules", after_clear)

    def test_external_facing_drops_injections_even_if_the_file_declares_them(self):
        """スキーマは空を要求するが、tuning.json は人も書くファイルで検証は走らない。

        「外向き成果物へ文体圧縮を漏らさない」を約束だけに預けると 1 行で破れるので、
        読み手側で潰す。env（PATH・API キーの類）は落とさない——注入だけが文体に効く。
        """
        data = _data()
        data["profiles"]["external-facing"] = {"injections": ["compact"], "env": ["shim"]}
        self.assertEqual(al.render_tuning_blocks(
            data, "external-facing", "kiro", include_session_start=True), "")
        self.assertEqual(
            al.tuning_launch_env(data, "external-facing", "kiro", "/usr/bin")["FLAG"], "1")

    def test_missing_or_broken_contract_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AGENT_TUNING_DIR": tmp}
        ):
            self.assertIsNone(al._load_tuning())
            pathlib.Path(tmp, "tuning.json").write_text("{", encoding="utf-8")
            self.assertIsNone(al._load_tuning())


if __name__ == "__main__":
    unittest.main()
