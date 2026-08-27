#!/usr/bin/env python3
"""CLI client for the authenticated VS Code Language Model API bridge."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def endpoint_path() -> Path:
    return Path(os.environ.get("VSCODE_COPILOT_BRIDGE_FILE", "~/.vscode-copilot-bridge.json")).expanduser()


def powershell_executable() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not executable:
        raise RuntimeError("powershell.exe が見つかりません（この起動方法は WSL 用です）")
    return executable


def windows_path(path: Path) -> str:
    try:
        return subprocess.run(["wslpath", "-w", str(path)], check=True, text=True,
                              capture_output=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Windows pathへ変換できません: {path}") from exc


def launch_windows_vscode(port: int, token: str, cwd: Path, code_bin: str) -> None:
    if not 1 <= port <= 65535:
        raise RuntimeError("port は1から65535で指定してください")
    win_cwd = windows_path(cwd)
    # --user-data-dirにより既存VS Codeプロセスへのenv引き渡し消失を避ける。
    script = (
        f"$env:VSCODE_COPILOT_BRIDGE_PORT='{port}';"
        f"$env:VSCODE_COPILOT_BRIDGE_TOKEN='{token}';"
        "$data=Join-Path $env:LOCALAPPDATA 'vscode-copilot-bridge';"
        f"& '{code_bin.replace("'", "''")}' --user-data-dir $data --new-window "
        f"'{win_cwd.replace("'", "''")}'"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    subprocess.Popen([powershell_executable(), "-NoProfile", "-NonInteractive",
                      "-EncodedCommand", encoded], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def start_bridge(path: Path, port: int, cwd: Path, code_bin: str) -> dict[str, object]:
    token = secrets.token_hex(32)
    endpoint = {"version": 1, "url": f"http://127.0.0.1:{port}/v1/chat", "token": token}
    launch_windows_vscode(port, token, cwd, code_bin)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(endpoint) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return endpoint


def read_endpoint(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not data.get("url") or not data.get("token"):
            raise ValueError("unsupported or incomplete endpoint file")
        return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"bridge endpoint を読めません: {path}: {exc}") from exc


def request(endpoint: dict[str, object], prompt: str, family: str | None, timeout: float) -> dict:
    body = {"prompt": prompt}
    if family:
        body["family"] = family
    req = urllib.request.Request(
        str(endpoint["url"]),
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {endpoint['token']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc).get("error", exc.reason)
        except Exception:
            message = exc.reason
        raise RuntimeError(f"bridge error ({exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"bridge に接続できません: {exc.reason}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="VS Code の Copilot Language Model へ問い合わせます")
    parser.add_argument("prompt", nargs="?", help="依頼。省略時は標準入力から読む")
    parser.add_argument("--family", help="モデル family の選択条件（例: gpt-4o）")
    parser.add_argument("--timeout", type=float, default=300, help="応答待ち秒数（既定: 300）")
    parser.add_argument("--json", action="store_true", help="モデル情報を含む JSON を出力")
    parser.add_argument("--start", action="store_true",
                        help="PowerShellから専用Windows VS Codeを現在のディレクトリで起動")
    parser.add_argument("--start-only", action="store_true", help="起動だけ行い問い合わせない")
    parser.add_argument("--port", type=int, default=32190, help="Windows bridge port（既定: 32190）")
    parser.add_argument("--code-bin", default="code", help="Windows側のcode command（既定: code）")
    args = parser.parse_args()
    try:
        path = endpoint_path()
        endpoint = start_bridge(path, args.port, Path.cwd(), args.code_bin) if args.start else read_endpoint(path)
        if args.start_only:
            print(f"bridge starting: {endpoint['url']}")
            return 0
        prompt = args.prompt if args.prompt is not None else sys.stdin.read()
        if not prompt.strip():
            parser.error("prompt が空です")
        # VS Codeの初回起動を待つ。接続拒否だけを短く再試行し、モデル/APIエラーは即時返す。
        deadline = time.monotonic() + min(args.timeout, 30) if args.start else 0
        while True:
            try:
                result = request(endpoint, prompt, args.family, args.timeout)
                break
            except RuntimeError as exc:
                if not args.start or "接続できません" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
    except RuntimeError as exc:
        print(f"vscode-copilot-chat: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False) if args.json else result["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
