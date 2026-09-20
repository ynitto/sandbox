"""agentcore.herdconfig と `agent-herd config` の契約。

縛るのは 3 つ: 設定ファイルの探索と形式（yaml / yml / json、既存の形式を保って書き戻す）、
`judge.model` の語彙（auto / off / モデル名。YAML の裸の off は False で届く）、
`agent-herd config` の入口（表示・--json・--check judge の終了コード・set / unset）。
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import herdconfig, herdcli  # noqa: E402


class HerdConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-herd-config-")
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"AGENT_PROJECT_AGENTS_HOME": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_file_is_auto(self):
        self.assertIsNone(herdconfig.find_path())
        self.assertEqual(herdconfig.load(), {})
        self.assertEqual(herdconfig.judge_setting(),
                         {"mode": "auto", "model": None, "error": None})

    def test_set_creates_yaml_and_unset_returns_to_auto(self):
        path = herdconfig.set_value("judge.model", "gemma4:e4b")
        self.assertEqual(path, self.home / "agent-herd.yaml")
        self.assertEqual(herdconfig.judge_setting()["model"], "gemma4:e4b")
        herdconfig.set_value("judge.model", "off")
        self.assertEqual(herdconfig.judge_setting()["mode"], "off")
        # 書き戻した off は文字列のまま（裸の off で False にならない）
        self.assertIn("'off'", path.read_text(encoding="utf-8"))
        herdconfig.unset_value("judge.model")
        self.assertEqual(herdconfig.judge_setting()["mode"], "auto")
        self.assertEqual(herdconfig.load(), {}, "空になった judge は消す")

    def test_bare_yaml_off_is_off(self):
        (self.home / "agent-herd.yaml").write_text("judge:\n  model: off\n", encoding="utf-8")
        self.assertEqual(herdconfig.judge_setting()["mode"], "off")

    def test_existing_json_keeps_its_format_and_other_keys(self):
        (self.home / "agent-herd.json").write_text(
            '{"other": {"keep": 1}, "judge": {"model": "gemma4:12b"}}', encoding="utf-8")
        herdconfig.set_value("judge.model", "gemma4:e4b")
        data = json.loads((self.home / "agent-herd.json").read_text(encoding="utf-8"))
        self.assertEqual(data, {"other": {"keep": 1}, "judge": {"model": "gemma4:e4b"}})
        self.assertFalse((self.home / "agent-herd.yaml").exists())

    def test_unknown_key_is_refused(self):
        with self.assertRaises(herdconfig.ConfigError):
            herdconfig.set_value("judge.temperature", "0")

    def test_broken_file_is_reported_not_swallowed(self):
        (self.home / "agent-herd.yaml").write_text("judge: [\n", encoding="utf-8")
        with self.assertRaises(herdconfig.ConfigError):
            herdconfig.load()
        self.assertIn("YAML", herdconfig.judge_setting()["error"])

    def test_calibration_roundtrip_and_unset_preserve_model(self):
        policy = {"model": "gemma4:e4b", "method": "logprobs", "min_coverage": .8,
                  "thresholds": {"route": .8, "filter": .6, "assess": None}}
        herdconfig.set_value("judge.model", "gemma4:e4b")
        herdconfig.set_value("judge.calibration", json.dumps(policy))
        self.assertEqual(herdconfig.describe()["calibration"], policy)
        herdconfig.unset_value("judge.calibration")
        self.assertIsNone(herdconfig.calibration_setting())
        self.assertEqual(herdconfig.judge_setting()["model"], "gemma4:e4b")

    def test_invalid_calibration_never_overwrites_settings(self):
        policy = {"model": "gemma4:e4b", "method": "logprobs", "min_coverage": .8,
                  "thresholds": {"route": .8}}
        herdconfig.set_value("judge.calibration", policy)
        for patch in ({"method": "text"}, {"min_coverage": float("nan")},
                      {"thresholds": {"route": 1.1}}, {"thresholds": {"typo": .8}},
                      {"thresholds": {"route": True}}, {"model": ""}):
            with self.assertRaises(herdconfig.ConfigError):
                herdconfig.set_value("judge.calibration", {**policy, **patch})
            self.assertEqual(herdconfig.calibration_setting(), policy)


class ConfigCommandTests(HerdConfigTests):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        rc = herdcli.cmd_config(argv, out=out, err=err)
        return rc, out.getvalue(), err.getvalue()

    def test_show_and_json(self):
        rc, out, _ = self._run([])
        self.assertEqual(rc, 0)
        self.assertIn("auto", out)
        rc, out, _ = self._run(["--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIsNone(data["path"])
        self.assertEqual(data["judge"]["mode"], "auto")

    def test_set_then_check_judge(self):
        rc, _, _ = self._run(["--check", "judge"])
        self.assertEqual(rc, 1, "指名が無ければ 1")
        rc, out, _ = self._run(["set", "judge.model", "gemma4:e4b"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["judge"]["model"], "gemma4:e4b")
        rc, out, _ = self._run(["--check", "judge"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["mode"], "pinned")
        rc, _, _ = self._run(["set", "judge.model", "off"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._run(["--check", "judge"])[0], 1)
        rc, _, _ = self._run(["unset", "judge.model"])
        self.assertEqual(rc, 0)
        self.assertEqual(herdconfig.judge_setting()["mode"], "auto")

    def test_argument_errors_are_exit_2(self):
        self.assertEqual(self._run(["set", "judge.model"])[0], 2)
        self.assertEqual(self._run(["set", "nope", "x"])[0], 2)
        self.assertEqual(self._run(["--check", "decide"])[0], 2)
        self.assertEqual(self._run(["--bogus"])[0], 2)

    def test_main_dispatches_config(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = herdcli.main(["config", "--json"], prog="agent-herd")
        self.assertEqual(rc, 0)
        self.assertIn('"judge"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
