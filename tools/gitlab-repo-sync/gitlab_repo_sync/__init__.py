# -*- coding: utf-8 -*-
"""gitlab-repo-sync — 設定した組のリポジトリを、安全な範囲でだけ同期する単体コマンド。

設定した「リポジトリの組（ペア）」を、設定した「ルール」に従って同期することだけをする。
スケジュールも常駐も持たない — cron / systemd timer / Windows タスクスケジューラなど、
外の仕組みから呼ばれる前提。

安全側の原則:
  - fast-forward できるときだけ自動で動かす。分岐は触らずに報告する。
  - `--force` は一方向の戦略で `allow_force` を明示したときだけ使う。
  - 削除の伝播も明示したときだけ。既定では消えた ref を作り直す方に倒す。

使い方:
  gitlab-repo-sync sync                     # 設定内の全ペアを同期
  gitlab-repo-sync sync --pair app          # 1 組だけ
  gitlab-repo-sync sync --dry-run           # 予定だけ表示（リモートを変えない）
  gitlab-repo-sync status                   # 前回結果と未解決の分岐
  gitlab-repo-sync list                     # ペア・リポジトリ・共有ストアの対応
"""
from __future__ import annotations

import argparse
import os
import sys

from .config import ConfigError, find_config, load_config
from .runner import print_layout, print_refs, print_status, run
from .util import Lock, Logger

__version__ = "1.0.0"

__all__ = ["main", "__version__"]


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="gitlab-repo-sync",
        description="設定したリポジトリの組を、ルールに従って同期する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("例:\n"
                "  gitlab-repo-sync sync --config ~/gitlab-repo-sync.yaml\n"
                "  gitlab-repo-sync sync --pair app --dry-run\n"
                "  gitlab-repo-sync status\n"
                "  gitlab-repo-sync list\n"
                "\n"
                "実行タイミングは外部に任せます（cron / systemd timer /\n"
                "Windows タスクスケジューラなど）。多重起動はロックで防ぎます。"))
    parser.add_argument("--version", action="version", version="gitlab-repo-sync %s" % __version__)
    parser.add_argument("--config", "-c", default="",
                        help="設定ファイル（既定: ./gitlab-repo-sync.yaml → ~/gitlab-repo-sync.yaml）")
    parser.add_argument("--verbose", "-v", action="store_true", help="git コマンドまで表示する")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="進捗を標準出力へ出さない（警告と失敗は標準エラーへ出す）")

    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser("sync", help="ペアを同期する")
    p_sync.add_argument("--pair", "-p", default=None, help="このペアだけ処理する")
    p_sync.add_argument("--dry-run", action="store_true", help="push せず、予定だけ表示する")

    sub.add_parser("status", help="前回結果と未解決の分岐を表示する")
    sub.add_parser("list", help="ペア・リポジトリ・共有ストアの対応を表示する")
    sub.add_parser("refs", help="記録済みの ref とストア内の ref 名を表示する")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    path = find_config(args.config)
    if not path:
        print("設定ファイルが見つかりません。--config で指定するか、"
              "カレントか HOME に gitlab-repo-sync.yaml を置いてください。", file=sys.stderr)
        return 1
    if not os.path.exists(path):
        print("設定ファイルが見つかりません: %s" % path, file=sys.stderr)
        return 1
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        print("設定の誤り（%s）: %s" % (path, exc), file=sys.stderr)
        return 1

    if args.command == "status":
        print_status(cfg)
        return 0
    if args.command == "list":
        print_layout(cfg)
        return 0
    if args.command == "refs":
        print_refs(cfg)
        return 0

    log = Logger(cfg.log_file, cfg.credentials.secrets(), args.verbose, args.quiet)
    lock_path = os.path.join(cfg.state_dir, "sync.lock")
    with Lock(lock_path, cfg.lock_timeout_minutes, log) as lock:
        if not lock.acquire():
            # 外部スケジューラが前回の実行中に次を撃った場合。失敗ではないので 0 で戻る。
            log.info("別の同期が実行中のため、今回はスキップします（%s）。" % lock_path)
            return 0
        return run(cfg, log, only=args.pair, dry_run=args.dry_run)
