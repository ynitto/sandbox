#!/usr/bin/env python3
"""
next_state.py — ハイブリッドインライン実行用 遷移計算スクリプト

会話内インライン実行時に、エージェントLLMが評価した条件結果を受け取り、
YAMLのtransitions定義に従って遷移先を確定する。
状態遷移はこのスクリプトが決定論的に処理し、エージェントが勝手に選ばない。

condition_rule フィールドがある場合は LLM 評価が不要な条件を自動判定し、
--list-conditions の出力に auto_result を付与する。

使い方:
  python scripts/next_state.py {名前} --initial-state
  python scripts/next_state.py {名前} --state classify --list-conditions
  python scripts/next_state.py {名前} --state classify --list-conditions --last-output BUG
  python scripts/next_state.py {名前} --state classify --evals '{"1": false}'
  python scripts/next_state.py {名前} --state classify --evals '{"1": false}' --last-output BUG

出力:
  initial_state_id       (--initial-state の場合)
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

from scripts.engine import load_workflow, resolve_workflow_path, evaluate_condition_rule


def _build_ctx(last_output: str, output_pairs: list[str]) -> dict:
    """--last-output と --output KEY=VALUE からコンテキストを構築する。"""
    ctx: dict = {"last_output": last_output}
    for pair in output_pairs:
        if "=" in pair:
            key, _, value = pair.partition("=")
            ctx[key.strip()] = value
    return ctx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ハイブリッドインライン実行用: 条件評価結果から遷移先を確定する"
    )
    parser.add_argument(
        "workflow",
        help="ワークフロー YAML ファイルのパス、または .statemachine/{name} の名前"
    )
    parser.add_argument(
        "--initial-state", action="store_true",
        help="initial_state を出力して終了（実行開始時に使用）"
    )
    parser.add_argument(
        "--state", default=None,
        help="現在のステートID（--initial-state 以外では必須）"
    )
    parser.add_argument(
        "--evals",
        default=None,
        help='条件評価結果 JSON: {"0": true, "1": false, ...}'
        ' (現在ステートからの候補トランジションをpriority順で0始まりにインデックス)'
        ' condition_rule がある条件は --last-output / --output で自動評価されるため省略可'
    )
    parser.add_argument(
        "--list-conditions", action="store_true",
        help="評価すべき条件リストを表示して終了（エージェントがeval前に呼ぶ用途）"
    )
    parser.add_argument(
        "--last-output", default="", metavar="VALUE",
        help="直前のステートの出力テキスト。condition_rule の自動評価に使用"
    )
    parser.add_argument(
        "--output", action="append", default=[], metavar="KEY=VALUE",
        help="output_key で保存された値を渡す。condition_rule の自動評価に使用。繰り返し指定可"
    )
    args = parser.parse_args()

    wf = load_workflow(resolve_workflow_path(args.workflow))

    # --initial-state: 開始ステートを返して終了
    if args.initial_state:
        print(wf.initial_state)
        return

    if args.state is None:
        print("ERROR: --state は --initial-state 以外では必須です", file=sys.stderr)
        sys.exit(1)

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

    # condition_rule の自動評価用コンテキストを構築
    ctx = _build_ctx(args.last_output, args.output)

    # --list-conditions: エージェントが評価すべき条件を出力
    if args.list_conditions:
        conditions_out = []
        for idx, t in enumerate(candidates):
            entry: dict = {
                "index": idx,
                "to": t.to_state,
                "priority": t.priority,
                "condition": t.condition,
                "description": t.description or "",
            }
            if t.condition_rule:
                entry["condition_rule"] = t.condition_rule
                rule_result = evaluate_condition_rule(t.condition_rule, ctx)
                if rule_result is not None:
                    # 自動評価済み — LLM評価不要
                    entry["auto_result"] = rule_result
                    entry["needs_llm_eval"] = False
                else:
                    entry["needs_llm_eval"] = True
            else:
                entry["needs_llm_eval"] = True
            conditions_out.append(entry)

        result = {
            "state": args.state,
            "conditions": conditions_out,
            "note": (
                "needs_llm_eval: false の条件は condition_rule で自動評価済み。"
                "LLM は needs_llm_eval: true の条件のみ評価すること。"
                "--evals では auto_result を持つ条件のインデックスは省略可（自動適用される）。"
            ),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.evals is None:
        print("ERROR: --evals は --list-conditions なしの場合は必須です", file=sys.stderr)
        sys.exit(1)

    # 評価結果をパース
    try:
        evals: dict = json.loads(args.evals)
    except json.JSONDecodeError as e:
        print(f"ERROR: --evals の JSON が不正です: {e}", file=sys.stderr)
        sys.exit(1)

    # priority 順で最初に true となるトランジションの遷移先を返す
    # condition_rule がある条件は自動評価を優先し、LLM eval を上書きする
    for idx, transition in enumerate(candidates):
        # 1. condition_rule で自動評価
        rule_result = evaluate_condition_rule(transition.condition_rule, ctx)
        if rule_result is not None:
            matched = rule_result
        else:
            # 2. LLM eval にフォールバック
            matched = evals.get(str(idx))

        if matched is True:
            print(transition.to_state)
            return

    print("NONE")


if __name__ == "__main__":
    main()
