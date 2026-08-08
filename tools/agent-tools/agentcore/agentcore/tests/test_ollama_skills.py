from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from agentcore import ollama_skills


class _SkillHome:
    """一時的なスキルホームを `AGENT_OLLAMA_SKILLS_DIR` に差し込む。"""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._prev = os.environ.get("AGENT_OLLAMA_SKILLS_DIR")
        os.environ["AGENT_OLLAMA_SKILLS_DIR"] = str(self.root)
        return self

    def add(self, name: str, body: str = "本文", frontmatter: bool = True) -> Path:
        path = self.root / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        head = ("---\nname: %s\ndescription: せつめい\n---\n" % name) if frontmatter else ""
        path.write_text(head + body, encoding="utf-8")
        return path

    def __exit__(self, *_exc):
        if self._prev is None:
            os.environ.pop("AGENT_OLLAMA_SKILLS_DIR", None)
        else:
            os.environ["AGENT_OLLAMA_SKILLS_DIR"] = self._prev
        self._tmp.cleanup()


class TestFrontmatter(unittest.TestCase):
    def test_strips_only_the_leading_block(self):
        text = "---\nname: a\n---\n# 見出し\n\n---\n区切り線は残る\n"
        self.assertEqual(ollama_skills.strip_frontmatter(text),
                         "# 見出し\n\n---\n区切り線は残る\n")

    def test_no_frontmatter_is_untouched(self):
        self.assertEqual(ollama_skills.strip_frontmatter("# 見出し"), "# 見出し")


class TestLeadingSlashes(unittest.TestCase):
    def test_takes_only_the_leading_block(self):
        calls, rest = ollama_skills.split_leading_slashes("/a\n/b 引数\n本文1\n/c\n")
        self.assertEqual(calls, [("a", ""), ("b", "引数")])
        self.assertEqual(rest, "本文1\n/c")

    def test_blank_line_ends_the_block(self):
        calls, rest = ollama_skills.split_leading_slashes("/a\n\n/b\n")
        self.assertEqual(calls, [("a", "")])
        self.assertEqual(rest, "/b")

    def test_paths_in_body_are_not_slash_calls(self):
        """本文中の `/usr/...` を誤爆しない（先頭ブロックしか見ない）。"""
        calls, rest = ollama_skills.split_leading_slashes("次を消して\n/usr/local/tmp\n")
        self.assertEqual(calls, [])
        self.assertEqual(rest, "次を消して\n/usr/local/tmp")

    def test_uppercase_is_not_a_slash_call(self):
        calls, _rest = ollama_skills.split_leading_slashes("/Bad\n")
        self.assertEqual(calls, [])


class TestExpand(unittest.TestCase):
    def test_no_skill_means_zero_overhead(self):
        """未使用時はプロンプトが 1 バイトも変わらない（prefill を太らせない）。"""
        with _SkillHome():
            out, loaded = ollama_skills.expand("ふつうの依頼")
        self.assertEqual(out, "ふつうの依頼")
        self.assertEqual(loaded, [])

    def test_explicit_skill_is_prepended_with_frontmatter_stripped(self):
        with _SkillHome() as home:
            home.add("pdf", "PDF の扱い方")
            out, loaded = ollama_skills.expand("これを要約", ["pdf"])
        self.assertIn("# スキル: pdf", out)
        self.assertIn("PDF の扱い方", out)
        self.assertNotIn("description: せつめい", out, "frontmatter は載せない")
        self.assertTrue(out.endswith("これを要約"))
        self.assertEqual([s["name"] for s in loaded], ["pdf"])

    def test_slash_line_is_consumed_when_resolvable(self):
        with _SkillHome() as home:
            home.add("summarize-logs", "ログ要約の手順")
            out, loaded = ollama_skills.expand("/summarize-logs 昨日\n対象は app.log")
        self.assertIn("（引数: 昨日）", out)
        self.assertIn("ログ要約の手順", out)
        self.assertTrue(out.endswith("対象は app.log"))
        self.assertNotIn("/summarize-logs", out, "スラッシュ行は本文から消える")
        self.assertEqual([s["name"] for s in loaded], ["summarize-logs"])

    def test_unknown_slash_line_is_passed_through_with_a_warning(self):
        """スキル名でない普通の行かもしれないので、偽陽性でプロンプトを壊さない。"""
        warnings = []
        with _SkillHome():
            out, loaded = ollama_skills.expand("/tmp を消して\n", warn=warnings.append)
        self.assertEqual(out, "/tmp を消して\n")
        self.assertEqual(loaded, [])
        self.assertTrue(any("見つかりません" in w for w in warnings))

    def test_explicit_missing_skill_raises(self):
        with _SkillHome():
            with self.assertRaises(ollama_skills.SkillNotFound):
                ollama_skills.expand("依頼", ["nope"])

    def test_disabled_ignores_slash_lines_but_keeps_explicit(self):
        with _SkillHome() as home:
            home.add("pdf")
            out, loaded = ollama_skills.expand("/pdf\n本文", ["pdf"], enabled=False)
        self.assertIn("/pdf\n本文", out, "スラッシュ行は本文のまま残る")
        self.assertEqual([s["name"] for s in loaded], ["pdf"])

    def test_duplicate_names_are_loaded_once(self):
        with _SkillHome() as home:
            home.add("pdf")
            out, loaded = ollama_skills.expand("/pdf\n本文", ["pdf"])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(out.count("# スキル: pdf"), 1)

    def test_skill_dir_placeholder_is_replaced(self):
        with _SkillHome() as home:
            path = home.add("tool", "実行: {skill_dir}/scripts/go.py")
            out, loaded = ollama_skills.expand("依頼", ["tool"])
        self.assertIn(f"実行: {path.parent}/scripts/go.py", out)
        self.assertTrue(loaded[0]["scripts"],
                        "同梱スクリプト前提であることを呼び出し側へ返す（ツールセットとの整合判定）")

    def test_plain_skill_is_not_marked_as_script_bundled(self):
        with _SkillHome() as home:
            home.add("prose", "手順を説明するだけ")
            _out, loaded = ollama_skills.expand("依頼", ["prose"])
        self.assertFalse(loaded[0]["scripts"])

    def test_partially_resolvable_leading_block_is_left_alone(self):
        """1 つでも解決できない行があれば「ただの本文」と見て触らない。"""
        with _SkillHome() as home:
            home.add("known")
            out, loaded = ollama_skills.expand("/known\n/unknown\n本文")
        self.assertEqual(out, "/known\n/unknown\n本文")
        self.assertEqual(loaded, [])


class TestListSkills(unittest.TestCase):
    def test_lists_only_dirs_with_skill_md(self):
        with _SkillHome() as home:
            home.add("a")
            (home.root / "b").mkdir()          # SKILL.md 無し
            names = [n for n, _p in ollama_skills.list_skills()]
        self.assertIn("a", names)
        self.assertNotIn("b", names)


if __name__ == "__main__":
    unittest.main()
