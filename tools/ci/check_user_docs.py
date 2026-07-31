#!/usr/bin/env python3
"""R10 検査 — 利用者向け文書の**本文**に内部名が現れないことを機械で確かめる。

R10（[常駐一本化設計](../../docs/plans/2026-07-24-single-resident-controller-design.md) §2）が
隠すのは「製品名としての内部名」であって、契約の語彙ではない。設計 §3.1 の用語集が
「契約の語彙としての『ノード』は残る — 隠すのは製品名だけ」と明記しているとおり、
`node_id` というキー名や `agent-node-command.schema.json` という契約名、
`single-resident-setup.md` というガイドのファイル名は**正当に**内部名を含む。

したがって素朴な `grep -w node` は成立しない。この検査は次を落としてから本文だけを見る:

- フェンス付きコードブロック（``` / ~~~）と HTML コメント
- インラインコード（`` `...` ``）— キー名・スキーマ名・コマンド名はここに入る
- リンク/画像のリンク先（`](...)`）と自動リンク（`<https://...>`）
- パスらしいトークン（`/` を含む）とファイル名らしいトークン（既知の拡張子で終わる）

どうしても本文で内部名に言及する必要がある行（R10 そのものを説明する行など）は、
行末に `<!-- r10-allow: 理由 -->` を書いて明示的に免除する。**黙って除外規則を
増やさない**ためで、免除は行ごとに理由が残る。

使い方:

    python3 tools/ci/check_user_docs.py            # リポジトリ全体を検査（CI と同じ）
    python3 tools/ci/check_user_docs.py --list     # 検査対象のファイル一覧
    python3 tools/ci/check_user_docs.py <file>...  # 指定ファイルだけ

終了コード: 0 = 違反なし / 1 = 違反あり / 2 = 使い方の誤り。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# 隠す内部名（実装計画 W3-2 / R4 の「node / sync / resident」）。
# **増やすならここだけ**——検査の語彙が複数箇所へ散ると、片方だけ育って検査が嘘になる。
INTERNAL_NAMES = ("resident", "sync", "node")

# 検査対象（利用者向け文書）。設計書・計画書（`docs/designs/` `docs/plans/`）は
# 内部の設計語彙で書くものなので対象外。glob はリポジトリルートからの相対。
TARGET_GLOBS = (
    "README.md",
    "docs/guides/*.md",
    "tools/agent-project/README.md",
    "tools/agent-project/GUIDE.md",
    "tools/agent-flow/README.md",
    "tools/agent-amigos/README.md",
    "tools/agent-dashboard/README.md",
)

ALLOW_MARK = "r10-allow"

_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"``[^`]*``|`[^`]*`")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S+")
_AUTOLINK = re.compile(r"<[^>\s]+>")
# パス・ファイル名として落とすトークンは **ASCII のパス文字だけ**で構成する。
# `\S+` にすると日本語は語間に空白が無いため「はnode、origin、heartbeat/lease を検査する」の
# ような一文がまるごと 1 トークンとして消え、検査したい語まで道連れになる。
_PATH_CHARS = r"A-Za-z0-9_.~*@:%#?=&$+\-"
_PATHY = re.compile(rf"[{_PATH_CHARS}]*[/\\][{_PATH_CHARS}/\\]*")
_FILENAME = re.compile(
    rf"[{_PATH_CHARS}]+\.(?:md|json|ya?ml|py|js|mjs|cjs|ts|sh|bash|ps1|service|txt|toml|cfg|ini|jsonl)\b")
# Node.js は JavaScript 処理系の固有名で、隠す対象の内部名ではない。
_NODEJS = re.compile(r"node\.?js", re.I)

_WORD = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(INTERNAL_NAMES) + r")(?![A-Za-z0-9_-])", re.I)


def prose_lines(text: str) -> "list[str]":
    """本文だけを行番号を保ったまま返す（落とした部分は空白に置き換える）。"""
    out: "list[str]" = []
    in_fence = False
    in_comment = False
    for raw in text.splitlines():
        line = raw
        if in_comment:
            end = line.find("-->")
            if end < 0:
                out.append("")
                continue
            line = line[end + 3:]
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence:
            out.append("")
            continue
        if ALLOW_MARK in line:
            out.append("")                  # 明示的な免除（理由はコメントに残る）
            continue
        line = re.sub(r"<!--.*?-->", " ", line)
        start = line.find("<!--")
        if start >= 0:
            in_comment = True
            line = line[:start]
        if _REF_DEF.match(line):
            out.append("")
            continue
        line = _INLINE_CODE.sub(" ", line)
        line = _LINK_TARGET.sub("] ", line)
        line = _AUTOLINK.sub(" ", line)
        line = _PATHY.sub(" ", line)
        line = _FILENAME.sub(" ", line)
        line = _NODEJS.sub(" ", line)
        out.append(line)
    return out


def check_text(text: str) -> "list[tuple[int, str, str]]":
    """(行番号, 内部名, 原文の行) の一覧を返す。空なら違反なし。"""
    raw = text.splitlines()
    found: "list[tuple[int, str, str]]" = []
    for i, line in enumerate(prose_lines(text)):
        for m in _WORD.finditer(line):
            found.append((i + 1, m.group(1).lower(), raw[i].strip()))
    return found


def check_file(path: Path) -> "list[tuple[int, str, str]]":
    return check_text(path.read_text(encoding="utf-8", errors="replace"))


def repo_root() -> Path:
    """このスクリプトの位置（`tools/ci/`）からリポジトリルートを決める。"""
    return Path(__file__).resolve().parents[2]


def target_files(root: "Path | None" = None) -> "list[Path]":
    root = root or repo_root()
    seen: "list[Path]" = []
    for pattern in TARGET_GLOBS:
        for p in sorted(root.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.append(p)
    return seen


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="利用者向け文書の内部名検査（R10）")
    ap.add_argument("files", nargs="*", help="検査するファイル（省略時は既定の対象一覧）")
    ap.add_argument("--list", action="store_true", help="検査対象のファイル一覧を出して終わる")
    args = ap.parse_args(argv)

    root = repo_root()
    files = [Path(f) for f in args.files] if args.files else target_files(root)
    if args.list:
        for p in files:
            print(os.path.relpath(p, root))
        return 0
    missing = [p for p in files if not p.is_file()]
    if missing:
        print(f"[r10] 検査対象が見つかりません: {', '.join(str(p) for p in missing)}",
              file=sys.stderr)
        return 2

    total = 0
    for p in files:
        for lineno, name, line in check_file(p):
            total += 1
            print(f"{os.path.relpath(p, root)}:{lineno}: 内部名 '{name}' が本文にあります\n"
                  f"    {line}")
    if total:
        print(f"\n[r10] 違反 {total} 件 / 検査 {len(files)} ファイル。\n"
              f"  利用者向け文書には内部名（{' / '.join(INTERNAL_NAMES)}）を出さない（R10）。\n"
              f"  対処: 利用者の言葉に書き換える（『常駐体』『同期』『端末』など）か、\n"
              f"        キー名・ファイル名としての言及ならインラインコード（`...`）で囲む。\n"
              f"        本文での言及が避けられない行は行末に "
              f"<!-- {ALLOW_MARK}: 理由 --> を付ける。",
              file=sys.stderr)
        return 1
    print(f"[r10] 違反なし（{len(files)} ファイル）。")
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
