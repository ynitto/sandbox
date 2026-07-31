#!/usr/bin/env python3
"""
WSL Terminal Launcher

.pyw 拡張子で実行すると Windows が pythonw.exe を使うため
コンソールウィンドウが表示されない。
Task Scheduler やスタートアップ登録からの起動でも同様。
"""
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
LOG_PATH    = SCRIPT_DIR / "launcher.log"


# ── ログ ────────────────────────────────────────────────────
def _log(msg: str, level: str = "INFO") -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}][{level}] {msg}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ── WSL 起動待機 ─────────────────────────────────────────────
def _wait_wsl_ready(distro: str = "", timeout: int = 60, interval: int = 3) -> bool:
    """wsl.exe -e echo ok が成功するまでポーリングする"""
    cmd   = ["wsl.exe"] + (["-d", distro] if distro else []) + ["-e", "echo", "ok"]
    label = f" ({distro})" if distro else ""
    _log(f"WSL 起動待機中{label}...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0:
                _log(f"WSL 起動確認{label}")
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass
        time.sleep(interval)
    _log(f"WSL 起動待機タイムアウト ({timeout}秒){label}", "WARN")
    return False


# ── bash コマンド生成 ────────────────────────────────────────
def _bash_cmd(wsl_path: str, command: str, keep_open: bool) -> str:
    p = wsl_path.replace("'", "'\\''")
    c = command.replace("'", "'\\''")
    return f"cd '{p}' && ({c}); exec bash" if keep_open else f"cd '{p}' && {c}"


# ── 起動 ─────────────────────────────────────────────────────
def _launch_wt(terminals: list, default_distro: str) -> None:
    """wt.exe で複数タブを一括起動する"""
    wt_args: list[str] = []
    first = True
    for t in terminals:
        distro = t.get("distro") or default_distro
        cmd    = _bash_cmd(t["wslPath"], t["command"], t.get("keepOpen", True))
        if not first:
            wt_args += [";", "new-tab"]
        else:
            wt_args.append("new-tab")
            first = False
        wt_args += ["--title", t["name"], "--"]
        wt_args += (
            ["wsl.exe", "-d", distro, "--cd", t["wslPath"], "--", "bash", "-c", cmd]
            if distro else
            ["wsl.exe", "--cd", t["wslPath"], "--", "bash", "-c", cmd]
        )
    _log("Windows Terminal を起動します...")
    subprocess.Popen(["wt.exe"] + wt_args)


def _launch_wsl(terminals: list, default_distro: str, delay_ms: int) -> None:
    """wsl.exe を個別ウィンドウで起動する (Windows Terminal なし)"""
    for t in terminals:
        distro = t.get("distro") or default_distro
        cmd    = _bash_cmd(t["wslPath"], t["command"], t.get("keepOpen", True))
        _log(f"起動: {t['name']} ({t['wslPath']})")
        args = ["wsl.exe"] + (["-d", distro] if distro else []) + ["--", "bash", "-c", cmd]
        subprocess.Popen(args)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000)


# ── メイン ───────────────────────────────────────────────────
def main() -> None:
    if not CONFIG_PATH.exists():
        _log(f"設定ファイルが見つかりません: {CONFIG_PATH}", "ERROR")
        sys.exit(1)
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"設定ファイルの読み込みに失敗: {e}", "ERROR")
        sys.exit(1)

    s       = cfg.get("settings", {})
    enabled = [t for t in cfg.get("terminals", []) if t.get("enabled")]
    if not enabled:
        _log("有効なターミナルが設定されていません。", "WARN")
        return

    _log(f"起動するターミナル数: {len(enabled)}")

    default = s.get("defaultDistro", "")
    use_wt  = s.get("terminalApp", "wt") == "wt" and bool(shutil.which("wt.exe"))
    if s.get("terminalApp", "wt") == "wt" and not use_wt:
        _log("wt.exe が見つかりません。wsl.exe で起動します。", "WARN")

    if s.get("wslWaitEnabled", True):
        timeout = s.get("wslWaitTimeoutSeconds", 60)
        for distro in sorted({t.get("distro") or default for t in enabled}):
            if not _wait_wsl_ready(distro, timeout):
                _log(f"WSL ({distro}) の起動確認失敗。続行します。", "WARN")

    try:
        if use_wt:
            _launch_wt(enabled, default)
        else:
            _launch_wsl(enabled, default, s.get("delayBetweenLaunchesMs", 500))
        _log("すべてのターミナルの起動要求が完了しました。")
    except Exception as e:
        _log(f"ターミナル起動中にエラーが発生しました: {e}", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()
