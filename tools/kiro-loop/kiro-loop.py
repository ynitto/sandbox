#!/usr/bin/env python
"""
kiro-loop.py — tmux 分割ウィンドウで kiro-cli を起動し、
設定ファイルに定義したプロンプトを定期的に送信するスクリプト。

依存ライブラリ:
  - tmux      (apt install tmux)     セッション起動・入力送信・出力取得
  - PyYAML    (pip install pyyaml)   設定ファイル読み込み（JSON も可、任意）

動作環境: WSL (Ubuntu) / Linux
終了方法: ターミナルを閉じる (SIGHUP) か Ctrl+C、またはコマンド quit

使い方:
  python /path/to/kiro-loop.py
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
import datetime as _dt
import fcntl
import hashlib
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
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
    print("[kiro-loop] ERROR: tmux が見つかりません。", file=sys.stderr)
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
                "[kiro-loop] ERROR: YAML 設定ファイルを読むには PyYAML が必要です。",
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
log = logging.getLogger("kiro-loop")


LOG_FILE_NAME = "kiro-loop.log"

# ---------------------------------------------------------------------------
# send/ls サブコマンド用定数
# ---------------------------------------------------------------------------

_KIRO_HOME = Path.home() / ".kiro"
_DEFAULT_SEND_SESSION = "kiro"
_SEND_STARTUP_TIMEOUT = 60
_PROMPT_RE = re.compile(r"(^\s*[>?❯›]\s*$|!>)", re.MULTILINE)
_ENV_LAST_ACTIVE = "KIRO_LAST_ACTIVE"


def _find_running_daemon(cwd: Path) -> int | None:
    """同じ cwd で動いている kiro-loop デーモンの PID を返す（なければ None）。

    _read_all_states() は後方で定義されているが Python は呼び出し時に解決するため問題ない。
    """
    cwd_str = str(cwd.resolve())
    for data in _read_all_states():  # noqa: F821 (前方参照)
        if data.get("cwd") == cwd_str:
            return int(data["pid"])
    return None


# ---------------------------------------------------------------------------
# 分散セマフォ（複数 kiro-loop 間の kiro-cli 同時実行数制御）
# ---------------------------------------------------------------------------

CONCURRENCY_AGENT_NAME = "kiro-loop-concurrency"
_SLOTS_DIR = Path.home() / ".kiro" / "slots"
_SLOTS_MUTEX = _SLOTS_DIR / ".lock"
_DEFAULT_SLOT_TIMEOUT = 7200  # 猶予時間のデフォルト値（秒）
_STATE_DIR = Path.home() / ".kiro" / "loop-state"  # デーモン状態ファイルディレクトリ


class GlobalSemaphore:
    """ファイルベースの分散セマフォ。複数 kiro-loop プロセス間で kiro-cli の同時実行数を制御する。

    スロットファイル:     ~/.kiro/slots/pane_{N}.json
    クールダウンファイル: ~/.kiro/slots/cooldown_{N}.json
    ミューテックス:       ~/.kiro/slots/.lock (fcntl.flock)
    """

    def __init__(self, max_concurrent: int, slot_timeout_seconds: int = _DEFAULT_SLOT_TIMEOUT, cooldown_seconds: int = 0) -> None:
        self.max_concurrent = max_concurrent
        self._slot_timeout = slot_timeout_seconds
        self.cooldown_seconds = cooldown_seconds
        _SLOTS_DIR.mkdir(parents=True, exist_ok=True)

    def acquire(self, pane_id: str, pid: int | None = None) -> bool:
        """スロットを取得する。取得できた場合 True、上限に達した場合 False を返す。

        pid を指定した場合はそのプロセス ID をスロットファイルに記録する。
        省略時は呼び出し元プロセスの PID を使用する。
        """
        if self.max_concurrent <= 0:
            return True

        slot_file = self._slot_path(pane_id)
        try:
            with open(_SLOTS_MUTEX, "w") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    slot_file.unlink(missing_ok=True)
                    active = self._count_active_slots()
                    if active < self.max_concurrent:
                        slot_file.write_text(
                            json.dumps({
                                "pane_id": pane_id,
                                "pid": pid if pid is not None else os.getpid(),
                                "acquired_at": time.time(),
                            }),
                            encoding="utf-8",
                        )
                        return True
                    return False
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except OSError as exc:
            log.warning("セマフォ取得中にエラーが発生しました: %s", exc)
            return True  # エラー時は実行を許可（安全側に倒す）

    def release(self, pane_id: str) -> None:
        """スロットを解放する（冪等）。クールダウンが設定されている場合は記録する。"""
        try:
            self._slot_path(pane_id).unlink(missing_ok=True)
        except OSError:
            pass
        if self.cooldown_seconds > 0:
            try:
                self._cooldown_path(pane_id).write_text(
                    json.dumps({"pane_id": pane_id, "released_at": time.time()}),
                    encoding="utf-8",
                )
            except OSError:
                pass

    @property
    def slot_timeout(self) -> int:
        return self._slot_timeout

    def slot_elapsed(self, pane_id: str) -> float | None:
        """スロットファイルが存在する場合、取得からの経過秒を返す。存在しない場合は None。
        ファイルが読めない場合はタイムアウト超過扱いの値を返す。
        """
        slot_file = self._slot_path(pane_id)
        if not slot_file.exists():
            return None
        try:
            data = json.loads(slot_file.read_text(encoding="utf-8"))
            return time.time() - float(data.get("acquired_at", 0))
        except (json.JSONDecodeError, OSError, ValueError):
            return float(self._slot_timeout + 1)

    def cooldown_remaining(self, pane_id: str) -> float:
        """クールダウンの残り秒数を返す。クールダウン中でなければ 0 以下の値を返す。
        期限切れのクールダウンファイルは削除する。
        """
        if self.cooldown_seconds <= 0:
            return 0.0
        cooldown_file = self._cooldown_path(pane_id)
        if not cooldown_file.exists():
            return 0.0
        try:
            data = json.loads(cooldown_file.read_text(encoding="utf-8"))
            released_at = float(data.get("released_at", 0))
            remaining = self.cooldown_seconds - (time.time() - released_at)
            if remaining <= 0:
                cooldown_file.unlink(missing_ok=True)
            return remaining
        except (json.JSONDecodeError, OSError, ValueError):
            return 0.0

    @staticmethod
    def is_busy(pane_id: str, slot_timeout: int = _DEFAULT_SLOT_TIMEOUT) -> bool:
        """スロットファイルを参照してペインが処理中かを判断する。"""
        slot_file = _SLOTS_DIR / f"pane_{pane_id.lstrip('%')}.json"
        if not slot_file.exists():
            return False
        try:
            data = json.loads(slot_file.read_text(encoding="utf-8"))
            acquired_at = float(data.get("acquired_at", 0))
            if time.time() - acquired_at > slot_timeout:
                return False
            pid = int(data.get("pid", 0))
            if pid > 0:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return False
            return True
        except (json.JSONDecodeError, OSError, ValueError):
            return False

    def _slot_path(self, pane_id: str) -> Path:
        return _SLOTS_DIR / f"pane_{pane_id.lstrip('%')}.json"

    def _cooldown_path(self, pane_id: str) -> Path:
        return _SLOTS_DIR / f"cooldown_{pane_id.lstrip('%')}.json"

    def _count_active_slots(self) -> int:
        now = time.time()
        count = 0
        for slot_file in _SLOTS_DIR.glob("pane_*.json"):
            try:
                data = json.loads(slot_file.read_text(encoding="utf-8"))
                pid = int(data.get("pid", 0))
                acquired_at = float(data.get("acquired_at", 0))

                if now - acquired_at > self._slot_timeout:
                    slot_file.unlink(missing_ok=True)
                    continue

                if pid > 0:
                    try:
                        os.kill(pid, 0)
                        count += 1
                    except ProcessLookupError:
                        slot_file.unlink(missing_ok=True)
                    except PermissionError:
                        count += 1  # 他ユーザーのプロセスは生きているとみなす
                else:
                    count += 1
            except (json.JSONDecodeError, OSError, ValueError):
                try:
                    slot_file.unlink(missing_ok=True)
                except OSError:
                    pass
        return count


class SlotMonitor:
    """agent hook が発火しなかった場合のフォールバック: ペイン出力を監視してスロットを解放する。

    状態遷移:
      waiting_start → (プロンプト消失) → processing → (プロンプト再出現 or タイムアウト) → 解放
    """

    _POLL_INTERVAL = 2.0
    _START_WAIT_TIMEOUT = 60.0  # kiro-cli が処理を始めるまでの最大待機秒数（固定）

    def __init__(self, semaphore: GlobalSemaphore, slot_timeout_seconds: int = _DEFAULT_SLOT_TIMEOUT) -> None:
        self._semaphore = semaphore
        self._slot_timeout = slot_timeout_seconds
        self._lock = threading.Lock()
        # pane_id → {"state": "waiting_start"|"processing", "acquired_at": float}
        self._pending: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def track(self, pane_id: str) -> None:
        """スロットを取得済みのペインの監視を開始する。"""
        with self._lock:
            self._pending[pane_id] = {
                "state": "waiting_start",
                "acquired_at": time.time(),
            }

    def untrack(self, pane_id: str) -> None:
        """監視を手動で終了する（agent hook 発火時など）。"""
        with self._lock:
            self._pending.pop(pane_id, None)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_loop,
            name="slot-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self._POLL_INTERVAL):
            with self._lock:
                pane_ids = list(self._pending.keys())

            for pane_id in pane_ids:
                self._check_pane(pane_id)

    def _check_pane(self, pane_id: str) -> None:
        with self._lock:
            entry = self._pending.get(pane_id)
            if entry is None:
                return
            state = entry["state"]
            acquired_at = entry["acquired_at"]

        # ペインが存在しない場合は即座に解放
        result = subprocess.run(
            [shutil.which("tmux") or "tmux", "display-message", "-p", "-t", pane_id, "#{pane_id}"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            self._release(pane_id)
            return

        content = _capture_pane(pane_id)
        has_prompt = _pane_has_prompt(content)
        now = time.time()

        if state == "waiting_start":
            if not has_prompt:
                with self._lock:
                    if pane_id in self._pending:
                        self._pending[pane_id]["state"] = "processing"
            elif now - acquired_at > self._START_WAIT_TIMEOUT:
                # kiro-cli が処理を開始しないままタイムアウト
                log.warning("SlotMonitor: ペイン %s が処理を開始しないためスロットを解放します。", pane_id)
                self._release(pane_id)

        elif state == "processing":
            if has_prompt:
                log.info("SlotMonitor: ペイン %s の処理完了を検知。スロットを解放します。", pane_id)
                self._release(pane_id)
            elif now - acquired_at > self._slot_timeout:
                log.warning("SlotMonitor: ペイン %s がタイムアウト。スロットを強制解放します。", pane_id)
                self._release(pane_id)

    def _release(self, pane_id: str) -> None:
        with self._lock:
            self._pending.pop(pane_id, None)
        self._semaphore.release(pane_id)


# ---------------------------------------------------------------------------
# Cron 式パーサー
# ---------------------------------------------------------------------------

class CronExpression:
    """5フィールド cron 式 (分 時 日 月 曜日) のパーサー。

    形式: "分 時 日 月 曜日"
    例:   "0 9 * * 1-5"   → 平日9:00
          "*/30 * * * *"  → 30分ごと
          "0 0 1 * *"     → 毎月1日0:00

    DOM と DOW が両方指定された場合は Vixie cron と同じ OR ロジックを使用する。
    """

    def __init__(self, expr: str) -> None:
        self._expr = expr.strip()
        fields = self._expr.split()
        if len(fields) != 5:
            raise ValueError(
                f"cron 式は「分 時 日 月 曜日」の5フィールドで指定してください: {expr!r}"
            )
        min_f, hour_f, dom_f, month_f, dow_f = fields
        self._mins = self._parse_field(min_f, 0, 59)
        self._hours = self._parse_field(hour_f, 0, 23)
        self._doms = self._parse_field(dom_f, 1, 31)
        self._months = self._parse_field(month_f, 1, 12)
        raw_dows = self._parse_field(dow_f, 0, 7)
        self._dows = {0 if v == 7 else v for v in raw_dows}  # 7 → 0 (日曜)
        self._dom_star = dom_f == "*"
        self._dow_star = dow_f == "*"

    def _parse_field(self, field: str, lo: int, hi: int) -> set[int]:
        values: set[int] = set()
        for part in field.split(","):
            step = 1
            if "/" in part:
                part, step_str = part.rsplit("/", 1)
                step = int(step_str)
                if step < 1:
                    raise ValueError(f"ステップは1以上で指定してください: {field!r}")
            if part == "*":
                values.update(range(lo, hi + 1, step))
            elif "-" in part:
                a, b = part.split("-", 1)
                values.update(range(int(a), int(b) + 1, step))
            else:
                v = int(part)
                values.update(range(v, hi + 1, step) if step > 1 else [v])
        return {v for v in values if lo <= v <= hi}

    def next_run(self, after: _dt.datetime) -> _dt.datetime:
        """after の1分後以降で最初に一致する時刻を返す（秒=0、ローカルタイム基準）。"""
        t = (after + _dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
        limit = after + _dt.timedelta(days=366 * 4)

        while t <= limit:
            if t.month not in self._months:
                t = self._next_valid_month(t)
                continue

            # DOM と DOW の評価 (Vixie cron: 両方指定時は OR)
            cron_dow = (t.weekday() + 1) % 7  # Python Mon=0..Sun=6 → cron Sun=0..Sat=6
            dom_ok = t.day in self._doms
            dow_ok = cron_dow in self._dows

            if self._dom_star and self._dow_star:
                day_ok = True
            elif self._dom_star:
                day_ok = dow_ok
            elif self._dow_star:
                day_ok = dom_ok
            else:
                day_ok = dom_ok or dow_ok

            if not day_ok:
                t = (t + _dt.timedelta(days=1)).replace(hour=0, minute=0)
                continue

            if t.hour not in self._hours:
                next_hours = [h for h in sorted(self._hours) if h > t.hour]
                if next_hours:
                    t = t.replace(hour=next_hours[0], minute=0)
                else:
                    t = (t + _dt.timedelta(days=1)).replace(hour=0, minute=0)
                continue

            if t.minute not in self._mins:
                next_mins = [m for m in sorted(self._mins) if m > t.minute]
                if next_mins:
                    t = t.replace(minute=next_mins[0])
                else:
                    t = (t + _dt.timedelta(hours=1)).replace(minute=0)
                continue

            return t

        raise ValueError(f"次回実行時刻が4年以内に見つかりません: {self._expr!r}")

    def _next_valid_month(self, t: _dt.datetime) -> _dt.datetime:
        year, month = t.year, t.month + 1
        for _ in range(25):
            if month > 12:
                month = 1
                year += 1
            if month in self._months:
                return t.replace(year=year, month=month, day=1, hour=0, minute=0)
            month += 1
        raise ValueError(f"有効な月が見つかりません: {self._expr!r}")

    def __str__(self) -> str:
        return self._expr


def configure_file_logging() -> Path:
    """~/.kiro/kiro-loop.log へのファイルハンドラを追加する。"""
    log_file = _KIRO_HOME / LOG_FILE_NAME
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    resolved_log_file = str(log_file.resolve())
    for handler in root_logger.handlers:
        if isinstance(handler, TimedRotatingFileHandler) and getattr(handler, "baseFilename", "") == resolved_log_file:
            return log_file

    file_handler = TimedRotatingFileHandler(
        filename=resolved_log_file,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(root_logger.level)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(file_handler)
    return log_file


# ---------------------------------------------------------------------------
# 設定ロード
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_NAMES = ["kiro-loop.yaml", "kiro-loop.yml", "kiro-loop.json"]


def find_default_config(cwd: Path) -> Path | None:
    """カレントディレクトリのみを探す（グローバル設定は使わない）。"""
    for name in DEFAULT_CONFIG_NAMES:
        candidate = cwd / name
        if candidate.is_file():
            return candidate
    return None


def load_config(cwd: Path) -> tuple[dict[str, Any], Path, bool]:
    """設定ファイルを読み込み (config, resolved_path, exists) を返す。
    ~/.kiro/ 配下の DEFAULT_CONFIG_NAMES を探す。
    ファイルが存在しない場合は空の config とデフォルトパスを返す（終了しない）。
    """
    kiro_home = Path.home() / ".kiro"
    config_path = find_default_config(kiro_home)
    if config_path is None:
        default_path = kiro_home / "kiro-loop.yaml"
        log.info(
            "~/.kiro の設定ファイルが見つかりません。必要に応じて %s に保存されます。",
            default_path,
        )
        return {}, default_path, False

    log.info("設定ファイルを読み込みます: %s", config_path)
    return _load_config_file(config_path), config_path, True


# ---------------------------------------------------------------------------
# tmux セッション名の生成
# ---------------------------------------------------------------------------

_TMUX_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _tmux_safe_id(s: str, maxlen: int = 12, fallback: str = "id") -> str:
    return _TMUX_SAFE_RE.sub("", s)[:maxlen] or fallback


def _sanitize_session_label(name: str) -> str:
    """tmux セッション名に使用できる文字列に変換する。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", name).strip("-_")
    return (cleaned or "target")[:24]


def _tmux_session_name(base_path: Path, instance_id: str) -> str:
    """実行インスタンスごとに独立した tmux セッション名を生成する。"""
    resolved = str(base_path.resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]
    label = _sanitize_session_label(base_path.name)
    short_id = _tmux_safe_id(instance_id, fallback="run")
    return f"kiro-loop-{label}-{digest}-{short_id}"


# ---------------------------------------------------------------------------
# JSONC (JSON with Comments) サポート
# ---------------------------------------------------------------------------

def _strip_jsonc_comments(text: str) -> str:
    """JSONC のコメント（// および /* */）を除去する。"""
    out: list[str] = []
    i = 0
    in_string = False
    escape = False

    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2 if i + 1 < len(text) else 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _load_jsonc_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        # VS Code settings.json は JSONC のため、コメントと trailing comma を許容する。
        stripped = _strip_jsonc_comments(text)
        stripped = re.sub(r"(\s*[}\]]),", r"\1", stripped)
        data = json.loads(stripped)
        return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# VS Code 設定からの定期プロンプト読み込み
# ---------------------------------------------------------------------------

def load_vscode_periodic_prompts(base_path: Path) -> list[dict[str, Any]]:
    """.vscode/settings.json の agentExecutor.periodicPrompts を読み込み、kiro-loop 形式へ変換する。"""
    settings_path = base_path / ".vscode" / "settings.json"
    if not settings_path.is_file():
        return []

    try:
        data = _load_jsonc_file(settings_path)
    except Exception as exc:
        log.warning("%s の読み込みに失敗しました: %s", settings_path, exc)
        return []

    raw_entries = data.get("agentExecutor.periodicPrompts")
    if not isinstance(raw_entries, list):
        return []

    prompts: list[dict[str, Any]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("enabled", True) is False:
            continue

        agent_id = str(entry.get("agentId", "")).strip().lower()
        if agent_id not in ("kiro", "kiro-cli"):
            continue

        prompt = str(entry.get("prompt", "")).strip()
        if not prompt:
            continue

        try:
            interval = int(entry.get("intervalMinutes", 0))
        except Exception:
            continue
        if interval < 1:
            continue

        prompts.append(
            {
                "name": prompt[:40],
                "prompt": prompt,
                "interval_minutes": interval,
                "enabled": True,
            }
        )

    if prompts:
        log.info("VS Code 設定から periodicPrompts を %d 件読み込みました。", len(prompts))

    return prompts


# ---------------------------------------------------------------------------
# ワークスペース固有のプロンプト設定（.kiro/kiro-loop.yml）
# ---------------------------------------------------------------------------

def _prompt_file(base_path: str) -> Path:
    """起動ディレクトリ単位の定期プロンプト設定ファイルパスを返す。"""
    return Path(base_path) / ".kiro" / "kiro-loop.yml"


def _load_prompt_file_data(base_path: str) -> dict[str, Any]:
    """起動ディレクトリ配下 .kiro/ から設定ファイル（DEFAULT_CONFIG_NAMES）を探して読む。"""
    kiro_dir = Path(base_path) / ".kiro"
    path: Path | None = None
    for name in DEFAULT_CONFIG_NAMES:
        candidate = kiro_dir / name
        if candidate.is_file():
            path = candidate
            break

    if path is None:
        return {}

    if path.suffix.lower() in (".yaml", ".yml") and yaml is None:
        log.warning("PyYAML がないため %s を読めません。pip install pyyaml", path)
        return {}

    try:
        data = _load_config_file(path)
        if isinstance(data, dict):
            return data
        log.warning("%s の形式が不正なため空設定として扱います。", path)
    except Exception as exc:
        log.error("%s の読み込みに失敗しました: %s", path, exc)

    return {}


def load_prompt_config(base_path: str) -> list[dict[str, Any]]:
    """起動ディレクトリ配下 .kiro/ から prompts を読む。"""
    data = _load_prompt_file_data(base_path)
    prompts = data.get("prompts", [])
    if isinstance(prompts, list):
        return [p for p in prompts if isinstance(p, dict)]
    if data:
        log.warning("%s/.kiro/ の prompts が配列ではありません。", base_path)

    return []


def save_prompt_config(base_path: str, prompts: list[dict[str, Any]]) -> bool:
    """起動ディレクトリ配下 .kiro/kiro-loop.yml に prompts を保存する。"""
    path = _prompt_file(base_path)

    if yaml is None:
        log.error("PyYAML が必要です。`pip install pyyaml` を実行してください。")
        return False

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # prompts 以外の設定（kiro_options など）を保持する。
        data = _load_prompt_file_data(base_path)
        data["prompts"] = prompts
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        log.info("定期プロンプト設定を保存しました: %s", path)
        return True
    except Exception as exc:
        log.error("定期プロンプト設定の保存に失敗しました: %s", exc)
        return False


# ---------------------------------------------------------------------------
# tmux ヘルパー（SessionManager より前に定義）
# ---------------------------------------------------------------------------

def _tmux_cmd(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    tmux_bin = shutil.which("tmux")
    if tmux_bin is None:
        raise RuntimeError("tmux が PATH に見つかりません。")
    if capture:
        return subprocess.run([tmux_bin, *args], capture_output=True, text=True)
    return subprocess.run(
        [tmux_bin, *args],
        check=False,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _send_to_pane(pane_id: str, text: str) -> tuple[bool, str]:
    """set-buffer + paste-buffer でペインにテキストを安全送信する。"""
    buffer_name = f"kiro-loop-{uuid.uuid4().hex[:8]}"
    try:
        result = _tmux_cmd("set-buffer", "-b", buffer_name, "--", text)
        if result.returncode != 0:
            err = (result.stderr or "").strip() or "tmux set-buffer に失敗しました。"
            return False, err

        result = _tmux_cmd("paste-buffer", "-t", pane_id, "-b", buffer_name)
        if result.returncode != 0:
            err = (result.stderr or "").strip() or "tmux paste-buffer に失敗しました。"
            return False, err

        result = _tmux_cmd("send-keys", "-t", pane_id, "Enter")
        if result.returncode != 0:
            err = (result.stderr or "").strip() or "tmux send-keys(Enter) に失敗しました。"
            return False, err

        return True, ""
    finally:
        _tmux_cmd("delete-buffer", "-b", buffer_name)


def _tmux_cmd_or_raise(*args: str, error_label: str) -> str:
    """_tmux_cmd を実行し、失敗または空出力なら RuntimeError を送出する。"""
    result = _tmux_cmd(*args)
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        raise RuntimeError(f"{error_label}に失敗しました: {err}")
    output = (result.stdout or "").strip()
    if not output:
        raise RuntimeError(f"{error_label}に失敗しました: 空の結果")
    return output


# ---------------------------------------------------------------------------
# セッション管理
# ---------------------------------------------------------------------------

class SessionManager:
    """カレントディレクトリ上で、プロンプトごとの kiro-cli ペインを直接管理する。"""

    _layout_lock = threading.Lock()  # 全インスタンスで共有するレイアウトロック

    def __init__(
        self,
        target_path: str,
        instance_id: str,
        kiro_args_base: list[str],
        split_direction: str,
        startup_timeout: int,
        response_timeout: int,
        echo_output: bool = False,
        uses_concurrency_agent: bool = False,
    ):
        resolved = Path(target_path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"パスが存在しないかディレクトリではありません: {resolved}")

        self._target_path = str(resolved)
        self._target_name = resolved.name or "default"
        self._instance_id = _tmux_safe_id(instance_id, fallback="run")
        self._kiro_args_base = kiro_args_base[:]
        self._split_direction = "vertical" if str(split_direction).lower() == "vertical" else "horizontal"
        self._startup_timeout = startup_timeout
        self._response_timeout = response_timeout
        self._echo_output = echo_output
        self._uses_concurrency_agent = uses_concurrency_agent

        # prompt_id → pane_id (str)
        self._panes: dict[str, str] = {}
        self._prompt_names: dict[str, str] = {}
        self._tmux_names: dict[str, str] = {}
        self._prompt_cwds: dict[str, str | None] = {}
        self._restart_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

        self._tmux_bin: str | None = None
        self._layout_window_target: str | None = None
        self._layout_controller_pane: str | None = None
        self._active_session_name: str | None = None
        self._tmux_session_name = _tmux_session_name(resolved, self._instance_id)

    # ------------------------------------------------------------------
    # tmux ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _session_from_window_target(window_target: str) -> str:
        if ":" in window_target:
            return window_target.split(":", 1)[0]
        return window_target

    def _split_option(self) -> str:
        return "-v" if self._split_direction == "vertical" else "-h"

    def _layout_name(self) -> str:
        return "even-vertical" if self._split_direction == "vertical" else "even-horizontal"

    def _split_label(self) -> str:
        return "縦" if self._split_direction == "vertical" else "横"

    def _run_tmux(self, args: list[str], capture_output: bool = True) -> subprocess.CompletedProcess[str]:
        return _tmux_cmd(*args, capture=capture_output)

    def _has_session(self, session_name: str) -> bool:
        return _tmux_cmd("has-session", "-t", session_name).returncode == 0

    def _pane_exists(self, pane_target: str) -> bool:
        return _tmux_cmd(
            "display-message", "-p", "-t", pane_target, "#{pane_id}"
        ).returncode == 0

    def _window_target_from_pane(self, pane_target: str) -> str:
        return _tmux_cmd_or_raise(
            "display-message", "-p", "-t", pane_target, "#{session_name}:#{window_index}",
            error_label="tmux ウィンドウ取得",
        )

    def _get_first_window_target(self, session_name: str) -> str:
        raw = _tmux_cmd_or_raise(
            "list-windows", "-t", session_name, "-F", "#{session_name}:#{window_index}",
            error_label="tmux ウィンドウ一覧取得",
        )
        for line in raw.splitlines():
            if target := line.strip():
                return target
        raise RuntimeError("tmux ウィンドウ一覧取得に失敗しました: ウィンドウが見つかりません")

    def _get_first_pane_target(self, window_target: str) -> str:
        raw = _tmux_cmd_or_raise(
            "list-panes", "-t", window_target, "-F", "#{pane_id}",
            error_label="tmux ペイン一覧取得",
        )
        for line in raw.splitlines():
            if target := line.strip():
                return target
        raise RuntimeError("tmux ペイン一覧取得に失敗しました: ペインが見つかりません")

    def _ensure_layout(self) -> None:
        with self.__class__._layout_lock:
            window_target = self._layout_window_target
            controller_pane = self._layout_controller_pane
            if window_target is not None and controller_pane is not None and self._pane_exists(controller_pane):
                return

            pane_target = os.environ.get("TMUX_PANE")
            if pane_target:
                result = _tmux_cmd(
                    "display-message", "-p", "-t", pane_target, "#{session_name}:#{window_index}"
                )
                if result.returncode == 0:
                    window_target = (result.stdout or "").strip()
                    if window_target:
                        self._layout_window_target = window_target
                        self._layout_controller_pane = pane_target
                        self._active_session_name = self._session_from_window_target(window_target)
                        log.info("現在の tmux ウィンドウを %s分割に使用します: %s", self._split_label(), window_target)
                        return

            session_name = self._tmux_session_name
            if not self._has_session(session_name):
                result = _tmux_cmd("new-session", "-d", "-s", session_name)
                if result.returncode != 0:
                    err = (result.stderr or "").strip()
                    raise RuntimeError(f"tmux セッション作成に失敗しました: {err}")
                log.info("tmux セッション '%s' を作成しました。", session_name)

            window_target = self._get_first_window_target(session_name)
            controller_pane = self._get_first_pane_target(window_target)
            self._active_session_name = session_name
            log.info("分割表示するには別端末で `tmux attach -t %s` を実行してください。", session_name)

            self._layout_window_target = window_target
            self._layout_controller_pane = controller_pane

    def _create_worker_pane(self, cmd: str, cwd: str) -> str:
        """kiro-cli を実行する新しいペインを作成してペインターゲットを返す。"""
        self._ensure_layout()

        with self.__class__._layout_lock:
            window_target = self._layout_window_target
            controller_pane = self._layout_controller_pane
            if window_target is None:
                raise RuntimeError("tmux レイアウトが初期化されていません。")

            split_target = controller_pane or window_target
            pane_target = _tmux_cmd_or_raise(
                "split-window",
                self._split_option(),
                "-d", "-P", "-F", "#{pane_id}",
                "-t", split_target,
                "-c", cwd,
                cmd,
                error_label="tmux ペイン分割",
            )

            _tmux_cmd("set-option", "-p", "-t", pane_target, "remain-on-exit", "on", capture=False)
            _tmux_cmd("select-layout", "-t", window_target, self._layout_name(), capture=False)

            if controller_pane and self._pane_exists(controller_pane):
                _tmux_cmd("select-pane", "-t", controller_pane, capture=False)
                _tmux_cmd("refresh-client", "-S", capture=False)

            return pane_target

    # ------------------------------------------------------------------
    # セッション識別ヘルパー
    # ------------------------------------------------------------------

    def _prompt_token(self, prompt_id: str) -> str:
        return _tmux_safe_id(prompt_id, fallback="prompt")

    def _tmux_name_for_prompt(self, prompt_id: str) -> str:
        composed = f"{self._instance_id}-{self._prompt_token(prompt_id)}"
        return _tmux_session_name(Path(self._target_path), composed)

    def get_attach_session_name(self) -> str:
        """アタッチセッション名を返す。"""
        return self._active_session_name or self._tmux_session_name

    def get_target_name(self) -> str:
        return self._target_name

    def get_target_path(self) -> str:
        return self._target_path

    # ------------------------------------------------------------------
    # ペイン起動 / 停止
    # ------------------------------------------------------------------

    def _resolve_cwd(self, cwd: str | None) -> str:
        if cwd:
            candidate = Path(cwd).expanduser().resolve()
            if candidate.is_dir():
                return str(candidate)
            log.warning("エントリの cwd '%s' が存在しないため target_path を使用します。", cwd)
        return self._target_path

    def _start_pane(self, prompt_id: str, prompt_name: str, cwd: str | None = None) -> bool:
        """新しい kiro-cli ペインを起動して管理下に登録する。"""
        if shutil.which("tmux") is None:
            raise RuntimeError("tmux が PATH に見つかりません。`sudo apt install tmux` を実行してください。")
        kiro_bin = shutil.which("kiro-cli")
        if kiro_bin is None:
            raise RuntimeError("kiro-cli が PATH に見つかりません。インストールしてください。")

        session_cwd = self._resolve_cwd(cwd)

        cmd_args = ["chat"] + self._kiro_args_base[:]
        if self._uses_concurrency_agent:
            agent_file = Path.home() / ".kiro" / "agents" / f"{CONCURRENCY_AGENT_NAME}.json"
            if agent_file.is_file():
                cmd_args += ["--agent", CONCURRENCY_AGENT_NAME]
        cmd = " ".join(shlex.quote(arg) for arg in [kiro_bin, *cmd_args])

        try:
            pane_target = self._create_worker_pane(cmd, session_cwd)
        except RuntimeError as exc:
            log.error("プロンプト '%s' のペイン起動に失敗しました: %s", prompt_name, exc)
            return False

        attach_session_name = self.get_attach_session_name()

        with self._lock:
            self._panes[prompt_id] = pane_target
            self._prompt_names[prompt_id] = prompt_name
            self._tmux_names[prompt_id] = attach_session_name
            self._prompt_cwds[prompt_id] = cwd
            if prompt_id not in self._restart_locks:
                self._restart_locks[prompt_id] = threading.Lock()

        log.info(
            "プロンプト '%s' 用ペインを起動しました (pane=%s, tmux=%s, args=%s)。",
            prompt_name, pane_target, attach_session_name, self._kiro_args_base,
        )
        self.write_state()
        return True

    def _stop_pane(self, prompt_id: str) -> None:
        """ペインを終了する（_restart_locks は保持する）。"""
        with self._lock:
            pane_target = self._panes.pop(prompt_id, None)

        if pane_target is not None and self._pane_exists(pane_target):
            log.info("kiro-cli ペインを終了します (pane=%s)。", pane_target)
            _tmux_cmd("send-keys", "-t", pane_target, "C-c", capture=False)
            time.sleep(0.2)
            try:
                window_target = self._window_target_from_pane(pane_target)
                _tmux_cmd("kill-pane", "-t", pane_target, capture=False)
                _tmux_cmd("select-layout", "-t", window_target, self._layout_name(), capture=False)
            except RuntimeError:
                _tmux_cmd("kill-pane", "-t", pane_target, capture=False)

    # ------------------------------------------------------------------
    # 公開インタフェース
    # ------------------------------------------------------------------

    def ensure_session(self, prompt_id: str, prompt_name: str) -> bool:
        """セッションが存在しない場合は起動する。成功時 True を返す。"""
        with self._lock:
            existing = self._panes.get(prompt_id)
            cwd = self._prompt_cwds.get(prompt_id)
        if existing is not None:
            return True
        return self._start_pane(prompt_id, prompt_name, cwd)

    def get_pane_id(self, prompt_id: str) -> str | None:
        """prompt_id に対応するペイン ID を返す（なければ None）。"""
        with self._lock:
            return self._panes.get(prompt_id)

    def send_prompt(self, prompt_id: str, prompt_text: str) -> bool:
        """tmux ペインにプロンプトを送信する（応答待ちはしない）。"""
        with self._lock:
            pane_target = self._panes.get(prompt_id)
            cwd = self._prompt_cwds.get(prompt_id, self._target_path) or self._target_path

        if pane_target is None or not self._pane_exists(pane_target):
            log.warning("kiro-cli ペインが存在しません (prompt_id=%s)。", prompt_id)
            return False

        short = prompt_text[:80] + ("..." if len(prompt_text) > 80 else "")
        log.info("プロンプトを送信します [%s] (pane=%s): %s", cwd, pane_target, short)
        print(f"[kiro-loop] send [{cwd}] (pane={pane_target}) {short}", file=sys.stderr, flush=True)

        ok, err = _send_to_pane(pane_target, prompt_text)
        if not ok:
            log.warning("テキスト送信に失敗しました: %s", err)
            print(f"[kiro-loop] done [{cwd}] failed", file=sys.stderr, flush=True)
            return False

        print(f"[kiro-loop] done [{cwd}] sent", file=sys.stderr, flush=True)
        return True

    def is_pane_alive(self, prompt_id: str) -> bool:
        """ペインが存在するか確認する。"""
        with self._lock:
            pane_target = self._panes.get(prompt_id)
        return pane_target is not None and self._pane_exists(pane_target)

    def is_restarting(self, prompt_id: str) -> bool:
        with self._lock:
            lock = self._restart_locks.get(prompt_id)
        return lock is not None and lock.locked()

    def restart_pane(self, prompt_id: str) -> None:
        """ペインを再起動する。"""
        with self._lock:
            if prompt_id not in self._restart_locks:
                self._restart_locks[prompt_id] = threading.Lock()
            restart_lock = self._restart_locks[prompt_id]
            cwd = self._prompt_cwds.get(prompt_id)
            prompt_name = self._prompt_names.get(prompt_id, prompt_id)

        if not restart_lock.acquire(blocking=False):
            log.info("kiro-cli ペイン再起動は既に進行中です (prompt_id=%s)。", prompt_id)
            return

        log.info("kiro-cli ペインを再起動します (prompt_id=%s)。", prompt_id)
        try:
            self._stop_pane(prompt_id)
            time.sleep(2)
            self._start_pane(prompt_id, prompt_name, cwd)
        finally:
            restart_lock.release()

    def sync_entries(self, entries: list[dict[str, Any]]) -> None:
        """エントリ一覧に合わせてペインを起動/停止する。"""
        desired: dict[str, str] = {}
        desired_cwd: dict[str, str | None] = {}
        for entry in entries:
            prompt_id = str(entry.get("id", "")).strip()
            if not prompt_id:
                continue
            prompt_name = str(entry.get("name", prompt_id)).strip() or prompt_id
            desired[prompt_id] = prompt_name
            desired_cwd[prompt_id] = str(entry.get("cwd", "")).strip() or None

        with self._lock:
            current_ids = set(self._panes.keys())

        remove_ids = current_ids - set(desired.keys())
        add_ids = [pid for pid in desired.keys() if pid not in current_ids]
        keep_ids = current_ids & set(desired.keys())

        for prompt_id in remove_ids:
            with self._lock:
                prompt_name = self._prompt_names.pop(prompt_id, prompt_id)
                self._tmux_names.pop(prompt_id, None)
                self._prompt_cwds.pop(prompt_id, None)
            log.info("プロンプト '%s' のペインを停止します。", prompt_name)
            self._stop_pane(prompt_id)

        with self._lock:
            for prompt_id in keep_ids:
                self._prompt_names[prompt_id] = desired[prompt_id]

        for prompt_id in add_ids:
            self._start_pane(prompt_id, desired[prompt_id], desired_cwd.get(prompt_id))

        if remove_ids and not add_ids:
            self.write_state()

    def get_status(self) -> tuple[str, str, int, int]:
        with self._lock:
            pane_ids = list(self._panes.items())
        alive = sum(1 for _, pane_target in pane_ids if self._pane_exists(pane_target))
        return self._target_name, self._target_path, len(pane_ids), alive

    def list_prompt_statuses(self) -> list[tuple[str, str, bool, str, str]]:
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)
            tmux_names = dict(self._tmux_names)

        statuses: list[tuple[str, str, bool, str, str]] = []
        for prompt_id, pane_target in items:
            prompt_name = names.get(prompt_id, prompt_id)
            tmux_name = tmux_names.get(prompt_id, "")
            statuses.append((prompt_name, prompt_id, self._pane_exists(pane_target), tmux_name, pane_target))

        statuses.sort(key=lambda item: item[0])
        return statuses

    def resolve_managed_pane(self, target: str) -> str | None:
        """管理下のペインの中から target に対応するペイン ID を返す。

        target には pane ID (%N)、tmux セッション名、またはプロンプト名を指定できる。
        管理外のターゲットは None を返す。
        """
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)
            tmux_names = dict(self._tmux_names)

        for prompt_id, pane_target in items:
            if (
                target == pane_target
                or target == tmux_names.get(prompt_id, "")
                or target == names.get(prompt_id, "")
            ):
                return pane_target

        return None

    def restart_if_dead(self) -> None:
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)

        for prompt_id, pane_target in items:
            if self.is_restarting(prompt_id):
                continue
            if not self._pane_exists(pane_target):
                prompt_name = names.get(prompt_id, prompt_id)
                log.warning("プロンプト '%s' のペインが終了しました。再起動します。", prompt_name)
                try:
                    self.restart_pane(prompt_id)
                except RuntimeError as exc:
                    log.error("プロンプト '%s' のペイン再起動に失敗しました: %s", prompt_name, exc)

    def _state_file_path(self) -> Path:
        return _STATE_DIR / f"{os.getpid()}.json"

    def write_state(self) -> None:
        """現在のペイン状態をファイルに書き出す（ls/send サブコマンドが参照する）。"""
        with self._lock:
            items = list(self._panes.items())
            names = dict(self._prompt_names)
        sessions_data = []
        for prompt_id, pane_target in items:
            sessions_data.append({
                "name": names.get(prompt_id, prompt_id),
                "id": prompt_id,
                "pane": pane_target,
                "alive": self._pane_exists(pane_target),
            })
        data = {
            "pid": os.getpid(),
            "cwd": self._target_path,
            "started_at": int(time.time()),
            "updated_at": time.time(),
            "sessions": sessions_data,
        }
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._state_file_path().write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("状態ファイルの書き出しに失敗しました: %s", exc)

    def remove_state(self) -> None:
        """状態ファイルを削除する。"""
        try:
            self._state_file_path().unlink(missing_ok=True)
        except OSError:
            pass

    def stop(self) -> None:
        with self._lock:
            prompt_ids = list(self._panes.keys())
            self._prompt_names.clear()
            self._tmux_names.clear()
            self._prompt_cwds.clear()

        for prompt_id in prompt_ids:
            self._stop_pane(prompt_id)
        self.remove_state()


# ---------------------------------------------------------------------------
# 定期実行スケジューラ
# ---------------------------------------------------------------------------

class PeriodicScheduler:
    """定期プロンプトのスケジュール管理。"""

    def __init__(
        self,
        session_mgr: SessionManager,
        entries: list[dict[str, Any]],
        semaphore: GlobalSemaphore | None = None,
        slot_monitor: "SlotMonitor | None" = None,
    ):
        self._session_mgr = session_mgr
        self._semaphore = semaphore
        self._slot_monitor = slot_monitor
        self._entries: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._set_entries(entries, allow_immediate_once=True)

    def _release_slot(self, pane_id: str | None) -> None:
        if self._semaphore is not None and pane_id:
            self._semaphore.release(pane_id)

    def _update_entry(self, entry_id: str, **fields: Any) -> None:
        with self._lock:
            for e in self._entries:
                if e.get("id") == entry_id:
                    e.update(fields)
                    break

    def _set_entries(self, entries: list[dict[str, Any]], allow_immediate_once: bool = False) -> None:
        normalized: list[dict[str, Any]] = []
        now = time.time()

        for entry in entries:
            if not entry.get("enabled", True):
                continue

            prompt = str(entry.get("prompt", "")).strip()
            if not prompt:
                continue

            name = str(entry.get("name", prompt[:40]))

            # スケジュール: cron 式 または interval_minutes のどちらかが必要
            cron_str = str(entry.get("cron", "")).strip()
            cron_expr: CronExpression | None = None
            interval = 0

            if cron_str:
                try:
                    cron_expr = CronExpression(cron_str)
                except ValueError as exc:
                    log.warning("cron 式が不正なためスキップします: %s (%s)", cron_str, exc)
                    continue
            else:
                interval_minutes = entry.get("interval_minutes")
                try:
                    interval = int(interval_minutes)  # type: ignore[arg-type]
                except Exception:
                    continue
                if interval < 1:
                    continue

            prompt_id = str(entry.get("id") or uuid.uuid4())
            run_immediately = bool(
                entry.get("run_immediately_on_startup", entry.get("run_immediately", False))
            )

            if allow_immediate_once and run_immediately:
                # 起動直後は kiro-cli セットアップ時間を見込んで 30 秒待ってから初回送信する。
                next_run_at = now + 30
            elif cron_expr is not None:
                next_run_at = cron_expr.next_run(_dt.datetime.now().astimezone()).timestamp()
            else:
                next_run_at = now + (interval * 60)

            fresh_context = bool(entry.get("fresh_context", False))
            fresh_context_interval_raw = entry.get("fresh_context_interval_minutes")
            try:
                fresh_context_interval = int(fresh_context_interval_raw) if fresh_context_interval_raw is not None else None
            except Exception:
                fresh_context_interval = None
            if fresh_context_interval is not None and fresh_context_interval < 1:
                fresh_context_interval = None

            entry_cwd = str(entry.get("cwd", "")).strip() or None

            normalized.append({
                "id": prompt_id,
                "name": name,
                "prompt": prompt,
                "cron": cron_str if cron_expr else None,
                "interval_minutes": interval,
                "enabled": True,
                "run_immediately_on_startup": run_immediately,
                "next_run_at": next_run_at,
                "fresh_context": fresh_context,
                "fresh_context_interval_minutes": fresh_context_interval,
                "next_clear_at": now if fresh_context else None,
                "cwd": entry_cwd,
            })

        self._session_mgr.sync_entries(normalized)

        with self._lock:
            self._entries = normalized

    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        """エントリを設定する（次回ループから適用）。"""
        self._set_entries(entries, allow_immediate_once=False)

    def _is_in_cooldown(self, entry: dict[str, Any], pane_id: str) -> bool:
        """クールダウン中かチェックし、中なら next_run_at を更新して True を返す。"""
        if self._semaphore is None:
            return False
        remaining = self._semaphore.cooldown_remaining(pane_id)
        if remaining > 0:
            name = str(entry.get("name", ""))
            log.info(
                "[%s] クールダウン中のため実行を延期します (残り %.0f 秒)。",
                name, remaining,
            )
            self._update_entry(str(entry.get("id", "")), next_run_at=time.time() + remaining + 1)
            return True
        return False

    def _next_run_at_for_entry(self, entry: dict[str, Any]) -> float:
        """エントリの次回実行時刻 (Unix timestamp) を計算する。"""
        cron_str = entry.get("cron")
        if cron_str:
            try:
                return CronExpression(cron_str).next_run(_dt.datetime.now()).timestamp()
            except Exception as exc:
                log.error("[%s] cron 次回時刻計算エラー: %s", entry.get("name", ""), exc)
                return time.time() + 60
        interval_minutes = max(int(entry.get("interval_minutes", 1)), 1)
        return time.time() + interval_minutes * 60

    def _acquire_slot(self, entry: dict[str, Any], pane_id: str) -> bool:
        """セマフォスロットを取得する。取得できない場合は今回の送信をスキップして False を返す。

        Returns True if execution should proceed, False if it should be skipped.
        """
        assert self._semaphore is not None
        name = str(entry.get("name", ""))

        elapsed = self._semaphore.slot_elapsed(pane_id)
        if elapsed is not None:
            if elapsed < self._semaphore.slot_timeout:
                log.info(
                    "[%s] 前回の実行が完了待ちです (経過 %.0f秒 / 猶予 %d秒)。"
                    "30秒後に再試行します。",
                    name, elapsed, self._semaphore.slot_timeout,
                )
                self._update_entry(str(entry.get("id", "")), next_run_at=time.time() + 30)
                return False
            else:
                log.warning(
                    "[%s] 猶予時間 (%d秒) を超過。スロットを強制解放します。",
                    name, self._semaphore.slot_timeout,
                )
                if self._slot_monitor is not None:
                    self._slot_monitor.untrack(pane_id)
                self._semaphore.release(pane_id)

        if self._is_in_cooldown(entry, pane_id):
            return False

        if not self._semaphore.acquire(pane_id):
            log.warning(
                "[%s] 同時実行数が上限 (%d) に達しています。今回の送信をスキップします。",
                name, self._semaphore.max_concurrent,
            )
            print(
                f"[kiro-loop] [{name}] 同時実行数が上限に達しています。今回はスキップします。",
                file=sys.stderr, flush=True,
            )
            self._update_entry(str(entry.get("id", "")), next_run_at=self._next_run_at_for_entry(entry))
            return False

        return True

    def _dispatch_prompt(self, entry: dict[str, Any], pane_id: str | None) -> None:
        """プロンプトを送信し、失敗時は再起動する。"""
        name = str(entry.get("name", ""))
        prompt_id = str(entry.get("id", ""))
        prompt = str(entry.get("prompt", ""))
        should_clear = bool(entry.get("_should_clear", False))
        fresh_context_interval = entry.get("fresh_context_interval_minutes")

        log.info("[%s] プロンプトを実行します。", name)
        try:
            if should_clear:
                log.info("[%s] fresh_context: コンテキストをクリアします。", name)
                if not self._session_mgr.send_prompt(prompt_id, "/clear"):
                    log.warning("[%s] /clear の送信に失敗しました。スキップします。", name)
                    self._release_slot(pane_id)
                    return
                time.sleep(2)
                if fresh_context_interval is not None:
                    new_next_clear_at = time.time() + (int(fresh_context_interval) * 60)
                    self._update_entry(str(entry.get("id", "")), next_clear_at=new_next_clear_at)

            ok = self._session_mgr.send_prompt(prompt_id, prompt)
            if ok:
                if self._slot_monitor is not None and pane_id:
                    self._slot_monitor.track(pane_id)
            else:
                self._release_slot(pane_id)
                if not self._stop_event.is_set():
                    log.warning("[%s] 送信失敗。ペイン再起動を試みます。", name)
                    try:
                        self._session_mgr.restart_pane(prompt_id)
                    except RuntimeError as exc:
                        log.error("[%s] 再起動失敗: %s", name, exc)
        except Exception as exc:
            self._release_slot(pane_id)
            log.error("[%s] 予期しないエラー: %s", name, exc, exc_info=True)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_loop,
            name="periodic-scheduler",
            daemon=True,
        )
        self._thread.start()
        log.info("定期スケジューラを開始しました。")

    def _run_loop(self) -> None:
        while not self._stop_event.wait(1):
            now = time.time()
            with self._lock:
                entries = [e.copy() for e in self._entries]

            for entry in entries:
                if not entry.get("enabled", True):
                    continue
                if now < float(entry.get("next_run_at", now)):
                    continue

                name = str(entry.get("name", ""))
                prompt_id = str(entry.get("id", ""))
                exclude_from_concurrency = bool(entry.get("exclude_from_concurrency", False))

                fresh_context = bool(entry.get("fresh_context", False))
                fresh_context_interval = entry.get("fresh_context_interval_minutes")

                should_clear = False
                if fresh_context:
                    if fresh_context_interval is not None:
                        next_clear_at = float(entry.get("next_clear_at") or 0)
                        if now >= next_clear_at:
                            should_clear = True
                    else:
                        should_clear = True
                # Stash should_clear in entry copy for _acquire_slot / _dispatch_prompt
                entry["_should_clear"] = should_clear

                if not self._session_mgr.ensure_session(prompt_id, name):
                    log.warning("[%s] 対応セッションの準備に失敗したため今回の送信をスキップします。", name)
                else:
                    pane_id: str | None = None
                    if self._semaphore is not None and not exclude_from_concurrency:
                        pane_id = self._session_mgr.get_pane_id(prompt_id)
                        if pane_id and not self._acquire_slot(entry, pane_id):
                            continue

                    self._dispatch_prompt(entry, pane_id)

                self._update_entry(str(entry.get("id", "")), next_run_at=self._next_run_at_for_entry(entry))

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# インタラクティブコマンドループ
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
コマンド一覧:
  status                          実行状態を表示
  ls                              管理下のセッション一覧を表示
  send <target> <text>            管理下のセッションにテキストを送信
                                  target: pane ID (%12)、tmux セッション名、またはプロンプト名
                                  例: send %12 status確認してください
                                  例: send my-prompt コードをレビューしてください
  prompt-add <interval> <prompt>  定期プロンプトを追加 (interval は分単位の整数)
  prompt-add <name> <interval> <prompt>
                                  名前付きで定期プロンプトを追加
  prompt-list                     定期プロンプト設定を表示
  prompt-remove <index>           指定インデックスの定期プロンプトを削除
  help                            このヘルプを表示
  quit / exit                     終了"""


def command_loop(
    session_mgr: SessionManager,
    scheduler: PeriodicScheduler,
    stop_event: threading.Event,
    config_path: Path,
) -> None:
    """stdin からコマンドを読んで定期プロンプト設定を管理する（メインスレッドで実行）。"""
    target_name = session_mgr.get_target_name()
    target_path = session_mgr.get_target_path()

    print("定期プロンプトが実行中です。'help' でコマンド一覧を表示します。", flush=True)
    print(f"設定ファイル: {config_path}", flush=True)

    while not stop_event.is_set():
        try:
            try:
                line = input("> ")
            except EOFError:
                # stdin が閉じられた（パイプ終端など）
                break

            line = line.strip()
            if not line:
                continue

            parts = line.split(maxsplit=2)
            cmd = parts[0].lower()

            if cmd in ("help", "h", "?"):
                print(_HELP_TEXT, flush=True)

            elif cmd == "status":
                target_label, target_dir, total_count, alive_count = session_mgr.get_status()
                print(f"target: {target_label}", flush=True)
                print(f"path: {target_dir}", flush=True)
                print(f"sessions: {alive_count}/{total_count} alive", flush=True)

                prompt_statuses = session_mgr.list_prompt_statuses()
                if not prompt_statuses:
                    print("  (プロンプトセッションは未作成)", flush=True)
                else:
                    for prompt_name, prompt_id, is_alive, tmux_name, pane_target in prompt_statuses:
                        state = "alive" if is_alive else "dead"
                        print(
                            f"  - [{state}] {prompt_name} (id={prompt_id}, tmux={tmux_name}, pane={pane_target})",
                            flush=True,
                        )

            elif cmd == "ls":
                prompt_statuses = session_mgr.list_prompt_statuses()
                if not prompt_statuses:
                    print("  (管理下のセッションはありません)", flush=True)
                else:
                    col_name = max(len(p[0]) for p in prompt_statuses)
                    col_name = max(col_name, 12)
                    col_tmux = max(len(p[3]) for p in prompt_statuses)
                    col_tmux = max(col_tmux, 10)
                    header = f"  {'プロンプト名':<{col_name}}  {'pane':>6}  {'状態':<6}  tmux セッション"
                    print(header, flush=True)
                    print(f"  {'-' * (col_name + col_tmux + 26)}", flush=True)
                    for prompt_name, _, is_alive, tmux_name, pane_target in prompt_statuses:
                        state = "alive" if is_alive else "dead"
                        pane_str = pane_target or "-"
                        print(
                            f"  {prompt_name:<{col_name}}  {pane_str:>6}  {state:<6}  {tmux_name}",
                            flush=True,
                        )

            elif cmd == "send":
                args = line.split(maxsplit=2)
                if len(args) < 3:
                    print("使い方: send <target> <text>", flush=True)
                    print("  target: pane ID (%N)、tmux セッション名、またはプロンプト名", flush=True)
                    continue

                target = args[1].strip()
                send_text = args[2].strip()
                # クォート除去
                if (
                    len(send_text) >= 2
                    and send_text[0] == send_text[-1]
                    and send_text[0] in ('"', "'")
                ):
                    send_text = send_text[1:-1].strip()

                if not target:
                    print("target が空です。", flush=True)
                    continue

                if not send_text:
                    print("text が空です。", flush=True)
                    continue

                pane_id = session_mgr.resolve_managed_pane(target)
                if pane_id is None:
                    print(f"管理下のセッションが見つかりません: '{target}'", flush=True)
                    print("  'ls' で管理下のセッション一覧を確認してください。", flush=True)
                    continue

                ok, err = _send_to_pane(pane_id, send_text)
                if ok:
                    print(f"送信しました: pane={pane_id}", flush=True)
                else:
                    print(f"送信に失敗しました: {err}", flush=True)

            elif cmd == "prompt-add":
                args = line.split(maxsplit=3)
                if len(args) < 3:
                    print(
                        "使い方: prompt-add <interval_minutes> <prompt>\n"
                        "        prompt-add <name> <interval_minutes> <prompt>",
                        flush=True,
                    )
                else:
                    name_override: str | None = None
                    interval_text = ""
                    prompt_parts: list[str] = []

                    # 形式A: prompt-add <interval> <prompt>
                    # 形式B: prompt-add <name> <interval> <prompt>
                    try:
                        int(args[1])
                        interval_text = args[1]
                        prompt_parts = args[2:]
                    except ValueError:
                        if len(args) < 4:
                            print(
                                "使い方: prompt-add <interval_minutes> <prompt>\n"
                                "        prompt-add <name> <interval_minutes> <prompt>",
                                flush=True,
                            )
                            continue
                        name_override = args[1]
                        interval_text = args[2]
                        prompt_parts = args[3:]

                    try:
                        interval = int(interval_text)
                        if interval < 1:
                            raise ValueError()
                    except ValueError:
                        print("interval_minutes は 1 以上の整数を指定してください。", flush=True)
                        continue

                    prompt_text = " ".join(prompt_parts).strip()
                    if not prompt_text:
                        print("prompt が空です。", flush=True)
                        continue

                    # 先頭と末尾が同じ引用符なら外す
                    if (
                        len(prompt_text) >= 2
                        and prompt_text[0] == prompt_text[-1]
                        and prompt_text[0] in ('"', "'")
                    ):
                        prompt_text = prompt_text[1:-1].strip()

                    if not prompt_text:
                        print("prompt が空です。", flush=True)
                        continue

                    ws_prompts = load_prompt_config(target_path)                    
                    ws_prompts.append({
                        "id": str(uuid.uuid4()),
                        "name": name_override or prompt_text[:40],
                        "prompt": prompt_text,
                        "interval_minutes": interval,
                        "enabled": True,
                    })

                    if save_prompt_config(target_path, ws_prompts):
                        scheduler.set_entries(ws_prompts)
                        print("定期プロンプトを追加しました。", flush=True)

            elif cmd == "prompt-list":
                args = line.split(maxsplit=1)
                if len(args) >= 2:
                    print("使い方: prompt-list", flush=True)
                    continue

                ws_prompts = load_prompt_config(target_path)
                print(f"[{target_name}] {target_path}", flush=True)
                if not ws_prompts:
                    print("  (定期プロンプトは未設定)", flush=True)
                    continue

                for idx, p in enumerate(ws_prompts, start=1):
                    enabled = p.get("enabled", True)
                    cron = str(p.get("cron", "")).strip()
                    run_immediately = bool(
                        p.get("run_immediately_on_startup", p.get("run_immediately", False))
                    )
                    prompt_text = str(p.get("prompt", "")).replace("\n", " ")
                    short = prompt_text[:80] + ("..." if len(prompt_text) > 80 else "")
                    flag = "on" if enabled else "off"
                    immediate_note = " (起動時即実行)" if run_immediately else ""
                    if cron:
                        schedule_note = f'cron "{cron}"'
                    else:
                        interval = p.get("interval_minutes", "?")
                        schedule_note = f"{interval}分"
                    print(f"  {idx:>2}. [{flag}] {schedule_note}{immediate_note}: {short}", flush=True)

            elif cmd == "prompt-remove":
                args = line.split(maxsplit=1)
                if len(args) < 2:
                    print("使い方: prompt-remove <index>", flush=True)
                else:
                    index_text = args[1]
                    ws_prompts = load_prompt_config(target_path)
                    if not ws_prompts:
                        print("削除対象がありません。", flush=True)
                        continue
                    try:
                        index = int(index_text)
                    except ValueError:
                        print("index は整数を指定してください。", flush=True)
                        continue

                    if index < 1 or index > len(ws_prompts):
                        print(f"インデックスは 1 から {len(ws_prompts)} の範囲で指定してください。", flush=True)
                        continue

                    removed = ws_prompts.pop(index - 1)
                    if save_prompt_config(target_path, ws_prompts):
                        scheduler.set_entries(ws_prompts)
                        short = str(removed.get("prompt", ""))[:60]
                        print(f"削除しました: {short}", flush=True)

            elif cmd in ("quit", "exit", "q"):
                print("終了します。", flush=True)
                stop_event.set()
                break

            else:
                print(f"不明なコマンド: '{cmd}'。'help' でコマンド一覧を表示します。", flush=True)

        except KeyboardInterrupt:
            break

    log.info("コマンドループを終了しました。")


# ---------------------------------------------------------------------------
# セッション監視ループ（別スレッド）
# ---------------------------------------------------------------------------

def _monitor_loop(session_mgr: SessionManager, stop_event: threading.Event) -> None:
    """死んだセッションを定期的に検出して再起動する。"""
    while not stop_event.wait(10):
        session_mgr.restart_if_dead()
        session_mgr.write_state()


# ---------------------------------------------------------------------------
# シグナルハンドラ / グローバル cleanup
# ---------------------------------------------------------------------------

_session_mgr_ref: SessionManager | None = None
_scheduler_ref: PeriodicScheduler | None = None
_slot_monitor_ref: SlotMonitor | None = None
_stop_event_ref: threading.Event | None = None


def _cleanup() -> None:
    if _scheduler_ref is not None:
        _scheduler_ref.stop()
    if _slot_monitor_ref is not None:
        _slot_monitor_ref.stop()
    if _session_mgr_ref is not None:
        _session_mgr_ref.stop()


def _signal_handler(sig: int, frame: Any) -> None:
    sig_name = signal.Signals(sig).name
    log.info("シグナル %s を受信しました。終了します。", sig_name)
    if _stop_event_ref is not None:
        _stop_event_ref.set()
    _cleanup()
    sys.exit(0)


# ---------------------------------------------------------------------------
# tmux 自動アタッチ
# ---------------------------------------------------------------------------

def _auto_attach_tmux_if_needed(args: argparse.Namespace) -> None:
    """tmux 外で起動された場合、tmux セッション内へ自己再実行して表示を有効化する。"""
    if args.controller_mode or args.no_auto_attach:
        return
    if os.environ.get("TMUX"):
        return

    tmux_bin = shutil.which("tmux")
    if tmux_bin is None:
        return

    target_path = Path.cwd()
    instance_id = args.instance_id or uuid.uuid4().hex[:8]
    session_name = _tmux_session_name(target_path, instance_id)
    script_path = Path(__file__).resolve()
    command_parts = [
        shlex.quote(sys.executable),
        shlex.quote(str(script_path)),
    ]

    command_parts.extend(["--instance-id", shlex.quote(instance_id)])

    if args.log_level:
        command_parts.extend(["--log-level", shlex.quote(args.log_level)])

    if args.split_direction:
        command_parts.extend(["--split-direction", shlex.quote(args.split_direction)])

    command_parts.append("--controller-mode")
    controller_cmd = " ".join(command_parts)

    has_session = (
        subprocess.run(
            [tmux_bin, "has-session", "-t", session_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    )

    if not has_session:
        log.info("tmux 外で起動されたため `%s` を新規作成してアタッチします。", session_name)
        os.execvp(
            tmux_bin,
            [
                tmux_bin,
                "new-session",
                "-s",
                session_name,
                "-c",
                str(target_path),
                controller_cmd,
            ],
        )

    create_window = subprocess.run(
        [
            tmux_bin,
            "new-window",
            "-t",
            session_name,
            "-n",
            "kiro-loop",
            "-c",
            str(target_path),
            controller_cmd,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if create_window.returncode != 0:
        log.warning("既存セッションへの controller ウィンドウ追加に失敗しました。")

    log.info("tmux 外で起動されたため `%s` へ自動アタッチします。", session_name)
    os.execvp(
        tmux_bin,
        [tmux_bin, "attach-session", "-t", session_name],
    )


# ---------------------------------------------------------------------------
# send/ls サブコマンド: tmux ヘルパー
# ---------------------------------------------------------------------------

def _session_name_exists(session: str) -> bool:
    return _tmux_cmd("has-session", "-t", session).returncode == 0


def _capture_pane(target: str) -> str:
    """セッション名またはペイン ID でペイン内容を取得する。"""
    r = _tmux_cmd("capture-pane", "-p", "-t", target)
    return r.stdout if r.returncode == 0 else ""



def _pane_has_prompt(content: str) -> bool:
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return False
    return bool(_PROMPT_RE.search("\n".join(lines[-3:])))


def _get_session_pane_cwd(session: str) -> str:
    r = _tmux_cmd("display-message", "-p", "-t", session, "#{pane_current_path}")
    return r.stdout.strip() if r.returncode == 0 else ""


def _find_kiro_pane_in_session(session: str) -> str | None:
    """セッション内の kiro-cli ペインを探してペイン ID を返す。

    pane_current_command で python/python3（コントローラー）を除外し、
    残りの中から kiro プロンプトが表示されているペインを優先して返す。
    """
    r = _tmux_cmd(
        "list-panes", "-t", session, "-F",
        "#{pane_id}\t#{pane_current_command}\t#{pane_dead}",
    )
    if r.returncode != 0:
        return None

    non_controller: list[str] = []
    all_alive: list[str] = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pane_id, command, dead = parts[0], parts[1], parts[2]
        if dead == "1":
            continue
        all_alive.append(pane_id)
        if not command.startswith("python"):
            non_controller.append(pane_id)

    # コントローラー以外でプロンプトが出ているペインを優先
    for pane_id in non_controller:
        if _pane_has_prompt(_capture_pane(pane_id)):
            return pane_id

    if non_controller:
        return non_controller[0]

    # フォールバックなし — コントローラーを kiro ペインと誤認しないため None を返す
    return None


def _resolve_target_pane(target: str) -> str | None:
    """セッション名またはペイン ID から kiro-cli ペイン ID を解決する。

    target が '%' で始まる場合はそのまま使用し、セッション名の場合は
    _find_kiro_pane_in_session() で kiro ペインを探す。
    """
    if target.startswith("%"):
        r = _tmux_cmd("display-message", "-p", "-t", target, "#{pane_id}")
        return target if r.returncode == 0 else None
    return _find_kiro_pane_in_session(target)


def _wait_for_session_prompt(session: str, timeout: int, label: str) -> bool:
    """セッション名でプロンプト待機（ensure_kiro_session の起動待ち用）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _session_name_exists(session):
            print(f"[kiro-loop] ERROR: セッション '{session}' が消えました", file=sys.stderr)
            return False
        if _pane_has_prompt(_capture_pane(session)):
            return True
        time.sleep(0.5)
    print(f"[kiro-loop] WARN: {label} がタイムアウトしました ({timeout}秒)", file=sys.stderr)
    return False


def _set_session_last_active(session: str) -> None:
    _tmux_cmd("set-environment", "-t", session, _ENV_LAST_ACTIVE, str(int(time.time())))


def ensure_kiro_session(session: str, work_dir: Path | None, kiro_bin: str) -> bool:
    """kiro-cli が起動中の tmux セッションを確保する。"""
    kiro_cmd = shlex.join([kiro_bin, "chat", "--trust-all-tools"])
    cwd_str = str(work_dir) if work_dir else None

    if not _session_name_exists(session):
        effective_cwd = cwd_str or str(Path.home())
        print(f"[kiro-loop] tmux セッション '{session}' を作成します (cwd={effective_cwd})", file=sys.stderr)
        r = _tmux_cmd("new-session", "-d", "-s", session, "-c", effective_cwd, kiro_cmd)
        if r.returncode != 0:
            print(f"[kiro-loop] ERROR: セッション作成に失敗しました: {r.stderr.strip()}", file=sys.stderr)
            return False
        print("[kiro-loop] kiro-cli 起動待ち...", file=sys.stderr)
        ok = _wait_for_session_prompt(session, _SEND_STARTUP_TIMEOUT, "起動")
        if ok:
            print("[kiro-loop] kiro-cli 起動完了", file=sys.stderr)
            _set_session_last_active(session)
        return ok

    pane_cwd = _get_session_pane_cwd(session)
    kiro_alive = _pane_has_prompt(_capture_pane(session))

    if kiro_alive and (cwd_str is None or pane_cwd == cwd_str):
        print(f"[kiro-loop] 既存セッション '{session}' を再利用します (cwd={pane_cwd})", file=sys.stderr)
        return True

    reason = f"cwd 変更 ({pane_cwd} → {cwd_str})" if kiro_alive else "kiro-cli が終了していました"
    print(f"[kiro-loop] kiro-cli を再起動します ({reason})", file=sys.stderr)

    effective_cwd = cwd_str or pane_cwd or str(Path.home())
    r = _tmux_cmd("respawn-pane", "-k", "-t", session, "-c", effective_cwd, kiro_cmd)
    if r.returncode != 0:
        print(f"[kiro-loop] ERROR: respawn-pane に失敗しました: {r.stderr.strip()}", file=sys.stderr)
        return False
    print("[kiro-loop] kiro-cli 起動待ち...", file=sys.stderr)
    ok = _wait_for_session_prompt(session, _SEND_STARTUP_TIMEOUT, "起動")
    if ok:
        print("[kiro-loop] kiro-cli 起動完了", file=sys.stderr)
        _set_session_last_active(session)
    return ok


def send_prompt_to_session(session: str, text: str) -> bool:
    """テキストを tmux セッションの kiro-cli ペインに送信する（応答待ちはしない）。

    セッション名が渡された場合は _resolve_target_pane() で kiro-cli ペインを
    特定してから送信する（コントローラーペインへの誤送信を防ぐ）。
    """
    pane_id = _resolve_target_pane(session)
    if pane_id is None:
        print(f"[kiro-loop] ERROR: kiro-cli ペインが見つかりません (target={session})", file=sys.stderr)
        return False

    single_line = " ".join(text.splitlines()).strip()
    short = single_line[:80] + ("..." if len(single_line) > 80 else "")
    print(f"[kiro-loop] 送信: {short} (pane={pane_id})", file=sys.stderr)

    r = _tmux_cmd("send-keys", "-t", pane_id, "--", single_line, "Enter")
    if r.returncode != 0:
        print(f"[kiro-loop] ERROR: send-keys に失敗しました: {r.stderr.strip()}", file=sys.stderr)
        return False

    return True


def _resolve_prompt_text(prompt_arg: str, cwd: Path) -> str:
    """プロンプト引数を解決して送信テキストを返す。

    解決順序:
    1. ファイルとして存在する → kiro-cli にファイル内容の実行を指示
    2. .kiro/kiro-loop.yml の定期プロンプト名と一致する → そのプロンプトテキスト
    3. そのまま自然文として使用
    """
    candidate = Path(prompt_arg).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    if candidate.is_file():
        content = candidate.read_text(encoding="utf-8").strip()
        return f"以下のファイルの内容を読んで実行してください:\n\n{content}"

    ws_prompts = load_prompt_config(str(cwd))
    for p in ws_prompts:
        if p.get("name") == prompt_arg:
            return str(p.get("prompt", "")).strip()

    return prompt_arg


# ---------------------------------------------------------------------------
# ls / send サブコマンド
# ---------------------------------------------------------------------------

def cmd_ls() -> None:
    """kiro-loop send -s PANE_ID で指定するペインIDをプロンプト名付きで表示する。"""
    states = _read_all_states()
    if states:
        all_sessions = [s for st in states for s in st.get("sessions", [])]
        col_name = max((len(s.get("name", "")) for s in all_sessions), default=10)
        col_name = max(col_name, 12)
        print(f"{'プロンプト名':<{col_name}}  {'pane':>6}  状態")
        print("-" * (col_name + 16))
        for state in states:
            for s in state.get("sessions", []):
                name = str(s.get("name", ""))
                pane = str(s.get("pane", "")) or "-"
                alive = "alive" if s.get("alive") else "dead"
                print(f"{name:<{col_name}}  {pane:>6}  {alive}")
        return

    # デーモンが動いていない場合: tmuxから全ペインを直接取得
    tmux_bin = shutil.which("tmux")
    if tmux_bin is None:
        print("[kiro-loop] ERROR: tmux が見つかりません。", file=sys.stderr)
        return

    result = subprocess.run(
        [tmux_bin, "list-sessions", "-F", "#{session_name}"],
        check=False,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0 or not result.stdout.strip():
        print("実行中の kiro セッションはありません。")
        return

    kiro_sessions = [s.strip() for s in result.stdout.splitlines() if s.strip().startswith("kiro")]

    if not kiro_sessions:
        print("実行中の kiro セッションはありません。")
        return

    # セッション内の全非コントローラーペインを列挙
    rows: list[tuple[str, str]] = []
    for session in kiro_sessions:
        r = _tmux_cmd(
            "list-panes", "-t", session, "-F",
            "#{pane_id}\t#{pane_current_command}\t#{pane_dead}",
        )
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            pane_id, command, dead = parts[0], parts[1], parts[2]
            if dead == "1" or command.startswith("python"):
                continue
            rows.append((pane_id, session))

    if not rows:
        print("実行中の kiro ペインはありません。")
        return

    col_sess = max(len(r[1]) for r in rows)
    col_sess = max(col_sess, 20)
    print(f"{'pane':>6}  {'セッション'}  ")
    print("-" * (col_sess + 10))
    for pane_id, session in rows:
        print(f"{pane_id:>6}  {session}")
    print()
    print("送信: kiro-loop send -s PANE_ID テキスト")
    print("例:   kiro-loop send -s %12 確認してください")


# ---------------------------------------------------------------------------
# デーモン状態ファイルのユーティリティ
# ---------------------------------------------------------------------------

def _read_all_states() -> list[dict[str, Any]]:
    """生きている kiro-loop デーモンの状態ファイルを全て読んで返す。"""
    if not _STATE_DIR.exists():
        return []
    results = []
    for f in sorted(_STATE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    results.append(data)
                except ProcessLookupError:
                    f.unlink(missing_ok=True)
                except PermissionError:
                    results.append(data)
        except Exception:
            pass
    return results


def _pane_is_busy(pane_id: str) -> bool:
    """スロットファイルを参照してペインが処理中かを判断する。

    スロットファイルが存在しない場合（max_concurrent=0 など）は False を返す。
    """
    return GlobalSemaphore.is_busy(pane_id)


def _find_managing_daemon(pane_id: str) -> dict[str, Any] | None:
    """指定ペインを管理しているデーモンの状態データを返す。"""
    for state in _read_all_states():
        for session in state.get("sessions", []):
            if session.get("pane") == pane_id:
                return state
    return None


def _try_acquire_slot_for_send(pane_id: str) -> bool:
    """cmd_send 用のスロットファイルを書き込む（max_concurrent > 0 のデーモン管理下のみ）。

    スロットファイルに管理デーモンの PID を設定することで、デーモンの SlotMonitor が
    kiro-cli のプロンプト復帰を検知した際に適切に解放できるようにする。
    max_concurrent=0 のデーモンはスロットを使わないため書き込まない（放置ファイル防止）。
    同時実行数上限に達している場合は False を返す。
    """
    daemon_state = _find_managing_daemon(pane_id)
    if daemon_state is None:
        return True

    cwd = daemon_state.get("cwd", "")
    if not cwd:
        return True

    daemon_pid = int(daemon_state.get("pid", 0))
    if daemon_pid <= 0:
        return True

    try:
        config, _, _ = load_config(Path(cwd))
        max_concurrent = int(config.get("max_concurrent", 0))
        if max_concurrent <= 0:
            return True
        slot_timeout = int(config.get("slot_timeout_seconds", _DEFAULT_SLOT_TIMEOUT))
        cooldown = int(config.get("cooldown_seconds", 0))
    except Exception:
        return True

    semaphore = GlobalSemaphore(max_concurrent, slot_timeout, cooldown)
    if semaphore.acquire(pane_id, pid=daemon_pid):
        log.debug("cmd_send: スロットを取得しました (pane=%s, daemon_pid=%d)", pane_id, daemon_pid)
        return True
    else:
        log.warning("cmd_send: 同時実行数が上限 (%d) に達しています (pane=%s)", max_concurrent, pane_id)
        return False


def cmd_slot_release() -> None:
    """$TMUX_PANE に対応するセマフォスロットを解放する（kiro-cli agent hook から呼び出される）。"""
    pane_env = os.environ.get("TMUX_PANE", "")
    if not pane_env:
        sys.exit(0)
    cooldown_seconds = 0
    try:
        config, _, _ = load_config(Path.cwd())
        cooldown_seconds = int(config.get("cooldown_seconds", 0))
    except Exception:
        pass
    GlobalSemaphore(0, cooldown_seconds=cooldown_seconds).release(pane_env)
    sys.exit(0)


def cmd_send(args: argparse.Namespace, cwd: Path) -> None:
    """プロンプトを tmux セッションの kiro-cli に送信する。"""
    kiro_bin = shutil.which("kiro-cli")
    if kiro_bin is None:
        print("[kiro-loop] ERROR: kiro-cli が PATH に見つかりません。", file=sys.stderr)
        sys.exit(1)

    prompt_arg = " ".join(args.prompt).strip()
    if not prompt_arg:
        print("[kiro-loop] ERROR: プロンプトが空です。", file=sys.stderr)
        sys.exit(1)

    target = getattr(args, "session", None)

    # --session 未指定時は状態ファイルから送信先ペインを自動解決する
    if not target:
        states = _read_all_states()
        alive_sessions = [
            s for st in states for s in st.get("sessions", [])
            if s.get("alive") and s.get("pane")
        ]
        if len(alive_sessions) == 1:
            target = alive_sessions[0]["pane"]
            print(
                f"[kiro-loop] 送信先ペインを自動解決: {target} ({alive_sessions[0].get('name')})",
                file=sys.stderr,
            )
        elif len(alive_sessions) > 1:
            print("[kiro-loop] 複数のペインが動作中です。-s PANE_ID で送信先を指定してください:", file=sys.stderr)
            for s in alive_sessions:
                print(f"  {s['pane']}  ({s.get('name', '')})", file=sys.stderr)
            print("例: kiro-loop send -s %12 テキスト", file=sys.stderr)
            sys.exit(1)

    if not target:
        target = _DEFAULT_SEND_SESSION

    work_dir: Path | None = None
    raw_dir = getattr(args, "dir", None)
    if raw_dir:
        work_dir = Path(raw_dir).expanduser().resolve()
        if not work_dir.is_dir():
            print(f"[kiro-loop] ERROR: ディレクトリが存在しません: {work_dir}", file=sys.stderr)
            sys.exit(1)

    prompt_text = _resolve_prompt_text(prompt_arg, cwd)
    print(f"[kiro-loop] 送信するプロンプト:\n{prompt_text}\n", file=sys.stderr)

    # ターゲットペインを解決する。
    # 既に kiro ペインが存在する場合は ensure_kiro_session を呼ばない。
    # kiro-cli が処理中（プロンプト非表示）でも誤って再起動しないようにするため。
    if target.startswith("%"):
        r = _tmux_cmd("display-message", "-p", "-t", target, "#{pane_id}")
        if r.returncode != 0:
            print(f"[kiro-loop] ERROR: ペイン '{target}' が見つかりません。", file=sys.stderr)
            sys.exit(1)
        send_target = target
    elif _session_name_exists(target):
        existing_pane = _find_kiro_pane_in_session(target)
        if existing_pane:
            print(f"[kiro-loop] セッション '{target}' の kiro ペイン {existing_pane} を使用します。", file=sys.stderr)
            send_target = existing_pane
        else:
            print(f"[kiro-loop] ERROR: セッション '{target}' に kiro ペインが見つかりません。", file=sys.stderr)
            print("  kiro-loop ls で確認するか、kiro-loop send でスタンドアロンセッションを作成してください。", file=sys.stderr)
            sys.exit(1)
    else:
        # セッションが存在しない場合のみ新規作成
        if not ensure_kiro_session(target, work_dir, kiro_bin):
            sys.exit(1)
        resolved = _resolve_target_pane(target)
        if resolved is None:
            print(f"[kiro-loop] ERROR: kiro-cli ペインが見つかりません (session={target})。", file=sys.stderr)
            sys.exit(1)
        send_target = resolved

    # kiro-cli が処理中なら送信を拒否する
    # スロットファイルがある場合はそちらを優先、なければプロンプト検出にフォールバック
    if _pane_is_busy(send_target) or not _pane_has_prompt(_capture_pane(send_target)):
        print(
            f"[kiro-loop] ERROR: ペイン {send_target} は現在処理中です。完了後に再送してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    # 管理デーモンが max_concurrent > 0 の場合はスロットを取得してから送信する。
    # これにより送信後の処理中にデーモンが別のプロンプトを送り込むのを防ぐ。
    # スロットは SlotMonitor がプロンプト復帰を検知した際に自動解放する。
    if not _try_acquire_slot_for_send(send_target):
        print(
            "[kiro-loop] ERROR: 同時実行数が上限に達しています。他のペインの処理が完了してから再送してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    if send_prompt_to_session(send_target, prompt_text):
        print("[kiro-loop] 完了しました", file=sys.stderr)
    else:
        print("[kiro-loop] WARN: 応答待ちがタイムアウトしました", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="kiro-cli を定期プロンプトで自動操作するスクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使い方:
  kiro-loop                              # デーモンモードで起動
  kiro-loop ls                           # kiro 関連セッションを一覧表示
  kiro-loop send "プロンプト"             # セッションにプロンプトを送信
  kiro-loop send task.md                 # ファイル内容を読んで実行
  kiro-loop send "MR コメント返答"        # kiro-loop.yaml の定期プロンプト名で送信
  kiro-loop send -s SESSION "プロンプト"  # 指定セッションに送信
""",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル (デフォルト: INFO)",
    )
    parser.add_argument(
        "--split-direction",
        choices=["horizontal", "vertical"],
        help="tmux 分割方向 (horizontal: 左右 / vertical: 上下)",
    )
    parser.add_argument(
        "--no-auto-attach",
        action="store_true",
        help="tmux 外で起動時に自動アタッチしない",
    )
    parser.add_argument(
        "--controller-mode",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--instance-id",
        help=argparse.SUPPRESS,
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    subparsers.add_parser("ls", help="kiro 関連の tmux セッションを一覧表示する")

    subparsers.add_parser(
        "slot-release",
        help=argparse.SUPPRESS,  # agent hook 専用コマンドのためヘルプ非表示
    )

    send_parser = subparsers.add_parser(
        "send",
        help="tmux セッションの kiro-cli にプロンプトを送信する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="プロンプトを kiro-cli tmux セッションに送信する",
        epilog=f"""
プロンプトの種類:
  自然文:                kiro-loop send "コードをレビューしてください"
  マークダウンファイル:   kiro-loop send task.md
  スケジュール済み名:     kiro-loop send "MR コメント返答"

セッションを指定しない場合は '{_DEFAULT_SEND_SESSION}' セッションを使用します。
""",
    )
    send_parser.add_argument(
        "prompt",
        nargs="+",
        metavar="PROMPT",
        help="送信するプロンプト（自然文、ファイルパス、またはスケジュール名）",
    )
    send_parser.add_argument(
        "--session", "-s",
        default=None,
        metavar="NAME",
        help=f"対象 tmux セッション名（省略時: '{_DEFAULT_SEND_SESSION}'）",
    )
    send_parser.add_argument(
        "--dir", "-d",
        default=None,
        metavar="DIR",
        help="作業ディレクトリ（省略時: カレントディレクトリ）",
    )

    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    cwd = Path.cwd()

    if args.subcommand == "ls":
        cmd_ls()
        return

    if args.subcommand == "slot-release":
        cmd_slot_release()
        return

    if args.subcommand == "send":
        cmd_send(args, cwd)
        return

    running_pid = _find_running_daemon(cwd)
    if running_pid is not None:
        log.info("既に実行中のプロセスがあります。起動をスキップします。", flush=True)
        sys.exit(0)

    # tmux 外で起動された場合、自己を tmux 内で再実行
    _auto_attach_tmux_if_needed(args)

    # 再度チェック（tmux 内での再起動後）
    running_pid = _find_running_daemon(cwd)
    if running_pid is not None:
        log.info("既に実行中のプロセスがあります。起動をスキップします。", flush=True)
        sys.exit(0)

    log_file = configure_file_logging()
    log.info("ファイルログを開始しました: %s", log_file)

    config, config_path, has_local_config = load_config(cwd)

    ws_config = _load_prompt_file_data(str(cwd))

    # kiro-cli 起動オプションの解決
    kiro_opts = config.get("kiro_options", {})
    if not isinstance(kiro_opts, dict):
        kiro_opts = {}

    if not has_local_config:
        ws_kiro_opts = ws_config.get("kiro_options", {})
        if isinstance(ws_kiro_opts, dict) and ws_kiro_opts:
            kiro_opts = ws_kiro_opts
            log.info(".kiro/kiro-loop.yml の kiro_options を使用します。")

    kiro_args: list[str] = []
    if kiro_opts.get("trust_all_tools", True):
        kiro_args.append("--trust-all-tools")
    if kiro_opts.get("resume", False):
        kiro_args.append("--resume")
    if kiro_opts.get("agent"):
        kiro_args.extend(["--agent", str(kiro_opts["agent"])])
    if kiro_opts.get("model"):
        kiro_args.extend(["--model", str(kiro_opts["model"])])
    for extra in kiro_opts.get("extra_args", []):
        kiro_args.append(str(extra))

    startup_timeout = int(config.get("startup_timeout", 60))
    response_timeout = int(config.get("response_timeout", 300))
    echo_output = bool(config.get("echo_output", False))
    split_direction = args.split_direction or str(config.get("split_direction", "horizontal"))
    if split_direction not in ("horizontal", "vertical"):
        log.warning("split_direction の値が不正なため horizontal を使用します: %s", split_direction)
        split_direction = "horizontal"

    entries: list[dict[str, Any]] = config.get("prompts", [])
    if not has_local_config:
        entries = load_vscode_periodic_prompts(cwd)

    if not entries:
        log.info("prompts が定義されていません。定期プロンプト未設定で起動します。")

    # 同時実行数制御の設定
    max_concurrent = int(config.get("max_concurrent", 0))
    slot_timeout_seconds = int(config.get("slot_timeout_seconds", 7200))
    cooldown_seconds = int(config.get("cooldown_seconds", 0))
    uses_user_agent = bool(kiro_opts.get("agent"))
    # uses_concurrency_agent: kiro-loop-concurrency agent を kiro-cli に注入するか
    # ユーザーが独自 agent を設定した場合は注入しないが、セマフォ制御は適用する
    uses_concurrency_agent = max_concurrent > 0 and not uses_user_agent

    semaphore: GlobalSemaphore | None = GlobalSemaphore(max_concurrent, slot_timeout_seconds, cooldown_seconds) if max_concurrent > 0 else None
    if max_concurrent > 0:
        if uses_user_agent:
            log.info(
                "同時実行数制御を有効にします (ペイン監視のみ): max_concurrent=%d, slot_timeout=%ds, cooldown=%ds",
                max_concurrent, slot_timeout_seconds, cooldown_seconds,
            )
        else:
            log.info(
                "同時実行数制御を有効にします: max_concurrent=%d, slot_timeout=%ds, cooldown=%ds",
                max_concurrent, slot_timeout_seconds, cooldown_seconds,
            )

    # グローバル参照（cleanup / シグナルハンドラ用）
    global _session_mgr_ref, _scheduler_ref, _slot_monitor_ref, _stop_event_ref

    stop_event = threading.Event()
    _stop_event_ref = stop_event

    instance_id = args.instance_id or uuid.uuid4().hex[:8]

    session_mgr = SessionManager(
        target_path=str(cwd),
        instance_id=instance_id,
        kiro_args_base=kiro_args,
        split_direction=split_direction,
        startup_timeout=startup_timeout,
        response_timeout=response_timeout,
        echo_output=echo_output,
        uses_concurrency_agent=uses_concurrency_agent,
    )
    _session_mgr_ref = session_mgr

    log.info("カレントディレクトリを起動対象に設定しました: %s", cwd)

    slot_monitor: SlotMonitor | None = SlotMonitor(semaphore, slot_timeout_seconds) if semaphore is not None else None
    _slot_monitor_ref = slot_monitor

    scheduler = PeriodicScheduler(session_mgr, entries, semaphore=semaphore, slot_monitor=slot_monitor)
    _scheduler_ref = scheduler

    # カレントディレクトリ配下の .kiro/kiro-loop.yml から定期プロンプトを読み込み
    ws_prompts = load_prompt_config(str(cwd))
    if ws_prompts:
        scheduler.set_entries(ws_prompts)

    # シグナルハンドラ登録
    # SIGHUP: ターミナルを閉じたとき / SIGTERM: kill / SIGINT: Ctrl+C
    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _signal_handler)

    atexit.register(_cleanup)

    # スケジューラ開始
    scheduler.start()

    # スロット監視スレッド起動（同時実行数制御が有効な場合のみ）
    if slot_monitor is not None:
        slot_monitor.start()

    # セッション監視スレッド起動
    monitor_thread = threading.Thread(
        target=_monitor_loop,
        args=(session_mgr, stop_event),
        name="session-monitor",
        daemon=True,
    )
    monitor_thread.start()

    log.info("実行中です。ターミナルを閉じるか 'quit' コマンドで終了します。")

    # コマンドループはメインスレッドで実行
    command_loop(session_mgr, scheduler, stop_event, config_path)

    # コマンドループ終了後のクリーンアップ
    stop_event.set()
    _cleanup()
    sys.exit(0)


if __name__ == "__main__":
    main()
