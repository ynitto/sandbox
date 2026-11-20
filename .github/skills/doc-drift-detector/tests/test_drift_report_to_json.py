import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drift_report_to_json.py"
NOW = "2026-11-20T01:30:00+00:00"
TABLE = """## サマリ
| ドキュメント | claim数 | verified | drifted | unverifiable | 鮮度スコア |
| --- | ---: | ---: | ---: | ---: | ---: |
"""


class DriftReportToJsonTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.input = Path(directory.name) / "drift-report.md"
        self.output = Path(directory.name) / "result.json"

    def run_adapter(self, markdown=None):
        if markdown is not None:
            self.input.write_text(markdown, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-S", str(SCRIPT), "--input", str(self.input),
             "--output", str(self.output), "--now", NOW],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def test_report_with_findings(self):
        result = self.run_adapter(TABLE + """| README.md | 5 | 2 | 2 | 1 | 40% |
| docs/spec.md | 3 | 2 | 1 | 0 | 67% |

## 指摘一覧
### D-001 [DOC-STALE / critical] ビルドコマンドが存在しない
### D-002 [CODE-DRIFT / major] 既定値が異なる
### D-003 [UNKNOWN / minor] 正しいパスを確認する

## 要確認リスト（UNKNOWN）
### D-003 [UNKNOWN / minor] 正しいパスを確認する

## 検証不能リスト（unverifiable）
- 実行時の挙動
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), {
            "version": 1,
            "source": "doc-drift-detector",
            "generated_at": NOW,
            "summary": {
                "documents": 2, "claims": 8, "verified": 4, "drifted": 3, "unverifiable": 1,
                "by_class": {"DOC-STALE": 1, "CODE-DRIFT": 1, "UNKNOWN": 1},
                "by_severity": {"critical": 1, "major": 1, "minor": 1},
            },
            "findings": [
                {"id": "D-001", "class": "DOC-STALE", "severity": "critical",
                 "title": "ビルドコマンドが存在しない"},
                {"id": "D-002", "class": "CODE-DRIFT", "severity": "major",
                 "title": "既定値が異なる"},
                {"id": "D-003", "class": "UNKNOWN", "severity": "minor",
                 "title": "正しいパスを確認する"},
            ],
        })

    def test_no_drift(self):
        result = self.run_adapter(TABLE + "| README.md | 1 | 1 | 0 | 0 | 100% |\n\n## 指摘一覧\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["summary"], {
            "documents": 1, "claims": 1, "verified": 1, "drifted": 0, "unverifiable": 0,
            "by_class": {"DOC-STALE": 0, "CODE-DRIFT": 0, "UNKNOWN": 0},
            "by_severity": {"critical": 0, "major": 0, "minor": 0},
        })
        self.assertEqual(report["findings"], [])

    def test_missing_summary_table(self):
        result = self.run_adapter("## サマリ\n\n表がありません。\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("サマリ表", result.stderr)
        self.assertFalse(self.output.exists())

    def test_invalid_summary_row(self):
        result = self.run_adapter(TABLE + "| README.md | 未集計 | 0 | 0 | 0 | — |\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("サマリ表", result.stderr)
        self.assertFalse(self.output.exists())

    def test_conversion_failure_removes_existing_output(self):
        self.output.write_text('{"version": 1}', encoding="utf-8")
        result = self.run_adapter("## サマリ\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("サマリ表", result.stderr)
        self.assertFalse(self.output.exists())

    def test_reordered_summary_columns(self):
        table = TABLE.replace("| verified | drifted |", "| drifted | verified |")
        result = self.run_adapter(table + "| README.md | 1 | 0 | 1 | 0 | 100% |\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("サマリ表の見出し", result.stderr)
        self.assertFalse(self.output.exists())

    def test_invalid_finding_heading(self):
        result = self.run_adapter(TABLE + """| README.md | 2 | 0 | 2 | 0 | 0% |

## 指摘一覧
### D-001 [DOC-STALE / major] 既定値が異なる
### D-002 [CODE-DRIFT / invalid] パスが異なる
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("指摘一覧の見出し", result.stderr)
        self.assertIn("D-002", result.stderr)
        self.assertFalse(self.output.exists())

    def test_drift_without_findings(self):
        result = self.run_adapter(TABLE + "| README.md | 1 | 0 | 1 | 0 | 0% |\n\n## 指摘一覧\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("指摘一覧が空", result.stderr)
        self.assertFalse(self.output.exists())

    def test_missing_input(self):
        result = self.run_adapter()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.input), result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
