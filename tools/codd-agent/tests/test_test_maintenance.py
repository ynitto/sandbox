"""任意の対応表と、個別テストの実行証拠の検証。外部サービスは使わない。"""
import hashlib
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

TOOL = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("codd_maintenance", TOOL / "machine/codd.py")
codd = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = codd
SPEC.loader.exec_module(codd)
LINT = TOOL.parent / "testcase-lint/bin/testcase-lint.js"
LINT_READY = bool(shutil.which("node")) and (TOOL.parent / "testcase-lint/node_modules/yaml").exists()


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.machine = self.root / ".statemachine/codd"
        self.machine.mkdir(parents=True)
        self.config = {"side": "impl", "refs": [], "graphify": "off", "test_bindings": ["test-bindings.json"],
                       "evidence": ["results/*.json"]}
        self.binding = {"version": 1, "bindings": [{"case": {"file": "tests/cases.yaml", "id": "SHIP-01"},
                        "targets": [{"suite": "unit", "file": "tests/shipping.test.js", "selector": "free"}]}]}
        self.write("tests/cases.yaml", "cases:\n  - {id: SHIP-01, steps: [送料を計算する], expected: [送料は0円になる]}\n")
        self.write("tests/shipping.test.js", "require('node:test')('free', () => require('node:assert/strict').equal(0, 0));\n")
        self.write("src/app.py", "def shipping():\n    return 0\n")
        self.write(".gitignore", ".codd/\nresults/\n__pycache__/\n")
        self.json("test-bindings.json", self.binding)
        self.json(".statemachine/codd/codd.json", self.config)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "initial")
        self.patch = mock.patch.object(codd, "MACHINE_DIR", self.machine)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.ctx = codd.Ctx(self.root)
        self.ctx.data.mkdir(exist_ok=True)
        self.bodies = {heading: "なし" for heading in codd.PLAN_HEADINGS}
        self.required = {("", "tests/cases.yaml")}

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def json(self, rel, data):
        self.write(rel, json.dumps(data, ensure_ascii=False))

    def digest(self, rel):
        return hashlib.sha256((self.root / rel).read_bytes()).hexdigest()

    def receipt(self):
        self.ctx._test_receipt = {"run_id": "this-run", "inputs_digest": codd.execution_inputs(self.ctx)}

    def evidence(self):
        self.receipt()
        return {"version": 1, "purpose": "execution", **self.ctx._test_receipt,
                "items": [{"id": "free", "kind": "behavior", "file": "tests/shipping.test.js", "suite": "unit",
                           "status": "passed", "test_sha256": self.digest("tests/shipping.test.js"),
                           "cases": [{"file": "tests/cases.yaml", "id": "SHIP-01", "sha256": self.digest("tests/cases.yaml")}]}]}

    def problems(self):
        return codd.bindings_execution_problems(self.ctx, 0, self.required, self.bodies)

    def test_exact_current_test_passes_and_unrelated_file_is_not_required(self):
        self.json("results/unit.json", self.evidence())
        self.assertEqual(self.problems(), [])
        self.assertEqual(codd.bindings_execution_problems(self.ctx, 0, {("", "README.md")}, self.bodies), [])

    def test_lint_other_test_skip_unknown_old_run_and_old_inputs_are_not_success(self):
        for change in ("lint", "other", "skip", "unknown", "old-run", "old-input", "wrong-suite", "old-case", "old-test", "old-format", "wrong-version"):
            with self.subTest(change=change):
                data = self.evidence()
                row = data["items"][0]
                if change == "lint": data["purpose"] = "check"
                if change == "other": row["id"] = "other"
                if change == "skip": row["status"] = "skipped"
                if change == "unknown": row["status"] = "same"
                if change == "old-run": data["run_id"] = "previous-run"
                if change == "old-input": data["inputs_digest"] = "old"
                if change == "wrong-suite": row["suite"] = "e2e"
                if change == "old-case": row["cases"][0]["sha256"] = "0" * 64
                if change == "old-test": row["test_sha256"] = "0" * 64
                if change == "old-format": data.pop("purpose")
                if change == "wrong-version": data["version"] = 2
                self.json("results/unit.json", data)
                self.assertTrue(self.problems())

    def test_conflicting_duplicate_and_changed_source_do_not_pass(self):
        data = self.evidence()
        data["items"].append({**data["items"][0], "status": "failed"})
        self.json("results/unit.json", data)
        self.assertTrue(self.problems())
        self.json("results/unit.json", self.evidence())
        self.write("src/app.py", "def shipping():\n    return 500\n")
        self.assertIn("入力が変わりました", "\n".join(self.problems()))

    def test_behavior_report_can_select_case_id(self):
        self.ctx.config["test"] = ["true"]
        self.json("results/unit.json", self.evidence())
        behavior = codd.Behavior("送料", rel="tests/shipping.test.js", cases=("SHIP-01",))
        self.assertEqual(codd.behavior_result(self.ctx, behavior, 0), "通った")

    def test_explicit_binding_finds_case_and_both_suites(self):
        self.binding["bindings"][0]["targets"].append({"suite": "e2e", "file": "e2e/checkout.js", "selector": "checkout"})
        self.json("test-bindings.json", self.binding)
        self.write("e2e/checkout.js", "// no common name\n")
        hits = codd.affected_tests(self.ctx, [], {"": {"tests/shipping.test.js"}}, rebuild=False)
        self.assertIn(("", "tests/cases.yaml"), hits)
        self.assertIn(("", "e2e/checkout.js"), hits)

    def test_duplicate_and_outside_paths_are_rejected(self):
        self.binding["bindings"] *= 2
        self.json("test-bindings.json", self.binding)
        with self.assertRaisesRegex(codd.CoddError, "重複"):
            codd.load_test_bindings(self.ctx)
        self.binding["bindings"] = self.binding["bindings"][:1]
        self.binding["bindings"][0]["case"]["file"] = "../cases.yaml"
        self.json("test-bindings.json", self.binding)
        with self.assertRaisesRegex(codd.CoddError, "相対パス"):
            codd.load_test_bindings(self.ctx)

    def test_binding_removal_needs_manifest_in_plan(self):
        self.json("test-bindings.json", {"version": 1, "bindings": []})
        self.assertIn("計画なしに削除", "\n".join(self.problems()))
        self.bodies["## 自分の変更案"] = "- test-bindings.json — 廃止したケースの対応を削除する"
        self.assertEqual(self.problems(), [])

    def test_optional_catalog_detects_missing_case_and_stale_inventory(self):
        self.ctx.config["test_catalogs"] = ["results/cases.json"]
        self.json("results/cases.json", {"cases": [
            {"file": "tests/cases.yaml", "id": "SHIP-01", "input_sha256": self.digest("tests/cases.yaml")},
            {"file": "tests/cases.yaml", "id": "SHIP-02", "input_sha256": self.digest("tests/cases.yaml")},
        ]})
        self.json("results/unit.json", self.evidence())
        self.assertIn("SHIP-02", "\n".join(self.problems()))
        self.json("results/cases.json", {"cases": [{"file": "tests/cases.yaml", "id": "SHIP-01", "input_sha256": "old"}]})
        self.assertIn("ケース一覧が古い", "\n".join(self.problems()))

    def test_case_in_another_repository_keeps_its_identity_and_hash(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        docs = Path(other.name)
        (docs / "tests").mkdir()
        case_path = docs / "tests/cases.yaml"
        case_path.write_text((self.root / "tests/cases.yaml").read_text())
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=docs, check=True)
        subprocess.run(["git", "add", "-A"], cwd=docs, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "initial"], cwd=docs, check=True)
        self.config["refs"] = [{"name": "docs", "path": str(docs)}]
        self.json(".statemachine/codd/codd.json", self.config)
        self.binding["bindings"][0]["case"]["side"] = "docs"
        self.json("test-bindings.json", self.binding)
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "bind external case")
        self.ctx = codd.Ctx(self.root)
        self.required = {("docs", "tests/cases.yaml")}
        data = self.evidence()
        data["items"][0]["cases"][0].update(side="docs", sha256=hashlib.sha256(case_path.read_bytes()).hexdigest())
        self.json("results/unit.json", data)
        self.assertEqual(self.problems(), [])
        case_path.write_text("changed")
        self.assertIn("入力が変わりました", "\n".join(self.problems()))

    def test_same_repository_scopes_share_patterns_and_route_the_catalog(self):
        rel = "docs/cases/shipping.yaml"
        self.write(rel, (self.root / "tests/cases.yaml").read_text())
        self.config.update(scope=["src", "tests", "test-bindings.json"],
                           refs=[{"name": "docs", "path": ".", "scope": ["docs"]}],
                           tests=["tests/**", "docs/cases/**"], test_catalogs=["results/cases.json"])
        self.json(".statemachine/codd/codd.json", self.config)
        self.binding["bindings"][0]["case"].update(side="docs", file=rel)
        self.json("test-bindings.json", self.binding)
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "split scopes")
        self.ctx = codd.Ctx(self.root)
        self.required = {("docs", rel)}
        data = self.evidence()
        data["items"][0]["cases"][0].update(side="docs", file=rel, sha256=self.digest(rel))
        self.json("results/unit.json", data)
        self.json("results/cases.json", {"cases": [{"file": rel, "id": "SHIP-01", "input_sha256": self.digest(rel)}]})
        self.assertEqual(self.problems(), [])

    @unittest.skipUnless(LINT_READY, "node と testcase-lint の依存（npm install）が要る")
    def test_real_node_junit_runner_produces_accepted_evidence(self):
        self.write("testcase-lint.config.yaml", "bindings: test-bindings.json\n"
                   f"run:\n  unit: {{ command: [{json.dumps(shutil.which('node'))}, --test, --test-reporter=junit, tests/shipping.test.js] }}\n")
        self.receipt()
        command = [shutil.which("node"), str(LINT), "run"]
        env = {**os.environ, "TEST_EVIDENCE_RUN_ID": self.ctx._test_receipt["run_id"],
               "TEST_EVIDENCE_INPUTS_DIGEST": self.ctx._test_receipt["inputs_digest"]}
        result = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.problems(), [])


@unittest.skipUnless(LINT_READY, "node と testcase-lint の依存（npm install）が要る")
class GherkinIntegrationTest(unittest.TestCase):
    """.feature のケースを点検し、Python の単体と Node の E2E を実行した結果を、対応表で照合する。"""

    def test_feature_to_python_unit_and_node_cli_e2e_with_exact_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "app"
            shutil.copytree(TOOL.parent / "testcase-lint/examples/shipping", root,
                            ignore=shutil.ignore_patterns("results", "__pycache__"))
            config = (root / "testcase-lint.config.yaml").read_text(encoding="utf-8")
            (root / "testcase-lint.config.yaml").write_text(config.replace("[python3,", f"[{json.dumps(sys.executable)},"), encoding="utf-8")
            feature = root / "tests/shipping.feature"

            def run(command, env=None, code=0):
                result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                return result

            run(["node", str(LINT), "-f", "json", "-o", "results/cases.json"])
            catalog = json.loads((root / "results/cases.json").read_text())
            self.assertEqual({c["file"] for c in catalog["cases"]}, {"tests/shipping.feature"})
            self.assertEqual({c["id"] for c in catalog["cases"]}, {"SHIP-01", "SHIP-02"})
            machine = root / ".statemachine/codd"
            machine.mkdir(parents=True)
            (machine / "codd.json").write_text(json.dumps({"side": "impl", "refs": [], "graphify": "off",
                                                           "test_bindings": ["test-bindings.json"],
                                                           "test_catalogs": ["results/cases.json"], "evidence": ["results/*.json"]}))
            run(["git", "init", "-q", "-b", "main"])
            run(["git", "add", "-A"])
            run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "initial"])
            with mock.patch.object(codd, "MACHINE_DIR", machine):
                ctx = codd.Ctx(root)
                ctx.data.mkdir(exist_ok=True)
                ctx._test_receipt = {"run_id": "integration-run", "inputs_digest": codd.execution_inputs(ctx)}
                run(["node", str(LINT), "run"], env={**os.environ, "PYTHON": sys.executable,
                                                     "TEST_EVIDENCE_RUN_ID": ctx._test_receipt["run_id"],
                                                     "TEST_EVIDENCE_INPUTS_DIGEST": ctx._test_receipt["inputs_digest"]})
                problems = codd.bindings_execution_problems(ctx, 0, {("", "tests/shipping.feature")},
                                                          {h: "なし" for h in codd.PLAN_HEADINGS})
                self.assertEqual(problems, [])
                feature.write_text(feature.read_text(encoding="utf-8").replace("4999", "4998"), encoding="utf-8")
                self.assertTrue(codd.bindings_execution_problems(ctx, 0, {("", "tests/shipping.feature")},
                                                               {h: "なし" for h in codd.PLAN_HEADINGS}))


if __name__ == "__main__":
    unittest.main()
