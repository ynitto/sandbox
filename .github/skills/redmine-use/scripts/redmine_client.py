#!/usr/bin/env python3
"""
Redmine REST API client (stdlib only).

Usage:
    python redmine_client.py configure                   # Save connection info to workspace
    python redmine_client.py info                        # Show server info
    python redmine_client.py list --project <id_or_name> [filters...]
    python redmine_client.py show --id <issue_id>
    python redmine_client.py update --id <issue_id> [fields...]
    python redmine_client.py comment --id <issue_id> --text <text>

Config priority (highest to lowest):
    1. CLI options (--url, --api-key)
    2. connections.yaml  -- workspace (.github/) > global (agent_dir/)
    3. Environment variables (REDMINE_URL, REDMINE_API_KEY)
    4. Workspace config file (.redmine.json in current directory)
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# config_loader is bundled in the same scripts/ directory
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from config_loader import get_connection, get_config_file_paths, get_yaml_write_path  # type: ignore[import]
    _HAS_CONFIG_LOADER = True
except ImportError:
    _HAS_CONFIG_LOADER = False

CONFIG_FILE = ".redmine.json"


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
    p.chmod(0o600)
    print(f"Saved to {p}")


def resolve_connection(args) -> tuple[str, str]:
    """
    Return (url, api_key) from:
      1. CLI options (--url / --api-key)
      2. connections.yaml (workspace > global) via --label
      3. Environment variables (REDMINE_URL / REDMINE_API_KEY)
      4. Legacy .redmine.json in cwd
    """
    # 1. CLI options
    url = getattr(args, "url", None) or ""
    api_key = getattr(args, "api_key", None) or ""

    # 2. connections.yaml
    if _HAS_CONFIG_LOADER and (not url or not api_key):
        label = getattr(args, "label", "default") or "default"
        conn = get_connection("redmine", label)
        if not url:
            url = conn.get("url", "")
        if not api_key:
            api_key = conn.get("api_key", "")

    # 3. Environment variables
    if not url:
        url = os.environ.get("REDMINE_URL", "")
    if not api_key:
        api_key = os.environ.get("REDMINE_API_KEY", "")

    # 4. Legacy .redmine.json
    if not url or not api_key:
        cfg = load_config()
        if not url:
            url = cfg.get("url", "")
        if not api_key:
            api_key = cfg.get("api_key", "")

    if not url:
        sys.exit(
            "ERROR: Redmine URL が未設定です。\n"
            "  connections.yaml の redmine.url、--url オプション、"
            "REDMINE_URL 環境変数のいずれかで設定してください。"
        )
    if not api_key:
        sys.exit(
            "ERROR: API キーが未設定です。\n"
            "  connections.yaml の redmine.api_key、--api-key オプション、"
            "REDMINE_API_KEY 環境変数のいずれかで設定してください。"
        )

    return url.rstrip("/"), api_key


# ---------------------------------------------------------------------------
# HTTP client (stdlib)
# ---------------------------------------------------------------------------

class RedmineClient:
    def __init__(self, url: str, api_key: str, timeout: int = 30):
        self.base_url = url
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = {
            "X-Redmine-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            sys.exit(f"ERROR: HTTP {e.code} {e.reason}\n{body_text}")
        except urllib.error.URLError as e:
            sys.exit(f"ERROR: 接続失敗 - {e.reason}")

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def put(self, path: str, data: dict) -> dict:
        return self._request("PUT", path, data)

    def post(self, path: str, data: dict) -> dict:
        return self._request("POST", path, data)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

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
    entries: list = existing.get("redmine", [])
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

    url = prompt("Redmine URL", "url", getattr(args, "url", None))
    api_key = prompt("API Key", "api_key", getattr(args, "api_key", None), secret=True)

    if not url or not api_key:
        print("ERROR: URL と API Key は必須です。", file=sys.stderr)
        return 1

    # Update or append entry
    new_entry: dict = {"label": label, "url": url, "api_key": api_key}
    updated = [e for e in entries if isinstance(e, dict) and e.get("label", "default") != label]
    updated.append(new_entry)
    existing["redmine"] = updated

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
    print(f"確認: python redmine_client.py --label {label} info")
    return 0


def cmd_info(client: RedmineClient, _args) -> int:
    """Show Redmine server information."""
    data = client.get("/")
    info = data.get("about") or data
    print(f"Redmine URL  : {client.base_url}")
    print(f"Server info  : {json.dumps(info, ensure_ascii=False)}")
    return 0


def cmd_list(client: RedmineClient, args) -> int:
    """List issues with optional filter conditions."""
    params: dict = {}

    if args.project:
        params["project_id"] = args.project
    if args.status:
        params["status_id"] = args.status
    if args.assigned_to:
        params["assigned_to_id"] = args.assigned_to
    if args.tracker:
        params["tracker_id"] = args.tracker
    if args.priority:
        params["priority_id"] = args.priority
    if args.author:
        params["author_id"] = args.author
    if args.subject:
        params["subject"] = args.subject
    if args.created_on:
        params["created_on"] = args.created_on
    if args.updated_on:
        params["updated_on"] = args.updated_on
    if args.due_date:
        params["due_date"] = args.due_date
    if args.sort:
        params["sort"] = args.sort

    params["limit"] = args.limit
    if args.offset:
        params["offset"] = args.offset

    # Additional raw filters
    if args.filter:
        for f in args.filter:
            if "=" in f:
                k, v = f.split("=", 1)
                params[k.strip()] = v.strip()
            else:
                print(f"WARNING: フィルタ形式が不正です（KEY=VALUE 形式で指定してください）: {f}", file=sys.stderr)

    # Include journals for status display
    params["include"] = "journals"

    qs = urllib.parse.urlencode(params)
    data = client.get(f"/issues.json?{qs}")

    issues = data.get("issues", [])
    total = data.get("total_count", len(issues))
    offset = data.get("offset", 0)

    if not issues:
        print("チケットが見つかりませんでした。")
        return 0

    print(f"チケット一覧 ({offset + 1}〜{offset + len(issues)} / 合計 {total} 件)\n")
    for issue in issues:
        issue_id = issue.get("id", "?")
        subject = issue.get("subject", "(件名なし)")
        status = issue.get("status", {}).get("name", "?")
        priority = issue.get("priority", {}).get("name", "?")
        assigned = issue.get("assigned_to", {}).get("name", "未割り当て")
        updated = _format_date(issue.get("updated_on", ""))
        print(f"#{issue_id:<6} [{status}] [{priority}]  {subject}  (担当: {assigned}, 更新: {updated})")

    return 0


def cmd_show(client: RedmineClient, args) -> int:
    """Show issue detail including journals."""
    data = client.get(f"/issues/{args.id}.json?include=journals,attachments,watchers")
    issue = data.get("issue", {})

    if not issue:
        print(f"チケット #{args.id} が見つかりませんでした。", file=sys.stderr)
        return 1

    print(f"\n=== チケット #{issue.get('id')} ===")
    print(f"件名       : {issue.get('subject', '')}")
    print(f"プロジェクト: {issue.get('project', {}).get('name', '')}")
    print(f"トラッカー  : {issue.get('tracker', {}).get('name', '')}")
    print(f"ステータス  : {issue.get('status', {}).get('name', '')}")
    print(f"優先度     : {issue.get('priority', {}).get('name', '')}")
    print(f"担当者     : {issue.get('assigned_to', {}).get('name', '未割り当て')}")
    print(f"作成者     : {issue.get('author', {}).get('name', '')}")
    print(f"進捗率     : {issue.get('done_ratio', 0)}%")
    print(f"作成日     : {_format_date(issue.get('created_on', ''))}")
    print(f"更新日     : {_format_date(issue.get('updated_on', ''))}")
    if issue.get("due_date"):
        print(f"期日       : {issue.get('due_date')}")

    desc = issue.get("description", "")
    if desc:
        print(f"\n説明:\n{_indent(desc, '  ')}")

    journals = issue.get("journals", [])
    if journals:
        print(f"\nジャーナル ({len(journals)} 件):")
        for j in journals:
            user = j.get("user", {}).get("name", "?")
            created = _format_date(j.get("created_on", ""))
            notes = j.get("notes", "")
            details = j.get("details", [])
            print(f"  [{created}] {user}:")
            for d in details:
                prop = d.get("property", "")
                name = d.get("name", "")
                old_val = d.get("old_value", "")
                new_val = d.get("new_value", "")
                print(f"    * {prop}/{name}: {old_val} → {new_val}")
            if notes:
                print(f"    > {_indent(notes, '      ').lstrip()}")

    return 0


def cmd_update(client: RedmineClient, args) -> int:
    """Update issue fields."""
    issue: dict = {}

    if args.subject is not None:
        issue["subject"] = args.subject
    if args.description is not None:
        issue["description"] = args.description
    if args.status_id is not None:
        issue["status_id"] = args.status_id
    if args.priority_id is not None:
        issue["priority_id"] = args.priority_id
    if args.assigned_to_id is not None:
        issue["assigned_to_id"] = args.assigned_to_id
    if args.done_ratio is not None:
        issue["done_ratio"] = args.done_ratio
    if args.due_date is not None:
        issue["due_date"] = args.due_date
    if args.tracker_id is not None:
        issue["tracker_id"] = args.tracker_id

    if not issue:
        print("ERROR: 更新するフィールドを指定してください。", file=sys.stderr)
        return 1

    client.put(f"/issues/{args.id}.json", {"issue": issue})
    print(f"チケット #{args.id} を更新しました。")
    return 0


def cmd_comment(client: RedmineClient, args) -> int:
    """Post a comment (journal note) to an issue."""
    if args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"ERROR: ファイルを読み込めませんでした: {e}", file=sys.stderr)
            return 1
    else:
        text = args.text

    if not text or not text.strip():
        print("ERROR: コメント本文が空です。", file=sys.stderr)
        return 1

    client.put(f"/issues/{args.id}.json", {"issue": {"notes": text}})
    print(f"チケット #{args.id} にコメントを投稿しました。")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_date(iso: str) -> str:
    """Convert ISO 8601 to readable format."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Redmine REST API client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Global connection options
    parser.add_argument("--url", help="Redmine URL (例: https://redmine.example.com)")
    parser.add_argument("--api-key", dest="api_key", help="Redmine API キー")
    parser.add_argument(
        "--label", default="default",
        help="connections.yaml で使う接続ラベル (デフォルト: default)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # configure
    p_conf = subparsers.add_parser("configure", help="connections.yaml に接続情報を保存する")
    p_conf.add_argument("--url", help="Redmine URL (省略時は対話入力)")
    p_conf.add_argument("--api-key", dest="api_key", help="Redmine API キー (省略時は対話入力)")

    # info
    subparsers.add_parser("info", help="Redmine サーバー情報を表示する")

    # list
    p_list = subparsers.add_parser("list", help="チケット一覧を取得する")
    p_list.add_argument("--project", help="プロジェクト ID またはスラッグ")
    p_list.add_argument("--status", default="open",
                        help="ステータス (open/closed/*/ステータスID, デフォルト: open)")
    p_list.add_argument("--assigned-to", dest="assigned_to",
                        help="担当者 (me またはユーザーID)")
    p_list.add_argument("--tracker", help="トラッカー ID")
    p_list.add_argument("--priority", help="優先度 ID")
    p_list.add_argument("--author", help="作成者 (me またはユーザーID)")
    p_list.add_argument("--subject", help="件名（部分一致）")
    p_list.add_argument("--created-on", dest="created_on",
                        help="作成日時フィルタ (例: >=2025-01-01)")
    p_list.add_argument("--updated-on", dest="updated_on",
                        help="更新日時フィルタ (例: >=2025-01-01)")
    p_list.add_argument("--due-date", dest="due_date",
                        help="期日フィルタ (例: <=2025-03-31)")
    p_list.add_argument("--sort", help="ソート順 (例: updated_on:desc)")
    p_list.add_argument("--limit", type=int, default=25,
                        help="取得件数 (デフォルト: 25, 最大: 100)")
    p_list.add_argument("--offset", type=int, help="オフセット")
    p_list.add_argument("--filter", nargs="+", metavar="KEY=VALUE",
                        help="追加フィルタ (KEY=VALUE 形式で複数指定可)")

    # show
    p_show = subparsers.add_parser("show", help="チケットの詳細を表示する")
    p_show.add_argument("--id", required=True, type=int, help="チケット ID")

    # update
    p_update = subparsers.add_parser("update", help="チケットを更新する")
    p_update.add_argument("--id", required=True, type=int, help="チケット ID")
    p_update.add_argument("--subject", help="新しい件名")
    p_update.add_argument("--description", help="新しい説明")
    p_update.add_argument("--status-id", dest="status_id", type=int, help="ステータス ID")
    p_update.add_argument("--priority-id", dest="priority_id", type=int, help="優先度 ID")
    p_update.add_argument("--assigned-to-id", dest="assigned_to_id", type=int,
                          help="担当者のユーザー ID")
    p_update.add_argument("--done-ratio", dest="done_ratio", type=int,
                          choices=range(0, 101), metavar="0-100", help="進捗率 (0〜100)")
    p_update.add_argument("--due-date", dest="due_date", help="期日 (YYYY-MM-DD)")
    p_update.add_argument("--tracker-id", dest="tracker_id", type=int, help="トラッカー ID")

    # comment
    p_comment = subparsers.add_parser("comment", help="チケットにコメントを投稿する")
    p_comment.add_argument("--id", required=True, type=int, help="チケット ID")
    comment_src = p_comment.add_mutually_exclusive_group(required=True)
    comment_src.add_argument("--text", help="コメント本文")
    comment_src.add_argument("--file", help="コメント本文を含むファイルパス")

    return parser


COMMANDS = {
    "configure": cmd_configure,
    "info": cmd_info,
    "list": cmd_list,
    "show": cmd_show,
    "update": cmd_update,
    "comment": cmd_comment,
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "configure":
        return cmd_configure(None, args)

    url, api_key = resolve_connection(args)
    client = RedmineClient(url, api_key)

    handler = COMMANDS.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(client, args)


if __name__ == "__main__":
    sys.exit(main())
