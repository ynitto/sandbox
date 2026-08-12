import sys
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_io import new_run_dir, safe_name  # noqa: E402
from run_suite import command_for  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
