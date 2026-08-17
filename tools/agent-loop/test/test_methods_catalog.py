import hashlib
import json
import pathlib
import unittest


class MethodCatalogTests(unittest.TestCase):
    def test_golden_catalog_has_exactly_twenty_four_valid_presets(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        paths = sorted((root / "methods").glob("*.json"))
        expected = {
            "restate-task", "path-grounding", "plan-first", "spec-first", "test-first",
            "failure-modes-first", "scope-guard", "one-change-per-step", "consistency-sweep",
            "output-contract-strict", "persist-until-done", "plan-reflect-each-tool",
            "evidence-or-fail", "no-self-approval", "adversarial-verify",
            "checklist-acceptance", "parallel-review", "council-review", "self-consistency",
            "derive-twice", "doc-follow-through",
            # 面（surface）ごとに plan 生成が自動付与する作業ルール
            # （docs/plans/2026-08-15-workflow-feature-improvement-proposals.md P1・P3）
            "ui-consistency", "test-green-evidence",
            # 実装フローの終端へ標準装備する統合検証の内容（同上 P1）
            "integration-verify",
        }
        methods = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        self.assertEqual({method["id"] for method in methods}, expected)
        self.assertEqual(len(paths), 24)
        self.assertTrue(all(method.get("origin") and method.get("fragments")
                            and method.get("enabled") is False for method in methods))

    def test_multi_step_planning_presets_do_not_target_lightweight_runs(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        ids = {"council-review", "derive-twice", "parallel-review", "self-consistency"}
        methods = {
            method["id"]: method
            for method in (json.loads((root / "methods" / f"{mid}.json").read_text(encoding="utf-8"))
                           for mid in ids)
        }
        for method in methods.values():
            self.assertEqual(method["when"]["tiers"], ["medium", "large"], method["id"])

    def test_basic_tier_only_gets_short_single_action_presets(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        ids = {"one-change-per-step", "path-grounding", "restate-task", "scope-guard"}
        for method_id in ids:
            method = json.loads((root / "methods" / f"{method_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(method["when"]["tiers"], ["basic", "small"], method_id)


if __name__ == "__main__":
    unittest.main()


class MethodSourceHashGoldenTests(unittest.TestCase):
    """同梱カタログの source ダイジェストを Python 側で固定する。

    ダイジェストは JS（agent-dashboard）にも実装があり、`source: methods/<id>@<hash>` は
    どちらが書いても同じでなければ「同じカタログ由来か」を突き合わせられない。両側が
    同じ golden を読むことで、片方の正規化（キー順・区切り・数値表記）が変わったら落ちる。
    """

    def test_shipped_catalog_hashes_match_the_shared_golden(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        golden = json.loads((root / "schemas" / "methods-source-hash.golden.json")
                            .read_text(encoding="utf-8"))["hashes"]
        actual = {}
        for path in sorted((root / "methods").glob("*.json")):
            method = json.loads(path.read_text(encoding="utf-8"))
            actual[method["id"]] = hashlib.sha256(
                json.dumps(method, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()).hexdigest()[:12]
        self.assertEqual(actual, golden)
