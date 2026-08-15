#!/usr/bin/env python3
"""
migrate.py — 既存ステートマシン定義の検査と新スキーマへの追随（lint / migrate）

スキーマは加算的に拡張される（check → check_on_exhausted → write）。拡張のたびに
手持ちの YAML が静かに古びるのを止めるため、既存定義を検査して dry-run で差分を提示し、
`--apply` で書き換える。正規化・検証は engine.py を import して 1 実装に保つ（再実装しない）。

検出項目:
  (a) 検査結果（check_status / check_ok / check_output）で分岐する遷移があるのに
      check 宣言が無いステート — 現行エンジンでは検証エラー。対象を列挙して修正案を示す
  (b) シェル記号入りの check 宣言 — argv 直接実行なので投入前に落ちる。修正案を示す
  (c) check を持つ非終端ステートの write 未割付 — action 本文から編集対象が
      一意に決まる場合だけ提案する（自動適用可）
  (d) check_on_exhausted の暗黙既定 — escalate を明示する提案（自動適用可）

(a)(b) は自動修正できない（検査コマンドを決めるのは人の仕事）。(c)(d) は `--apply` で
コメントを保ったまま行を挿入し、書き換え後に engine の検証を再実行して、エラーが
増えるなら書き込まない。

使い方:
  python scripts/migrate.py examples/*.yaml            # dry-run（検出項目と差分の表示）
  python scripts/migrate.py .statemachine --apply      # フォルダごと適用
  python scripts/migrate.py 'flows/**/*.yaml'          # glob 指定

終了コード: 0 = 問題も未適用の提案も無し / 1 = 検証エラーあり、または dry-run で提案あり
"""

from __future__ import annotations

import argparse
import difflib
import glob as _glob
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

# スクリプト単体実行 / スキルルートからの両方に対応
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.engine import load_workflow, validate_workflow  # noqa: E402


# ─────────────────────────────────────────────
#  対象の収集
# ─────────────────────────────────────────────

def collect_targets(raw_paths: list[str]) -> list[Path]:
    """ファイル・フォルダ・glob の混在指定から workflow YAML を集める。

    フォルダは配下の *.yaml / *.yml を再帰で拾い、トップレベルに `states` を持つ
    ものだけを対象にする（置き場が定まっていないため、無関係な YAML の混入を許す）。
    """
    found: list[Path] = []
    for raw in raw_paths:
        path = Path(raw)
        if path.is_dir():
            candidates = sorted([*path.rglob("*.yaml"), *path.rglob("*.yml")])
        elif _glob.has_magic(raw):
            candidates = sorted(Path(p) for p in _glob.glob(raw, recursive=True))
        else:
            candidates = [path]
        for p in candidates:
            if p.is_file() and _looks_like_workflow(p) and p not in found:
                found.append(p)
    return found


def _looks_like_workflow(path: Path) -> bool:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("states"), dict)


# ─────────────────────────────────────────────
#  検査（lint）— 判定は engine の validate をそのまま使う
# ─────────────────────────────────────────────

def _suggestion(error: str) -> str:
    if "check がありません" in error:
        return ("修正案: そのステートに check（成果を測るコマンド）を宣言する。"
                "決められないなら condition_rule を check_* 以外のキーへ書き換える")
    if "シェル記号" in error:
        return "修正案: パイプ等はスクリプトへまとめ、check にはそのスクリプトの argv を書く"
    return ""


def lint_file(path: Path) -> list[tuple[str, str]]:
    """(エラー文, 修正案) のリスト。engine の検証結果が正典で、ここでは再判定しない。"""
    try:
        workflow = load_workflow(path)
    except Exception as exc:  # 構造が壊れていて load 自体が失敗する定義
        return [(f"読み込みに失敗しました: {exc}", "")]
    return [(e, _suggestion(e)) for e in validate_workflow(workflow)]


# ─────────────────────────────────────────────
#  提案（c)(d) — コメントを保つためテキストへ行挿入する
# ─────────────────────────────────────────────

@dataclass
class Proposal:
    state_id: str
    kind: str       # "write" | "check_on_exhausted"
    at: int         # lines への挿入位置
    line: str       # 挿入する行（改行なし）
    note: str


def _find_state_block(lines: list[str], state_id: str) -> "tuple[int, int, int] | None":
    """states 配下のステートキー行を探し (開始行, 終了行exclusive, キーのインデント) を返す。

    ponytail: 行パターンで探す素朴な実装。`context:` 配下に同名キーがあると誤検出しうる。
    位置まで欲しくなったらコメント保持パーサ（ruamel）へ上げる。
    """
    pattern = re.compile(rf"^(\s+){re.escape(state_id)}:\s*(#.*)?$")
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        end = len(lines)
        for j in range(i + 1, len(lines)):
            stripped = lines[j].strip()
            if not stripped:
                continue
            if (len(lines[j]) - len(lines[j].lstrip())) <= indent and not stripped.startswith("#"):
                end = j
                break
        return i, end, indent
    return None


def _find_entry(lines: list[str], start: int, end: int, key: str) -> "tuple[int, int, int] | None":
    """ブロック内のフィールド行を探し (開始行, 継続行を含む終了行exclusive, インデント) を返す。"""
    pattern = re.compile(rf"^(\s*){key}:")
    for i in range(start + 1, end):
        m = pattern.match(lines[i])
        if not m:
            continue
        indent = len(m.group(1))
        j = i + 1
        while j < end:
            stripped = lines[j].strip()
            if not stripped:
                break
            line_indent = len(lines[j]) - len(lines[j].lstrip())
            # ブロック形式の継続（深いインデント・同じ深さのシーケンス項目・内側のコメント）
            if line_indent > indent or (line_indent == indent and stripped.startswith("- ")):
                j += 1
                continue
            break
        return i, j, indent
    return None


# バッククォートで名指しされたファイルパスだけを編集対象の候補にする。
# スペース入り（コマンド）・拡張子なし（スキル名）は候補にしない。
_BACKTICK_RE = re.compile(r"`([^`\s]+)`")


def _write_candidates(action_text: str, check: "dict | None") -> list[str]:
    """action 本文から write 割付の候補パスを抽出する。

    check の argv に載っているパスは測る側（テスト等）なので除外する。
    .md はコード系ワークフローでは仕様参照であることが多いので、
    .md 以外の候補があるときは除外する（全候補が .md ならドキュメント編集とみなして残す）。
    """
    check_tokens = {check["command"], *check["args"]} if check else set()
    candidates: list[str] = []
    for m in _BACKTICK_RE.finditer(action_text):
        p = m.group(1)
        if not re.search(r"\.[A-Za-z0-9_]+$", p):
            continue
        if any(p in t or t in p for t in check_tokens):
            continue
        if p not in candidates:
            candidates.append(p)
    non_md = [p for p in candidates if not p.endswith(".md")]
    return non_md if non_md else candidates


def propose_edits(path: Path, text: str) -> list[Proposal]:
    raw = yaml.safe_load(text)
    workflow = load_workflow(path)
    lines = text.splitlines()
    proposals: list[Proposal] = []
    for state_id, sdef in (raw.get("states") or {}).items():
        if not isinstance(sdef, dict) or "check" not in sdef:
            continue
        block = _find_state_block(lines, state_id)
        if block is None:
            continue
        start, end, _ = block
        entry = _find_entry(lines, start, end, "check")
        if entry is None:
            continue
        check_start, check_end, indent = entry
        state = workflow.states.get(state_id)
        pad = " " * indent

        # (c) write 未割付 — 編集対象が一意に決まる場合だけ提案する
        if not sdef.get("terminal") and "write" not in sdef and state is not None:
            candidates = _write_candidates(state.action, state.check)
            if len(candidates) == 1:
                proposals.append(Proposal(
                    state_id=state_id, kind="write",
                    at=check_start, line=f"{pad}write: {candidates[0]}",
                    note=f"state '{state_id}' に write: {candidates[0]}"
                         "（action 本文で名指しされた唯一の編集対象）"))

        # (d) check_on_exhausted の暗黙既定を明示する
        if "check_on_exhausted" not in sdef:
            proposals.append(Proposal(
                state_id=state_id, kind="check_on_exhausted",
                at=check_end, line=f"{pad}check_on_exhausted: escalate",
                note=f"state '{state_id}' に check_on_exhausted: escalate（暗黙既定の明示）"))
    return proposals


def apply_proposals(text: str, proposals: list[Proposal]) -> str:
    lines = text.splitlines()
    for p in sorted(proposals, key=lambda p: p.at, reverse=True):
        lines.insert(p.at, p.line)
    ending = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + ending


def apply_file(path: Path, new_text: str, before_errors: list[str]) -> "str | None":
    """検証してから書き込む。エラーが増えるなら書かない。戻り値は失敗理由（成功は None）。"""
    tmp = path.parent / f".migrate-{uuid.uuid4().hex[:8]}-{path.name}"
    tmp.write_text(new_text, encoding="utf-8")
    try:
        after_errors = validate_workflow(load_workflow(tmp))
    except Exception as exc:
        return f"書き換え後の定義が読み込めません: {exc}"
    finally:
        tmp.unlink(missing_ok=True)
    grown = [e for e in after_errors if e not in before_errors]
    if grown:
        return "書き換えで検証エラーが増えるため適用しません: " + "; ".join(grown)
    path.write_text(new_text, encoding="utf-8")
    return None


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────

def run(paths: list[str], apply: bool) -> int:
    targets = collect_targets(paths)
    if not targets:
        print("対象の workflow YAML が見つかりません（トップレベルに states を持つファイルが対象）")
        return 1

    exit_code = 0
    for path in targets:
        text = path.read_text(encoding="utf-8")
        issues = lint_file(path)
        proposals = [] if any("読み込みに失敗" in e for e, _ in issues) else propose_edits(path, text)

        if not issues and not proposals:
            print(f"{path}: 問題なし")
            continue

        print(f"{path}:")
        for error, suggestion in issues:
            exit_code = 1
            print(f"  ✗ {error}")
            if suggestion:
                print(f"    {suggestion}")

        if not proposals:
            continue
        new_text = apply_proposals(text, proposals)
        for p in proposals:
            print(f"  → 提案: {p.note}")
        if apply:
            failure = apply_file(path, new_text, [e for e, _ in issues])
            if failure:
                exit_code = 1
                print(f"  ✗ {failure}")
            else:
                print(f"  ✓ 適用しました（{len(proposals)} 件）")
        else:
            exit_code = 1
            diff = difflib.unified_diff(
                text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                fromfile=str(path), tofile=f"{path} (migrated)")
            sys.stdout.writelines(diff)
            print("  （適用するには --apply）")
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="既存ステートマシン定義を検査し、新スキーマへ追随させる")
    parser.add_argument("paths", nargs="+", metavar="PATH",
                        help="workflow YAML / フォルダ / glob（繰り返し指定可）")
    parser.add_argument("--apply", action="store_true",
                        help="提案 (c)(d) をファイルへ書き込む（既定は dry-run で差分表示）")
    args = parser.parse_args()
    sys.exit(run(args.paths, args.apply))


if __name__ == "__main__":
    main()
