#!/usr/bin/env python3
"""
next_state.py — ハイブリッドインライン実行用 遷移計算スクリプト

会話内インライン実行時に、エージェントLLMが評価した条件結果を受け取り、
YAMLのtransitions定義に従って遷移先を確定する。
状態遷移はこのスクリプトが決定論的に処理し、エージェントが勝手に選ばない。

使い方:
  python scripts/next_state.py workflow.yaml --state classify --evals '{"0": true, "1": false}'

出力:
  次のstate_id           (マッチしたトランジションがある場合)
  NONE                   (一致するトランジションがない場合)
  TERMINAL               (現在のステートが終端ステートの場合)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# スクリプト単体実行 / スキルルートからの両方に対応
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.engine import load_workflow


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ハイブリッドインライン実行用: 条件評価結果から遷移先を確定する"
    )
    parser.add_argument("workflow", help="ワークフロー YAML ファイルのパス")
    parser.add_argument(
        "--state", required=True,
        help="現在のステートID"
    )
    parser.add_argument(
        "--evals",
        default=None,
        help='条件評価結果 JSON: {"0": true, "1": false, ...}'
        ' (現在ステートからの候補トランジションをpriority順で0始まりにインデックス)'
    )
    parser.add_argument(
        "--list-conditions", action="store_true",
        help="評価すべき条件リストを表示して終了（エージェントがeval前に呼ぶ用途）"
    )
    args = parser.parse_args()

    wf = load_workflow(args.workflow)

    state = wf.states.get(args.state)
    if state is None:
        print(f"ERROR: ステート '{args.state}' が見つかりません", file=sys.stderr)
        sys.exit(1)

    if state.terminal:
        print("TERMINAL")
        return

    # 現在のステートからの候補トランジション（priority 昇順）
    candidates = [
        t for t in wf.transitions
        if t.from_state == args.state or t.from_state == "*"
    ]
    candidates.sort(key=lambda t: t.priority)

    if not candidates:
        print("NONE")
        return

    # --list-conditions: エージェントが評価すべき条件を出力
    if args.list_conditions:
        result = {
            "state": args.state,
            "conditions": [
                {
                    "index": idx,
                    "to": t.to_state,
                    "priority": t.priority,
                    "condition": t.condition,
                    "description": t.description or "",
                }
                for idx, t in enumerate(candidates)
            ],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # --list-conditions なしで --evals も未指定はエラー
    if not args.list_conditions and args.evals is None:
        print("ERROR: --evals は --list-conditions なしの場合は必須です", file=sys.stderr)
        sys.exit(1)

    # 評価結果をパース
    try:
        evals: dict = json.loads(args.evals)
    except json.JSONDecodeError as e:
        print(f"ERROR: --evals の JSON が不正です: {e}", file=sys.stderr)
        sys.exit(1)

    # priority 順で最初に true となるトランジションの遷移先を返す
    for idx, transition in enumerate(candidates):
        matched = evals.get(str(idx))
        if matched is True:
            print(transition.to_state)
            return

    print("NONE")


if __name__ == "__main__":
    main()
