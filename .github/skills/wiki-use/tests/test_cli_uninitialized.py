"""wiki_query / wiki_ingest / wiki_lint を、初期化前（skill-registry.json に設定が無い）に
動かしたときの振る舞いの回帰テスト。

- `--help` は設定を読まずに使い方を出し、終了コード 0 で終わる
- サブコマンドを渡したときは、トレースバックではなく「wiki_init.py で初期化して」の
  1 文を [ERROR] として出し、終了コード 1 で終わる

スクリプトの置き場所がエージェントホーム（~/.claude/skills/wiki-use など）でないときは
~ の下の skill-registry.json を探すので、HOME を空の一時フォルダに向けて「未初期化」を作る。
実行: python3 -m unittest discover -s .github/skills/wiki-use/tests
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

CASES = [
    ("wiki_query.py", ["list-pages"]),
    ("wiki_ingest.py", ["next-batch"]),
    ("wiki_lint.py", []),
]


class UninitializedCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = dict(os.environ)
        self.env["HOME"] = self._home.name
        self.env["USERPROFILE"] = self._home.name
        self.env["PYTHONIOENCODING"] = "utf-8"

    def _run(self, script: str, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *args],
            capture_output=True, text=True, encoding="utf-8",
            env=self.env, cwd=self._home.name, timeout=60,
        )

    def test_help_works_before_init(self) -> None:
        for script, _ in CASES:
            with self.subTest(script=script):
                proc = self._run(script, ["--help"])
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("usage", proc.stdout.lower())

    def test_command_before_init_reports_without_traceback(self) -> None:
        for script, args in CASES:
            with self.subTest(script=script):
                proc = self._run(script, args)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertIn("[ERROR]", proc.stderr)
                self.assertIn("wiki_init.py", proc.stderr)


if __name__ == "__main__":
    unittest.main()
