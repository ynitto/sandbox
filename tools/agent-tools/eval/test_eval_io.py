import sys
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_io import new_run_dir, safe_name  # noqa: E402
from run_suite import command_for  # noqa: E402
from coverage_eval import audit, load_manifest  # noqa: E402


class ResultLayoutTests(unittest.TestCase):
    def test_run_directory_is_unique_and_safe(self):
        with TemporaryDirectory() as tmp:
            first = new_run_dir(Path(tmp), "qwen:9b", "base line")
            second = new_run_dir(Path(tmp), "qwen:9b", "base line")
            self.assertNotEqual(first, second)
            self.assertIn("qwen-9b-base-line", first.name)

    def test_safe_name_has_a_nonempty_fallback(self):
        self.assertEqual(safe_name(":::"), "unknown")

    def test_worker_command_routes_ledger_to_unit_directory(self):
        args = Namespace(model="model:tag", cli="aider", repeat=2, wall=30,
                         embedding_model="embed", tfidf_only=False)
        cmd, env = command_for("worker", args, Path("/tmp/result/worker"))
        self.assertEqual(env["WORKER_EVAL_DIR"], "/tmp/result/worker")
        self.assertIn("model:tag", cmd)
        self.assertIn("aider", cmd)

    def test_judge_command_uses_the_selected_cli_as_its_variant_base(self):
        args = Namespace(model="model:tag", cli="aider", repeat=2, wall=30,
                         embedding_model="embed", tfidf_only=False)
        cmd, env = command_for("judge", args, Path("/tmp/result/judge"))
        self.assertEqual(env["JUDGE_EVAL_DIR"], "/tmp/result/judge")
        self.assertEqual(cmd[cmd.index("--base-cli") + 1], "aider")

    def test_coverage_inventory_matches_canonical_flow_roles(self):
        self.assertEqual(audit(load_manifest()), [])

    def test_coverage_unit_writes_a_machine_readable_report(self):
        args = Namespace(model="model:tag", cli="aider", repeat=2, wall=30,
                         embedding_model="embed", tfidf_only=False)
        cmd, env = command_for("coverage", args, Path("/tmp/result/coverage"))
        self.assertEqual(env, {})
        self.assertEqual(cmd[-1], "/tmp/result/coverage/coverage.json")


if __name__ == "__main__":
    unittest.main()
