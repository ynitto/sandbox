"""点検ツールの出力を一時ディレクトリに用意し、CLI の本文を確かめる。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


TOOL = Path(__file__).resolve().parents[1]
SCRIPT = TOOL / "nightly_check_digest.py"


class DigestTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="nightly_check_")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = json.loads((TOOL / "config.example.json").read_text(encoding="utf-8"))
        for check, path in zip(self.config["checks"], ("lint.md", "evidence.json", "last-report.json", "gc.log")):
            check["path"] = path
        self.config_path = self.root / "config.json"

    def write(self, path, content):
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        (self.root / path).write_text(content, encoding="utf-8")

    def evidence(self, status="passed"):
        item = {"id": "testcase-lint/case", "kind": "behavior", "status": status, "file": "case.yaml"}
        if status == "failed":
            item.update(error="K001 期待結果が曖昧", path="case.yaml")
        self.write("evidence.json", {"version": 1, "purpose": "check", "root": ".", "items": [
            {"id": "testcase-lint/errors", "kind": "metric", "value": 0, "max": 0}, item,
        ]})

    def report(self, **changes):
        pair = {"pair": "app", "rule": "mirror", "strategy": "fast-forward",
                "status": "ok", "changed": 0, "diverged": [], "conflicts": [], "skipped": [], "failed": []}
        pair.update(changes)
        self.write("last-report.json", {"finished_at": "2026-11-19T02:30:00Z", "pairs": [pair]})

    def gc(self, code):
        self.write("gc.log", f"gc start 2026-11-18\ngc exit 1\n"
                   f"gc start 2026-11-19\n整理の結果\ngc exit {code}\n")

    def run_digest(self, *args):
        self.write("config.json", self.config)
        return self.run_with_config(*args)

    def run_with_config(self, *args):
        return subprocess.run([sys.executable, "-S", str(SCRIPT), "--config", str(self.config_path),
                               "--date", "2026-11-19", *args], capture_output=True, encoding="utf-8")

    def body(self):
        run = self.run_digest()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr, "")
        return run.stdout

    def test_all_green(self):
        self.write("lint.md", "# 点検の結果\n\n## パス\n\n- なし\n\n## 本流に渡すやりたいこと\n\n- なし\n")
        self.evidence()
        self.report()
        self.gc(0)
        self.assertEqual(self.body(), "## 夜間点検サマリ（2026-11-19）\n\n"
                                     "✅ すべて正常：codd-lint、testcase-lint、gitlab-repo-sync、change-watch gc\n")

    def test_partial_failure(self):
        self.write("lint.md", "# 点検の結果\n- 文書がありません\n")
        self.evidence("failed")
        self.report(status="unchanged", diverged=["refs/heads/main"], conflicts=["refs/heads/dev"])
        self.gc(0)
        body = self.body()
        report_path = str(self.root / "last-report.json").replace("_", r"\_")
        for expected in ("✅ 正常：change-watch gc", "❌ 失敗：codd-lint", "文書がありません",
                         "❌ 失敗：testcase-lint", "case.yaml — K001", "❌ 失敗：gitlab-repo-sync",
                         "分岐（1 件） refs/heads/main", "衝突（1 件） refs/heads/dev",
                         f"- 参照先：{report_path}\n"):
            self.assertIn(expected, body)
        self.assertNotIn("# 点検の結果", body)

    def test_missing_and_old_sources(self):
        self.config["checks"][1].pop("max_age_hours")
        self.evidence()
        self.report()
        now = time.time()
        for name, age in (("evidence.json", 27), ("last-report.json", 13)):
            mtime = now - age * 3600
            os.utime(self.root / name, (mtime, mtime))
        body = self.body()
        for check in self.config["checks"]:
            self.assertIn(f"❓ 未確認（結果なし）：{check['name']}", body)
        self.assertEqual(body.count("結果ファイルがありません"), 2)
        self.assertIn("結果が古い（27.0 時間前）", body)
        self.assertIn("結果が古い（13.0 時間前）", body)
        self.assertNotIn("✅", body)

    def test_gc_failure_and_incomplete_run(self):
        self.config["checks"] = [self.config["checks"][3]]
        self.gc(3)
        self.assertIn("- gc exit 3\n", self.body())
        with (self.root / "gc.log").open("a", encoding="utf-8") as stream:
            stream.write("gc start 2026-11-19\n")
        self.assertIn("❓ 未確認（結果なし）：change-watch gc", self.body())

    def test_generic_report_and_command(self):
        self.config["checks"] = [
            {"name": "汎用 JSON", "type": "json_report", "path": "report.json",
             "failure_conditions": [{"path": "summary.bySeverity.error", "op": "ne", "value": 0}]},
            {"name": "コマンド", "type": "command",
             "argv": [sys.executable, "-c", "print('command output'); raise SystemExit(9)"]},
        ]
        self.write("report.json", {"summary": {"bySeverity": {"error": 2}}})
        body = self.body()
        self.assertIn("summary.bySeverity.error = 2", body)
        self.assertIn("❌ 失敗：コマンド", body)
        self.assertIn("- 終了コード 9\n- command output\n", body)

    def test_command_source_stays_on_one_markdown_line(self):
        self.config["checks"] = [
            {"name": "コマンド", "type": "command",
             "argv": [sys.executable, "-c", "raise SystemExit(1)\n# [comment]"]},
        ]
        body = self.body()
        source = next(line for line in body.splitlines() if line.startswith("- 参照先："))
        self.assertIn(r"raise SystemExit(1) \# \[comment\]", source)
        self.assertNotIn("\n# [comment]", body)

    def test_empty_findings_and_output_file(self):
        self.config["checks"] = self.config["checks"][:1]
        self.write("lint.md", "")
        output = self.root / "digest.md"
        run = self.run_digest("--output", str(output))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, "")
        self.assertIn("✅ すべて正常：codd-lint", output.read_text(encoding="utf-8"))

    def test_invalid_configuration(self):
        self.config["checks"][0]["max_age_hours"] = 0
        run = self.run_digest()
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")
        self.assertIn("max_age_hours", run.stderr)

    def test_log_failure_patterns_required(self):
        check = self.config["checks"][3]
        self.config["checks"] = [check]
        self.gc(3)
        for case in ("省略", "空配列"):
            with self.subTest(case=case):
                if case == "省略":
                    del check["failure_patterns"]
                else:
                    check["failure_patterns"] = []
                run = self.run_digest()
                self.assertEqual(run.returncode, 2)
                self.assertEqual(run.stdout, "")
                self.assertIn("failure_patterns", run.stderr)

    def test_failure_details_limit(self):
        self.write("lint.md", "\n".join(f"lint {i}" for i in range(1, 8)))
        self.evidence("failed")
        self.report(diverged=[f"ref{i}" for i in range(1, 8)])
        self.config["checks"][3]["failure_patterns"] = ["^error "]
        self.write("gc.log", "gc start yesterday\n" + "error old\n" * 8
                   + "gc exit 1\ngc start today\n"
                   + "".join(f"error {i}\n" for i in range(1, 8))
                   + "gc exit 1\nerror after end\n")
        self.config["checks"].append({
            "name": "コマンド", "type": "command",
            "argv": [sys.executable, "-c",
                     "for i in range(1, 8): print(f'output {i}')\nraise SystemExit(1)"],
        })
        body = self.body()
        for prefix in ("lint", "error", "output"):
            self.assertIn(f"- {prefix} 5\n- ほか 2 件\n", body)
            self.assertNotIn(f"{prefix} 6", body)
        self.assertIn("ref5, ほか 2 件", body)
        self.assertNotIn("ref6", body)
        self.assertEqual(body.count("ほか 2 件"), 4)

    def test_missing_configuration(self):
        run = self.run_with_config()
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")
        self.assertIn("config.json", run.stderr)

    def test_invalid_json_configuration(self):
        self.write("config.json", '{"checks": [')
        run = self.run_with_config()
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")

    def test_missing_command(self):
        self.config["checks"] = [
            {"name": "コマンド", "type": "command",
             "argv": [str(self.root / "missing-command")]},
        ]
        self.assertIn("❓ 未確認（結果なし）：コマンド", self.body())

    def test_command_timeout(self):
        self.config["checks"] = [
            {"name": "コマンド", "type": "command",
             "argv": [sys.executable, "-c", "import time; time.sleep(10)"],
             "timeout_seconds": 0.1},
        ]
        self.assertIn("❌ 失敗：コマンド\n\n- コマンドが制限時間を超えました", self.body())

    def test_yaml_configuration(self):
        self.config_path = self.root / "config.yaml"
        self.write("config.yaml", "checks:\n  - name: YAML 点検\n"
                   "    type: findings_file\n    path: lint.md\n")
        self.write("lint.md", "")
        run = self.run_with_config()
        if run.returncode == 2 and run.stderr == (
            "集約できません: YAML を読むには PyYAML が必要です。JSON 設定も使えます\n"
        ):
            self.skipTest("PyYAML が利用できません")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("✅ すべて正常：YAML 点検", run.stdout)

    def test_invalid_report(self):
        self.config["checks"] = [self.config["checks"][1]]
        self.write("evidence.json", '{"version": 1, "items": [')
        self.assertIn("❓ 未確認（結果なし）：testcase-lint", self.body())


if __name__ == "__main__":
    unittest.main()
