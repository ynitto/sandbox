"""agentcore.agentcli.json_variant の単体テスト（JSON 契約の役割へ振り替える CLI 定義の申告）。

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


class JsonVariantTests(unittest.TestCase):
    """定義の申告だけで振り替わり、申告が壊れていても元の定義に倒れること。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir = self.root / "agents"      # plugin_dirs は project_dir/agents を見る
        self.dir.mkdir()
        agentcli.clear_cache()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(agentcli.clear_cache)

    def _resolve(self, name: str) -> str:
        return agentcli.json_variant(name, project_dir=self.root)

    def test_declared_variant_is_used(self):
        _write_cli(self.dir, "toy", json_variant="toy-json")
        _write_cli(self.dir, "toy-json")
        self.assertEqual(self._resolve("toy"), "toy-json")

    def test_no_declaration_keeps_the_original(self):
        _write_cli(self.dir, "toy")
        self.assertEqual(self._resolve("toy"), "toy")

    def test_variant_that_does_not_exist_is_ignored(self):
        # 設定ミスで実行を殺さない（振り替え先が無ければ元の定義で走る）
        _write_cli(self.dir, "toy", json_variant="toy-missing")
        self.assertEqual(self._resolve("toy"), "toy")

    def test_self_reference_does_not_loop(self):
        _write_cli(self.dir, "toy", json_variant="toy")
        self.assertEqual(self._resolve("toy"), "toy")

    def test_unknown_cli_is_returned_as_is(self):
        self.assertEqual(self._resolve("nope"), "nope")

    def test_variant_of_the_variant_is_not_followed(self):
        # 振り替えは 1 段だけ（連鎖させると定義ミスで無限に辿れる）
        _write_cli(self.dir, "toy", json_variant="toy-json")
        _write_cli(self.dir, "toy-json", json_variant="toy-json2")
        _write_cli(self.dir, "toy-json2")
        self.assertEqual(self._resolve("toy"), "toy-json")

    def test_declaration_is_case_insensitive(self):
        _write_cli(self.dir, "toy", json_variant="Toy-JSON")
        _write_cli(self.dir, "toy-json")
        self.assertEqual(self._resolve("toy"), "toy-json")


class ShippedDefinitionTests(unittest.TestCase):
    """同梱の ollama 定義が JSON 変種を申告していること（設計 §4.3 の実体）。"""

    def test_ollama_declares_the_json_variant(self):
        repo = Path(__file__).resolve().parents[4]
        spec = json.loads((repo / "agents" / "ollama.json").read_text(encoding="utf-8"))
        self.assertEqual(spec.get("json_variant"), "ollama-json")
        self.assertTrue((repo / "agents" / "ollama-json.json").exists())


if __name__ == "__main__":
    unittest.main()
