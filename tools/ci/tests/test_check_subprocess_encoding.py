"""subprocess の文字コード検査（`tools/ci/check_subprocess_encoding.py`）の単体テスト。

検査が ast で書かれている理由（関数名だけで突き合わせると同名の自前関数まで数える）を
固定するのが主目的。後半の 2 本は性格が違い、今のリポジトリが違反 0 件であることと、
`decode_cli_output` の写しが原本（tools/winauto/winauto.py）と同じ本体であることを見る。
"""

from __future__ import annotations

import ast
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_subprocess_encoding as cse   # noqa: E402

ROOT = Path(__file__).resolve().parents[3]

# decode_cli_output の原本と写し。単体で配るツール・スキルは共有モジュールに依存できないので
# 各々に写してある。**写しを足したらここにも足す**（下の一致検査が全部を見る）。
ORIGINAL = "tools/winauto/winauto.py"
COPIES = (
    "install.py",
    "tools/winauto/install.py",
    ".github/skills/presenter/sdpm/utils/cli_output.py",
    ".github/skills/ltm-use/scripts/sync_kiro_memory.py",
    ".github/skills/outlook-use/scripts/auth.py",
    ".github/skills/teams-use/scripts/auth.py",
    ".github/skills/skill-creator/scripts/extract-copilot-history.py",
    "tools/agent-tools/agentcore/agentcore/harness/toolloop.py",
    "tools/kiro-log-exporter/kiro_log_exporter.py",
    "tools/terminal-launcher/install.py",
    "tools/vscode-copilot/vscode-copilot.py",
    "tools/wsl-launcher/setup.py",
)


def lines(source: str) -> "list[int]":
    return [n for n, _ in cse.check_source(source)]


WIN = "# cmd.exe を呼ぶ\n"


class CheckSource(unittest.TestCase):
    def test_flags_text_true_without_encoding(self):
        src = WIN + "import subprocess\nsubprocess.run(['cmd.exe'], capture_output=True, text=True)\n"
        self.assertEqual(lines(src), [3])

    def test_flags_universal_newlines(self):
        src = WIN + "import subprocess\nsubprocess.check_output(['x'], universal_newlines=True)\n"
        self.assertEqual(lines(src), [3])

    def test_flags_every_function(self):
        src = WIN + "import subprocess\n" + "".join(
            f"subprocess.{f}(['x'], text=True)\n" for f in sorted(cse.SUBPROCESS_FUNCS))
        self.assertEqual(len(lines(src)), len(cse.SUBPROCESS_FUNCS))

    def test_encoding_given_is_ok(self):
        src = WIN + "import subprocess\nsubprocess.run(['x'], text=True, encoding='utf-8')\n"
        self.assertEqual(lines(src), [])

    def test_bytes_mode_is_ok(self):
        src = WIN + "import subprocess\nsubprocess.run(['x'], capture_output=True)\n" \
              "subprocess.run(['x'], text=False)\n"
        self.assertEqual(lines(src), [])

    def test_file_without_windows_marker_is_ignored(self):
        src = "import subprocess\nsubprocess.run(['git'], text=True)\n"
        self.assertEqual(lines(src), [])

    def test_marker_is_case_insensitive(self):
        src = "# PowerShell\nimport subprocess\nsubprocess.run(['x'], text=True)\n"
        self.assertEqual(lines(src), [3])

    def test_from_import_and_alias(self):
        src = WIN + "import subprocess as sp\nfrom subprocess import run as r\n" \
              "sp.Popen(['x'], text=True)\nr(['x'], text=True)\n"
        self.assertEqual(lines(src), [4, 5])

    def test_same_named_function_is_not_subprocess(self):
        """関数名だけで突き合わせない: 自前の run() や asyncio.run() は数えない。"""
        src = WIN + "import asyncio\ndef run(cmd, text=True):\n    pass\n" \
              "run(['x'], text=True)\nasyncio.run(main(), text=True)\n"
        self.assertEqual(lines(src), [])

    def test_text_from_variable_is_not_flagged(self):
        src = WIN + "import subprocess\nsubprocess.run(['x'], text=flag)\n"
        self.assertEqual(lines(src), [])


class TestFiles(unittest.TestCase):
    def test_is_test_file(self):
        for rel in ("tools/winauto/tests/test_winauto.py", "a/test/x.py",
                    "a/test_x.py", "a/x_test.py"):
            self.assertTrue(cse.is_test_file(Path(rel)), rel)
        for rel in ("tools/winauto/install.py", "a/contest.py", "a/testing/x.py"):
            self.assertFalse(cse.is_test_file(Path(rel)), rel)


class ErrorMessage(unittest.TestCase):
    def test_error_names_the_helper(self):
        """直し方が分からない ERROR にしない: 使う関数を名前で書く。"""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "talks_to_windows.py"
            path.write_text(WIN + "import subprocess\nsubprocess.run(['x'], text=True)\n",
                            encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cse.main([str(path)])
        self.assertEqual(rc, 1)
        self.assertIn("decode_cli_output()", out.getvalue())


class Repository(unittest.TestCase):
    def test_repository_has_no_violation(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cse.main([])
        self.assertEqual(rc, 0, out.getvalue())

    def test_copies_match_original(self):
        """写しが原本と同じ本体（docstring 込み）であること。片方だけ直すと割れる。"""
        def body(rel: str) -> str:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            defs = [n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "decode_cli_output"]
            self.assertEqual(len(defs), 1, rel)
            return ast.dump(defs[0])

        original = body(ORIGINAL)
        for rel in COPIES:
            with self.subTest(rel):
                self.assertEqual(body(rel), original)


if __name__ == "__main__":
    unittest.main()
