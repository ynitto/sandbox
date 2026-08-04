"""agentcore.modeltier の単体テスト（モデル階層のオプトイン展開規則）。

    python -m unittest discover -s tools/agent-tools/agentcore/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import modeltier  # noqa: E402

TIERS = {"strong": {"model": "opus"}, "weak": {"model": "haiku"}}


class NormalizeTests(unittest.TestCase):
    def test_opt_in_absent_or_broken_is_empty(self):
        """未設定・型違い・空宣言はすべて {}（＝展開は何もしない）。"""
        for raw in (None, "", [], {}, {"purposes": {"plan": "weak"}},
                    {"strong": "opus"}, {"strong": {"model": ""}}):
            self.assertEqual(modeltier.normalize(raw), {}, raw)

    def test_values_are_normalized_like_agents(self):
        """agent_cli は小文字化・model は strip。既存 agents: の正規化と同じ流儀。"""
        got = modeltier.normalize({"strong": {"agent_cli": " Claude ", "model": " opus "}})
        self.assertEqual(got, {"strong": {"agent_cli": "claude", "model": "opus"}})

    def test_purposes_keeps_only_known_tier_values(self):
        got = modeltier.normalize({"weak": {"model": "haiku"},
                                   "purposes": {"Assess": "STRONG", "plan": "off",
                                                "route": "mystery", "": "weak"}})
        self.assertEqual(got["purposes"], {"assess": "strong", "plan": "off"})


class ExpandTests(unittest.TestCase):
    def test_no_tiers_returns_explicit_unchanged(self):
        """オプトイン: model_tiers 無しでは明示 agents: がそのまま（挙動不変）。"""
        explicit = {"plan": {"model": "opus"}}
        self.assertEqual(modeltier.expand("project", None, explicit), explicit)
        self.assertEqual(modeltier.expand("project", None, None), {})

    def test_default_assignment_project(self):
        got = modeltier.expand("project", TIERS)
        self.assertEqual(got["plan"], {"model": "opus"})
        self.assertEqual(got["assess"], {"model": "haiku"})
        self.assertNotIn("unknown", got)

    def test_deliverable_kinds_untouched_for_flow(self):
        """成果物を直接作る kind（worker/work/generate/…）は既定で触らない。"""
        got = modeltier.expand("flow", TIERS)
        self.assertEqual(got["planner"], {"model": "opus"})
        self.assertEqual(got["classify"], {"model": "haiku"})
        for untouched in ("worker", "work", "generate", "synthesize", "map", "reduce"):
            self.assertNotIn(untouched, got)

    def test_explicit_wins_whole_entry(self):
        """明示 agents: は purpose 単位で丸ごと勝つ（tier の model と部分マージしない）。"""
        got = modeltier.expand("project", TIERS, {"assess": {"agent_cli": "ollama"}})
        self.assertEqual(got["assess"], {"agent_cli": "ollama"})

    def test_purposes_override_and_off(self):
        raw = dict(TIERS, purposes={"assess": "strong", "plan": "off", "generate": "weak"})
        got = modeltier.expand("flow", raw)
        self.assertEqual(got.get("generate"), {"model": "haiku"})   # 明示で対象化できる
        got = modeltier.expand("project", raw)
        self.assertEqual(got["assess"], {"model": "opus"})
        self.assertNotIn("plan", got)                               # off = 適用除外

    def test_valid_filters_tier_keys_but_not_explicit(self):
        raw = dict(TIERS, purposes={"nonsense": "weak"})
        got = modeltier.expand("audit", raw, {"custom": {"model": "x"}},
                               valid=("extract", "distill", "review"))
        self.assertNotIn("nonsense", got)             # 階層由来は語彙で絞る
        self.assertEqual(got["custom"], {"model": "x"})  # 明示キーには触れない
        self.assertEqual(got["extract"], {"model": "haiku"})

    def test_single_tier_declaration_only_touches_that_tier(self):
        """weak だけ宣言 → strong 分類の purpose は触らない（グローバルのまま）。"""
        got = modeltier.expand("project", {"weak": {"model": "haiku"}})
        self.assertNotIn("plan", got)
        self.assertEqual(got["assess"], {"model": "haiku"})

    def test_unknown_workload_expands_nothing(self):
        self.assertEqual(modeltier.expand("amigos", TIERS), {})


if __name__ == "__main__":
    unittest.main()
