# -*- coding: utf-8 -*-
"""ログ・状態ファイル・ロックなど、どのモジュールからも使う下回り。

ここに置くものの共通点は「同期の判断には関わらないが、無人実行で事故らないために要る」こと。
特にログは、URL に埋めた PAT をそのまま出してしまうと運用ログが資格情報の平文置き場に
なるので、出力の直前で必ず伏せ字にする。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys


# --------------------------------------------------------------------------- #
# ログ
# --------------------------------------------------------------------------- #

class Logger:
    """標準出力と（設定されていれば）ログファイルへ書く。

    伏せ字の対象は 2 つ:
      - 設定から集めたトークン文字列そのもの
      - URL に埋め込まれた資格情報（https://user:pass@host → https://***@host）
    git のエラーメッセージには remote URL がそのまま載るので、後者が効く。
    """

    CRED_RE = re.compile(r"(https?://)[^/@\s]+@")

    def __init__(self, log_file="", secrets=(), verbose=False, quiet=False):
        self.log_file = log_file
        self.secrets = sorted((s for s in secrets if s), key=len, reverse=True)
        self.verbose = verbose
        # quiet: 進捗を標準出力へ出さない（cron から呼ぶとき用）。警告と失敗は
        # 握りつぶさず標準エラーへ出す — cron はそれを見て人へ届ける。
        self.quiet = quiet
        if log_file:
            parent = os.path.dirname(os.path.abspath(log_file))
            if parent:
                os.makedirs(parent, exist_ok=True)

    def redact(self, text):
        text = str(text)
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return self.CRED_RE.sub(r"\1***@", text)

    def _emit(self, level, message):
        line = "%s [%s] %s" % (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            level, self.redact(message))
        alarming = level in ("WARN", "ERROR")
        if alarming or not self.quiet:
            print(line, file=sys.stderr if alarming else sys.stdout, flush=True)
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as exc:          # ログが書けなくても同期そのものは続ける
                print("ログファイルに書けません: %s" % exc, file=sys.stderr, flush=True)

    def info(self, message):
        self._emit("INFO", message)

    def warn(self, message):
        self._emit("WARN", message)

    def error(self, message):
        self._emit("ERROR", message)

    def debug(self, message):
        if self.verbose:
            self._emit("DEBUG", message)


# --------------------------------------------------------------------------- #
# JSON の読み書き（途中で落ちても壊れた状態を残さない）
# --------------------------------------------------------------------------- #

def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default
    return data


def write_json(path, payload):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def safe_name(text):
    """任意の文字列をファイル名に使える形へ落とす。"""
    return re.sub(r"[^\w.-]", "_", str(text))


# --------------------------------------------------------------------------- #
# 多重起動防止
# --------------------------------------------------------------------------- #

class Lock:
    """状態ディレクトリ上のロックファイル。

    実行タイミングは外部（cron / タスクスケジューラ）任せなので、前回の実行が
    長引いている最中に次のキックが来ることがある。同じストアを 2 プロセスで
    触ると fetch/push が競合するため、後から来た方は黙って降りる。
    """

    def __init__(self, path, timeout_minutes=180, log=None):
        self.path = path
        self.timeout = datetime.timedelta(minutes=timeout_minutes)
        self.log = log
        self.acquired = False

    def acquire(self):
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        for attempt in (1, 2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                # 1 回目だけ「古すぎるロック（＝異常終了の置き土産）」を掃除して再試行する。
                if attempt == 2 or not self._clear_if_stale():
                    return False
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(),
                           "started_at": datetime.datetime.now().isoformat(timespec="seconds")}, f)
            self.acquired = True
            return True
        return False

    def _clear_if_stale(self):
        try:
            age = datetime.datetime.now() - datetime.datetime.fromtimestamp(
                os.path.getmtime(self.path))
        except OSError:
            return True                    # 読めた時点で消えていた = 取りに行ってよい
        if age <= self.timeout:
            return False
        if self.log:
            self.log.warn("古いロック（%d 分経過）を破棄します: %s"
                          % (age.total_seconds() // 60, self.path))
        try:
            os.remove(self.path)
        except OSError:
            return False
        return True

    def release(self):
        if not self.acquired:
            return
        try:
            os.remove(self.path)
        except OSError:
            pass
        self.acquired = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
