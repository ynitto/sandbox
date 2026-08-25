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

    def test_aider_enables_the_fixed_reliability_policy_once(self):
        spec = agentcli.load_cli("aider", project_dir=self.repo)
        command = spec["command"]
        self.assertEqual(command.count("--agent-policy"), 1)
        index = command.index("--agent-policy")
        self.assertEqual(command[index + 1], "gemma4-e4b-reliability-v1")

    def test_ollama_declares_the_json_variant_for_json_contract_roles(self):
        """申告はローダ経由で見る——用途別の起動形は profile なのでファイルは 1 つ。"""
        for role in ("planner", "evaluator", "filter", "judge", "reduce", "extract"):
            variant = agentcli.resolve_variant("ollama", role, project_dir=self.repo)
            self.assertEqual(variant["agent_cli"], "ollama-json", role)
        spec = agentcli.load_cli("ollama-json", project_dir=self.repo)
        self.assertEqual(spec["name"], "ollama", "台帳のキーは正典名（用途で割らない）")
        self.assertEqual(spec["profile"], "json")

    def test_ollama_declares_the_list_variant_for_split(self):
        # 配列契約（split）は JSON モードでは満たせない。配列用の起動形を申告していること。
        for name in ("ollama", "ollama-json"):
            variant = agentcli.resolve_variant(name, "split", project_dir=self.repo)
            self.assertEqual(variant["agent_cli"], "ollama-list", name)
        cmd = agentcli.load_cli("ollama-list", project_dir=self.repo)["command"]
        self.assertEqual(cmd[cmd.index("--format") + 1], "array")

    def test_the_ollama_roles_are_profiles_of_one_agent_not_separate_agents(self):
        """用途で agent_cli を増やさない（台帳と格付けのキーが割れないこと）。

        用途の次元は候補契約が既に持っている
        （candidate=(agent_cli, model) → qualifications: {operation_class → 格付け}、
        agent-audit の集計キーも (agent_cli, model, operation_class)）。同じ次元を
        agent_cli の値へ畳み込むと 1 実行系の実測が偽の候補へ割れ、運用者にも
        別エージェントに見える。
        """
        names = sorted(p.stem for p in (self.repo / "agents").glob("*.json"))
        self.assertNotIn("ollama-json", names, "用途別の定義ファイルは残さない")
        self.assertIn("ollama", names)
        for role in ("json", "list", "list-thinking", "read", "verify"):
            spec = agentcli.load_cli(f"ollama-{role}", project_dir=self.repo)
            self.assertEqual(spec["name"], "ollama", role)
            self.assertEqual(spec["profile"], role)
            self.assertEqual(agentcli.canonical_name(f"ollama-{role}", self.repo), "ollama")

    def test_a_role_does_not_inherit_the_base_interactive_face(self):
        """継承すると、対話面を持たない役割に base の TUI が生えて実行経路が変わる。

        agent-dashboard は interactive の有無も見て「ペインで駆動できるか」を決めるので、
        ここが崩れると定型業務が黙って別の経路へ行く。
        """
        base = agentcli.load_cli("ollama", project_dir=self.repo)
        self.assertTrue(base.get("interactive"), "base は TUI を持つ")
        for role in ("json", "list", "read", "verify"):
            spec = agentcli.load_cli(f"ollama-{role}", project_dir=self.repo)
            self.assertIsNone(spec.get("interactive"), role)

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
