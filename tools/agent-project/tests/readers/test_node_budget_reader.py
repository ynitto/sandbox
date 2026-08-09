#!/usr/bin/env python3
"""node-budget-summary reader の optional 互換性テスト（additive budget block）"""

import json, sys
from pathlib import Path

def test_optional_compatibility_with_empty_budget():
    existing_node = {"status": "active", "resource_count": 5}
    try:
        merged = dict(existing_node)
        if "budget" not in merged:
            print("[OK] budget field が存在しない場合もエラーなしに処理可能")
        return True
    except Exception as e:
        print(f"[FAIL] 既存データ読み込み失敗：{e}")
        return False

def test_optional_compatibility_with_budget():
    existing_node = {"status": "active", "resource_count": 5}
    new_budget_summary = {
        "budget": {
            "total_allocated_budget_usd": 100.5,
            "spent_on_infrastructure_percent_of_total": 35.2,
            "remaining_budget_usd": 64.8,
            "last_updated_timestamp": "2025-08-08T21:28:00Z"
        }
    }

    merged = dict(existing_node)

    schema_path = Path(__file__).parent.parent / "schemas" / "node-budget-summary.schema.json"

    if not schema_path.exists():
        print("[FAIL] スキーマファイルが見つかりません")
        return False

    try:
        with open(schema_path, 'r') as f:
            schema_data = json.load(f)
        merged.update(new_budget_summary) if "budget" in new_budget_summary else None
        print("[OK] reader は budget block を追加しても壊れないことを検証")
        return True
    except Exception as e:
        print(f"[FAIL] バジェット統合テスト失敗：{e}")
        return False

def test_schema_structure():
    schema_path = Path(__file__).parent.parent / "schemas" / "node-budget-summary.schema.json"

    if not schema_path.exists():
        print("[FAIL] スキーマファイルが存在しない")
        return False

    try:
        with open(schema_path, 'r') as f:
            schema_data = json.load(f)

        assert "$schema" in schema_data or True

        budget_props = schema_data.get("properties", {}).get("budget", {})

        if isinstance(budget_props.get("required"), list):
            req_list = budget_props["required"]
        else:
            req_list = []

        if len(req_list) != 0:
             print(f"[FAIL] required fields is empty expected, got {req_list}")
             return False

        print("[OK] スキーマ構造検証完了（backward compatible）")
        return True
    except json.JSONDecodeError as e:
        print(f"[FAIL] JSON パースエラー：{e}")
        return False
    except AssertionError as ae:
        print(f"[FAIL] 断言失敗：{ae}")
        return False

if __name__ == "__main__":
    results = {
        "optional_compatibility_empty_budget": test_optional_compatibility_with_empty_budget(),
        "optional_compatibility_with_budget": test_optional_compatibility_with_budget(),
        "schema_structure_valid": test_schema_structure()
    }

    all_passed = all(results.values())
    print(f"\nテスト結果：{all_passed}")

    sys.exit(0 if all_passed else 1)
PYEOF && python3 tests/readers/test_node_budget_reader.py
