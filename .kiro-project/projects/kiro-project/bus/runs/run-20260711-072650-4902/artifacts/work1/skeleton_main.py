#!/usr/bin/env python3
"""kiro-flow: 分散 Dynamic Workflow 実行エンジン

エントリーポイント・ヘルプ表示・サブコマンド骨格。
本番実装は kiro-flow.py に存在する。
"""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kiro-flow",
        description="kiro-flow — 分散 Dynamic Workflow 実行エンジン",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
サブコマンド:
  run     ワークフローを実行する
  status  実行中の run の状態を表示する
  result  完了した run の最終結果を表示する
  clean   古い run を掃除する
  daemon  デーモン（ワーカー）を起動する

使用例:
  kiro-flow run --goal "React ダッシュボードを作成する"
  kiro-flow status --run-id abc123
  kiro-flow result --run-id abc123
  kiro-flow clean --older-than 7d
""",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="kiro-flow 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # run サブコマンド
    run_parser = subparsers.add_parser(
        "run",
        help="ワークフローを実行する",
        description="kiro-flow run: タスクグラフを生成してワークフローを開始する",
    )
    run_parser.add_argument(
        "--goal",
        required=True,
        metavar="GOAL",
        help="実行するゴール（自然言語で記述）",
    )
    run_parser.add_argument(
        "--run-id",
        metavar="RUN_ID",
        help="run ID を手動指定（省略時は自動生成）",
    )
    run_parser.add_argument(
        "--planner",
        metavar="PLANNER",
        default="default",
        help="タスク分解に使うプランナー（デフォルト: default）",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="タスクグラフを生成するだけで実行しない",
    )

    # status サブコマンド
    status_parser = subparsers.add_parser(
        "status",
        help="実行中の run の状態を表示する",
        description="kiro-flow status: run の進捗・タスク状態を確認する",
    )
    status_parser.add_argument(
        "--run-id",
        required=True,
        metavar="RUN_ID",
        help="確認する run の ID",
    )
    status_parser.add_argument(
        "--watch",
        action="store_true",
        help="状態をリアルタイムで監視する",
    )

    # result サブコマンド
    result_parser = subparsers.add_parser(
        "result",
        help="完了した run の最終結果を表示する",
        description="kiro-flow result: 完了した run の成果物を取得する",
    )
    result_parser.add_argument(
        "--run-id",
        required=True,
        metavar="RUN_ID",
        help="結果を取得する run の ID",
    )
    result_parser.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="出力フォーマット（デフォルト: text）",
    )

    # clean サブコマンド
    clean_parser = subparsers.add_parser(
        "clean",
        help="古い run を掃除する",
        description="kiro-flow clean: 古い run データを削除する",
    )
    clean_parser.add_argument(
        "--older-than",
        metavar="DURATION",
        default="7d",
        help="削除対象の経過時間（例: 7d, 24h）（デフォルト: 7d）",
    )
    clean_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="削除対象を表示するだけで削除しない",
    )

    # daemon サブコマンド
    daemon_parser = subparsers.add_parser(
        "daemon",
        help="デーモン（ワーカー）を起動する",
        description="kiro-flow daemon: タスクを処理するワーカーデーモンを起動する",
    )
    daemon_parser.add_argument(
        "--run-id",
        metavar="RUN_ID",
        help="特定 run のワーカーとして起動（省略時は全 run を監視）",
    )
    daemon_parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        metavar="N",
        help="同時実行タスク数（デフォルト: 4）",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    # 各サブコマンドのディスパッチ（将来の実装へのスタブ）
    handlers = {
        "run": _handle_run,
        "status": _handle_status,
        "result": _handle_result,
        "clean": _handle_clean,
        "daemon": _handle_daemon,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


def _handle_run(args: argparse.Namespace) -> int:
    print(f"[kiro-flow] run: goal='{args.goal}'")
    print("（未実装）タスクグラフを生成して実行します。")
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    print(f"[kiro-flow] status: run_id='{args.run_id}'")
    print("（未実装）run の状態を表示します。")
    return 0


def _handle_result(args: argparse.Namespace) -> int:
    print(f"[kiro-flow] result: run_id='{args.run_id}'")
    print("（未実装）最終結果を表示します。")
    return 0


def _handle_clean(args: argparse.Namespace) -> int:
    print(f"[kiro-flow] clean: older_than='{args.older_than}'")
    print("（未実装）古い run を削除します。")
    return 0


def _handle_daemon(args: argparse.Namespace) -> int:
    print(f"[kiro-flow] daemon: concurrency={args.concurrency}")
    print("（未実装）ワーカーデーモンを起動します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
