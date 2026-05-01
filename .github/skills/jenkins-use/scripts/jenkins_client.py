#!/usr/bin/env python3
"""
Jenkins REST API client (stdlib only).

Usage:
    python jenkins_client.py configure               # Save connection info to workspace
    python jenkins_client.py info                    # Show server info
    python jenkins_client.py list-jobs
    python jenkins_client.py list-builds --job <name>
    python jenkins_client.py build --job <name> [--params KEY=VALUE ...]
    python jenkins_client.py status --job <name> [--build <number>]
    python jenkins_client.py wait --job <name> [--build <number>]
    python jenkins_client.py log --job <name> [--build <number>] [--tail N] [--follow]

Config priority (highest to lowest):
    1. CLI options (--url, --user, --token)
    2. connections.yaml  -- workspace (.github/) > global (agent_dir/)
    3. Environment variables (JENKINS_URL, JENKINS_USER, JENKINS_TOKEN)
    4. Workspace config file (.jenkins.json in current directory)
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# config_loader is bundled in the same scripts/ directory
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from config_loader import get_connection, get_yaml_write_path  # type: ignore[import]
    _HAS_CONFIG_LOADER = True
except ImportError:
    _HAS_CONFIG_LOADER = False

CONFIG_FILE = ".jenkins.json"


# ---------------------------------------------------------------------------
# Workspace config
# ---------------------------------------------------------------------------

def config_path() -> Path:
    return Path.cwd() / CONFIG_FILE


def load_config() -> dict:
    p = config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(data: dict) -> None:
    p = config_path()
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Restrict permissions so token is not world-readable
    p.chmod(0o600)
    print(f"Saved to {p}")


def cmd_configure(_client, args) -> int:
    """Create or update a connections.yaml entry interactively."""
    write_path = get_yaml_write_path()

    # Load existing YAML
    existing: dict = {}
    if write_path.exists():
        try:
            import yaml  # type: ignore[import]
            with open(write_path, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            pass

    label = getattr(args, "label", "default") or "default"
    entries: list = existing.get("jenkins", [])
    if not isinstance(entries, list):
        entries = []
    current = next((e for e in entries if isinstance(e, dict) and e.get("label", "default") == label), {})

    def prompt(display: str, key: str, cli_val: str | None = None, secret: bool = False) -> str:
        if cli_val:
            return cli_val
        current_val = current.get(key, "")
        hint = f" [{current_val if not secret or not current_val else '****'}]" if current_val else ""
        value = input(f"{display}{hint}: ").strip()
        return value or current_val

    url = prompt("Jenkins URL", "url", getattr(args, "url", None))
    user = prompt("Username", "user", getattr(args, "user", None))
    token = prompt("API Token", "token", getattr(args, "token", None), secret=True)

    if not url or not user or not token:
        print("ERROR: URL, Username, API Token はすべて必須です。", file=sys.stderr)
        return 1

    # Update or append entry
    new_entry: dict = {"label": label, "url": url, "user": user, "token": token}
    updated = [e for e in entries if isinstance(e, dict) and e.get("label", "default") != label]
    updated.append(new_entry)
    existing["jenkins"] = updated

    try:
        import yaml  # type: ignore[import]
    except ImportError:
        print("ERROR: pyyaml が必要です。pip install pyyaml", file=sys.stderr)
        return 1

    write_path.parent.mkdir(parents=True, exist_ok=True)
    with open(write_path, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    write_path.chmod(0o600)

    print(f"保存しました: {write_path}  (label={label})")
    print(f"確認: python jenkins_client.py --label {label} info")
    return 0


# ---------------------------------------------------------------------------
# HTTP client (stdlib, with retry + exponential backoff)
# ---------------------------------------------------------------------------

RETRY_STATUS = {500, 502, 503, 504}


class HTTPResponse:
    """Thin wrapper to normalise urllib response into a dict-like object."""
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.headers = headers  # lower-cased keys
        self.body = body

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> dict:
        return json.loads(self.body)

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


class HTTPClient:
    def __init__(self, user: str, token: str, timeout: int = 30, retries: int = 3):
        creds = base64.b64encode(f"{user}:{token}".encode()).decode()
        self._auth_header = f"Basic {creds}"
        self.timeout = timeout
        self.retries = retries

    def _request(self, method: str, url: str, data: bytes | None = None,
                 extra_headers: dict | None = None) -> HTTPResponse:
        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json, text/plain, */*",
        }
        if extra_headers:
            headers.update(extra_headers)
        if data is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        last_err = None
        for attempt in range(self.retries + 1):
            if attempt:
                wait = 2 ** attempt  # 2, 4, 8 …
                time.sleep(wait)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    hdrs = {k.lower(): v for k, v in resp.headers.items()}
                    status = resp.status
                    if status in RETRY_STATUS and attempt < self.retries:
                        last_err = urllib.error.HTTPError(url, status, f"HTTP {status}", hdrs, None)
                        continue
                    return HTTPResponse(status, hdrs, body)
            except urllib.error.HTTPError as e:
                if e.code in RETRY_STATUS and attempt < self.retries:
                    last_err = e
                    continue
                # Read body for error context
                body = e.read() if hasattr(e, "read") else b""
                raise _HTTPError(e.code, body.decode("utf-8", errors="replace")[:300]) from None
            except urllib.error.URLError as e:
                last_err = e
                if attempt < self.retries:
                    continue
                raise ConnectionError(str(e.reason)) from None
            except TimeoutError:
                last_err = TimeoutError(f"Request timed out after {self.timeout}s")
                if attempt < self.retries:
                    continue
                raise

        raise last_err  # exhausted retries

    def get(self, url: str, params: dict | None = None) -> HTTPResponse:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return self._request("GET", url)

    def post(self, url: str, data: dict | None = None,
             extra_headers: dict | None = None) -> HTTPResponse:
        encoded = urllib.parse.urlencode(data or {}).encode() or None
        return self._request("POST", url, data=encoded, extra_headers=extra_headers)


class _HTTPError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Jenkins client
# ---------------------------------------------------------------------------

class JenkinsClient:
    def __init__(self, url: str, user: str, token: str,
                 http_timeout: int = 30, retries: int = 3):
        self.base_url = url.rstrip("/")
        self.http = HTTPClient(user, token, timeout=http_timeout, retries=retries)
        self._crumb: dict | None = None

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str, params: dict | None = None) -> HTTPResponse:
        return self.http.get(self._url(path), params=params)

    def _post(self, path: str, data: dict | None = None) -> HTTPResponse:
        crumb = self._get_crumb()
        return self.http.post(self._url(path), data=data, extra_headers=crumb)

    def _get_crumb(self) -> dict:
        if self._crumb is not None:
            return self._crumb
        try:
            resp = self.http.get(self._url("/crumbIssuer/api/json"))
            d = resp.json()
            self._crumb = {d["crumbRequestField"]: d["crumb"]}
        except (_HTTPError, Exception):
            self._crumb = {}
        return self._crumb

    # API methods -----------------------------------------------------------

    def get_info(self) -> dict:
        return self._get("/api/json").json()

    def list_jobs(self) -> list:
        return self._get("/api/json", params={"tree": "jobs[name,url,color]"}).json().get("jobs", [])

    def list_builds(self, job: str, limit: int = 10) -> list:
        tree = f"builds[number,result,timestamp,duration,url]{{{limit}}}"
        return self._get(f"/job/{job}/api/json", params={"tree": tree}).json().get("builds", [])

    def get_build(self, job: str, build_ref) -> dict:
        return self._get(f"/job/{job}/{build_ref}/api/json").json()

    def trigger_build(self, job: str, params: dict | None = None) -> int | None:
        if params:
            resp = self._post(f"/job/{job}/buildWithParameters", data=params)
        else:
            resp = self._post(f"/job/{job}/build")
        location = resp.header("location")
        parts = [p for p in location.rstrip("/").split("/") if p]
        if parts and parts[-1].isdigit():
            return int(parts[-1])
        return None

    def get_queue_item(self, queue_id: int) -> dict:
        return self._get(f"/queue/item/{queue_id}/api/json").json()

    def wait_for_queue_item(self, queue_id: int, timeout: int = 60, interval: int = 3) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.get_queue_item(queue_id)
            if item.get("executable"):
                return item["executable"]["number"]
            if item.get("cancelled"):
                raise RuntimeError("Build was cancelled while waiting in queue.")
            time.sleep(interval)
        raise TimeoutError(f"Queued item {queue_id} did not start within {timeout}s.")

    def wait_for_build(self, job: str, build_number: int,
                       timeout: int = 1800, interval: int = 10) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            build = self.get_build(job, build_number)
            if not build.get("building") and build.get("result"):
                return build
            elapsed = _fmt_duration(build.get("duration", 0))
            estimated = _fmt_duration(build.get("estimatedDuration", 0))
            msg = f"  Waiting... #{build_number} running ({elapsed}"
            if build.get("estimatedDuration"):
                msg += f", est: {estimated}"
            print(msg + ")", flush=True)
            time.sleep(interval)
        raise TimeoutError(f"Build #{build_number} did not complete within {timeout}s.")

    def get_console_log(self, job: str, build_ref) -> str:
        return self._get(f"/job/{job}/{build_ref}/consoleText").text()

    def stream_console_log(self, job: str, build_ref, poll_interval: int = 3) -> None:
        start = 0
        while True:
            resp = self._get(
                f"/job/{job}/{build_ref}/logText/progressiveText",
                params={"start": start},
            )
            text = resp.text()
            if text:
                print(text, end="", flush=True)
            more = resp.header("x-more-data", "false").lower() == "true"
            start = int(resp.header("x-text-size", str(start)))
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


def _result_icon(result) -> str:
    return {"SUCCESS": "✅", "FAILURE": "❌", "ABORTED": "⏹", "UNSTABLE": "⚠️"}.get(result or "", "⏳")


def _print_build(build: dict) -> None:
    icon = _result_icon(build.get("result"))
    print(f"Build #{build.get('number', '?')}")
    print(f"  Status  : {icon} {build.get('result') or 'IN PROGRESS'}")
    if build.get("timestamp"):
        print(f"  Started : {_fmt_timestamp(build['timestamp'])}")
    if build.get("duration"):
        print(f"  Duration: {_fmt_duration(build['duration'])}")
    if build.get("url"):
        print(f"  URL     : {build['url']}")


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
        icon = "✅" if color == "blue" else ("❌" if color == "red" else "⏸")
        print(f"  {icon} {job['name']}")
    return 0


def cmd_list_builds(client: JenkinsClient, args) -> int:
    builds = client.list_builds(args.job, limit=args.limit)
    if not builds:
        print(f"No builds found for job '{args.job}'.")
        return 0
    for b in builds:
        icon = _result_icon(b.get("result"))
        ts = _fmt_timestamp(b["timestamp"]) if b.get("timestamp") else "?"
        dur = _fmt_duration(b["duration"]) if b.get("duration") else "?"
        print(f"  {icon} #{b.get('number', '?'):>5}  {(b.get('result') or 'IN PROGRESS'):<12}  {ts}  ({dur})")
    return 0


def cmd_build(client: JenkinsClient, args) -> int:
    params = {}
    for p in (args.params or []):
        if "=" not in p:
            print(f"ERROR: Invalid parameter '{p}'. Use KEY=VALUE.", file=sys.stderr)
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
    build = client.get_build(args.job, args.build or "lastBuild")
    _print_build(build)
    return {"FAILURE": 2, "ABORTED": 3}.get(build.get("result", ""), 0)


def cmd_wait(client: JenkinsClient, args) -> int:
    build_ref = args.build or "lastBuild"
    if build_ref == "lastBuild":
        build_number = client.get_build(args.job, "lastBuild")["number"]
    else:
        build_number = int(build_ref)

    print(f"Waiting for build #{build_number} of '{args.job}'...")
    try:
        build = client.wait_for_build(args.job, build_number,
                                      timeout=args.timeout, interval=args.interval)
    except TimeoutError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print()
    _print_build(build)
    return {"SUCCESS": 0, "FAILURE": 2}.get(build.get("result", ""), 3)


def cmd_log(client: JenkinsClient, args) -> int:
    build_ref = args.build or "lastBuild"

    if args.follow:
        build_number = client.get_build(args.job, build_ref)["number"]
        print(f"Streaming log for build #{build_number}...")
        client.stream_console_log(args.job, build_number)
        return 0

    log = client.get_console_log(args.job, build_ref)
    if args.tail:
        lines = log.splitlines()
        log = "\n".join(lines[-args.tail:])
    print(log)
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Jenkins REST API client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"Connection info is read from (priority order):\n"
            f"  1. --url / --user / --token options\n"
            f"  2. JENKINS_URL / JENKINS_USER / JENKINS_TOKEN env vars\n"
            f"  3. {CONFIG_FILE} in the current directory (created by 'configure')"
        ),
    )
    parser.add_argument("--url", help="Jenkins base URL")
    parser.add_argument("--user", help="Jenkins username")
    parser.add_argument("--token", help="Jenkins API token")
    parser.add_argument(
        "--label", default="default",
        help="connections.yaml で使う接続ラベル (デフォルト: default)",
    )
    parser.add_argument("--http-timeout", type=int, default=30, metavar="SECS")
    parser.add_argument("--retries", type=int, default=3)

    sub = parser.add_subparsers(dest="command", required=True)
    p_conf = sub.add_parser("configure", help="connections.yaml に接続情報を保存する")
    p_conf.add_argument("--url", help="Jenkins base URL (省略時は対話入力)")
    p_conf.add_argument("--user", help="Jenkins username (省略時は対話入力)")
    p_conf.add_argument("--token", help="Jenkins API token (省略時は対話入力)")
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
    p_log.add_argument("--follow", action="store_true", help="Stream until build finishes")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def resolve_connection(args) -> tuple[str, str, str]:
    """
    Return (url, user, token) from:
      1. CLI options (--url / --user / --token)
      2. connections.yaml (workspace > global) via --label
      3. Environment variables (JENKINS_URL / JENKINS_USER / JENKINS_TOKEN)
      4. Legacy .jenkins.json in cwd
    """
    cfg = load_config()

    # 1. CLI options
    url   = args.url   or ""
    user  = args.user  or ""
    token = args.token or ""

    # 2. connections.yaml
    if _HAS_CONFIG_LOADER and (not url or not user or not token):
        label = getattr(args, "label", "default") or "default"
        conn = get_connection("jenkins", label)
        if not url:
            url   = conn.get("url", "")
        if not user:
            user  = conn.get("user", "")
        if not token:
            token = conn.get("token", "")

    # 3. Environment variables
    if not url:   url   = os.environ.get("JENKINS_URL",   "")
    if not user:  user  = os.environ.get("JENKINS_USER",  "")
    if not token: token = os.environ.get("JENKINS_TOKEN", "")

    # 4. Legacy .jenkins.json
    if not url:   url   = cfg.get("url",   "")
    if not user:  user  = cfg.get("user",  "")
    if not token: token = cfg.get("token", "")

    return url, user, token


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # configure does not need a live client
    if args.command == "configure":
        return cmd_configure(None, args)

    url, user, token = resolve_connection(args)
    missing = [name for name, val in [("URL", url), ("user", user), ("token", token)] if not val]
    if missing:
        print(
            f"ERROR: Missing Jenkins {', '.join(missing)}.\n"
            f"Run 'python jenkins_client.py configure' to save connection info,\n"
            f"or set JENKINS_URL / JENKINS_USER / JENKINS_TOKEN environment variables.",
            file=sys.stderr,
        )
        return 1

    client = JenkinsClient(url=url, user=user, token=token,
                           http_timeout=args.http_timeout, retries=args.retries)

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
    except _HTTPError as e:
        print(f"ERROR: HTTP {e.code} - {e}", file=sys.stderr)
        return 1
    except ConnectionError as e:
        print(f"ERROR: Could not connect to Jenkins: {e}", file=sys.stderr)
        return 1
    except TimeoutError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
