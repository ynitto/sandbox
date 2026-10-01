#!/usr/bin/env python3
"""生成カタログ検査 — コミット済みの `generated/skill-catalog.json` が、いまの実体から
生成し直した結果と完全に一致することを機械で確かめる。

`check_skill_catalog.py` が README.md（人が書く目録）を見るのに対し、こちらは
git-skill-manager が持つ機械可読の目録 `.github/skills/git-skill-manager/generated/
skill-catalog.json` だけを見る。スキルを足しても消しても、この JSON は誰かが
生成し直さない限り古いまま残る（実際に 26 スキルのまま 89 スキルの時代まで残った）。

**スキルの列挙規則はこの検査に書かない。** install.py の `_discover_all_skills` /
`_discover_core_skills` を `check_skill_catalog.load_installer()` で読み込み、
`_discover_all_skills` の結果を generator（`generate_skill_catalog.py`）の
`generate_catalog(skills_dir, skill_names)` にそのまま渡す。エントリの中身
（description・tier など）は generator が作る。この検査が持つのは比較だけ。

見るもの（どれも違反なら終了コード 1）:

(a) **生成器と install.py の食い違い** — 生成し直した結果について
    - スキル名の集合 == `_discover_all_skills()`（frontmatter の name とディレクトリ名がずれると落ちる）
    - total_skills == スキル数
    - tier=core の集合 == `_discover_core_skills()`
(b) **鮮度** — コミット済みファイルの中身が、生成し直した結果と 1 バイトも違わないこと
    （改行は LF に揃えて比べる）。違えば、増えた・消えた・中身が変わったスキル名を出す。

使い方:

    python3 tools/ci/check_generated_skill_catalog.py           # 検査（CI と同じ）
    python3 tools/ci/check_generated_skill_catalog.py --write   # 生成し直して書き込む

終了コード: 0 = 違反なし / 1 = 違反あり / 2 = 検査の前提が壊れている。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_skill_catalog   # noqa: E402

CATALOG_REL = Path(".github/skills/git-skill-manager/generated/skill-catalog.json")
GENERATOR_REL = Path(".github/skills/git-skill-manager/scripts/generate_skill_catalog.py")


def load_generator(root: "Path | None" = None) -> ModuleType:
    """generate_skill_catalog.py をモジュールとして読み込む（実行はしない）。"""
    path = (root or check_skill_catalog.repo_root()) / GENERATOR_REL
    spec = importlib.util.spec_from_file_location("_skill_catalog_generator", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"generate_skill_catalog.py を読み込めません: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _names(names: "set[str]") -> str:
    return ", ".join(sorted(names))


def expected_catalog(skills_dir: Path, installer: ModuleType,
                     generator: ModuleType) -> "tuple[dict, str]":
    """install.py の列挙で generator を回し、(カタログ, 書き込む文字列) を返す。"""
    names = installer._discover_all_skills(str(skills_dir))
    catalog = generator.generate_catalog(str(skills_dir), names)
    return catalog, generator.render_catalog(catalog)


def check_consistency(catalog: dict, all_skills: "list[str]",
                      core_skills: "list[str]") -> "list[str]":
    """(a) 生成結果が install.py の列挙と矛盾しないか。"""
    problems: "list[str]" = []
    entries = catalog.get("skills", [])
    names = {s.get("name") for s in entries}
    actual_all, actual_core = set(all_skills), set(core_skills)
    if names != actual_all:
        if names - actual_all:
            problems.append(f"生成結果: install.py が見つけないスキル名 "
                            f"{_names(names - actual_all)}（frontmatter の name がディレクトリ名と違う？）")
        if actual_all - names:
            problems.append(f"生成結果: install.py が見つけるのに載らないスキル "
                            f"{_names(actual_all - names)}")
    if catalog.get("total_skills") != len(actual_all):
        problems.append(f"生成結果: total_skills={catalog.get('total_skills')}、"
                        f"install.py が見つけるスキルは {len(actual_all)} 本")
    core = {s.get("name") for s in entries if s.get("tier") == "core"}
    if core != actual_core:
        problems.append(f"生成結果: tier=core の集合が install.py と違う"
                        f"（生成のみ: {_names(core - actual_core) or 'なし'} / "
                        f"install.py のみ: {_names(actual_core - core) or 'なし'}）")
    return problems


def describe_drift(committed_text: str, expected: dict) -> "list[str]":
    """(b) コミット済みと生成結果の差を、スキル名の単位で言葉にする。"""
    rel = CATALOG_REL.as_posix()
    try:
        committed = json.loads(committed_text)
        entries = {s["name"]: s for s in committed.get("skills", [])}
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return [f"{rel}: JSON として読めません（{e}）"]
    want = {s["name"]: s for s in expected["skills"]}
    out: "list[str]" = []
    if committed.get("total_skills") != expected["total_skills"]:
        out.append(f"{rel}: total_skills={committed.get('total_skills')}、"
                   f"実体は {expected['total_skills']}")
    if set(want) - set(entries):
        out.append(f"{rel}: 載っていないスキル {len(set(want) - set(entries))} 本: "
                   f"{_names(set(want) - set(entries))}")
    if set(entries) - set(want):
        out.append(f"{rel}: 実体に無いスキル {len(set(entries) - set(want))} 本: "
                   f"{_names(set(entries) - set(want))}")
    changed = {n for n in set(want) & set(entries) if want[n] != entries[n]}
    if changed:
        out.append(f"{rel}: 中身が古いスキル {len(changed)} 本: {_names(changed)}")
    if not out:
        out.append(f"{rel}: スキルの中身は同じだが、並び・集計・書式が生成結果と違います")
    return out


def check_root(root: Path, installer: "ModuleType | None" = None,
               generator: "ModuleType | None" = None) -> "tuple[list[str], int]":
    """`root` のコミット済みカタログを検査する。(違反, スキル数) を返す。"""
    installer = installer or check_skill_catalog.load_installer()
    generator = generator or load_generator()
    skills_dir = root / ".github" / "skills"
    catalog, text = expected_catalog(skills_dir, installer, generator)
    problems = check_consistency(catalog, installer._discover_all_skills(str(skills_dir)),
                                 installer._discover_core_skills(str(skills_dir)))
    path = root / CATALOG_REL
    if not path.is_file():
        problems.append(f"{CATALOG_REL.as_posix()}: ファイルがありません")
    else:
        committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if committed != text:
            problems.extend(describe_drift(committed, catalog))
    return problems, catalog["total_skills"]


def write_root(root: Path, installer: "ModuleType | None" = None,
               generator: "ModuleType | None" = None) -> int:
    """生成し直してコミット用のファイルへ書く。書いたスキル数を返す。"""
    installer = installer or check_skill_catalog.load_installer()
    generator = generator or load_generator()
    catalog, text = expected_catalog(root / ".github" / "skills", installer, generator)
    path = root / CATALOG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return catalog["total_skills"]


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true",
                        help="生成し直して generated/skill-catalog.json に書き込む")
    args = parser.parse_args(argv)
    root = check_skill_catalog.repo_root()
    if not (root / GENERATOR_REL).is_file():
        print(f"[generated-catalog] 生成器が見つかりません: {root / GENERATOR_REL}",
              file=sys.stderr)
        return 2
    if args.write:
        count = write_root(root)
        print(f"[generated-catalog] {CATALOG_REL.as_posix()} を書きました（{count} スキル）。")
    problems, count = check_root(root)
    for p in problems:
        print(p)
    if problems:
        print(f"\n[generated-catalog] 違反 {len(problems)} 件 / 検査 {count} スキル。\n"
              f"  対処: python3 tools/ci/check_generated_skill_catalog.py --write を実行して\n"
              f"  生成し直した {CATALOG_REL.as_posix()} をコミットする。",
              file=sys.stderr)
        return 1
    print(f"[generated-catalog] 違反なし（{count} スキル）。")
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
