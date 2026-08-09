import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403


class MethodIntegrationTests(unittest.TestCase):
    def _tuning(self):
        return {
            "version": 1, "revision": 1,
            "methods": [{"id": "test-first", "enabled": False,
                         "fragments": [{"role": "worker", "text": "write the test first"}],
                         "when": {"tiers": ["economy"], "max_relative_cost": 1},
                         "description": "d", "origin": "test"}],
            "trials": [{"id": "trial-1", "assignment": "alternating",
                        "variants": [{"id": "baseline", "methods": []},
                                     {"id": "candidate", "methods": ["test-first"]}]}],
        }

    def test_retry_suffix_alternates_and_records_variant(self):
        data = self._tuning()
        with mock.patch.object(kf._methodlib, "load", return_value=data), \
             mock.patch.object(kf._methodlib, "current_tier", return_value="economy"), \
             mock.patch.object(kf._methodlib, "relative_cost", return_value=0.5):
            kf._set_method_context("task-r0", "n1")
            self.assertEqual(kf._apply_methods("prompt", "work", "kiro", "m"), "prompt")
            self.assertEqual(kf._last_methods("work")["trial"]["variant"], "baseline")
            kf._set_method_context("task-r1", "n1")
            prompt = kf._apply_methods("prompt", "work", "kiro", "m")
            self.assertIn("write the test first", prompt)
            self.assertEqual(kf._method_ledger_fields("work")["methods"], ["test-first"])

    def test_bus_result_keeps_method_evidence(self):
        root = tempfile.mkdtemp(prefix="kf-method-bus-")
        self.addCleanup(shutil.rmtree, root, True)
        bus = kf.Bus(root, "r1")
        bus.ensure_run("req")
        bus.write_result("n1", "worker", "done", "ok", methods=["test-first"],
                         trial={"id": "trial-1", "variant": "candidate"})
        rec = bus.read_result("n1")
        self.assertEqual(rec["methods"], ["test-first"])
        self.assertEqual(rec["trial"], {"id": "trial-1", "variant": "candidate"})


if __name__ == "__main__":
    unittest.main()
