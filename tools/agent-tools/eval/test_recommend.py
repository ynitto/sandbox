"""評価 archive → agent-recommendation の契約。

CI は `python -m unittest discover` で回すので unittest.TestCase で書く
（素の `def test_*` は 1 件も収集されない）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import recommend

_ROOT = Path(__file__).resolve().parents[3]
ARCHIVE = Path(__file__).parent / "results" / "archive"
GENERATED_AT = dt.datetime(2026, 8, 26, tzinfo=dt.timezone.utc)
SCHEMA = _ROOT / "schemas" / "agent-recommendation.schema.json"


def _build():
    os.environ.setdefault("KIRO_AGENTS_DIR", str(_ROOT / "agents"))
    return recommend.build_recommendation(ARCHIVE, generated_at=GENERATED_AT, revision=1)


class RecommendationTests(unittest.TestCase):
    def test_local_tiers_are_the_single_word_herd(self):
        """人が打つのは `herd` の 1 語。具体名もモデルも書かない。"""
        document = _build()
        for tier in ("basic", "small"):
            self.assertEqual(document["tiers"][tier]["candidates"], [{"agent_cli": "herd"}])
        blob = json.dumps(document["tiers"], ensure_ascii=False)
        self.assertNotIn("gemma4", blob, "tiers にモデル名が漏れていない")
        self.assertNotIn("aider", blob, "tiers に具体の agent_cli が漏れていない")

    def test_cloud_tiers_are_slots_not_values(self):
        """クラウドは実測できないので枠だけ宣言する（値は適用時に人が選ぶ）。"""
        document = _build()
        self.assertEqual(document["tiers"]["medium"]["slots"], [{"requires": "cloud-standard"}])
        self.assertEqual(document["tiers"]["large"]["slots"], [{"requires": "cloud-premium"}])
        self.assertEqual(document["tiers"]["medium"]["candidates"], [])

    def test_herd_members_come_from_the_entrypoint_not_the_spelling(self):
        """一族は `command[0] == "agent-herd"` で決まる。クラウドは自動的に外れる。"""
        document = _build()
        self.assertEqual(document["herd"]["members"], ["aider", "ollama", "opencode"])
        for cloud in ("claude", "codex", "copilot", "cursor", "kiro"):
            self.assertNotIn(cloud, document["herd"]["members"])

    def test_expansion_keeps_unusable_members_visible(self):
        """裏付けの無い一族の候補も残す——「入っているのに使われない」理由を隠さない。"""
        document = _build()
        rows = {(r["agent_cli"], r["model"]): r for r in document["herd"]["expansion"]}
        self.assertIn(("aider", "gemma4:12b"), rows)
        self.assertFalse(rows[("aider", "gemma4:12b")]["usable"],
                         "12b のコード worker は blocked（wall 600/1800 とも 0 完走）")
        self.assertTrue(rows[("aider", "gemma4:e4b")]["usable"])
        self.assertIn("bounded-review", rows[("ollama", "gemma4:12b")]["qualified_for"])
        self.assertNotIn("bounded-review", rows[("ollama", "gemma4:e4b")]["qualified_for"])

    def test_required_models_exclude_candidates_without_evidence(self):
        """裏付けの無い候補のためにモデルを引かせない。"""
        document = _build()
        self.assertEqual(document["requires"]["models"], ["gemma4:12b", "gemma4:e4b"])
        self.assertEqual(document["requires"]["entrypoint"], "agent-herd")

    def test_qualifications_block_is_the_seed_itself(self):
        """適格性の生成は 1 実装のまま（第 2 実装を作らない）。"""
        import qualification_seed

        document = _build()
        expected = qualification_seed.build_seed(ARCHIVE, generated_at=GENERATED_AT, revision=1)
        self.assertEqual(document["qualifications"], expected)

    def test_execution_policy_is_auto(self):
        """おまかせのまま。節約にすると昇格受けが tier 検査で常に落ちる（2026-08-23 §2.2）。"""
        self.assertEqual(_build()["execution_policy"], {"mode": "auto"})

    def test_concurrency_is_declared_only_when_local_evidence_exists(self):
        document = _build()
        self.assertEqual(
            document["control"]["workloads"]["flow"]["concurrency"],
            {"max_runs": 1, "workers": 1})

    def test_evidence_covers_every_qualification(self):
        """推奨の値と根拠を同じ文書へ置く（別の場所へ探しに行かせない）。"""
        document = _build()
        pairs = {(row["agent_cli"], row["model"], row["operation_class"])
                 for row in document["evidence"]}
        for candidate in document["qualifications"]["candidates"]:
            for operation in candidate["qualifications"]:
                self.assertIn((candidate["agent_cli"], candidate["model"], operation), pairs)

    def test_output_is_deterministic(self):
        self.assertEqual(_build(), _build())

    def test_matches_the_schema_shape(self):
        """正典スキーマの required と enum を素の Python で突き合わせる（jsonschema に依存しない）。"""
        document = _build()
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        for key in schema["required"]:
            self.assertIn(key, document, f"必須キーが無い: {key}")
        self.assertEqual(document["version"], schema["properties"]["version"]["const"])
        self.assertIn(document["execution_policy"]["mode"],
                      schema["properties"]["execution_policy"]["properties"]["mode"]["enum"])
        statuses = schema["properties"]["evidence"]["items"]["properties"]["status"]["enum"]
        for row in document["evidence"]:
            self.assertIn(row["status"], statuses)


class DiffTests(unittest.TestCase):
    def test_unset_terminal_marks_everything_as_a_change(self):
        with tempfile.TemporaryDirectory() as empty:
            lines = recommend.diff_lines(_build(), Path(empty))
        changed = [line for line in lines if line.startswith("*")]
        self.assertTrue(any("実行レベル 単純作業" in line for line in changed))
        self.assertTrue(any("実行方針" in line for line in changed))
        self.assertTrue(any("適格性" in line for line in changed))

    def test_filled_cloud_slot_is_not_reported_as_a_change(self):
        """枠は「人が選ぶ場所」であって推奨値ではない。埋まっていれば差分にしない。"""
        document = _build()
        with tempfile.TemporaryDirectory() as dir_name:
            control = Path(dir_name)
            (control / "profiles.json").write_text(json.dumps({
                "version": 1,
                "tiers": {
                    "basic": {"order": 0, "candidates": [{"agent_cli": "herd"}]},
                    "small": {"order": 1, "candidates": [{"agent_cli": "herd"}]},
                    "medium": {"order": 2, "candidates": [{"agent_cli": "claude", "model": "sonnet"}]},
                    "large": {"order": 3, "candidates": [{"agent_cli": "claude", "model": "opus"}]},
                },
                "execution_policy": {"mode": "auto"},
            }), encoding="utf-8")
            lines = recommend.diff_lines(document, control)
        tier_lines = [line for line in lines if line.startswith(("*", " ")) and "実行レベル" in line]
        self.assertTrue(all(line.startswith(" ") for line in tier_lines),
                        f"充足した枠が差分に出ている: {tier_lines}")

    def test_diff_never_writes(self):
        with tempfile.TemporaryDirectory() as dir_name:
            control = Path(dir_name)
            recommend.diff_lines(_build(), control)
            self.assertEqual(sorted(p.name for p in control.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
