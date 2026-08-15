#!/usr/bin/env python3
"""
next_state.py — ハイブリッドインライン実行用 遷移計算スクリプト

会話内インライン実行時に、エージェントLLMが評価した条件結果を受け取り、
YAMLのtransitions定義に従って遷移先を確定する。
状態遷移はこのスクリプトが決定論的に処理し、エージェントが勝手に選ばない。

condition_rule フィールドがある場合は LLM 評価が不要な条件を自動判定し、
--auto-eval の出力に auto_result を付与する。

使い方:
  python scripts/next_state.py {名前} --initial-state
  python scripts/next_state.py {名前} --state classify --auto-eval --context '{"last_output":"BUG"}'
  python scripts/next_state.py {名前} --state classify --eval '{"1": false}' --context '{"last_output":"BUG"}'
  python scripts/next_state.py {名前} --state implement --state-check

  旧ハーネス互換（非推奨。--context の不足値を補完する）:
  python scripts/next_state.py {名前} --state classify --list-conditions --last-output BUG
  python scripts/next_state.py {名前} --state classify --evals '{"1": false}' --output result=BUG

出力:
  initial_state_id       (--initial-state の場合)
  次のstate_id           (マッチしたトランジションがある場合)
  NONE                   (一致するトランジションがない場合)
  TERMINAL               (現在のステートが終端ステートの場合)

--auto-eval の JSON には resolved が付く。condition_rule だけで遷移先が確定した場合は
その state_id（全条件が偽なら "NONE"）が入り、LLM 評価は不要。

--state-check は、そのステートが宣言する決定的検査を正規化して返す（外部ハーネスが
自前で YAML を読み直さずに済むように、正規化の実装をここ 1 つに保つための口）。
検査を実行したハーネスは、その結果を次の 3 キーで --context へ載せると
condition_rule から決定的に分岐できる:

  check_status  終了コードの文字列（実行できなかった場合は "error"）
  check_ok      "true" / "false"
  check_output  診断の先頭行

これらは**ハーネスが測った事実**であって、モデルが書いたテキストではない。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

# スクリプト単体実行 / スキルルートからの両方に対応
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.engine import (
    load_workflow, render_template, resolve_workflow_path, evaluate_condition_rule,
    validate_workflow,
)


def _build_ctx(context_json: "str | None", last_output: str, output_pairs: list[str]) -> dict:
    """コンテキストを構築する。

    --context（JSON オブジェクト）が正典。旧ハーネスの --last-output / --output は
    不足値の補完としてのみ効き、同じキーが両方にあれば --context を採る。
    """
    now = _dt.datetime.now().astimezone()
    ctx: dict = {"today": now.strftime("%Y-%m-%d"),
                 "now": now.isoformat(timespec="seconds"),
                 "last_output": last_output}
    for pair in output_pairs:
        if "=" in pair:
            key, _, value = pair.partition("=")
            ctx[key.strip()] = value
    if not context_json:
        return ctx
    try:
        explicit = json.loads(context_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: --context の JSON が不正です: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(explicit, dict):
        print("ERROR: --context は JSON オブジェクトで指定してください", file=sys.stderr)
        sys.exit(1)
    return {**ctx, **explicit}


def _unconditional(transition) -> bool:
    """条件文もルールも持たないトランジション。評価する対象が無く常に成立する。"""
    return not str(transition.condition or "").strip() and not transition.condition_rule.strip()


def _resolve(conditions: list[dict]) -> "str | None":
    """condition_rule だけで遷移先が決まるなら state_id（全て偽なら "NONE"）を返す。"""
    for entry in conditions:
        if entry["needs_llm_eval"]:
            return None   # ここより先は LLM 評価なしには決められない
        if entry["auto_result"]:
            return entry["to"]
    return "NONE"


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
        "--eval", "--evals", dest="evals",
        default=None,
        help='条件評価結果 JSON: {"0": true, "1": false, ...}'
        ' (現在ステートからの候補トランジションをpriority順で0始まりにインデックス)'
        ' condition_rule がある条件は --context から自動評価されるため省略可'
    )
    parser.add_argument(
        "--auto-eval", "--list-conditions", dest="auto_eval", action="store_true",
        help="condition_rule を先に評価し、条件リストと resolved を表示して終了"
    )
    parser.add_argument(
        "--state-check", dest="state_check", action="store_true",
        help="そのステートの決定的検査（check）を正規化して JSON で表示して終了"
    )
    parser.add_argument(
        "--context", default=None, metavar="JSON",
        help='コンテキスト変数の JSON オブジェクト。'
        ' 例: \'{"last_output":"BUG","result":"PASS"}\''
        ' condition_rule 評価・条件文のテンプレート展開に使用'
    )
    parser.add_argument(
        "--last-output", default="", metavar="VALUE",
        help="[非推奨] 旧ハーネス互換。--context に last_output が無い場合のみ効く"
    )
    parser.add_argument(
        "--output", action="append", default=[], metavar="KEY=VALUE",
        help="[非推奨] 旧ハーネス互換。--context に同じキーが無い場合のみ効く。繰り返し指定可"
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

    # --state-check: 検査の宣言を正規化して返す（終端判定より前 — 宣言の照会であって遷移ではない）
    if args.state_check:
        errors = [e for e in validate_workflow(wf) if args.state in e]
        if errors:
            print("ERROR: " + "\n".join(errors), file=sys.stderr)
            sys.exit(1)
        print(json.dumps({
            "state": args.state,
            "check": state.check,
            "check_retries": state.check_retries,
            "check_on_exhausted": state.check_on_exhausted,
            "check_feedback": state.check_feedback,
        }, ensure_ascii=False, indent=2))
        return

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
    ctx = _build_ctx(args.context, args.last_output, args.output)

    # --auto-eval: condition_rule を先に評価し、LLM 評価が必要な条件を残して出力
    if args.auto_eval:
        # 最優先の候補が無条件なら評価するものが何も無い。条件リストを組まずに遷移先を返す。
        if _unconditional(candidates[0]):
            print(json.dumps({
                "state": args.state,
                "auto_advance": True,
                "next_state": candidates[0].to_state,
                "note": "無条件トランジション。条件評価も --eval も不要でそのまま遷移する。",
            }, ensure_ascii=False, indent=2))
            return

        conditions_out = []
        for idx, t in enumerate(candidates):
            entry: dict = {
                "index": idx,
                "to": t.to_state,
                "priority": t.priority,
                "condition": render_template(t.condition, ctx),
                "description": t.description or "",
                "needs_llm_eval": True,
            }
            if _unconditional(t):
                # 後続の無条件トランジションは前段が全て偽のときのフォールバック。
                entry["auto_result"] = True
                entry["needs_llm_eval"] = False
            elif t.condition_rule:
                entry["condition_rule"] = t.condition_rule
                rule_result = evaluate_condition_rule(t.condition_rule, ctx)
                if rule_result is not None:
                    # 自動評価済み — LLM評価不要
                    entry["auto_result"] = rule_result
                    entry["needs_llm_eval"] = False
            conditions_out.append(entry)

        result = {
            "state": args.state,
            "conditions": conditions_out,
            "resolved": _resolve(conditions_out),
            "note": (
                "resolved が null 以外なら遷移先は condition_rule だけで確定しており、"
                "LLM 評価も --eval も不要。"
                "null の場合は needs_llm_eval: true の条件のみ LLM で評価し、--eval に渡す"
                "（auto_result を持つ条件のインデックスは省略可 — 自動適用される）。"
            ),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.evals is None:
        print("ERROR: --eval は --auto-eval なしの場合は必須です", file=sys.stderr)
        sys.exit(1)

    # 評価結果をパース
    try:
        evals: dict = json.loads(args.evals)
    except json.JSONDecodeError as e:
        print(f"ERROR: --eval の JSON が不正です: {e}", file=sys.stderr)
        sys.exit(1)

    # priority 順で最初に true となるトランジションの遷移先を返す
    # condition_rule がある条件は自動評価を優先し、LLM eval を上書きする
    for idx, transition in enumerate(candidates):
        # 0. 無条件トランジションは評価せず成立させる（--auto-eval の auto_advance と同じ判定）
        if _unconditional(transition):
            print(transition.to_state)
            return
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
