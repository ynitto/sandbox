#!/usr/bin/env python3
"""Run Aider and expose its exact token counts through the agent CLI contract."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path


# ---------------------------------------------------------------------------
# 環境の補完（非ログインシェル対策）
#
# agentcore/ollama_adapter.py の同名ブロックの複製（正典はあちら）。agent-aider は
# install.sh がこのファイル 1 枚を ~/.local/bin へコピーして配るため、agentcore を
# import できない。直すときは 3 箇所（ollama_adapter / ここ / agent-opencode）を揃えること。
# aider は接続先を OLLAMA_API_BASE（litellm）で読むので、~/.profile の補完が無いと
# 既定の localhost へ向かうか、接続がプロキシへ流れて 504 Gateway Timeout になる。
# ---------------------------------------------------------------------------
_PROFILE_ENV_PREFIXES = ("OLLAMA_", "AGENT_OLLAMA_")
_PROFILE_ENV_EXACT = ("NO_PROXY", "no_proxy")


def _complete_ollama_env() -> None:
    host = os.environ.get("OLLAMA_HOST", "")
    base = os.environ.get("OLLAMA_API_BASE", "")
    if host and not base:
        os.environ["OLLAMA_API_BASE"] = host if "://" in host else f"http://{host}"
    elif base and not host:
        os.environ["OLLAMA_HOST"] = base
    target = os.environ.get("OLLAMA_API_BASE") or os.environ.get("OLLAMA_HOST") or ""
    try:
        hostname = urllib.parse.urlsplit(
            target if "://" in target else f"//{target}").hostname
    except ValueError:
        hostname = None
    hosts = [hostname] if hostname else ["localhost", "127.0.0.1"]
    entries: "list[str]" = []
    for var in ("NO_PROXY", "no_proxy"):
        for item in os.environ.get(var, "").split(","):
            item = item.strip()
            if item and item not in entries:
                entries.append(item)
    entries.extend(h for h in hosts if h not in entries)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = ",".join(entries)


def _import_profile_env(path: str) -> dict:
    profile = os.path.expanduser(path)
    if not os.path.isfile(profile):
        return {}
    # profile を source した後の環境を JSON で受け取る。stdin は閉じる——
    # このプロセスの stdin に本文が来る呼び方でも profile に読ませない。
    dump = "import json, os; print(json.dumps(dict(os.environ)))"
    try:
        proc = subprocess.run(
            ["sh", "-c", '. "$1" >/dev/null 2>&1; exec "$2" -c "$3"',
             "sh", profile, sys.executable, dump],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    imported: dict = {}
    for name, value in data.items():
        if ((name.startswith(_PROFILE_ENV_PREFIXES) or name in _PROFILE_ENV_EXACT)
                and name not in os.environ and isinstance(value, str)):
            os.environ[name] = value
            imported[name] = value
    return imported


def load_profile_env(path: str = "~/.profile") -> dict:
    imported: dict = {}
    if not (os.environ.get("OLLAMA_HOST") and os.environ.get("OLLAMA_API_BASE")
            and (os.environ.get("NO_PROXY") or os.environ.get("no_proxy"))):
        imported = _import_profile_env(path)
    _complete_ollama_env()
    return imported


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
    # aider（litellm）を起動する前に環境を補完する。子プロセスは環境を継承する。
    load_profile_env()
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
