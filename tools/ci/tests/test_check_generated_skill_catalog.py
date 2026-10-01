"""生成カタログ検査（`tools/ci/check_generated_skill_catalog.py`）の単体テスト。

大半は一時ディレクトリに `.github/skills/<名前>/SKILL.md` を合成して見る。列挙は
本物の install.py、エントリの中身は本物の generator に任せる（検査と同じ経路を通すため）。
最後の `RepoCatalogTest` だけは、コミット済みの skill-catalog.json が本物の
install.py の列挙と一致していることを確かめる。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_generated_skill_catalog as gen_check   # noqa: E402
import check_skill_catalog   # noqa: E402

INSTALLER = check_skill_catalog.load_installer()
GENERATOR = gen_check.load_generator()


def make_skill(root: Path, name: str, tier: "str | None" = None,
               fm_name: "str | None" = None, description: str = "テスト用") -> Path:
    skill_dir = root / ".github" / "skills" / name
    skill_dir.mkdir(parents=True)
    meta = f"metadata:\n  version: 1.0.0\n  tier: {tier}\n" if tier else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {fm_name or name}\ndescription: {description}\n{meta}---\n\n# {name}\n",
        encoding="utf-8")
    return skill_dir


class FixtureCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        make_skill(self.root, "alpha", tier="core")
        make_skill(self.root, "beta", tier="stable")
        make_skill(self.root, "gamma")
        self.skills_dir = self.root / ".github" / "skills"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self) -> "list[str]":
        problems, _ = gen_check.check_root(self.root, INSTALLER, GENERATOR)
        return problems

    def write(self) -> int:
        return gen_check.write_root(self.root, INSTALLER, GENERATOR)

    def committed(self) -> dict:
        return json.loads((self.root / gen_check.CATALOG_REL).read_text(encoding="utf-8"))


class FreshnessTest(FixtureCase):
    def test_fresh_catalog_passes(self) -> None:
        self.assertEqual(self.write(), 3)
        self.assertEqual(self.check(), [])

    def test_missing_catalog_fails(self) -> None:
        problems = self.check()
        self.assertTrue(any("ファイルがありません" in p for p in problems), problems)

    def test_added_skill_is_detected(self) -> None:
        self.write()
        make_skill(self.root, "delta")
        problems = self.check()
        self.assertTrue(any("total_skills=3、実体は 4" in p for p in problems), problems)
        self.assertTrue(any("載っていないスキル 1 本: delta" in p for p in problems), problems)

    def test_removed_skill_is_detected(self) -> None:
        self.write()
        (self.skills_dir / "gamma" / "SKILL.md").unlink()
        problems = self.check()
        self.assertTrue(any("実体に無いスキル 1 本: gamma" in p for p in problems), problems)

    def test_changed_frontmatter_is_detected(self) -> None:
        self.write()
        (self.skills_dir / "beta" / "SKILL.md").write_text(
            "---\nname: beta\ndescription: 書き換えた\n---\n", encoding="utf-8")
        problems = self.check()
        self.assertTrue(any("中身が古いスキル 1 本: beta" in p for p in problems), problems)

    def test_formatting_drift_is_detected(self) -> None:
        self.write()
        path = self.root / gen_check.CATALOG_REL
        path.write_text(json.dumps(self.committed(), ensure_ascii=False, indent=4) + "\n",
                        encoding="utf-8")
        problems = self.check()
        self.assertTrue(any("並び・集計・書式" in p for p in problems), problems)

    def test_crlf_checkout_is_not_drift(self) -> None:
        self.write()
        path = self.root / gen_check.CATALOG_REL
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.check(), [])

    def test_broken_json_is_reported(self) -> None:
        self.write()
        (self.root / gen_check.CATALOG_REL).write_text("{", encoding="utf-8")
        problems = self.check()
        self.assertTrue(any("JSON として読めません" in p for p in problems), problems)


class ConsistencyTest(FixtureCase):
    """生成結果そのものが install.py の列挙と矛盾しないこと。"""

    def test_sets_match_installer(self) -> None:
        self.write()
        catalog = self.committed()
        all_skills = INSTALLER._discover_all_skills(str(self.skills_dir))
        self.assertEqual({s["name"] for s in catalog["skills"]}, set(all_skills))
        self.assertEqual(catalog["total_skills"], len(all_skills))
        self.assertEqual({s["name"] for s in catalog["skills"] if s["tier"] == "core"},
                         set(INSTALLER._discover_core_skills(str(self.skills_dir))))

    def test_frontmatter_name_mismatch_fails(self) -> None:
        make_skill(self.root, "epsilon", fm_name="other-name")
        self.write()
        problems = self.check()
        self.assertTrue(any("install.py が見つけないスキル名 other-name" in p
                            for p in problems), problems)

    def test_generator_default_discovery_matches_installer(self) -> None:
        # install.py は `_` / `.` 始まりも SKILL.md があればスキルとみなし、
        # SKILL.md の無いディレクトリやファイルは数えない。生成器単体の既定も同じであること。
        make_skill(self.root, "_shared")
        (self.skills_dir / "no-skill-md").mkdir()
        (self.skills_dir / "README.md").write_text("x", encoding="utf-8")
        self.assertEqual(GENERATOR.discover_skills(str(self.skills_dir)),
                         INSTALLER._discover_all_skills(str(self.skills_dir)))
        self.assertEqual(GENERATOR.generate_catalog(str(self.skills_dir)),
                         gen_check.expected_catalog(self.skills_dir, INSTALLER, GENERATOR)[0])


class DeterminismTest(FixtureCase):
    def test_repeated_generation_is_identical(self) -> None:
        first = gen_check.expected_catalog(self.skills_dir, INSTALLER, GENERATOR)[1]
        second = gen_check.expected_catalog(self.skills_dir, INSTALLER, GENERATOR)[1]
        self.assertEqual(first, second)

    def test_listing_order_does_not_matter(self) -> None:
        expected = gen_check.expected_catalog(self.skills_dir, INSTALLER, GENERATOR)[1]
        real = os.listdir
        with mock.patch.object(os, "listdir", lambda p: list(reversed(real(p)))):
            shuffled = gen_check.expected_catalog(self.skills_dir, INSTALLER, GENERATOR)[1]
        self.assertEqual(expected, shuffled)

    def test_runtime_byproducts_do_not_change_output(self) -> None:
        expected = gen_check.expected_catalog(self.skills_dir, INSTALLER, GENERATOR)[1]
        (self.skills_dir / "gamma" / "scripts" / "__pycache__").mkdir(parents=True)
        (self.skills_dir / "gamma" / "references").mkdir()
        (self.skills_dir / "gamma" / "references" / ".DS_Store").write_text("", encoding="utf-8")
        self.assertEqual(gen_check.expected_catalog(self.skills_dir, INSTALLER, GENERATOR)[1],
                         expected)

    def test_scripts_and_references_are_reflected(self) -> None:
        (self.skills_dir / "gamma" / "scripts").mkdir()
        (self.skills_dir / "gamma" / "scripts" / "run.py").write_text("", encoding="utf-8")
        catalog = gen_check.expected_catalog(self.skills_dir, INSTALLER, GENERATOR)[0]
        gamma = next(s for s in catalog["skills"] if s["name"] == "gamma")
        self.assertTrue(gamma["has_scripts"])
        self.assertFalse(gamma["has_references"])


class RepoCatalogTest(unittest.TestCase):
    """コミット済みの skill-catalog.json と本物の install.py の列挙が一致すること。"""

    def test_committed_catalog_matches_installer(self) -> None:
        root = check_skill_catalog.repo_root()
        skills_dir = str(root / ".github" / "skills")
        catalog = json.loads((root / gen_check.CATALOG_REL).read_text(encoding="utf-8"))
        all_skills = INSTALLER._discover_all_skills(skills_dir)
        self.assertEqual({s["name"] for s in catalog["skills"]}, set(all_skills))
        self.assertEqual(catalog["total_skills"], len(all_skills))
        self.assertEqual({s["name"] for s in catalog["skills"] if s["tier"] == "core"},
                         set(INSTALLER._discover_core_skills(skills_dir)))

    def test_committed_catalog_is_fresh(self) -> None:
        problems, _ = gen_check.check_root(check_skill_catalog.repo_root(), INSTALLER, GENERATOR)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
