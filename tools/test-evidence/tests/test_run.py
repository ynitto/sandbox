import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).resolve().parents[1] / "run.py"


class RunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "cases.yaml").write_text("cases: []\n")
        (self.root / "test_app.py").write_text("# test source\n")
        self.binding = {"version": 1, "bindings": [{"case": {"file": "cases.yaml", "id": "TC-01"},
                       "targets": [{"suite": "unit", "file": "test_app.py", "selector": "TestApp::test_ok"}]}]}
        self.save_binding()

    def save_binding(self):
        (self.root / "bindings.json").write_text(json.dumps(self.binding))

    def run_xml(self, xml, code=0, mutate=False, file_report=False):
        body = ("from pathlib import Path\n" + ("Path('test_app.py').write_text('changed')\n" if mutate else "")
                + (f"Path('report.xml').write_text({xml!r})\n" if file_report else f"print({xml!r})\n") + f"raise SystemExit({code})\n")
        (self.root / "emit.py").write_text(body)
        return subprocess.run([sys.executable, str(RUNNER), "--bindings", "bindings.json", "--suite", "unit", "--junit",
                               "report.xml" if file_report else "-", "--output", "results.json", "--", sys.executable, "emit.py"],
                              cwd=self.root, env={**os.environ, "TEST_EVIDENCE_RUN_ID": "current", "TEST_EVIDENCE_INPUTS_DIGEST": "inputs"},
                              capture_output=True, text=True)

    def xml(self, content="", name="test_ok", status=""):
        return f'<testsuites><testsuite><testcase classname="TestApp" name="{name}"{status}>{content}</testcase></testsuite></testsuites>'

    def test_current_report_matches_selector_and_keeps_input_hashes(self):
        r = self.run_xml(self.xml(), file_report=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads((self.root / "results.json").read_text())
        self.assertEqual((data["purpose"], data["run_id"], data["inputs_digest"]), ("execution", "current", "inputs"))
        self.assertEqual(data["items"][0]["id"], "TestApp::test_ok")
        self.assertEqual(data["items"][0]["cases"][0]["id"], "TC-01")
        self.assertEqual(len(data["items"][0]["test_sha256"]), 64)

    def test_skip_error_failure_other_test_empty_and_runner_failure_do_not_pass(self):
        for xml, code in [(self.xml('<skipped/>'), 0), (self.xml('<error/>'), 0), (self.xml('<failure/>'), 0),
                          (self.xml(name="other"), 0), ('<testsuites/>', 0), (self.xml(), 1),
                          (self.xml(status=' status="notrun"'), 0), (self.xml(status=' status="unknown"'), 0)]:
            with self.subTest(xml=xml, code=code):
                self.assertNotEqual(self.run_xml(xml, code).returncode, 0)

    def test_mutated_input_is_rejected(self):
        r = self.run_xml(self.xml(), mutate=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("入力が変わりました", r.stderr)
        self.assertFalse((self.root / "results.json").exists())

    def test_failure_message_is_preserved_for_the_result_reader(self):
        result = self.run_xml(self.xml('<failure message="expected 0, got 500"/>'), code=1)
        self.assertEqual(result.returncode, 1)
        item = json.loads((self.root / "results.json").read_text())["items"][0]
        self.assertEqual(item["error"], "expected 0, got 500")
        self.assertIn("expected 0, got 500", result.stderr)

    def test_stale_file_report_is_not_reused_and_old_output_is_removed(self):
        (self.root / "report.xml").write_text(self.xml())
        (self.root / "results.json").write_text('{"old": true}')
        result = subprocess.run([sys.executable, str(RUNNER), "--bindings", "bindings.json", "--suite", "unit",
                                 "--junit", "report.xml", "--output", "results.json", "--", sys.executable, "-c", "pass"],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "results.json").exists())

    def test_ambiguous_same_name_without_file_is_rejected(self):
        (self.root / "test_other.py").write_text("# another test")
        self.binding["bindings"][0]["targets"].append({"suite": "unit", "file": "test_other.py", "selector": "TestApp::test_ok"})
        self.save_binding()
        self.assertIn("特定できません", self.run_xml(self.xml()).stderr)

    def test_same_test_can_cover_multiple_cases(self):
        self.binding["bindings"].append({"case": {"file": "cases.yaml", "id": "TC-02"}, "targets": self.binding["bindings"][0]["targets"]})
        self.save_binding()
        self.assertEqual(self.run_xml(self.xml()).returncode, 0)
        self.assertEqual(len(json.loads((self.root / "results.json").read_text())["items"][0]["cases"]), 2)


if __name__ == "__main__":
    unittest.main()
