"""agentcore.agentcli.resolve_variant の単体テスト（用途別に振り替える CLI 定義の申告）。

設計: docs/plans/2026-08-08-agent-ollama-expansion-design.md §4.3
コンセプト: 柱3 / C9 — 仕事に足る最小のモデルへ流す設定を、人の手作業に払わせない。

    python -m unittest discover -s tools/agent-tools/agentcore/tests
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import agentcli  # noqa: E402


def _write_cli(d: Path, name: str, **extra) -> None:
    spec = {"name": name, "command": [f"{name}-bin"], "prompt_via": "stdin"}
    spec.update(extra)
    (d / f"{name}.json").write_text(json.dumps(spec), encoding="utf-8")


class ResolveVariantTests(unittest.TestCase):
    """定義の申告だけで振り替わり、申告が壊れていても None（元の定義のまま）に倒れること。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir = self.root / "agents"      # plugin_dirs は project_dir/agents を見る
        self.dir.mkdir()
        agentcli.clear_cache()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(agentcli.clear_cache)

    def _resolve(self, name: str, purpose: str):
        return agentcli.resolve_variant(name, purpose, project_dir=self.root)

    def test_declared_variant_is_used(self):
        _write_cli(self.dir, "toy", variants={"planner": "toy-json"})
        _write_cli(self.dir, "toy-json")
        self.assertEqual(self._resolve("toy", "planner"),
                         {"agent_cli": "toy-json", "default_model": None})

    def test_variant_carries_its_own_default_model(self):
        _write_cli(self.dir, "toy", variants={"verify": "toy-verify"})
        _write_cli(self.dir, "toy-verify", default_model="tuned-model")
        self.assertEqual(self._resolve("toy", "verify"),
                         {"agent_cli": "toy-verify", "default_model": "tuned-model"})

    def test_no_declaration_returns_none(self):
        _write_cli(self.dir, "toy")
        self.assertIsNone(self._resolve("toy", "planner"))

    def test_undeclared_purpose_returns_none(self):
        _write_cli(self.dir, "toy", variants={"planner": "toy-json"})
        _write_cli(self.dir, "toy-json")
        self.assertIsNone(self._resolve("toy", "verify"), "宣言の無い用途では振り替えない")

    def test_variant_that_does_not_exist_is_ignored(self):
        # 設定ミスで実行を殺さない（振り替え先が無ければ None＝元の定義で走る）
        _write_cli(self.dir, "toy", variants={"planner": "toy-missing"})
        self.assertIsNone(self._resolve("toy", "planner"))

    def test_self_reference_does_not_loop(self):
        _write_cli(self.dir, "toy", variants={"planner": "toy"})
        self.assertIsNone(self._resolve("toy", "planner"))

    def test_unknown_cli_returns_none(self):
        self.assertIsNone(self._resolve("nope", "planner"))

    def test_variant_of_the_variant_is_not_followed(self):
        # 振り替えは 1 段だけ（連鎖させると定義ミスで無限に辿れる）
        _write_cli(self.dir, "toy", variants={"planner": "toy-json"})
        _write_cli(self.dir, "toy-json", variants={"planner": "toy-json2"})
        _write_cli(self.dir, "toy-json2")
        self.assertEqual(self._resolve("toy", "planner"),
                         {"agent_cli": "toy-json", "default_model": None})

    def test_declaration_is_case_insensitive(self):
        _write_cli(self.dir, "toy", variants={"PLANNER": "Toy-JSON"})
        _write_cli(self.dir, "toy-json")
        self.assertEqual(self._resolve("toy", "planner"),
                         {"agent_cli": "toy-json", "default_model": None})

    def test_malformed_variants_field_is_rejected(self):
        _write_cli(self.dir, "toy", variants=["not", "an", "object"])
        with self.assertRaises(agentcli.AgentCliError):
            agentcli.load_cli("toy", project_dir=self.root)


class ShippedDefinitionTests(unittest.TestCase):
    """同梱の ollama / aider 定義が用途別の変種を申告していること（設計 §4.3 の実体）。"""

    def setUp(self):
        self.repo = Path(__file__).resolve().parents[4]
        agentcli.clear_cache()
        self.addCleanup(agentcli.clear_cache)

    def test_aider_split_resolves_to_the_thinking_list_variant(self):
        """Aider/Gemma の split は Thinking を使える専用起動形へ振り替わる。"""
        variant = agentcli.resolve_variant("aider", "split", project_dir=self.repo)
        self.assertEqual(variant["agent_cli"], "ollama-list-thinking")
        spec = agentcli.load_cli("ollama-list-thinking", project_dir=self.repo)
        cmd = spec["command"]
        self.assertEqual(cmd[cmd.index("--think") + 1], "on")
        self.assertNotIn("--format", cmd)
        self.assertEqual(json.loads(spec["env"]["AGENT_OLLAMA_OPTIONS"])["temperature"], 0)

    def test_ollama_declares_the_json_variant_for_json_contract_roles(self):
        spec = json.loads((self.repo / "agents" / "ollama.json").read_text(encoding="utf-8"))
        for role in ("planner", "evaluator", "filter", "judge", "reduce", "extract"):
            self.assertEqual(spec["variants"].get(role), "ollama-json", role)
        self.assertTrue((self.repo / "agents" / "ollama-json.json").exists())

    def test_ollama_declares_the_list_variant_for_split(self):
        # 配列契約（split）は JSON モードでは満たせない。配列用の起動形を申告していること。
        for name in ("ollama", "ollama-json"):
            spec = json.loads((self.repo / "agents" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(spec["variants"].get("split"), "ollama-list", name)
        cmd = json.loads((self.repo / "agents" / "ollama-list.json").read_text(encoding="utf-8"))["command"]
        self.assertEqual(cmd[cmd.index("--format") + 1], "array")

    def test_ollama_declares_the_retrieve_variant(self):
        # retrieve は根拠を実際に読める必要がある。ollama-json へ寄せると read tool を失う。
        for name in ("ollama", "ollama-json"):
            variant = agentcli.resolve_variant(name, "retrieve", project_dir=self.repo)
            self.assertEqual(variant["agent_cli"], "ollama-read", name)

    def test_ollama_declares_the_verify_variant_with_its_tuned_default_model(self):
        # verify 専用チューニング（12b・stall-timeout）は variants 経由でだけ到達できる
        # ——ollama-verify は他の変種から辿られないので、ここが唯一の到達経路。
        variant = agentcli.resolve_variant("ollama", "verify", project_dir=self.repo)
        self.assertEqual(variant["agent_cli"], "ollama-verify")
        self.assertEqual(variant["default_model"], "gemma4:12b")


if __name__ == "__main__":
    unittest.main()
