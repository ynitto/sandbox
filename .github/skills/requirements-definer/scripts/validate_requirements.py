#!/usr/bin/env python3
"""
validate_requirements.py - requirements.json のスキーマバリデーション

requirements-definer が出力した requirements.json が正しいスキーマに準拠しているか
を検証する。外部依存ゼロ（Python 標準ライブラリのみ）。

使い方:
  # デフォルト（カレントディレクトリの requirements.json を検証）
  python validate_requirements.py

  # ファイルを指定
  python validate_requirements.py --file path/to/requirements.json

  # 警告も表示（オプションフィールドの未設定も報告）
  python validate_requirements.py --strict

終了コード:
  0 = バリデーション通過
  1 = バリデーションエラーあり
  2 = ファイルが見つからない / JSON パースエラー
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


# ─── バリデーション関数 ───────────────────────────────────────

def validate(data: dict, strict: bool) -> list[str]:
    """requirements.json をバリデーションし、エラーメッセージのリストを返す。"""
    errors: list[str] = []
    warnings: list[str] = []

    # トップレベル必須フィールド
    for field in ("goal", "functional_requirements", "non_functional_requirements", "scope"):
        if field not in data:
            errors.append(f"[MISSING] トップレベルの必須フィールド '{field}' がありません")

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
            # 必須フィールド
            for field in ("id", "name", "description"):
                if field not in req:
                    errors.append(f"[MISSING] {prefix}.{field} がありません")
                elif not str(req[field]).strip():
                    errors.append(f"[EMPTY] {prefix}.{field} が空文字です")
            # ID 形式チェック
            req_id = req.get("id", "")
            if req_id and not FUNC_ID_PATTERN.match(str(req_id)):
                errors.append(
                    f"[FORMAT] {prefix}.id='{req_id}' は 'F-01' 形式でなければなりません"
                )
            # ID 重複チェック
            if req_id in seen_ids:
                errors.append(f"[DUPLICATE] {prefix}.id='{req_id}' が重複しています")
            seen_ids.add(req_id)
            # moscow
            moscow = req.get("moscow")
            if moscow is not None and moscow not in VALID_MOSCOW:
                errors.append(
                    f"[VALUE] {prefix}.moscow='{moscow}' は "
                    f"{VALID_MOSCOW} のいずれかでなければなりません"
                )
            # acceptance_criteria
            criteria = req.get("acceptance_criteria", [])
            if not isinstance(criteria, list):
                errors.append(f"[TYPE] {prefix}.acceptance_criteria は配列でなければなりません")
            else:
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
            # strict: オプションフィールドの警告
            if strict and "user_story" not in req:
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
        # persona 参照の一貫性チェック
        if strict:
            defined_persona_ids = {
                p.get("id") for p in personas if isinstance(p, dict)
            }
            for req in func_reqs:
                if isinstance(req, dict):
                    ref_id = req.get("persona")
                    if ref_id and ref_id not in defined_persona_ids:
                        warnings.append(
                            f"[WARN] functional_requirements id='{req.get('id')}' の "
                            f"persona='{ref_id}' が personas に定義されていません"
                        )

    return errors + (warnings if strict else [])


# ─── エントリポイント ──────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="requirements.json のスキーマバリデーション"
    )
    parser.add_argument(
        "--file",
        default="requirements.json",
        help="バリデーション対象の JSON ファイルパス（デフォルト: requirements.json）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="オプションフィールドの未設定も警告として報告する",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"❌ ファイルが見つかりません: {path}", file=sys.stderr)
        return 2

    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ JSON パースエラー: {e}", file=sys.stderr)
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
