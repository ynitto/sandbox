#!/usr/bin/env python3
"""スキルdescriptionの自動最適化スクリプト。

Anthropic API を使って description を反復的に改善する。
claude -p（Claude Code CLI）不要。Claude Code / Copilot / Kiro すべてで動作。

Anthropics skill-creator の run_loop.py + improve_description.py に相当。

必要な環境変数:
    ANTHROPIC_API_KEY: Anthropic API キー

使い方:
    python optimize_description.py \\
        --skill-path <SKILLS_BASE>/<skill-name> \\
        --eval-set eval.json \\
        --max-iterations 5 \\
        --verbose

eval set JSON 形式:
    [
      {"query": "スキルを作って", "should_trigger": true},
      {"query": "バグを直して", "should_trigger": false}
    ]

出力 JSON 形式（最終的に stdout に出力）:
    {
      "best_description": "...",
      "best_score": "18/20",
      "iterations_run": 3,
      "history": [...]
    }

出力された best_description を SKILL.md の description に反映する:
    python optimize_description.py ... | python -c "
    import json, sys
    result = json.load(sys.stdin)
    print(result['best_description'])
    "
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
from utils import parse_skill_md

try:
    from eval_trigger import run_eval, load_all_skills
except ImportError:
    print(
        "エラー: eval_trigger.py が見つかりません。同じ scripts/ ディレクトリに配置してください。",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Anthropic API 呼び出し（テキスト生成用）
# ---------------------------------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_EVAL_MODEL = "claude-haiku-4-5-20251001"   # 評価用（高速・低コスト）
DEFAULT_IMPROVE_MODEL = "claude-sonnet-4-6"         # 改善提案用（高品質）


def _call_anthropic_text(
    prompt: str,
    model: str,
    api_key: str,
    max_tokens: int = 512,
) -> str:
    """Anthropic Messages API を呼び出してテキストを返す（標準ライブラリのみ使用）。"""
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic API エラー {e.code}: {detail}") from e

    for block in body.get("content", []):
        if block.get("type") == "text":
            return block["text"].strip()
    return ""


# ---------------------------------------------------------------------------
# description 改善（improve_description.py 相当）
# ---------------------------------------------------------------------------

IMPROVE_PROMPT_TEMPLATE = """\
あなたはAIエージェントのスキルの description を最適化する専門家です。

スキル名: {skill_name}
現在の description:
"{current_description}"

現在のスコア: {score}

{failures_section}

{history_section}

スキルの内容（参考）:
<skill_content>
{skill_content}
</skill_content>

失敗ケースを分析して、より良い description を提案してください。
注意点:
- 特定のクエリへの過学習は避け、ユーザーの意図の**カテゴリ**から一般化してください
- 100〜200語程度（絶対最大: 1024文字）
- 命令形で書いてください（「このスキルを使う」「〜の場合に使用する」）
- 具体的な発動フレーズを含めると効果的です
- 前の試みと構造的に異なるアプローチを試みてください

新しい description のみを <new_description> タグで囲んで返してください。余計な説明は不要です。"""


def improve_description(
    skill_name: str,
    skill_content: str,
    current_description: str,
    eval_results: dict,
    history: list[dict],
    api_key: str,
    model: str,
) -> str:
    """eval 結果をもとに description を改善する。"""
    failed_triggers = [
        r for r in eval_results["results"]
        if r["should_trigger"] and not r["pass"]
    ]
    false_triggers = [
        r for r in eval_results["results"]
        if not r["should_trigger"] and not r["pass"]
    ]

    score = f"{eval_results['summary']['passed']}/{eval_results['summary']['total']}"

    # 失敗ケースのセクション
    failures_lines = []
    if failed_triggers:
        failures_lines.append("発動すべきなのに発動しなかったケース（false negative）:")
        for r in failed_triggers:
            failures_lines.append(f'  - "{r["query"]}"')
    if false_triggers:
        failures_lines.append("発動すべきでないのに発動したケース（false positive）:")
        for r in false_triggers:
            failures_lines.append(f'  - "{r["query"]}"')
    failures_section = "\n".join(failures_lines) if failures_lines else "（失敗なし）"

    # 履歴セクション
    history_lines = []
    if history:
        history_lines.append("過去の試み（これらと同じ description は避けてください）:")
        for h in history[-5:]:  # 直近5件のみ
            history_lines.append(
                f'  [{h["passed"]}/{h["total"]}] "{h["description"]}"'
            )
    history_section = "\n".join(history_lines) if history_lines else ""

    prompt = IMPROVE_PROMPT_TEMPLATE.format(
        skill_name=skill_name,
        current_description=current_description,
        score=score,
        failures_section=failures_section,
        history_section=history_section,
        skill_content=skill_content[:3000],  # 長すぎる場合は切り詰め
    )

    text = _call_anthropic_text(prompt, model, api_key, max_tokens=512)
    match = re.search(r"<new_description>(.*?)</new_description>", text, re.DOTALL)
    description = match.group(1).strip().strip('"') if match else text.strip().strip('"')

    # 1024文字超えた場合は再リクエスト
    if len(description) > 1024:
        shorten_prompt = (
            f"{prompt}\n\n---\n\n"
            f"前の応答で生成された description が {len(description)} 文字と長すぎました:\n\n"
            f'"{description}"\n\n'
            f"1024文字以内に収めて、重要なトリガーワードと意図のカバレッジを保ちながら"
            f"書き直してください。<new_description> タグで囲んで返してください。"
        )
        text2 = _call_anthropic_text(shorten_prompt, model, api_key, max_tokens=512)
        match2 = re.search(r"<new_description>(.*?)</new_description>", text2, re.DOTALL)
        description = match2.group(1).strip().strip('"') if match2 else description[:1024]

    return description


# ---------------------------------------------------------------------------
# train/test 分割
# ---------------------------------------------------------------------------

def split_eval_set(
    eval_set: list[dict], holdout: float, seed: int = 42
) -> tuple[list[dict], list[dict]]:
    """should_trigger で層化サンプリングして train/test に分割する。"""
    random.seed(seed)
    trigger = [e for e in eval_set if e["should_trigger"]]
    no_trigger = [e for e in eval_set if not e["should_trigger"]]
    random.shuffle(trigger)
    random.shuffle(no_trigger)

    n_trigger_test = max(1, int(len(trigger) * holdout))
    n_no_trigger_test = max(1, int(len(no_trigger) * holdout))

    test_set = trigger[:n_trigger_test] + no_trigger[:n_no_trigger_test]
    train_set = trigger[n_trigger_test:] + no_trigger[n_no_trigger_test:]
    return train_set, test_set


# ---------------------------------------------------------------------------
# メインループ
# ---------------------------------------------------------------------------

def run_optimize_loop(
    eval_set: list[dict],
    skill_path: Path,
    api_key: str,
    eval_model: str,
    improve_model: str,
    max_iterations: int,
    holdout: float,
    num_workers: int,
    verbose: bool,
) -> dict:
    """eval + improve を繰り返してベストな description を返す。"""
    name, original_description, content = parse_skill_md(skill_path)
    current_description = original_description
    all_skills = load_all_skills(skill_path)

    # train/test 分割
    if holdout > 0 and len(eval_set) >= 4:
        train_set, test_set = split_eval_set(eval_set, holdout)
    else:
        train_set = eval_set
        test_set = []

    if verbose:
        print(f"スキル: {name}", file=sys.stderr)
        print(f"train: {len(train_set)}件 / test: {len(test_set)}件", file=sys.stderr)
        print(f"最大イテレーション: {max_iterations}", file=sys.stderr)

    history: list[dict] = []

    for iteration in range(1, max_iterations + 1):
        if verbose:
            print(f"\n{'='*50}", file=sys.stderr)
            print(f"イテレーション {iteration}/{max_iterations}", file=sys.stderr)
            print(f"description: {current_description}", file=sys.stderr)

        # train + test を一括評価
        all_queries = train_set + (test_set if test_set else [])
        eval_results = run_eval(
            eval_set=all_queries,
            skill_name=name,
            all_skills=all_skills,
            api_key=api_key,
            model=eval_model,
            num_workers=num_workers,
            verbose=verbose,
        )

        # train/test を分離
        train_queries = {q["query"] for q in train_set}
        train_results_list = [r for r in eval_results["results"] if r["query"] in train_queries]
        test_results_list = [r for r in eval_results["results"] if r["query"] not in train_queries]

        train_passed = sum(1 for r in train_results_list if r["pass"])
        test_passed = sum(1 for r in test_results_list if r["pass"]) if test_results_list else None

        train_eval = {
            "results": train_results_list,
            "summary": {
                "passed": train_passed,
                "failed": len(train_results_list) - train_passed,
                "total": len(train_results_list),
            },
        }

        if verbose:
            print(
                f"train: {train_passed}/{len(train_results_list)} PASS"
                + (f"  test: {test_passed}/{len(test_results_list)} PASS" if test_results_list else ""),
                file=sys.stderr,
            )

        history.append({
            "iteration": iteration,
            "description": current_description,
            "passed": train_passed,
            "failed": len(train_results_list) - train_passed,
            "total": len(train_results_list),
            "test_passed": test_passed,
            "test_total": len(test_results_list) if test_results_list else None,
        })

        # 全件 PASS なら終了
        if train_passed == len(train_results_list):
            if verbose:
                print(f"\n全 train クエリが PASS しました（イテレーション {iteration}）", file=sys.stderr)
            break

        if iteration == max_iterations:
            if verbose:
                print(f"\n最大イテレーション数に達しました（{max_iterations}）", file=sys.stderr)
            break

        # description を改善
        if verbose:
            print("description を改善中...", file=sys.stderr)

        current_description = improve_description(
            skill_name=name,
            skill_content=content,
            current_description=current_description,
            eval_results=train_eval,
            history=history,
            api_key=api_key,
            model=improve_model,
        )

        if verbose:
            print(f"改善後: {current_description}", file=sys.stderr)

    # ベストな description を選択（test スコア優先、なければ train スコア）
    if test_set:
        best = max(
            history,
            key=lambda h: (h.get("test_passed") or 0, h["passed"]),
        )
        best_score = f"{best.get('test_passed', '?')}/{best.get('test_total', '?')}"
    else:
        best = max(history, key=lambda h: h["passed"])
        best_score = f"{best['passed']}/{best['total']}"

    if verbose:
        print(f"\nベスト: イテレーション {best['iteration']} スコア={best_score}", file=sys.stderr)
        print(f"ベスト description: {best['description']}", file=sys.stderr)

    return {
        "skill_name": name,
        "original_description": original_description,
        "best_description": best["description"],
        "best_score": best_score,
        "iterations_run": len(history),
        "holdout": holdout,
        "train_size": len(train_set),
        "test_size": len(test_set),
        "history": history,
    }


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="スキル description の自動最適化（Anthropic API使用）"
    )
    parser.add_argument(
        "--skill-path",
        required=True,
        help="スキルディレクトリのパス",
    )
    parser.add_argument(
        "--eval-set",
        required=True,
        help="eval set JSON ファイルのパス",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=5,
        help="最大イテレーション数（デフォルト: 5）",
    )
    parser.add_argument(
        "--holdout",
        type=float,
        default=0.4,
        help="テストセットの割合（デフォルト: 0.4、0で無効）",
    )
    parser.add_argument(
        "--eval-model",
        default=DEFAULT_EVAL_MODEL,
        help=f"評価用モデル（デフォルト: {DEFAULT_EVAL_MODEL}）",
    )
    parser.add_argument(
        "--improve-model",
        default=DEFAULT_IMPROVE_MODEL,
        help=f"改善提案用モデル（デフォルト: {DEFAULT_IMPROVE_MODEL}）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="並列ワーカー数（デフォルト: 5）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="進捗を stderr に出力",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("エラー: ANTHROPIC_API_KEY 環境変数が未設定です", file=sys.stderr)
        sys.exit(1)

    skill_path = Path(args.skill_path)
    if not (skill_path / "SKILL.md").exists():
        print(f"エラー: SKILL.md が見つかりません: {skill_path}", file=sys.stderr)
        sys.exit(1)

    eval_set = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    if len(eval_set) < 2:
        print("エラー: eval set は最低2件必要です", file=sys.stderr)
        sys.exit(1)

    output = run_optimize_loop(
        eval_set=eval_set,
        skill_path=skill_path,
        api_key=api_key,
        eval_model=args.eval_model,
        improve_model=args.improve_model,
        max_iterations=args.max_iterations,
        holdout=args.holdout,
        num_workers=args.workers,
        verbose=args.verbose,
    )

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
