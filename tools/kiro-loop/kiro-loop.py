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


INSTANCE_FILE_NAME = "kiro-loop.pid"
LOG_FILE_NAME = "kiro-loop.log"


def _runtime_dir(base_path: Path) -> Path:
    return base_path / ".kiro"


def _instance_file(base_path: Path) -> Path:
    return _runtime_dir(base_path) / INSTANCE_FILE_NAME


def _log_file(base_path: Path) -> Path:
    return _runtime_dir(base_path) / LOG_FILE_NAME


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("不要ファイルの削除に失敗しました: %s (%s)", path, exc)


def _process_matches_target(pid: int, target_path: Path) -> bool:
    """プロセスが生きているか確認する（macOS / Linux 共通）。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 他ユーザー所有プロセスは「生きている」とみなす
        return True
    except OSError:
        return False

    # Linux: /proc/{pid}/cmdline でコマンドラインを確認
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.exists():
        try:
            cmdline_text = proc_cmdline.read_text(encoding="utf-8", errors="ignore").replace("\x00", " ")
        except OSError:
            return False
        if "kiro-loop.py" not in cmdline_text and "kiro-loop" not in cmdline_text:
            return False

    # Linux: /proc/{pid}/cwd でカレントディレクトリを確認
    proc_cwd = Path(f"/proc/{pid}/cwd")
    if proc_cwd.exists():
        try:
            return proc_cwd.resolve() == target_path.resolve()
        except OSError:
            pass

    # macOS / fallback: PID が生存していれば一致とみなす
    return True


def find_running_instance(target_path: Path) -> int | None:
    instance_file = _instance_file(target_path)
    if not instance_file.is_file():
        return None

    try:
        data = json.loads(instance_file.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
    except Exception:
        _safe_unlink(instance_file)
        return None

    if pid <= 0:
        _safe_unlink(instance_file)
        return None

    if _process_matches_target(pid, target_path):
        return pid

    _safe_unlink(instance_file)
    return None


def write_instance_file(target_path: Path) -> Path:
    runtime_dir = _runtime_dir(target_path)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    instance_file = _instance_file(target_path)
    payload = {
        "pid": os.getpid(),
        "cwd": str(target_path.resolve()),
        "started_at": int(time.time()),
        "script": str(Path(__file__).resolve()),
    }
    instance_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return instance_file


def configure_file_logging(target_path: Path) -> Path:
    log_file = _log_file(target_path)
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
    ファイルが存在しない場合は空の config とデフォルトパスを返す（終了しない）。
    """
    config_path = find_default_config(cwd)
    if config_path is None:
        default_path = cwd / "kiro-loop.yaml"
        log.info(
            "起動ディレクトリの設定ファイルが見つかりません。必要に応じて %s に保存されます。",
            default_path,
        )
        return {}, default_path, False

    log.info("設定ファイルを読み込みます: %s", config_path)
    return _load_config_file(config_path), config_path, True


# ---------------------------------------------------------------------------
# tmux セッション名の生成
# ---------------------------------------------------------------------------

def _sanitize_session_label(name: str) -> str:
    """tmux セッション名に使用できる文字列に変換する。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", name).strip("-_")
    return (cleaned or "target")[:24]


def _tmux_session_name(base_path: Path, instance_id: str) -> str:
    """実行インスタンスごとに独立した tmux セッション名を生成する。"""
    resolved = str(base_path.resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]
    label = _sanitize_session_label(base_path.name)
    short_id = re.sub(r"[^A-Za-z0-9]", "", instance_id)[:12] or "run"
    return f"kiro-{label}-{digest}-{short_id}"


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
    """起動ディレクトリ配下 .kiro/kiro-loop.yml 全体を辞書として読む。"""
    path = _prompt_file(base_path)
    if not path.is_file():
        return {}
    if yaml is None:
        log.warning("PyYAML がないため %s を読めません。", path)
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if isinstance(data, dict):
            return data
        log.warning("%s の形式が不正なため空設定として扱います。", path)
    except Exception as exc:
        log.error("%s の読み込みに失敗しました: %s", path, exc)
    return {}


def load_prompt_config(base_path: str) -> list[dict[str, Any]]:
    """起動ディレクトリ配下 .kiro/kiro-loop.yml から prompts を読む。"""
    path = _prompt_file(base_path)
    data = _load_prompt_file_data(base_path)
    prompts = data.get("prompts", [])
    if isinstance(prompts, list):
        return [p for p in prompts if isinstance(p, dict)]
    if data:
        log.warning("%s の prompts が配列ではありません。", path)
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
# tmux へのテキスト安全送信（シェルインジェクション回避）
# ---------------------------------------------------------------------------

def send_text_via_tmux(pane_target: str, text: str) -> tuple[bool, str]:
    """指定 pane へテキストを送信する。"""
    tmux_bin = shutil.which("tmux")
    if tmux_bin is None:
        return False, "tmux が PATH に見つかりません。"

    pane_check = subprocess.run(
        [tmux_bin, "display-message", "-p", "-t", pane_target, "#{pane_id}"],
        check=False,
        text=True,
        capture_output=True,
    )
    if pane_check.returncode != 0:
        err = (pane_check.stderr or "").strip() or "指定 pane が見つかりません。"
        return False, err

    buffer_name = f"kiro-loop-send-{uuid.uuid4().hex[:8]}"
    try:
        result = subprocess.run(
            [tmux_bin, "set-buffer", "-b", buffer_name, "--", text],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip() or "tmux set-buffer に失敗しました。"
            return False, err

        result = subprocess.run(
            [tmux_bin, "paste-buffer", "-t", pane_target, "-b", buffer_name],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip() or "tmux paste-buffer に失敗しました。"
            return False, err

        result = subprocess.run(
            [tmux_bin, "send-keys", "-t", pane_target, "Enter"],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip() or "tmux send-keys(Enter) に失敗しました。"
            return False, err

        return True, ""
    finally:
        subprocess.run(
            [tmux_bin, "delete-buffer", "-b", buffer_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )


# ---------------------------------------------------------------------------
# kiro-cli セッション管理
# ---------------------------------------------------------------------------

class KiroSession:
    """tmux 上で kiro-cli を分割ペイン起動し、プロンプトを送信するセッション。"""

    _layout_lock = threading.Lock()

    def __init__(
        self,
        cwd: str,
        kiro_args: list[str],
        tmux_session_name: str,
        split_direction: str = "horizontal",
        startup_timeout: int = 60,
        response_timeout: int = 300,
        echo_output: bool = False,
    ):
        self._cwd = cwd
        self._kiro_args = kiro_args
        self._tmux_session_name = tmux_session_name
        self._split_direction = "vertical" if str(split_direction).lower() == "vertical" else "horizontal"
        self._startup_timeout = startup_timeout
        self._response_timeout = response_timeout
        self._echo_output = echo_output
        self._pane_target: str | None = None
        self._tmux_bin: str | None = None
        self._layout_window_target: str | None = None
        self._layout_controller_pane: str | None = None
        self._active_session_name: str | None = None
        self._lock = threading.Lock()
        self._restart_lock = threading.Lock()

    @staticmethod
    def _session_from_window_target(window_target: str) -> str:
        """tmux の window ターゲットからセッション名を抽出する。"""
        if ":" in window_target:
            return window_target.split(":", 1)[0]
        return window_target

    # ------------------------------------------------------------------
    # レイアウト・ペイン操作ヘルパー
    # ------------------------------------------------------------------

    def _split_option(self) -> str:
        return "-v" if self._split_direction == "vertical" else "-h"

    def _layout_name(self) -> str:
        return "even-vertical" if self._split_direction == "vertical" else "even-horizontal"

    def _split_label(self) -> str:
        return "縦" if self._split_direction == "vertical" else "横"

    def _run_tmux(self, args: list[str], capture_output: bool = False) -> subprocess.CompletedProcess[str]:
        tmux_bin = self._tmux_bin or shutil.which("tmux")
        if tmux_bin is None:
            raise RuntimeError("tmux が PATH に見つかりません。`sudo apt install tmux` を実行してください。")
        self._tmux_bin = tmux_bin
        if capture_output:
            return subprocess.run(
                [tmux_bin, *args],
                check=False,
                text=True,
                capture_output=True,
            )
        return subprocess.run(
            [tmux_bin, *args],
            check=False,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _has_session(self, session_name: str) -> bool:
        result = self._run_tmux(["has-session", "-t", session_name], capture_output=True)
        return result.returncode == 0

    def _pane_exists(self, pane_target: str) -> bool:
        result = self._run_tmux(
            ["display-message", "-p", "-t", pane_target, "#{pane_id}"],
            capture_output=True,
        )
        return result.returncode == 0

    def _window_target_from_pane(self, pane_target: str) -> str:
        result = self._run_tmux(
            ["display-message", "-p", "-t", pane_target, "#{session_name}:#{window_index}"],
            capture_output=True,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            raise RuntimeError(f"tmux ウィンドウ取得に失敗しました: {err}")
        window_target = (result.stdout or "").strip()
        if not window_target:
            raise RuntimeError("tmux ウィンドウ取得に失敗しました: 空の結果")
        return window_target

    def _get_first_window_target(self, session_name: str) -> str:
        """セッション内の先頭ウィンドウターゲットを返す。base-index 設定に依存しない。"""
        result = self._run_tmux(
            ["list-windows", "-t", session_name, "-F", "#{session_name}:#{window_index}"],
            capture_output=True,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            raise RuntimeError(f"tmux ウィンドウ一覧取得に失敗しました: {err}")
        for line in (result.stdout or "").splitlines():
            target = line.strip()
            if target:
                return target
        raise RuntimeError("tmux ウィンドウ一覧取得に失敗しました: ウィンドウが見つかりません")

    def _get_first_pane_target(self, window_target: str) -> str:
        """ウィンドウ内の先頭ペインターゲットを返す。"""
        result = self._run_tmux(
            ["list-panes", "-t", window_target, "-F", "#{pane_id}"],
            capture_output=True,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            raise RuntimeError(f"tmux ペイン一覧取得に失敗しました: {err}")
        for line in (result.stdout or "").splitlines():
            target = line.strip()
            if target:
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
                result = self._run_tmux(
                    ["display-message", "-p", "-t", pane_target, "#{session_name}:#{window_index}"],
                    capture_output=True,
                )
                if result.returncode == 0:
                    window_target = (result.stdout or "").strip()
                    if window_target:
                        self._layout_window_target = window_target
                        self._layout_controller_pane = pane_target
                        self._active_session_name = self._session_from_window_target(window_target)
                        log.info("現在の tmux ウィンドウを %s分割に使用します: %s", self._split_label(), window_target)
                        return

            # --no-auto-attach などで tmux 外実行された場合のフォールバック
            session_name = self._tmux_session_name
            if not self._has_session(session_name):
                result = self._run_tmux(
                    ["new-session", "-d", "-s", session_name, "-c", self._cwd],
                    capture_output=True,
                )
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

    def _create_worker_pane(self, cmd: str) -> str:
        """kiro-cli を実行する新しいペインを作成してペインターゲットを返す。"""
        self._ensure_layout()

        with self.__class__._layout_lock:
            window_target = self._layout_window_target
            controller_pane = self._layout_controller_pane
            if window_target is None:
                raise RuntimeError("tmux レイアウトが初期化されていません。")

            split_target = controller_pane or window_target
            result = self._run_tmux(
                [
                    "split-window",
                    self._split_option(),
                    "-d",
                    "-P",
                    "-F",
                    "#{pane_id}",
                    "-t",
                    split_target,
                    "-c",
                    self._cwd,
                    cmd,
                ],
                capture_output=True,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                raise RuntimeError(f"tmux ペイン分割に失敗しました: {err}")

            pane_target = (result.stdout or "").strip()
            if not pane_target:
                raise RuntimeError("tmux ペイン分割に失敗しました: 空の結果")

            # kiro-cli がすぐ終了しても出力を確認できるようにする
            self._run_tmux(["set-option", "-p", "-t", pane_target, "remain-on-exit", "on"], capture_output=False)
            self._run_tmux(["select-layout", "-t", window_target, self._layout_name()], capture_output=False)

            # 入力は常に controller 側 (kiro-loop) へ戻す
            if controller_pane and self._pane_exists(controller_pane):
                self._run_tmux(["select-pane", "-t", controller_pane], capture_output=False)
                self._run_tmux(["refresh-client", "-S"], capture_output=False)

            return pane_target

    def get_attach_session_name(self) -> str:
        """アタッチセッション名を返す。"""
        with self._lock:
            active_name = self._active_session_name
        return active_name or self._tmux_session_name

    def get_pane_target(self) -> str:
        """ペインターゲットを返す。"""
        with self._lock:
            return self._pane_target or ""

    def _send_text(self, pane_target: str, text: str) -> bool:
        """テキストを安全に送信する（set-buffer + paste-buffer）。"""
        buffer_name = f"kiro-loop-{uuid.uuid4().hex[:8]}"
        try:
            result = self._run_tmux(
                ["set-buffer", "-b", buffer_name, "--", text],
                capture_output=True,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                log.warning("tmux set-buffer に失敗しました: %s", err)
                return False
            result = self._run_tmux(
                ["paste-buffer", "-t", pane_target, "-b", buffer_name],
                capture_output=True,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                log.warning("tmux paste-buffer に失敗しました: %s", err)
                return False
            result = self._run_tmux(
                ["send-keys", "-t", pane_target, "Enter"],
                capture_output=True,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                log.warning("tmux send-keys(Enter) に失敗しました: %s", err)
                return False
            return True
        finally:
            try:
                self._run_tmux(["delete-buffer", "-b", buffer_name], capture_output=False)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # 起動 / 停止
    # ------------------------------------------------------------------

    def start(self) -> None:
        """tmux 分割ペイン上で kiro-cli を起動する。"""
        tmux_bin = shutil.which("tmux")
        if tmux_bin is None:
            raise RuntimeError("tmux が PATH に見つかりません。`sudo apt install tmux` を実行してください。")
        self._tmux_bin = tmux_bin

        kiro_bin = shutil.which("kiro-cli")
        if kiro_bin is None:
            raise RuntimeError("kiro-cli が PATH に見つかりません。インストールしてください。")

        cmd_args = ["chat"] + self._kiro_args
        cmd = " ".join(shlex.quote(arg) for arg in [kiro_bin, *cmd_args])

        pane_target = self._create_worker_pane(cmd)
        with self._lock:
            self._pane_target = pane_target
        log.info("kiro-cli 起動完了 (pane=%s, cwd=%s)。", pane_target, self._cwd)

    def stop(self) -> None:
        """ペインを終了する。"""
        with self._lock:
            pane_target = self._pane_target
            self._pane_target = None
        if pane_target is not None and self._pane_exists(pane_target):
            log.info("kiro-cli セッションを終了します (cwd=%s)。", self._cwd)
            self._run_tmux(["send-keys", "-t", pane_target, "C-c"], capture_output=False)
            time.sleep(0.2)
            window_target = self._window_target_from_pane(pane_target)
            self._run_tmux(["kill-pane", "-t", pane_target], capture_output=False)
            self._run_tmux(
                ["select-layout", "-t", window_target, self._layout_name()],
                capture_output=False,
            )

    def is_alive(self) -> bool:
        """ペインが存在するか確認する。"""
        with self._lock:
            pane_target = self._pane_target
        return pane_target is not None and self._pane_exists(pane_target)

    def restart(self) -> None:
        """セッションを再起動する。失敗時は RuntimeError を raise する。"""
        if not self._restart_lock.acquire(blocking=False):
            log.info("kiro-cli セッション再起動は既に進行中です (cwd=%s)。", self._cwd)
            return
        log.info("kiro-cli セッションを再起動します (cwd=%s)。", self._cwd)
        try:
            self.stop()
            time.sleep(2)
            self.start()
        finally:
            self._restart_lock.release()

    def is_restarting(self) -> bool:
        return self._restart_lock.locked()

    # ------------------------------------------------------------------
    # プロンプト送信
    # ------------------------------------------------------------------

    def send_prompt(self, prompt_text: str) -> bool:
        """tmux ペインにプロンプトを送信する（応答待ちはしない）。"""
        with self._lock:
            pane_target = self._pane_target
        if pane_target is None or not self._pane_exists(pane_target):
            log.warning("kiro-cli セッションが終了しています (cwd=%s)。", self._cwd)
            return False

        short = prompt_text[:80] + ("..." if len(prompt_text) > 80 else "")
        log.info("プロンプトを送信します [%s] (pane=%s): %s", self._cwd, pane_target, short)
        print(f"[kiro-loop] send [{self._cwd}] (pane={pane_target}) {short}", file=sys.stderr, flush=True)

        if not self._send_text(pane_target, prompt_text):
            print(f"[kiro-loop] done [{self._cwd}] failed", file=sys.stderr, flush=True)
            return False

        print(f"[kiro-loop] done [{self._cwd}] sent", file=sys.stderr, flush=True)
        return True


# ---------------------------------------------------------------------------
# セッションマネージャ（カレントディレクトリ単一ワークスペース）
# ---------------------------------------------------------------------------

class SessionManager:
    """カレントディレクトリ上で、プロンプトごとの kiro-cli セッションを管理する。"""

    def __init__(
        self,
        target_path: str,
        instance_id: str,
        kiro_args_base: list[str],
        split_direction: str,
        startup_timeout: int,
        response_timeout: int,
        echo_output: bool = False,
    ):
        resolved = Path(target_path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"パスが存在しないかディレクトリではありません: {resolved}")
        self._target_path = str(resolved)
        self._target_name = resolved.name or "default"
        self._instance_id = re.sub(r"[^A-Za-z0-9\-_]", "", instance_id)[:12] or "run"
        self._kiro_args_base = kiro_args_base[:]
        self._split_direction = split_direction
        self._startup_timeout = startup_timeout
        self._response_timeout = response_timeout
        self._echo_output = echo_output
        self._sessions: dict[str, KiroSession] = {}
        self._prompt_names: dict[str, str] = {}
        self._tmux_names: dict[str, str] = {}
        self._lock = threading.Lock()

    def _prompt_token(self, prompt_id: str) -> str:
        token = re.sub(r"[^A-Za-z0-9\-_]", "", prompt_id)[:12]
        return token or "prompt"

    def _tmux_name_for_prompt(self, prompt_id: str) -> str:
        composed = f"{self._instance_id}-{self._prompt_token(prompt_id)}"
        return _tmux_session_name(Path(self._target_path), composed)

    def _build_session(self, prompt_id: str) -> tuple["KiroSession", str]:
        tmux_name = self._tmux_name_for_prompt(prompt_id)
        session = KiroSession(
            cwd=self._target_path,
            kiro_args=self._kiro_args_base[:],
            tmux_session_name=tmux_name,
            split_direction=self._split_direction,
            startup_timeout=self._startup_timeout,
            response_timeout=self._response_timeout,
            echo_output=self._echo_output,
        )
        return session, tmux_name

    def _start_session(self, prompt_id: str, prompt_name: str) -> bool:
        session, tmux_name = self._build_session(prompt_id)
        try:
            session.start()
        except RuntimeError as exc:
            log.error("プロンプト '%s' のセッション起動に失敗しました: %s", prompt_name, exc)
            return False

        attach_session_name = session.get_attach_session_name()
        with self._lock:
            self._sessions[prompt_id] = session
            self._prompt_names[prompt_id] = prompt_name
            self._tmux_names[prompt_id] = attach_session_name

        log.info(
            "プロンプト '%s' 用セッションを起動しました (tmux=%s, generated=%s, args=%s)。",
            prompt_name,
            attach_session_name,
            tmux_name,
            self._kiro_args_base,
        )
        return True

    def sync_entries(self, entries: list[dict[str, Any]]) -> None:
        """エントリ一覧に合わせてセッションを起動/停止する。"""
        desired: dict[str, str] = {}
        for entry in entries:
            prompt_id = str(entry.get("id", "")).strip()
            if not prompt_id:
                continue
            prompt_name = str(entry.get("name", prompt_id)).strip() or prompt_id
            desired[prompt_id] = prompt_name

        with self._lock:
            current_ids = set(self._sessions.keys())

        remove_ids = current_ids - set(desired.keys())
        add_ids = [pid for pid in desired.keys() if pid not in current_ids]
        keep_ids = current_ids & set(desired.keys())

        for prompt_id in remove_ids:
            with self._lock:
                session = self._sessions.pop(prompt_id, None)
                prompt_name = self._prompt_names.pop(prompt_id, prompt_id)
                self._tmux_names.pop(prompt_id, None)
            if session is not None:
                log.info("プロンプト '%s' のセッションを停止します。", prompt_name)
                session.stop()

        with self._lock:
            for prompt_id in keep_ids:
                self._prompt_names[prompt_id] = desired[prompt_id]

        for prompt_id in add_ids:
            self._start_session(prompt_id, desired[prompt_id])

    def get_session(self, prompt_id: str, prompt_name: str) -> "KiroSession | None":
        with self._lock:
            existing = self._sessions.get(prompt_id)
        if existing is not None:
            return existing
        if not self._start_session(prompt_id, prompt_name):
            return None
        with self._lock:
            return self._sessions.get(prompt_id)

    def get_target_name(self) -> str:
        return self._target_name

    def get_target_path(self) -> str:
        return self._target_path

    def get_status(self) -> tuple[str, str, int, int]:
        with self._lock:
            sessions = list(self._sessions.values())
        alive = sum(1 for s in sessions if s.is_alive())
        return self._target_name, self._target_path, len(sessions), alive

    def list_prompt_statuses(self) -> list[tuple[str, str, bool, str, str]]:
        with self._lock:
            items = list(self._sessions.items())
            names = dict(self._prompt_names)
            tmux_names = dict(self._tmux_names)
        statuses: list[tuple[str, str, bool, str, str]] = []
        for prompt_id, session in items:
            prompt_name = names.get(prompt_id, prompt_id)
            tmux_name = tmux_names.get(prompt_id, "")
            pane_target = session.get_pane_target()
            statuses.append((prompt_name, prompt_id, session.is_alive(), tmux_name, pane_target))
        statuses.sort(key=lambda item: item[0])
        return statuses

    def restart_if_dead(self) -> None:
        with self._lock:
            items = list(self._sessions.items())
            names = dict(self._prompt_names)
        for prompt_id, session in items:
            if session.is_restarting():
                continue
            if not session.is_alive():
                prompt_name = names.get(prompt_id, prompt_id)
                log.warning("プロンプト '%s' のセッションが終了しました。再起動します。", prompt_name)
                try:
                    session.restart()
                except RuntimeError as exc:
                    log.error("プロンプト '%s' のセッション再起動に失敗しました: %s", prompt_name, exc)

    def stop(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._prompt_names.clear()
            self._tmux_names.clear()
        for session in sessions:
            session.stop()


# ---------------------------------------------------------------------------
# 定期実行スケジューラ（単一スレッド）
# ---------------------------------------------------------------------------

class PeriodicScheduler:
    """定期プロンプトのスケジュール管理。"""

    def __init__(self, session_mgr: SessionManager, entries: list[dict[str, Any]]):
        self._session_mgr = session_mgr
        self._entries: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._set_entries(entries, allow_immediate_once=True)

    def _set_entries(self, entries: list[dict[str, Any]], allow_immediate_once: bool = False) -> None:
        normalized: list[dict[str, Any]] = []
        now = time.time()
        for entry in entries:
            if not entry.get("enabled", True):
                continue
            prompt = str(entry.get("prompt", "")).strip()
            interval_minutes = entry.get("interval_minutes")
            name = str(entry.get("name", prompt[:40])) if prompt else ""
            try:
                interval = int(interval_minutes)
            except Exception:
                continue
            if not prompt or interval < 1:
                continue
            prompt_id = str(entry.get("id") or uuid.uuid4())
            run_immediately = bool(
                entry.get("run_immediately_on_startup", entry.get("run_immediately", False))
            )
            # 起動直後は kiro-cli セットアップ時間を見込んで 30 秒待ってから初回送信する。
            next_run_at = now + 30 if (allow_immediate_once and run_immediately) else now + (interval * 60)
            fresh_context = bool(entry.get("fresh_context", False))
            fresh_context_interval_raw = entry.get("fresh_context_interval_minutes")
            try:
                fresh_context_interval = int(fresh_context_interval_raw) if fresh_context_interval_raw is not None else None
            except Exception:
                fresh_context_interval = None
            if fresh_context_interval is not None and fresh_context_interval < 1:
                fresh_context_interval = None
            normalized.append({
                "id": prompt_id,
                "name": name,
                "prompt": prompt,
                "interval_minutes": interval,
                "enabled": True,
                "run_immediately_on_startup": run_immediately,
                "next_run_at": next_run_at,
                "fresh_context": fresh_context,
                "fresh_context_interval_minutes": fresh_context_interval,
                "next_clear_at": now if fresh_context else None,
            })
        self._session_mgr.sync_entries(normalized)
        with self._lock:
            self._entries = normalized

    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        """エントリを設定する（次回ループから適用）。"""
        self._set_entries(entries, allow_immediate_once=False)

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
                prompt = str(entry.get("prompt", ""))
                interval_minutes = int(entry.get("interval_minutes", 1))
                fresh_context = bool(entry.get("fresh_context", False))
                fresh_context_interval = entry.get("fresh_context_interval_minutes")

                # fresh_context_interval_minutes が設定されている場合は独立間隔で /clear を判定
                should_clear = False
                if fresh_context:
                    if fresh_context_interval is not None:
                        next_clear_at = float(entry.get("next_clear_at") or 0)
                        if now >= next_clear_at:
                            should_clear = True
                    else:
                        should_clear = True

                session = self._session_mgr.get_session(prompt_id, name)
                if session is None:
                    log.warning("[%s] 対応セッションの準備に失敗したため今回の送信をスキップします。", name)
                else:
                    log.info("[%s] プロンプトを実行します。", name)
                    try:
                        if should_clear:
                            log.info("[%s] fresh_context: コンテキストをクリアします。", name)
                            if not session.send_prompt("/clear"):
                                log.warning("[%s] /clear の送信に失敗しました。スキップします。", name)
                                continue
                            time.sleep(2)
                            # next_clear_at を更新
                            if fresh_context_interval is not None:
                                new_next_clear_at = time.time() + (int(fresh_context_interval) * 60)
                                with self._lock:
                                    for e in self._entries:
                                        if e.get("id") == entry.get("id"):
                                            e["next_clear_at"] = new_next_clear_at
                                            break
                        ok = session.send_prompt(prompt)
                        if not ok and not self._stop_event.is_set():
                            log.warning("[%s] 送信失敗。セッション再起動を試みます。", name)
                            try:
                                session.restart()
                            except RuntimeError as exc:
                                log.error("[%s] 再起動失敗: %s", name, exc)
                    except Exception as exc:
                        log.error("[%s] 予期しないエラー: %s", name, exc, exc_info=True)

                    next_run_at = time.time() + (interval_minutes * 60)
                    with self._lock:
                        for e in self._entries:
                            if e.get("id") == entry.get("id"):
                                e["next_run_at"] = next_run_at
                                break

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
  send <pane> <text>              指定ペインにテキストを送信
                                  例: send %12 status確認してください
  prompt-add <interval> <prompt>  定期プロンプトを追加
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
    print(f"定期プロンプトが実行中です。'help' でコマンド一覧を表示します。", flush=True)
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

            elif cmd == "send":
                send_args = line.split(maxsplit=2)
                if len(send_args) < 3:
                    print("使い方: send <pane> <text>", flush=True)
                else:
                    pane_target = send_args[1].strip()
                    send_text = send_args[2].strip()
                    # クォート除去
                    if (
                        len(send_text) >= 2
                        and send_text[0] == send_text[-1]
                        and send_text[0] in ('"', "'")
                    ):
                        send_text = send_text[1:-1].strip()
                    if not pane_target:
                        print("pane が空です。", flush=True)
                    elif not send_text:
                        print("text が空です。", flush=True)
                    else:
                        ok, err = send_text_via_tmux(pane_target, send_text)
                        if ok:
                            print("送信しました。", flush=True)
                        else:
                            print(f"送信に失敗しました: {err}", flush=True)

            elif cmd == "prompt-add":
                add_args = line.split(maxsplit=3)
                if len(add_args) < 3:
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
                        int(add_args[1])
                        interval_text = add_args[1]
                        prompt_parts = add_args[2:]
                    except ValueError:
                        if len(add_args) < 4:
                            print(
                                "使い方: prompt-add <interval_minutes> <prompt>\n"
                                "        prompt-add <name> <interval_minutes> <prompt>",
                                flush=True,
                            )
                            continue
                        name_override = add_args[1]
                        interval_text = add_args[2]
                        prompt_parts = add_args[3:]

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

                    new_entry: dict[str, Any] = {
                        "prompt": prompt_text,
                        "interval_minutes": interval,
                    }
                    if name_override:
                        new_entry["name"] = name_override

                    ws_prompts = load_prompt_config(target_path)
                    ws_prompts.append(new_entry)
                    if save_prompt_config(target_path, ws_prompts):
                        scheduler.set_entries(ws_prompts)
                        print("定期プロンプトを追加しました。", flush=True)
                    else:
                        print("保存に失敗しました。", flush=True)

            elif cmd == "prompt-list":
                ws_prompts = load_prompt_config(target_path)
                print(f"[{target_name}] {target_path}", flush=True)
                if not ws_prompts:
                    print("  (定期プロンプトは未設定)", flush=True)
                else:
                    for idx, p in enumerate(ws_prompts, start=1):
                        enabled = p.get("enabled", True)
                        interval = p.get("interval_minutes", "?")
                        run_immediately = bool(
                            p.get("run_immediately_on_startup", p.get("run_immediately", False))
                        )
                        prompt_text = str(p.get("prompt", "")).replace("\n", " ")
                        short = prompt_text[:80] + ("..." if len(prompt_text) > 80 else "")
                        flag = "on" if enabled else "off"
                        immediate_note = " (起動時即実行)" if run_immediately else ""
                        print(f"  {idx:>2}. [{flag}] {interval}分{immediate_note}: {short}", flush=True)

            elif cmd == "prompt-remove":
                remove_args = line.split(maxsplit=1)
                if len(remove_args) < 2:
                    print("使い方: prompt-remove <index>", flush=True)
                else:
                    index_text = remove_args[1]
                    try:
                        index = int(index_text)
                        if index < 1:
                            raise ValueError()
                    except ValueError:
                        print("index は 1 以上の整数を指定してください。", flush=True)
                        continue

                    ws_prompts = load_prompt_config(target_path)
                    if index > len(ws_prompts):
                        print(f"インデックスが範囲外です（{index}）。", flush=True)
                    else:
                        removed = ws_prompts.pop(index - 1)
                        if save_prompt_config(target_path, ws_prompts):
                            scheduler.set_entries(ws_prompts)
                            short = str(removed.get("prompt", ""))[:60]
                            print(f"削除しました: {short}", flush=True)
                        else:
                            print("保存に失敗しました。", flush=True)

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


# ---------------------------------------------------------------------------
# シグナルハンドラ / グローバル cleanup
# ---------------------------------------------------------------------------

_session_mgr_ref: SessionManager | None = None
_scheduler_ref: PeriodicScheduler | None = None
_stop_event_ref: threading.Event | None = None
_instance_file_ref: Path | None = None


def _cleanup() -> None:
    if _scheduler_ref is not None:
        _scheduler_ref.stop()
    if _session_mgr_ref is not None:
        _session_mgr_ref.stop()
    if _instance_file_ref is not None:
        _safe_unlink(_instance_file_ref)


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
        log.info("tmux 外で起動されたため '%s' を新規作成してアタッチします。", session_name)
        os.execvp(
            tmux_bin,
            [
                tmux_bin,
                "new-session",
                "-s", session_name,
                "-c", str(target_path),
                controller_cmd,
            ],
        )
    else:
        create_window = subprocess.run(
            [tmux_bin, "new-window", "-t", session_name, "-c", str(target_path), controller_cmd],
            check=False,
            capture_output=True,
            text=True,
        )
        if create_window.returncode != 0:
            log.warning(
                "既存セッション '%s' へのウィンドウ追加に失敗しました: %s。アタッチのみ試みます。",
                session_name,
                create_window.stderr.strip(),
            )
        os.execvp(
            tmux_bin,
            [tmux_bin, "attach-session", "-t", session_name],
        )


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="kiro-cli を定期プロンプトで自動操作するスクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
起動例:
  python kiro-loop.py                      # カレントディレクトリの設定ファイルを使用

起動後のコマンド例:
  > status                     状態表示
  > quit                       終了
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
        default=None,
        choices=["horizontal", "vertical"],
        help="tmux ペインの分割方向 (デフォルト: horizontal)",
    )
    parser.add_argument(
        "--no-auto-attach",
        action="store_true",
        default=False,
        help="起動後に tmux セッションへ自動アタッチしない（デフォルト: 自動アタッチ）",
    )
    parser.add_argument(
        "--controller-mode",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--instance-id",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    cwd = Path.cwd()

    # 多重起動チェック
    if find_running_instance(cwd) is not None:
        log.info("既に起動中のインスタンスが見つかりました。起動をスキップします。")
        sys.exit(0)

    # tmux 外で起動された場合、自己を tmux 内で再実行
    _auto_attach_tmux_if_needed(args)

    # 再度チェック（tmux 内での再起動後）
    if find_running_instance(cwd) is not None:
        log.info("既に起動中のインスタンスが見つかりました。起動をスキップします。")
        sys.exit(0)

    log_file = configure_file_logging(cwd)
    log.info("ファイルログを開始しました: %s", log_file)

    config, config_path, has_local_config = load_config(cwd)
    ws_config = _load_prompt_file_data(str(cwd))

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

    # グローバル参照（cleanup / シグナルハンドラ用）
    global _session_mgr_ref, _scheduler_ref, _stop_event_ref, _instance_file_ref

    stop_event = threading.Event()
    _stop_event_ref = stop_event

    _instance_file_ref = write_instance_file(cwd)
    log.info("実行中プロセス情報を記録しました: %s", _instance_file_ref)

    instance_id = args.instance_id or uuid.uuid4().hex[:8]

    session_mgr = SessionManager(
        target_path=str(cwd),
        instance_id=instance_id,
        kiro_args_base=kiro_args,
        split_direction=split_direction,
        startup_timeout=startup_timeout,
        response_timeout=response_timeout,
        echo_output=echo_output,
    )
    _session_mgr_ref = session_mgr

    log.info("カレントディレクトリを起動対象に設定しました: %s", cwd)

    scheduler = PeriodicScheduler(session_mgr, entries)
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
