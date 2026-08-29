"""aider を使わない編集適用エージェント（設計 2026-08-27 §3.6・未決 5 の対照実装）。

LLM は呼ばない——見るのは材料の作り方と、当てられなかったときの振る舞いである。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import editagent  # noqa: E402


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_an_existing_target_is_shown_with_its_body(self):
        (self.dir / "a.py").write_text("x = 1\n", encoding="utf-8")
        text = editagent._materials(["a.py"], [], self.dir)
        self.assertIn("a.py", text)
        self.assertIn("x = 1", text)

    def test_a_missing_target_is_normal_and_says_so(self):
        """「このファイルを作れ」という依頼はこの形で来る（aider も新規作成を受ける）。"""
        text = editagent._materials(["new.py"], [], self.dir)
        self.assertIn("new.py", text)
        self.assertIn("まだ存在しません", text)

    def test_an_unreadable_reference_stops_the_run(self):
        """参照が読めないのは依頼の前提が崩れている。黙って進めない。"""
        with self.assertRaises(RuntimeError):
            editagent._materials([], ["missing.py"], self.dir)

    def test_references_are_marked_as_not_editable(self):
        (self.dir / "t.py").write_text("assert True\n", encoding="utf-8")
        self.assertIn("変更しない", editagent._materials([], ["t.py"], self.dir))


class RunEditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        (self.dir / "b.py").write_text("x = 1\n", encoding="utf-8")

    def _run(self, replies, **kwargs):
        answers = iter(replies)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(editagent.ollama_loop, "run_plain",
                               side_effect=lambda *a, **k: {
                                   "text": next(answers), "tokens_in": 3, "tokens_out": 4}):
            rc = editagent.run_edit(model="m", message="直して", files=["b.py"], reads=[],
                                    cwd=self.dir, out=out, err=err, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    BLOCK = "b.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n"

    def test_it_applies_and_reports_usage(self):
        rc, out, err = self._run([self.BLOCK])
        self.assertEqual(rc, 0)
        self.assertIn("適用しました", out)
        self.assertIn("@agent-usage tokens_in=3 tokens_out=4", err)
        self.assertEqual((self.dir / "b.py").read_text(encoding="utf-8"), "x = 2\n")

    def test_readonly_checks_without_writing(self):
        rc, out, _err = self._run([self.BLOCK], readonly=True)
        self.assertEqual(rc, 0)
        self.assertIn("書き込みませんでした", out)
        self.assertEqual((self.dir / "b.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_a_miss_is_retried_once_with_the_reason(self):
        miss = "b.py\n<<<<<<< SEARCH\nzzz\n=======\nx = 9\n>>>>>>> REPLACE\n"
        rc, out, _err = self._run([miss, self.BLOCK])
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.dir / "b.py").read_text(encoding="utf-8"), "x = 2\n")

    def test_prose_without_blocks_fails_instead_of_claiming_success(self):
        rc, _out, err = self._run(["直しました。", "やはり直しました。"])
        self.assertEqual(rc, 1)
        self.assertIn("ブロックがありません", err)
        self.assertEqual((self.dir / "b.py").read_text(encoding="utf-8"), "x = 1\n",
                         "当てられなかったのに書き換えない")

    def test_usage_is_summed_across_the_retry(self):
        _rc, _out, err = self._run(["説明だけ", "まだ説明だけ"])
        self.assertIn("tokens_in=6 tokens_out=8", err)


if __name__ == "__main__":
    unittest.main()
