"""入力変換・lint・異なる言語のランナー・結果照合を通した任意の結合テスト。"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[2]
READY = (shutil.which("node") and (TOOLS / "gherkin-cases/node_modules/@cucumber/gherkin").exists()
         and (TOOLS / "testcase-lint/node_modules/yaml").exists())


@unittest.skipUnless(READY, "install the optional input converter and lint dependencies to run this integration")
class IntegrationTest(unittest.TestCase):
    def test_unmanaged_temporary_gherkin_tag_survives_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "shipping.feature").write_text("""# language: ja
機能: 送料
  @case:SHIP-01 @temporary
  シナリオ: 送料
    もし 5000円の注文の送料を計算する
    ならば 送料は0円になる
""", encoding="utf-8")
            converted = subprocess.run(["node", str(TOOLS / "gherkin-cases/bin/gherkin-cases.js"),
                                        "shipping.feature", "-o", "generated/cases.json"], cwd=root, capture_output=True, text=True)
            self.assertEqual(converted.returncode, 0, converted.stdout + converted.stderr)
            checked = subprocess.run(["node", str(TOOLS / "testcase-lint/bin/testcase-lint.js"), "generated/cases.json",
                                      "--fail-on", "warning", "-f", "json"], cwd=root, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
            data = json.loads(checked.stdout)
            finding = next(f for f in data["findings"] if f["code"] == "T01")
            self.assertEqual(finding["cases"], ["SHIP-01"])
            self.assertEqual(finding["file"], "shipping.feature")

    def test_feature_to_python_unit_and_node_cli_e2e_with_exact_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "app"
            shutil.copytree(TOOLS / "test-evidence/examples/shipping", root,
                            ignore=shutil.ignore_patterns("results", "__pycache__"))
            feature = root / "tests/shipping.feature"
            feature.write_text("""# language: ja
機能: 送料
  シナリオアウトライン: 金額<金額>円の送料
    もし 商品合計<金額>円の注文の送料を計算する
    ならば 送料は<送料>円になる
    例:
      | case_id | 金額 | 送料 |
      | SHIP-01 | 4999 | 500 |
      | SHIP-02 | 5000 | 0 |
""", encoding="utf-8")
            (root / "testcase-lint.config.yaml").write_text("cases: [generated/cases.json]\nevidence: results/check.json\n")
            bindings = json.loads((root / "test-bindings.json").read_text())
            for binding in bindings["bindings"]:
                binding["case"]["file"] = "tests/shipping.feature"
            (root / "test-bindings.json").write_text(json.dumps(bindings))

            def run(command, env=None):
                result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            run(["node", str(TOOLS / "gherkin-cases/bin/gherkin-cases.js"), "tests/shipping.feature", "-o", "generated/cases.json"])
            run(["node", str(TOOLS / "testcase-lint/bin/testcase-lint.js"), "-f", "json", "-o", "results/cases.json"])
            catalog = json.loads((root / "results/cases.json").read_text())
            self.assertEqual({c["file"] for c in catalog["cases"]}, {"tests/shipping.feature"})
            self.assertEqual({c["id"] for c in catalog["cases"]}, {"SHIP-01", "SHIP-02"})
            machine = root / ".statemachine/codd"
            machine.mkdir(parents=True)
            config = {"side": "impl", "refs": [], "graphify": "off", "test_bindings": ["test-bindings.json"],
                      "test_catalogs": ["results/cases.json"], "evidence": ["results/*.json"]}
            (machine / "codd.json").write_text(json.dumps(config))
            run(["git", "init", "-q", "-b", "main"])
            run(["git", "add", "-A"])
            run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "initial"])
            spec = importlib.util.spec_from_file_location("codd_input_integration", TOOLS / "codd-agent/machine/codd.py")
            codd = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = codd
            spec.loader.exec_module(codd)
            with mock.patch.object(codd, "MACHINE_DIR", machine):
                ctx = codd.Ctx(root)
                ctx.data.mkdir(exist_ok=True)
                ctx._test_receipt = {"run_id": "integration-run", "inputs_digest": codd.execution_inputs(ctx)}
                env = {**os.environ, "PYTHON": sys.executable, "TEST_EVIDENCE_RUN_ID": ctx._test_receipt["run_id"],
                       "TEST_EVIDENCE_INPUTS_DIGEST": ctx._test_receipt["inputs_digest"]}
                for suite, command in (("unit", [sys.executable, "run_unit.py"]),
                                       ("e2e", ["node", "--test", "--test-reporter=junit", "tests/e2e.test.cjs"])):
                    run([sys.executable, str(TOOLS / "test-evidence/run.py"), "--bindings", "test-bindings.json", "--suite", suite,
                         "--junit", "-", "--output", f"results/{suite}.json", "--", *command], env=env)
                problems = codd.bindings_execution_problems(ctx, 0, {("", "tests/shipping.feature")},
                                                          {h: "なし" for h in codd.PLAN_HEADINGS})
                self.assertEqual(problems, [])
            feature.write_text(feature.read_text().replace("4999", "4998"), encoding="utf-8")
            stale = subprocess.run(["node", str(TOOLS / "testcase-lint/bin/testcase-lint.js"), "-f", "json"],
                                   cwd=root, capture_output=True, text=True)
            self.assertEqual(stale.returncode, 1)
            self.assertIn("D00", {f["code"] for f in json.loads(stale.stdout)["findings"]})


if __name__ == "__main__":
    unittest.main()
