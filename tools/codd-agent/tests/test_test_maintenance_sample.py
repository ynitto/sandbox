"""割引サンプルが生成する結果を codd-agent で照合する。"""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("codd_maintenance_sample", TOOL / "machine/codd.py")
codd = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = codd
SPEC.loader.exec_module(codd)
LINT = TOOL.parent / "testcase-lint/bin/testcase-lint.js"
LINT_READY = bool(shutil.which("node")) and LINT.is_file() and (LINT.parents[1] / "node_modules/yaml").exists()


@unittest.skipUnless(LINT_READY, "node と testcase-lint の依存（npm install）が要る")
class MaintenanceSampleTest(unittest.TestCase):
    def test_discount_sample_produces_accepted_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "sample"
            shutil.copytree(TOOL / "samples/test-maintenance", root,
                            ignore=shutil.ignore_patterns("results", "__pycache__"))

            def run_command(*argv, env=None):
                result = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            def read_json(relative):
                return json.loads((root / relative).read_text(encoding="utf-8"))

            machine = root / ".statemachine/codd"
            machine.mkdir(parents=True)
            shutil.copyfile(root / "codd-config.example.json", machine / "codd.json")
            run_command("git", "init", "-q", "-b", "main")
            run_command("git", "add", "-A")
            run_command("git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-qm", "サンプルの初回コミット")
            run_command("node", str(LINT))
            (root / "results/case-check.json").unlink()

            # TESTCASE_LINT には実行ファイルのパスを渡す。
            launcher = Path(folder) / "testcase-lint"
            launcher.write_text(f'#!/bin/sh\nexec node {shlex.quote(str(LINT))} "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)

            with mock.patch.object(codd, "MACHINE_DIR", machine):
                ctx = codd.Ctx(root)
                ctx.data.mkdir(exist_ok=True)
                receipt = {"run_id": "discount-sample-run", "inputs_digest": codd.execution_inputs(ctx)}
                ctx._test_receipt = receipt
                run_command(*codd.test_commands(ctx.config["test"])[0][1], env={
                    **os.environ,
                    "TESTCASE_LINT": str(launcher),
                    "TEST_EVIDENCE_RUN_ID": receipt["run_id"],
                    "TEST_EVIDENCE_INPUTS_DIGEST": receipt["inputs_digest"],
                })

                catalog = read_json("results/cases.json")
                case_file = "tests/cases/discount.yaml"
                self.assertEqual({(case["file"], case["id"]) for case in catalog["cases"]},
                                 {(case_file, "DISC-01"), (case_file, "DISC-02")})
                self.assertEqual(read_json("results/case-check.json")["purpose"], "check")
                execution = read_json("results/unit.json")
                self.assertEqual(execution["purpose"], "execution")
                for key, value in receipt.items():
                    self.assertEqual(execution[key], value)
                self.assertEqual({(item["id"], item["status"]) for item in execution["items"]}, {
                    ("DiscountTest::test_applies_at_10000", "passed"),
                    ("DiscountTest::test_none_below_10000", "passed"),
                })
                bodies = {heading: "なし" for heading in codd.PLAN_HEADINGS}
                self.assertEqual(codd.bindings_execution_problems(ctx, 0, {("", case_file)}, bodies), [])


if __name__ == "__main__":
    unittest.main()
