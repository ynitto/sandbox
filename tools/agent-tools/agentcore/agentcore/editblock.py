"""agentcore.editblock — SEARCH/REPLACE ブロックの解析と適用（aider 非依存の編集適用）。

設計: docs/plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md §3.6・未決 5。

## なぜ持つか

aider はエージェントではなく**編集適用エンジン**で、我々が使っているのは「対象ファイルが
決まった局所編集」だけである。その 1 点を自前で持てるなら、依存（litellm 経由）と毎ターン
再送される約 6.3 KB のシステムプロンプトを落とせる。**外すかどうかは実測で決める**ので、
ここは去就を測るための対照実装である。

## 何を写したか（aider 0.86.2 `coders/editblock_coder.py` を読んで再実装）

コードは持ち込まず、**規約と曖昧一致の階段**だけを同じにした。同じ綴りのブロックを同じ
順序で当てないと、比較が「実装の差」ではなく「書式の差」になる。

階段は **3 段**である:

1. `_perfect_replace` … 行の完全一致
2. `_replace_missing_leading_whitespace` … 先頭空白だけがずれている場合
3. `_try_dotdotdots` … `...` で中略されたブロック

**difflib の類似一致（4 段目）は写していない。** 上流にあるが、`replace_most_similar_chunk`
の途中に無条件 `return` があって**到達しない**（aider 0.86.2 を AST で確認。設計 §3.6 が
「perfect_replace → replace_most_similar_chunk → difflib」と書いているのは実物と違う）。
効いていないものを移植対象に数えると、去就の判断材料が実際より重く見える。
"""
from __future__ import annotations

import re
from pathlib import Path

__all__ = ["Block", "ApplyError", "find_blocks", "apply_blocks", "replace_chunk"]

# ブロックの綴り。aider と同じ正規表現にする（モデルが学習している書式を変えない）。
_HEAD = re.compile(r"^<{5,9} SEARCH>?\s*$")
_DIVIDER = re.compile(r"^={5,9}\s*$")
_UPDATED = re.compile(r"^>{5,9} REPLACE\s*$")
_FENCES = ("```", "~~~")


class ApplyError(RuntimeError):
    """ブロックを当てられなかった。**黙って無視しない**——「直したつもり」を作らない。"""


class Block(tuple):
    """(path, search, replace) の 1 枚。"""

    __slots__ = ()

    def __new__(cls, path: str, search: str, replace: str):
        return super().__new__(cls, (path, search, replace))

    path = property(lambda self: self[0])
    search = property(lambda self: self[1])
    replace = property(lambda self: self[2])


def _strip_filename(raw: str) -> str:
    """ブロック直前の行からファイル名を取り出す（装飾を剥がす）。"""
    name = raw.strip()
    if name == "..." or not name:
        return ""
    for fence in _FENCES:
        if name.startswith(fence):
            candidate = name[len(fence):]
            return candidate if candidate and ("." in candidate or "/" in candidate) else ""
    return name.rstrip(":").lstrip("#").strip().strip("`").strip("*")


def _find_filename(before: "list[str]") -> str:
    """ブロックの手前 3 行から、いちばん近い妥当なファイル名を選ぶ。"""
    for raw in reversed(before):
        name = _strip_filename(raw)
        if name and ("." in name or "/" in name):
            return name
    return ""


def find_blocks(text: str) -> "list[Block]":
    """本文から SEARCH/REPLACE ブロックを取り出す。壊れた綴りは `ApplyError`。

    ファイル名は**直前 3 行**から拾い、無ければ直前のブロックのものを引き継ぐ
    （同じファイルへ続けて当てる書き方が普通なので、毎回書かせない）。
    """
    lines = (text or "").splitlines(keepends=True)
    blocks: "list[Block]" = []
    current = ""
    i = 0
    while i < len(lines):
        if not _HEAD.match(lines[i].strip()):
            i += 1
            continue
        name = _find_filename(lines[max(0, i - 3):i]) or current
        if not name:
            raise ApplyError(
                "SEARCH ブロックの直前にファイル名の行がありません"
                "（`パス` を単独の行で書いてから <<<<<<< SEARCH を置いてください）")
        current = name
        i += 1
        search: "list[str]" = []
        while i < len(lines) and not _DIVIDER.match(lines[i].strip()):
            search.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ApplyError("`=======` が見つかりません（SEARCH ブロックが閉じていません）")
        i += 1
        replace: "list[str]" = []
        while i < len(lines) and not _UPDATED.match(lines[i].strip()):
            replace.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ApplyError("`>>>>>>> REPLACE` が見つかりません")
        i += 1
        blocks.append(Block(name, "".join(search), "".join(replace)))
    return blocks


def _prep(content: str) -> "tuple[str, list[str]]":
    if content and not content.endswith("\n"):
        content += "\n"
    return content, content.splitlines(keepends=True)


def _perfect_replace(whole_lines, part_lines, replace_lines) -> "str | None":
    """1 段目: 行の完全一致。"""
    part = tuple(part_lines)
    span = len(part_lines)
    for i in range(len(whole_lines) - span + 1):
        if tuple(whole_lines[i:i + span]) == part:
            return "".join(whole_lines[:i] + replace_lines + whole_lines[i + span:])
    return None


def _leading_offset(whole_lines, part_lines) -> "str | None":
    """非空白部分が全行一致し、字下げのずれが全行で同じならその字下げを返す。"""
    if not all(w.lstrip() == p.lstrip() for w, p in zip(whole_lines, part_lines)):
        return None
    offsets = {w[:len(w) - len(p)] for w, p in zip(whole_lines, part_lines) if w.strip()}
    return offsets.pop() if len(offsets) == 1 else None


def _replace_missing_leading_whitespace(whole_lines, part_lines, replace_lines) -> "str | None":
    """2 段目: 先頭空白だけがずれている場合。

    弱いモデルほど字下げを落とす。SEARCH と REPLACE の両方から**同じだけ**外して
    当て直し、当たった位置の字下げを REPLACE 側へ付け直す。
    """
    leading = [len(p) - len(p.lstrip()) for p in part_lines + replace_lines if p.strip()]
    if leading and min(leading):
        cut = min(leading)
        part_lines = [p[cut:] if p.strip() else p for p in part_lines]
        replace_lines = [p[cut:] if p.strip() else p for p in replace_lines]
    span = len(part_lines)
    for i in range(len(whole_lines) - span + 1):
        add = _leading_offset(whole_lines[i:i + span], part_lines)
        if add is None:
            continue
        indented = [add + r if r.strip() else r for r in replace_lines]
        return "".join(whole_lines[:i] + indented + whole_lines[i + span:])
    return None


_DOTS = re.compile(r"(^\s*\.\.\.\n)", re.MULTILINE | re.DOTALL)


def _try_dotdotdots(whole: str, part: str, replace: str) -> "str | None":
    """3 段目: `...` で中略されたブロック。中略の対応が崩れていれば `ApplyError`。"""
    part_pieces = _DOTS.split(part)
    replace_pieces = _DOTS.split(replace)
    if len(part_pieces) != len(replace_pieces):
        raise ApplyError("`...` の数が SEARCH と REPLACE で合っていません")
    if len(part_pieces) == 1:
        return None
    if any(part_pieces[i] != replace_pieces[i] for i in range(1, len(part_pieces), 2)):
        raise ApplyError("`...` の位置が SEARCH と REPLACE で合っていません")
    for chunk, into in zip(part_pieces[::2], replace_pieces[::2]):
        if not chunk and not into:
            continue
        if not chunk:
            whole = (whole if whole.endswith("\n") else whole + "\n") + into
            continue
        found = whole.count(chunk)
        if found != 1:
            raise ApplyError("`...` 区間が 1 か所に定まりません"
                             f"（{found} 箇所に一致）")
        whole = whole.replace(chunk, into, 1)
    return whole


def replace_chunk(whole: str, search: str, replace: str) -> "str | None":
    """階段を上から順に試す。当たらなければ None（呼び出し側が失敗として扱う）。"""
    whole, whole_lines = _prep(whole)
    search, part_lines = _prep(search)
    replace, replace_lines = _prep(replace)

    for attempt in (part_lines,
                    # 先頭の空行はモデルが余計に足しがちなので 1 行だけ落として再挑戦する。
                    part_lines[1:] if len(part_lines) > 2 and not part_lines[0].strip() else None):
        if attempt is None:
            continue
        for rung in (_perfect_replace, _replace_missing_leading_whitespace):
            result = rung(whole_lines, attempt, replace_lines)
            if result is not None:
                return result
    return _try_dotdotdots(whole, search, replace)


def apply_blocks(blocks, cwd, *, dry_run: bool = False) -> "list[str]":
    """ブロックを順に当てる。触ったパス（cwd からの相対）を返す。

    `dry_run=True` なら書かずに当たるかだけ確かめる（`--readonly` の根拠。aider の
    `--dry-run` に当たる）。当たらないブロックは `ApplyError`——「直したつもり」を作らない。
    """
    root = Path(cwd).resolve()
    touched: "list[str]" = []
    for block in blocks:
        target = (root / block.path).resolve()
        if root not in target.parents and target != root:
            raise ApplyError(f"作業ディレクトリの外は変更できません: {block.path}")
        exists = target.is_file()
        content = target.read_text(encoding="utf-8") if exists else ""
        if not block.search.strip():
            # SEARCH が空＝新規作成か末尾追記（aider と同じ意味）。
            updated = content + block.replace
        else:
            if not exists:
                raise ApplyError(f"ファイルがありません: {block.path}")
            updated = replace_chunk(content, block.search, block.replace)
            if updated is None:
                raise ApplyError(
                    f"{block.path} に SEARCH ブロックが見つかりません"
                    "（原文をそのまま写してください。字下げも含めて 1 文字ずつ一致が要ります）")
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(updated, encoding="utf-8")
        rel = str(target.relative_to(root))
        if rel not in touched:
            touched.append(rel)
    return touched
