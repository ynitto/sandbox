#!/usr/bin/env python3
import json
import os
import pathlib
import shutil
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

    def test_method_switches_by_tier_and_relative_cost(self):
        data = _data()
        data["methods"] = [{"id": "cheap-check", "enabled": True,
                            "fragments": [{"role": "session", "text": "cheap only"}],
                            "when": {"tiers": ["economy"], "max_relative_cost": 1},
                            "description": "d", "origin": "test"}]
        with mock.patch.object(al._methodlib, "current_tier", return_value="economy"), \
             mock.patch.object(al._methodlib, "relative_cost", return_value=0.5):
            self.assertIn("cheap only", al.render_method_blocks(data, "kiro", assignment_key="p1")["text"])
            al.bind_method_application("%1", "p1")
            self.assertEqual(al.method_evidence("%1")["methods"], ["cheap-check"])
        with mock.patch.object(al._methodlib, "current_tier", return_value="full"), \
             mock.patch.object(al._methodlib, "relative_cost", return_value=2):
            self.assertEqual(al.render_method_blocks(data, "kiro")["text"], "")

    def test_catalog_snapshot_and_custom_method_revision(self):
        with tempfile.TemporaryDirectory() as tuning, tempfile.TemporaryDirectory() as catalog, \
             mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": tuning,
                                          "AGENT_METHODS_DIR": catalog}):
            method = {"id": "test-first", "description": "d", "enabled": False,
                      "fragments": [{"role": "worker", "text": "test"}],
                      "when": {}, "origin": "built-in"}
            pathlib.Path(catalog, "test-first.json").write_text(
                json.dumps(method), encoding="utf-8")
            enabled = al.method_enable("test-first")
            self.assertEqual(enabled["revision"], 1)
            self.assertRegex(enabled["methods"][0]["source"],
                             r"^methods/test-first@[0-9a-f]{12}$")
            method["description"] = "changed"
            pathlib.Path(catalog, "test-first.json").write_text(
                json.dumps(method), encoding="utf-8")
            self.assertEqual(al._load_tuning()["methods"][0]["description"], "d")
            self.assertEqual(al.method_disable("test-first")["revision"], 2)
            custom = al.method_add("custom-check", "session", "double check",
                                   {"tiers": ["full"]})
            self.assertEqual(custom["revision"], 3)
            self.assertEqual(custom["methods"][-1]["source"], "custom/custom-check")

    def test_enable_refuses_rules_that_are_not_selected_automatically(self):
        """自動適用の対象は selection: auto だけ。per-task / engine は enable させない。

        書けてしまうと tuning.json に「効かない宣言」が残る（agentcore 側は自動注入から
        外す）ばかりか、選ばれ方が別系統のルールを全対象へ効かせたつもりにさせる。
        """
        with tempfile.TemporaryDirectory() as tuning, tempfile.TemporaryDirectory() as catalog, \
             mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": tuning,
                                          "AGENT_METHODS_DIR": catalog}):
            for mid, selection in (("split-policy-behavior", "engine"),
                                   ("integration-verify", "per-task")):
                pathlib.Path(catalog, f"{mid}.json").write_text(json.dumps({
                    "id": mid, "description": "d", "enabled": False, "selection": selection,
                    "fragments": [{"role": "worker", "text": "t"}], "origin": "built-in"}),
                    encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    al.method_enable(mid)
                self.assertIn("自動適用の対象ではありません", str(caught.exception))
            # 断ったのだから tuning.json は作られない（revision も進めない）
            self.assertEqual(al._read_tuning_raw(), {})




class MethodEditSafetyTests(unittest.TestCase):
    """編集系は「適用の視点」ではなく生の tuning.json を土台にする。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.methods = pathlib.Path(self.tmp, "methods")
        self.methods.mkdir()
        (self.methods / "test-first.json").write_text(json.dumps({
            "id": "test-first", "description": "テストから始める", "enabled": False,
            "fragments": [{"role": "worker", "text": "先にテストを書く"}],
            "when": {}, "origin": "built-in"}), encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": self.tmp,
                                               "AGENT_METHODS_DIR": str(self.methods)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, data):
        pathlib.Path(self.tmp, "tuning.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_enable_does_not_wipe_a_globally_disabled_contract(self):
        """`enabled: false` は「いま適用しない」であって「中身が無い」ではない。

        適用の視点（_load_tuning）を編集の土台にすると、全体を止めていた端末で
        injections / profiles / trials が消え、revision も 0 から振り直しになる。
        """
        self._write({"version": 1, "revision": 7, "enabled": False,
                     "injections": [{"id": "rtk", "trigger": "every_prompt",
                                     "source": {"type": "inline", "text": "x"}}],
                     "profiles": {"default": {"injections": ["rtk"], "env": []},
                                  "external-facing": {"injections": [], "env": []}}})
        data = al.method_enable("test-first")
        self.assertEqual(data["revision"], 8, "revision は後退しない")
        self.assertEqual([i["id"] for i in data["injections"]], ["rtk"], "注入が消えない")
        self.assertEqual(data["profiles"]["default"]["injections"], ["rtk"])
        self.assertFalse(data["enabled"], "止めていた事実も残る")

    def test_concurrent_write_is_refused_instead_of_overwriting(self):
        """4 つの書き手が全体を置き換える契約なので、書く直前に revision を照合する。"""
        self._write({"version": 1, "revision": 1})
        real = al._read_tuning_raw

        def bump_between_read_and_write():
            self._write({"version": 1, "revision": 99})   # 他の書き手が先に書いた
            return real()

        data = al._read_tuning_raw()
        with mock.patch.object(al, "_read_tuning_raw", side_effect=bump_between_read_and_write):
            with self.assertRaisesRegex(ValueError, "他の書き手"):
                al._write_tuning(dict(data), "test", base_revision=1)

if __name__ == "__main__":
    unittest.main()
