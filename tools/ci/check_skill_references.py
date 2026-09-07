#!/usr/bin/env python3
"""スキルの参照ファイル（progressive disclosure）の健全性を機械で確かめる。

`.github/skills/*/` は SKILL.md を軽く保ち、詳細を `references/*.md` に逃がす設計
（段階的開示）になっている。この設計は「エージェントがリンクを踏んで必要な時だけ
読む」ことを前提にしているが、エージェントが Read ツールではなく Bash の
`cat`/`sed` で読む場合、リンクをたどらず SKILL.md 本文の**地の文字列**だけを見る。
つまりこの前提が崩れてもエラーは出ない——スキルは「読めば動く」ままだが、
`references/` に置いた詳細情報がエージェントに一度も渡らない、という壊れ方をする。

この検査は3つの観点で機械的に守る:

(a) **切れリンク** — SKILL.md 本文が `references/xxx.md` という**パス風の文字列**で
    言及していて、そのファイルが実在しないもの。Markdown リンク（`[text](path)`）と
    バックティック表記（`` `references/xxx.md` `` や `` `<PREFIX>/references/xxx.md` ``）
    の両方を見る——後者はコピー用のパスや他スキルへの言及でも使われるが、地の文で
    `references/` に続けてファイル名を書いた時点で「そこにある」という主張になる。
    主張と実態がずれているなら、対象を作るかその言及自体を外すべきというのがこの
    検査の立場。

(b) **孤児** — `references/` 配下の `.md` が、SKILL.md からもスクリプトからも
    一度も到達できないもの。到達の数え方は2種類:
      - **地の文到達**: SKILL.md 本文、または SKILL.md から地の文到達済みの
        `references/*.md` の本文に、そのファイルの**ファイル名（basename）**が
        文字列として出てくる。`references/` 接頭辞の有無は問わない——見出しで
        「### references/」と宣言してから箇条書きでファイル名だけ列挙するスキルが
        実在するため（react-frontend-coder 等）、接頭辞を必須にすると実際に読める
        参照まで孤児と誤検出する。
      - **スクリプト経由到達**: `scripts/*.py`（`.js` `.sh` も対象）のソースが
        `references/<name>` という形でディレクトリ名・ファイル名を組み立てている
        場合、そのスクリプトを読めば該当ファイル（`<name>` がディレクトリなら
        配下の `.md` すべて）に到達できる。presenter の `pptx_builder.py` が
        `references/workflows/` `references/guides/` を実行時に読み出す構成が
        これに当たる——SKILL.md はそれらのファイル名を一つも書かないが、
        「スクリプトを実行すれば読まれる」という到達経路が別に成立している。

(c) **2ホップ参照** — 地の文到達はしているが、SKILL.md から直接ではなく
    `references/*.md` 経由でしか出てこないもの（SKILL.md → 参照A → 参照B）。
    これは孤児ではない（読めばいつかは辿れる）が、「SKILL.md を読んだだけでは
    存在を知れない」という段階的開示の設計意図を外れている。**許可リストに無い
    2ホップ参照は違反**とする——見つかったら SKILL.md 側から直接参照するのが
    既定の直し方で、許可リストは「意図的にそうしている」ことを明記する場所。
    スクリプト経由到達は数え方が別物なので、この2ホップ判定には含めない。

使い方:

    python3 tools/ci/check_skill_references.py            # 全スキルを検査（CI と同じ）
    python3 tools/ci/check_skill_references.py --list     # 検査対象のスキル一覧
    python3 tools/ci/check_skill_references.py <skill>...  # 指定スキルだけ（名前 or パス）

終了コード: 0 = 違反なし / 1 = 違反あり / 2 = 使い方の誤り。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 2ホップ参照の許可リスト。**増やすならここだけ**——キー: スキル名、値: そのスキルの
# `references/` からの相対パス（例: "references/foo.md"）の集合。
# 既定の直し方は「SKILL.md から直接参照する」であり、ここに載せるのは最終手段。
ALLOWED_TWO_HOP: "dict[str, frozenset[str]]" = {}

_SCRIPT_SUFFIXES = (".py", ".js", ".sh")

# Markdown リンク: [text](references/xxx.md)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\((references/[\w./\-]+\.md)\)")
# バックティック表記: `references/xxx.md` や `<接頭辞>/references/xxx.md`
_BACKTICK_PATH_RE = re.compile(r"`(?:[^`]*?/)?(references/[\w.\-/]+\.md)`")

# scripts/*.py 等が references/ 配下のディレクトリ・ファイルを組み立てるパターン。
#   Path(...) / "references" / "workflows"   のような pathlib 連結
_SCRIPT_PATHLIB_RE = re.compile(r"""["']references["']\s*/\s*["']([\w.\-]+)["']""")
#   "references/workflows" のようなプレーンな文字列
_SCRIPT_PLAIN_RE = re.compile(r"references/([\w.\-]+)")


def _path_style_refs(text: str) -> "set[str]":
    """SKILL.md 本文中の `references/xxx.md` パス風文字列を集める（実在は問わない）。"""
    return set(_MD_LINK_RE.findall(text)) | set(_BACKTICK_PATH_RE.findall(text))


def _script_reachable(skill_dir: Path) -> "set[str]":
    """scripts/ のソースから読み出される references/*.md（スキル相対パス）を返す。"""
    refs_dir = skill_dir / "references"
    scripts_dir = skill_dir / "scripts"
    reachable: "set[str]" = set()
    if not scripts_dir.is_dir():
        return reachable
    for script in scripts_dir.rglob("*"):
        if script.suffix not in _SCRIPT_SUFFIXES or not script.is_file():
            continue
        text = script.read_text(encoding="utf-8", errors="replace")
        names = set(_SCRIPT_PATHLIB_RE.findall(text)) | set(_SCRIPT_PLAIN_RE.findall(text))
        for name in names:
            target = refs_dir / name
            if target.is_dir():
                reachable |= {p.relative_to(skill_dir).as_posix() for p in target.rglob("*.md")}
            elif target.is_file() and target.suffix == ".md":
                reachable.add(target.relative_to(skill_dir).as_posix())
    return reachable


@dataclass
class SkillCheckResult:
    skill_dir: Path
    broken_links: "list[str]" = field(default_factory=list)
    orphans: "list[str]" = field(default_factory=list)
    two_hop_violations: "list[str]" = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.skill_dir.name

    def is_clean(self) -> bool:
        return not (self.broken_links or self.orphans or self.two_hop_violations)


def check_skill(skill_dir: Path) -> SkillCheckResult:
    skill_md = skill_dir / "SKILL.md"
    result = SkillCheckResult(skill_dir=skill_dir)
    if not skill_md.is_file():
        return result
    skill_text = skill_md.read_text(encoding="utf-8", errors="replace")

    referenced_paths = _path_style_refs(skill_text)
    result.broken_links = sorted(
        ref for ref in referenced_paths if not (skill_dir / ref).is_file()
    )

    refs_dir = skill_dir / "references"
    if not refs_dir.is_dir():
        return result
    all_refs = sorted(p.relative_to(skill_dir).as_posix() for p in refs_dir.rglob("*.md"))
    if not all_refs:
        return result

    ref_text = {rf: (skill_dir / rf).read_text(encoding="utf-8", errors="replace") for rf in all_refs}

    def mentions(text: str, ref: str) -> bool:
        return Path(ref).name in text

    direct = {rf for rf in all_refs if mentions(skill_text, rf)}

    edges: "dict[str, set[str]]" = {}
    for rf in all_refs:
        edges[rf] = {other for other in all_refs if other != rf and mentions(ref_text[rf], other)}

    reachable = set(direct)
    frontier = set(direct)
    while frontier:
        next_frontier: "set[str]" = set()
        for rf in frontier:
            for other in edges.get(rf, ()):
                if other not in reachable:
                    reachable.add(other)
                    next_frontier.add(other)
        frontier = next_frontier

    script_reachable = _script_reachable(skill_dir)
    allowed = ALLOWED_TWO_HOP.get(skill_dir.name, frozenset())

    result.orphans = sorted(
        rf for rf in all_refs if rf not in reachable and rf not in script_reachable
    )
    result.two_hop_violations = sorted(
        rf for rf in reachable if rf not in direct and rf not in allowed
    )
    return result


def repo_root() -> Path:
    """このスクリプトの位置（`tools/ci/`）からリポジトリルートを決める。"""
    return Path(__file__).resolve().parents[2]


def skills_root(root: "Path | None" = None) -> Path:
    return (root or repo_root()) / ".github" / "skills"


def iter_skill_dirs(root: "Path | None" = None) -> "list[Path]":
    base = skills_root(root)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def _resolve_skill_arg(arg: str, root: Path) -> "Path | None":
    p = Path(arg)
    candidates = [p, skills_root(root) / arg]
    for c in candidates:
        if (c / "SKILL.md").is_file():
            return c
    return None


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="スキル参照ファイルの健全性検査")
    ap.add_argument("skills", nargs="*", help="検査するスキル（名前 or パス、省略時は全スキル）")
    ap.add_argument("--list", action="store_true", help="検査対象のスキル一覧を出して終わる")
    args = ap.parse_args(argv)

    root = repo_root()
    if args.skills:
        skill_dirs = []
        missing = []
        for arg in args.skills:
            resolved = _resolve_skill_arg(arg, root)
            if resolved is None:
                missing.append(arg)
            else:
                skill_dirs.append(resolved)
        if missing:
            print(f"[skill-refs] 指定されたスキルが見つかりません: {', '.join(missing)}",
                  file=sys.stderr)
            return 2
    else:
        skill_dirs = iter_skill_dirs(root)

    if args.list:
        for d in skill_dirs:
            print(os.path.relpath(d, root))
        return 0

    total = 0
    for skill_dir in skill_dirs:
        r = check_skill(skill_dir)
        rel = os.path.relpath(skill_dir, root)
        for ref in r.broken_links:
            total += 1
            print(f"{rel}/SKILL.md: 切れリンク '{ref}' — ファイルが存在しません")
        for ref in r.orphans:
            total += 1
            print(f"{rel}/{ref}: 孤児 — SKILL.md からもスクリプトからも到達できません")
        for ref in r.two_hop_violations:
            total += 1
            print(f"{rel}/{ref}: 2ホップ参照 — SKILL.md から直接参照されておらず、"
                  f"許可リストにもありません")

    if total:
        print(f"\n[skill-refs] 違反 {total} 件 / 検査 {len(skill_dirs)} スキル。\n"
              f"  対処: 切れリンクは参照先を作るかリンクを外す / 孤児は SKILL.md から\n"
              f"        参照するか削除する / 2ホップ参照は SKILL.md から直接参照する\n"
              f"        （意図的なものだけ tools/ci/check_skill_references.py の\n"
              f"        ALLOWED_TWO_HOP に明記する）。",
              file=sys.stderr)
        return 1
    print(f"[skill-refs] 違反なし（{len(skill_dirs)} スキル）。")
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
