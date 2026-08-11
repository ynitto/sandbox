#!/usr/bin/env python3
"""Run Aider and expose its exact token counts through the agent CLI contract."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _read_usage(path: Path):
    tokens_in = tokens_out = 0
    found = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        props = event.get("properties", {})
        prompt = props.get("prompt_tokens")
        completion = props.get("completion_tokens")
        if (event.get("event") != "message_send"
                or type(prompt) is not int or prompt < 0
                or type(completion) is not int or completion < 0):
            continue
        tokens_in += prompt
        tokens_out += completion
        found = True
    return (tokens_in, tokens_out) if found else None


def main(argv=None):
    fd, name = tempfile.mkstemp(prefix="agent-aider-", suffix=".jsonl")
    os.close(fd)
    log_path = Path(name)
    try:
        try:
            result = subprocess.run(["aider", "--analytics-log", name, *(argv or sys.argv[1:])])
        except FileNotFoundError:
            print("agent-aider: aider command not found", file=sys.stderr)
            return 127
        usage = _read_usage(log_path)
        if usage:
            print(f"@agent-usage tokens_in={usage[0]} tokens_out={usage[1]}", file=sys.stderr)
        return result.returncode
    finally:
        log_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
