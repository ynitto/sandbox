"""夜間メニューの展開と受け箱への投入を確かめる。"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import runner as job  # noqa: E402
import nightly_menu as menu  # noqa: E402


class NightlyMenuTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = menu.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_list_and_generate_every_menu(self):
        examples = {
            "docs-followup": ("target_dir=資料",),
            "test-maintenance": ("lint_results=指摘.txt", "bindings=対応表.json"),
            "changelog-draft": ("period=先週から今日まで",),
            "drift-scan": ("doc_drift_results=文書.md", "codd_gate_results=検査.txt"),
        }
        code, out, err = self.run_cli("list")
        self.assertEqual((code, err), (0, ""))
        self.assertCountEqual([line.split("\t", 1)[0] for line in out.splitlines()], examples)

        for name, parameters in examples.items():
            with self.subTest(menu=name):
                argv = ["generate", "--menu", name]
                for parameter in parameters:
                    argv.extend(("--param", parameter))
                code, out, err = self.run_cli(*argv)
                self.assertEqual((code, err), (0, ""))
                for heading in ("目的", "入力", "期待成果物", "検収観点"):
                    self.assertIn(f"## {heading}\n", out)
                for parameter in parameters:
                    self.assertIn(parameter.split("=", 1)[1], out)
                self.assertNotIn("{{", out)

    def test_missing_required_parameters_are_named(self):
        code, out, err = self.run_cli("generate", "--menu", "test-maintenance")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("lint_results", err)
        self.assertIn("bindings", err)

    def test_unknown_menu_and_parameter_are_named(self):
        for argv, unknown in (
            (["--menu", "no-such-menu"], "no-such-menu"),
            (["--menu", "docs-followup", "--param", "typo=src"], "typo"),
        ):
            with self.subTest(unknown=unknown):
                code, out, err = self.run_cli("generate", *argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(unknown, err)

    def test_default_parameters_and_output_file(self):
        path = self.work / "依頼.md"
        code, out, err = self.run_cli(
            "generate", "--menu", "docs-followup", "--param", "target_dir=資料", "--out", str(path),
        )
        self.assertEqual((code, out, err), (0, "", ""))
        prompt = path.read_text(encoding="utf-8")
        self.assertIn("`HEAD~1..HEAD`", prompt)
        self.assertIn("`docs-followup-draft.md`", prompt)

    def test_submit_uses_the_existing_box_format(self):
        args = ("--menu", "docs-followup", "--param", "target_dir=名前=値")
        code, prompt, err = self.run_cli("generate", *args)
        self.assertEqual((code, err), (0, ""))
        for flags, box in (
            ([], job.box_of(self.work)),
            (["--box", str(self.work / "別の受け箱")], self.work / "別の受け箱"),
        ):
            with self.subTest(box=box):
                code, out, err = self.run_cli("submit", *args, "--dir", str(self.work), "--name", "nightly docs", *flags)
                self.assertEqual((code, err), (0, ""))
                job_id = out.strip()
                self.assertTrue(job_id.endswith("-nightly-docs"))
                pending = box / "pending" / f"{job_id}.md"
                self.assertEqual(list((box / "pending").iterdir()), [pending])
                self.assertEqual(pending.read_text(encoding="utf-8"), prompt)
                self.assertEqual(list((box / "tmp").iterdir()), [])
                self.assertEqual(list((box / "running").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
