#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.0.0",
# ]
# ///
"""
Terminal Bridge MCP Server.

Translates MCP tool calls into local HTTP requests against the
``vscode-terminal-bridge`` VS Code extension running on the user's machine.
The extension listens on a loopback TCP port (default ``52718``) and exposes
the REST surface documented in
``vscode-extensions/terminal-bridge/README.md``.

Environment variables:
    TERMINAL_BRIDGE_HOST    Bridge host (default ``127.0.0.1``)
    TERMINAL_BRIDGE_PORT    Bridge port (default ``52718``)
    TERMINAL_BRIDGE_TIMEOUT Per-request HTTP timeout, seconds (default ``180``)

Usage:
    uv run server.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# HTTP client to the VS Code extension
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 52718
DEFAULT_TIMEOUT = 180.0


def _bridge_base_url() -> str:
    host = os.environ.get("TERMINAL_BRIDGE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("TERMINAL_BRIDGE_PORT", str(DEFAULT_PORT)))
    return f"http://{host}:{port}"


def _bridge_timeout() -> float:
    raw = os.environ.get("TERMINAL_BRIDGE_TIMEOUT")
    if raw is None:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT


def _bridge_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> Any:
    """Send a JSON request to the bridge extension and return the decoded body."""
    url = _bridge_base_url() + path
    if query:
        clean = {k: v for k, v in query.items() if v is not None}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"

    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_bridge_timeout()) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Terminal Bridge returned HTTP {err.code} for {method} {path}: {detail}"
        ) from err
    except urllib.error.URLError as err:
        raise RuntimeError(
            f"Cannot reach Terminal Bridge at {url}. "
            "Is the VS Code extension installed and running? "
            f"Underlying error: {err.reason}"
        ) from err

    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Non-JSON response from bridge: {body!r}") from err


def _pack(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _locator(
    terminal_index: int | None,
    terminal_name: str | None,
    process_id: int | None,
) -> dict[str, Any]:
    locator: dict[str, Any] = {}
    if terminal_index is not None:
        locator["terminalIndex"] = terminal_index
    if terminal_name is not None:
        locator["terminalName"] = terminal_name
    if process_id is not None:
        locator["processId"] = process_id
    return locator


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "terminal-bridge",
    instructions=(
        "Drive the user's VS Code integrated terminals through the "
        "Terminal Bridge extension. Use list_terminals first to discover "
        "what is currently open, then prefer execute_in_terminal (which "
        "requires VS Code shell integration) to run commands and capture "
        "output. Use send_to_terminal for text that is not a complete "
        "command (e.g. answering prompts) or when shell integration is "
        "unavailable. wait_for_output is useful after send_to_terminal to "
        "synchronize with a known prompt or log line."
    ),
)


@mcp.tool()
def list_terminals() -> str:
    """
    List the integrated terminals that are currently open in VS Code.

    Returns:
        JSON array of terminals with fields:
          - index: 0-based position in vscode.window.terminals
          - name:  display name shown in the VS Code UI
          - processId: OS process id (may be null)
          - hasShellIntegration: true when /api/execute is usable
          - cwd: working directory if shell integration reports it
    """
    result = _bridge_request("GET", "/api/terminals")
    return _pack(result)


@mcp.tool()
def execute_in_terminal(
    command: str,
    terminal_index: int | None = None,
    terminal_name: str | None = None,
    process_id: int | None = None,
) -> str:
    """
    Run a shell command in the targeted terminal and return captured output.

    The targeted terminal must have VS Code shell integration active.
    Provide exactly one of terminal_index / terminal_name / process_id;
    if none are given the call returns an error.

    Args:
        command: Command line to execute (single line, no trailing newline).
        terminal_index: Index from list_terminals.
        terminal_name: Terminal display name.
        process_id: OS pid as reported by list_terminals.

    Returns:
        JSON object: {"output": "..."} containing stdout+stderr captured
        through the shell integration stream.
    """
    payload = _locator(terminal_index, terminal_name, process_id)
    payload["command"] = command
    result = _bridge_request("POST", "/api/execute", payload=payload)
    return _pack(result)


@mcp.tool()
def send_to_terminal(
    text: str,
    terminal_index: int | None = None,
    terminal_name: str | None = None,
    process_id: int | None = None,
) -> str:
    """
    Write text to the terminal as if the user typed it.

    Unlike execute_in_terminal, this does not wait for or return output.
    Append a newline character to submit a command line. Combine with
    wait_for_output or read_terminal_output to inspect what happens next.

    Args:
        text: Raw text to send. Include "\\n" to commit a command.
        terminal_index / terminal_name / process_id: Terminal selector.

    Returns:
        JSON object: {"success": true}.
    """
    payload = _locator(terminal_index, terminal_name, process_id)
    payload["text"] = text
    result = _bridge_request("POST", "/api/send", payload=payload)
    return _pack(result)


@mcp.tool()
def read_terminal_output(terminal_name: str | None = None) -> str:
    """
    Read captured terminal output from the bridge's ring buffer.

    Args:
        terminal_name: When set, returns the captured lines for that terminal.
                       When omitted, returns the list of terminal names that
                       have any captured output available.

    Returns:
        JSON object — either {"terminal": name, "lines": [...]} or
        {"available": [name, ...]}.
    """
    result = _bridge_request(
        "GET",
        "/api/output",
        query={"terminal": terminal_name},
    )
    return _pack(result)


@mcp.tool()
def create_terminal(
    name: str | None = None,
    command: str | None = None,
    cwd: str | None = None,
) -> str:
    """
    Create a new VS Code integrated terminal and optionally send an initial
    command.

    Args:
        name: Optional display name (VS Code may suffix it for uniqueness).
        command: If set, the bridge calls Terminal.sendText after creation.
                 No newline is appended automatically — include "\\n" to run.
        cwd: Initial working directory. Ignored if VS Code rejects the path.

    Returns:
        JSON object with index, name, hasShellIntegration. Shell integration
        usually needs a moment to activate; the bridge waits briefly before
        reporting.
    """
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if command is not None:
        payload["command"] = command
    if cwd is not None:
        payload["cwd"] = cwd
    result = _bridge_request("POST", "/api/create", payload=payload)
    return _pack(result)


@mcp.tool()
def close_terminal(
    terminal_index: int | None = None,
    terminal_name: str | None = None,
    process_id: int | None = None,
) -> str:
    """
    Dispose of a VS Code terminal.

    Args:
        terminal_index / terminal_name / process_id: Terminal selector.

    Returns:
        JSON object: {"success": true, "closed": "<name>"}.
    """
    payload = _locator(terminal_index, terminal_name, process_id)
    result = _bridge_request("POST", "/api/close", payload=payload)
    return _pack(result)


@mcp.tool()
def wait_for_output(
    pattern: str,
    terminal_index: int | None = None,
    terminal_name: str | None = None,
    process_id: int | None = None,
    timeout_ms: int | None = None,
) -> str:
    """
    Block until captured output for the terminal matches a JavaScript regex.

    Only output produced after this call begins (relative to the bridge's
    ring buffer) is considered.

    Args:
        pattern: ECMAScript regular expression source. The bridge constructs
                 ``new RegExp(pattern)`` on the VS Code side.
        terminal_index / terminal_name / process_id: Terminal selector.
        timeout_ms: Maximum wait in milliseconds. Default 30 000, capped at
                    120 000 by the bridge.

    Returns:
        JSON object: {"matched": bool, "matchedText": str?, "output": str,
        "timedOut": bool?}.
    """
    payload = _locator(terminal_index, terminal_name, process_id)
    payload["pattern"] = pattern
    if timeout_ms is not None:
        payload["timeoutMs"] = timeout_ms
    result = _bridge_request("POST", "/api/wait-for-output", payload=payload)
    return _pack(result)


@mcp.tool()
def bridge_health() -> str:
    """
    Check that the Terminal Bridge VS Code extension is reachable.

    Returns:
        JSON object: {"status": "ok", "terminals": N, "capturedTerminals": [...]}.
        Raises RuntimeError when the bridge is unreachable.
    """
    result = _bridge_request("GET", "/api/health")
    return _pack(result)


if __name__ == "__main__":
    mcp.run()
