import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evidence_diff.py"


def item(id_, status=None, kind="behavior", **fields):
    return {"id": id_, "kind": kind, "title": f"タイトル {id_}", "status": status, **fields}


class EvidenceDiffTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.before = root / "before.json"
        self.after = root / "after.json"
        self.output = root / "diff.json"
        self.md = root / "diff.md"

    def write_evidence(self, path, items):
        path.write_text(json.dumps({
            "version": 1, "source": "webui-test", "generatedAt": "2026-11-20T03:00:00Z",
            "root": ".", "items": items,
        }), encoding="utf-8")

    def run_diff(self):
        return subprocess.run([
            sys.executable, str(SCRIPT), "--before", str(self.before), "--after", str(self.after),
            "--output", str(self.output), "--md-out", str(self.md),
        ], capture_output=True, text=True)

    def read_report(self):
        result = self.run_diff()
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_compare(self):
        self.write_evidence(self.before, [
            item("ui/new-failure", "passed"),
            item("ui/recovered", "failed"),
            item("ui/still-failing", "failed"),
            item("ui/skipped", "failed"),
            item("ui/removed", kind="metric", value=10),
            item("ui/status", "same", "image", sha256="a"),
            item("ui/hash", "same", "image", sha256="a"),
            item("ui/same", "changed", "image", sha256="a"),
            item("ui/shared", "failed"),
        ])
        self.write_evidence(self.after, [
            item("ui/new-failure", "failed", error="タイムアウト"),
            item("ui/recovered", "passed"),
            item("ui/still-failing", "failed"),
            item("ui/skipped", "skipped"),
            item("ui/added", "failed"),
            item("ui/status", "changed", "image", sha256="a"),
            item("ui/hash", "same", "image", sha256="b"),
            item("ui/same", "same", "image", sha256="a"),
            item("ui/shared", "new", "image", sha256="a"),
        ])
        report = self.read_report()
        self.assertEqual(report["version"], 1)
        self.assertEqual(report["source"], "webui-test-evidence-diff")
        self.assertFalse(report["baseline_missing"])
        self.assertEqual(report["summary"], {
            "new_failures": 2, "recovered": 1, "still_failing": 1, "screen_changes": 2,
            "added": 2, "removed": 2, "failed_total": 3, "passed_total": 1,
        })
        changes = report["changes"]
        self.assertEqual(changes["new_failures"], [
            {"id": "ui/added", "title": "タイトル ui/added", "error": None},
            {"id": "ui/new-failure", "title": "タイトル ui/new-failure", "error": "タイムアウト"},
        ])
        self.assertEqual([entry["id"] for entry in changes["recovered"]], ["ui/recovered"])
        self.assertEqual([entry["id"] for entry in changes["screen_changes"]], ["ui/hash", "ui/status"])
        self.assertEqual([(entry["id"], entry["kind"]) for entry in changes["added"]],
                         [("ui/added", "behavior"), ("ui/shared", "image")])
        self.assertEqual([(entry["id"], entry["kind"]) for entry in changes["removed"]],
                         [("ui/removed", "metric"), ("ui/shared", "behavior")])
        markdown = self.md.read_text(encoding="utf-8")
        self.assertEqual([line for line in markdown.splitlines() if line.startswith("## ")],
                         ["## 新規失敗", "## 回復", "## 画面変化"])
        self.assertIn("タイムアウト", markdown)
        self.assertNotIn("ui/still-failing", markdown)
        self.assertNotIn("ui/removed", markdown)

    def test_removed_screen_with_unchanged_hash(self):
        self.write_evidence(self.before, [item("ui/screen", "same", "image", sha256="a")])
        self.write_evidence(self.after, [item("ui/screen", "removed", "image", sha256="a")])

        report = self.read_report()
        self.assertEqual(report["summary"]["screen_changes"], 1)
        self.assertEqual(report["changes"]["screen_changes"], [
            {"id": "ui/screen", "title": "タイトル ui/screen", "status": "removed"},
        ])
        self.assertIn("画面がなくなった", self.md.read_text(encoding="utf-8"))

    def test_duplicate_item_key_is_invalid_in_either_input(self):
        first = item("ui/duplicate", "passed")
        duplicate = item("ui/duplicate", "failed")
        for path in (self.before, self.after):
            with self.subTest(path=path.name):
                self.write_evidence(self.before, [first])
                self.write_evidence(self.after, [first])
                self.write_evidence(path, [first, duplicate])

                result = self.run_diff()
                self.assertEqual(result.returncode, 2)
                self.assertIn(str(path), result.stderr)
                self.assertIn("id='ui/duplicate', kind='behavior'", result.stderr)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.md.exists())

    def test_same_id_with_different_kinds_is_valid(self):
        self.write_evidence(self.before, [])
        self.write_evidence(self.after, [
            item("ui/shared", "passed"),
            item("ui/shared", "changed", "image", sha256="a"),
        ])
        report = self.read_report()
        self.assertEqual(report["summary"]["passed_total"], 1)
        self.assertEqual(report["summary"]["screen_changes"], 1)

    def test_missing_before(self):
        self.write_evidence(self.after, [
            item("ui/failure", "failed"),
            item("ui/pass", "passed"),
            item("ui/screen", "changed", "image", sha256="a"),
        ])
        report = self.read_report()
        self.assertTrue(report["baseline_missing"])
        self.assertEqual(report["summary"], {
            "new_failures": 0, "recovered": 0, "still_failing": 0, "screen_changes": 0,
            "added": 0, "removed": 0, "failed_total": 1, "passed_total": 0,
        })
        self.assertFalse(any(report["changes"].values()))
        self.assertEqual(self.md.read_text(encoding="utf-8"), "変化なし\n")

    def test_missing_or_invalid_after_clears_old_outputs(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                if invalid:
                    self.after.write_text("{", encoding="utf-8")
                for path in (self.output, self.md):
                    path.write_text("古い結果", encoding="utf-8")
                self.assertEqual(self.run_diff().returncode, 2)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.md.exists())


if __name__ == "__main__":
    unittest.main()
