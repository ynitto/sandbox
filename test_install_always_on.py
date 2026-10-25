"""常時枠（置いた SKILL.md の name + description）の計測と、インストール対象の絞り込みを検証する。

`python -m unittest test_install_always_on` で実行する。
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import install


def write_skill(root, name, description, tier="experimental"):
    d = Path(root, name)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nmetadata:\n  tier: {tier}\n---\n本文",
        encoding="utf-8")


class MeasureAlwaysOnTest(unittest.TestCase):
    def test_sums_name_and_description_bytes_largest_first(self):
        with tempfile.TemporaryDirectory() as home:
            write_skill(home, "a", "あいう")          # 1 + 9
            write_skill(home, "bb", '"引用符は数えない"')  # 2 + 24
            d = Path(home, "c")
            d.mkdir()
            (d / "SKILL.md").write_text(
                "---\nname: c\ndescription: >\n  一行目\n  二行目\nmetadata:\n  tier: core\n---\n",
                encoding="utf-8")                      # 1 + len("一行目 二行目")
            Path(home, "not-a-skill").mkdir()
            self.assertEqual(install._measure_always_on(home),
                             [("bb", 26), ("c", 1 + len("一行目 二行目".encode())), ("a", 10)])

    def test_warns_only_above_twice_the_aider_budget(self):
        budget = install.AIDER_INDEX_BUDGET
        for total, warned in ((2 * budget, False), (2 * budget + 1, True)):
            with self.subTest(total=total), tempfile.TemporaryDirectory() as home:
                write_skill(home, "s", "x" * (total - 1))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    install.report_always_on(home)
                self.assertIn(f"常時枠 1 本 / {total:,} B", out.getvalue())
                self.assertEqual("警告" in out.getvalue(), warned)


class InstallableSkillsTest(unittest.TestCase):
    def test_excludes_deprecated_but_keeps_it_in_the_catalog_enumeration(self):
        with tempfile.TemporaryDirectory() as repo:
            write_skill(repo, "new", "使う", tier="stable")
            write_skill(repo, "old", "使わない", tier="deprecated")
            self.assertEqual(install._discover_installable_skills(repo), (["new"], ["old"]))
            self.assertEqual(install._discover_all_skills(repo), ["new", "old"])


if __name__ == "__main__":
    unittest.main()
