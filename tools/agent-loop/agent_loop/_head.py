# _head.py — 共有 import と定数（元 agent-loop.py の冒頭ブロック）。
# この断片は単体 import しない。agent_loop/__init__.py が共有名前空間へ exec 合成する。
from __future__ import annotations

"""
agent-loop.py — tmux 分割ウィンドウで kiro-cli を起動し、
設定ファイルに定義したプロンプトを定期的に送信するスクリプト。

依存ライブラリ:
  - tmux      (apt install tmux)     セッション起動・入力送信・出力取得
  - PyYAML    (pip install pyyaml)   設定ファイル読み込み（JSON も可、任意）

動作環境: WSL (Ubuntu) / Linux
終了方法: ターミナルを閉じる (SIGHUP) か Ctrl+C、またはコマンド quit

使い方:
  python /path/to/agent-loop.py
  起動後、コマンドプロンプト (>) で状態確認と定期プロンプト設定を管理できます。
    > status
    > prompt-list
    > help

設定ファイルや定期プロンプトの例は README を参照。

注記:
  - プロンプト送信後の応答待機は行いません。
  - tmux 内で実行した場合は現在ウィンドウを分割して表示します。
"""

import argparse
import atexit
import collections
import datetime as _dt
import fcntl
import hashlib
import hmac
import http.server
import importlib.util
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

try:
    import readline as _readline
    _readline.set_history_length(50)
except ImportError:
    _readline = None  # type: ignore

# ---------------------------------------------------------------------------
# 依存ライブラリの存在チェック
# ---------------------------------------------------------------------------

if shutil.which("tmux") is None:
    print("[agent-loop] ERROR: tmux が見つかりません。", file=sys.stderr)
    print("  Ubuntu/WSL: sudo apt install tmux", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# PyYAML（任意）
# ---------------------------------------------------------------------------

try:
    import yaml  # type: ignore

    def _load_config_file(path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

except ImportError:
    yaml = None  # type: ignore

    def _load_config_file(path: Path) -> dict[str, Any]:  # type: ignore[misc]
        """PyYAML がない場合は JSON のみ受け付ける。"""
        if path.suffix.lower() in (".yaml", ".yml"):
            print(
                "[agent-loop] ERROR: YAML 設定ファイルを読むには PyYAML が必要です。",
                file=sys.stderr,
            )
            print("  pip install pyyaml", file=sys.stderr)
            sys.exit(1)
        with path.open(encoding="utf-8") as f:
            return json.load(f)


# ---------------------------------------------------------------------------
# ログ設定（stderr — コマンドプロンプトの入力と混在しないよう分離）
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("agent-loop")


LOG_FILE_NAME = "agent-loop.log"

# ---------------------------------------------------------------------------
# send/ls サブコマンド用定数
# ---------------------------------------------------------------------------

_AGENT_HOME = Path.home() / ".agent"
_DEFAULT_SEND_SESSION = "kiro"
_SEND_STARTUP_TIMEOUT = 60
_PROMPT_RE = re.compile(r"(^\s*[>?❯›]\s*$|!>)", re.MULTILINE)
_ENV_LAST_ACTIVE = "AGENT_LAST_ACTIVE"
_AGENTS_DIR = Path.home() / ".kiro" / "agents"

# ---------------------------------------------------------------------------
# inbound webhook 用定数
# ---------------------------------------------------------------------------

_WEBHOOK_QUEUE_MAX = 100           # name ごとの外部キュー上限（超過は古いものから破棄）
_WEBHOOK_DEFAULT_HOST = "127.0.0.1"
_WEBHOOK_DEFAULT_PATH_PREFIX = "/hooks"
_WEBHOOK_DEFAULT_MAX_BODY = 1_048_576  # 1MB
_WEBHOOK_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


def _find_running_daemon(cwd: Path) -> int | None:
    """同じ cwd で動いている agent-loop デーモンの PID を返す（なければ None）。

    _read_all_states() は後方で定義されているが Python は呼び出し時に解決するため問題ない。
    """
    cwd_str = str(cwd.resolve())
    for data in _read_all_states():  # noqa: F821 (前方参照)
        if data.get("cwd") == cwd_str:
            return int(data["pid"])
    return None


