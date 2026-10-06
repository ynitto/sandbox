"""手順（guides）の書き方を、よくある書き方で黙って効かなくしないことを固定する（先頭の `codd:`・glob）。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MACHINE = Path(__file__).resolve().parents[1] / "machine"


def load_codd():
    spec = importlib.util.spec_from_file_location("codd_machine_guide_syntax", MACHINE / "codd.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclass が自分のモジュールを引けるように
    spec.loader.exec_module(mod)
    return mod


codd = load_codd()


class FrontMatterTest(unittest.TestCase):
    def test_yaml_block_list_is_read_as_an_array(self) -> None:
        meta = codd.front_codd("---\nname: m\ncodd:\n  files:\n    - db/migrations/*.sql\n    - \"db/seed/*.sql\"\n"
                               "  change: [create]\n---\n# 手順\n")
        self.assertEqual(meta, {"files": ["db/migrations/*.sql", "db/seed/*.sql"], "change": ["create"]})

    def test_trailing_comment_does_not_become_part_of_the_glob(self) -> None:
        meta = codd.front_codd("---\ncodd:  # 手順\n  # 説明の行\n  files: [\"db/*.sql\"]  # マイグレーション\n"
                               "  asks: [\"Issue #12 の書き方\"]\n---\n")
        self.assertEqual(meta, {"files": ["db/*.sql"], "asks": ["Issue #12 の書き方"]})   # 引用符の中の # は残す

    def test_bom_and_capitalized_booleans(self) -> None:
        self.assertEqual(codd.front_codd("\ufeff---\ncodd:\n  tests: True\n  must: FALSE\n---\n"),
                         {"tests": True, "must": False})

    def test_placed_guides_accept_a_single_glob_and_a_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".agents/guides").mkdir(parents=True)
            (repo / ".agents/guides/sql.md").write_text(
                "\ufeff---\ncodd:\n  files: db/*.sql\n  asks: ロックが長くならない\n---\n\n# SQL\n", encoding="utf-8")
            (guide,) = codd.front_guides(repo, [])
            self.assertEqual((guide.target, guide.files, guide.asks), (".agents/guides/sql.md", ["db/*.sql"],
                                                                      ["ロックが長くならない"]))
            (repo / ".agents/guides/ts.md").write_text(   # Copilot の applyTo と同じ、`,` 区切りの 1 行
                "---\ncodd:\n  files: \"**/*.ts, src/*.{ts,tsx}\"\n---\n", encoding="utf-8")
            ts = next(g for g in codd.front_guides(repo, []) if g.target.endswith("ts.md"))
            self.assertEqual(ts.files, ["**/*.ts", "src/*.{ts,tsx}"])

    def test_config_saved_with_a_bom_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / codd.CONFIG_NAME
            path.write_text("\ufeff" + json.dumps({"side": "impl", "refs": [{"name": "docs", "path": "../docs"}]}), encoding="utf-8")
            self.assertEqual(codd.load_config(Path(tmp))["side"], "impl")



class GlobTest(unittest.TestCase):
    def test_braces_and_leading_dot_slash_match_like_other_tools(self) -> None:
        self.assertTrue(codd.path_matches("src/*.{ts,tsx}", "src/a.tsx"))
        self.assertFalse(codd.path_matches("src/*.{ts,tsx}", "src/a.js"))
        self.assertFalse(codd.path_matches("src/*.{ts,tsx}", "src/x/a.ts"))   # `*` はフォルダをまたがない
        self.assertTrue(codd.path_matches("a{b}.py", "a{b}.py"))              # 選択肢の無い波括弧は文字のまま
        self.assertEqual(codd.rule_list(["./src/*.py", "src/*.py"], "x"), ["src/*.py"])
        self.assertEqual(codd.exclude_list(["./tmp/"], "x"), ["tmp/"])

    def test_document_globs_with_braces_find_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            for rel in ("docs/a/x.md", "docs/b/y.md", "docs/c/z.md", "README.md", "notes.txt"):
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_text("# x\n", encoding="utf-8")
            self.assertEqual(codd.expand_rules(repo, ["docs/{a,b}/*.md", "*.{md,txt}"]),
                             ["docs/a/x.md", "docs/b/y.md", "README.md", "notes.txt"])


class AnchorTest(unittest.TestCase):
    def test_section_is_found_by_heading_text_or_link_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "rules.md").write_text("# Rules\n\n## Table Format\n\nuse a type column\n\n## 書き方\n\n短く\n",
                                           encoding="utf-8")
            for anchor in ("Table Format", "table-format"):
                self.assertEqual(codd.doc_text(repo, f"rules.md#{anchor}"), "## Table Format\n\nuse a type column\n")
            self.assertEqual(codd.doc_text(repo, "rules.md#書き方"), "## 書き方\n\n短く\n")
            self.assertIsNone(codd.doc_text(repo, "rules.md#missing"))

if __name__ == "__main__":
    unittest.main()
