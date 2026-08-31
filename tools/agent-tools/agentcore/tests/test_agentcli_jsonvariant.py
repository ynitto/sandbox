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


class JsonObjectOnlyFieldTests(unittest.TestCase):
    """器の性質 `json_object_only` の宣言（出力契約の分岐は argv の綴りではなくこれを読む）。

    agent-project の plan は、この宣言が真の器でだけ「1 件ずつ」契約へ切り替える
    ——配列を返せる器（クラウド CLI ほか）に 1 件ずつを課すと、タスク K 件に
    K+1 回の呼び出しを払う（2026-08-31 の 1 件ずつ化の適用範囲の限定）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir = self.root / "agents"
        self.dir.mkdir()
        agentcli.clear_cache()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(agentcli.clear_cache)

    def test_default_is_free_text(self):
        _write_cli(self.dir, "toy")
        self.assertFalse(agentcli.load_cli("toy", project_dir=self.root)["json_object_only"])

    def test_profile_can_declare_it_without_leaking_to_the_base(self):
        _write_cli(self.dir, "toy", profiles={
            "json": {"command": ["toy-bin", "--format", "json"], "json_object_only": True}})
        self.assertTrue(
            agentcli.load_cli("toy-json", project_dir=self.root)["json_object_only"])
        self.assertFalse(
            agentcli.load_cli("toy", project_dir=self.root)["json_object_only"],
            "base は自由文の器のまま")


class ShippedDefinitionTests(unittest.TestCase):
    """同梱の ollama / aider 定義が用途別の変種を申告していること（設計 §4.3 の実体）。"""

    def setUp(self):
        self.repo = Path(__file__).resolve().parents[4]
        agentcli.clear_cache()
        self.addCleanup(agentcli.clear_cache)

    def test_split_resolves_to_the_same_variant_from_both_local_bases(self):
        """split の起動形は base に依らない（用途が同じなら起動形も同じ）。

        以前は aider 経路だけ `ollama-list-thinking` を指していた。あれに実測は無く
        （split 4/6 は `--format array` の数字）、同じ用途へ 2 つの答えを持つ理由が
        無かったので 2026-08-29 に測ってあるほうへ統一した。`list-thinking` の起動形
        自体は残す——think の効きを測り直す（計画 P(2)）ときの対照になる。
        """
        for base in ("aider", "ollama"):
            with self.subTest(base=base):
                variant = agentcli.resolve_variant(base, "split", project_dir=self.repo)
                self.assertEqual(variant["agent_cli"], "ollama-list")
        spec = agentcli.load_cli("ollama-list", project_dir=self.repo)
        cmd = spec["command"]
        self.assertEqual(cmd[cmd.index("--format") + 1], "array")

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

    def test_readonly_is_no_writes_not_no_tools_on_capable_containers(self):
        """readonly は「書かない」の宣言であって「道具ゼロ」ではない。

        器の強い CLI（クラウド）では読み取りの道具を残す——高性能モデルは適度な自由度
        （プロンプト外の材料を自分で読める）を持たせたほうが良く、書込の禁止は
        CLI 側の読み取り専用モードが担う（codex の --sandbox read-only が元からこの姿勢。
        claude は対話面の readonly が既に plan モードだけだった——ヘッドレスを揃えた）。
        道具ゼロの手当ては、道具でプロンプト内の整形を壊す小さいモデル（ローカルの器）
        にだけ掛ける。
        """
        claude = agentcli.load_cli("claude", project_dir=self.repo)
        self.assertEqual(claude["readonly_args"], ["--permission-mode", "plan"])
        self.assertEqual(claude["interactive"]["readonly_args"], claude["readonly_args"],
                         "ヘッドレスと対話面で readonly の姿勢を揃える")
        kiro = agentcli.load_cli("kiro", project_dir=self.repo)
        self.assertEqual(kiro["readonly_args"], ["--trust-tools=fs_read"],
                         "退避時に既に信頼していた読み取り道具を、非退避でも同じにする")
        codex = agentcli.load_cli("codex", project_dir=self.repo)
        self.assertEqual(codex["readonly_args"], ["--sandbox", "read-only"])
        # ローカルの器は道具ゼロのまま——道具を持った e4b はプロンプト内で完結する整形を
        # シェルで解こうとして壊す（実測 2026-08-30: map 2/5・道具ゼロで 5/5）。
        ollama = agentcli.load_cli("ollama", project_dir=self.repo)
        self.assertIn("--tools", ollama["write_args"])
        self.assertNotIn("--tools", ollama["readonly_args"])

    def test_the_json_container_declares_json_object_only(self):
        """`--format json` の器は宣言でオブジェクト限定と分かる（plan の契約分岐が読む）。"""
        self.assertTrue(
            agentcli.load_cli("ollama-json", project_dir=self.repo)["json_object_only"])
        for name in ("ollama", "ollama-list", "claude", "kiro", "codex", "copilot"):
            self.assertFalse(
                agentcli.load_cli(name, project_dir=self.repo)["json_object_only"], name)

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
