"""スキル参照ファイル検査（`tools/ci/check_skill_references.py`）の単体テスト。

`check_skill()` という公開インターフェースに対して、実際のスキル構造を模した
一時ディレクトリを渡し、返ってくる `SkillCheckResult` の内容だけを見る。
正常系・切れリンク・孤児・2ホップ参照の4ケースを最低限カバーする。
最後の1本だけ性格が違い、リポジトリ本体の全スキルが今この瞬間に違反していない
ことを見る（＝ CI のゲート本体）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_skill_references as skillrefs   # noqa: E402


def make_skill(root: Path, name: str, skill_md: str,
                references: "dict[str, str] | None" = None,
                scripts: "dict[str, str] | None" = None) -> Path:
    """`root` 配下にスキルディレクトリを組み立てて返す。"""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    for rel, content in (references or {}).items():
        path = skill_dir / "references" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for rel, content in (scripts or {}).items():
        path = skill_dir / "scripts" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return skill_dir


class CheckSkillBehavior(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_clean_skill_with_direct_reference_has_no_violations(self):
        skill_dir = make_skill(
            self.root, "clean-skill",
            "# clean-skill\n\n詳細は [references/guide.md](references/guide.md) を参照。\n",
            references={"guide.md": "# ガイド\n本文。\n"},
        )
        result = skillrefs.check_skill(skill_dir)
        self.assertTrue(result.is_clean())
        self.assertEqual(result.broken_links, [])
        self.assertEqual(result.orphans, [])
        self.assertEqual(result.two_hop_violations, [])

    def test_direct_reference_by_bare_filename_is_not_flagged(self):
        """`### references/` 見出しの下でファイル名だけ列挙するスタイルを孤児扱いしない。"""
        skill_dir = make_skill(
            self.root, "bare-name-skill",
            "# bare-name-skill\n\n### references/\n\n- **guide.md** - ガイド\n",
            references={"guide.md": "# ガイド\n本文。\n"},
        )
        result = skillrefs.check_skill(skill_dir)
        self.assertTrue(result.is_clean())

    def test_broken_link_to_missing_reference_is_flagged(self):
        skill_dir = make_skill(
            self.root, "broken-link-skill",
            "# broken-link-skill\n\n手順は `references/missing.md` を参照。\n",
        )
        result = skillrefs.check_skill(skill_dir)
        self.assertEqual(result.broken_links, ["references/missing.md"])
        self.assertFalse(result.is_clean())

    def test_orphan_reference_unreachable_from_skill_md_is_flagged(self):
        skill_dir = make_skill(
            self.root, "orphan-skill",
            "# orphan-skill\n\n詳細は [references/used.md](references/used.md) を参照。\n",
            references={
                "used.md": "使われているファイル。\n",
                "orphan.md": "誰からも参照されないファイル。\n",
            },
        )
        result = skillrefs.check_skill(skill_dir)
        self.assertEqual(result.orphans, ["references/orphan.md"])
        self.assertFalse(result.is_clean())

    def test_reference_reachable_only_via_another_reference_is_two_hop_violation(self):
        skill_dir = make_skill(
            self.root, "two-hop-skill",
            "# two-hop-skill\n\n詳細は [references/entry.md](references/entry.md) を参照。\n",
            references={
                "entry.md": "深い話は `references/deep.md` を参照。\n",
                "deep.md": "深い内容。\n",
            },
        )
        result = skillrefs.check_skill(skill_dir)
        self.assertEqual(result.orphans, [], "2ホップでも到達できるので孤児ではない")
        self.assertEqual(result.two_hop_violations, ["references/deep.md"])
        self.assertFalse(result.is_clean())

    def test_allowlisted_two_hop_reference_is_not_a_violation(self):
        skill_dir = make_skill(
            self.root, "two-hop-allowed-skill",
            "# two-hop-allowed-skill\n\n詳細は [references/entry.md](references/entry.md) を参照。\n",
            references={
                "entry.md": "深い話は `references/deep.md` を参照。\n",
                "deep.md": "深い内容。\n",
            },
        )
        skillrefs.ALLOWED_TWO_HOP["two-hop-allowed-skill"] = frozenset({"references/deep.md"})
        try:
            result = skillrefs.check_skill(skill_dir)
        finally:
            del skillrefs.ALLOWED_TWO_HOP["two-hop-allowed-skill"]
        self.assertEqual(result.two_hop_violations, [])
        self.assertTrue(result.is_clean())

    def test_reference_reachable_only_via_script_is_not_orphan(self):
        skill_dir = make_skill(
            self.root, "script-reachable-skill",
            "# script-reachable-skill\n\nビルドスクリプトが資料を読み込む。\n",
            references={"workflows/step1.md": "手順。\n"},
            scripts={"builder.py": (
                'from pathlib import Path\n'
                'd = Path(__file__).parent.parent / "references" / "workflows"\n'
            )},
        )
        result = skillrefs.check_skill(skill_dir)
        self.assertEqual(result.orphans, [])

    def test_missing_skill_md_yields_no_violations(self):
        skill_dir = self.root / "no-skill-md"
        skill_dir.mkdir()
        result = skillrefs.check_skill(skill_dir)
        self.assertTrue(result.is_clean())

    def test_missing_references_dir_yields_no_orphans_or_two_hop(self):
        skill_dir = make_skill(self.root, "no-refs-dir-skill", "# no-refs-dir-skill\n本文のみ。\n")
        result = skillrefs.check_skill(skill_dir)
        self.assertTrue(result.is_clean())


class RepositorySkills(unittest.TestCase):
    """検査本体: リポジトリ本体の全スキルが今この瞬間に違反していないこと。"""

    def test_all_skills_have_no_reference_violations(self):
        bad = []
        for skill_dir in skillrefs.iter_skill_dirs():
            result = skillrefs.check_skill(skill_dir)
            for ref in result.broken_links:
                bad.append(f"{skill_dir.name}/SKILL.md: 切れリンク {ref}")
            for ref in result.orphans:
                bad.append(f"{skill_dir.name}/{ref}: 孤児")
            for ref in result.two_hop_violations:
                bad.append(f"{skill_dir.name}/{ref}: 2ホップ参照（未許可）")
        self.assertEqual(bad, [], "スキルの参照ファイルに違反がある:\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
