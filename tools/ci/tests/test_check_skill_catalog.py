"""スキル目録検査（`tools/ci/check_skill_catalog.py`）の単体テスト。

一時ディレクトリに `.github/skills/<名前>/SKILL.md` と README.md を合成し、
`check_root()` が返す違反だけを見る。列挙は本物の install.py に任せる（検査と
同じ経路を通すため）が、**本物の README.md と `.github/skills/` には依存しない**
——依存すると README を直した日にこのテストが落ちる。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_skill_catalog as catalog   # noqa: E402

INSTALLER = catalog.load_installer()


def make_skill(root: Path, name: str, tier: "str | None" = None) -> None:
    skill_dir = root / ".github" / "skills" / name
    skill_dir.mkdir(parents=True)
    meta = f"metadata:\n  version: 1.0.0\n  tier: {tier}\n" if tier else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: テスト用\n{meta}---\n\n# {name}\n",
        encoding="utf-8")


def table(names: "list[str]") -> str:
    rows = "".join(f"| **{n}** | 概要 |\n" for n in names)
    return f"| スキル | 概要 |\n|--------|------|\n{rows}"


def readme(core: "list[str]", others: "list[str]", total: int, tail: str = "") -> str:
    return (f"# Agent Skills\n\n## スキル一覧（全 {total} スキル）\n\n"
            f"### 基盤スキル（常時ロード）— {len(core)}\n\n{table(core)}\n"
            f"### その他 — {len(others)}\n\n{table(others)}\n"
            f"## インストール\n\n本文\n{tail}")


class CheckSkillCatalog(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        make_skill(self.root, "alpha", tier="core")
        make_skill(self.root, "beta", tier="core")
        make_skill(self.root, "gamma")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, text: str) -> "list[str]":
        (self.root / "README.md").write_text(text, encoding="utf-8")
        problems, count = catalog.check_root(self.root, INSTALLER)
        self.assertEqual(count, 3)
        return problems

    # --- 基準: すべて一致していれば違反なし ---

    def test_all_consistent(self) -> None:
        self.assertEqual(self.check(readme(["alpha", "beta"], ["gamma"], 3)), [])

    # --- (a) 常時ロード ---

    def test_core_extra_in_readme(self) -> None:
        problems = self.check(readme(["alpha", "beta", "gamma"], [], 3))
        self.assertEqual(problems,
                         ["README.md: 常時ロードに載るが tier: core でない 1 本: gamma"])

    def test_core_missing_from_readme(self) -> None:
        problems = self.check(readme(["alpha"], ["beta", "gamma"], 3))
        self.assertEqual(problems,
                         ["README.md: tier: core なのに常時ロードに無い 1 本: beta"])

    def test_core_section_absent(self) -> None:
        text = f"## スキル一覧（全 3 スキル）\n\n### その他\n\n{table(['alpha', 'beta', 'gamma'])}"
        problems = self.check(text)
        self.assertEqual(len(problems), 1)
        self.assertIn("「常時ロード」の節がありません", problems[0])

    # --- (b) 宣言件数 ---

    def test_declared_count_mismatch(self) -> None:
        problems = self.check(readme(["alpha", "beta"], ["gamma"], 2))
        self.assertEqual(problems, ["README.md:3: 宣言は全 2 スキル、.github/skills/ の実数は 3"])

    # --- (c) 表と実体 ---

    def test_table_has_ghost(self) -> None:
        problems = self.check(readme(["alpha", "beta"], ["gamma", "ghost"], 3))
        self.assertEqual(problems,
                         ["README.md: 表にあるが .github/skills/ に無い 1 本: ghost"])

    def test_table_misses_skill(self) -> None:
        make_skill(self.root, "delta")
        (self.root / "README.md").write_text(readme(["alpha", "beta"], ["gamma"], 4),
                                             encoding="utf-8")
        problems, _ = catalog.check_root(self.root, INSTALLER)
        self.assertEqual(problems,
                         ["README.md: .github/skills/ にあるが表に無い 1 本: delta"])

    def test_names_outside_catalog_are_ignored(self) -> None:
        # スキル一覧の節の外（使い方の表）や、コードブロックの中の名前は数えない
        tail = f"\n## 使い方\n\n{table(['ghost'])}\n```\n{table(['ghost2'])}```\n"
        self.assertEqual(self.check(readme(["alpha", "beta"], ["gamma"], 3, tail)), [])

    # --- (d) 重複見出し ---

    def test_same_subheading_under_different_parents_is_allowed(self) -> None:
        tail = "\n## A の使い方\n\n### ガードレール\n\n## B の使い方\n\n### ガードレール\n"
        self.assertEqual(self.check(readme(["alpha", "beta"], ["gamma"], 3, tail)), [])

    def test_duplicated_heading(self) -> None:
        tail = "\n## インストール\n\n再掲\n"
        problems = self.check(readme(["alpha", "beta"], ["gamma"], 3, tail))
        self.assertEqual(len(problems), 1)
        self.assertIn("見出し「# Agent Skills > ## インストール」が 2 回あります", problems[0])

    def test_duplicated_block_is_reported(self) -> None:
        # README 冒頭の丸ごと重複と同じ形: 一覧の見出しと常時ロード節が 2 回
        head = (f"## スキル一覧（全 3 スキル）\n\n"
                f"### 基盤スキル（常時ロード）— 2\n\n{table(['alpha', 'beta'])}\n")
        body = readme(["alpha", "beta"], ["gamma"], 3).removeprefix("# Agent Skills\n\n")
        text = "# Agent Skills\n\n" + head + body
        problems = self.check(text)
        self.assertEqual(len(problems), 2)
        self.assertTrue(all("2 回あります" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
