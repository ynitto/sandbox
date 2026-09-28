#!/usr/bin/env python3
"""スキル目録検査 — README.md のスキル一覧が `.github/skills/` の実体と一致することを機械で確かめる。

README.md の「## スキル一覧」は手で書く目録で、スキルを足しても消しても誰も
直さなければそのまま残る。とくに「常時ロード」の一覧は、install.py が
`metadata.tier: core` を見て毎セッション配るスキルの説明であり、ここがずれると
「いま何が常駐しているか」を文書から知る手段が無くなる。

**スキルの列挙規則はこの検査に書かない。** install.py の `_discover_all_skills` /
`_discover_core_skills` を import して、インストーラ自身に数えさせる。規則を
写すと、install.py 側だけが育って検査が嘘になるため。この検査が持つのは
README を読む規則と比較だけ。

見るもの（4 つ。どれも違反なら終了コード 1）:

(a) **常時ロード** — 見出しに「常時ロード」を含む `###` 節の表に並ぶスキル名の
    集合が、`_discover_core_skills()` と完全に一致すること。違えば両方向の差を出す。
(b) **宣言件数** — 「## スキル一覧（全 N スキル）」の N が
    `len(_discover_all_skills())` と一致すること。見出しが複数あれば全部を見る。
(c) **表と実体** — スキル一覧の節（「## スキル一覧」から次の `##` まで）の表に
    並ぶスキル名の集合と、`.github/skills/` のスキル名の集合の差が、両方向とも
    空であること。
(d) **重複見出し** — 同じ見出しが 2 回以上現れないこと。比べるのは**祖先の見出しを
    含めた見出しの経路**（例「## scrum-master の使い方 > ### ガードレール」）。
    別の節の下に同じ小見出しを置くのは正当な書き方なので落とさないが、同じ節を
    丸ごと 2 回書いた重複は経路ごと一致するので落ちる。

見ないもの:

- スキル名は表の 1 列目の太字（`| **name** | … |`）だけから取る。概要の文中や、
  スキル一覧の節の外（使い方の節の表など）に出てくる名前は数えない。
- 節見出しの「— N」（分類ごとの件数）と、どのスキルがどの分類に載っているか。
  分類は frontmatter に無く、正典が決まっていないため。
- フェンス付きコードブロック（``` / ~~~）の中の見出しと表。
- README.md 以外の文書。

README.md を直すのはこの検査の仕事ではない。どちらに合わせるか（README を直すか、
frontmatter の tier を変えるか）は設計判断なので、差を見せるところまでにしている。

使い方:

    python3 tools/ci/check_skill_catalog.py   # リポジトリ全体を検査（CI と同じ）

終了コード: 0 = 違反なし / 1 = 違反あり / 2 = 検査の前提が壊れている。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_CATALOG_HEADING = re.compile(r"^スキル一覧")
_DECLARED_COUNT = re.compile(r"^スキル一覧（全\s*(\d+)\s*スキル）")
_CORE_SECTION = "常時ロード"
_TABLE_NAME = re.compile(r"^\|\s*\*\*([^*|]+?)\*\*\s*\|")


def repo_root() -> Path:
    """このスクリプトの位置（`tools/ci/`）からリポジトリルートを決める。"""
    return Path(__file__).resolve().parents[2]


def load_installer(root: "Path | None" = None) -> ModuleType:
    """リポジトリ直下の install.py をモジュールとして読み込む（実行はしない）。"""
    path = (root or repo_root()) / "install.py"
    spec = importlib.util.spec_from_file_location("_skill_catalog_install", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"install.py を読み込めません: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prose_lines(text: str) -> "list[tuple[int, str]]":
    """フェンス付きコードブロックの外の行を（行番号, 行）で返す。"""
    out: "list[tuple[int, str]]" = []
    fence: "str | None" = None
    for i, line in enumerate(text.splitlines(), start=1):
        m = _FENCE.match(line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif m.group(1) == fence:
                fence = None
            continue
        if fence is None:
            out.append((i, line))
    return out


def parse_readme(text: str) -> dict:
    """README から検査に使う材料だけを抜く。

    返り値:
      declared:  [(行番号, N)] —「## スキル一覧（全 N スキル）」の N
      catalog:   スキル一覧の節の表に並ぶ名前の集合
      core:      「常時ロード」節の表に並ぶ名前の集合（節が無ければ None）
      headings:  [(行番号, 経路)] — 経路は祖先を含めた見出しの並び
    """
    declared: "list[tuple[int, int]]" = []
    catalog: "set[str]" = set()
    core: "set[str] | None" = None
    headings: "list[tuple[int, tuple[str, ...]]]" = []

    stack: "list[tuple[int, str]]" = []   # (level, 見出し行)
    in_catalog = False
    in_core = False
    for lineno, line in _prose_lines(text):
        h = _HEADING.match(line)
        if h:
            level, title = len(h.group(1)), h.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, f"{h.group(1)} {title}"))
            headings.append((lineno, tuple(s for _, s in stack)))
            if level <= 2:
                in_catalog = level == 2 and bool(_CATALOG_HEADING.match(title))
                in_core = False
                m = _DECLARED_COUNT.match(title) if level == 2 else None
                if m:
                    declared.append((lineno, int(m.group(1))))
            elif level == 3:
                in_core = in_catalog and _CORE_SECTION in title
                if in_core and core is None:
                    core = set()
            continue
        m = _TABLE_NAME.match(line)
        if m and in_catalog:
            name = m.group(1).strip()
            catalog.add(name)
            if in_core and core is not None:
                core.add(name)
    return {"declared": declared, "catalog": catalog, "core": core, "headings": headings}


def _names(names: "set[str] | list[str]") -> str:
    return ", ".join(sorted(names))


def check_catalog(readme_text: str, all_skills: "list[str]",
                  core_skills: "list[str]") -> "list[str]":
    """違反を 1 件 1 行の文字列で返す（空なら違反なし）。"""
    parsed = parse_readme(readme_text)
    problems: "list[str]" = []
    actual_all, actual_core = set(all_skills), set(core_skills)

    # (a) 常時ロード
    core = parsed["core"]
    if core is None:
        problems.append(f"README.md: 「{_CORE_SECTION}」の節がありません"
                        f"（core は {len(actual_core)} 本: {_names(actual_core)}）")
    else:
        only_readme, only_core = core - actual_core, actual_core - core
        if only_readme:
            problems.append(f"README.md: 常時ロードに載るが tier: core でない "
                            f"{len(only_readme)} 本: {_names(only_readme)}")
        if only_core:
            problems.append(f"README.md: tier: core なのに常時ロードに無い "
                            f"{len(only_core)} 本: {_names(only_core)}")

    # (b) 宣言件数
    if not parsed["declared"]:
        problems.append("README.md: 「## スキル一覧（全 N スキル）」の見出しがありません")
    for lineno, n in parsed["declared"]:
        if n != len(actual_all):
            problems.append(f"README.md:{lineno}: 宣言は全 {n} スキル、"
                            f".github/skills/ の実数は {len(actual_all)}")

    # (c) 表と実体
    only_readme, only_repo = parsed["catalog"] - actual_all, actual_all - parsed["catalog"]
    if only_readme:
        problems.append(f"README.md: 表にあるが .github/skills/ に無い "
                        f"{len(only_readme)} 本: {_names(only_readme)}")
    if only_repo:
        problems.append(f"README.md: .github/skills/ にあるが表に無い "
                        f"{len(only_repo)} 本: {_names(only_repo)}")

    # (d) 重複見出し
    seen: "dict[tuple[str, ...], list[int]]" = {}
    for lineno, path in parsed["headings"]:
        seen.setdefault(path, []).append(lineno)
    for path, linenos in seen.items():
        if len(linenos) > 1:
            where = ", ".join(f"L{n}" for n in linenos)
            problems.append(f"README.md: 見出し「{' > '.join(path)}」が "
                            f"{len(linenos)} 回あります（{where}）")
    return problems


def check_root(root: Path, installer: "ModuleType | None" = None) -> "tuple[list[str], int]":
    """`root` の README.md と `.github/skills/` を突き合わせる。(違反, スキル数) を返す。"""
    installer = installer or load_installer()
    skills_dir = str(root / ".github" / "skills")
    all_skills = installer._discover_all_skills(skills_dir)
    core_skills = installer._discover_core_skills(skills_dir)
    readme = (root / "README.md").read_text(encoding="utf-8", errors="replace")
    return check_catalog(readme, all_skills, core_skills), len(all_skills)


def main(argv: "list[str] | None" = None) -> int:
    root = repo_root()
    if not (root / "README.md").is_file():
        print(f"[skill-catalog] README.md が見つかりません: {root}", file=sys.stderr)
        return 2
    problems, count = check_root(root)
    for p in problems:
        print(p)
    if problems:
        print(f"\n[skill-catalog] 違反 {len(problems)} 件 / 検査 {count} スキル。\n"
              f"  README.md のスキル一覧が .github/skills/ の実体とずれています。\n"
              f"  対処: README.md の一覧・件数を実体に合わせるか、"
              f"frontmatter の tier を README に合わせる（どちらが正しいかは設計判断）。",
              file=sys.stderr)
        return 1
    print(f"[skill-catalog] 違反なし（{count} スキル）。")
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
