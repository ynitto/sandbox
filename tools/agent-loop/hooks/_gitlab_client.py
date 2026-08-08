"""Minimal GitLab REST client for agent-loop hooks (stdlib only)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HTTP_TIMEOUT_SEC = 15
MAX_BODY_BYTES = 5 * 1024 * 1024
DEFAULT_TOKEN_ENVS = ("GITLAB_TOKEN", "GL_TOKEN")


@dataclass(frozen=True)
class GitLabConnection:
    host: str
    project_path: str
    api_url: str
    public_url: str | None
    token_env: str | None


class GitLabError(Exception):
    def __init__(self, message: str, *, status: int | None = None, kind: str = "error"):
        super().__init__(message)
        self.status = status
        self.kind = kind  # auth | transient | error


def _parse_connections_text(text: str) -> list[dict[str, Any]]:
    """Parse gitlab connection list without external deps (simple YAML subset)."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_gitlab = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "gitlab:":
            in_gitlab = True
            continue
        if not in_gitlab:
            continue
        if stripped.startswith("- "):
            if current:
                entries.append(current)
            current = {}
            rest = stripped[2:].strip()
            if rest and ":" in rest:
                k, _, v = rest.partition(":")
                current[k.strip()] = v.strip().strip("'\"")
            continue
        if current is not None and ":" in stripped:
            k, _, v = stripped.partition(":")
            current[k.strip()] = v.strip().strip("'\"")
    if current:
        entries.append(current)
    return entries


def load_gitlab_connections(workspace: str | None) -> list[dict[str, Any]]:
    paths: list[Path] = []
    if workspace:
        paths.append(Path(workspace) / ".agents" / "connections.yaml")
    paths.append(Path.home() / ".agents" / "connections.yaml")
    out: list[dict[str, Any]] = []
    seen_hosts: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for entry in _parse_connections_text(text):
            host = str(entry.get("host") or "").strip()
            if host and host not in seen_hosts:
                seen_hosts.add(host)
                out.append(entry)
    return out


def parse_git_remote(cwd: str | Path | None) -> tuple[str, str]:
    """Return (host, project_path) from git remote origin."""
    work = Path(cwd or os.getcwd())
    try:
        remote_url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=str(work),
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
        raise GitLabError("cannot resolve git remote origin", kind="error")

    if remote_url.startswith("git@"):
        without_prefix = remote_url[4:]
        host, _, path = without_prefix.partition(":")
        project = path.rstrip("/")
        if project.endswith(".git"):
            project = project[:-4]
        if host and project:
            return host, project
    elif "://" in remote_url:
        parsed = urllib.parse.urlparse(remote_url)
        host = parsed.hostname or ""
        project = parsed.path.lstrip("/")
        if project.endswith(".git"):
            project = project[:-4]
        if host and project:
            return host, project
    raise GitLabError("unsupported git remote URL format", kind="error")


def _host_matches(entry_host: str, remote_host: str) -> bool:
    return entry_host.lower() == remote_host.lower()


def resolve_connection(
    host: str,
    project_path: str,
    workspace: str | None,
) -> GitLabConnection:
    for entry in load_gitlab_connections(workspace):
        entry_host = str(entry.get("host") or "").strip()
        if not entry_host or not _host_matches(entry_host, host):
            continue
        api_url = str(entry.get("api_url") or f"https://{entry_host}/api/v4").rstrip("/")
        public_url = str(entry.get("public_url") or "").strip() or None
        token_env = str(entry.get("token_env") or "").strip() or None
        return GitLabConnection(
            host=entry_host,
            project_path=project_path,
            api_url=api_url,
            public_url=public_url,
            token_env=token_env,
        )
    return GitLabConnection(
        host=host,
        project_path=project_path,
        api_url=f"https://{host}/api/v4",
        public_url=None,
        token_env=None,
    )


def resolve_token(conn: GitLabConnection) -> str:
    candidates: list[str] = []
    if conn.token_env:
        candidates.append(conn.token_env)
    candidates.extend(DEFAULT_TOKEN_ENVS)
    for name in candidates:
        val = os.environ.get(name)
        if val:
            return val
    raise GitLabError("gitlab token not configured", kind="auth")


def _rewrite_public_url(value: str, conn: GitLabConnection) -> str:
    if not conn.public_url or not value.startswith(("http://", "https://")):
        return value
    pub = urllib.parse.urlparse(conn.public_url)
    cur = urllib.parse.urlparse(value)
    if not pub.scheme or not pub.netloc:
        return value
    return urllib.parse.urlunparse(
        (pub.scheme, pub.netloc, cur.path, cur.params, cur.query, cur.fragment)
    )


def rewrite_item_urls(item: dict[str, Any], conn: GitLabConnection) -> dict[str, Any]:
    out = dict(item)
    for key in ("web_url", "url"):
        val = out.get(key)
        if isinstance(val, str):
            out[key] = _rewrite_public_url(val, conn)
    return out


def _request(conn: GitLabConnection, token: str, path: str, params: dict[str, str] | None = None) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{conn.api_url}{path}{query}"
    req = urllib.request.Request(
        url,
        headers={"PRIVATE-TOKEN": token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            status = getattr(resp, "status", resp.getcode())
            body = resp.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status in (401, 403):
            raise GitLabError("gitlab authentication failed", status=status, kind="auth") from exc
        if status == 429 or status >= 500:
            raise GitLabError("gitlab temporary error", status=status, kind="transient") from exc
        raise GitLabError("gitlab request failed", status=status, kind="error") from exc
    except urllib.error.URLError as exc:
        raise GitLabError("gitlab network error", kind="transient") from exc

    if status < 200 or status >= 300:
        if status in (401, 403):
            raise GitLabError("gitlab authentication failed", status=status, kind="auth")
        if status == 429 or status >= 500:
            raise GitLabError("gitlab temporary error", status=status, kind="transient")
        raise GitLabError("gitlab request failed", status=status, kind="error")
    if len(body) > MAX_BODY_BYTES:
        raise GitLabError("gitlab response too large", kind="error")
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise GitLabError("gitlab invalid JSON response", kind="error") from exc


def _encoded_project(project_path: str) -> str:
    return urllib.parse.quote(project_path, safe="")


def list_issues(
    conn: GitLabConnection,
    token: str,
    *,
    state: str = "opened",
    labels: str = "",
    assignee: str = "",
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"state": state, "scope": "all", "per_page": "100"}
    if labels:
        params["labels"] = labels
    if assignee:
        params["assignee_username"] = assignee
    path = f"/projects/{_encoded_project(conn.project_path)}/issues"
    data = _request(conn, token, path, params)
    if not isinstance(data, list):
        raise GitLabError("unexpected issues response", kind="error")
    return [rewrite_item_urls(item, conn) for item in data if isinstance(item, dict)]


def list_merge_requests(
    conn: GitLabConnection,
    token: str,
    *,
    state: str = "opened",
    assignee: str = "",
    source_branch_prefix: str = "",
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"state": state, "scope": "all", "per_page": "100"}
    path = f"/projects/{_encoded_project(conn.project_path)}/merge_requests"
    data = _request(conn, token, path, params)
    if not isinstance(data, list):
        raise GitLabError("unexpected merge requests response", kind="error")
    items = [rewrite_item_urls(item, conn) for item in data if isinstance(item, dict)]
    if assignee:
        items = [mr for mr in items if _matches_assignee(mr, assignee)]
    if source_branch_prefix:
        items = [
            mr for mr in items
            if str(mr.get("source_branch") or "").startswith(source_branch_prefix)
        ]
    return items


def _matches_assignee(mr: dict[str, Any], username: str) -> bool:
    names = {a.get("username") for a in (mr.get("assignees") or []) if isinstance(a, dict)}
    assignee = mr.get("assignee") or {}
    if isinstance(assignee, dict) and assignee.get("username"):
        names.add(assignee["username"])
    return username in names


def connect_from_cwd(
    cwd: str | Path | None,
    workspace: str | None = None,
) -> tuple[GitLabConnection, str]:
    host, project_path = parse_git_remote(cwd)
    conn = resolve_connection(host, project_path, workspace)
    token = resolve_token(conn)
    return conn, token


def event_key(project_path: str, kind: str, iid: int | str) -> str:
    return f"{project_path}:{kind}:{iid}"
