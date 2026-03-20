#!/usr/bin/env python3
"""
GitLab MCP Server for issue-driven development.

Provides MCP tools for interacting with a self-hosted GitLab instance.
The target repository is auto-detected from the git remote of the workspace.

Environment variables:
  GITLAB_TOKEN or GL_TOKEN   Personal Access Token (required)
  GITLAB_WORKSPACE           Path to workspace git repo (default: current working dir)

Usage:
  GITLAB_TOKEN=glpat-xxx python server.py

Claude Code configuration (~/.claude.json or workspace .mcp.json):
  {
    "mcpServers": {
      "gitlab": {
        "command": "python",
        "args": ["/path/to/mcp-servers/gitlab/server.py"],
        "cwd": "/path/to/your/workspace",
        "env": {
          "GITLAB_TOKEN": "glpat-xxxxxxxxxxxx"
        }
      }
    }
  }
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# GitLab API helpers
# ---------------------------------------------------------------------------

def get_workspace() -> str:
    """Return the workspace directory (env var or current dir)."""
    return os.environ.get("GITLAB_WORKSPACE", os.getcwd())


def get_token() -> str:
    token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GL_TOKEN")
    if not token:
        raise RuntimeError(
            "GITLAB_TOKEN or GL_TOKEN environment variable is required.\n"
            "Example: export GITLAB_TOKEN=glpat-xxxxxxxxxxxx"
        )
    return token


def get_project_info_from_remote() -> tuple[str, str]:
    """Parse git remote origin URL to extract GitLab host and project path."""
    workspace = get_workspace()
    try:
        remote_url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
            cwd=workspace,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise RuntimeError(
            f"Cannot get git remote 'origin' from {workspace}. "
            "Make sure GITLAB_WORKSPACE points to a git repository."
        ) from e

    # SSH:   git@gitlab.example.com:namespace/repo.git
    # HTTPS: https://gitlab.example.com/namespace/repo.git
    # HTTPS with token: https://oauth2:TOKEN@gitlab.example.com/namespace/repo.git
    if remote_url.startswith("git@"):
        without_prefix = remote_url[4:]
        host, path = without_prefix.split(":", 1)
        project = path.rstrip("/")
        if project.endswith(".git"):
            project = project[:-4]
    elif "://" in remote_url:
        parsed = urllib.parse.urlparse(remote_url)
        host = parsed.hostname
        project = parsed.path.lstrip("/")
        if project.endswith(".git"):
            project = project[:-4]
    else:
        raise RuntimeError(f"Unknown remote URL format: {remote_url}")

    return host, project


def encode_project(project: str) -> str:
    return urllib.parse.quote(project, safe="")


def gitlab_api(
    method: str,
    path: str,
    data: dict | None = None,
    params: dict | None = None,
    host: str | None = None,
    token: str | None = None,
) -> Any:
    """Make a GitLab REST API call and return parsed JSON."""
    if host is None or token is None:
        _host, _project = get_project_info_from_remote()
        if host is None:
            host = _host
        if token is None:
            token = get_token()

    url = f"https://{host}/api/v4{path}"
    if params:
        filtered = {k: v for k, v in params.items() if v is not None}
        if filtered:
            url = url + "?" + urllib.parse.urlencode(filtered)

    headers = {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read()
            return json.loads(content) if content.strip() else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitLab API error: HTTP {e.code} {e.reason}\n{msg}") from e


def _ctx() -> tuple[str, str, str]:
    """Return (host, encoded_project, token) for the current workspace."""
    host, project = get_project_info_from_remote()
    token = get_token()
    ep = encode_project(project)
    return host, ep, token


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "gitlab",
    instructions=(
        "GitLab MCP server for issue-driven development. "
        "Provides tools to fetch issues, create merge requests, post comments, "
        "and handle review feedback. "
        "The target repository is auto-detected from the workspace git remote."
    ),
)


# --- Project / User ---

@mcp.tool()
def get_project_info() -> str:
    """
    Get the GitLab project information parsed from the workspace git remote.
    Returns host, project path, and the project's web URL.
    """
    host, project = get_project_info_from_remote()
    result = {
        "host": host,
        "project": project,
        "base_url": f"https://{host}/{project}",
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def get_current_user() -> str:
    """
    Get information about the authenticated GitLab user (via GITLAB_TOKEN).
    Returns username, name, email, and user ID.
    """
    host, ep, token = _ctx()
    user = gitlab_api("GET", "/user", host=host, token=token)
    return json.dumps(user, ensure_ascii=False, indent=2)


# --- Issues ---

@mcp.tool()
def list_issues(
    state: str = "opened",
    labels: str | None = None,
    assignee_username: str | None = None,
    author_username: str | None = None,
) -> str:
    """
    List issues in the project.

    Args:
        state: Issue state filter — "opened", "closed", or "all". Default: "opened".
        labels: Comma-separated label names to filter by (AND condition).
                Example: "status:open,priority:high"
        assignee_username: Filter by assignee's GitLab username.
        author_username: Filter by author's GitLab username.

    Returns:
        JSON array of issue objects. Key fields per issue:
          iid, title, description, state, labels, author, assignee, web_url, created_at
    """
    host, ep, token = _ctx()
    params = {
        "per_page": 100,
        "state": state,
        "labels": labels,
        "assignee_username": assignee_username,
        "author_username": author_username,
    }
    issues = gitlab_api("GET", f"/projects/{ep}/issues", params=params, host=host, token=token)
    return json.dumps(issues, ensure_ascii=False, indent=2)


@mcp.tool()
def get_issue(issue_iid: int) -> str:
    """
    Get a single issue by its IID (project-level issue number).

    Args:
        issue_iid: The issue IID (the number shown in the GitLab UI, e.g. #42).

    Returns:
        JSON object with full issue details including description, labels,
        assignees, milestone, and web_url.
    """
    host, ep, token = _ctx()
    issue = gitlab_api("GET", f"/projects/{ep}/issues/{issue_iid}", host=host, token=token)
    return json.dumps(issue, ensure_ascii=False, indent=2)


@mcp.tool()
def get_issue_comments(issue_iid: int) -> str:
    """
    Get all comments (notes) on an issue.

    Args:
        issue_iid: The issue IID.

    Returns:
        JSON array of note objects. Key fields: id, body, author, created_at.
        Notes are returned in chronological order.
    """
    host, ep, token = _ctx()
    notes = gitlab_api(
        "GET",
        f"/projects/{ep}/issues/{issue_iid}/notes",
        params={"per_page": 100, "sort": "asc"},
        host=host,
        token=token,
    )
    return json.dumps(notes, ensure_ascii=False, indent=2)


@mcp.tool()
def create_issue_comment(issue_iid: int, body: str) -> str:
    """
    Post a comment on an issue.

    Args:
        issue_iid: The issue IID.
        body: Comment body in Markdown format.
              Typically used to report work completion, request review,
              or acknowledge review feedback.

    Returns:
        JSON object of the created note with id, body, author, web_url.
    """
    host, ep, token = _ctx()
    note = gitlab_api(
        "POST",
        f"/projects/{ep}/issues/{issue_iid}/notes",
        data={"body": body},
        host=host,
        token=token,
    )
    return json.dumps(note, ensure_ascii=False, indent=2)


@mcp.tool()
def update_issue(
    issue_iid: int,
    state_event: str | None = None,
    add_labels: str | None = None,
    remove_labels: str | None = None,
    assignee_username: str | None = None,
) -> str:
    """
    Update an issue's state, labels, or assignee.

    Args:
        issue_iid: The issue IID.
        state_event: "close" to close the issue, "reopen" to reopen it.
        add_labels: Comma-separated label names to add.
        remove_labels: Comma-separated label names to remove.
        assignee_username: Set the assignee by GitLab username.
                           Pass an empty string "" to unassign.

    Returns:
        JSON object of the updated issue.
    """
    host, ep, token = _ctx()
    data: dict[str, Any] = {}

    if add_labels or remove_labels:
        issue = gitlab_api("GET", f"/projects/{ep}/issues/{issue_iid}", host=host, token=token)
        current = set(issue.get("labels", []))
        if add_labels:
            for lbl in add_labels.split(","):
                current.add(lbl.strip())
        if remove_labels:
            for lbl in remove_labels.split(","):
                current.discard(lbl.strip())
        data["labels"] = ",".join(sorted(current))

    if assignee_username is not None:
        if assignee_username == "":
            data["assignee_ids"] = []
        else:
            users = gitlab_api(
                "GET", "/users",
                params={"username": assignee_username},
                host=host, token=token,
            )
            if users:
                data["assignee_ids"] = [users[0]["id"]]

    if state_event:
        data["state_event"] = state_event

    updated = gitlab_api(
        "PUT", f"/projects/{ep}/issues/{issue_iid}",
        data=data, host=host, token=token,
    )
    return json.dumps(updated, ensure_ascii=False, indent=2)


# --- Merge Requests ---

@mcp.tool()
def list_merge_requests(
    state: str = "opened",
    source_branch: str | None = None,
) -> str:
    """
    List merge requests in the project.

    Args:
        state: "opened", "closed", "merged", or "all". Default: "opened".
        source_branch: Filter by source branch name.

    Returns:
        JSON array of MR objects. Key fields: iid, title, state,
        source_branch, target_branch, author, web_url.
    """
    host, ep, token = _ctx()
    params = {
        "per_page": 100,
        "state": state,
        "source_branch": source_branch,
    }
    mrs = gitlab_api("GET", f"/projects/{ep}/merge_requests", params=params, host=host, token=token)
    return json.dumps(mrs, ensure_ascii=False, indent=2)


@mcp.tool()
def get_merge_request(mr_iid: int) -> str:
    """
    Get a single merge request by its IID.

    Args:
        mr_iid: The MR IID (project-level number shown in GitLab UI).

    Returns:
        JSON object with full MR details including description, state,
        reviewers, and diff_refs.
    """
    host, ep, token = _ctx()
    mr = gitlab_api("GET", f"/projects/{ep}/merge_requests/{mr_iid}", host=host, token=token)
    return json.dumps(mr, ensure_ascii=False, indent=2)


@mcp.tool()
def create_merge_request(
    title: str,
    source_branch: str,
    target_branch: str = "main",
    description: str = "",
    draft: bool = False,
    issue_iid: int | None = None,
) -> str:
    """
    Create a merge request.

    Args:
        title: MR title (concise, imperative mood).
        source_branch: The feature branch to merge from.
        target_branch: The base branch to merge into. Default: "main".
        description: MR description in Markdown. Include what was done,
                     how to test, and reference the issue (e.g. "Closes #42").
        draft: If True, creates the MR as a Draft (WIP). Default: False.
        issue_iid: If provided, appends "Closes #<iid>" to the description
                   to auto-close the issue on merge.

    Returns:
        JSON object of the created MR with iid, web_url, and state.
    """
    host, ep, token = _ctx()

    if issue_iid is not None:
        closes_line = f"\n\nCloses #{issue_iid}"
        description = description + closes_line if description else closes_line.strip()

    mr_title = f"Draft: {title}" if draft else title
    data: dict[str, Any] = {
        "title": mr_title,
        "source_branch": source_branch,
        "target_branch": target_branch,
        "description": description,
    }
    if draft:
        data["draft"] = True

    mr = gitlab_api("POST", f"/projects/{ep}/merge_requests", data=data, host=host, token=token)
    return json.dumps(mr, ensure_ascii=False, indent=2)


@mcp.tool()
def update_merge_request(
    mr_iid: int,
    title: str | None = None,
    description: str | None = None,
    state_event: str | None = None,
    draft: bool | None = None,
    target_branch: str | None = None,
) -> str:
    """
    Update a merge request.

    Args:
        mr_iid: The MR IID.
        title: New title. Pass without "Draft: " prefix; use draft param to toggle.
        description: New description in Markdown.
        state_event: "close" to close the MR, "reopen" to reopen it.
        draft: True to mark as draft, False to mark as ready.
        target_branch: Change the target/base branch.

    Returns:
        JSON object of the updated MR.
    """
    host, ep, token = _ctx()
    data: dict[str, Any] = {}

    if title is not None:
        data["title"] = title
    if description is not None:
        data["description"] = description
    if state_event is not None:
        data["state_event"] = state_event
    if draft is not None:
        data["draft"] = draft
    if target_branch is not None:
        data["target_branch"] = target_branch

    mr = gitlab_api(
        "PUT", f"/projects/{ep}/merge_requests/{mr_iid}",
        data=data, host=host, token=token,
    )
    return json.dumps(mr, ensure_ascii=False, indent=2)


# --- MR Review / Discussions ---

@mcp.tool()
def list_mr_discussions(mr_iid: int) -> str:
    """
    List all discussions (threads) on a merge request.

    This includes inline review comments, general MR notes, and system notes.
    Each discussion has one or more notes. Review comments have position info
    (file path, line number).

    Args:
        mr_iid: The MR IID.

    Returns:
        JSON array of discussion objects. Key fields per discussion:
          id, individual_note (bool), resolved (bool),
          notes[].body, notes[].author, notes[].position (for inline comments).

        Filter for unresolved review threads:
          [d for d in discussions if not d["individual_note"] and not d["resolved"]]
    """
    host, ep, token = _ctx()
    discussions = gitlab_api(
        "GET",
        f"/projects/{ep}/merge_requests/{mr_iid}/discussions",
        params={"per_page": 100},
        host=host,
        token=token,
    )
    return json.dumps(discussions, ensure_ascii=False, indent=2)


@mcp.tool()
def create_mr_note(mr_iid: int, body: str) -> str:
    """
    Post a general comment on a merge request (not inline).

    Use this to acknowledge review comments, summarize changes made,
    or request re-review after addressing feedback.

    Args:
        mr_iid: The MR IID.
        body: Comment body in Markdown.

    Returns:
        JSON object of the created note.
    """
    host, ep, token = _ctx()
    note = gitlab_api(
        "POST",
        f"/projects/{ep}/merge_requests/{mr_iid}/notes",
        data={"body": body},
        host=host,
        token=token,
    )
    return json.dumps(note, ensure_ascii=False, indent=2)


@mcp.tool()
def reply_to_mr_discussion(mr_iid: int, discussion_id: str, body: str) -> str:
    """
    Reply to an existing discussion thread on a merge request.

    Use this to respond to specific review comments in their thread context.

    Args:
        mr_iid: The MR IID.
        discussion_id: The discussion ID (string, from list_mr_discussions).
        body: Reply body in Markdown. Explain the change you made or
              why you disagree with the reviewer's suggestion.

    Returns:
        JSON object of the created note.
    """
    host, ep, token = _ctx()
    note = gitlab_api(
        "POST",
        f"/projects/{ep}/merge_requests/{mr_iid}/discussions/{discussion_id}/notes",
        data={"body": body},
        host=host,
        token=token,
    )
    return json.dumps(note, ensure_ascii=False, indent=2)


@mcp.tool()
def resolve_mr_discussion(mr_iid: int, discussion_id: str, resolved: bool = True) -> str:
    """
    Mark a discussion thread on a merge request as resolved (or unresolved).

    Call this after addressing the review comment and optionally replying.

    Args:
        mr_iid: The MR IID.
        discussion_id: The discussion ID (string, from list_mr_discussions).
        resolved: True to resolve, False to unresolve. Default: True.

    Returns:
        JSON object of the updated discussion.
    """
    host, ep, token = _ctx()
    discussion = gitlab_api(
        "PUT",
        f"/projects/{ep}/merge_requests/{mr_iid}/discussions/{discussion_id}",
        data={"resolved": resolved},
        host=host,
        token=token,
    )
    return json.dumps(discussion, ensure_ascii=False, indent=2)


# --- Utility ---

@mcp.tool()
def make_branch_name(issue_iid: int) -> str:
    """
    Generate a branch name for an issue following the convention:
    feature/issue-{iid}-{slug}

    The slug is derived from the issue title (ASCII chars only, lowercased,
    spaces replaced with hyphens, max 40 chars).

    Args:
        issue_iid: The issue IID.

    Returns:
        The branch name string, e.g. "feature/issue-42-add-login-page".
    """
    import re
    host, ep, token = _ctx()
    issue = gitlab_api("GET", f"/projects/{ep}/issues/{issue_iid}", host=host, token=token)
    title = issue.get("title", "")
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")[:40]
    slug = slug or "task"
    return f"feature/issue-{issue_iid}-{slug}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
