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

    def test_cli_name_without_resolve_variant_falls_back_to_base(self):
        """用途別の変種振り替え（resolve_variant）が無い木では base のまま返し、欠けたことを残す。"""
        class _Cli:
            @staticmethod
            def json_variant(name):  # 旧シンボルだけが残る木を模す
                return f"{name}-json"

        stub = mock.Mock(spec=["_agentcli", "LIST_CONTRACT_ROLES"])
        stub._agentcli = _Cli
        stub.LIST_CONTRACT_ROLES = frozenset({"split"})
        with mock.patch.object(engine, "_FLOW", stub):
            self.assertEqual(engine.cli_name_for("split"), "ollama")
        self.assertTrue(any("resolve_variant" in gap for gap in engine.missing()))

    def test_cli_name_uses_resolve_variant_when_present(self):
        """resolve_variant を持つ木は本番の解決器をそのまま呼ぶ。"""
        class _Cli:
            @staticmethod
            def resolve_variant(name, purpose):
                return {"agent_cli": f"{name}-{purpose}", "default_model": None} if purpose == "split" else None

        stub = mock.Mock(spec=["_agentcli", "VARIANT_ELIGIBLE_ROLES"])
        stub._agentcli = _Cli
        stub.VARIANT_ELIGIBLE_ROLES = frozenset({"split", "verify"})
        with mock.patch.object(engine, "_FLOW", stub):
            self.assertEqual(engine.cli_name_for("split"), "ollama-split")
            self.assertEqual(engine.cli_name_for("planner"), "ollama")  # 対象外の役割は素通り
        self.assertEqual(engine.missing(), [])

    def test_missing_is_empty_when_the_engine_is_complete(self):
        """揃っている木では 1 件も記録しない（常時警告は読まれなくなる）。"""
        engine.unwrap_list({"data": [1]})
        engine.cli_name_for("split")
        engine.extract_json('{"a": 1}')
        self.assertEqual(engine.missing(), [])

    def test_extract_json_still_parses_without_the_engine(self):
        with mock.patch.object(engine, "_FLOW", object()):
            self.assertEqual(engine.extract_json('{"a": 1}'), {"a": 1})

    def test_extract_list_uses_the_engine_split_normalizer(self):
        class _Flow:
            @staticmethod
            def extract_list(text):
                return [text]

        with mock.patch.object(engine, "_FLOW", _Flow):
            self.assertEqual(engine.extract_list("group"), ["group"])

    def test_load_env_uses_the_selected_cli_definition(self):
        class _Cli:
            @staticmethod
            def load_cli(name):
                return {"env": {"SELECTED": name}}

        stub = mock.Mock(spec=["_agentcli"])
        stub._agentcli = _Cli
        with mock.patch.object(engine, "_FLOW", stub):
            self.assertEqual(engine.load_env("ollama-list-thinking"),
                             {"SELECTED": "ollama-list-thinking"})


if __name__ == "__main__":
    unittest.main()
