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
        "Drive the user's VS Code integrated terminals so the human can watch "
        "what you do in real time.\n"
        "\n"
        "Recommended workflows:\n"
        "  * Discover state first: call list_terminals before doing anything "
        "    else. Reuse an existing terminal when its name matches your task "
        "    (e.g. 'api-server', 'frontend'); only create a new one when you "
        "    need an isolated context.\n"
        "  * Short, output-bearing commands: use execute_in_terminal. It blocks "
        "    until completion and returns combined stdout/stderr.\n"
        "  * Long-running processes (dev servers, log tails, watchers): use "
        "    create_terminal with a descriptive name + send_to_terminal to fire "
        "    the command, then wait_for_output to confirm it really started.\n"
        "  * Periodic monitoring or post-hoc inspection: read_terminal_output "
        "    returns the last ~200 lines captured per terminal.\n"
        "  * Driving another CLI agent inside a sibling terminal: use "
        "    send_to_terminal — never put '\\n' (line breaks) inside the text "
        "    argument or the receiving agent's input box will be left in a bad "
        "    state. Submit by sending a separate empty send_to_terminal with "
        "    text='\\n' or by using the agent's own submit shortcut.\n"
        "\n"
        "Prefer running commands here over your built-in shell tool whenever a "
        "human is supposed to see the command running, when you need a "
        "dedicated long-lived process, or when you need a clean context "
        "boundary (e.g. starting another agent in a fresh terminal as a "
        "lightweight '/clear')."
    ),
)


@mcp.tool()
def list_terminals() -> str:
    """
    List the integrated terminals that are currently open in VS Code.

    Always call this first when you don't know the current terminal state.
    Reuse an existing terminal when its name matches your intent — only fall
    back to create_terminal for genuinely new work, otherwise the user ends
    up with dozens of look-alike tabs.

    Returns:
        JSON array of terminals with fields:
          - index: 0-based position in vscode.window.terminals
          - name:  display name shown in the VS Code UI
          - processId: OS process id (may be null)
          - hasShellIntegration: true when execute_in_terminal is usable
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

    Use this for short, output-bearing commands (build, test, grep, status
    queries). The call blocks until the shell reports completion via the
    shell integration stream and then returns the combined stdout+stderr.

    For long-running processes (dev servers, watchers, REPLs), do NOT use
    this tool — it will hang. Use create_terminal + send_to_terminal +
    wait_for_output instead so the user can keep watching the live tab.

    Requirements:
      * The targeted terminal must have VS Code shell integration active.
        Confirm via list_terminals (hasShellIntegration=true). Shell
        integration works in bash / zsh / pwsh / fish on VS Code 1.93+.
      * Provide exactly one of terminal_index / terminal_name / process_id.

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

    Unlike execute_in_terminal, this is fire-and-forget — it does not wait
    for or return output. Typical uses:

      * Start a long-running process: send_to_terminal text="npm run dev\\n"
        then wait_for_output pattern="ready in" to confirm startup.
      * Answer an interactive prompt (yes/no, password, REPL input).
      * Talk to another CLI agent running in a sibling terminal (e.g. another
        Claude Code, Codex CLI).

    CRITICAL — when targeting another interactive CLI agent (Claude Code,
    Codex, etc.) do NOT include line breaks ('\\n') inside `text`. Multiline
    input is pasted into the agent's prompt area and tends to leave its input
    box in a broken state instead of submitting. Send the visible text in one
    call, then submit it with a second send_to_terminal whose text is exactly
    "\\n" (or use the receiving agent's own submit shortcut).

    Args:
        text: Raw text to send. For a normal shell, include "\\n" to commit.
              For another interactive agent, keep text on a single line and
              submit separately.
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

    The bridge stores only the most recent ~200 lines per terminal name
    (configurable via the `terminalBridge.captureBufferLines` setting), so
    treat this as a recent-tail view, not a full log. For exhaustive logs,
    have the process write to a file and tail it.

    Useful for monitoring long-running processes started via send_to_terminal,
    spying on a Claude Code running in another terminal, or inspecting a
    server's startup output without blocking on wait_for_output.

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

    Always give a descriptive `name` ("api-server", "frontend", "test-watch",
    "claude-impl-worker", etc.). Later calls can target this terminal by
    name, and the user can find it in the VS Code terminal dropdown.

    Useful as a lightweight "/clear": create a terminal and launch a fresh
    `claude` / `codex` inside it to hand off a self-contained task in a clean
    context window.

    Args:
        name: Optional display name (VS Code may suffix it for uniqueness).
        command: If set, the bridge calls Terminal.sendText after creation.
                 No newline is appended automatically — include "\\n" to run.
        cwd: Initial working directory. Ignored if VS Code rejects the path.

    Returns:
        JSON object with index, name, hasShellIntegration. Shell integration
        usually needs a moment to activate; the bridge waits briefly before
        reporting. If hasShellIntegration is false on the first read, call
        list_terminals again after ~1s before deciding execute_in_terminal
        is unusable.
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
    ring buffer) is considered. Pair this with send_to_terminal to
    synchronise on a known prompt or log line — e.g. send "npm run dev\\n"
    then wait_for_output pattern="Local:.*http" to confirm the dev server is
    actually listening before proceeding.

    Args:
        pattern: ECMAScript regular expression source. The bridge constructs
                 ``new RegExp(pattern)`` on the VS Code side. Escape
                 backslashes in the JSON tool call (e.g. "\\\\d+").
        terminal_index / terminal_name / process_id: Terminal selector.
        timeout_ms: Maximum wait in milliseconds. Default 30 000, capped at
                    120 000 by the bridge. If you suspect the process is just
                    slow, call wait_for_output again instead of bumping the
                    timeout — the user can see progress in the terminal.

    Returns:
        JSON object: {"matched": bool, "matchedText": str?, "output": str,
        "timedOut": bool?}. On timeout, inspect `output` and decide whether
        to keep waiting or take corrective action.
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
