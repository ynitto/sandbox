#!/usr/bin/env python3
"""
gl.py - GitLab REST API client for issue-driven development workflow.

Uses only Python stdlib. No external dependencies required.
Works on Windows, macOS, and Linux.

GitLab host and project path are parsed from `git remote get-url origin`.

Usage:
  python gl.py [--get FIELD] <command> [arguments]

  --get FIELD  Extract a single value from the JSON output using dot-path notation.
               Examples:
                 --get username              → string field
                 --get iid                   → numeric field
                 --get 0.web_url             → first element's field (for arrays)
                 --get author.username       → nested field

Environment variables:
  GITLAB_TOKEN or GL_TOKEN   Personal Access Token (required)
  GITLAB_SELF_DEFER_MINUTES  Default defer period for check-defer (default: 60)
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from gl_common import title_to_slug


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def get_project_info():
    """Parse git remote origin URL to extract GitLab host and project path."""
    try:
        remote_url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        sys.exit("ERROR: Cannot get git remote 'origin'. Run from inside a git repo.")

    # Supported formats:
    #   SSH:   git@gitlab.com:namespace/repo.git
    #   HTTPS: https://gitlab.com/namespace/repo.git
    #   HTTPS with token: https://oauth2:TOKEN@gitlab.com/namespace/repo.git
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
        sys.exit(f"ERROR: Unknown remote URL format: {remote_url}")

    return host, project


def get_token():
    token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GL_TOKEN")
    if not token:
        sys.exit(
            "ERROR: Set GITLAB_TOKEN or GL_TOKEN environment variable.\n"
            "  Example: export GITLAB_TOKEN=glpat-xxxxxxxxxxxx"
        )
    return token


def api(host, token, method, path, data=None, params=None):
    """Make a GitLab REST API call and return parsed JSON."""
    url = f"https://{host}/api/v4{path}"
    if params:
        url = url + "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
    headers = {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            return json.loads(content) if content.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode("utf-8", errors="replace")
        except Exception:
            msg = "(no details)"
        sys.exit(f"ERROR: HTTP {e.code} {e.reason}\n{msg}")


def encode_project(project):
    """URL-encode namespace/repo as namespace%2Frepo for API paths."""
    return urllib.parse.quote(project, safe="")


def extract_field(data, field_path):
    """
    Extract a nested value using dot-path notation.
      "username"       → data["username"]
      "0.web_url"      → data[0]["web_url"]
      "author.username"→ data["author"]["username"]
    """
    current = data
    for part in field_path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as e:
                sys.exit(f"ERROR: Cannot index list with '{part}': {e}")
        elif isinstance(current, dict):
            if part not in current:
                sys.exit(f"ERROR: Key '{part}' not found in object")
            current = current[part]
        else:
            sys.exit(f"ERROR: Cannot traverse '{part}' in {type(current).__name__}")
    return current


def api_list(host, token, path, params=None):
    """Make paginated GET requests and return all pages combined as a list."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    all_results = []
    page = 1
    while True:
        params["page"] = page
        url = f"https://{host}/api/v4{path}?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
        headers = {
            "PRIVATE-TOKEN": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read()
                page_data = json.loads(content) if content.strip() else []
                if not isinstance(page_data, list):
                    return page_data
                all_results.extend(page_data)
                next_page = resp.headers.get("X-Next-Page", "").strip()
                if not next_page:
                    break
                page = int(next_page)
        except urllib.error.HTTPError as e:
            try:
                msg = e.read().decode("utf-8", errors="replace")
            except Exception:
                msg = "(no details)"
            sys.exit(f"ERROR: HTTP {e.code} {e.reason}\n{msg}")
    return all_results


def out(obj, get_field=None):
    """Print JSON (or an extracted field) to stdout."""
    if get_field:
        val = extract_field(obj, get_field)
        print(val if isinstance(val, (str, int, float, bool)) else json.dumps(val, ensure_ascii=False))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def read_body(body, body_file, option_name="--body"):
    """Return body text from --body or --body-file (cross-platform, no shell needed).

    --body-file - reads from stdin.
    --body-file PATH reads from the specified file (UTF-8).
    --body and --body-file are mutually exclusive.
    """
    if body_file is not None:
        if body:
            sys.exit(f"ERROR: Cannot use both {option_name} and {option_name}-file simultaneously.")
        if body_file == "-":
            return sys.stdin.read()
        try:
            with open(body_file, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            sys.exit(f"ERROR: Cannot read file '{body_file}': {e}")
    return body or ""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_project_info(args, host, project, token):
    """Show parsed project info derived from git remote origin."""
    out({
        "host": host,
        "project": project,
        "project_encoded": encode_project(project),
        "base_url": f"https://{host}/{project}",
    }, args.get)


def cmd_current_user(args, host, project, token):
    """Show the authenticated user."""
    out(api(host, token, "GET", "/user"), args.get)


def cmd_list_issues(args, host, project, token):
    """List project issues with optional filters."""
    ep = encode_project(project)
    params = {
        "state": args.state,
        "labels": args.label or None,
        "assignee_username": args.assignee or None,
        "author_username": args.author or None,
    }
    out(api_list(host, token, f"/projects/{ep}/issues", params=params), args.get)


def cmd_get_issue(args, host, project, token):
    """Get a single issue by ID."""
    ep = encode_project(project)
    out(api(host, token, "GET", f"/projects/{ep}/issues/{args.issue_id}"), args.get)


def cmd_create_issue(args, host, project, token):
    """Create a new issue."""
    ep = encode_project(project)
    data = {"title": args.title, "description": read_body(args.body, args.body_file)}
    if args.labels:
        data["labels"] = args.labels
    if args.assignee:
        users = api(host, token, "GET", "/users", params={"username": args.assignee})
        if not isinstance(users, list) or not users:
            sys.exit(f"ERROR: GitLab user '{args.assignee}' not found")
        if len(users) > 1:
            sys.exit(f"ERROR: Ambiguous username '{args.assignee}' matched {len(users)} users")
        data["assignee_ids"] = [users[0]["id"]]
    out(api(host, token, "POST", f"/projects/{ep}/issues", data=data), args.get)


def cmd_update_issue(args, host, project, token):
    """Update issue labels, assignee, or state."""
    ep = encode_project(project)
    data = {}

    # Label management: fetch current labels and apply add/remove
    if args.add_labels or args.remove_labels:
        issue = api(host, token, "GET", f"/projects/{ep}/issues/{args.issue_id}")
        current = set(issue.get("labels", []))
        if args.add_labels:
            for lbl in args.add_labels.split(","):
                current.add(lbl.strip())
        if args.remove_labels:
            for lbl in args.remove_labels.split(","):
                current.discard(lbl.strip())
        data["labels"] = ",".join(sorted(current))

    if args.assignee:
        users = api(host, token, "GET", "/users", params={"username": args.assignee})
        if not isinstance(users, list) or not users:
            sys.exit(f"ERROR: GitLab user '{args.assignee}' not found")
        if len(users) > 1:
            sys.exit(f"ERROR: Ambiguous username '{args.assignee}' matched {len(users)} users")
        data["assignee_ids"] = [users[0]["id"]]

    if args.state_event:
        data["state_event"] = args.state_event  # "close" or "reopen"

    if not data:
        sys.exit(
            "ERROR: No update fields specified. "
            "Use --add-labels, --remove-labels, --assignee, or --state-event."
        )

    out(api(host, token, "PUT", f"/projects/{ep}/issues/{args.issue_id}", data=data), args.get)


def cmd_add_comment(args, host, project, token):
    """Post a comment on an issue."""
    ep = encode_project(project)
    out(api(
        host, token, "POST",
        f"/projects/{ep}/issues/{args.issue_id}/notes",
        data={"body": read_body(args.body, args.body_file)},
    ), args.get)


def cmd_get_comments(args, host, project, token):
    """List all comments on an issue."""
    ep = encode_project(project)
    out(api_list(
        host, token,
        f"/projects/{ep}/issues/{args.issue_id}/notes",
    ), args.get)


def cmd_list_mrs(args, host, project, token):
    """List merge requests."""
    ep = encode_project(project)
    params = {
        "state": args.state,
        "source_branch": args.source_branch or None,
    }
    out(api_list(host, token, f"/projects/{ep}/merge_requests", params=params), args.get)


def cmd_create_mr(args, host, project, token):
    """Create a merge request."""
    ep = encode_project(project)
    title = f"Draft: {args.title}" if args.draft else args.title
    data = {
        "title": title,
        "source_branch": args.source_branch,
        "target_branch": args.target_branch,
        "description": read_body(args.description, args.description_file, "--description"),
    }
    if args.draft:
        data["draft"] = True
    out(api(host, token, "POST", f"/projects/{ep}/merge_requests", data=data), args.get)


def cmd_update_mr(args, host, project, token):
    """Update a merge request (description, draft status)."""
    ep = encode_project(project)
    data = {}
    if args.description is not None or args.description_file is not None:
        data["description"] = read_body(args.description, args.description_file, "--description")
    if args.no_draft:
        data["draft"] = False
        mr = api(host, token, "GET", f"/projects/{ep}/merge_requests/{args.mr_id}")
        title = mr.get("title", "")
        if title.startswith("Draft: "):
            data["title"] = title[len("Draft: "):]
        elif title.startswith("WIP: "):
            data["title"] = title[len("WIP: "):]
    out(api(host, token, "PUT", f"/projects/{ep}/merge_requests/{args.mr_id}", data=data), args.get)


def cmd_merge_mr(args, host, project, token):
    """Merge a merge request."""
    ep = encode_project(project)
    data = {}
    if args.squash:
        data["squash"] = True
    if args.remove_source_branch:
        data["should_remove_source_branch"] = True
    out(api(
        host, token, "PUT",
        f"/projects/{ep}/merge_requests/{args.mr_id}/merge",
        data=data,
    ), args.get)


def cmd_make_branch_name(args, host, project, token):
    """
    Generate the branch name for an issue.
    Output: feature/issue-{id}-{slug}
    """
    ep = encode_project(project)
    issue = api(host, token, "GET", f"/projects/{ep}/issues/{args.issue_id}")
    slug = title_to_slug(issue["title"])
    print(f"feature/issue-{args.issue_id}-{slug}")


def cmd_get_mr_pipeline(args, host, project, token):
    """Get the latest CI pipeline for a merge request.

    Output JSON includes at minimum:
      {"status": "success"|"running"|"pending"|"failed"|"canceled"|"skipped"|"none", ...}

    "none" is returned when no pipeline exists (CI not configured or not yet triggered).
    """
    ep = encode_project(project)
    pipelines = api(
        host, token, "GET",
        f"/projects/{ep}/merge_requests/{args.mr_id}/pipelines",
        params={"per_page": 1},
    )
    if not pipelines:
        out({"status": "none", "id": None, "web_url": None}, args.get)
        return
    out(pipelines[0], args.get)


def cmd_check_defer(args, host, project, token):
    """
    Check whether the worker should skip (defer) an issue it created itself.

    Output JSON:
      {"defer": true/false, "reason": "...", ...}

    Defer when:
      - The issue was created by the authenticated user (me), AND
      - The issue was created less than --minutes ago.

    After the defer period expires, "defer" becomes false and the worker
    may take the issue.
    """
    ep = encode_project(project)
    issue = api(host, token, "GET", f"/projects/{ep}/issues/{args.issue_id}")
    me = api(host, token, "GET", "/user")

    author = issue.get("author", {}).get("username", "")
    my_username = me.get("username", "")
    defer_minutes = args.minutes

    if author != my_username:
        out({
            "defer": False,
            "reason": "not_my_issue",
            "author": author,
            "me": my_username,
        }, args.get)
        return

    # Issue was created by me — check age
    created_at_str = issue.get("created_at", "")
    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    age_minutes = (now - created_at).total_seconds() / 60

    if age_minutes < defer_minutes:
        out({
            "defer": True,
            "reason": "self_created_too_recent",
            "age_minutes": round(age_minutes, 1),
            "defer_minutes": defer_minutes,
            "remaining_minutes": int(defer_minutes - age_minutes),
        }, args.get)
    else:
        out({
            "defer": False,
            "reason": "self_created_but_expired",
            "age_minutes": round(age_minutes, 1),
            "defer_minutes": defer_minutes,
        }, args.get)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="GitLab REST API client for issue-driven development.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Global option: extract a field from JSON output
    parser.add_argument(
        "--get", metavar="FIELD",
        help="Extract a field from JSON output using dot-path (e.g. username, 0.web_url)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("project-info", help="Show host/project parsed from git remote origin")
    sub.add_parser("current-user", help="Show authenticated user info")

    p = sub.add_parser("list-issues", help="List project issues")
    p.add_argument("--label", help="Filter by label (comma-separated, AND condition)")
    p.add_argument("--assignee", help="Filter by assignee username")
    p.add_argument("--author", help="Filter by author username")
    p.add_argument("--state", default="opened",
                   choices=["opened", "closed", "all"])

    p = sub.add_parser("get-issue", help="Get a single issue")
    p.add_argument("issue_id", type=int)

    p = sub.add_parser("create-issue", help="Create a new issue")
    p.add_argument("--title", required=True)
    p.add_argument("--body", default="", help="Issue description (Markdown)")
    p.add_argument("--body-file", metavar="FILE",
                   help="Read issue description from FILE (use - for stdin). "
                        "Mutually exclusive with --body.")
    p.add_argument("--labels", help="Comma-separated label names")
    p.add_argument("--assignee", help="Assignee username")

    p = sub.add_parser("update-issue", help="Update issue labels / assignee / state")
    p.add_argument("issue_id", type=int)
    p.add_argument("--add-labels",    help="Labels to add (comma-separated)")
    p.add_argument("--remove-labels", help="Labels to remove (comma-separated)")
    p.add_argument("--assignee", help="Set assignee username")
    p.add_argument("--state-event", choices=["close", "reopen"])

    p = sub.add_parser("add-comment", help="Post a comment on an issue")
    p.add_argument("issue_id", type=int)
    p.add_argument("--body", default="", help="Comment body (Markdown)")
    p.add_argument("--body-file", metavar="FILE",
                   help="Read comment body from FILE (use - for stdin). "
                        "Mutually exclusive with --body.")

    p = sub.add_parser("get-comments", help="List all comments on an issue")
    p.add_argument("issue_id", type=int)

    p = sub.add_parser("list-mrs", help="List merge requests")
    p.add_argument("--source-branch", help="Filter by source branch name")
    p.add_argument("--state", default="opened",
                   choices=["opened", "closed", "merged", "all"])

    p = sub.add_parser("create-mr", help="Create a merge request")
    p.add_argument("--title", required=True)
    p.add_argument("--source-branch", required=True)
    p.add_argument("--target-branch", default="main")
    p.add_argument("--description", default="", help="MR description (Markdown)")
    p.add_argument("--description-file", metavar="FILE",
                   help="Read MR description from FILE (use - for stdin). "
                        "Mutually exclusive with --description.")
    p.add_argument("--draft", action="store_true")

    p = sub.add_parser("update-mr", help="Update a merge request")
    p.add_argument("mr_id", type=int)
    p.add_argument("--description", default=None, help="MR description (Markdown)")
    p.add_argument("--description-file", metavar="FILE",
                   help="Read MR description from FILE (use - for stdin). "
                        "Mutually exclusive with --description.")
    p.add_argument("--no-draft", action="store_true", help="Remove draft status")

    p = sub.add_parser("merge-mr", help="Merge a merge request")
    p.add_argument("mr_id", type=int)
    p.add_argument("--squash", action="store_true")
    p.add_argument("--remove-source-branch", action="store_true")

    p = sub.add_parser("get-mr-pipeline",
                       help="Get the latest CI pipeline for a merge request")
    p.add_argument("mr_id", type=int)

    p = sub.add_parser("make-branch-name",
                       help="Generate branch name for an issue (feature/issue-{id}-{slug})")
    p.add_argument("issue_id", type=int)

    p = sub.add_parser("check-defer",
                       help="Check if the worker should skip a self-created issue")
    p.add_argument("issue_id", type=int)
    p.add_argument(
        "--minutes",
        type=float,
        default=float(os.environ.get("GITLAB_SELF_DEFER_MINUTES", "60")),
        help="Defer period in minutes (default: 60, or $GITLAB_SELF_DEFER_MINUTES)",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

COMMANDS = {
    "project-info":    cmd_project_info,
    "current-user":    cmd_current_user,
    "list-issues":     cmd_list_issues,
    "get-issue":       cmd_get_issue,
    "create-issue":    cmd_create_issue,
    "update-issue":    cmd_update_issue,
    "add-comment":     cmd_add_comment,
    "get-comments":    cmd_get_comments,
    "list-mrs":        cmd_list_mrs,
    "create-mr":       cmd_create_mr,
    "update-mr":       cmd_update_mr,
    "merge-mr":        cmd_merge_mr,
    "get-mr-pipeline": cmd_get_mr_pipeline,
    "make-branch-name": cmd_make_branch_name,
    "check-defer":     cmd_check_defer,
}


def main():
    args = build_parser().parse_args()
    host, project = get_project_info()
    token = get_token()
    COMMANDS[args.command](args, host, project, token)


if __name__ == "__main__":
    main()
