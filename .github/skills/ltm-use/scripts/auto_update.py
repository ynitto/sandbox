#!/usr/bin/env python3
"""auto_update.py - 記憶の自動同期スクリプト

セッション開始時に手順 2-1〜2-3 を一括実行する。インターバルチェックを内包し、
前回実行から interval_hours 未満の場合はスキップする。

Usage:
  python auto_update.py check                    # インターバルチェックして同期
  python auto_update.py check --force            # インターバル無視して強制同期
  python auto_update.py configure --interval 12  # インターバルを12時間に設定
  python auto_update.py status                   # 現在の設定を表示
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_utils

_INTERVAL_KEY = "auto_sync_interval_hours"
_LAST_CHECKED_KEY = "auto_sync_last_checked_at"
_DEFAULT_INTERVAL = 24


def _should_sync(cfg: dict) -> bool:
    """前回同期から interval_hours 以上経過しているかを判定する。"""
    last_checked = cfg.get(_LAST_CHECKED_KEY)
    if not last_checked:
        return True
    interval = cfg.get(_INTERVAL_KEY, _DEFAULT_INTERVAL)
    try:
        last_dt = datetime.fromisoformat(last_checked)
    except (ValueError, TypeError):
        return True
    now = datetime.now(timezone.utc)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return now - last_dt >= timedelta(hours=interval)


def run_sync(force: bool = False) -> None:
    """記憶同期のメインエントリーポイント。

    force=True の場合、interval に関わらず即座に同期する。
    """
    cfg = memory_utils.load_config()

    if not force and not _should_sync(cfg):
        interval = cfg.get(_INTERVAL_KEY, _DEFAULT_INTERVAL)
        last = cfg.get(_LAST_CHECKED_KEY, "未チェック")
        print(f"⏭️  インターバル未達のためスキップ (間隔: {interval}h, 最終チェック: {last})")
        return

    scripts_dir = os.path.dirname(os.path.abspath(__file__))

    # 手順 2-1: shared からの記憶を home に取り込む
    try:
        subprocess.run(
            [sys.executable, os.path.join(scripts_dir, "sync_memory.py"), "--import-to-home"],
            check=False,
        )
    except Exception as e:
        print(f"⚠️  手順 2-1 (sync_memory --import-to-home) をスキップしました: {e}")

    # 手順 2-2: share_score >= auto_promote_threshold の記憶を shared へ昇格し push する
    try:
        subprocess.run(
            [
                sys.executable, os.path.join(scripts_dir, "promote_memory.py"),
                "--scope", "home", "--target", "shared", "--auto", "--push",
            ],
            check=False,
        )
    except Exception as e:
        print(f"⚠️  手順 2-2 (promote_memory) をスキップしました: {e}")

    # 手順 2-3: Copilot Memory インポート（スクリプト内で72h スキップ）
    try:
        subprocess.run(
            [sys.executable, os.path.join(scripts_dir, "sync_copilot_memory.py")],
            check=False,
        )
    except Exception as e:
        print(f"⚠️  手順 2-3 (sync_copilot_memory) をスキップしました: {e}")

    # 最終チェック日時を更新（サブプロセス実行中の config 変更を保持するため再読み込み）
    cfg = memory_utils.load_config()
    cfg[_LAST_CHECKED_KEY] = datetime.now(timezone.utc).isoformat()
    memory_utils.save_config(cfg)


def configure_sync(interval_hours: int | None = None) -> None:
    """自動同期設定を変更する。"""
    cfg = memory_utils.load_config()
    if interval_hours is not None:
        if interval_hours < 1:
            print("❌ interval_hours は 1 以上で指定してください")
            return
        cfg[_INTERVAL_KEY] = interval_hours
        memory_utils.save_config(cfg)
        print(f"✅ 自動同期設定を保存しました")
        print(f"   チェック間隔: {interval_hours} 時間")


# --- CLI ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="記憶の自動同期チェック")
    sub = parser.add_subparsers(dest="command")

    check_cmd = sub.add_parser("check", help="記憶を同期する")
    check_cmd.add_argument("--force", action="store_true", help="間隔を無視して即座に同期")

    config_cmd = sub.add_parser("configure", help="自動同期設定を変更する")
    config_cmd.add_argument("--interval", type=int, default=None, metavar="HOURS",
                            help="チェック間隔（時間）")

    sub.add_parser("status", help="現在の自動同期設定を表示する")

    args = parser.parse_args()

    if args.command == "check":
        run_sync(force=args.force)
    elif args.command == "configure":
        configure_sync(interval_hours=args.interval)
    elif args.command == "status":
        cfg = memory_utils.load_config()
        interval = cfg.get(_INTERVAL_KEY, _DEFAULT_INTERVAL)
        last = cfg.get(_LAST_CHECKED_KEY, "未チェック")
        print(f"チェック間隔: {interval} 時間")
        print(f"最終チェック: {last}")
    else:
        parser.print_help()
