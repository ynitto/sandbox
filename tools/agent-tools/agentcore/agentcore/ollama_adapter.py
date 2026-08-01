"""Ollama の非ストリーミング API を CLI 契約へ変換する小さなアダプター。"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def generate(model: str, prompt: str) -> dict:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if "://" not in host:
        host = f"http://{host}"
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"ollama API error ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ollama に接続できません: {exc.reason}") from exc


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: agent-ollama <model>", file=sys.stderr)
        return 2
    try:
        result = generate(args[0], sys.stdin.read())
        print(str(result.get("response") or ""), end="")
        print(
            f"@agent-usage tokens_in={int(result.get('prompt_eval_count') or 0)} "
            f"tokens_out={int(result.get('eval_count') or 0)}",
            file=sys.stderr)
        return 0
    except (RuntimeError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
