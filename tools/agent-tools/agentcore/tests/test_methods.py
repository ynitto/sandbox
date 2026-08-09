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


if __name__ == "__main__":
    unittest.main()
