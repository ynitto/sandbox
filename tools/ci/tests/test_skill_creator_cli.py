"""skill-creator の `quick_validate.py` / `package_skill.py` の引数処理の単体テスト。

固定するのは `-h` / `--help` が使い方を出して終了コード 0 で終わること（以前は
`--help` をスキルのフォルダ名として読み、「ディレクトリではありません」で落ちていた）と、
引数なしが使い方の誤り（終了コード 1）のままであること。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / ".github" / "skills" / "skill-creator" / "scripts"


def run(script: str, *args: str, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=cwd)


class SkillCreatorCliTest(unittest.TestCase):
    def test_help_prints_usage(self):
        with tempfile.TemporaryDirectory() as cwd:
            for script in ("quick_validate.py", "package_skill.py"):
                for flag in ("-h", "--help"):
                    with self.subTest(script=script, flag=flag):
                        proc = run(script, flag, cwd=cwd)
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        self.assertIn("使い方", proc.stdout)
            # package_skill.py が --help を名前にした .skill を作っていないこと
            self.assertEqual(list(Path(cwd).iterdir()), [])

    def test_no_args_is_usage_error(self):
        with tempfile.TemporaryDirectory() as cwd:
            for script in ("quick_validate.py", "package_skill.py"):
                with self.subTest(script=script):
                    proc = run(script, cwd=cwd)
                    self.assertEqual(proc.returncode, 1)
                    self.assertIn("使い方", proc.stdout)


if __name__ == "__main__":
    unittest.main()
