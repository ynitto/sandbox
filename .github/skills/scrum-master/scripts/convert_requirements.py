#!/usr/bin/env python3
"""
convert_requirements.py - requirements.md → requirements.json 変換

requirements-definer が出力した requirements.md を構造化 JSON に変換する。
scrum-master が plan.json を生成する前段処理として利用できる。
外部依存ゼロ（Python 標準ライブラリのみ）。

使い方:
  # デフォルト（カレントディレクトリの requirements.md を変換）
  python convert_requirements.py

  # ファイルを指定
  python convert_requirements.py --file path/to/requirements.md

  # 出力先を指定（デフォルト: requirements.json）
  python convert_requirements.py --output path/to/requirements.json

  # 標準出力に JSON を表示（ファイル保存なし）
  python convert_requirements.py --stdout

終了コード:
  0 = 変換成功
  1 = 変換エラー
  2 = ファイルが見つからない / パースエラー
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# ─── Markdown パーサー ────────────────────────────────────────

def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.match(r"^[-: ]+$", c) for c in cells if c.strip())


def _split_table_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_requirements_md(text: str) -> dict:
    """
    requirements.md を解析して requirements dict を返す。
    """
    lines = text.splitlines()
    result: dict = {
        "goal": "",
        "functional_requirements": [],
        "non_functional_requirements": [],
        "scope": {"in": [], "out": []},
    }

    section: str | None = None
    current_req: dict | None = None
    in_ac_table = False
    in_scope_in = False
    in_scope_out = False

    def _save_req() -> None:
        nonlocal current_req
        if current_req is not None:
            result["functional_requirements"].append(current_req)
            current_req = None

    for line in lines:
        stripped = line.strip()

        # ── H2 セクション境界 ──────────────────────────────
        if re.match(r"^## ", stripped):
            _save_req()
            in_ac_table = False
            in_scope_in = False
            in_scope_out = False

            title = stripped[3:].strip()
            if "プロジェクト概要" in title:
                section = "overview"
            elif re.search(r"ペルソナ", title):
                section = "personas"
                result.setdefault("personas", [])
            elif "スコープ" in title:
                section = "scope"
            elif "非機能要件" in title:
                section = "nonfunctional"
            elif "機能要件" in title:
                section = "functional"
            elif "カスタマージャーニー" in title:
                section = "journey"
            else:
                section = None
            continue

        # ── H3: スコープのサブセクション ──────────────────
        if re.match(r"^### ", stripped) and section == "scope":
            sub = stripped[4:].strip()
            in_scope_in = "In" in sub
            in_scope_out = "Out" in sub
            continue

        # ── H3: 機能要件 F-NN ─────────────────────────────
        if re.match(r"^### ", stripped) and section == "functional":
            _save_req()
            in_ac_table = False
            h3 = stripped[4:].strip()
            m = re.match(r"(F-\d{2,}):\s*(.+)", h3)
            if m:
                current_req = {
                    "id": m.group(1),
                    "name": m.group(2).strip(),
                    "description": "",
                    "user_story": "",
                    "moscow": "must",
                    "acceptance_criteria": [],
                }
            continue

        # ── プロジェクト概要 ───────────────────────────────
        if section == "overview":
            m = re.match(r"\*\*ゴール\*\*:\s*(.+)", stripped)
            if m:
                result["goal"] = m.group(1).strip()
            continue

        # ── 機能要件の各フィールド ─────────────────────────
        if section == "functional" and current_req is not None:
            m = re.match(r"\*\*ユーザーストーリー\*\*:\s*(.+)", stripped)
            if m:
                val = m.group(1).strip()
                current_req["user_story"] = val
                current_req["description"] = val
                continue

            m = re.match(r"\*\*MoSCoW\*\*:\s*(.+)", stripped, re.IGNORECASE)
            if m:
                current_req["moscow"] = m.group(1).strip().lower()
                continue

            m = re.match(r"\*\*ペルソナ\*\*:\s*(.+)", stripped)
            if m:
                current_req["persona"] = m.group(1).strip()
                continue

            if re.match(r"\*\*受け入れ条件\*\*", stripped):
                in_ac_table = True
                continue

            if in_ac_table and stripped.startswith("|"):
                cells = _split_table_row(stripped)
                if _is_table_separator(cells):
                    continue
                if cells and cells[0].strip() in ("#", "No", "no", ""):
                    continue
                if len(cells) >= 4:
                    given = cells[1]
                    when = cells[2]
                    then = cells[3]
                    if any([given, when, then]):
                        current_req["acceptance_criteria"].append({
                            "given": given,
                            "when": when,
                            "then": then,
                        })
                continue

            if in_ac_table and stripped and not stripped.startswith("|"):
                in_ac_table = False

        # ── 非機能要件テーブル ─────────────────────────────
        if section == "nonfunctional" and stripped.startswith("|"):
            cells = _split_table_row(stripped)
            if _is_table_separator(cells):
                continue
            if len(cells) >= 3 and re.match(r"N-\d{2,}", cells[0]):
                result["non_functional_requirements"].append({
                    "id": cells[0],
                    "name": cells[1],
                    "description": cells[2],
                })
            continue

        # ── ペルソナテーブル ───────────────────────────────
        if section == "personas" and stripped.startswith("|"):
            cells = _split_table_row(stripped)
            if _is_table_separator(cells):
                continue
            if len(cells) >= 3 and re.match(r"P-\d{2,}", cells[0]):
                result["personas"].append({
                    "id": cells[0],
                    "name": cells[1],
                    "description": cells[2],
                })
            continue

        # ── スコープ ───────────────────────────────────────
        if section == "scope":
            if in_scope_in and stripped.startswith("- "):
                feat = stripped[2:].strip()
                if feat:
                    result["scope"]["in"].append(feat)
            elif in_scope_out and stripped.startswith("|"):
                cells = _split_table_row(stripped)
                if _is_table_separator(cells):
                    continue
                if len(cells) >= 1:
                    feat = cells[0]
                    note = cells[1] if len(cells) > 1 else ""
                    if feat and feat not in ("機能", ""):
                        result["scope"]["out"].append({
                            "feature": feat,
                            "note": note,
                        })

    _save_req()
    return result


# ─── ファイルユーティリティ ───────────────────────────────────

def find_requirements_file(cwd: Path) -> Path | None:
    """requirements.md を探す。"""
    p = cwd / "requirements.md"
    return p if p.exists() else None


# ─── エントリポイント ──────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="requirements.md を requirements.json に変換する"
    )
    parser.add_argument(
        "--file",
        default=None,
        help="変換元の requirements.md パス（デフォルト: カレントディレクトリを検索）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="出力先の requirements.json パス（デフォルト: requirements.md と同じディレクトリの requirements.json）",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="ファイル保存せずに標準出力へ JSON を表示する",
    )
    args = parser.parse_args()

    cwd = Path(".")

    # 入力ファイルの解決
    if args.file:
        src_path = Path(args.file)
    else:
        src_path = find_requirements_file(cwd)
        if src_path is None:
            print("❌ requirements.md が見つかりません", file=sys.stderr)
            return 2

    if not src_path.exists():
        print(f"❌ ファイルが見つかりません: {src_path}", file=sys.stderr)
        return 2

    # パース
    try:
        text = src_path.read_text(encoding="utf-8")
        data = parse_requirements_md(text)
    except Exception as e:
        print(f"❌ 読み込みエラー: {e}", file=sys.stderr)
        return 2

    json_text = json.dumps(data, ensure_ascii=False, indent=2)

    # 出力
    if args.stdout:
        print(json_text)
        return 0

    if args.output:
        dest_path = Path(args.output)
    else:
        dest_path = src_path.parent / "requirements.json"

    try:
        dest_path.write_text(json_text, encoding="utf-8")
    except Exception as e:
        print(f"❌ 書き込みエラー: {e}", file=sys.stderr)
        return 1

    func_count = len(data.get("functional_requirements", []))
    nfr_count = len(data.get("non_functional_requirements", []))
    print(
        f"✅ 変換完了: {src_path} → {dest_path} "
        f"(機能要件 {func_count} 件 / 非機能要件 {nfr_count} 件)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
