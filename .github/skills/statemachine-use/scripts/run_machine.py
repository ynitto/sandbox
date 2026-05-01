#!/usr/bin/env python3
"""
run_machine.py  —  yaml-statemachine スキル用 CLI ランナー

使い方:
  python scripts/run_machine.py workflow.yaml
  python scripts/run_machine.py workflow.yaml --input "ログインバグを修正して" --verbose
  python scripts/run_machine.py workflow.yaml --dry-run
  python scripts/run_machine.py workflow.yaml --context key=value --context other=123
  python scripts/run_machine.py workflow.yaml --agent claude
  python scripts/run_machine.py workflow.yaml --agent copilot
  python scripts/run_machine.py workflow.yaml --agent kiro
  python scripts/run_machine.py workflow.yaml --agent anthropic --model claude-sonnet-4-20250514

LLM バックエンド:
  claude     Claude Code CLI (`claude -p`)           ← デフォルト
  copilot    GitHub Copilot CLI (`gh copilot explain`)
  kiro       Kiro CLI (`kiro -p`)
  anthropic  Anthropic Python SDK（ANTHROPIC_API_KEY 必須）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Allow running from the repo root or scripts/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.engine import StateMachineEngine, load_workflow, validate_workflow


# ─────────────────────────────────────────────
#  CLI LLM 関数ファクトリ
# ─────────────────────────────────────────────

# 各 CLI の呼び出しコマンドテンプレート
# {model_args} はモデル指定に対応している CLI のみ展開される
_CLI_CONFIGS: dict[str, dict] = {
    "claude": {
        "cmd": ["claude", "-p", "--no-markdown"],
        "model_flag": "--model",   # --model <id> を追加するフラグ名
        "stdin": True,             # プロンプトを stdin で渡す
    },
    "copilot": {
        "cmd": ["copilot", "-p"],
        "model_flag": "--model",
        "stdin": False,            # プロンプトを引数で渡す
    },
    "kiro": {
        "cmd": ["kiro-cli", "chat"],
        "model_flag": "--model",
        "stdin": False,
    },
}


async def call_cli_llm(prompt: str, cli: str, model: str | None = None) -> str:
    """指定した AI CLI を非同期サブプロセスで呼び出してレスポンスを返す。"""
    cfg = _CLI_CONFIGS[cli]
    cmd = list(cfg["cmd"])

    if cfg["model_flag"] and model:
        cmd += [cfg["model_flag"], model]

    if cfg["stdin"]:
        # プロンプトを stdin で渡す（引数長制限を回避）
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=prompt.encode())
    else:
        # プロンプトを最後の引数として渡す
        proc = await asyncio.create_subprocess_exec(
            *cmd, prompt,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode().strip()
        raise RuntimeError(
            f"CLI '{cli}' がエラーを返しました (code={proc.returncode}): {err}"
        )

    return stdout.decode().strip()


async def anthropic_llm(prompt: str, model: str = "claude-sonnet-4-20250514") -> str:
    """Anthropic Python SDK を呼び出してテキストレスポンスを返す。"""
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic パッケージがインストールされていません。実行: pip install anthropic")
        sys.exit(1)

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 環境変数を使用
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YAML定義の LLM ステートマシンを実行する"
    )
    parser.add_argument("workflow", help="ワークフロー YAML ファイルのパス")
    parser.add_argument(
        "--input", "-i", default="",
        help="マシンに渡す初期入力テキスト（タスク/プロンプト）"
    )
    parser.add_argument(
        "--context", "-c", action="append", default=[], metavar="KEY=VALUE",
        help="コンテキスト変数を設定（繰り返し指定可）。例: --context env=production"
    )
    parser.add_argument(
        "--max-steps", type=int, default=None,
        help="YAML 設定の最大ステップ数を上書きする"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="トランジションの詳細な推論を表示"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="YAML のパースと検証のみ、実行しない"
    )
    parser.add_argument(
        "--output-json", action="store_true",
        help="最終結果を JSON 形式で標準出力に出力"
    )
    parser.add_argument(
        "--agent",
        choices=["claude", "copilot", "kiro", "anthropic"],
        default="claude",
        help=(
            "使用する LLM バックエンド (デフォルト: claude)\n"
            "  claude    : Claude Code CLI `claude -p`\n"
            "  copilot   : GitHub Copilot CLI `gh copilot explain`\n"
            "  kiro      : Kiro CLI `kiro -p`\n"
            "  anthropic : Anthropic Python SDK (ANTHROPIC_API_KEY 必須)"
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "モデル ID を指定（claude / anthropic のみ有効）。"
            "例: claude-opus-4-5, claude-sonnet-4-20250514"
        ),
    )
    return parser.parse_args()


def parse_context_args(context_args: list[str]) -> dict:
    ctx = {}
    for arg in context_args:
        if "=" not in arg:
            print(f"WARNING: 不正なコンテキスト引数をスキップします (KEY=VALUE 形式が必要): {arg}")
            continue
        key, _, value = arg.partition("=")
        # Try to parse as int/float/bool/JSON, fall back to string
        for cast in (int, float, json.loads):
            try:
                value = cast(value)
                break
            except (ValueError, json.JSONDecodeError):
                pass
        ctx[key.strip()] = value
    return ctx


async def main() -> None:
    args = parse_args()
    workflow_path = Path(args.workflow)

    if not workflow_path.exists():
        print(f"ERROR: ワークフローファイルが見つかりません: {workflow_path}")
        sys.exit(1)

    # ロードと検証
    print(f"ワークフローを読み込んでいます: {workflow_path}")
    workflow = load_workflow(workflow_path)
    print(f"  名前: {workflow.name}")
    if workflow.description:
        print(f"  説明: {workflow.description}")
    print(f"  ステート数: {len(workflow.states)}  トランジション数: {len(workflow.transitions)}")

    errors = validate_workflow(workflow)
    if errors:
        print("\nバリデーションエラー:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("  ✓ 検証成功")

    if args.dry_run:
        print("\nドライラン完了。ワークフローは有効です。")
        _print_workflow_summary(workflow)
        sys.exit(0)

    # Apply CLI overrides
    if args.max_steps is not None:
        workflow.config.max_steps = args.max_steps
    if args.verbose:
        workflow.config.verbose = True

    # Parse context
    context = parse_context_args(args.context)

    # API キーを確認（anthropic モードのみ）
    if args.agent == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("\nERROR: ANTHROPIC_API_KEY 環境変数が設定されていません。")
            sys.exit(1)

    print(f"\nワークフロー '{workflow.name}' を実行します...")
    print(f"  LLM バックエンド: {args.agent}" + (f" ({args.model})" if args.model else ""))
    if args.input:
        print(f"入力: {args.input[:100]}{'...' if len(args.input) > 100 else ''}")
    print()

    # LLM 関数を構築
    if args.agent == "anthropic":
        model = args.model or "claude-sonnet-4-20250514"
        async def llm_fn(prompt: str) -> str:
            return await anthropic_llm(prompt, model=model)
    else:
        cli = args.agent
        model = args.model  # None の場合は call_cli_llm 内で無視される
        async def llm_fn(prompt: str) -> str:
            return await call_cli_llm(prompt, cli=cli, model=model)

    # Run
    engine = StateMachineEngine(llm_fn=llm_fn, verbose=args.verbose)
    result = await engine.run(workflow, input_text=args.input, context=context)

    # 出力
    print("\n" + "═" * 60)
    if result.success:
        print(f"✓ ワークフローが正常に完了しました")
        print(f"  最終ステート: {result.final_state}")
        print(f"  実行ステップ数: {len(result.steps)}")
        print(f"\n{'─'*60}")
        print("最終出力:")
        print("─" * 60)
        print(result.output)
    else:
        print(f"✗ ワークフローが失敗しました")
        print(f"  最終ステート: {result.final_state}")
        print(f"  エラー: {result.error}")
        sys.exit(1)

    if args.output_json:
        print("\n" + "─" * 60)
        print("JSON 結果:")
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


def _print_workflow_summary(workflow) -> None:
    print("\nワークフロー構造:")
    for state_id, state in workflow.states.items():
        marker = "◉" if state_id == workflow.initial_state else ("□" if state.terminal else "○")
        print(f"  {marker} {state_id}: {state.description}")
    print("\nトランジション:")
    for t in workflow.transitions:
        print(f"  {t.from_state} → {t.to_state} [priority={t.priority}]")
        print(f"    条件: {t.condition[:80]}...")


if __name__ == "__main__":
    asyncio.run(main())
