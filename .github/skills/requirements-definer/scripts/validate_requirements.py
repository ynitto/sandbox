#!/usr/bin/env python3
"""
validate_requirements.py - requirements.md / requirements.json のバリデーション

requirements-definer が出力した requirements.md（または後方互換の requirements.json）が
正しいスキーマに準拠しているかを検証する。外部依存ゼロ（Python 標準ライブラリのみ）。

使い方:
  # デフォルト（カレントディレクトリの requirements.md を自動検出）
  python validate_requirements.py

  # ファイルを指定
  python validate_requirements.py --file path/to/requirements.md
  python validate_requirements.py --file path/to/requirements.json

  # 警告も表示（オプションフィールドの未設定も報告）
  python validate_requirements.py --strict

終了コード:
  0 = バリデーション通過
  1 = バリデーションエラーあり
  2 = ファイルが見つからない / パースエラー
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# ─── 定数 ────────────────────────────────────────────────────

FUNC_ID_PATTERN = re.compile(r"^F-\d{2,}$")
NON_FUNC_ID_PATTERN = re.compile(r"^N-\d{2,}$")
VALID_MOSCOW = {"must", "should", "could", "wont"}
VALID_PERSONA_ID_PATTERN = re.compile(r"^P-\d{2,}$")


# ─── Markdown パーサー ────────────────────────────────────────

def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.match(r"^[-: ]+$", c) for c in cells if c.strip())


def _split_table_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_requirements_md(text: str) -> dict:
    """
    requirements.md を解析して requirements dict を返す。
    JSON スキーマと互換のある形式で返す。
    """
    lines = text.splitlines()
    result: dict = {
        "goal": "",
        "functional_requirements": [],
        "non_functional_requirements": [],
        "scope": {"in": [], "out": []},
    }

    section: str | None = None      # 現在の H2 セクション
    current_req: dict | None = None  # 処理中の機能要件
    in_ac_table = False              # 受け入れ条件テーブル内か
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
            elif "機能要件" in title:
                section = "functional"
            elif "非機能要件" in title:
                section = "nonfunctional"
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
            # ユーザーストーリー
            m = re.match(r"\*\*ユーザーストーリー\*\*:\s*(.+)", stripped)
            if m:
                val = m.group(1).strip()
                current_req["user_story"] = val
                current_req["description"] = val
                continue

            # MoSCoW
            m = re.match(r"\*\*MoSCoW\*\*:\s*(.+)", stripped, re.IGNORECASE)
            if m:
                current_req["moscow"] = m.group(1).strip().lower()
                continue

            # ペルソナ
            m = re.match(r"\*\*ペルソナ\*\*:\s*(.+)", stripped)
            if m:
                current_req["persona"] = m.group(1).strip()
                continue

            # 受け入れ条件テーブル開始
            if re.match(r"\*\*受け入れ条件\*\*", stripped):
                in_ac_table = True
                continue

            # 受け入れ条件テーブル行
            if in_ac_table and stripped.startswith("|"):
                cells = _split_table_row(stripped)
                if _is_table_separator(cells):
                    continue
                # ヘッダー行スキップ（1列目が "#" または "No" など）
                if cells and cells[0].strip() in ("#", "No", "no", ""):
                    continue
                # データ行: | 番号 | Given | When | Then |
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

            # テーブル外の非空行でテーブル終了
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


def load_requirements(path: Path) -> dict:
    """requirements.md または requirements.json を読み込んで dict を返す。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        return parse_requirements_md(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON パースエラー: {e}") from e


def find_requirements_file(cwd: Path) -> Path | None:
    """requirements.md → requirements.json の優先順で探す。"""
    for name in ("requirements.md", "requirements.json"):
        p = cwd / name
        if p.exists():
            return p
    return None


# ─── バリデーション関数 ───────────────────────────────────────

def validate(data: dict, strict: bool) -> list[str]:
    """requirements dict をバリデーションし、エラー/警告メッセージのリストを返す。"""
    errors: list[str] = []
    warnings: list[str] = []

    # トップレベル必須フィールド
    for field in ("goal", "functional_requirements", "non_functional_requirements", "scope"):
        if field not in data:
            errors.append(f"[MISSING] 必須フィールド '{field}' がありません")

    # goal
    goal = data.get("goal", "")
    if isinstance(goal, str) and not goal.strip():
        errors.append("[EMPTY] 'goal' が空文字です")

    # functional_requirements
    func_reqs = data.get("functional_requirements", [])
    if not isinstance(func_reqs, list):
        errors.append("[TYPE] 'functional_requirements' は配列でなければなりません")
    else:
        if len(func_reqs) == 0:
            errors.append("[EMPTY] 'functional_requirements' に要件が1件もありません")
        seen_ids: set[str] = set()
        for i, req in enumerate(func_reqs):
            prefix = f"functional_requirements[{i}]"
            if not isinstance(req, dict):
                errors.append(f"[TYPE] {prefix} はオブジェクトでなければなりません")
                continue
            for field in ("id", "name", "description"):
                if field not in req:
                    errors.append(f"[MISSING] {prefix}.{field} がありません")
                elif not str(req[field]).strip():
                    errors.append(f"[EMPTY] {prefix}.{field} が空文字です")
            req_id = req.get("id", "")
            if req_id and not FUNC_ID_PATTERN.match(str(req_id)):
                errors.append(
                    f"[FORMAT] {prefix}.id='{req_id}' は 'F-01' 形式でなければなりません"
                )
            if req_id in seen_ids:
                errors.append(f"[DUPLICATE] {prefix}.id='{req_id}' が重複しています")
            seen_ids.add(req_id)
            moscow = req.get("moscow")
            if moscow is not None and moscow not in VALID_MOSCOW:
                errors.append(
                    f"[VALUE] {prefix}.moscow='{moscow}' は "
                    f"{VALID_MOSCOW} のいずれかでなければなりません"
                )
            criteria = req.get("acceptance_criteria", [])
            if not isinstance(criteria, list):
                errors.append(f"[TYPE] {prefix}.acceptance_criteria は配列でなければなりません")
            else:
                if strict and len(criteria) == 0:
                    warnings.append(f"[WARN] {prefix} に受け入れ条件が定義されていません")
                for j, ac in enumerate(criteria):
                    ac_prefix = f"{prefix}.acceptance_criteria[{j}]"
                    if not isinstance(ac, dict):
                        errors.append(f"[TYPE] {ac_prefix} はオブジェクトでなければなりません")
                        continue
                    for field in ("given", "when", "then"):
                        if field not in ac:
                            errors.append(f"[MISSING] {ac_prefix}.{field} がありません")
                        elif not str(ac[field]).strip():
                            errors.append(f"[EMPTY] {ac_prefix}.{field} が空文字です")
            if strict and not req.get("user_story", "").strip():
                warnings.append(f"[WARN] {prefix}.user_story が未設定です（推奨）")

    # non_functional_requirements
    non_func_reqs = data.get("non_functional_requirements", [])
    if not isinstance(non_func_reqs, list):
        errors.append("[TYPE] 'non_functional_requirements' は配列でなければなりません")
    else:
        seen_ids = set()
        for i, req in enumerate(non_func_reqs):
            prefix = f"non_functional_requirements[{i}]"
            if not isinstance(req, dict):
                errors.append(f"[TYPE] {prefix} はオブジェクトでなければなりません")
                continue
            for field in ("id", "name", "description"):
                if field not in req:
                    errors.append(f"[MISSING] {prefix}.{field} がありません")
                elif not str(req[field]).strip():
                    errors.append(f"[EMPTY] {prefix}.{field} が空文字です")
            req_id = req.get("id", "")
            if req_id and not NON_FUNC_ID_PATTERN.match(str(req_id)):
                errors.append(
                    f"[FORMAT] {prefix}.id='{req_id}' は 'N-01' 形式でなければなりません"
                )
            if req_id in seen_ids:
                errors.append(f"[DUPLICATE] {prefix}.id='{req_id}' が重複しています")
            seen_ids.add(req_id)
            # strict: 数値目標の有無をチェック
            if strict:
                desc = req.get("description", "")
                if not re.search(r"\d", desc):
                    warnings.append(
                        f"[WARN] {prefix} の description に数値目標が含まれていません（推奨）"
                    )

    # scope
    scope = data.get("scope", {})
    if not isinstance(scope, dict):
        errors.append("[TYPE] 'scope' はオブジェクトでなければなりません")
    else:
        if "in" not in scope:
            errors.append("[MISSING] scope.in がありません")
        elif not isinstance(scope["in"], list):
            errors.append("[TYPE] scope.in は配列でなければなりません")
        if "out" not in scope:
            errors.append("[MISSING] scope.out がありません")
        elif not isinstance(scope["out"], list):
            errors.append("[TYPE] scope.out は配列でなければなりません")
        else:
            for i, item in enumerate(scope["out"]):
                if isinstance(item, dict) and "feature" not in item:
                    errors.append(f"[MISSING] scope.out[{i}].feature がありません")

    # personas（任意）
    personas = data.get("personas", [])
    if personas:
        if not isinstance(personas, list):
            errors.append("[TYPE] 'personas' は配列でなければなりません")
        else:
            seen_ids = set()
            for i, persona in enumerate(personas):
                prefix = f"personas[{i}]"
                if not isinstance(persona, dict):
                    errors.append(f"[TYPE] {prefix} はオブジェクトでなければなりません")
                    continue
                for field in ("id", "name", "description"):
                    if field not in persona:
                        errors.append(f"[MISSING] {prefix}.{field} がありません")
                p_id = persona.get("id", "")
                if p_id and not VALID_PERSONA_ID_PATTERN.match(str(p_id)):
                    errors.append(
                        f"[FORMAT] {prefix}.id='{p_id}' は 'P-01' 形式でなければなりません"
                    )
                if p_id in seen_ids:
                    errors.append(f"[DUPLICATE] {prefix}.id='{p_id}' が重複しています")
                seen_ids.add(p_id)
            if strict:
                defined_ids = {p.get("id") for p in personas if isinstance(p, dict)}
                for req in func_reqs:
                    if isinstance(req, dict):
                        ref_id = req.get("persona")
                        if ref_id and ref_id not in defined_ids:
                            warnings.append(
                                f"[WARN] functional_requirements id='{req.get('id')}' の "
                                f"persona='{ref_id}' が personas に定義されていません"
                            )

    return errors + (warnings if strict else [])


# ─── エントリポイント ──────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="requirements.md / requirements.json のバリデーション"
    )
    parser.add_argument(
        "--file",
        default=None,
        help="バリデーション対象のファイルパス（デフォルト: requirements.md を自動検出）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="オプションフィールドの未設定も警告として報告する",
    )
    args = parser.parse_args()

    cwd = Path(".")
    if args.file:
        path = Path(args.file)
    else:
        path = find_requirements_file(cwd)
        if path is None:
            print(
                "❌ requirements.md / requirements.json が見つかりません",
                file=sys.stderr,
            )
            return 2

    if not path.exists():
        print(f"❌ ファイルが見つかりません: {path}", file=sys.stderr)
        return 2

    print(f"📄 バリデーション対象: {path}")

    try:
        data = load_requirements(path)
    except ValueError as e:
        print(f"❌ パースエラー: {e}", file=sys.stderr)
        return 2

    issues = validate(data, strict=args.strict)
    errors = [m for m in issues if not m.startswith("[WARN]")]
    warnings = [m for m in issues if m.startswith("[WARN]")]

    if warnings:
        print("⚠️  警告:")
        for w in warnings:
            print(f"  {w}")

    if errors:
        print(f"\n❌ バリデーション失敗 — {len(errors)} 件のエラー:")
        for e in errors:
            print(f"  {e}")
        return 1

    func_count = len(data.get("functional_requirements", []))
    non_func_count = len(data.get("non_functional_requirements", []))
    print(
        f"✅ バリデーション通過 — "
        f"機能要件 {func_count} 件 / 非機能要件 {non_func_count} 件"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
