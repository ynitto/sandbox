"""エンジンが欠けた木でも、ハーネスが止まらず・黙りもしないこと。

未着地のシンボルへ直接触って全 run が起動前に死ぬ事故を 2 度踏んだ
（`LIST_CONTRACT_ROLES` と `unwrap_list`）。守るのは 2 つで、どちらか片方では足りない
——落ちないだけだと、欠けた木で取った数字を揃った木の数字として読んでしまう。

    python3 -m pytest tools/agent-tools/eval/test_engine.py
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402


class MissingEngineTests(unittest.TestCase):
    def setUp(self):
        engine._MISSING.clear()

    def tearDown(self):
        engine._MISSING.clear()

    def test_unwrap_list_without_the_engine_function(self):
        """器を剥がせない木では剥がさずに返し、欠けたことを残す。"""
        with mock.patch.object(engine, "_FLOW", object()):
            self.assertEqual(engine.unwrap_list({"data": [1, 2]}), {"data": [1, 2]})
        self.assertTrue(any("unwrap_list" in gap for gap in engine.missing()))

    def test_cli_name_falls_back_to_the_json_variant(self):
        """配列契約の振り替えが無ければ JSON 変種へ倒し、欠けたことを残す。"""
        class _Cli:
            @staticmethod
            def json_variant(name):
                return f"{name}-json"

        stub = mock.Mock(spec=["_agentcli"])
        stub._agentcli = _Cli
        with mock.patch.object(engine, "_FLOW", stub):
            self.assertEqual(engine.cli_name_for("split"), "ollama-json")
        self.assertTrue(any("list_variant" in gap for gap in engine.missing()))

    def test_missing_is_empty_when_the_engine_is_complete(self):
        """揃っている木では 1 件も記録しない（常時警告は読まれなくなる）。"""
        engine.unwrap_list({"data": [1]})
        engine.cli_name_for("split")
        engine.extract_json('{"a": 1}')
        self.assertEqual(engine.missing(), [])

    def test_extract_json_still_parses_without_the_engine(self):
        with mock.patch.object(engine, "_FLOW", object()):
            self.assertEqual(engine.extract_json('{"a": 1}'), {"a": 1})


if __name__ == "__main__":
    unittest.main()
