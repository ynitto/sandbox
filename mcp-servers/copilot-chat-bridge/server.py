#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.0.0",
# ]
# ///
"""
Copilot Chat Bridge MCP Server.

Translates MCP tool calls into local HTTP requests against the
``vscode-copilot-chat-bridge`` VS Code extension. The extension exposes
two surfaces:

  * The official ``vscode.lm`` language model API — same models that
    Copilot Chat itself uses, callable programmatically with text in /
    text out semantics.
  * The ``workbench.action.chat.*`` UI commands — for opening a chat
    panel with a prefilled query or starting a fresh session. These are
    fire-and-forget because VS Code does not surface chat UI output to
    extensions.

Environment variables:
    COPILOT_CHAT_BRIDGE_HOST     Bridge host    (default ``127.0.0.1``)
    COPILOT_CHAT_BRIDGE_PORT     Bridge port    (default ``52719``)
    COPILOT_CHAT_BRIDGE_TIMEOUT  HTTP timeout s (default ``600``)

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
DEFAULT_PORT = 52719
DEFAULT_TIMEOUT = 600.0


def _bridge_base_url() -> str:
    host = os.environ.get("COPILOT_CHAT_BRIDGE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("COPILOT_CHAT_BRIDGE_PORT", str(DEFAULT_PORT)))
    return f"http://{host}:{port}"


def _bridge_timeout() -> float:
    raw = os.environ.get("COPILOT_CHAT_BRIDGE_TIMEOUT")
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
            f"Copilot Chat Bridge returned HTTP {err.code} for {method} {path}: "
            f"{detail}"
        ) from err
    except urllib.error.URLError as err:
        raise RuntimeError(
            f"Cannot reach Copilot Chat Bridge at {url}. "
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


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "copilot-chat-bridge",
    instructions=(
        "Talk to the VS Code Copilot Chat surface from outside.\n"
        "\n"
        "Two access modes are available:\n"
        "  * Language Model API (ask_copilot / ask_copilot_with_context):\n"
        "    Programmatic, structured. Returns the model's text response.\n"
        "    Does NOT show up in the user's Chat UI history. Good for\n"
        "    sub-task delegation, second opinions, fan-out queries, etc.\n"
        "    The first request per workspace triggers a one-time consent\n"
        "    dialog inside VS Code; subsequent calls are silent.\n"
        "  * UI commands (open_chat / new_chat_session):\n"
        "    Drive the visible Chat panel so the human can see and\n"
        "    continue the conversation. Fire-and-forget — VS Code does\n"
        "    not surface chat output back to the bridge, so use these\n"
        "    when you want to hand the question to the human, not when\n"
        "    you want to read the answer yourself.\n"
        "\n"
        "Recommended workflow:\n"
        "  1. list_chat_models once at start of a session.\n"
        "  2. Pick a model (vendor='copilot' by default) and call\n"
        "     ask_copilot or ask_copilot_with_context for tasks where\n"
        "     you need the reply back.\n"
        "  3. Use open_chat when you want to surface a draft prompt in\n"
        "     the user's Chat panel for them to inspect / edit / submit.\n"
        "  4. Use new_chat_session before a fresh task whose context\n"
        "     should NOT leak from a previous chat thread."
    ),
)


@mcp.tool()
def list_chat_models(
    vendor: str | None = None,
    family: str | None = None,
) -> str:
    """
    List the language-model chat models registered in VS Code.

    Args:
        vendor: Filter by provider (e.g. ``"copilot"``).
        family: Filter by model family (e.g. ``"gpt-4o"``,
                ``"claude-3.5-sonnet"``).

    Returns:
        JSON array. Each entry has id, name, vendor, family, version,
        maxInputTokens. ``id`` is what you pass to ask_copilot as
        ``model_id`` for an exact pin.
    """
    result = _bridge_request(
        "GET",
        "/api/models",
        query={"vendor": vendor, "family": family},
    )
    return _pack(result)


@mcp.tool()
def ask_copilot(
    prompt: str,
    system: str | None = None,
    vendor: str | None = None,
    family: str | None = None,
    model_id: str | None = None,
    timeout_ms: int | None = None,
    justification: str | None = None,
) -> str:
    """
    Send a single-turn prompt to a VS Code language model and return the
    full response text.

    Use this when you want the model's *answer* back (the human does not
    see the call in their Chat UI history). For showing the question in
    the visible Chat panel, use open_chat instead.

    The first call per workspace pops a consent dialog inside VS Code. If
    the user denies it, this tool returns a ``NoPermissions`` error and
    you should fall back to open_chat.

    Args:
        prompt: User-facing prompt content.
        system: Optional system-style instruction. Inlined into the
                conversation as a tagged "[system instructions]" preamble
                because not all vendors expose a real system role.
        vendor: Vendor selector (default from extension setting,
                normally ``"copilot"``).
        family: Family selector (e.g. ``"gpt-4o"``).
        model_id: Exact model id from list_chat_models — wins over
                  vendor/family when set.
        timeout_ms: Per-request timeout in milliseconds (capped at
                    600 000 by the bridge).
        justification: Optional human-readable reason shown in the
                       consent dialog the first time the user is asked.

    Returns:
        JSON object: {"text": "...", "model": {...}, "timedOut": bool}.
    """
    payload: dict[str, Any] = {"prompt": prompt}
    if system is not None:
        payload["system"] = system
    if vendor is not None:
        payload["vendor"] = vendor
    if family is not None:
        payload["family"] = family
    if model_id is not None:
        payload["modelId"] = model_id
    if timeout_ms is not None:
        payload["timeoutMs"] = timeout_ms
    if justification is not None:
        payload["justification"] = justification
    result = _bridge_request("POST", "/api/ask", payload=payload)
    return _pack(result)


@mcp.tool()
def ask_copilot_with_context(
    prompt: str,
    files: list[str] | None = None,
    use_active_selection: bool = False,
    use_active_editor: bool = False,
    system: str | None = None,
    vendor: str | None = None,
    family: str | None = None,
    model_id: str | None = None,
    timeout_ms: int | None = None,
    justification: str | None = None,
) -> str:
    """
    Same as ask_copilot but attaches editor / file context to the prompt.

    The bridge runs inside VS Code so it can read the user's currently
    active editor, the active selection, and any workspace file path.
    Context blocks are inlined into the user message as fenced code
    blocks labeled with their language id.

    Args:
        prompt: The actual question (rendered after the attached context).
        files: List of file paths to inline. Absolute paths and
               workspace-relative paths are both accepted; relative paths
               resolve against the first workspace folder.
        use_active_selection: When true, include the active editor's
                              current selection (skipped if no selection).
        use_active_editor: When true, include the full content of the
                           active editor.
        system, vendor, family, model_id, timeout_ms, justification:
            Same as ask_copilot.

    Returns:
        JSON object: {"text": "...", "model": {...}, "timedOut": bool}.
    """
    payload: dict[str, Any] = {"prompt": prompt}
    if files:
        payload["files"] = list(files)
    if use_active_selection:
        payload["useActiveSelection"] = True
    if use_active_editor:
        payload["useActiveEditor"] = True
    if system is not None:
        payload["system"] = system
    if vendor is not None:
        payload["vendor"] = vendor
    if family is not None:
        payload["family"] = family
    if model_id is not None:
        payload["modelId"] = model_id
    if timeout_ms is not None:
        payload["timeoutMs"] = timeout_ms
    if justification is not None:
        payload["justification"] = justification
    result = _bridge_request("POST", "/api/ask-with-context", payload=payload)
    return _pack(result)


@mcp.tool()
def open_chat(
    query: str | None = None,
    is_partial_query: bool = False,
    mode: str | None = None,
) -> str:
    """
    Open the VS Code Chat panel, optionally with a prefilled prompt.

    Fire-and-forget — VS Code does NOT return chat output to the bridge.
    Use this to hand a draft question to the human or to surface a long
    prompt in the visible Chat history. To actually read the response,
    use ask_copilot instead.

    Args:
        query: Text to prefill in the Chat input. Without ``query`` the
               panel is opened blank.
        is_partial_query: When true, VS Code treats ``query`` as a partial
                          prompt that the user is expected to extend
                          before submitting (the chat does not auto-send).
        mode: Optional Chat mode hint — ``"ask"``, ``"edit"``, or
              ``"agent"``. Honoured by recent VS Code builds; ignored on
              older ones.

    Returns:
        JSON object: {"success": true}.
    """
    payload: dict[str, Any] = {}
    if query is not None:
        payload["query"] = query
    if is_partial_query:
        payload["isPartialQuery"] = True
    if mode is not None:
        payload["mode"] = mode
    result = _bridge_request("POST", "/api/open", payload=payload)
    return _pack(result)


@mcp.tool()
def new_chat_session() -> str:
    """
    Start a fresh Chat session (analogous to clicking the "+" / New Chat
    button in the Chat UI). Useful as a lightweight ``/clear`` before
    handing the human a brand-new topic via open_chat.

    Returns:
        JSON object: {"success": true, "executed": "<vscode command id>"}.
    """
    result = _bridge_request("POST", "/api/new-session")
    return _pack(result)


@mcp.tool()
def bridge_health() -> str:
    """
    Check that the Copilot Chat Bridge VS Code extension is reachable and
    report how many language-model chat models VS Code currently exposes.

    Returns:
        JSON object: {"status": "ok", "defaultVendor": "...",
        "defaultFamily": ..., "availableModels": N}.
        Raises RuntimeError when the bridge is unreachable.
    """
    result = _bridge_request("GET", "/api/health")
    return _pack(result)


if __name__ == "__main__":
    mcp.run()
