#!/usr/bin/env python3
"""
kiro-loop.py — kiro-cli をインタラクティブモードで起動し、
設定ファイルに定義したプロンプトを定期的に送信するスクリプト。

依存ライブラリ:
  - pexpect   (pip install pexpect)   PTY 制御でインタラクティブ操作
  - PyYAML    (pip install pyyaml)    設定ファイル読み込み（JSON も可、任意）

動作環境: WSL (Ubuntu) / Linux
終了方法: ターミナルを閉じる (SIGHUP) か Ctrl+C、またはコマンド quit

使い方:
  python3 /path/to/kiro-loop.py [--config CONFIG_FILE]
  起動後、コマンドプロンプト (>) でワークスペースを追加・管理できます。
    > add myproject ~/projects/my-app
    > list
    > help

設定ファイル (kiro-loop.yaml) の例は付属の kiro-loop.yaml.example を参照。
"""

import argparse
import atexit
import json
import logging
import re
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 依存ライブラリの存在チェック
# ---------------------------------------------------------------------------

try:
    import pexpect  # type: ignore
except ImportError:
    print("[kiro-loop] ERROR: pexpect が見つかりません。", file=sys.stderr)
    print("  pip install pexpect", file=sys.stderr)
    sys.exit(1)

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


# ---------------------------------------------------------------------------
# 設定ロード
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_NAMES = ["kiro-loop.yaml", "kiro-loop.yml", "kiro-loop.json"]


def find_default_config(cwd: Path) -> Path | None:
    """カレントディレクトリと HOME を順番に探す。"""
    for name in DEFAULT_CONFIG_NAMES:
        for base in (cwd, Path.home()):
            candidate = base / name
            if candidate.is_file():
                return candidate
    return None


def load_config(config_path: Path | None, cwd: Path) -> tuple[dict[str, Any], Path]:
    """設定ファイルを読み込み (config, resolved_path) を返す。
    ファイルが存在しない場合は空の config とデフォルトパスを返す（終了しない）。
    """
    if config_path is None:
        config_path = find_default_config(cwd)
    if config_path is None:
        default_path = cwd / "kiro-loop.yaml"
        log.info(
            "設定ファイルが見つかりません。ワークスペースを追加すると %s に自動保存されます。",
            default_path,
        )
        return {}, default_path

    log.info("設定ファイルを読み込みます: %s", config_path)
    return _load_config_file(config_path), config_path


def _write_config(config: dict[str, Any], config_path: Path) -> bool:
    """設定ファイルを書き込む。PyYAML があれば YAML、なければ JSON で保存。"""
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if yaml is not None:
            # 拡張子が .yaml/.yml でなければ .yaml に揃える
            write_path = (
                config_path
                if config_path.suffix.lower() in (".yaml", ".yml")
                else config_path.with_suffix(".yaml")
            )
            with write_path.open("w", encoding="utf-8") as f:
                yaml.dump(
                    config, f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
        else:
            # PyYAML なし → JSON にフォールバック
            write_path = (
                config_path
                if config_path.suffix.lower() == ".json"
                else config_path.with_suffix(".json")
            )
            with write_path.open("w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        log.info("設定ファイルを保存しました: %s", write_path)
        return True
    except Exception as exc:
        log.error("設定ファイルの書き込みに失敗しました: %s", exc)
        return False


# ---------------------------------------------------------------------------
# kiro-cli セッション管理
# ---------------------------------------------------------------------------

class KiroSession:
    """kiro-cli をインタラクティブモードで制御する PTY セッション。"""

    def __init__(
        self,
        cwd: str,
        kiro_args: list[str],
        startup_timeout: int = 60,
        response_timeout: int = 300,
        echo_output: bool = True,
    ):
        self._cwd = cwd
        self._kiro_args = kiro_args
        self._startup_timeout = startup_timeout
        self._response_timeout = response_timeout
        self._echo_output = echo_output
        self._child: pexpect.spawn | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """kiro-cli を起動してプロンプト待ちにする。失敗時は RuntimeError を raise する。"""
        kiro_bin = shutil.which("kiro-cli")
        if kiro_bin is None:
            raise RuntimeError("kiro-cli が PATH に見つかりません。インストールしてください。")

        cmd_args = ["chat"] + self._kiro_args
        log.info("kiro-cli を起動します: kiro-cli %s (cwd=%s)", " ".join(cmd_args), self._cwd)

        cols = shutil.get_terminal_size((200, 50)).columns

        child = pexpect.spawn(
            kiro_bin,
            cmd_args,
            cwd=self._cwd,
            encoding="utf-8",
            codec_errors="replace",
            timeout=self._startup_timeout,
            dimensions=(50, cols),
        )

        if self._echo_output:
            child.logfile_read = sys.stderr

        log.info("kiro-cli の起動を待機中 (最大 %d 秒)...", self._startup_timeout)
        self._wait_for_prompt(child, timeout=self._startup_timeout, label="起動")

        with self._lock:
            self._child = child

        log.info("kiro-cli 起動完了 (cwd=%s)。", self._cwd)

    def send_prompt(self, prompt_text: str) -> bool:
        """プロンプトを送信して応答完了まで待つ。"""
        with self._lock:
            child = self._child

        if child is None or not child.isalive():
            log.warning("kiro-cli セッションが終了しています (cwd=%s)。", self._cwd)
            return False

        short = prompt_text[:80] + ("..." if len(prompt_text) > 80 else "")
        log.info("プロンプトを送信します [%s]: %s", self._cwd, short)
        child.sendline(prompt_text)
        return self._wait_for_prompt(child, timeout=self._response_timeout, label="応答")

    def _wait_for_prompt(self, child: pexpect.spawn, timeout: int, label: str) -> bool:
        """プロンプト記号が現れるまで待つ。失敗時は False を返すか RuntimeError を raise する。"""
        patterns = [
            r"(?m)^[>\?❯›] $",
            r"(?m)^[>\?❯›]\s",
            r"\u276f\s",   # ❯ (U+276F)
            r"\u203a\s",   # › (U+203A)
            pexpect.EOF,
            pexpect.TIMEOUT,
        ]
        eof_idx = len(patterns) - 2
        timeout_idx = len(patterns) - 1

        try:
            idx = child.expect(patterns, timeout=timeout)
        except pexpect.EOF:
            log.warning("kiro-cli が終了しました (EOF)。")
            return False
        except pexpect.TIMEOUT:
            msg = f"kiro-cli の{label}がタイムアウトしました ({timeout} 秒)。"
            if label == "起動":
                raise RuntimeError(msg)
            log.warning("%s 次の定期実行時に再試行します。", msg)
            return False

        if idx == eof_idx:
            log.warning("kiro-cli が終了しました (EOF)。")
            return False
        if idx == timeout_idx:
            msg = f"kiro-cli の{label}がタイムアウトしました ({timeout} 秒)。"
            if label == "起動":
                raise RuntimeError(msg)
            log.warning("%s 次の定期実行時に再試行します。", msg)
            return False

        return True

    def stop(self) -> None:
        with self._lock:
            child = self._child
            self._child = None

        if child is not None and child.isalive():
            log.info("kiro-cli セッションを終了します (cwd=%s)。", self._cwd)
            try:
                child.sendcontrol("c")
                child.expect(pexpect.EOF, timeout=5)
            except Exception:
                pass
            try:
                child.close(force=True)
            except Exception:
                pass

    def is_alive(self) -> bool:
        with self._lock:
            return self._child is not None and self._child.isalive()

    def restart(self) -> None:
        """セッションを再起動する。失敗時は RuntimeError を raise する。"""
        log.info("kiro-cli セッションを再起動します (cwd=%s)。", self._cwd)
        self.stop()
        time.sleep(2)
        self.start()


# ---------------------------------------------------------------------------
# ワークスペース管理
# ---------------------------------------------------------------------------

class WorkspaceManager:
    """複数のワークスペース（ディレクトリ）と対応する kiro-cli セッションを管理する。"""

    def __init__(
        self,
        kiro_args_base: list[str],
        startup_timeout: int,
        response_timeout: int,
        echo_output: bool,
    ):
        self._kiro_args_base = kiro_args_base
        self._startup_timeout = startup_timeout
        self._response_timeout = response_timeout
        self._echo_output = echo_output
        self._workspaces: dict[str, str] = {}         # name -> resolved path
        self._sessions: dict[str, KiroSession] = {}   # name -> session
        self._default: str | None = None
        self._lock = threading.Lock()

    def add_workspace(self, name: str, path: str, set_default: bool = False) -> bool:
        """ワークスペースを追加して kiro-cli セッションを起動する。"""
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            log.error("パスが存在しないかディレクトリではありません: %s", resolved)
            return False

        # 既存セッションを取り出して停止（ロック外で stop する）
        with self._lock:
            existing = self._sessions.pop(name, None)
            self._workspaces[name] = str(resolved)
            if set_default or self._default is None:
                self._default = name

        if existing is not None:
            existing.stop()

        # セッション起動（時間がかかるのでロック外）
        session = KiroSession(
            cwd=str(resolved),
            kiro_args=self._kiro_args_base[:],
            startup_timeout=self._startup_timeout,
            response_timeout=self._response_timeout,
            echo_output=self._echo_output,
        )
        try:
            session.start()
        except RuntimeError as exc:
            log.error("ワークスペース '%s' の kiro-cli 起動に失敗しました: %s", name, exc)
            with self._lock:
                self._workspaces.pop(name, None)
                if self._default == name:
                    self._default = next(iter(self._workspaces), None)
            return False

        with self._lock:
            self._sessions[name] = session

        log.info("ワークスペース '%s' を追加しました (%s)。", name, resolved)
        return True

    def remove_workspace(self, name: str) -> bool:
        """ワークスペースを削除してセッションを停止する。"""
        with self._lock:
            if name not in self._workspaces:
                log.warning("ワークスペース '%s' が見つかりません。", name)
                return False
            session = self._sessions.pop(name, None)
            del self._workspaces[name]
            if self._default == name:
                self._default = next(iter(self._workspaces), None)

        if session is not None:
            session.stop()

        log.info("ワークスペース '%s' を削除しました。", name)
        return True

    def set_default(self, name: str) -> bool:
        """デフォルトワークスペースを変更する。"""
        with self._lock:
            if name not in self._workspaces:
                log.warning("ワークスペース '%s' が見つかりません。", name)
                return False
            self._default = name
        log.info("デフォルトワークスペースを '%s' に設定しました。", name)
        return True

    def get_session(self, workspace_name: str | None) -> KiroSession | None:
        """指定ワークスペース（省略時はデフォルト）のセッションを返す。"""
        with self._lock:
            name = workspace_name if workspace_name else self._default
            if name is None:
                return None
            return self._sessions.get(name)

    def list_workspaces(self) -> list[tuple[str, str, bool, bool]]:
        """(name, path, is_default, is_alive) のリストを返す。"""
        with self._lock:
            return [
                (
                    name,
                    path,
                    name == self._default,
                    self._sessions[name].is_alive() if name in self._sessions else False,
                )
                for name, path in self._workspaces.items()
            ]

    def restart_dead_sessions(self) -> None:
        """死んでいるセッションを再起動する（監視スレッド用）。"""
        with self._lock:
            items = list(self._sessions.items())
        for name, session in items:
            if not session.is_alive():
                log.warning("ワークスペース '%s' のセッションが終了しました。再起動します。", name)
                try:
                    session.restart()
                except RuntimeError as exc:
                    log.error("ワークスペース '%s' の再起動に失敗しました: %s", name, exc)

    def get_workspace_defs(self) -> list[dict[str, Any]]:
        """現在のワークスペース定義を設定ファイル形式で返す。"""
        with self._lock:
            return [
                {"name": name, "path": path, "default": name == self._default}
                for name, path in self._workspaces.items()
            ]

    def stop_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.stop()


# ---------------------------------------------------------------------------
# 定期実行スケジューラ
# ---------------------------------------------------------------------------

class PeriodicScheduler:
    """定期プロンプトのスケジュール管理。"""

    def __init__(self, workspace_mgr: WorkspaceManager, entries: list[dict[str, Any]]):
        self._workspace_mgr = workspace_mgr
        self._entries = entries
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for entry in self._entries:
            if not entry.get("enabled", True):
                log.info("スキップ (enabled=false): %s", entry.get("name", entry.get("prompt", "")[:40]))
                continue

            prompt = entry.get("prompt", "").strip()
            interval_minutes = entry.get("interval_minutes")
            name = entry.get("name", prompt[:40])
            workspace = entry.get("workspace")  # None のときはデフォルトワークスペースを使う

            if not prompt:
                log.warning("prompt が空のエントリをスキップします: %s", entry)
                continue
            if not interval_minutes or interval_minutes < 1:
                log.warning("interval_minutes が無効なエントリをスキップします: %s", name)
                continue

            t = threading.Thread(
                target=self._run_entry,
                args=(name, prompt, int(interval_minutes), workspace),
                name=f"periodic-{name[:20]}",
                daemon=True,
            )
            self._threads.append(t)
            t.start()
            ws_label = workspace or "(デフォルト)"
            log.info("定期プロンプト登録: '%s' — %d 分ごと [workspace=%s]", name, interval_minutes, ws_label)

        log.info("合計 %d 件の定期プロンプトが有効です。", len(self._threads))

    def _run_entry(self, name: str, prompt: str, interval_minutes: int, workspace: str | None) -> None:
        """１つのプロンプトエントリの定期実行ループ。"""
        interval_sec = interval_minutes * 60
        ws_label = workspace or "(デフォルト)"

        log.info("[%s] 定期実行開始 (interval=%d 分, workspace=%s)。", name, interval_minutes, ws_label)

        while not self._stop_event.is_set():
            session = self._workspace_mgr.get_session(workspace)
            if session is None:
                log.warning(
                    "[%s] ワークスペース '%s' のセッションがまだ準備できていません。%d 秒後に再確認します。",
                    name, ws_label, interval_sec,
                )
            else:
                log.info("[%s] プロンプトを実行します (workspace=%s)。", name, ws_label)
                try:
                    ok = session.send_prompt(prompt)
                    if not ok and not self._stop_event.is_set():
                        log.warning("[%s] 送信失敗。セッション再起動を試みます。", name)
                        try:
                            session.restart()
                        except RuntimeError as exc:
                            log.error("[%s] 再起動失敗: %s", name, exc)
                except Exception as exc:
                    log.error("[%s] 予期しないエラー: %s", name, exc, exc_info=True)

            if self._stop_event.wait(interval_sec):
                break

        log.info("[%s] 定期実行を終了しました。", name)

    def stop(self) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=5)


# ---------------------------------------------------------------------------
# インタラクティブコマンドループ
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
コマンド一覧:
  add <name> <path>   ワークスペースを追加して kiro-cli を起動（設定ファイルに自動保存）
                      例: add myproject ~/projects/my-app
  remove <name>       ワークスペースを削除してセッションを停止（設定ファイルに自動保存）
  default <name>      デフォルトワークスペースを変更（設定ファイルに自動保存）
  list                ワークスペースと状態を一覧表示
  status              実行状態を表示
  save [path]         現在のワークスペース設定を設定ファイルに保存
                      例: save           （現在の設定ファイルパスに上書き）
                          save ~/my.yaml （指定パスに保存）
  help                このヘルプを表示
  quit / exit         終了"""


def command_loop(
    workspace_mgr: WorkspaceManager,
    stop_event: threading.Event,
    config: dict[str, Any],
    config_path: Path,
) -> None:
    """stdin からコマンドを読んでワークスペースを管理する（メインスレッドで実行）。"""
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

            elif cmd == "add":
                if len(parts) < 3:
                    print("使い方: add <name> <path>", flush=True)
                else:
                    if workspace_mgr.add_workspace(parts[1], parts[2]):
                        config["workspaces"] = workspace_mgr.get_workspace_defs()
                        _write_config(config, config_path)

            elif cmd == "remove":
                if len(parts) < 2:
                    print("使い方: remove <name>", flush=True)
                else:
                    if workspace_mgr.remove_workspace(parts[1]):
                        config["workspaces"] = workspace_mgr.get_workspace_defs()
                        _write_config(config, config_path)

            elif cmd == "default":
                if len(parts) < 2:
                    print("使い方: default <name>", flush=True)
                else:
                    if workspace_mgr.set_default(parts[1]):
                        config["workspaces"] = workspace_mgr.get_workspace_defs()
                        _write_config(config, config_path)

            elif cmd == "save":
                save_path = Path(parts[1]).expanduser().resolve() if len(parts) >= 2 else config_path
                config["workspaces"] = workspace_mgr.get_workspace_defs()
                _write_config(config, save_path)
                if len(parts) >= 2:
                    config_path = save_path  # 以降はこのパスを使う

            elif cmd == "list":
                workspaces = workspace_mgr.list_workspaces()
                if not workspaces:
                    print("登録されているワークスペースはありません。", flush=True)
                    print("  add <name> <path> で追加してください。", flush=True)
                else:
                    print(f"  {'名前':<20} {'状態':<8} パス", flush=True)
                    print("  " + "-" * 58, flush=True)
                    for ws_name, ws_path, is_default, is_alive in workspaces:
                        marker = "* " if is_default else "  "
                        status = "[alive]" if is_alive else "[dead] "
                        print(f"{marker}{ws_name:<20} {status} {ws_path}", flush=True)

            elif cmd == "status":
                workspaces = workspace_mgr.list_workspaces()
                print(f"ワークスペース: {len(workspaces)} 件", flush=True)
                for ws_name, ws_path, is_default, is_alive in workspaces:
                    marker = "(default) " if is_default else "          "
                    status = "alive" if is_alive else "dead"
                    print(f"  {marker}{ws_name}: {ws_path} [{status}]", flush=True)

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

def _monitor_loop(workspace_mgr: WorkspaceManager, stop_event: threading.Event) -> None:
    """死んだセッションを定期的に検出して再起動する。"""
    while not stop_event.wait(10):
        workspace_mgr.restart_dead_sessions()


# ---------------------------------------------------------------------------
# シグナルハンドラ / グローバル cleanup
# ---------------------------------------------------------------------------

_workspace_mgr_ref: WorkspaceManager | None = None
_scheduler_ref: PeriodicScheduler | None = None
_stop_event_ref: threading.Event | None = None


def _cleanup() -> None:
    if _scheduler_ref is not None:
        _scheduler_ref.stop()
    if _workspace_mgr_ref is not None:
        _workspace_mgr_ref.stop_all()


def _signal_handler(sig: int, frame: Any) -> None:
    sig_name = signal.Signals(sig).name
    log.info("シグナル %s を受信しました。終了します。", sig_name)
    if _stop_event_ref is not None:
        _stop_event_ref.set()
    _cleanup()
    sys.exit(0)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="kiro-cli を定期プロンプトで自動操作するスクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
起動例:
  python3 kiro-loop.py                      # カレントディレクトリの設定ファイルを使用
  python3 kiro-loop.py --config ~/my.yaml   # 設定ファイルを明示指定

起動後のコマンド例:
  > add myproject ~/projects/my-app   ワークスペース追加
  > list                               一覧表示
  > default myproject                  デフォルト変更
  > quit                               終了
""",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="設定ファイルのパス (デフォルト: カレントディレクトリ or HOME の kiro-loop.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル (デフォルト: INFO)",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    cwd = Path.cwd()
    config_path_arg = Path(args.config).resolve() if args.config else None
    config, config_path = load_config(config_path_arg, cwd)

    # kiro-cli 起動オプションの解決
    kiro_opts = config.get("kiro_options", {})
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
    echo_output = bool(config.get("echo_output", True))

    entries: list[dict[str, Any]] = config.get("prompts", [])
    if not entries:
        log.info("prompts が定義されていません。ワークスペース管理モードで起動します。")

    # グローバル参照（cleanup / シグナルハンドラ用）
    global _workspace_mgr_ref, _scheduler_ref, _stop_event_ref

    stop_event = threading.Event()
    _stop_event_ref = stop_event

    workspace_mgr = WorkspaceManager(
        kiro_args_base=kiro_args,
        startup_timeout=startup_timeout,
        response_timeout=response_timeout,
        echo_output=echo_output,
    )
    _workspace_mgr_ref = workspace_mgr

    # 設定ファイルに定義されたワークスペースを起動
    workspace_defs: list[dict[str, Any]] = config.get("workspaces", [])
    for ws_def in workspace_defs:
        ws_name = ws_def.get("name", "")
        ws_path = ws_def.get("path", "")
        ws_default = bool(ws_def.get("default", False))
        if not ws_name or not ws_path:
            log.warning("無効なワークスペース定義をスキップします: %s", ws_def)
            continue
        workspace_mgr.add_workspace(ws_name, ws_path, set_default=ws_default)

    if not workspace_defs:
        # カレントディレクトリを自動ワークスペースとして登録する
        ws_name = cwd.name or "default"
        log.info("ワークスペース未設定のため、カレントディレクトリを自動登録します: %s (%s)", ws_name, cwd)
        workspace_mgr.add_workspace(ws_name, str(cwd), set_default=True)

    scheduler = PeriodicScheduler(workspace_mgr, entries)
    _scheduler_ref = scheduler

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
        args=(workspace_mgr, stop_event),
        name="session-monitor",
        daemon=True,
    )
    monitor_thread.start()

    log.info("実行中です。ターミナルを閉じるか 'quit' コマンドで終了します。")

    # コマンドループはメインスレッドで実行
    command_loop(workspace_mgr, stop_event, config, config_path)

    # コマンドループ終了後のクリーンアップ
    stop_event.set()
    _cleanup()
    sys.exit(0)


if __name__ == "__main__":
    main()
