#!/usr/bin/env python3
"""
Jenkins REST API client.

Usage:
    python jenkins_client.py <command> [options]

Commands:
    info                  Show Jenkins server info
    list-jobs             List all jobs
    list-builds           List builds for a job
    build                 Trigger a build
    status                Show build status
    wait                  Wait for a build to complete
    log                   Get console log

Environment variables:
    JENKINS_URL    Base URL of Jenkins (e.g. https://jenkins.example.com)
    JENKINS_USER   Jenkins username
    JENKINS_TOKEN  Jenkins API token
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("ERROR: 'requests' is not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# HTTP client with retry
# ---------------------------------------------------------------------------

def build_session(retries: int = 3, backoff_factor: float = 2.0) -> requests.Session:
    """Create a requests Session with automatic retry on 5xx and connection errors."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# ---------------------------------------------------------------------------
# Jenkins client
# ---------------------------------------------------------------------------

class JenkinsClient:
    def __init__(
        self,
        url: str,
        user: str,
        token: str,
        http_timeout: int = 30,
        retries: int = 3,
    ):
        self.base_url = url.rstrip("/")
        self.auth = (user, token)
        self.http_timeout = http_timeout
        self.session = build_session(retries=retries)
        self._crumb: dict | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, auth=self.auth, timeout=self.http_timeout, **kwargs)
        resp.raise_for_status()
        return resp

    def _post(self, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        crumb = self._get_crumb()
        if crumb:
            headers.update(crumb)
        resp = self.session.post(
            url, auth=self.auth, timeout=self.http_timeout, headers=headers, **kwargs
        )
        resp.raise_for_status()
        return resp

    def _get_crumb(self) -> dict | None:
        """Fetch Jenkins CSRF crumb (cached after first call)."""
        if self._crumb is not None:
            return self._crumb
        try:
            resp = self.session.get(
                f"{self.base_url}/crumbIssuer/api/json",
                auth=self.auth,
                timeout=self.http_timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._crumb = {data["crumbRequestField"]: data["crumb"]}
            else:
                self._crumb = {}  # CSRF disabled
        except Exception:
            self._crumb = {}
        return self._crumb

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    def get_info(self) -> dict:
        """Return Jenkins server info."""
        return self._get("/api/json").json()

    def list_jobs(self) -> list[dict]:
        """Return a list of all top-level jobs."""
        data = self._get(
            "/api/json",
            params={"tree": "jobs[name,url,color]"},
        ).json()
        return data.get("jobs", [])

    def list_builds(self, job: str, limit: int = 10) -> list[dict]:
        """Return recent builds for a job."""
        tree = f"builds[number,result,timestamp,duration,url]{{{limit}}}"
        data = self._get(
            f"/job/{job}/api/json",
            params={"tree": tree},
        ).json()
        return data.get("builds", [])

    def get_build(self, job: str, build_number: int | str) -> dict:
        """Return detail of a specific build. Use 'lastBuild' for the latest."""
        return self._get(f"/job/{job}/{build_number}/api/json").json()

    def trigger_build(self, job: str, params: dict | None = None) -> int | None:
        """Trigger a build and return the queue item number."""
        if params:
            resp = self._post(
                f"/job/{job}/buildWithParameters",
                data=params,
            )
        else:
            resp = self._post(f"/job/{job}/build")
        # Jenkins returns 201 with Location header pointing to queue item
        location = resp.headers.get("Location", "")
        # e.g. https://jenkins/queue/item/123/
        parts = [p for p in location.rstrip("/").split("/") if p]
        if parts and parts[-1].isdigit():
            return int(parts[-1])
        return None

    def get_queue_item(self, queue_id: int) -> dict:
        """Return queue item info."""
        return self._get(f"/queue/item/{queue_id}/api/json").json()

    def wait_for_queue_item(self, queue_id: int, timeout: int = 60, interval: int = 3) -> int:
        """Wait until the queued build starts and return its build number."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.get_queue_item(queue_id)
            executable = item.get("executable")
            if executable:
                return executable["number"]
            if item.get("cancelled"):
                raise RuntimeError("Build was cancelled while waiting in queue.")
            time.sleep(interval)
        raise TimeoutError(f"Queued item {queue_id} did not start within {timeout}s.")

    def wait_for_build(
        self,
        job: str,
        build_number: int,
        timeout: int = 1800,
        interval: int = 10,
    ) -> dict:
        """Poll until the build finishes. Returns the final build dict."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            build = self.get_build(job, build_number)
            if not build.get("building") and build.get("result"):
                return build
            elapsed = int(build.get("duration", 0) / 1000)
            estimated = int(build.get("estimatedDuration", 0) / 1000)
            print(
                f"  Waiting... build #{build_number} still running "
                f"(elapsed: {_fmt_duration(elapsed * 1000)}"
                + (f", estimated: {_fmt_duration(estimated * 1000)}" if estimated else "")
                + ")",
                flush=True,
            )
            time.sleep(interval)
        raise TimeoutError(
            f"Build #{build_number} did not complete within {timeout}s."
        )

    def get_console_log(self, job: str, build_number: int | str) -> str:
        """Return the full console output of a build."""
        return self._get(f"/job/{job}/{build_number}/consoleText").text

    def stream_console_log(
        self,
        job: str,
        build_number: int | str,
        poll_interval: int = 3,
    ) -> None:
        """Stream console output of a running build until it finishes."""
        start = 0
        while True:
            resp = self._get(
                f"/job/{job}/{build_number}/logText/progressiveText",
                params={"start": start},
            )
            text = resp.text
            if text:
                print(text, end="", flush=True)
            more = resp.headers.get("X-More-Data", "false").lower() == "true"
            start = int(resp.headers.get("X-Text-Size", start))
            if not more:
                break
            time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _fmt_duration(ms: int) -> str:
    secs = ms // 1000
    if secs < 60:
        return f"{secs}s"
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m {secs}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins}m {secs}s"


def _fmt_timestamp(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _result_icon(result: str | None) -> str:
    icons = {"SUCCESS": "✅", "FAILURE": "❌", "ABORTED": "⏹", "UNSTABLE": "⚠️"}
    return icons.get(result or "", "⏳")


def _print_build(build: dict) -> None:
    number = build.get("number", "?")
    result = build.get("result") or "IN PROGRESS"
    ts = _fmt_timestamp(build["timestamp"]) if build.get("timestamp") else "?"
    dur = _fmt_duration(build["duration"]) if build.get("duration") else "?"
    url = build.get("url", "")
    icon = _result_icon(build.get("result"))
    print(f"Build #{number}")
    print(f"  Status  : {icon} {result}")
    print(f"  Started : {ts}")
    print(f"  Duration: {dur}")
    print(f"  URL     : {url}")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_info(client: JenkinsClient, _args) -> int:
    info = client.get_info()
    print(f"Jenkins {info.get('version', '?')}")
    print(f"  Description: {info.get('description') or '(none)'}")
    print(f"  URL        : {client.base_url}")
    return 0


def cmd_list_jobs(client: JenkinsClient, _args) -> int:
    jobs = client.list_jobs()
    if not jobs:
        print("No jobs found.")
        return 0
    for job in jobs:
        color = job.get("color", "")
        status = "✅" if color == "blue" else ("❌" if color == "red" else "⏸")
        print(f"  {status} {job['name']}")
    return 0


def cmd_list_builds(client: JenkinsClient, args) -> int:
    builds = client.list_builds(args.job, limit=args.limit)
    if not builds:
        print(f"No builds found for job '{args.job}'.")
        return 0
    for b in builds:
        number = b.get("number", "?")
        result = b.get("result") or "IN PROGRESS"
        icon = _result_icon(b.get("result"))
        ts = _fmt_timestamp(b["timestamp"]) if b.get("timestamp") else "?"
        dur = _fmt_duration(b["duration"]) if b.get("duration") else "?"
        print(f"  {icon} #{number:>5}  {result:<12}  {ts}  ({dur})")
    return 0


def cmd_build(client: JenkinsClient, args) -> int:
    params = {}
    for p in (args.params or []):
        if "=" not in p:
            print(f"ERROR: Invalid parameter format '{p}'. Use KEY=VALUE.", file=sys.stderr)
            return 1
        k, v = p.split("=", 1)
        params[k] = v

    print(f"Triggering build for job '{args.job}'...")
    queue_id = client.trigger_build(args.job, params or None)
    if queue_id is None:
        print("Build triggered. (Queue ID not available.)")
        return 0

    print(f"Queued (item #{queue_id}). Waiting for build to start...")
    try:
        build_number = client.wait_for_queue_item(queue_id)
    except TimeoutError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Build #{build_number} started.")
    print(f"  URL: {client.base_url}/job/{args.job}/{build_number}/")
    return 0


def cmd_status(client: JenkinsClient, args) -> int:
    build_ref = args.build if args.build else "lastBuild"
    build = client.get_build(args.job, build_ref)
    _print_build(build)
    result = build.get("result")
    if result == "FAILURE":
        return 2
    if result == "ABORTED":
        return 3
    return 0


def cmd_wait(client: JenkinsClient, args) -> int:
    build_ref = args.build if args.build else "lastBuild"
    if build_ref == "lastBuild":
        b = client.get_build(args.job, "lastBuild")
        build_number = b["number"]
    else:
        build_number = int(build_ref)

    print(f"Waiting for build #{build_number} of '{args.job}' to complete...")
    try:
        build = client.wait_for_build(
            args.job,
            build_number,
            timeout=args.timeout,
            interval=args.interval,
        )
    except TimeoutError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print()
    _print_build(build)
    result = build.get("result", "")
    if result == "SUCCESS":
        return 0
    if result == "FAILURE":
        return 2
    return 3


def cmd_log(client: JenkinsClient, args) -> int:
    build_ref = args.build if args.build else "lastBuild"

    if args.follow:
        build = client.get_build(args.job, build_ref)
        build_number = build["number"]
        print(f"Streaming log for build #{build_number}...")
        client.stream_console_log(args.job, build_number)
        return 0

    log = client.get_console_log(args.job, build_ref)
    if args.tail:
        lines = log.splitlines()
        log = "\n".join(lines[-args.tail :])
    print(log)
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Jenkins REST API client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", default=os.environ.get("JENKINS_URL"), help="Jenkins base URL")
    parser.add_argument("--user", default=os.environ.get("JENKINS_USER"), help="Jenkins username")
    parser.add_argument("--token", default=os.environ.get("JENKINS_TOKEN"), help="Jenkins API token")
    parser.add_argument("--http-timeout", type=int, default=30, metavar="SECS")
    parser.add_argument("--retries", type=int, default=3)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="Show Jenkins server info")
    sub.add_parser("list-jobs", help="List all jobs")

    p_lb = sub.add_parser("list-builds", help="List builds for a job")
    p_lb.add_argument("--job", required=True)
    p_lb.add_argument("--limit", type=int, default=10)

    p_build = sub.add_parser("build", help="Trigger a build")
    p_build.add_argument("--job", required=True)
    p_build.add_argument("--params", nargs="*", metavar="KEY=VALUE")

    p_status = sub.add_parser("status", help="Show build status")
    p_status.add_argument("--job", required=True)
    p_status.add_argument("--build", help="Build number (default: latest)")

    p_wait = sub.add_parser("wait", help="Wait for a build to complete")
    p_wait.add_argument("--job", required=True)
    p_wait.add_argument("--build", help="Build number (default: latest)")
    p_wait.add_argument("--timeout", type=int, default=1800, metavar="SECS")
    p_wait.add_argument("--interval", type=int, default=10, metavar="SECS")

    p_log = sub.add_parser("log", help="Get console log")
    p_log.add_argument("--job", required=True)
    p_log.add_argument("--build", help="Build number (default: latest)")
    p_log.add_argument("--tail", type=int, metavar="N", help="Show last N lines")
    p_log.add_argument("--follow", action="store_true", help="Stream log until build finishes")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.url:
        print("ERROR: Jenkins URL is required. Set JENKINS_URL or use --url.", file=sys.stderr)
        return 1
    if not args.user:
        print("ERROR: Jenkins user is required. Set JENKINS_USER or use --user.", file=sys.stderr)
        return 1
    if not args.token:
        print("ERROR: Jenkins token is required. Set JENKINS_TOKEN or use --token.", file=sys.stderr)
        return 1

    client = JenkinsClient(
        url=args.url,
        user=args.user,
        token=args.token,
        http_timeout=args.http_timeout,
        retries=args.retries,
    )

    commands = {
        "info": cmd_info,
        "list-jobs": cmd_list_jobs,
        "list-builds": cmd_list_builds,
        "build": cmd_build,
        "status": cmd_status,
        "wait": cmd_wait,
        "log": cmd_log,
    }

    try:
        return commands[args.command](client, args)
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Could not connect to Jenkins: {e}", file=sys.stderr)
        return 1
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP {e.response.status_code} - {e.response.text[:200]}", file=sys.stderr)
        return 1
    except TimeoutError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
