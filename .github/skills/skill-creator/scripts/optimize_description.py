#!/usr/bin/env python3
"""スキルdescriptionの最適化スクリプト。

eval_trigger.py の結果をもとに description を改善する。
環境に応じて2つのモードで動作する:

  [自動モード] claude -p が使える環境（Claude Code）:
    Claude に description の改善案を自動生成させてイテレーションを繰り返す。
    Anthropics の improve_description.py + run_loop.py に相当。

  [手動支援モード] claude -p が使えない環境（Copilot / Kiro）:
    改善のためのプロンプトをテキストとして出力する。
    エージェント（Copilot / Kiro）がそのプロンプトに従って改善案を生成する。

使い方:
    # eval set JSON で評価してから最適化（Claude Code）
    python optimize_description.py \\
        --skill-path <SKILLS_BASE>/<skill-name> \\
        --eval-set eval.json \\
        --max-iterations 5 --verbose

    # Copilot / Kiro 向け（改善プロンプトを出力）
    python optimize_description.py \\
        --skill-path <SKILLS_BASE>/<skill-name> \\
        --eval-set eval.json \\
        --prompt-only

    # 環境確認
    python optimize_description.py --check-env
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
from utils import parse_skill_md

try:
    from eval_trigger import run_eval, _load_all_skills, _find_project_root, _has_claude_cli
except ImportError:
    print("エラー: eval_trigger.py が見つかりません。同じ scripts/ ディレクトリに配置してください。",
          file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# 環境チェック
# ---------------------------------------------------------------------------

def _has_claude_cli_checked() -> bool:
    return shutil.which("claude") is not None


# ---------------------------------------------------------------------------
# description 改善プロンプトの生成
# ---------------------------------------------------------------------------

IMPROVE_PROMPT = """\
あなたはAIエージェントのスキルの description を最適化する専門家です。

スキル名: {skill_name}

現在の description:
"{current_description}"

現在のスコア: {score}（eval set に対するトリガー精度）

{failures_section}

{history_section}

スキルの内容（参考）:
<skill_content>
{skill_content}
</skill_content>

失敗ケースを分析して、より良い description を提案してください。

注意点:
- 特定クエリへの過学習は避け、ユーザーの意図の「カテゴリ」から一般化する
- 100〜200語程度（絶対最大: 1024文字）
- 命令形で書く（「このスキルを使う」「〜の場合に使用する」）
- 発動すべき具体的なフレーズを「〜して」「〜を...して」の形で含めると効果的
- 前の試みと構造的に異なるアプローチを試みる

新しい description のみを <new_description> タグで囲んで返してください。"""


def _build_improve_prompt(
    skill_name: str,
    skill_content: str,
    current_description: str,
    eval_results: dict,
    history: list[dict],
) -> str:
    """改善プロンプトを構築する。"""
    summary = eval_results["summary"]
    score = f"{summary['passed']}/{summary['total']}"

    failed_triggers = [r for r in eval_results["results"] if r["should_trigger"] and not r["pass"]]
    false_triggers = [r for r in eval_results["results"] if not r["should_trigger"] and not r["pass"]]

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

    history_lines = []
    if history:
        history_lines.append("過去の試み（同じ description は避けてください）:")
        for h in history[-5:]:
            history_lines.append(f'  [{h["passed"]}/{h["total"]}] "{h["description"]}"')
    history_section = "\n".join(history_lines) if history_lines else ""

    return IMPROVE_PROMPT.format(
        skill_name=skill_name,
        current_description=current_description,
        score=score,
        failures_section=failures_section,
        history_section=history_section,
        skill_content=skill_content[:3000],
    )


# ---------------------------------------------------------------------------
# 自動モード: claude -p で description を改善（Claude Code 専用）
# ---------------------------------------------------------------------------

def _call_claude_for_description(prompt: str, model: str | None) -> str:
    """claude -p を使って description 改善案を生成する。"""
    cmd = ["claude", "-p", "--output-format", "text"]
    if model:
        cmd.extend(["--model", model])

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p が失敗しました: {result.stderr}")
    return result.stdout


def _parse_new_description(text: str, original_prompt: str, model: str | None) -> str:
    """応答から <new_description> タグ内のテキストを取り出す。"""
    match = re.search(r"<new_description>(.*?)</new_description>", text, re.DOTALL)
    description = match.group(1).strip().strip('"') if match else text.strip().strip('"')

    # 1024文字超過の場合は再リクエスト
    if len(description) > 1024:
        shorten_prompt = (
            f"{original_prompt}\n\n---\n\n"
            f"前の応答で生成された description が {len(description)} 文字と長すぎました:\n\n"
            f'"{description}"\n\n'
            f"1024文字以内で書き直してください。<new_description> タグで囲んで返してください。"
        )
        text2 = _call_claude_for_description(shorten_prompt, model)
        match2 = re.search(r"<new_description>(.*?)</new_description>", text2, re.DOTALL)
        description = match2.group(1).strip().strip('"') if match2 else description[:1024]

    return description


# ---------------------------------------------------------------------------
# train/test 分割
# ---------------------------------------------------------------------------

def _split_eval_set(
    eval_set: list[dict], holdout: float, seed: int = 42
) -> tuple[list[dict], list[dict]]:
    random.seed(seed)
    trigger = [e for e in eval_set if e["should_trigger"]]
    no_trigger = [e for e in eval_set if not e["should_trigger"]]
    random.shuffle(trigger)
    random.shuffle(no_trigger)
    n_trig_test = max(1, int(len(trigger) * holdout))
    n_notrig_test = max(1, int(len(no_trigger) * holdout))
    test = trigger[:n_trig_test] + no_trigger[:n_notrig_test]
    train = trigger[n_trig_test:] + no_trigger[n_notrig_test:]
    return train, test


# ---------------------------------------------------------------------------
# 最適化ループ（自動モード）
# ---------------------------------------------------------------------------

def run_optimize_loop(
    eval_set: list[dict],
    skill_path: Path,
    model: str | None,
    max_iterations: int,
    holdout: float,
    num_workers: int,
    verbose: bool,
) -> dict:
    """claude -p を使って description を自動最適化するループ。"""
    name, original_description, content = parse_skill_md(skill_path)
    current_description = original_description
    all_skills = _load_all_skills(skill_path)
    project_root = _find_project_root()

    if holdout > 0 and len(eval_set) >= 4:
        train_set, test_set = _split_eval_set(eval_set, holdout)
    else:
        train_set, test_set = eval_set, []

    if verbose:
        print(f"スキル: {name}", file=sys.stderr)
        print(f"train: {len(train_set)}件 / test: {len(test_set)}件", file=sys.stderr)
        print(f"最大イテレーション: {max_iterations}", file=sys.stderr)

    history: list[dict] = []

    for iteration in range(1, max_iterations + 1):
        if verbose:
            print(f"\n{'='*50}", file=sys.stderr)
            print(f"イテレーション {iteration}/{max_iterations}", file=sys.stderr)

        all_queries = train_set + test_set
        eval_results = run_eval(
            eval_set=all_queries,
            skill_name=name,
            skill_description=current_description,
            all_skills=all_skills,
            use_claude_cli=True,
            project_root=project_root,
            model=model,
            num_workers=num_workers,
            verbose=verbose,
        )

        train_q_set = {q["query"] for q in train_set}
        train_list = [r for r in eval_results["results"] if r["query"] in train_q_set]
        test_list = [r for r in eval_results["results"] if r["query"] not in train_q_set]

        train_passed = sum(1 for r in train_list if r["pass"])
        test_passed = sum(1 for r in test_list if r["pass"]) if test_list else None

        train_eval = {
            "results": train_list,
            "summary": {
                "passed": train_passed,
                "failed": len(train_list) - train_passed,
                "total": len(train_list),
            },
        }

        if verbose:
            print(
                f"train: {train_passed}/{len(train_list)} PASS"
                + (f"  test: {test_passed}/{len(test_list)} PASS" if test_list else ""),
                file=sys.stderr,
            )

        history.append({
            "iteration": iteration,
            "description": current_description,
            "passed": train_passed,
            "failed": len(train_list) - train_passed,
            "total": len(train_list),
            "test_passed": test_passed,
            "test_total": len(test_list) if test_list else None,
        })

        if train_passed == len(train_list):
            if verbose:
                print(f"\n全 train クエリが PASS しました（イテレーション {iteration}）", file=sys.stderr)
            break

        if iteration == max_iterations:
            if verbose:
                print(f"\n最大イテレーション数に達しました（{max_iterations}）", file=sys.stderr)
            break

        # description 改善
        if verbose:
            print("description を改善中（claude -p）...", file=sys.stderr)

        prompt = _build_improve_prompt(name, content, current_description, train_eval, history)
        try:
            text = _call_claude_for_description(prompt, model)
            current_description = _parse_new_description(text, prompt, model)
            if verbose:
                print(f"改善後: {current_description}", file=sys.stderr)
        except Exception as e:
            print(f"警告: description 改善に失敗しました: {e}", file=sys.stderr)
            break

    # ベストを選択（test スコア優先）
    if test_set:
        best = max(history, key=lambda h: (h.get("test_passed") or 0, h["passed"]))
        best_score = f"{best.get('test_passed', '?')}/{best.get('test_total', '?')}"
    else:
        best = max(history, key=lambda h: h["passed"])
        best_score = f"{best['passed']}/{best['total']}"

    if verbose:
        print(f"\nベスト: iter={best['iteration']} score={best_score}", file=sys.stderr)

    return {
        "skill_name": name,
        "original_description": original_description,
        "best_description": best["description"],
        "best_score": best_score,
        "iterations_run": len(history),
        "mode": "claude-cli",
        "history": history,
    }


# ---------------------------------------------------------------------------
# 手動支援モード: Copilot / Kiro 向け
# ---------------------------------------------------------------------------

def generate_manual_prompt(
    eval_set: list[dict],
    skill_path: Path,
    use_heuristic_for_eval: bool,
    num_workers: int,
    verbose: bool,
) -> None:
    """Copilot / Kiro 向け: 改善プロンプトをテキスト出力する。"""
    name, description, content = parse_skill_md(skill_path)
    all_skills = _load_all_skills(skill_path)

    print("=" * 60)
    print("Copilot / Kiro 向け: description 最適化プロンプト")
    print("以下をコピーしてエージェントに送信してください。")
    print("=" * 60)
    print()

    # 評価結果を取得（ヒューリスティクスモード）
    eval_results = run_eval(
        eval_set=eval_set,
        skill_name=name,
        skill_description=description,
        all_skills=all_skills,
        use_claude_cli=False,
        num_workers=num_workers,
        verbose=verbose,
    )

    summary = eval_results["summary"]
    print(f"【現在のスコア】 {summary['passed']}/{summary['total']} PASS（ヒューリスティクス評価）")
    print()

    prompt = _build_improve_prompt(
        skill_name=name,
        skill_content=content,
        current_description=description,
        eval_results=eval_results,
        history=[],
    )
    print(prompt)
    print()
    print("=" * 60)
    print("改善案が得られたら、SKILL.md の description フィールドを更新してください。")
    print(f"更新後: python eval_trigger.py --skill-path {skill_path} --eval-set <eval.json> --verbose")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="スキル description の最適化"
    )
    parser.add_argument("--skill-path", default=None,
                        help="スキルディレクトリのパス")
    parser.add_argument("--eval-set", default=None,
                        help="eval set JSON ファイルのパス")
    parser.add_argument("--max-iterations", type=int, default=5,
                        help="自動モードの最大イテレーション数（デフォルト: 5）")
    parser.add_argument("--holdout", type=float, default=0.4,
                        help="テストセット割合（デフォルト: 0.4）")
    parser.add_argument("--model", default=None,
                        help="claude -p に渡すモデル名")
    parser.add_argument("--workers", type=int, default=5,
                        help="並列ワーカー数（デフォルト: 5）")
    parser.add_argument("--prompt-only", action="store_true",
                        help="Copilot/Kiro 向け: 改善プロンプトをテキスト出力して終了")
    parser.add_argument("--check-env", action="store_true",
                        help="動作環境を確認して終了")
    parser.add_argument("--verbose", action="store_true",
                        help="進捗を stderr に出力")
    args = parser.parse_args()

    has_cli = _has_claude_cli_checked()

    if args.check_env:
        print(f"claude CLI: {'✅ 利用可能（自動モード）' if has_cli else '❌ 未インストール（手動支援モード）'}")
        if not has_cli:
            print("\n自動最適化には Claude Code が必要です。")
            print("Copilot / Kiro 環境では --prompt-only オプションを使ってください。")
        return

    if not args.skill_path:
        parser.error("--skill-path を指定してください")
    if not args.eval_set:
        parser.error("--eval-set を指定してください")

    skill_path = Path(args.skill_path)
    if not (skill_path / "SKILL.md").exists():
        print(f"エラー: SKILL.md が見つかりません: {skill_path}", file=sys.stderr)
        sys.exit(1)

    eval_set = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    if len(eval_set) < 2:
        print("エラー: eval set は最低2件必要です", file=sys.stderr)
        sys.exit(1)

    # Copilot / Kiro 向け: プロンプト出力モード
    if args.prompt_only or not has_cli:
        if not has_cli and not args.prompt_only:
            print("情報: claude が見つかりません。手動支援モードで動作します。", file=sys.stderr)
            print("      --prompt-only フラグを明示するか、Claude Code をインストールしてください。",
                  file=sys.stderr)
            print()
        generate_manual_prompt(
            eval_set=eval_set,
            skill_path=skill_path,
            use_heuristic_for_eval=True,
            num_workers=args.workers,
            verbose=args.verbose,
        )
        return

    # Claude Code 向け: 自動最適化ループ
    output = run_optimize_loop(
        eval_set=eval_set,
        skill_path=skill_path,
        model=args.model,
        max_iterations=args.max_iterations,
        holdout=args.holdout,
        num_workers=args.workers,
        verbose=args.verbose,
    )

    print(json.dumps(output, ensure_ascii=False, indent=2))

    if args.verbose:
        print(f"\nベスト description:\n{output['best_description']}", file=sys.stderr)
        print("\n上記を SKILL.md の description に反映してください。", file=sys.stderr)


if __name__ == "__main__":
    main()
