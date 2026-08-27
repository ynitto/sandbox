#!/usr/bin/env python3
"""CLI client for the authenticated VS Code Language Model API bridge."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def endpoint_path() -> Path:
    return Path(os.environ.get("VSCODE_COPILOT_BRIDGE_FILE", "~/.vscode-copilot-bridge.json")).expanduser()


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
    args = parser.parse_args()
    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
    if not prompt.strip():
        parser.error("prompt が空です")
    try:
        result = request(read_endpoint(endpoint_path()), prompt, args.family, args.timeout)
    except RuntimeError as exc:
        print(f"vscode-copilot-chat: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False) if args.json else result["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
