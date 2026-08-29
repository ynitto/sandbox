"""SEARCH/REPLACE の解析と曖昧一致の階段（設計 2026-08-27 §3.6・未決 5）。

**上流（aider 0.86.2）と同じ結果になることを、上流を実際に読んで確かめる。** 写経した
つもりで挙動が違うと、去就の実測が「実装の差」ではなく「取りこぼしの差」を測ってしまう。
aider が入っていない環境ではその照合だけ skip し、自前の不変条件は常に見る。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import editblock as eb  # noqa: E402


def _upstream():
    """aider の階段を **import せずに** 読む（PIL 等の重い依存を引かない）。"""
    import glob
    import types
    hits = glob.glob(str(Path.home() / ".local/share/uv/tools/aider-chat/lib/*/"
                         "site-packages/aider/coders/editblock_coder.py"))
    if not hits:
        return None
    source = Path(hits[0]).read_text(encoding="utf-8")
    # モジュール冒頭の import 群は使わないので、必要な関数群だけを取り出して実行する。
    keep, capture = [], False
    for line in source.splitlines(keepends=True):
        if line.startswith("def "):
            capture = line.split("(")[0][4:] in {
                "prep", "perfect_or_whitespace", "perfect_replace",
                "replace_most_similar_chunk", "try_dotdotdots",
                "replace_part_with_missing_leading_whitespace",
                "match_but_for_leading_whitespace", "replace_closest_edit_distance"}
        if capture:
            keep.append(line)
    module = types.ModuleType("upstream_editblock")
    module.__dict__["re"] = __import__("re")
    module.__dict__["difflib"] = __import__("difflib")
    exec("".join(keep), module.__dict__)   # noqa: S102 — 上流の照合そのものが目的
    return module


UPSTREAM = _upstream()


class LadderMatchesUpstreamTests(unittest.TestCase):
    """3 段が上流と同じ答えを返すこと。"""

    CASES = [
        # (whole, search, replace, ラベル)
        ("def f():\n    return 1\n", "    return 1\n", "    return 2\n", "完全一致"),
        # 字下げを落として書いてきた SEARCH（弱いモデルが最もよくやる）
        ("class A:\n    def f(self):\n        return 1\n",
         "def f(self):\n    return 1\n", "def f(self):\n    return 2\n", "先頭空白のずれ"),
        # `...` の中略
        ("a = 1\nb = 2\nc = 3\n", "a = 1\n...\nc = 3\n", "a = 9\n...\nc = 8\n", "中略"),
        # どの段にも当たらない（difflib 段が生きていれば当たる形）
        ("def f():\n    return 1\n", "def f():\n    return 42\n",
         "def f():\n    return 3\n", "一致しない"),
    ]

    @unittest.skipIf(UPSTREAM is None, "aider が入っていない環境")
    def test_every_rung_agrees_with_upstream(self):
        for whole, search, replace, label in self.CASES:
            with self.subTest(label):
                try:
                    mine = eb.replace_chunk(whole, search, replace)
                except eb.ApplyError:
                    mine = None
                try:
                    theirs = UPSTREAM.replace_most_similar_chunk(whole, search, replace)
                except ValueError:
                    theirs = None
                self.assertEqual(mine, theirs, label)

    def test_the_fuzzy_rung_is_dead_upstream_too(self):
        """difflib 段は上流で到達不能——写していないのは取りこぼしではない。"""
        whole, search = "def f():\n    return 1\n", "def f():\n    return 42\n"
        self.assertIsNone(eb.replace_chunk(whole, search, "x\n"))
        if UPSTREAM is not None:
            self.assertIsNone(UPSTREAM.replace_most_similar_chunk(whole, search, "x\n"))


class ParsingTests(unittest.TestCase):
    def test_a_block_carries_its_filename(self):
        text = ("直します。\n\neval/billing.py\n```python\n<<<<<<< SEARCH\nold\n"
                "=======\nnew\n>>>>>>> REPLACE\n```\n")
        blocks = eb.find_blocks(text)
        self.assertEqual([(b.path, b.search, b.replace) for b in blocks],
                         [("eval/billing.py", "old\n", "new\n")])

    def test_the_filename_carries_over_to_the_next_block(self):
        text = ("a/b.py\n<<<<<<< SEARCH\n1\n=======\n2\n>>>>>>> REPLACE\n"
                "<<<<<<< SEARCH\n3\n=======\n4\n>>>>>>> REPLACE\n")
        self.assertEqual([b.path for b in eb.find_blocks(text)], ["a/b.py", "a/b.py"])

    def test_a_block_without_any_filename_is_an_error(self):
        with self.assertRaises(eb.ApplyError):
            eb.find_blocks("<<<<<<< SEARCH\n1\n=======\n2\n>>>>>>> REPLACE\n")

    def test_an_unclosed_block_is_an_error(self):
        with self.assertRaises(eb.ApplyError):
            eb.find_blocks("a.py\n<<<<<<< SEARCH\n1\n=======\n2\n")

    def test_prose_without_blocks_yields_nothing(self):
        self.assertEqual(eb.find_blocks("直しました。特に問題ありません。"), [])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_it_writes_and_reports_what_it_touched(self):
        (self.dir / "b.py").write_text("x = 1\n", encoding="utf-8")
        touched = eb.apply_blocks([eb.Block("b.py", "x = 1\n", "x = 2\n")], self.dir)
        self.assertEqual(touched, ["b.py"])
        self.assertEqual((self.dir / "b.py").read_text(encoding="utf-8"), "x = 2\n")

    def test_dry_run_checks_without_writing(self):
        """`--readonly` の根拠（aider の --dry-run に当たる）。"""
        (self.dir / "b.py").write_text("x = 1\n", encoding="utf-8")
        touched = eb.apply_blocks([eb.Block("b.py", "x = 1\n", "x = 2\n")],
                                  self.dir, dry_run=True)
        self.assertEqual(touched, ["b.py"])
        self.assertEqual((self.dir / "b.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_an_empty_search_creates_the_file(self):
        eb.apply_blocks([eb.Block("new/made.py", "", "print(1)\n")], self.dir)
        self.assertEqual((self.dir / "new/made.py").read_text(encoding="utf-8"), "print(1)\n")

    def test_a_miss_is_an_error_not_a_silent_skip(self):
        (self.dir / "b.py").write_text("x = 1\n", encoding="utf-8")
        with self.assertRaises(eb.ApplyError):
            eb.apply_blocks([eb.Block("b.py", "y = 9\n", "y = 8\n")], self.dir)
        self.assertEqual((self.dir / "b.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_it_refuses_to_escape_the_working_directory(self):
        with self.assertRaises(eb.ApplyError):
            eb.apply_blocks([eb.Block("../outside.py", "", "x\n")], self.dir)


if __name__ == "__main__":
    unittest.main()
