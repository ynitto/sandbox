import unittest

from agentcore import methods


class MethodTests(unittest.TestCase):
    def test_resource_match_and_alternating_trial(self):
        data = {"methods": [{"id": "guard", "enabled": False,
                              "when": {"tiers": ["small"], "max_relative_cost": 0},
                              "fragments": [{"role": "worker", "text": "check"}]}],
                "trials": [{"id": "guard-test", "variants": [
                    {"id": "control", "methods": []}, {"id": "candidate", "methods": ["guard"]}]}]}
        ctx = {"role": "worker", "tier": "small", "relative_cost": 0}
        control = methods.select(data, ctx, "req-x-r0")
        candidate = methods.select(data, ctx, "req-x-r1")
        self.assertEqual(control["methods"], [])
        self.assertEqual(candidate["methods"], ["guard"])
        self.assertEqual(candidate["trial"], {"id": "guard-test", "variant": "candidate"})
        self.assertEqual(methods.select(data, {**ctx, "tier": "large"}, "req-x-r1")["methods"], [])

    def test_non_auto_selections_never_auto_inject(self):
        """per-task / engine は enabled: true でも自動注入しない。

        選ばれ方が別系統（工程ごとの選択・エンジンが run パラメータで決定）なので、
        enabled で全対象へ効かせると選択と矛盾する二重注入になる
        （例: --split-policy file の run に behavior の指示も入る）。UI の出し分けでは
        守れない（CLI の enable・手書き tuning.json・run 複製が同じ穴を開ける）ため、
        自動注入の唯一のチョークポイントであるここで落とす。
        """
        def data(selection):
            method = {"id": "x", "enabled": True,
                      "fragments": [{"role": "worker", "text": "text"}]}
            if selection:
                method["selection"] = selection
            return {"methods": [method]}

        ctx = {"role": "worker"}
        self.assertEqual(methods.select(data(None), ctx)["methods"], ["x"])
        self.assertEqual(methods.select(data("auto"), ctx)["methods"], ["x"])
        for selection in ("per-task", "engine"):
            applied = methods.select(data(selection), ctx)
            self.assertEqual(applied["methods"], [], selection)
            self.assertEqual(applied["text"], "", selection)

    def test_trial_variant_cannot_smuggle_a_non_auto_method(self):
        """trial の variant が per-task / engine を名指ししても効かせない。

        効かなかった variant はその実行を代表しないので、trial としても記録しない
        （宣言だけで trial を名乗ると「測っていない」を「効かなかった」と誤って数える）。
        """
        data = {"methods": [{"id": "eng", "enabled": False, "selection": "engine",
                             "fragments": [{"role": "worker", "text": "text"}]}],
                "trials": [{"id": "t", "variants": [
                    {"id": "control", "methods": []}, {"id": "candidate", "methods": ["eng"]}]}]}
        applied = methods.select(data, {"role": "worker"}, "req-r1")
        self.assertEqual(applied["methods"], [])
        self.assertIsNone(applied["trial"])

    def test_auto_selectable_defaults_to_auto(self):
        self.assertTrue(methods.auto_selectable({"id": "a"}))
        self.assertTrue(methods.auto_selectable({"id": "a", "selection": "auto"}))
        self.assertFalse(methods.auto_selectable({"id": "a", "selection": "engine"}))
        self.assertFalse(methods.auto_selectable({"id": "a", "selection": "per-task"}))


if __name__ == "__main__":
    unittest.main()
