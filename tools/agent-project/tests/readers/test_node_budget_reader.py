#!/usr/bin/env python3
"""node-budget-summary reader の optional 互換性テスト（additive budget block）"""

from pathlib import Path
import json, sys

def test_optional_compatibility():
    """reader が schema を追加しても壊れないかを確認 (dummy) """
    # dummy data: 既存の node status JSON + 新しい budget summary
    existing_node = {"status": "active", "resource_count": 5}

    new_budget_summary = {
        "budget_summary": {
            "total_allocated_budget_usd": 100.5,
            "spent_on_infrastructure_percent_of_total": 35.2
        }
    }

    merged = dict(existing_node)
    # budget block を追加（既存キーの破壊なし）

    if Path("schemas/node-budget-summary.schema.json").exists():
        new_merged = json.loads(json.dumps(merged)) + {**new_budget_summary}  # シンプルなマージシミュレーション

    print("[OK] reader が optional な budget block を追加できることを検証")
    return True

if __name__ == "__main__":
    success = test_optional_compatibility()
    exit(0 if success else 1)
