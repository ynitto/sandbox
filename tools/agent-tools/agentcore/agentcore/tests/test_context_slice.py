"""決定的コンテキスト・スライシングの契約。

見ているのは 3 つ。**依存を辿って落とさない**こと、**落としたものを必ず言う**こと、
**切れないときは切らずに None を返す**こと（呼び出し側が原本へ倒せる形）。

抜粋が構文として壊れていないことも毎回確かめる——読ませる材料が壊れていると、
モデルの失敗なのか道具の失敗なのかが後から切り分けられなくなる。
"""
from __future__ import annotations

import ast
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agentcore import context_slice


SAMPLE = '''"""モジュールの説明。"""
from __future__ import annotations

import json

UNIT = 1024
NOISE = "使われない定数"


def _scale(n: int) -> float:
    return n / UNIT


def human_bytes(n: int) -> str:
    """対象。_scale と UNIT に依存する。"""
    return f"{_scale(n):.1f}"


def unrelated(payload):
    return json.dumps(payload)


class Report:
    """クラスの説明。"""

    def render(self) -> str:
        return human_bytes(UNIT)

    def summary(self) -> str:
        return "summary"
'''


class SliceSourceTest(unittest.TestCase):
    def slice(self, targets, source=SAMPLE):
        return context_slice.slice_source(source, targets, path="sample.py")

    def test_target_and_its_dependencies_are_kept(self):
        result = self.slice(["human_bytes"])
        self.assertIsNotNone(result)
        self.assertEqual(set(result.included), {"human_bytes", "_scale", "UNIT"})
        self.assertIn("def human_bytes", result.text)
        self.assertIn("def _scale", result.text)
        self.assertIn("UNIT = 1024", result.text)

    def test_unrelated_definitions_are_dropped_but_named(self):
        result = self.slice(["human_bytes"])
        self.assertNotIn("def unrelated", result.text)
        self.assertIn("unrelated", result.omitted)
        self.assertIn("NOISE", result.omitted)
        self.assertIn("Report", result.omitted)
        # 落としたものは見出しに出る。ここが抜けると抜粋が「全部」と読まれる。
        header = [line for line in result.text.splitlines() if line.startswith("#")]
        self.assertTrue(any("省略したシンボル" in line for line in header))
        self.assertTrue(any("unrelated" in line for line in header))

    def test_imports_and_module_docstring_are_kept(self):
        result = self.slice(["human_bytes"])
        self.assertIn("import json", result.text)
        self.assertIn("from __future__ import annotations", result.text)
        self.assertIn("モジュールの説明", result.text)

    def test_excerpt_is_smaller_than_the_original(self):
        result = self.slice(["human_bytes"])
        self.assertLess(result.kept_lines, result.total_lines)
        self.assertEqual(result.total_lines, len(SAMPLE.splitlines()))

    def test_method_target_keeps_the_class_scaffolding_only(self):
        result = self.slice(["Report.render"])
        self.assertIn("class Report:", result.text)
        self.assertIn("def render", result.text)
        self.assertNotIn("def summary", result.text)
        self.assertIn("Report.summary", result.omitted)
        self.assertIn("Report.render", result.included)
        # メソッドが辿った依存も一緒に来る（クラスの外にあっても落とさない）。
        self.assertIn("def human_bytes", result.text)

    def test_whole_class_target_keeps_every_method(self):
        result = self.slice(["Report"])
        self.assertIn("def render", result.text)
        self.assertIn("def summary", result.text)
        self.assertNotIn("Report.summary", result.omitted)

    def test_a_class_named_whole_and_by_method_is_rendered_once(self):
        """全体と部分の両方を指名されたら全体が勝つ（2 回描くと原本に無い構造になる）。"""
        result = self.slice(["Report", "Report.render"])
        self.assertEqual(result.text.count("class Report:"), 1)
        self.assertIn("def summary", result.text)
        self.assertNotIn("Report.summary", result.omitted)

    def test_every_excerpt_still_parses(self):
        for targets in (["human_bytes"], ["Report.render"], ["Report"], ["UNIT"],
                        ["human_bytes", "unrelated"]):
            with self.subTest(targets=targets):
                result = self.slice(targets)
                self.assertIsNotNone(result)
                ast.parse(result.text)

    def test_broken_source_is_not_sliced(self):
        self.assertIsNone(context_slice.slice_source("def (:", ["x"]))

    def test_unknown_symbol_is_not_sliced(self):
        self.assertIsNone(self.slice(["does_not_exist"]))

    def test_no_targets_is_not_sliced(self):
        self.assertIsNone(self.slice([]))

    def test_one_known_target_survives_an_unknown_one(self):
        result = self.slice(["human_bytes", "does_not_exist"])
        self.assertIsNotNone(result)
        self.assertIn("human_bytes", result.included)


class SliceFileTest(unittest.TestCase):
    def test_missing_file_is_not_sliced(self):
        self.assertIsNone(context_slice.slice_file("/nonexistent/x.py", ["f"]))

    def test_existing_file_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(SAMPLE, encoding="utf-8")
            result = context_slice.slice_file(path, ["human_bytes"])
            self.assertIn("def human_bytes", result.text)
            self.assertIn(str(path), result.text)


class CliTest(unittest.TestCase):
    def _run(self, *argv) -> "tuple[int, str, str]":
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = context_slice.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _write(self, tmp: str, body: str = SAMPLE) -> Path:
        path = Path(tmp) / "sample.py"
        path.write_text(body, encoding="utf-8")
        return path

    def test_writes_the_excerpt_and_reports_what_it_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp)
            code, out, err = self._run(str(path), "--symbol", "human_bytes")
        self.assertEqual(code, 0)
        self.assertIn("def human_bytes", out)
        self.assertNotIn("def unrelated", out)
        self.assertIn("省略", err)

    def test_unsliceable_input_falls_back_to_the_whole_file_loudly(self):
        """静かに原本へ倒れると、抜粋が効いていない条件で測ってしまう。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "def broken(:\n")
            code, out, err = self._run(str(path), "--symbol", "broken")
        self.assertEqual(code, 0)
        self.assertEqual(out, "def broken(:\n")
        self.assertIn("ファイル全体を出します", err)

    def test_unreadable_path_is_an_error_not_a_silent_empty_excerpt(self):
        code, out, err = self._run("/nonexistent/x.py", "--symbol", "f")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("読めません", err)

    def test_out_file_receives_the_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp)
            target = Path(tmp) / "excerpt.py"
            code, out, _ = self._run(str(path), "--symbol", "human_bytes",
                                     "-o", str(target))
            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            self.assertIn("def human_bytes", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
